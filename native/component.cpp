#include <foobar2000/SDK/foobar2000.h>
#include <foobar2000/SDK/album_art_helpers.h>
#include <foobar2000/SDK/archive.h>
#include <foobar2000/SDK/input_impl.h>
#include <foobar2000/SDK/threadsLite.h>
#include <foobar2000/helpers/file_info_delta.h>
#include <shlwapi.h>
#include <shellapi.h>
#include <winhttp.h>
#include <bcrypt.h>
#include "descriptor.hpp"
#include "http_user_agent.hpp"
#include "mega_cng.hpp"
#include "sha256.hpp"
#include "mediafire_page.hpp"
#include "checkpoint_index.hpp"
#include "archive_range_policy.hpp"
#include "archive_playback_stats.hpp"
#include "zip_member_cache.hpp"
#include "mp3_startup.hpp"
#include "site_art.hpp"
#include "host_zlib.hpp"
#include "trace_time.hpp"
#include "host_trace_etw.hpp"
#include "doujinstyle_build_version.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <charconv>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <filesystem>
#include <fstream>
#include <list>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <tuple>
#include <unordered_map>
#include <vector>

void doujin_lookup_on_playback(std::string id);

// Keep the update-facing version numeric until foobar2000's comparison
// behavior for SemVer prereleases is qualified. Full build identity is embedded
// in the description and is also available from the companion worker --version.
DECLARE_COMPONENT_VERSION("DoujinStyle Library", DOUJIN_BASE_VERSION,
    "Indexes local .djt track references and plays audio through foobar2000 decoders.\n"
    "Build: " DOUJIN_FULL_VERSION "\nSource: " DOUJIN_SOURCE_SHA256);
VALIDATE_COMPONENT_FILENAME("foo_doujinstyle.dll");
DECLARE_FILE_TYPE("DoujinStyle track references", "*.djt");

namespace {
constexpr std::string_view archive_prefix = "doujinarchive://";
constexpr std::string_view member_prefix = "doujinmember://";
constexpr std::size_t block_size = 256 * 1024;
constexpr std::size_t stream_block_size = doujin::archive_range_window;
constexpr std::size_t zip_tail_bytes = 22 + 65535;
constexpr auto mediafire_link_lifetime = std::chrono::minutes(5);
constexpr GUID track_input_id = {0x0266a6db, 0x0172, 0x4c94, {0xb5,0x81,0x67,0x71,0x5c,0x29,0x14,0x31}};
using Bytes = std::vector<unsigned char>;
using PlaybackClock = std::chrono::steady_clock;

constexpr GUID http_diagnostics_id = {0x1b44ad3b, 0x63a3, 0x4a65, {0xa0,0x1b,0xf2,0x09,0x16,0x59,0x8d,0xaf}};
advconfig_branch_factory http_diagnostics("DoujinStyle", http_diagnostics_id, advconfig_branch::guid_branch_tools, 0);
advconfig_checkbox_factory_cached trace_archive_requests("Trace all archive HTTP requests", "doujinstyle.trace_archive_http",
    {0x61af9fd3, 0x7dad, 0x487d, {0x9e,0x44,0xce,0x0a,0xf7,0x15,0x4f,0xdd}}, http_diagnostics_id, 0, false);
advconfig_checkbox_factory_cached disable_http3("Disable HTTP/3 for native requests", "doujinstyle.disable_http3",
    {0x6cae6ca1, 0xbd30, 0x42fb, {0x85,0xbe,0x62,0x3b,0x67,0xf6,0x32,0x9b}}, http_diagnostics_id, 1, false);
advconfig_checkbox_factory_cached reuse_mediafire_link("Reuse recent MediaFire download links (experimental)",
    "doujinstyle.reuse_mediafire_link",
    {0xb293c23d, 0x3e76, 0x483d, {0x89,0xdf,0x32,0x49,0xa3,0x22,0xdb,0x27}}, http_diagnostics_id, 2, false);
advconfig_checkbox_factory_cached trace_decoder_bitrate("Trace decoder bitrate events", "doujinstyle.trace_bitrate",
    {0x302dd57f, 0x8f8f, 0x4eca, {0x9b,0x92,0x87,0x29,0x1f,0x4d,0x3c,0x66}}, http_diagnostics_id, 3, false);
advconfig_string_factory catalog_priority("Catalog Number source order", "doujinstyle.catalog_priority",
    {0xf9e27dfa, 0x0f97, 0x4530, {0xbc,0xf0,0x09,0x09,0x4a,0x0d,0xb4,0xd2}}, http_diagnostics_id, 4,
    "track, release, lookup, album");
advconfig_checkbox_factory_cached coalesce_deflate_ranges("Coalesce DEFLATE playback ranges", "doujinstyle.coalesce_deflate",
    {0xb688f9b1, 0x9ce0, 0x4162, {0x86,0x1a,0x8b,0x43,0x0a,0x24,0x0a,0x52}}, http_diagnostics_id, 5, true);
advconfig_integer_factory_cached checkpoint_spacing("Checkpoint spacing in MiB (rounded up to 2/4/8/16/32)",
    "doujinstyle.checkpoint_spacing", {0x425eaeec, 0xe30d, 0x4f5e, {0x9e,0x99,0x1d,0x60,0x60,0x59,0xd5,0x36}},
    http_diagnostics_id, 6, 4, 2, 32);

auto elapsed_text(PlaybackClock::time_point begin, PlaybackClock::time_point end = PlaybackClock::now()) {
    return doujin::trace_duration(std::chrono::duration<double>(end - begin).count());
}

template<class Fn>
auto as_io(Fn&& fn) {
    try { return fn(); }
    catch (const std::runtime_error& e) { throw exception_io_data(e.what()); }
}

std::string hostname(std::string_view url) {
    auto host = url.substr(url.find("://") + 3);
    return std::string(host.substr(0, host.find_first_of("/?:")));
}

bool mediafire_share(std::string_view url) {
    const auto host = hostname(url);
    return host == "mediafire.com" || host == "www.mediafire.com" || host == "m.mediafire.com";
}

bool mega_share(std::string_view url) { return url.find('#') != url.npos; }

std::string release_url(std::string_view id) {
    return "https://doujinstyle.com/?p=page&type=1&id=" + std::string(id);
}

bool is_release_site(std::string_view url) {
    if (!url.starts_with("https://")) return false;
    const auto host = hostname(url);
    return host == "doujinstyle.com" || host == "www.doujinstyle.com";
}

// Report only the failing operation. Transport/decoder exception text may contain
// signed URLs, so never copy arbitrary nested exceptions into the console.
template<class Fn>
auto playback_stage(const char* stage, Fn&& fn) {
    try { return fn(); }
    catch (const exception_aborted&) { throw; }
    catch (...) {
        console::print((std::string("DoujinStyle: ") + stage + " failed.").c_str());
        throw;
    }
}

using doujin::lower_ascii;

struct HttpHandle {
    HINTERNET value = nullptr;
    ~HttpHandle() { reset(); }
    HttpHandle() = default;
    HttpHandle(const HttpHandle&) = delete;
    HttpHandle& operator=(const HttpHandle&) = delete;

    void reset() {
        if (value) WinHttpCloseHandle(value);
        value = nullptr;
    }
};

struct HttpResponse {
    HttpHandle request;
    std::string url, trace_context, trace_label;
    PlaybackClock::time_point started, headers_at;
    DWORD status = 0;
    const char* protocol = "HTTP version unavailable";
    bool trace = false;
    std::optional<doujin::ContentRange> mega_range;
};

struct HttpCapabilities {
    const char* label;
    bool http3;
};

HttpCapabilities configure_http_capabilities(HINTERNET session, bool allow_http3) {
#if defined(WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL) && defined(WINHTTP_PROTOCOL_FLAG_HTTP2)
    // WinHTTP's capability mask defaults to HTTP/1.x. Permit h2 and h3 so the stack can negotiate.
    DWORD flags = WINHTTP_PROTOCOL_FLAG_HTTP2;
#ifdef WINHTTP_PROTOCOL_FLAG_HTTP3
    DWORD modern = flags | WINHTTP_PROTOCOL_FLAG_HTTP3;
    if (allow_http3 && WinHttpSetOption(session, WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL, &modern, sizeof modern))
        return {"HTTP/1.x, HTTP/2, HTTP/3", true};
#endif
    if (WinHttpSetOption(session, WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL, &flags, sizeof flags))
        return {"HTTP/1.x, HTTP/2", false};
#endif
    return {"WinHTTP defaults", false};
}

const char* response_protocol(HINTERNET request) {
#ifdef WINHTTP_OPTION_HTTP_PROTOCOL_USED
    DWORD flags = 0, bytes = sizeof flags;
    if (WinHttpQueryOption(request, WINHTTP_OPTION_HTTP_PROTOCOL_USED, &flags, &bytes)) {
#ifdef WINHTTP_PROTOCOL_FLAG_HTTP3
        if (flags & WINHTTP_PROTOCOL_FLAG_HTTP3) return "HTTP/3";
#endif
#ifdef WINHTTP_PROTOCOL_FLAG_HTTP2
        if (flags & WINHTTP_PROTOCOL_FLAG_HTTP2) return "HTTP/2";
#endif
        if (!flags) return "HTTP/1.x";
    }
#endif
    return "HTTP version unavailable";
}

std::string winhttp_diagnostics(HINTERNET request) {
    std::string details;
#ifdef WINHTTP_OPTION_REQUEST_STATS
    WINHTTP_REQUEST_STATS stats{};
    stats.cStats = WinHttpRequestStatLast;
    DWORD stats_size = sizeof stats;
    if (WinHttpQueryOption(request, WINHTTP_OPTION_REQUEST_STATS, &stats, &stats_size)) {
        details += " | connection request index=" + std::to_string(stats.ulIndex) +
            " first=" + ((stats.ullFlags & WINHTTP_REQUEST_STAT_FLAG_FIRST_REQUEST) ? "yes" : "no");
        if (stats.cStats > WinHttpConnectFailureCount)
            details += " connect failures=" + std::to_string(stats.rgullStats[WinHttpConnectFailureCount]);
        if (stats.cStats > WinHttpProxyFailureCount)
            details += " proxy failures=" + std::to_string(stats.rgullStats[WinHttpProxyFailureCount]);
    }
#endif
#ifdef WINHTTP_OPTION_REQUEST_TIMES
    WINHTTP_REQUEST_TIMES times{};
    times.cTimes = WinHttpRequestTimeLast;
    DWORD times_size = sizeof times;
    LARGE_INTEGER frequency{};
    if (!WinHttpQueryOption(request, WINHTTP_OPTION_REQUEST_TIMES, &times, &times_size) ||
        !QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0)
        return details + " | WinHTTP phase timings unavailable";
    auto stamp = [&](WINHTTP_REQUEST_TIME_ENTRY entry) {
        const auto index = static_cast<std::size_t>(entry);
        return index < static_cast<std::size_t>(times.cTimes) ? times.rgullTimes[index] : 0ULL;
    };
    auto append = [&](const char* phase, ULONGLONG start, ULONGLONG end) {
        if (start && end >= start)
            details += std::string(" | ") + phase + " " +
                doujin::trace_duration(static_cast<double>(end - start) / frequency.QuadPart);
    };
    append("proxy detection", stamp(WinHttpProxyDetectionStart), stamp(WinHttpProxyDetectionEnd));
    append("connection acquire", stamp(WinHttpConnectionAcquireStart), stamp(WinHttpConnectionAcquireEnd));
    append("DNS", stamp(WinHttpNameResolutionStart), stamp(WinHttpNameResolutionEnd));
    append("connection establish", stamp(WinHttpConnectionEstablishmentStart), stamp(WinHttpConnectionEstablishmentEnd));
    append("TLS", stamp(WinHttpTlsHandshakeClientLeg1Start), std::max({
        stamp(WinHttpTlsHandshakeClientLeg1End), stamp(WinHttpTlsHandshakeClientLeg2End),
        stamp(WinHttpTlsHandshakeClientLeg3End)}));
    append("proxy tunnel", stamp(WinHttpProxyTunnelStart), stamp(WinHttpProxyTunnelEnd));
    append("stream wait", stamp(WinHttpStreamWaitStart), stamp(WinHttpStreamWaitEnd));
    append("request sent to response headers", stamp(WinHttpSendRequestHeadersEnd),
           stamp(WinHttpReceiveResponseHeadersEnd));
#endif
    return details;
}

void report_http_body(const HttpResponse& response, std::size_t bytes, PlaybackClock::time_point first) {
    if (!response.trace) return;
    auto line = response.trace_label + " | body " + std::to_string(bytes) + " bytes";
    if (first != PlaybackClock::time_point{})
        line += " | headers to first readable body " + elapsed_text(response.headers_at, first);
    line += " | request through body " + elapsed_text(response.started);
    console::print(line.c_str());
}

[[noreturn]] void range_error(const char* operation, DWORD error = GetLastError()) {
    const auto message = std::string("DoujinStyle: WinHTTP ") + operation + " failed (" + std::to_string(error) + ")";
    console::print(message.c_str());
    throw exception_io_data(message.c_str());
}

std::wstring http_wide(std::string_view text) {
    if (text.empty()) return {};
    const auto size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (size <= 0) range_error("URL conversion");
    std::wstring result(size, L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()),
                             result.data(), size))
        range_error("URL conversion");
    return result;
}

std::wstring query_header(HINTERNET request, DWORD kind, const wchar_t* name = WINHTTP_HEADER_NAME_BY_INDEX) {
    wchar_t buffer[4096]{};
    DWORD bytes = sizeof buffer;
    if (WinHttpQueryHeaders(request, kind, name, buffer, &bytes, WINHTTP_NO_HEADER_INDEX))
        return std::wstring(buffer);
    const auto error = GetLastError();
    if (error == ERROR_WINHTTP_HEADER_NOT_FOUND) return {};
    range_error("response header", error);
}

std::string response_referrer_policy(HINTERNET request) {
    const auto header = query_header(request, WINHTTP_QUERY_CUSTOM, L"Referrer-Policy");
    // Referrer-Policy is an ASCII token list; do not silently truncate UTF-16.
    std::string text;
    text.reserve(header.size());
    for (const wchar_t unit : header) {
        if (unit < 0x20 || unit > 0x7E) return std::string(doujin::referrer_policy(""));
        text.push_back(static_cast<char>(unit));
    }
    return std::string(doujin::referrer_policy(text));
}

