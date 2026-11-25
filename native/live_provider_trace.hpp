#pragma once
#include <string_view>
namespace doujin {
inline bool live_smoke_host(std::string_view host) {
    if (host.empty() || host.size()>253 || host.front()=='.' || host.back()=='.') return false;
    std::size_t at=0;
    while (at<host.size()) {
        const auto end=host.find('.',at);
        const auto label=host.substr(at,end==host.npos ? host.size()-at : end-at);
        if (label.empty() || label.size()>63 || label.front()=='-' || label.back()=='-' ||
            label.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-")!=label.npos) return false;
        if (end==host.npos) break;
        at=end+1;
    }
    return host=="doujinstyle.com" || host=="www.doujinstyle.com" ||
        host=="mediafire.com" || host.ends_with(".mediafire.com");
}

}

#ifdef _WIN32
#include <windows.h>
#include <winhttp.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
namespace doujin {
inline bool live_flag(const wchar_t* name) {
    wchar_t value[4]{};
    return GetEnvironmentVariableW(name,value,4)==1 && value[0]==L'1';
}
inline bool live_smoke_enabled() { return live_flag(L"DOUJIN_LIVE_PROVIDER_SMOKE"); }
namespace live_smoke {
inline std::atomic<unsigned> requests{0};
inline std::atomic<bool> rate_limited{false};
inline std::mutex bytes_lock, log_lock;
inline std::uint64_t remaining_bytes=live_flag(L"DOUJIN_LIVE_CAPTURE_DOWNLOAD")?
    130ULL*1024*1024:16ULL*1024*1024;
using Clock=std::chrono::steady_clock;
struct Observation {
    bool enabled=false;
    unsigned id=0;
    std::string host, method, range_sha256;
    DWORD status=0;
    std::uint64_t body_bytes=0,range_first=0,range_last=0,resource_size=0;
    long long headers_us=-1,first_body_us=-1,dns_us=-1,connect_us=-1,tls_us=-1,
        request_to_headers_us=-1,connection_request=-1,connection_first=-1,protocol=-1;
    bool complete=false;
    Clock::time_point start=Clock::now();
    long long elapsed() const {
        return std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-start).count();
    }
    void begin(std::string canonical, INTERNET_SCHEME scheme, INTERNET_PORT port,
               std::string_view url, std::wstring_view verb, std::size_t body_size) {
        if(!live_smoke_enabled()) return;
        if(rate_limited.load() || scheme!=INTERNET_SCHEME_HTTPS || port!=443 ||
            !live_smoke_host(canonical) || url.find('#')!=url.npos ||
            url.substr(8,url.find('/',8)-8).find('@')!=url.npos || body_size>65536 ||
            (verb!=L"GET" && !(verb==L"POST" &&
                (canonical=="doujinstyle.com" || canonical=="www.doujinstyle.com"))))
            throw std::runtime_error("Live provider destination/method or rate-limit policy denied");
        const auto count=requests.fetch_add(1);
        if(count>=48) throw std::runtime_error("Live provider request cap reached");
        enabled=true; id=count+1; host=std::move(canonical); method=verb==L"POST"?"POST":"GET";
        start=Clock::now();
    }
    void headers(HINTERNET request,DWORD code) {
        if(!enabled) return;
        status=code; headers_us=elapsed();
        if(status==429) rate_limited.store(true);
#ifdef WINHTTP_OPTION_HTTP_PROTOCOL_USED
        DWORD flags=0,size=sizeof flags;
        if(WinHttpQueryOption(request,WINHTTP_OPTION_HTTP_PROTOCOL_USED,&flags,&size))
            protocol=static_cast<long long>(flags);
#endif
#ifdef WINHTTP_OPTION_REQUEST_STATS
        WINHTTP_REQUEST_STATS stats{};
        stats.cStats=WinHttpRequestStatLast;
        DWORD size_stats=sizeof stats;
        if(WinHttpQueryOption(request,WINHTTP_OPTION_REQUEST_STATS,&stats,&size_stats)) {
            connection_request=stats.ulIndex;
            connection_first=(stats.ullFlags&WINHTTP_REQUEST_STAT_FLAG_FIRST_REQUEST)?1:0;
        }
#endif
#ifdef WINHTTP_OPTION_REQUEST_TIMES
        WINHTTP_REQUEST_TIMES times{};
        times.cTimes=WinHttpRequestTimeLast;
        DWORD size_times=sizeof times;
        LARGE_INTEGER frequency{};
        if(WinHttpQueryOption(request,WINHTTP_OPTION_REQUEST_TIMES,&times,&size_times) &&
            QueryPerformanceFrequency(&frequency) && frequency.QuadPart>0) {
            const auto at=[&](WINHTTP_REQUEST_TIME_ENTRY entry) {
                const auto i=static_cast<std::size_t>(entry);
                return i<static_cast<std::size_t>(times.cTimes)?times.rgullTimes[i]:0ULL;
            };
            const auto span=[&](ULONGLONG a,ULONGLONG b)->long long {
                return a&&b>=a?static_cast<long long>(static_cast<double>(b-a)*1000000.0/
                    static_cast<double>(frequency.QuadPart)):-1;
            };
            dns_us=span(at(WinHttpNameResolutionStart),at(WinHttpNameResolutionEnd));
            connect_us=span(at(WinHttpConnectionEstablishmentStart),at(WinHttpConnectionEstablishmentEnd));
            tls_us=span(at(WinHttpTlsHandshakeClientLeg1Start),std::max({
                at(WinHttpTlsHandshakeClientLeg1End),at(WinHttpTlsHandshakeClientLeg2End),
                at(WinHttpTlsHandshakeClientLeg3End)}));
            request_to_headers_us=span(at(WinHttpSendRequestHeadersEnd),at(WinHttpReceiveResponseHeadersEnd));
        }
#endif
    }
    BOOL read(HINTERNET request,void* buffer,DWORD wanted,DWORD* count) {
        if(!enabled) return WinHttpReadData(request,buffer,wanted,count);
        std::lock_guard lock(bytes_lock);
        if(!remaining_bytes) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return FALSE; }
        const auto allowed=static_cast<DWORD>(std::min<std::uint64_t>(wanted,remaining_bytes));
        if(!WinHttpReadData(request,buffer,allowed,count)) return FALSE;
        remaining_bytes-=*count;
        if(*count && first_body_us<0) first_body_us=elapsed();
        body_bytes+=*count;
        return TRUE;
    }
    ~Observation() {
        if(!enabled) return;
        // Only validated host labels, numbers and hashes. No URL, path, headers or media.
        std::lock_guard lock(log_lock);
        wchar_t path[32768]{};
        const auto n=GetEnvironmentVariableW(L"DOUJIN_LIVE_HTTP_LOG",path,32768);
        std::FILE* target=nullptr;
        if(!n || n>=32768 || _wfopen_s(&target,path,L"ab") || !target) return;
        std::fprintf(target,
            "{\"id\":%u,\"host\":\"%s\",\"method\":\"%s\",\"status\":%lu,\"headers_us\":%lld,\"first_body_us\":%lld,\"body_bytes\":%llu,\"elapsed_us\":%lld,\"complete\":%s,\"dns_us\":%lld,\"connect_us\":%lld,\"tls_us\":%lld,\"request_to_headers_us\":%lld,\"connection_request\":%lld,\"connection_first\":%lld,\"protocol\":%lld,\"range_first\":%llu,\"range_last\":%llu,\"resource_size\":%llu,\"range_sha256\":\"%s\"}\n",
            id,host.c_str(),method.c_str(),static_cast<unsigned long>(status),headers_us,first_body_us,
            static_cast<unsigned long long>(body_bytes),elapsed(),complete?"true":"false",
            dns_us,connect_us,tls_us,request_to_headers_us,connection_request,connection_first,protocol,
            static_cast<unsigned long long>(range_first),static_cast<unsigned long long>(range_last),
            static_cast<unsigned long long>(resource_size),range_sha256.c_str());
        std::fclose(target);
    }
};
inline thread_local Observation* current=nullptr;
struct Scope {
    Observation* previous;
    explicit Scope(Observation* active):previous(current){current=active;}
    ~Scope(){current=previous;}
};
inline BOOL read(HINTERNET request,void* buffer,DWORD wanted,DWORD* count) {
    return current?current->read(request,buffer,wanted,count):WinHttpReadData(request,buffer,wanted,count);
}
}
}
#endif