std::string redirect_url(std::string_view base, const std::wstring& location) {
    wchar_t combined[8193]{};
    DWORD length = static_cast<DWORD>(std::size(combined));
    const auto wide = http_wide(base);
    if (FAILED(UrlCombineW(wide.c_str(), location.c_str(), combined, &length, URL_DONT_ESCAPE_EXTRA_INFO)))
        throw exception_io_data("Invalid HTTP redirect location");
    pfc::stringcvt::string_utf8_from_wide utf8(combined);
    std::string url(utf8.get_ptr());
    url.resize(url.find('#') == url.npos ? url.size() : url.find('#'));
    as_io([&] { doujin::validate_url(url); });
    if (base.starts_with("https://") && !url.starts_with("https://"))
        throw exception_io_data("HTTP redirect would downgrade HTTPS");
    return url;
}

struct MediaFireDownload {
    std::string url, referrer, policy;
    std::uint64_t size;
    std::wstring etag;
    PlaybackClock::time_point expires;
};

using MegaCipher = doujin::mega::WindowsCipher;

class RangeTransport {
public:
    RangeTransport(const std::string& url, const std::string& referrer, abort_callback& abort,
                   const MediaFireDownload* cached = nullptr) :
        url_(cached ? cached->url : url), referrer_(cached ? cached->referrer : referrer),
        policy_(cached ? cached->policy : std::string{}) {
        session_.value = WinHttpOpen(doujin::http_user_agent, WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                                     WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session_.value && GetLastError() == ERROR_INVALID_PARAMETER)
            session_.value = WinHttpOpen(doujin::http_user_agent, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                         WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session_.value) range_error("session open");
        capabilities_ = configure_http_capabilities(session_.value, !disable_http3.get());
        if (!WinHttpSetTimeouts(session_.value, 6000, 7000, 7000, 7000))
            range_error("timeout configuration");
        if (!cached) {
            struct Adapter {
                bool (*matches)(std::string_view);
                void (RangeTransport::*resolve)(abort_callback&);
                const char* stage;
            };
            static constexpr Adapter adapters[]{
                {mediafire_share, &RangeTransport::resolve_mediafire, "MediaFire share-page resolution"},
                {mega_share, &RangeTransport::resolve_mega, "MEGA public-file resolution"},
            };
            for (const auto& adapter : adapters) {
                if (adapter.matches(url_)) {
                    playback_stage(adapter.stage, [&] { (this->*adapter.resolve)(abort); });
                    break;
                }
            }
        }
    }

    MediaFireDownload remember_download(std::uint64_t size, const std::wstring& etag) const {
        return {url_, referrer_, policy_, size, etag, PlaybackClock::now() + mediafire_link_lifetime};
    }

    void begin(const std::string& range, const std::wstring& validator, bool startup, abort_callback& abort,
               HttpResponse& response) const {
        response.trace = startup || trace_archive_requests.get();
        response.trace_context = "archive " + range;
        if (mega_cipher_) {
            const auto span = as_io([&] { return doujin::mega_range(range, mega_size_); });
            response.mega_range = span;
            response.url = url_;
            const auto query = response.url.find('?');
            auto end = query == std::string::npos ? response.url.size() : query;
            if (response.url[end - 1] == '/') response.url.erase(--end, 1);
            response.url.insert(end, "/" + std::to_string(span.first) + "-" + std::to_string(span.last));
            open({}, L"Accept-Encoding: identity\r\n", abort, response);
            return;
        }
        auto headers = L"Range: " + http_wide(range) + L"\r\nAccept-Encoding: identity\r\n";
        if (!validator.empty()) headers += L"If-Range: " + validator + L"\r\n";
        get(headers, abort, response, true);
    }

    void open_site_image(abort_callback& abort, HttpResponse& response) const {
        response.url = url_;
        const auto referring_url = doujin::navigation_referrer(referrer_, url_);
        open(referring_url, L"Accept: image/jpeg,image/png,image/x-icon,image/gif\r\nAccept-Encoding: identity\r\n",
             abort, response);
    }

    void decode_mega_range(std::uint64_t offset, Bytes& encrypted) const {
        as_io([&] { mega_cipher_->decrypt(offset, encrypted); });
    }

private:
    struct Origin {
        Origin(const std::wstring& name, INTERNET_PORT target_port, INTERNET_SCHEME target_scheme)
            : host(name), port(target_port), scheme(target_scheme) {}
        std::wstring host;
        INTERNET_PORT port;
        INTERNET_SCHEME scheme;
        HttpHandle handle;
    };

    std::pair<HINTERNET, bool> connection_for(const std::wstring& host, INTERNET_PORT port,
                                              INTERNET_SCHEME scheme) const {
        // RangeObject serializes fetches on this transport. Redirects close the old request first.
        const auto found = std::find_if(origins_.begin(), origins_.end(), [&](const auto& origin) {
            return origin.port == port && origin.scheme == scheme && _wcsicmp(origin.host.c_str(), host.c_str()) == 0;
        });
        if (found != origins_.end()) {
            origins_.splice(origins_.begin(), origins_, found);
            return {origins_.front().handle.value, true};
        }
        origins_.emplace_front(host, port, scheme);
        auto& connection = origins_.front().handle.value;
        connection = WinHttpConnect(session_.value, host.c_str(), port, 0);
        if (!connection) {
            const auto error = GetLastError();
            origins_.pop_front();
            range_error("connection setup", error);
        }
        if (origins_.size() > 8) origins_.pop_back();
        return {connection, false};
    }

    void get(const std::wstring& headers, abort_callback& abort, HttpResponse& response,
             bool archive_range = false) const {
        response.url = url_;
        auto referrer = referrer_;
        auto policy = policy_;
        for (unsigned redirects = 0; ; ++redirects) {
            referrer = as_io([&] { return doujin::navigation_referrer(referrer, response.url, policy); });
            open(referrer, headers, abort, response, archive_range);
            const auto status = response.status;
            if (status != 301 && status != 302 && status != 303 && status != 307 && status != 308) return;
            const auto location = query_header(response.request.value, WINHTTP_QUERY_LOCATION);
            if (location.empty()) return;
            if (redirects == 5) throw exception_io_data("Too many HTTP redirects");
            if (auto next = response_referrer_policy(response.request.value); !next.empty()) policy = std::move(next);
            response.url = redirect_url(response.url, location);
            response.request.reset();
        }
    }

    void open(const std::string& referrer, const std::wstring& headers, abort_callback& abort,
              HttpResponse& response, bool archive_range = false, std::string_view body = {}) const {
        abort.check();
        response.started = PlaybackClock::now();
        const auto wide = http_wide(response.url);
        URL_COMPONENTS parts{};
        parts.dwStructSize = sizeof parts;
        parts.dwHostNameLength = parts.dwUrlPathLength = parts.dwExtraInfoLength = static_cast<DWORD>(-1);
        if (!WinHttpCrackUrl(wide.c_str(), static_cast<DWORD>(wide.size()), 0, &parts))
            range_error("URL parsing");
        const std::wstring host(parts.lpszHostName, parts.dwHostNameLength);
        if (!response.trace_context.empty()) {
            const pfc::stringcvt::string_utf8_from_wide host_utf8(host.c_str());
            response.trace_label = "DoujinStyle: HTTP " + std::to_string(session_id_) + "." +
                std::to_string(++requests_) + " " + response.trace_context + " host=" + host_utf8.get_ptr();
        }
        std::wstring path = parts.dwUrlPathLength ? std::wstring(parts.lpszUrlPath, parts.dwUrlPathLength) : L"/";
        if (parts.dwExtraInfoLength) path.append(parts.lpszExtraInfo, parts.dwExtraInfoLength);
        const DWORD flags = (parts.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0) |
            WINHTTP_FLAG_ESCAPE_DISABLE | WINHTTP_FLAG_ESCAPE_DISABLE_QUERY;
        const auto [connection, reused_origin] = connection_for(host, parts.nPort, parts.nScheme);
        const auto referring_url = http_wide(referrer);
        const auto request = response.request.value = WinHttpOpenRequest(
            connection, body.empty() ? L"GET" : L"POST", path.c_str(), nullptr,
            referring_url.empty() ? WINHTTP_NO_REFERER : referring_url.c_str(), WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
        if (!request) range_error("request setup");
        const char* protocols = capabilities_.label;
#if defined(WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL) && defined(WINHTTP_PROTOCOL_FLAG_HTTP2)
        if (archive_range && capabilities_.http3) {
            const auto origin = lower_ascii(hostname(response.url));
            if (origin == "mediafire.com" || origin.ends_with(".mediafire.com")) {
                // These hosts showed a repeated long pre-header delay with HTTP/3 permitted.
                // Override only the archive request so the share page and cookies keep their session.
                DWORD allowed = WINHTTP_PROTOCOL_FLAG_HTTP2;
                if (WinHttpSetOption(request, WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL, &allowed, sizeof allowed)) {
                    protocols = "HTTP/1.x, HTTP/2 (MediaFire archive policy)";
                } else {
                    const auto error = GetLastError();
                    console::print((response.trace_label + " | MediaFire archive protocol override failed (" +
                                    std::to_string(error) + ")").c_str());
                }
            }
        }
#endif
        DWORD disabled_features = WINHTTP_DISABLE_REDIRECTS;
        if (!WinHttpSetOption(request, WINHTTP_OPTION_DISABLE_FEATURE, &disabled_features, sizeof disabled_features))
            range_error("redirect configuration");
        const auto send_at = PlaybackClock::now();
        const auto body_size = static_cast<DWORD>(body.size());
        if (!WinHttpSendRequest(request, headers.c_str(), static_cast<DWORD>(headers.size()),
                                body.empty() ? WINHTTP_NO_REQUEST_DATA : const_cast<char*>(body.data()),
                                body_size, body_size, 0)) {
            const auto error = GetLastError();
            abort.check();
            if (!response.trace_label.empty())
                console::print((response.trace_label + " | send failed after " +
                                elapsed_text(send_at)).c_str());
            range_error("request send", error);
        }
        const auto sent_at = PlaybackClock::now();
        abort.check();
        if (!WinHttpReceiveResponse(request, nullptr)) {
            const auto error = GetLastError();
            abort.check();
            if (!response.trace_label.empty())
                console::print((response.trace_label + " | receive headers failed after " +
                                elapsed_text(sent_at)).c_str());
            range_error("response", error);
        }
        response.headers_at = PlaybackClock::now();
        abort.check();
        DWORD status_bytes = sizeof response.status;
        if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                 WINHTTP_HEADER_NAME_BY_INDEX, &response.status, &status_bytes, WINHTTP_NO_HEADER_INDEX))
            range_error("response status");
        response.protocol = response_protocol(request);
        if (response.trace || (!response.trace_label.empty() && response.status >= 400)) {
            std::string closure;
            if (std::string_view(response.protocol) == "HTTP/1.x") {
                std::array<wchar_t, 128> value{};
                DWORD bytes = sizeof value;
                if (WinHttpQueryHeaders(request, WINHTTP_QUERY_CONNECTION, WINHTTP_HEADER_NAME_BY_INDEX,
                    value.data(), &bytes, WINHTTP_NO_HEADER_INDEX) && _wcsicmp(value.data(), L"close") == 0)
                    closure = " | server requested connection close";
            }
            console::print((response.trace_label + " | HTTP " + std::to_string(response.status) +
                " via " + response.protocol + " | allowed " + protocols +
                " | origin handle " + (reused_origin ? "reused" : "new") +
                " | local setup " + elapsed_text(response.started, send_at) +
                " | send " + elapsed_text(send_at, sent_at) +
                " | receive headers " + elapsed_text(sent_at, response.headers_at) +
                " | headers total " + elapsed_text(response.started, response.headers_at) +
                winhttp_diagnostics(request) + closure).c_str());
        }
    }

    void resolve_mediafire(abort_callback& abort) {
        HttpResponse response;
        response.trace = true;
        response.trace_context = "MediaFire share";
        get(L"Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.1\r\nAccept-Encoding: identity\r\n", abort, response);
        console::print(("DoujinStyle: MediaFire share page HTTP " + std::to_string(response.status) +
                        " via " + response.protocol).c_str());
        if (response.status == 403)
            throw exception_io_data("MediaFire share page returned HTTP 403. In DoujinStyle Search, try Repair track metadata to check the current offer.");
        if (response.status != 200)
            throw exception_io_data(("MediaFire share page returned HTTP " + std::to_string(response.status)).c_str());
        if (!mediafire_share(response.url)) throw exception_io_data("Unexpected MediaFire share page destination");
        std::string page;
        PlaybackClock::time_point first;
        char block[8192];
        constexpr std::size_t limit = 1024 * 1024;
        while (page.size() < limit) {
            abort.check();
            DWORD got = 0;
            const auto wanted = static_cast<DWORD>(std::min(sizeof block, limit - page.size()));
            if (!WinHttpReadData(response.request.value, block, wanted, &got)) range_error("share page read");
            if (!got) break;
            if (page.empty()) first = PlaybackClock::now();
            page.append(block, got);
        }
        report_http_body(response, page.size(), first);
        auto link = as_io([&] {
            return doujin::extract_mediafire_download(page, response_referrer_policy(response.request.value));
        });
        const auto base = link.base.empty() ? response.url : redirect_url(response.url, http_wide(link.base));
        auto destination = link.href.starts_with("https://") ? std::move(link.href) : redirect_url(base, http_wide(link.href));
        as_io([&] { doujin::validate_url(destination); });
        if (!destination.starts_with("https://") || !hostname(destination).ends_with(".mediafire.com"))
            throw exception_io_data("Unexpected MediaFire download destination");
        url_ = std::move(destination);
        referrer_ = std::move(response.url);
        policy_ = std::move(link.policy);
    }

    void resolve_mega(abort_callback& abort) {
        const auto link = as_io([&] { return doujin::parse_mega_link(url_); });
        HttpResponse response;
        response.trace = true;
        response.trace_context = "MEGA public file";
        response.url = "https://g.api.mega.co.nz/cs?id=" + std::to_string(GetTickCount64());
        const auto body = R"([{"a":"g","p":")" + link.handle + R"(","g":1,"ssl":2}])";
        open({}, L"Content-Type: application/json\r\nAccept-Encoding: identity\r\n",
             abort, response, false, body);
        if (response.status != 200) throw exception_io_data(("MEGA public API returned HTTP " +
                                                            std::to_string(response.status)).c_str());
        std::string metadata;
        std::array<char, 4096> buffer{};
        while (metadata.size() < 64 * 1024) {
            abort.check();
            DWORD count = 0;
            if (!WinHttpReadData(response.request.value, buffer.data(), static_cast<DWORD>(buffer.size()), &count))
                range_error("MEGA public API read");
            if (!count) break;
            metadata.append(buffer.data(), count);
        }
        if (metadata.size() >= 64 * 1024) throw exception_io_data("MEGA public API response was too large");
        const auto public_file = as_io([&] { return doujin::mega::parse_public_file(metadata); });
        mega_size_ = public_file.size;
        url_ = public_file.endpoint;
        mega_cipher_ = as_io([&] { return std::make_unique<MegaCipher>(link); });
    }

    HttpHandle session_;
    mutable std::list<Origin> origins_;
    inline static std::atomic<unsigned> next_session_id_ = 0;
    const unsigned session_id_ = ++next_session_id_;
    mutable unsigned requests_ = 0;
    HttpCapabilities capabilities_{"WinHTTP defaults", false};
    std::string url_, referrer_, policy_;
    std::unique_ptr<MegaCipher> mega_cipher_;
    std::uint64_t mega_size_ = 0;
};

struct Span {
    std::uint64_t first;
    Bytes data;
};

class RangeObject {
public:
    RangeObject(const std::string& url, const std::string& referrer, abort_callback& abort,
                const MediaFireDownload* cached = nullptr) :
        transport_(url, referrer, abort, cached), archive_key_(url + "\n" + referrer + "\n") {
        auto [head, bytes] = fetch("bytes=-" + std::to_string(zip_tail_bytes), abort, zip_tail_bytes);
        if (head.last != size_ - 1 || head.first != size_ - std::min<std::uint64_t>(size_, zip_tail_bytes))
            throw exception_io_data("Archive host returned a mismatched suffix range");
        if (cached && (size_ != cached->size || (!cached->etag.empty() && etag_ != cached->etag)))
            throw exception_io_data("MediaFire archive no longer matches the recent download link");
        tail_ = {head.first, std::move(bytes)};
    }

    MediaFireDownload remember_download() const {
        return transport_.remember_download(size_, etag_);
    }

    std::uint64_t size() const { return size_; }
    std::uint64_t body_bytes() const { return body_bytes_.load(); }
    unsigned range_count() const { return responses_.load(); }

    std::optional<doujin::ZipMember> locate_member(std::string_view name,
                                                   const std::optional<doujin::ZipLocator>& locator,
                                                   abort_callback& abort) {
        std::unique_lock<std::timed_mutex> lock(member_mutex_, std::defer_lock);
        while (!lock.try_lock_for(std::chrono::milliseconds(25))) abort.check();
        abort.check();
        return members_.locate(name, locator, [&] {
            const doujin::ZipReadAt read = [&](auto offset, auto data) {
                if (read_at(offset, data.data(), data.size(), abort) != data.size())
                    throw exception_io_data("Short read from ZIP directory");
            };
            return locator ? doujin::find_zip_member(size(), *locator, read) : doujin::find_zip_member(size(), name, read);
        });
    }

    std::filesystem::path checkpoint_directory(const doujin::ZipMember& entry) const {
        if (etag_.size() < 2 || etag_.front() != L'"' || etag_.back() != L'"') return {};
        // ETag is an opaque HTTP revision identifier. Encode UTF-16 into
        // UTF-8 without lossy wchar_t -> char truncation before hashing.
        if (etag_.size() > 4096) return {};
        const auto units = static_cast<int>(etag_.size());
        const int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, etag_.data(),
                                               units, nullptr, 0, nullptr, nullptr);
        if (length <= 0) return {};
        std::string etag(static_cast<std::size_t>(length), '\0');
        if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, etag_.data(), units,
                                etag.data(), length, nullptr, nullptr) != length)
            return {};
        const auto name = doujin::sha256_key(archive_key_ + etag + "\n" + std::to_string(size_) + "\n" +
                                     std::to_string(entry.offset) + "\n" + std::to_string(entry.packed_size) + "\n" +
                                     std::to_string(entry.size) + "\n" + std::to_string(entry.crc));
        if (name.empty()) return {};
        const auto profile = filesystem::g_get_native_path(core_api::get_profile_path());
        return std::filesystem::path(http_wide(profile.c_str())) / L"DoujinStyle" / L"Seek" / name;
    }

    void report(const char* stage) const {
        console::print((std::string("DoujinStyle: ") + stage + " | " + std::to_string(body_bytes()) +
                        " archive response bytes in " + std::to_string(responses_.load()) + " ranges | archive session age " +
                        elapsed_text(started_)).c_str());
    }

    std::size_t read_at(std::uint64_t at, void* output, std::size_t requested, abort_callback& abort,
                        std::size_t read_ahead = block_size, std::uint64_t end_limit = UINT64_MAX,
                        std::size_t exact_range_limit = stream_block_size) {
        const auto limit = std::min(size_, end_limit);
        if (at >= limit) return 0;
        auto* dst = static_cast<unsigned char*>(output);
        const auto wanted = static_cast<std::size_t>(std::min<std::uint64_t>(requested, limit - at));
        std::size_t written = 0;
        while (written < wanted) {
            abort.check();
            auto count = copy_cached(at, dst + written, wanted - written);
            if (!count) {
                // Fetches share one transport and validator, but a cache hit only needs the cache mutex.
                std::unique_lock<std::timed_mutex> fetch_lock(fetch_mutex_, std::defer_lock);
                const auto queued = Clock::now();
                while (!fetch_lock.try_lock_for(std::chrono::milliseconds(25))) abort.check();
                if (const auto now = Clock::now(); now - queued >= std::chrono::milliseconds(250))
                    console::print(("DoujinStyle: archive range waited " + elapsed_text(queued, now) +
                                    " for byte " + std::to_string(at)).c_str());
                abort.check();
                count = copy_cached(at, dst + written, wanted - written);
                if (!count) count = fetch_and_copy(at, limit, read_ahead, exact_range_limit,
                                                  dst + written, wanted - written, abort);
            }
            written += count;
            at += count;
        }
        return written;
    }

private:
    std::size_t copy_cached(std::uint64_t at, unsigned char* out, std::size_t wanted) {
        const auto copy = [&](const Span& span) -> std::size_t {
            if (at < span.first || at - span.first >= span.data.size()) return 0;
            const auto offset = static_cast<std::size_t>(at - span.first);
            const auto count = std::min(span.data.size() - offset, wanted);
            std::memcpy(out, span.data.data() + offset, count);
            return count;
        };
        // The archive tail is immutable after opening and survives audio cache eviction.
        if (const auto count = copy(tail_)) return count;
        std::lock_guard lock(cache_mutex_);
        for (auto span = spans_.begin(); span != spans_.end(); ++span) {
            if (const auto count = copy(*span)) {
                spans_.splice(spans_.begin(), spans_, span);
                return count;
            }
        }
        return 0;
    }

    std::size_t fetch_and_copy(std::uint64_t at, std::uint64_t limit, std::size_t read_ahead, std::size_t exact_range_limit,
                               unsigned char* out, std::size_t wanted, abort_callback& abort) {
        auto start = at;
        // Audio cursors pass zero to fetch only the outstanding decoder/host request.
        if (read_ahead) {
            const auto aligned = at / block_size * block_size;
            if (at - aligned < read_ahead) start = aligned;
        }
        const auto window = read_ahead ? read_ahead : std::min(wanted, exact_range_limit);
        auto end = start + std::min<std::uint64_t>(limit - start, window) - 1;
        if (at < tail_.first) end = std::min(end, tail_.first - 1);
        {
            std::lock_guard lock(cache_mutex_);
            for (const auto& cached : spans_) {
                const auto after = cached.first + cached.data.size();
                if (after <= at) start = std::max(start, after);
                if (cached.first > at) end = std::min(end, cached.first - 1);
            }
        }
        auto [range, bytes] = fetch("bytes=" + std::to_string(start) + "-" + std::to_string(end), abort, window);
        if (range.first != start || range.last != end)
            throw exception_io_data("Archive host returned a mismatched byte range");
        const auto offset = static_cast<std::size_t>(at - start);
        const auto count = std::min(bytes.size() - offset, wanted);
        std::memcpy(out, bytes.data() + offset, count);
        std::lock_guard lock(cache_mutex_);
        spans_.push_front({start, std::move(bytes)});
        span_bytes_ += spans_.front().data.size();
        while (spans_.size() > 16 || span_bytes_ > 16 * stream_block_size) {
            span_bytes_ -= spans_.back().data.size();
            spans_.pop_back();
        }
        return count;
    }

    std::pair<doujin::ContentRange, Bytes> fetch(const std::string& header, abort_callback& abort,
                                                std::size_t byte_limit = block_size) {
        const auto& validator = etag_.empty() ? modified_ : etag_;
        HttpResponse response;
        transport_.begin(header, validator, responses_.load() < 4, abort, response);
        const auto request = response.request.value;
        const auto status = response.status;
        const bool valid_status = status == 206 || (response.mega_range && status == 200);
        if (size_ == 0 || !valid_status || std::string_view(last_protocol_) != response.protocol) {
            console::print(("DoujinStyle: archive " + header + " HTTP " + std::to_string(status) +
                            " via " + response.protocol).c_str());
            last_protocol_ = response.protocol;
        }
        if (!valid_status && response.mega_range)
            throw exception_io_data(("MEGA archive range returned HTTP " + std::to_string(status)).c_str());
        if (!valid_status)
            throw exception_io_data(("Archive range returned HTTP " + std::to_string(status) + ", expected 206").c_str());
        const auto encoding = query_header(request, WINHTTP_QUERY_CONTENT_ENCODING);
        if (!encoding.empty() && _wcsicmp(encoding.c_str(), L"identity") != 0)
            throw exception_io_data("Archive response has unexpected Content-Encoding");
        const auto range_header = query_header(request, WINHTTP_QUERY_CONTENT_RANGE);
        const auto range = response.mega_range ? *response.mega_range : as_io([&] {
            // Content-Range is an ASCII grammar. Reject non-ASCII bytes
            // rather than truncating wchar_t values under Windows.
            std::string ascii;
            ascii.reserve(range_header.size());
            for (const wchar_t unit : range_header) {
                if (unit < 0x20 || unit > 0x7E)
                    throw exception_io_data("Archive Content-Range is not ASCII");
                ascii.push_back(static_cast<char>(unit));
            }
            return doujin::parse_content_range(ascii);
        });
        if (response.mega_range && status == 206 && range_header != L"bytes " + std::to_wstring(range.first) +
            L"-" + std::to_wstring(range.last) + L"/" + std::to_wstring(range.size))
            throw exception_io_data("MEGA storage returned a mismatched Content-Range");
        const auto etag = response.mega_range ? std::wstring{} : query_header(request, WINHTTP_QUERY_ETAG);
        const auto modified = response.mega_range ? std::wstring{} : query_header(request, WINHTTP_QUERY_LAST_MODIFIED);
        const auto length = range.last - range.first + 1;
        if (length > byte_limit) throw exception_io_data("Unexpectedly large HTTP range");
        const auto content_length = query_header(request, WINHTTP_QUERY_CONTENT_LENGTH);
        if (!content_length.empty() && content_length != std::to_wstring(length))
            throw exception_io_data("Archive Content-Length disagrees with Content-Range");
        if (size_ == 0) {
            size_ = range.size;
            if (!etag.starts_with(L"W/")) etag_ = etag;
            modified_ = modified;
        } else if (size_ != range.size || (!etag_.empty() && !etag.empty() && etag != etag_) ||
                   (etag_.empty() && !modified_.empty() && !modified.empty() && modified != modified_)) {
            throw exception_io_data("Archive changed while reading");
        }
        Bytes bytes(static_cast<std::size_t>(length));
        Clock::time_point first;
        std::size_t offset = 0;
        while (offset < bytes.size()) {
            abort.check();
            DWORD count = 0;
            const auto wanted = static_cast<DWORD>(std::min(bytes.size() - offset, block_size));
            if (!WinHttpReadData(request, bytes.data() + offset, wanted, &count)) {
                const auto error = GetLastError();
                abort.check();
                console::print((response.trace_label + " | body read failed after " +
                                elapsed_text(response.headers_at) + " and " +
                                std::to_string(offset) + " bytes").c_str());
                range_error("range read", error);
            }
            if (!count) throw exception_io_data("Archive range body is shorter than Content-Range");
            if (offset == 0 && response.trace) first = Clock::now();
            offset += count;
            body_bytes_.fetch_add(count, std::memory_order_relaxed);
        }
        abort.check();
        if (content_length.empty()) {
            unsigned char extra = 0;
            DWORD count = 0;
            if (!WinHttpReadData(request, &extra, 1, &count)) range_error("range EOF");
            if (count) throw exception_io_data("Archive range body is longer than Content-Range");
        }
        report_http_body(response, offset, first);
        if (response.mega_range) transport_.decode_mega_range(range.first, bytes);
        if (header.starts_with("bytes=-"))
            console::print(("DoujinStyle: ZIP tail ready (" + std::to_string(length) + " bytes)").c_str());
        ++responses_;
        if (const auto now = Clock::now(); now - last_progress_ >= std::chrono::seconds(3)) {
            last_progress_ = now;
            report("ZIP transfer progress");
        }
        return {range, std::move(bytes)};
    }

    using Clock = std::chrono::steady_clock;
    const Clock::time_point started_ = Clock::now();
    Clock::time_point last_progress_ = started_;
    std::atomic<std::uint64_t> body_bytes_ = 0;
    std::atomic<unsigned> responses_ = 0;
    RangeTransport transport_;
    std::string archive_key_;
    const char* last_protocol_ = "";
    std::wstring etag_, modified_;
    std::uint64_t size_ = 0;
    std::timed_mutex fetch_mutex_;
    std::timed_mutex member_mutex_;
    doujin::ZipMemberCache members_;
    std::mutex cache_mutex_;
    Span tail_{};
    std::list<Span> spans_;
    std::size_t span_bytes_ = 0;
};

std::shared_ptr<RangeObject> shared_range_object(const std::string& url, const std::string& referrer, abort_callback& abort) {
    struct OpenSlot {
        std::timed_mutex open;
        std::weak_ptr<RangeObject> resource;
        std::optional<MediaFireDownload> download;
        std::exception_ptr failure;
        std::chrono::steady_clock::time_point retry_after;
    };
    static std::mutex mutex;
    static std::unordered_map<std::string, std::shared_ptr<OpenSlot>> active;
    std::shared_ptr<OpenSlot> slot;
    {
        std::lock_guard lock(mutex);
        const auto now = std::chrono::steady_clock::now();
        std::erase_if(active, [now](const auto& item) {
            const auto& entry = item.second;
            return entry.use_count() == 1 && entry->resource.expired() && now >= entry->retry_after &&
                (!entry->download || now >= entry->download->expires);
        });
        auto& entry = active[url + "\n" + referrer];
        if (!entry) entry = std::make_shared<OpenSlot>();
        slot = entry;
    }
    const auto queued = PlaybackClock::now();
    std::unique_lock<std::timed_mutex> lock(slot->open, std::defer_lock);
    while (!lock.try_lock_for(std::chrono::milliseconds(25))) abort.check();
    abort.check();
    if (auto live = slot->resource.lock()) {
        console::print(("DoujinStyle: reusing archive session | waited " +
                        elapsed_text(queued) + " for archive open").c_str());
        return live;
    }
    if (slot->failure && std::chrono::steady_clock::now() < slot->retry_after)
        std::rethrow_exception(slot->failure);
    try {
        const bool experiment = reuse_mediafire_link.get() && mediafire_share(url);
        if (slot->download && (!experiment || PlaybackClock::now() >= slot->download->expires))
            slot->download.reset();
        std::shared_ptr<RangeObject> opened;
        if (slot->download) {
            console::print("DoujinStyle: trying recent MediaFire CDN link in a fresh session");
            try {
                opened = std::make_shared<RangeObject>(url, referrer, abort, &*slot->download);
                console::print("DoujinStyle: recent MediaFire CDN link passed suffix range validation");
            } catch (const exception_io_data&) {
                abort.check();
                slot->download.reset();
                console::print("DoujinStyle: recent MediaFire CDN link failed; resolving share page");
            }
        }
        if (!opened) {
            opened = std::make_shared<RangeObject>(url, referrer, abort);
            if (experiment) slot->download = opened->remember_download();
        }
        slot->resource = opened;
        slot->failure = {};
        return opened;
    } catch (const exception_aborted&) {
        throw;
    } catch (...) {
        slot->failure = std::current_exception();
        slot->retry_after = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        throw;
    }
}

class RangeCursor : public file_readonly {
public:
    RangeCursor(std::shared_ptr<RangeObject> resource, std::uint64_t start, std::uint64_t length,
                doujin::SingleMemberZip envelope = {}) :
        resource_(std::move(resource)), start_(start), length_(length), envelope_(std::move(envelope)) {}
    t_size read(void* buffer, t_size bytes, abort_callback& abort) override {
        abort.check();
        auto* output = static_cast<unsigned char*>(buffer);
        const auto wanted = std::min<std::uint64_t>(bytes, get_size(abort) - position_);
        const auto header = envelope_.header.size();
        const auto trailer_at = header + length_;
        t_size written = 0;
        while (written < wanted) {
            const auto remaining = static_cast<std::size_t>(wanted - written);
            std::size_t count;
            if (position_ < header) {
                count = std::min<std::uint64_t>(remaining, header - position_);
                std::memcpy(output + written, envelope_.header.data() + position_, count);
            } else if (position_ < trailer_at) {
                count = std::min<std::uint64_t>(remaining, trailer_at - position_);
                if (resource_->read_at(start_ + position_ - header, output + written, count, abort,
                                       0, start_ + length_) != count)
                    throw exception_io_data("Short read from remote ZIP member");
            } else {
                count = remaining;
                std::memcpy(output + written, envelope_.trailer.data() + position_ - trailer_at, count);
            }
            written += count;
            position_ += count;
        }
        return written;
    }
    t_filesize get_size(abort_callback&) override { return envelope_.header.size() + length_ + envelope_.trailer.size(); }
    t_filesize get_position(abort_callback&) override { return position_; }
    void seek(t_filesize at, abort_callback& abort) override {
        abort.check();
        if (at > get_size(abort)) throw exception_io_seek_out_of_range();
        position_ = at;
    }
    void reopen(abort_callback& abort) override { seek(0, abort); }
    bool can_seek() override { return true; }
    bool is_remote() override { return true; }
    bool get_content_type(pfc::string_base&) override { return false; }

private:
    std::shared_ptr<RangeObject> resource_;
    std::uint64_t start_, length_;
    doujin::SingleMemberZip envelope_;
    t_filesize position_ = 0;
};

std::optional<doujin::ZipMember> locate_member(RangeObject& resource, std::string_view member, abort_callback& abort,
                                              const std::optional<doujin::ZipLocator>& locator = {}) {
    return as_io([&] { return resource.locate_member(member, locator, abort); });
}

const doujin::DeflateApi* host_deflate_api() {
    static const auto bindings = [] {
        const auto library = doujin::foobar_zlib_path();
        if (library.empty()) return doujin::DeflateApi{};
        // The host already imports this DLL. Look up that exact image without loading a PATH candidate.
        const auto module = GetModuleHandleW(library.c_str());
        if (!module) return doujin::DeflateApi{};
        return doujin::DeflateApi{
            reinterpret_cast<decltype(&inflateInit2_)>(GetProcAddress(module, "inflateInit2_")),
            reinterpret_cast<decltype(&inflate)>(GetProcAddress(module, "inflate")),
            reinterpret_cast<decltype(&inflateEnd)>(GetProcAddress(module, "inflateEnd")),
            reinterpret_cast<decltype(&crc32)>(GetProcAddress(module, "crc32")),
            reinterpret_cast<decltype(&inflatePrime)>(GetProcAddress(module, "inflatePrime")),
            reinterpret_cast<decltype(&inflateSetDictionary)>(GetProcAddress(module, "inflateSetDictionary")),
            reinterpret_cast<decltype(&inflateGetDictionary)>(GetProcAddress(module, "inflateGetDictionary")),
        };
    }();
    return bindings.init && bindings.inflate_chunk && bindings.end && bindings.checksum ? &bindings : nullptr;
}

class Mp3StartupLimit : public exception_io_data {
public:
    explicit Mp3StartupLimit(const char* message) : exception_io_data(message) {}
};

doujin::CheckpointCacheBudget seek_cache_budget{128 * 1024 * 1024};

class DeflatedMember {
    struct Lane {
        Lane(const doujin::DeflateApi& api, const doujin::ZipMember& entry, const doujin::DeflateCheckpoint* point,
             abort_callback& abort) :
            decoder(api, entry, point), first(point ? point->output : 0), ready(first),
            fetched(point ? point->input : 0) {
            filesystem::g_open_temp(cache_file, abort);
        }

        bool contains(std::uint64_t at, std::uint64_t end, std::uint64_t member_size) const {
            return first <= at && end <= ready && (end < member_size || decoder.finished());
        }

        doujin::DeflateStream decoder;
        file::ptr cache_file;
        std::uint64_t first, ready, fetched, next_checkpoint = 0;
        Bytes input;
        std::size_t input_used = 0, input_size = 0;
    };

public:
    DeflatedMember(std::shared_ptr<RangeObject> resource, doujin::ZipMember entry, const doujin::DeflateApi& api,
                   abort_callback& abort) :
        resource_(std::move(resource)), entry_(entry), api_(api),
        checkpoints_(entry, api, api.supports_checkpoints() ? resource_->checkpoint_directory(entry) : std::filesystem::path{},
                     "-" + std::to_string(GetCurrentProcessId()), &seek_cache_budget,
                     doujin::normalize_checkpoint_mib(checkpoint_spacing.get()) * doujin::checkpoint_mib),
        prefix_(api, entry, nullptr, abort) {}

    std::uint64_t size() const { return entry_.size; }
    struct Restores { std::uint64_t queries, count, compressed_advance; };
    Restores restores() const { return {lookup_count_.load(), restore_count_.load(), compressed_advance_.load()}; }

    void build_checkpoints(threaded_process_status& progress, abort_callback& abort) {
        if (!checkpoints_.can_persist()) throw exception_io_data("Archive supplied no strong revision validator");
        std::array<unsigned char, 64 * 1024> buffer{};
        std::uint64_t checked = 0;
        for (;;) {
            abort.check();
            std::uint64_t target;
            {
                std::unique_lock<std::timed_mutex> lock(mutex_, std::defer_lock);
                while (!lock.try_lock_for(std::chrono::milliseconds(25))) abort.check();
                checkpoints_.apply_retention();
                target = checkpoints_.next_missing_after(checked);
            }
            if (target == size()) break;
            const auto end = std::min(size(), target + checkpoints_.disk_spacing());
            for (auto at = target; at < end;) {
                progress.poll_pause();
                progress.set_progress_float(static_cast<double>(at) / static_cast<double>(size()));
                at += read_at(at, buffer.data(), static_cast<t_size>(std::min<std::uint64_t>(buffer.size(), end - at)), abort);
                std::lock_guard lock(mutex_);
                if (checkpoints_.have_saved_at(target)) break;
            }
            checked = target;
        }
    }

    t_size read_at(std::uint64_t at, void* data, t_size bytes, abort_callback& abort,
                   doujin::Mp3StartupBudget* budget = nullptr, bool playback = false, bool sequential = false) {
        const auto wanted = static_cast<t_size>(std::min<std::uint64_t>(bytes, size() - at));
        if (!wanted) return 0;
        const auto end = at + wanted;
        for (;;) {
            abort.check();
            std::unique_lock<std::timed_mutex> lock(mutex_, std::defer_lock);
            while (!lock.try_lock_for(std::chrono::milliseconds(25))) abort.check();
            if (failure_) std::rethrow_exception(failure_);
            auto* cached = prefix_.contains(at, end, size()) ? &prefix_ : nullptr;
            if (!cached && resumed_ && resumed_->contains(at, end, size())) cached = resumed_.get();
            if (cached) {
                cached->cache_file->seek(at - cached->first, abort);
                cached->cache_file->read_object(data, wanted, abort);
                return wanted;
            }
            try {
                auto& lane = lane_for(at, abort);
                if (at > lane.ready + 4 * stream_block_size && logged_distant_read_ != at) {
                    logged_distant_read_ = at;
                    console::print(("DoujinStyle: uncached DEFLATE read at member byte " + std::to_string(at) +
                        " needs inflation from byte " + std::to_string(lane.ready)).c_str());
                }
                pump(lane, end, abort, budget, playback, sequential);
            } catch (const Mp3StartupLimit&) { throw; }
            catch (const exception_aborted&) { throw; }
            catch (...) { failure_ = std::current_exception(); throw; }
        }
    }

private:
    Lane& lane_for(std::uint64_t at, abort_callback& abort) {
        auto* lane = &prefix_;
        if (resumed_ && resumed_->first <= at && resumed_->ready > lane->ready) lane = resumed_.get();
        // Preserve both the sequential prefix and one recent seek region for other readers.
        if (api_.supports_checkpoints() && at > lane->ready + block_size) {
            ++lookup_count_;
            checkpoints_.apply_retention();
            const auto* point = checkpoints_.at_or_before(at);
            if (point && point->output > lane->ready + block_size) {
                const auto prior_input = lane->fetched;
                resumed_ = as_io([&] { return std::make_unique<Lane>(api_, entry_, point, abort); });
                if (point->input > prior_input) compressed_advance_.fetch_add(point->input - prior_input);
                ++restore_count_;
                console::print(("DoujinStyle: restored DEFLATE checkpoint at member byte " +
                                std::to_string(point->output) + ", compressed byte " + std::to_string(point->input)).c_str());
                lane = resumed_.get();
            }
        }
        return *lane;
    }

    void pump(Lane& lane, std::uint64_t end, abort_callback& abort, doujin::Mp3StartupBudget* budget,
              bool playback, bool sequential) {
        if (lane.input_used == lane.input_size && lane.fetched < entry_.packed_size) {
            // The inflater needs a small compressed input window even for a short decoder read.
            const auto need = end > lane.ready ? end - lane.ready : 0;
            const auto input_window = doujin::deflate_input_window(need, playback, sequential);
            const auto available = budget ? budget->compressed_available() : UINT64_MAX;
            if (!available) {
                budget->reject();
                throw Mp3StartupLimit("MP3 startup compressed-input limit reached");
            }
            const auto wanted = static_cast<std::size_t>(std::min({input_window, entry_.packed_size - lane.fetched, available}));
            lane.input.resize(wanted);
            if (resource_->read_at(entry_.offset + lane.fetched, lane.input.data(), wanted, abort,
                                   0, entry_.offset + entry_.packed_size, wanted) != wanted)
                throw exception_io_data("Short read from compressed ZIP member");
            if (budget) budget->consumed_compressed(wanted);
            lane.fetched += wanted;
            lane.input_size = wanted;
            lane.input_used = 0;
        }
        abort.check();
        std::array<unsigned char, 64 * 1024> output;
        const auto next = checkpoints_.next_after(lane.ready);
        if (api_.supports_checkpoints() && lane.ready + output.size() >= next && lane.next_checkpoint == 0)
            lane.next_checkpoint = next;
        const auto chunk = as_io([&] {
            return lane.decoder.step(std::span(lane.input).subspan(lane.input_used, lane.input_size - lane.input_used),
                                     output, lane.next_checkpoint != 0);
        });
        // Publish the disk block and inflater state together. Cancellation is checked before the next step.
        if (chunk.produced) {
            lane.cache_file->seek(lane.ready - lane.first, fb2k::noAbort);
            lane.cache_file->write(output.data(), chunk.produced, fb2k::noAbort);
            lane.ready += chunk.produced;
        }
        lane.input_used += chunk.consumed;
        if (lane.next_checkpoint != 0) {
            if (auto point = lane.decoder.checkpoint(lane.next_checkpoint)) {
                checkpoints_.apply_retention();
                checkpoints_.upsert(std::move(*point));
                lane.next_checkpoint = 0;
            }
        }
    }

    std::shared_ptr<RangeObject> resource_;
    doujin::ZipMember entry_;
    doujin::DeflateApi api_;
    doujin::CheckpointIndex checkpoints_;
    Lane prefix_;
    std::unique_ptr<Lane> resumed_;
    std::uint64_t logged_distant_read_ = 0;
    std::atomic<std::uint64_t> lookup_count_{0}, restore_count_{0}, compressed_advance_{0};
    std::timed_mutex mutex_;
    std::exception_ptr failure_;
};

std::shared_ptr<DeflatedMember> shared_deflated_member(const std::shared_ptr<RangeObject>& resource,
                                                     const doujin::ZipMember& entry, abort_callback& abort) {
    const auto* api = host_deflate_api();
    if (!api) throw exception_io_data("The foobar2000 zlib runtime is unavailable");
    static std::mutex mutex;
    static std::map<std::pair<RangeObject*, std::uint64_t>, std::weak_ptr<DeflatedMember>> members;
    std::lock_guard lock(mutex);
    std::erase_if(members, [](const auto& member) { return member.second.expired(); });
    auto& slot = members[{resource.get(), entry.offset}];
    if (auto running = slot.lock()) return running;
    auto member = as_io([&] { return std::make_shared<DeflatedMember>(resource, entry, *api, abort); });
    slot = member;
    return member;
}

class DeflateCursor : public file_readonly {
public:
    explicit DeflateCursor(std::shared_ptr<DeflatedMember> member) : member_(std::move(member)) {}
    t_size read(void* buffer, t_size bytes, abort_callback& abort) override {
        const auto done = member_->read_at(position_, buffer, bytes, abort, nullptr, coalescing_, sequential_);
        position_ += done;
        sequential_ = done != 0;
        return done;
    }
    t_filesize get_size(abort_callback&) override { return member_->size(); }
    t_filesize get_position(abort_callback&) override { return position_; }
    void seek(t_filesize at, abort_callback& abort) override {
        abort.check();
        if (at > member_->size()) throw exception_io_seek_out_of_range();
        position_ = at;
        sequential_ = false;
    }
    void reopen(abort_callback& abort) override { seek(0, abort); }
    bool can_seek() override { return true; }
    bool is_remote() override { return true; }
    bool get_content_type(pfc::string_base&) override { return false; }
    void start_coalescing() { coalescing_ = coalesce_deflate_ranges.get(); }

private:
    std::shared_ptr<DeflatedMember> member_;
    t_filesize position_ = 0;
    std::atomic<bool> coalescing_{false};
    bool sequential_ = false;
};

file::ptr open_member_cursor(const std::shared_ptr<RangeObject>& resource, const doujin::ZipMember& entry,
                             abort_callback& abort) {
    if (entry.method == 0) return fb2k::service_new<RangeCursor>(resource, entry.offset, entry.size);
    return fb2k::service_new<DeflateCursor>(shared_deflated_member(resource, entry, abort));
}

class Mp3PlaybackFile : public file_readonly {
public:
    Mp3PlaybackFile(std::shared_ptr<DeflatedMember> member, bool seekable) :
        member_(std::move(member)), seekable_(seekable) {}

    t_size read(void* buffer, t_size bytes, abort_callback& abort) override {
        abort.check();
        if (buffered_.is_valid()) return buffered_->read(buffer, bytes, abort);
        const auto requested = static_cast<t_size>(std::min<std::uint64_t>(bytes, member_->size() - position_));
        if (!budget_.allow_read(position_, requested)) {
            console::print(("DoujinStyle: MP3 startup blocked read at member byte " + std::to_string(position_) +
                " | requested " + std::to_string(requested) + " bytes").c_str());
            throw Mp3StartupLimit("MP3 decoder startup read limit reached");
        }
        const auto done = member_->read_at(position_, buffer, requested, abort, &budget_);
        position_ += done;
        return done;
    }

    t_filesize get_size(abort_callback&) override { return seekable_ ? member_->size() : filesize_invalid; }
    t_filesize get_position(abort_callback& abort) override {
        return buffered_.is_valid() ? buffered_->get_position(abort) : position_;
    }

    void seek(t_filesize at, abort_callback& abort) override {
        abort.check();
        if (!seekable_) throw exception_io_object_not_seekable();
        if (at > member_->size()) throw exception_io_seek_out_of_range();
        if (!budget_.allow_seek(at)) {
            console::print(("DoujinStyle: MP3 startup blocked seek to member byte " + std::to_string(at)).c_str());
            throw Mp3StartupLimit("MP3 decoder startup sought outside the prefix");
        }
        if (buffered_.is_valid()) buffered_->seek(at, abort);
        else position_ = at;
    }

    void reopen(abort_callback& abort) override {
        abort.check();
        if (buffered_.is_valid()) buffered_->reopen(abort);
        else position_ = 0;
    }

    bool can_seek() override { return seekable_; }
    bool is_remote() override { return true; }
    bool get_content_type(pfc::string_base&) override { return false; }
    bool rejected() const { return budget_.rejected(); }
    void permit_playback_reads() { budget_.finish(); }

    void install_buffering(abort_callback& abort) {
        if (const auto buffering = foobar2000_io::read_ahead_tools::tryGet()) {
            auto cursor = fb2k::service_new<DeflateCursor>(member_);
            cursor->start_coalescing();
            file::ptr source = cursor;
            source->seek(position_, abort);
            buffering->open_file_helper(source, nullptr, abort);
            if (source.is_valid() && source->get_position(abort) == position_) {
                buffered_ = std::move(source);
                console::print("DoujinStyle: MP3 running with foobar2000 playback buffering");
            }
        }
    }

private:
    std::shared_ptr<DeflatedMember> member_;
    bool seekable_;
    t_filesize position_ = 0;
    file::ptr buffered_;
    doujin::Mp3StartupBudget budget_;
};

class ArchiveFilesystem : public filesystem {
public:
    bool is_our_path(const char* path) override {
        return path && (std::string_view(path).starts_with(archive_prefix) || std::string_view(path).starts_with(member_prefix));
    }
    bool get_canonical_path(const char* path, pfc::string_base& out) override {
        if (!is_our_path(path)) return false;
        out = path;
        return true;
    }
    bool get_display_path(const char* path, pfc::string_base& out) override {
        if (!is_our_path(path)) return false;
        out = "DoujinStyle remote ZIP";
        return true;
    }
    void open(file::ptr& out, const char* path, t_open_mode mode, abort_callback& abort) override {
        if ((mode & open_mode_mask) != open_mode_read) throw exception_io_denied();
        const bool inner = std::string_view(path).starts_with(member_prefix);
        const auto [url, referrer, member, zip_locator] = as_io([&] {
            std::string_view locator(path);
            if (inner) {
                locator.remove_prefix(member_prefix.size());
                const auto slash = locator.find('/');
                if (slash == locator.npos || slash + 1 == locator.size())
                    throw std::runtime_error("invalid remote audio member address");
                locator = locator.substr(0, slash);
            } else {
                if (!locator.starts_with(archive_prefix) || !locator.ends_with(".zip"))
                    throw std::runtime_error("invalid remote ZIP address");
                locator.remove_prefix(archive_prefix.size());
                locator.remove_suffix(4);
            }
            const auto member_separator = locator.find('~');
            const auto member_address = member_separator == locator.npos ? std::string_view{} :
                                        locator.substr(member_separator + 1);
            const auto locator_separator = member_address.find('!');
            auto member = doujin::decode_hex(member_address.substr(0, locator_separator));
            std::optional<doujin::ZipLocator> zip_locator;
            if (locator_separator != member_address.npos) {
                if (member.empty()) throw std::runtime_error("missing remote audio member name");
                zip_locator = doujin::parse_zip_locator(member_address.substr(locator_separator + 1));
            }
            locator = locator.substr(0, member_separator);
            const auto dot = locator.find('.');
            auto value = doujin::decode_hex(locator.substr(0, dot));
            auto source = dot == locator.npos ? std::string() : doujin::decode_hex(locator.substr(dot + 1));
            doujin::validate_archive_url(value);
            if (!source.empty()) doujin::validate_url(source);
            if (inner && member.empty()) throw std::runtime_error("missing remote audio member name");
            return std::tuple{std::move(value), std::move(source), std::move(member), std::move(zip_locator)};
        });
        auto resource = as_io([&] { return shared_range_object(url, referrer, abort); });
        if (member.empty()) {
            out = fb2k::service_new<RangeCursor>(resource, 0, resource->size());
        } else {
            const auto entry = locate_member(*resource, member, abort, zip_locator);
            if (!entry) throw exception_io_data("ZIP member no longer matches the archive directory");
            if (inner) {
                out = open_member_cursor(resource, *entry, abort);
            } else {
                auto envelope = as_io([&] { return doujin::make_single_member_zip(*entry, member); });
                out = fb2k::service_new<RangeCursor>(resource, entry->offset, entry->packed_size, std::move(envelope));
            }
        }
    }
    void get_stats(const char* path, t_filestats& stats, bool& writable, abort_callback& abort) override {
        file::ptr reader;
        open(reader, path, open_mode_read, abort);
        stats = reader->get_stats(abort);
        writable = false;
    }
    bool is_remote(const char*) override { return true; }
    bool supports_content_types() override { return false; }
    void list_directory(const char*, directory_callback&, abort_callback&) override {
        throw exception_io_denied();
    }
    void remove(const char*, abort_callback&) override { throw exception_io_denied(); }
    void move(const char*, const char*, abort_callback&) override { throw exception_io_denied(); }
    void create_directory(const char*, abort_callback&) override { throw exception_io_denied(); }
};

doujin::Descriptor read_track(const file::ptr& file, abort_callback& abort, std::string* fingerprint = nullptr) {
    const auto size = file->get_size(abort);
    if (size == 0 || size > 65536) throw exception_io_data("Invalid Doujin track reference size");
    if (file->get_position(abort) != 0) file->seek(0, abort);
    std::string text(static_cast<std::size_t>(size), '\0');
    file->read_object(text.data(), text.size(), abort);
    if (fingerprint) *fingerprint = doujin::sha256_key(text);
    return as_io([&] { return doujin::parse_descriptor(text); });
}

constexpr std::string_view metadata_signature = "DOUJIN-PLAYBACK-METADATA 1\n";
constexpr std::size_t metadata_limit = 128 * 1024;

bool load_playback_metadata(const std::filesystem::path& path, file_info& output, abort_callback& abort) {
    if (path.empty()) return false;
    try {
        std::ifstream stream(path, std::ios::binary | std::ios::ate);
        const auto size = stream.tellg();
        if (size <= 0 || static_cast<std::uintmax_t>(size) > metadata_limit) return false;
        std::string bytes(static_cast<std::size_t>(size), '\0');
        stream.seekg(0);
        if (!stream.read(bytes.data(), bytes.size()) || !bytes.starts_with(metadata_signature)) return false;
        stream_reader_memblock_ref reader(bytes.data() + metadata_signature.size(),
                                          bytes.size() - metadata_signature.size());
        file_info_impl recovered;
        recovered.from_stream(&reader, abort);
        if (reader.get_remaining() || !std::isfinite(recovered.get_length()) || recovered.get_length() < 0)
            return false;
        // The SDK's from_stream() reads the ReplayGain fields but does not assign them.
        reader.reset();
        stream_reader_formatter<> format(reader, abort);
        double duration;
        replaygain_info gain;
        format >> duration >> gain.m_track_gain >> gain.m_album_gain >> gain.m_track_peak >> gain.m_album_peak;
        recovered.set_replaygain(gain);
        output.copy(recovered);
        return true;
    } catch (const exception_aborted&) {
        throw;
    } catch (...) {
        return false;
    }
}

bool save_playback_metadata(const std::filesystem::path& path, const stream_writer_buffer_simple& snapshot) {
    const auto size = snapshot.m_buffer.get_size();
    if (path.empty() || size > metadata_limit - metadata_signature.size()) return false;
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    if (error) return false;
    auto temporary = path;
    temporary += L"." + std::to_wstring(GetCurrentProcessId()) + L"." +
        std::to_wstring(GetCurrentThreadId()) + L".tmp";
    std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
    stream.write(metadata_signature.data(), metadata_signature.size());
    stream.write(reinterpret_cast<const char*>(snapshot.m_buffer.get_ptr()), size);
    stream.close();
    if (stream && MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) return true;
    std::filesystem::remove(temporary, error);
    return false;
}

bool cover_image(const void* bytes, std::size_t size) {
    return size >= 8 && (std::memcmp(bytes, "\xff\xd8\xff", 3) == 0 ||
                        std::memcmp(bytes, "\x89PNG\r\n\x1a\n", 8) == 0);
}

album_art_data_ptr remote_site_picture(const std::string& path, const std::string& referrer,
                                      bool icon, abort_callback& abort) {
    using Clock = std::chrono::steady_clock;
    struct Entry { album_art_data_ptr cover; Clock::time_point retry; };
    static std::mutex mutex;
    static std::unordered_map<std::string, Entry> cache;
    // Normal album-art jobs run on a worker. A main-thread query remains local.
    {
        std::lock_guard lock(mutex);
        const auto now = Clock::now();
        if (const auto found = cache.find(path); found != cache.end() && found->second.retry > now)
            return found->second.cover;
        if (core_api::is_main_thread()) return {};
        if (cache.size() >= 32) cache.erase(cache.begin());
        cache[path] = {{}, now + std::chrono::seconds(60)};
    }
    try {
        RangeTransport transport("https://doujinstyle.com" + path, referrer, abort);
        HttpResponse response;
        transport.open_site_image(abort, response);
        if (response.status != 200) return {};
        const auto limit = icon ? doujin::site_icon_bytes : doujin::page_art_bytes;
        const auto length = query_header(response.request.value, WINHTTP_QUERY_CONTENT_LENGTH);
        if (!length.empty()) {
            wchar_t* end = nullptr;
            const auto count = std::wcstoull(length.c_str(), &end, 10);
            if (end != length.c_str() + length.size() || !count || count > limit) return {};
        }
        Bytes data(limit + 1);
        std::size_t size = 0;
        while (size < data.size()) {
            abort.check();
            DWORD count = 0;
            if (!WinHttpReadData(response.request.value, data.data() + size, static_cast<DWORD>(data.size() - size), &count))
                return {};
            if (!count) break;
            size += count;
        }
        auto cover = doujin::site_art(std::span(data).first(size), icon);
        if (cover.is_empty()) return {};
        std::lock_guard lock(mutex);
        cache[path] = {cover, Clock::now() + std::chrono::hours(24)};
        return cover;
    } catch (const exception_aborted&) {
        std::lock_guard lock(mutex);
        cache.erase(path);
        throw;
    } catch (const exception_io&) {
        return {};
    }
}

album_art_data_ptr cached_site_picture(const char* name, bool icon, abort_callback& abort) {
    pfc::string8 path;
    path.set_string(core_api::get_profile_path());
    path.add_filename("DoujinStyle");
    path.add_filename("Artwork");
    path.add_filename(name);
    try {
        file::ptr picture;
        filesystem::g_open_read(picture, path, abort);
        const auto size = picture->get_size(abort);
        if (size == 0 || size > (icon ? doujin::site_icon_bytes : doujin::page_art_bytes)) return {};
        Bytes bytes(static_cast<std::size_t>(size));
        picture->read_object(bytes.data(), bytes.size(), abort);
        return doujin::site_art(bytes, icon);
    } catch (const exception_aborted&) {
        throw;
    } catch (const exception_io&) {
        return {};
    }
}

constexpr std::size_t artwork_limit = 4 * 1024 * 1024;

bool archive_image(const void* bytes, std::size_t size) {
    if (cover_image(bytes, size)) return true;
    if (size < 12) return false;
    const std::string_view signature(static_cast<const char*>(bytes), 12);
    return signature.starts_with("GIF87a") || signature.starts_with("GIF89a") ||
           signature.starts_with("BM") ||
           (signature.starts_with("RIFF") && signature.substr(8) == "WEBP");
}

// Decoders can read tags at either end of a stored member. A seek deep into
// DEFLATE would inflate the skipped bytes, so that case is limited separately.
class ArtProbeCursor : public file_readonly {
public:
    ArtProbeCursor(file::ptr source, bool compressed) : source_(std::move(source)), compressed_(compressed) {}
    t_size read(void* output, t_size size, abort_callback& abort) override {
        if (size > remaining_ || (compressed_ && size > artwork_limit - get_position(abort)))
            throw exception_io_data("Embedded artwork probe reached its byte limit");
        const auto count = source_->read(output, size, abort);
        remaining_ -= count;
        return count;
    }
    t_filesize get_size(abort_callback& abort) override { return source_->get_size(abort); }
    t_filesize get_position(abort_callback& abort) override { return source_->get_position(abort); }
    void seek(t_filesize position, abort_callback& abort) override {
        if (compressed_ && position > artwork_limit) throw exception_io_seek_out_of_range();
        source_->seek(position, abort);
    }
    void reopen(abort_callback& abort) override { source_->reopen(abort); }
    bool can_seek() override { return source_->can_seek(); }
    bool is_remote() override { return true; }
    bool get_content_type(pfc::string_base& type) override { return source_->get_content_type(type); }
private:
    file::ptr source_;
    t_size remaining_ = artwork_limit;
    bool compressed_;
};

file::ptr archive_art_cursor(const std::shared_ptr<RangeObject>& resource, const doujin::Descriptor& track,
                             const doujin::ZipMember& member, const std::string& name, abort_callback& abort) {
    if (member.method == 0 || host_deflate_api()) return open_member_cursor(resource, member, abort);
    auto locator = doujin::encode_hex(track.url);
    if (!track.referrer.empty()) locator += "." + doujin::encode_hex(track.referrer);
    const auto outer = std::string(archive_prefix) + locator + "~" + doujin::encode_hex(name) + ".zip";
    pfc::string8 path;
    archive_impl::g_make_unpack_path(path, outer.c_str(), name.c_str(), "zip");
    file::ptr source;
    filesystem::g_open_read(source, path.c_str(), abort);
    return source;
}

album_art_data_ptr extract_member_picture(const doujin::Descriptor& track,
                                          const std::shared_ptr<RangeObject>& resource,
                                          const GUID& type, abort_callback& abort) {
    try {
        if (!album_art_extractor::g_is_supported_path(track.filename.c_str())) return {};
        file::ptr source;
        bool compressed = false;
        if (resource) {
            const auto entry = locate_member(*resource, track.member, abort, track.zip_locator);
            if (!entry || (entry->method == 8 && !host_deflate_api())) return {};
            compressed = entry->method == 8;
            source = open_member_cursor(resource, *entry, abort);
        } else {
            filesystem::g_open_read(source, track.url.c_str(), abort);
        }
        file::ptr file = fb2k::service_new<ArtProbeCursor>(source, compressed);
        auto extractor = album_art_extractor::g_open(file, track.filename.c_str(), abort);
        if (extractor.is_empty()) return {};
        auto picture = extractor->query(type, abort);
        if (picture.is_valid() && picture->size() <= artwork_limit) return picture;
    } catch (const exception_aborted&) {
        throw;
    } catch (const std::exception&) {}
    return {};
}

std::optional<doujin::ArtworkType> archive_art_type(const GUID& type) {
    if (type == album_art_ids::cover_front) return doujin::ArtworkType::front;
    if (type == album_art_ids::cover_back) return doujin::ArtworkType::back;
    if (type == album_art_ids::disc) return doujin::ArtworkType::disc;
    if (type == album_art_ids::artist) return doujin::ArtworkType::artist;
    return {};
}

album_art_data_ptr extract_folder_picture(const doujin::Descriptor& track,
                                          const std::shared_ptr<RangeObject>& resource,
                                          doujin::ArtworkType type, abort_callback& abort) {
    const auto album = track.metadata.find("ALBUM");
    const auto names = doujin::find_zip_artwork(resource->size(), track.member,
        album != track.metadata.end() ? std::string_view(album->second) : std::string_view{},
        type, artwork_limit, [&](auto offset, auto bytes) {
            abort.check();
            if (resource->read_at(offset, bytes.data(), bytes.size(), abort) != bytes.size())
                throw exception_io_data("Short read from ZIP artwork directory");
        });
    unsigned attempts = 0;
    for (const auto& name : names) {
        if (++attempts > 3) break;
        try {
            const auto entry = locate_member(*resource, name, abort);
            if (!entry || entry->size > artwork_limit || entry->packed_size > artwork_limit) continue;
            const auto file = archive_art_cursor(resource, track, *entry, name, abort);
            auto art = album_art_data_impl::g_create(file.get_ptr(), static_cast<t_size>(entry->size), abort);
            if (!archive_image(art->data(), art->size())) continue;
            if (const auto* zlib = host_deflate_api())
                if (zlib->checksum(0, static_cast<const unsigned char*>(art->data()),
                                   static_cast<unsigned>(art->size())) != entry->crc) continue;
            return art;
        } catch (const exception_aborted&) {
            throw;
        } catch (const exception_io&) {}
    }
    return {};
}

class ArchivePictureCache {
public:
    album_art_data_ptr lookup(const std::string& key) {
        if (key.empty()) return {};
        std::lock_guard lock(mutex_);
        const auto now = PlaybackClock::now();
        std::erase_if(entries_, [&](const auto& entry) {
            if (entry.expires > now) return false;
            bytes_ -= entry.picture->size();
            return true;
        });
        const auto found = std::find_if(entries_.begin(), entries_.end(), [&](const auto& entry) {
            return entry.key == key;
        });
        if (found == entries_.end()) return {};
        entries_.splice(entries_.begin(), entries_, found);
        return entries_.front().picture;
    }

    album_art_data_ptr remember(const std::string& key, album_art_data_ptr picture) {
        if (key.empty()) return picture;
        std::lock_guard lock(mutex_);
        std::erase_if(entries_, [&](const auto& entry) {
            if (entry.key != key) return false;
            bytes_ -= entry.picture->size();
            return true;
        });
        constexpr std::size_t byte_limit = 24 * 1024 * 1024;
        while (!entries_.empty() && (entries_.size() >= 96 || bytes_ + picture->size() > byte_limit)) {
            bytes_ -= entries_.back().picture->size();
            entries_.pop_back();
        }
        entries_.push_front({key, picture, PlaybackClock::now() + std::chrono::minutes(30)});
        bytes_ += picture->size();
        return picture;
    }

private:
    struct Entry {
        std::string key;
        album_art_data_ptr picture;
        PlaybackClock::time_point expires;
    };
    std::mutex mutex_;
    std::list<Entry> entries_;
    std::size_t bytes_ = 0;
};

class TrackCover : public album_art_extractor_instance {
public:
    explicit TrackCover(doujin::Descriptor track) : track_(std::move(track)) {}

    album_art_data_ptr query(const GUID& what, abort_callback& abort) override {
        static ArchivePictureCache pictures;
        abort.check();
        const auto key = doujin::sha256_key(track_.url + "\n" + track_.referrer + "\n" + track_.member + "\n" +
                                    std::string(reinterpret_cast<const char*>(&what), sizeof what));
        if (auto cached = pictures.lookup(key); cached.is_valid()) return cached;
        if (!core_api::is_main_thread()) {
            try {
                std::shared_ptr<RangeObject> resource;
                if (!track_.member.empty()) resource = shared_range_object(track_.url, track_.referrer, abort);
                if (auto embedded = extract_member_picture(track_, resource, what, abort); embedded.is_valid())
                    return pictures.remember(key, embedded);
                if (auto type = archive_art_type(what); resource && type)
                    if (auto folder = extract_folder_picture(track_, resource, *type, abort); folder.is_valid())
                        return pictures.remember(key, folder);
            } catch (const exception_aborted&) {
                throw;
            } catch (const std::exception&) {}
        }
        throw exception_album_art_not_found();
    }

private:
    doujin::Descriptor track_;
};

class TrackCoverExtractor : public album_art_extractor_v2 {
    bool is_our_path(const char*, const char* extension) override { return stricmp_utf8(extension, "djt") == 0; }
    GUID get_guid() override { return track_input_id; }

    album_art_extractor_instance_ptr open(file_ptr file, const char* path, abort_callback& abort) override {
        if (!file) filesystem::g_open_read(file, path, abort);
        auto track = read_track(file, abort);
        if (track.release_id.empty()) track.release_id = doujin::release_id_from_filename(path);
        if (track.referrer.empty() && !track.release_id.empty() && mediafire_share(track.url))
            track.referrer = release_url(track.release_id);
        return fb2k::service_new<TrackCover>(std::move(track));
    }
};

class SiteCover : public album_art_extractor_instance_v2 {
public:
    explicit SiteCover(std::vector<std::string> releases = {}) : releases_(std::move(releases)) {}

    album_art_data_ptr query(const GUID& what, abort_callback& abort) override {
        if (releases_.empty() || what != album_art_ids::cover_front) throw exception_album_art_not_found();
        for (const auto& id : releases_) {
            abort.check();
            const auto name = id + ".jpg";
            if (auto picture = cached_site_picture(name.c_str(), false, abort); picture.is_valid()) return picture;
            const auto referrer = release_url(id);
            if (auto picture = remote_site_picture("/thumbs/" + name, referrer, false, abort); picture.is_valid())
                return picture;
            if (auto picture = remote_site_picture("/covers/" + name, referrer, false, abort); picture.is_valid())
                return picture;
        }
        if (auto picture = cached_site_picture("site-icon.img", true, abort); picture.is_valid()) return picture;
        if (auto picture = remote_site_picture("/favicon.ico", "https://doujinstyle.com/", true, abort); picture.is_valid())
            return picture;
        throw exception_album_art_not_found();
    }

    album_art_path_list::ptr query_paths(const GUID&, abort_callback&) override {
        return fb2k::service_new<album_art_path_list_dummy>();
    }
private:
    std::vector<std::string> releases_;
};

class SiteCoverFallback : public album_art_fallback {
public:
    album_art_extractor_instance_v2::ptr open(metadb_handle_list_cref items, pfc::list_base_const_t<GUID> const& ids,
                                              abort_callback& abort) override {
        bool front = false;
        for (t_size i = 0; i < ids.get_count(); ++i) front = front || ids[i] == album_art_ids::cover_front;
        // The host queries the returned extractor even when this provider has no artwork.
        if (!front) return fb2k::service_new<SiteCover>();
        std::vector<std::string> releases;
        const auto paths = std::min<t_size>(items.get_count(), 32);
        for (t_size i = 0; i < paths && releases.size() < 3; ++i) {
            abort.check();
            const auto path = items[i]->get_path();
            if (stricmp_utf8(pfc::string_extension(path), "djt") != 0) continue;
            try {
                file::ptr file;
                filesystem::g_open_read(file, path, abort);
                auto id = read_track(file, abort).release_id;
                if (id.empty()) id = doujin::release_id_from_filename(path);
                if (!id.empty() && std::find(releases.begin(), releases.end(), id) == releases.end())
                    releases.push_back(std::move(id));
            } catch (const exception_aborted&) {
                throw;
            } catch (const exception_io&) {}
        }
        return fb2k::service_new<SiteCover>(std::move(releases));
    }
};

doujin::Descriptor read_playback_reference(const file::ptr& file, const char* path, abort_callback& abort,
                                          std::string* fingerprint = nullptr) {
    auto track = read_track(file, abort, fingerprint);
    if (track.release_id.empty()) track.release_id = doujin::release_id_from_filename(path);
    if (track.referrer.empty() && !track.release_id.empty() && mediafire_share(track.url))
        track.referrer = release_url(track.release_id);
    return track;
}

class BuildSeekIndexMenu : public contextmenu_item_simple {
    GUID get_parent() override { return contextmenu_groups::root; }
    unsigned get_num_items() override { return 1; }
    GUID get_item_guid(unsigned) override { return {0x14731de4, 0xf352, 0x4006, {0xad,0xc8,0x57,0x7e,0x1a,0x18,0x6f,0x52}}; }
    void get_item_name(unsigned, pfc::string_base& out) override { out = "Build DoujinStyle seek index"; }
    bool get_item_description(unsigned, pfc::string_base& out) override {
        out = "Fill missing seek checkpoints for a selected DEFLATE ZIP track.";
        return true;
    }
    bool context_get_display(unsigned item, metadb_handle_list_cref items, pfc::string_base& out,
                             unsigned& flags, const GUID& caller) override {
        return eligible(items) && contextmenu_item_simple::context_get_display(item, items, out, flags, caller);
    }
    void context_command(unsigned, metadb_handle_list_cref items, const GUID&) override {
        if (!eligible(items)) return;
        auto callback = threaded_process_callback_lambda::create(
            [path = std::string(items[0]->get_path())](threaded_process_status& progress, abort_callback& abort) {
                static std::mutex job;
                std::unique_lock lock(job, std::try_to_lock);
                if (!lock.owns_lock()) {
                    console::print("DoujinStyle: a seek index job is already running");
                    return;
                }
                try {
                    progress.set_item_path(path.c_str());
                    const auto started = PlaybackClock::now();
                    file::ptr file;
                    filesystem::g_open_read(file, path.c_str(), abort);
                    const auto track = read_playback_reference(file, path.c_str(), abort);
                    if (track.member.empty()) throw exception_io_data("This track has no ZIP member");
                    const auto resource = shared_range_object(track.url, track.referrer, abort);
                    const auto body_at_start = resource->body_bytes();
                    const auto entry = locate_member(*resource, track.member, abort, track.zip_locator);
                    if (!entry || entry->method != 8) throw exception_io_data("This track has no supported DEFLATE member");
                    if (entry->packed_size > 128 * doujin::checkpoint_mib || entry->size > 256 * doujin::checkpoint_mib)
                        throw exception_io_data("Member exceeds the manual index job's 128 MiB packed or 256 MiB output limit");
                    shared_deflated_member(resource, *entry, abort)->build_checkpoints(progress, abort);
                    console::print(("DoujinStyle: seek index pass complete | " + elapsed_text(started) +
                        " | " + std::to_string(resource->body_bytes() - body_at_start) + " archive response bytes").c_str());
                } catch (const exception_aborted&) { throw; }
                catch (const std::exception& error) {
                    console::print((std::string("DoujinStyle: seek index job | ") + error.what()).c_str());
                }
            });
        threaded_process::g_run_modeless(callback, threaded_process::flag_show_abort |
            threaded_process::flag_show_delayed | threaded_process::flag_show_progress,
            core_api::get_main_window(), "Build DoujinStyle seek index");
    }
    static bool eligible(metadb_handle_list_cref items) {
        return items.get_count() == 1 && std::string_view(items[0]->get_path()).ends_with(".djt");
    }
};

class ReleaseMenu : public contextmenu_item_simple {
    GUID get_parent() override { return contextmenu_groups::root; }
    unsigned get_num_items() override { return 1; }
    GUID get_item_guid(unsigned) override { return {0xaccd21f5, 0xaba6, 0x4de1, {0x84,0x1d,0xb5,0xaa,0x08,0xc3,0x99,0xd2}}; }
    void get_item_name(unsigned, pfc::string_base& out) override { out = "Open DoujinStyle release"; }
    bool get_item_description(unsigned, pfc::string_base& out) override {
        out = "Open this track's DoujinStyle listing in the default browser.";
        return true;
    }
    bool context_get_display(unsigned item, metadb_handle_list_cref data, pfc::string_base& out,
                             unsigned& flags, const GUID& caller) override {
        return !url(data).empty() && contextmenu_item_simple::context_get_display(item, data, out, flags, caller);
    }
    void context_command(unsigned, metadb_handle_list_cref data, const GUID&) override {
        const auto link = url(data);
        if (!link.empty()) {
            const auto target = http_wide(link);
            ShellExecuteW(core_api::get_main_window(), L"open", target.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
        }
    }
    static std::string url(metadb_handle_list_cref data) {
        if (data.get_count() != 1 || !std::string_view(data[0]->get_path()).ends_with(".djt")) return {};
        file_info_impl info;
        if (data[0]->get_info(info)) {
            const auto* value = info.meta_get("DOUJINSTYLE URL", 0);
            if (value && is_release_site(value)) return value;
        }
        const auto id = doujin::release_id_from_filename(data[0]->get_path());
        return id.empty() ? std::string() : release_url(id);
    }
};

void append_release_event(file_info& info, const std::string& event) {
    if (event.empty()) return;
    const auto field = info.meta_find("RELEASE EVENT");
    if (field != static_cast<t_size>(pfc_infinite))
        for (t_size index = 0; index < info.meta_enum_value_count(field); ++index)
            if (doujin::release_event_from_tag(info.meta_enum_value(field, index)) == event) return;
    info.meta_add("RELEASE EVENT", event.c_str());
}

std::string catalog_id_tag(const file_info& info) {
    const auto field = info.meta_find("CATALOGID");
    if (field != static_cast<t_size>(pfc_infinite)) {
        for (t_size index = 0; index < info.meta_enum_value_count(field); ++index)
            if (auto code = doujin::catalog_number_from_tag(info.meta_enum_value(field, index)); !code.empty())
                return code;
    }
    return {};
}

void fill_release_metadata(file_info& info) {
    if (!info.meta_exists("LABEL")) {
        if (const auto label = info.meta_get("PUBLISHER", 0); label && *label) info.meta_set("LABEL", label);
    }
    for (const auto field : {"CATALOGNUMBER", "CATALOG NUMBER"}) {
        if (const auto value = info.meta_get(field, 0); value && *value)
            if (auto event = doujin::release_event_from_tag(value); !event.empty()) {
                info.meta_remove_field(field);
                append_release_event(info, event);
            }
    }
    std::vector<std::string> comment_events;
    if (const auto field = info.meta_find("COMMENT"); field != static_cast<t_size>(pfc_infinite))
        for (t_size index = 0; index < info.meta_enum_value_count(field); ++index)
            if (auto event = doujin::release_event_from_tag(info.meta_enum_value(field, index)); !event.empty())
                comment_events.push_back(std::move(event));
    for (const auto& event : comment_events) append_release_event(info, event);
    if (!info.meta_exists("CATALOGNUMBER")) {
        if (const auto explicit_number = info.meta_get("CATALOG NUMBER", 0); explicit_number && *explicit_number)
            info.meta_set("CATALOGNUMBER", explicit_number);
        else if (const auto alias = catalog_id_tag(info); !alias.empty())
            info.meta_set("CATALOGNUMBER", alias.c_str());
    }
    if (const auto value = info.meta_get("DATE", 0); value && *value) {
        const std::string raw(value);
        if (const auto date = doujin::format_release_date(raw); !date.empty()) info.meta_set("DATE", date.c_str());
        else {
            if (raw.size() == 4 && raw.find_first_not_of("0123456789") == raw.npos)
                info.meta_set("RELEASE YEAR", raw.c_str());
            info.meta_remove_field("DATE");
        }
    }
}

constexpr const char* raw_track_catalog = "DOUJINSTYLE PLAYBACK TRACK CATALOG";

void apply_catalog_priority(file_info& info, const doujin::Descriptor& descriptor, const std::string& decoded) {
    if (!descriptor.has_catalog_sources) return;
    info.meta_remove_field("CATALOGNUMBER");
    info.meta_remove_field("CATALOG NUMBER");
    info.meta_remove_field("CATALOG NUMBER SOURCE");
    pfc::string8 configured;
    catalog_priority.get(configured);
    for (const auto source : doujin::catalog_source_order(configured.c_str())) {
        const auto& candidate = source == 0 && !decoded.empty() ? decoded : descriptor.catalog_sources[source];
        if (candidate.empty() || !doujin::release_event_from_tag(candidate).empty()) continue;
        info.meta_set("CATALOGNUMBER", candidate.c_str());
        info.meta_set("CATALOG NUMBER SOURCE", doujin::catalog_source_names[source]);
        return;
    }
}

class LegacyInfoDelta : public file_info_filter {
public:
    LegacyInfoDelta(const file_info& baseline, const file_info& current) : change_(baseline, current) {}

    bool apply_filter(trackRef, t_filestats, file_info& info) override {
        change_.apply(info);
        return true;
    }

private:
    const file_info_delta change_;
};

class TrackInput : public input_stubs {
public:
    void open(file::ptr hint, const char* path, t_input_open_reason reason, abort_callback& abort) {
        if (reason == input_open_info_write) throw exception_tagging_unsupported();
        auto file = hint;
        input_open_file_helper(file, path, reason, abort);
        std::string fingerprint;
        descriptor_ = read_playback_reference(file, path, abort, &fingerprint);
        fingerprint_ = fingerprint;
        descriptor_file_ = file;
        descriptor_path_ = path;
        if (!fingerprint.empty()) {
            const auto profile = filesystem::g_get_native_path(core_api::get_profile_path());
            metadata_path_ = std::filesystem::path(http_wide(profile.c_str())) /
                L"DoujinStyle" / L"PlaybackMetadata" / (http_wide(fingerprint) + L".bin");
        }
    }

    void get_info(file_info& info, abort_callback& abort) {
        info_with_catalog(info, abort, nullptr);
    }

    void info_with_catalog(file_info& info, abort_callback& abort, std::string* raw_catalog) {
        if (load_playback_metadata(metadata_path_, info, abort)) {
            std::string decoded;
            if (const auto saved = info.meta_get(raw_track_catalog, 0)) decoded = saved;
            info.meta_remove_field(raw_track_catalog);
            if (decoded.empty()) decoded = catalog_id_tag(info);
            fill_release_metadata(info);
            apply_catalog_priority(info, descriptor_, decoded);
            if (raw_catalog) *raw_catalog = std::move(decoded);
            return;
        }
        for (const auto& [key, value] : descriptor_.metadata) info.meta_set(key.c_str(), value.c_str());
        for (const auto& [key, values] : descriptor_.multi_metadata) {
            info.meta_set(key.c_str(), values.front().c_str());
            for (std::size_t index = 1; index < values.size(); ++index)
                info.meta_add(key.c_str(), values[index].c_str());
        }
        for (const auto& [key, value] : descriptor_.technical_text) info.info_set(key.c_str(), value.c_str());
        if (is_release_site(descriptor_.referrer)) info.meta_set("DOUJINSTYLE URL", descriptor_.referrer.c_str());
        else if (!descriptor_.release_id.empty()) info.meta_set("DOUJINSTYLE URL", release_url(descriptor_.release_id).c_str());
        if (!info.meta_exists("TITLE")) info.meta_set("TITLE", descriptor_.filename.c_str());
        for (const auto& [key, value] : descriptor_.technical) {
            if (key == "DURATION_MS") info.set_length(static_cast<double>(value) / 1000.0);
            else if (key == "BITRATE_KBPS") info.info_set_bitrate(static_cast<int>(std::min<std::uint64_t>(value, 1000000)));
            else if (key.ends_with("_BYTES")) info.info_set(key.c_str(), std::to_string(value).c_str());
            else info.info_set_int(key.c_str(), std::min<std::uint64_t>(value, 0x7fffffff));
        }
        fill_release_metadata(info);
        apply_catalog_priority(info, descriptor_, {});
    }

    t_filestats2 get_stats2(std::uint32_t flags, abort_callback& abort) {
        return descriptor_file_->get_stats2_(flags, abort);
    }

    void decode_initialize(unsigned flags, abort_callback& abort) {
        decode_started_ = PlaybackClock::now();
        seek_trace_.reset();
        decoder_.reset();
        playback_deflate_.reset();
        playback_member_.reset();
        mp3_source_.reset();
        mp3_member_.reset();
        archive_resource_.reset();
        first_audio_ = true;
        trace_bitrate_ = trace_decoder_bitrate.get() && (flags & input_flag_playback) != 0;
        reported_native_live_ = false;
        last_reported_bitrate_ = 0;
        sampled_legacy_info_ = false;
        legacy_live_baseline_.reset();
        legacy_live_info_.reset();
        have_legacy_live_baseline_ = false;
        playback_ = metadata_pending_ = (flags & input_flag_playback) != 0;
        if (playback_) doujin::host_trace::emit("component.track.open.begin");
        file::ptr source;
        std::string path = descriptor_.filename;
        if (descriptor_.member.empty()) {
            playback_stage("direct audio open", [&] { filesystem::g_open_read(source, descriptor_.url.c_str(), abort); });
        } else {
            auto locator = doujin::encode_hex(descriptor_.url);
            if (!descriptor_.referrer.empty()) locator += "." + doujin::encode_hex(descriptor_.referrer);
            playback_stage("ZIP member open", [&] {
                archive_resource_ = shared_range_object(descriptor_.url, descriptor_.referrer, abort);
                const auto& resource = archive_resource_;
                const auto entry = locate_member(*resource, descriptor_.member, abort, descriptor_.zip_locator);
                if (!entry && descriptor_.zip_locator)
                    throw exception_io_data("ZIP member locator no longer matches the archive. Reindex the release.");
                const bool streaming = entry && (entry->method == 0 || host_deflate_api());
                if (entry) {
                    locator += "~" + doujin::encode_hex(descriptor_.member);
                    if (descriptor_.zip_locator) locator += "!" + doujin::format_zip_locator(*descriptor_.zip_locator);
                    const auto detail = entry->method == 0 ? "stored slice" :
                        (streaming ? "streaming DEFLATE" : "deflate member only (host reader fallback)");
                    console::print((std::string("DoujinStyle: ZIP ") + detail +
                                    " (" + std::to_string(entry->packed_size) + " of " +
                                    std::to_string(resource->size()) + " archive bytes)").c_str());
                } else {
                    console::print("DoujinStyle: ZIP layout requires the standard archive reader");
                }
                if (streaming) {
                    path = std::string(member_prefix) + locator + "/" + descriptor_.filename;
                    if (entry->method == 8 && playback_)
                        playback_member_ = shared_deflated_member(resource, *entry, abort);
                    if (metadata_pending_ && entry->method == 8 && entry->size > doujin::Mp3StartupBudget::compressed_limit &&
                        lower_ascii(descriptor_.filename).ends_with(".mp3")) {
                        mp3_member_ = *entry;
                        mp3_path_ = path;
                        mp3_flags_ = flags;
                        return;
                    }
                    if (playback_member_) {
                        playback_deflate_ = fb2k::service_new<DeflateCursor>(playback_member_);
                        source = playback_deflate_;
                    } else {
                        source = open_member_cursor(resource, *entry, abort);
                    }
                } else {
                    const auto outer = std::string(archive_prefix) + locator + ".zip";
                    pfc::string8 member_path;
                    archive_impl::g_make_unpack_path(member_path, outer.c_str(), descriptor_.member.c_str(), "zip");
                    path = member_path.c_str();
                    filesystem::g_open_read(source, path.c_str(), abort);
                }
                resource->report("ZIP member ready");
                if (playback_) doujin::host_trace::emit("component.archive.member.ready");
            });
        }
        if (playback_) doujin::host_trace::emit("component.track.open.end");
        if (mp3_member_) {
            try { open_mp3(true, abort); }
            catch (const exception_aborted&) { throw; }
            catch (...) {
                if (!mp3_source_.is_valid() || !mp3_source_->rejected()) throw;
                open_mp3(false, abort);
            }
            return;
        }
        input_decoder::ptr decoder;
        if (archive_resource_) archive_resource_->report("opening decoder");
        const auto decoder_open_at = PlaybackClock::now();
        if (metadata_pending_ && archive_resource_) {
            try {
                if (const auto buffering = foobar2000_io::read_ahead_tools::tryGet()) {
                    auto buffered = source;
                    buffering->open_file_helper(buffered, path.c_str(), abort);
                    if (buffered.is_valid()) source = std::move(buffered);
                    console::print("DoujinStyle: playback buffering delegated to foobar2000");
                }
            } catch (const exception_aborted&) {
                throw;
            } catch (...) {
                console::print("DoujinStyle: archive playback read-ahead unavailable");
            }
        }
        playback_stage("audio decoder open", [&] {
            input_entry::g_open_for_decoding(decoder, source, path.c_str(), abort, true);
        });
        if (decoder->get_subsong_count() == 0) throw exception_io_data("Remote audio contains no playable tracks");
        decoder_subsong_ = decoder->get_subsong(0);
        const auto decoder_init_at = PlaybackClock::now();
        if (playback_) doujin::host_trace::emit("component.decoder.initialize.begin");
        playback_stage("decoder initialization", [&] { decoder->initialize(decoder_subsong_, flags, abort); });
        if (playback_) doujin::host_trace::emit("component.decoder.initialize.end");
        if (metadata_pending_) {
            const auto decoder_ready_at = PlaybackClock::now();
            console::print(("DoujinStyle: playback setup | source open " +
                            elapsed_text(decode_started_, decoder_open_at) +
                            " | decoder open " + elapsed_text(decoder_open_at, decoder_init_at) +
                            " | decoder initialize " +
                            elapsed_text(decoder_init_at, decoder_ready_at)).c_str());
        }
        if (archive_resource_) archive_resource_->report("decoder ready");
        decoder_ = decoder;
        if (trace_bitrate_) {
            try {
                t_int32 rate = 0;
                config_object::g_get_data_int32(standard_config_objects::int32_dynamic_bitrate_display_rate, rate);
                console::print(("DoujinStyle: foobar2000 VBR updates/sec = " + std::to_string(rate)).c_str());
            } catch (const std::exception&) {
                console::print("DoujinStyle: native VBR update-rate readback unavailable");
            }
        }
    }

    bool decode_run(audio_chunk& chunk, abort_callback& abort) {
        auto run = [&] {
            const auto result = playback_stage("audio decode", [&] { return decoder_->run(chunk, abort); });
            if (first_audio_ && mp3_source_.is_valid() && mp3_source_->rejected())
                throw Mp3StartupLimit("MP3 startup reached its probe limit");
            return result;
        };
        bool result;
        try { result = run(); }
        catch (const exception_aborted&) { throw; }
        catch (...) {
            if (!first_audio_ || !mp3_source_.is_valid() || !mp3_source_->can_seek() || !mp3_source_->rejected()) throw;
            open_mp3(false, abort);
            result = run();
        }
        if (result) sampled_legacy_info_ = false;
        if (seek_trace_) {
            if (result && chunk.get_sample_count() != 0) {
                if (playback_) doujin::host_trace::emit("component.decoder.seek.first_pcm", chunk.get_sample_count());
                const auto [started, seconds, bytes, ranges, before] = *seek_trace_;
                const auto after = playback_member_ ? playback_member_->restores() : DeflatedMember::Restores{};
                const auto restored = after.count - before.count;
                const auto advanced = after.compressed_advance - before.compressed_advance;
                const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(PlaybackClock::now() - started).count();
                doujin::archive_playback_stats.seek(static_cast<std::uint64_t>(ms), bool(playback_member_),
                                                    after.queries > before.queries, restored != 0, advanced);
                console::print(("DoujinStyle: seek to first decoded audio | target " + doujin::trace_duration(seconds) +
                                " | " + elapsed_text(started) +
                                " | " + std::to_string(archive_resource_->body_bytes() - bytes) +
                                " archive response bytes in " + std::to_string(archive_resource_->range_count() - ranges) +
                                " ranges | " + std::to_string(restored) + " checkpoint restores | " +
                                std::to_string(advanced) + " compressed member bytes bypassed").c_str());
                seek_trace_.reset();
            } else if (!result) {
                seek_trace_.reset();
            }
        }
        if (first_audio_ && result && chunk.get_sample_count() != 0) {
            if (playback_) doujin::host_trace::emit("component.decoder.first_pcm", chunk.get_sample_count());
            first_audio_ = false;
            if (playback_deflate_.is_valid()) playback_deflate_->start_coalescing();
            if (mp3_source_.is_valid()) mp3_source_->permit_playback_reads();
            if (archive_resource_ && metadata_pending_) archive_resource_->report("first decoded audio");
            if (archive_resource_ && playback_) {
                const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(PlaybackClock::now() - decode_started_).count();
                std::error_code error;
                const auto saved_metadata = std::filesystem::is_regular_file(metadata_path_, error);
                const auto familiar = doujin::archive_playback_stats.start(
                    fingerprint_.empty() ? descriptor_path_ : fingerprint_, saved_metadata, static_cast<std::uint64_t>(ms));
                console::print((std::string("DoujinStyle: archive startup | ") + (familiar ? "familiar" : "new") +
                                " reference | " + elapsed_text(decode_started_) + " to first decoded audio").c_str());
            }
            if (metadata_pending_)
                console::print(("DoujinStyle: decode start to first audio " +
                                elapsed_text(decode_started_)).c_str());
            if (metadata_pending_ && !descriptor_.release_id.empty())
                fb2k::inMainThread([id = descriptor_.release_id] { doujin_lookup_on_playback(id); });
        } else if (!first_audio_ && metadata_pending_) {
            metadata_pending_ = false;
            if (mp3_source_.is_valid()) {
                // Querying the decoder for tags here can trigger another deep scan.
                try { mp3_source_->install_buffering(abort); }
                catch (const exception_aborted&) { throw; }
                catch (...) { console::print("DoujinStyle: MP3 playback read-ahead unavailable"); }
            } else {
                publish_metadata(decoder_, decoder_subsong_, abort);
            }
        }
        return result;
    }
    void decode_seek(double seconds, abort_callback& abort) {
        if (mp3_source_.is_valid() && mp3_source_->can_seek()) mp3_source_->permit_playback_reads();
        seek_trace_.reset();
        const auto started = PlaybackClock::now();
        if (playback_) doujin::host_trace::emit("component.seek.begin");
        const auto bytes = archive_resource_ ? archive_resource_->body_bytes() : 0;
        const auto ranges = archive_resource_ ? archive_resource_->range_count() : 0;
        const auto restores = playback_member_ ? playback_member_->restores() : DeflatedMember::Restores{};
        playback_stage("audio seek", [&] { decoder_->seek(seconds, abort); });
        sampled_legacy_info_ = false;
        legacy_live_info_.copy(legacy_live_baseline_);
        if (playback_ && archive_resource_) seek_trace_ = SeekTrace{started, seconds, bytes, ranges, restores};
    }
    bool decode_can_seek() { return (!mp3_source_.is_valid() || mp3_source_->can_seek()) && decoder_->can_seek(); }
    bool decode_get_dynamic_info(file_info& info, double& delta) {
        if (sampled_legacy_info_) return false;
        sampled_legacy_info_ = true;
        const auto changed = decoder_->get_dynamic_info(info, delta);
        if (changed && trace_bitrate_) {
            const auto bitrate = info.info_get_bitrate_vbr();
            if (bitrate > 0 && bitrate != last_reported_bitrate_) {
                last_reported_bitrate_ = bitrate;
                console::print(("DoujinStyle: decoder reported " + std::to_string(bitrate) + " kbps (legacy VBR)").c_str());
            }
        }
        return changed;
    }
    bool decode_get_dynamic_info_track(file_info& info, double& delta) { return decoder_->get_dynamic_info_track(info, delta); }
    void decode_poll_live_info(pfc::list_base_t<input_live_info_t>& info, abort_callback& abort) {
        input_decoder_v5::ptr live;
        if (!(live &= decoder_)) {
            // The host may call both callbacks after one chunk. Sample the inner legacy callback once.
            abort.check();
            if (sampled_legacy_info_) return;
            if (!have_legacy_live_baseline_) {
                get_info(legacy_live_baseline_, abort);
                legacy_live_info_.copy(legacy_live_baseline_);
                have_legacy_live_baseline_ = true;
            }
            double delta = 0;
            if (decode_get_dynamic_info(legacy_live_info_, delta)) {
                auto apply = fb2k::service_base_new<LegacyInfoDelta>(legacy_live_baseline_, legacy_live_info_);
                info.add_item({.payload = std::move(apply), .scope = input_live_info_t::scope_info, .deltaTime = delta});
            }
            return;
        }
        live->poll_live_info(info, abort);
        if (!trace_bitrate_ || reported_native_live_) return;
        for (const auto& event : info) {
            if (event.scope == input_live_info_t::scope_info) {
                reported_native_live_ = true;
                console::print("DoujinStyle: forwarded a 2.25 decoder live technical update");
                break;
            }
        }
    }
    void decode_on_idle(abort_callback& abort) { decoder_->on_idle(abort); }
    void retag(const file_info&, abort_callback&) { throw exception_tagging_unsupported(); }
    void remove_tags(abort_callback&) { throw exception_tagging_unsupported(); }

    static bool g_is_our_content_type(const char*) { return false; }
    static bool g_is_our_path(const char*, const char* extension) { return stricmp_utf8(extension, "djt") == 0; }
    static GUID g_get_guid() { return track_input_id; }
    static const char* g_get_name() { return "DoujinStyle track reference"; }

private:
    void open_mp3(bool seekable, abort_callback& abort) {
        decoder_.reset();
        mp3_source_ = fb2k::service_new<Mp3PlaybackFile>(playback_member_, seekable);
        console::print(seekable ? "DoujinStyle: bounded MP3 decoder startup" :
            "DoujinStyle: MP3 startup probe exceeded its limit; retrying as a sequential stream");
        input_decoder::ptr decoder;
        playback_stage("MP3 audio decoder open", [&] {
            input_entry::g_open_for_decoding(decoder, mp3_source_, mp3_path_.c_str(), abort, true);
        });
        if (!decoder->get_subsong_count()) throw exception_io_data("Remote MP3 contains no playable track");
        decoder_subsong_ = decoder->get_subsong(0);
        playback_stage("MP3 decoder initialization", [&] {
            decoder->initialize(decoder_subsong_, mp3_flags_ | (seekable ? 0u : input_flag_no_seeking), abort);
        });
        if (mp3_source_->rejected()) throw Mp3StartupLimit("MP3 startup reached its probe limit");
        decoder_ = std::move(decoder);
    }

    void publish_metadata(const input_decoder::ptr& decoder, unsigned subsong, abort_callback& abort) {
        try {
            file_info_impl actual;
            decoder->get_info(subsong, actual, abort);
            file_info_impl combined;
            std::string previous_catalog;
            info_with_catalog(combined, abort, &previous_catalog);
            stream_writer_buffer_simple prior;
            combined.to_stream(&prior, abort);
            fill_release_metadata(actual);
            std::string decoded_catalog;
            if (const auto value = actual.meta_get("CATALOGNUMBER", 0)) decoded_catalog = value;
            if (const auto events = actual.meta_find("RELEASE EVENT"); events != static_cast<t_size>(pfc_infinite))
                for (t_size index = 0; index < actual.meta_enum_value_count(events); ++index)
                    append_release_event(combined, doujin::release_event_from_tag(actual.meta_enum_value(events, index)));
            for (t_size field = 0; field < actual.meta_get_count(); ++field) {
                const auto name = actual.meta_enum_name(field);
                if (stricmp_utf8(name, "DOUJINSTYLE URL") == 0) continue;
                if (stricmp_utf8(name, raw_track_catalog) == 0) continue;
                if (stricmp_utf8(name, "DOUJINSTYLE DATE ADDED") == 0 ||
                    stricmp_utf8(name, "DOUJINSTYLE TAG") == 0 ||
                    stricmp_utf8(name, "RELEASE GENRE") == 0 || stricmp_utf8(name, "RELEASE EVENT") == 0 ||
                    stricmp_utf8(name, "BANGUMI URL") == 0 || stricmp_utf8(name, "DATE SOURCE") == 0) continue;
                if (stricmp_utf8(name, "RELEASE YEAR") == 0 && combined.meta_exists("DATE")) continue;
                const auto destination = stricmp_utf8(name, "ALBUMARTIST") == 0 ? "ALBUM ARTIST" : name;
                const auto count = actual.meta_enum_value_count(field);
                if (!count || !*actual.meta_enum_value(field, 0)) continue;
                combined.meta_set(destination, actual.meta_enum_value(field, 0));
                if (stricmp_utf8(name, "DATE") == 0) combined.meta_set("DATE SOURCE", "Embedded");
                for (t_size value = 1; value < count; ++value)
                    combined.meta_add(destination, actual.meta_enum_value(field, value));
            }
            for (t_size field = 0; field < actual.info_get_count(); ++field)
                combined.info_set(actual.info_enum_name(field), actual.info_enum_value(field));
            if (actual.get_length() > 0) combined.set_length(actual.get_length());
            auto replaygain = combined.get_replaygain();
            replaygain.overwrite(actual.get_replaygain());
            combined.set_replaygain(replaygain);
            apply_catalog_priority(combined, descriptor_, decoded_catalog);
            stream_writer_buffer_simple snapshot;
            combined.to_stream(&snapshot, abort);
            const auto equal = [&](const stream_writer_buffer_simple& candidate) {
                return snapshot.m_buffer.get_size() == candidate.m_buffer.get_size() &&
                    std::memcmp(snapshot.m_buffer.get_ptr(), candidate.m_buffer.get_ptr(),
                                snapshot.m_buffer.get_size()) == 0;
            };
            pfc::string8 canonical;
            filesystem::g_get_canonical_path(descriptor_path_.c_str(), canonical);
            const auto item = metadb::get()->handle_create(canonical.c_str(), 0);
            file_info_impl indexed;
            if (item->get_info(indexed)) {
                stream_writer_buffer_simple current;
                indexed.to_stream(&current, abort);
                if (equal(current) && equal(prior) && decoded_catalog == previous_catalog) return;
            }
            if (!equal(prior) || decoded_catalog != previous_catalog) {
                if (descriptor_.has_catalog_sources && !decoded_catalog.empty())
                    combined.meta_set(raw_track_catalog, decoded_catalog.c_str());
                stream_writer_buffer_simple persisted;
                combined.to_stream(&persisted, abort);
                combined.meta_remove_field(raw_track_catalog);
                if (!save_playback_metadata(metadata_path_, persisted))
                    console::print("DoujinStyle: could not persist decoder metadata to the profile cache");
            }
            const auto original_stats = descriptor_file_->get_stats(abort);
            t_filestats current_stats;
            bool writable = false;
            filesystem::g_get_stats(canonical.c_str(), current_stats, writable, abort);
            if (original_stats.m_timestamp != current_stats.m_timestamp ||
                original_stats.m_size != current_stats.m_size) return;
            const auto hints = metadb_hint_list_v3::create();
            hints->add_hint_forced(item, combined, original_stats, true);
            fb2k::inMainThread([hints] {
                hints->on_done();
                console::print("DoujinStyle: decoder metadata applied to the media database");
            });
        } catch (const exception_aborted&) {
            throw;
        } catch (...) {
            console::print("DoujinStyle: decoder metadata update failed");
        }
    }

    doujin::Descriptor descriptor_;
    std::string descriptor_path_;
    std::string fingerprint_;
    std::filesystem::path metadata_path_;
    file::ptr descriptor_file_;
    input_decoder::ptr decoder_;
    std::shared_ptr<RangeObject> archive_resource_;
    PlaybackClock::time_point decode_started_;
    struct SeekTrace {
        PlaybackClock::time_point started;
        double seconds;
        std::uint64_t bytes;
        unsigned ranges;
        DeflatedMember::Restores restores;
    };
    std::optional<SeekTrace> seek_trace_;
    unsigned decoder_subsong_ = 0;
    std::optional<doujin::ZipMember> mp3_member_;
    std::shared_ptr<DeflatedMember> playback_member_;
    service_ptr_t<DeflateCursor> playback_deflate_;
    service_ptr_t<Mp3PlaybackFile> mp3_source_;
    std::string mp3_path_;
    unsigned mp3_flags_ = 0;
    bool first_audio_ = true, metadata_pending_ = false, playback_ = false;
    bool trace_bitrate_ = false, reported_native_live_ = false;
    bool sampled_legacy_info_ = false, have_legacy_live_baseline_ = false;
    file_info_impl legacy_live_baseline_, legacy_live_info_;
    t_int64 last_reported_bitrate_ = 0;
};

service_factory_single_t<ArchiveFilesystem> archive_filesystem_factory;
service_factory_single_t<TrackCoverExtractor> track_cover_factory;
service_factory_single_t<SiteCoverFallback> site_cover_factory;
contextmenu_item_factory_t<ReleaseMenu> release_menu_factory;
contextmenu_item_factory_t<BuildSeekIndexMenu> seek_index_menu_factory;
input_singletrack_factory_t<TrackInput, input_entry::flag_redirect> track_input_factory;
}
