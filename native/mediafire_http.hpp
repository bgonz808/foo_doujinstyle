#pragma once
#include "http_user_agent.hpp"
#include "live_provider_trace.hpp"

#include "mediafire_page.hpp"
#include "mega_public.hpp"
#include "mediafire_download_policy.hpp"
#include "site_offers.hpp"

#include <cstdint>
#include <optional>
#include <stdexcept>

namespace doujin {
class MediaFireRangeError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

inline bool mediafire_index_url(std::string_view url, bool share) {
    validate_url(url);
    if (!url.starts_with("https://")) return false;
    const auto authority = url.substr(8);
    auto host = lower_ascii(std::string(authority.substr(0, authority.find_first_of("/?"))));
    if (host.ends_with(":443")) host.resize(host.size() - 4);
    if (host.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789.-") != host.npos) return false;
    const bool share_host = host == "mediafire.com" || host == "www.mediafire.com" || host == "m.mediafire.com";
    return share ? share_host : host.ends_with(".mediafire.com");
}

// Explicit provider families, not a generic wildcard or suffix substring.
inline ContentRange check_mediafire_range(std::string_view range, std::string_view encoding,
    std::string_view content_length, std::uint64_t start, std::uint64_t length, bool suffix) {
    if (!encoding.empty() && lower_ascii(std::string(encoding)) != "identity")
        throw std::runtime_error("Archive response has unexpected Content-Encoding");
    const auto span = parse_content_range(range);
    const auto expected_start = suffix ? span.size - std::min(length, span.size) : start;
    if (span.first != expected_start || span.last - span.first + 1 != std::min(length, span.size - span.first))
        throw std::runtime_error("Archive host returned a mismatched byte range");
    if (!content_length.empty() && content_length != std::to_string(span.last - span.first + 1))
        throw std::runtime_error("Archive Content-Length disagrees with Content-Range");
    return span;
}
}

#ifdef _WIN32
#include "third_party/json.hpp"
#include <windows.h>
#include <shlwapi.h>
#include <winhttp.h>
#include <array>
#include <type_traits>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>

namespace doujin {
class MediaFireHttp {
    using Json = nlohmann::json;

    struct Handle {
        HINTERNET value;
        explicit Handle(HINTERNET value = nullptr) : value(value) {}
        ~Handle() { if (value) WinHttpCloseHandle(value); }
        Handle(const Handle&) = delete;
        Handle& operator=(const Handle&) = delete;
    };

    [[noreturn]] static void fail(const char* stage, DWORD error = GetLastError()) {
        if(auto* observed=live_smoke::current) observed->failed("worker_winhttp",error);
        throw std::runtime_error(std::string("WinHTTP ") + stage + " failed (" + std::to_string(error) + ")");
    }

    static std::wstring wide(std::string_view text) {
        if (text.empty()) return {};
        const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
        if (!count) fail("text conversion");
        std::wstring result(count, L'\0');
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), count))
            fail("text conversion");
        return result;
    }

    static std::string utf8(std::wstring_view text) {
        if (text.empty()) return {};
        const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
        if (!count) fail("text conversion");
        std::string result(count, '\0');
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), count, nullptr, nullptr))
            fail("text conversion");
        return result;
    }

    static std::wstring header(HINTERNET request, DWORD type, const wchar_t* name = WINHTTP_HEADER_NAME_BY_INDEX) {
        std::array<wchar_t, 4096> buffer{};
        DWORD bytes = static_cast<DWORD>(sizeof buffer);
        if (WinHttpQueryHeaders(request, type, name, buffer.data(), &bytes, WINHTTP_NO_HEADER_INDEX))
            return std::wstring(buffer.data(), bytes / sizeof(wchar_t));
        if (GetLastError() == ERROR_WINHTTP_HEADER_NOT_FOUND) return {};
        fail("header");
    }

    static std::string offer_header(HINTERNET request) {
        std::array<char, 8193> buffer{};
        DWORD bytes = static_cast<DWORD>(buffer.size());
        if (WinHttpQueryHeaders(request, WINHTTP_QUERY_LOCATION | WINHTTP_QUERY_FLAG_WIRE_ENCODING,
                WINHTTP_HEADER_NAME_BY_INDEX, buffer.data(), &bytes, WINHTTP_NO_HEADER_INDEX)) {
            auto raw = std::string_view(buffer.data(), bytes);
            if (raw.ends_with('\0')) raw.remove_suffix(1);
            if (raw.find('\0') == raw.npos) return std::string(raw);
        }
        // Older WinHTTP implementations may only return the wide header.
        return utf8(header(request, WINHTTP_QUERY_LOCATION));
    }

    static std::string combine(const std::string& base, const std::wstring& location) {
        std::array<wchar_t, 8193> result{};
        DWORD capacity = static_cast<DWORD>(result.size());
        if (FAILED(UrlCombineW(wide(base).c_str(), location.c_str(), result.data(), &capacity, URL_DONT_ESCAPE_EXTRA_INFO)))
            throw std::runtime_error("Invalid MediaFire redirect or download location");
        auto url = utf8(result.data());
        if (const auto fragment = url.find('#'); fragment != url.npos) url.resize(fragment);
        validate_url(url);
        return url;
    }

    template<typename Reply>
    auto exchange(const std::string& url, const wchar_t* method, const std::string& referring_url,
                  std::string_view policy, const std::wstring& headers, std::string body, Reply reply) {
        const auto address = wide(url);
        URL_COMPONENTS parts{};
        parts.dwStructSize = sizeof parts;
        parts.dwHostNameLength = parts.dwUrlPathLength = parts.dwExtraInfoLength = static_cast<DWORD>(-1);
        if (!WinHttpCrackUrl(address.c_str(), static_cast<DWORD>(address.size()), 0, &parts))
            fail("URL parsing");
        const std::wstring host(parts.lpszHostName, parts.dwHostNameLength);
        live_smoke::Observation observation;
        live_smoke::Scope scope(live_smoke_enabled()?&observation:nullptr);
        observation.begin(lower_ascii(utf8(host)),parts.nScheme,parts.nPort,url,method,body.size());
        std::wstring path = parts.dwUrlPathLength ? std::wstring(parts.lpszUrlPath, parts.dwUrlPathLength) : L"/";
        if (parts.dwExtraInfoLength) path.append(parts.lpszExtraInfo, parts.dwExtraInfoLength);
        const Handle connection(WinHttpConnect(session_.value, host.c_str(), parts.nPort, 0));
        if (!connection.value) fail("connection");
        const auto referer = wide(navigation_referrer(referring_url, url, policy));
        DWORD flags = WINHTTP_FLAG_ESCAPE_DISABLE | WINHTTP_FLAG_ESCAPE_DISABLE_QUERY;
        if (parts.nScheme == INTERNET_SCHEME_HTTPS) flags |= WINHTTP_FLAG_SECURE;
        const Handle request(WinHttpOpenRequest(connection.value, method, path.c_str(), nullptr,
            referer.empty() ? WINHTTP_NO_REFERER : referer.c_str(), WINHTTP_DEFAULT_ACCEPT_TYPES,
            flags));
        if (!request.value) fail("request");
        DWORD disabled = WINHTTP_DISABLE_REDIRECTS;
        if (!WinHttpSetOption(request.value, WINHTTP_OPTION_DISABLE_FEATURE, &disabled, sizeof disabled))
            fail("redirect configuration");
        DWORD autologon = WINHTTP_AUTOLOGON_SECURITY_LEVEL_HIGH;
        if (!WinHttpSetOption(request.value, WINHTTP_OPTION_AUTOLOGON_POLICY, &autologon, sizeof autologon))
            fail("credential policy");
        const auto length = static_cast<DWORD>(body.size());
        if (!WinHttpSendRequest(request.value, headers.c_str(), static_cast<DWORD>(headers.size()),
            body.empty() ? WINHTTP_NO_REQUEST_DATA : body.data(), length, length, 0) ||
            !WinHttpReceiveResponse(request.value, nullptr))
            fail("response");
        DWORD status = 0, bytes = sizeof status;
        if (!WinHttpQueryHeaders(request.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                WINHTTP_HEADER_NAME_BY_INDEX, &status, &bytes, WINHTTP_NO_HEADER_INDEX))
            fail("status");
        observation.headers(request.value,status);
        auto result=reply(request.value, status);
        observation.complete=true;observation.failure_stage="none";
        return result;
    }

    template<typename Allowed, typename Reply>
    auto get(std::string url, const std::string& referring_url, std::string policy,
             const std::wstring& headers, Allowed allowed, Reply reply) {
        using Result = std::invoke_result_t<Reply, HINTERNET, DWORD, const std::string&>;
        for (unsigned redirects = 0; redirects <= 5; ++redirects) {
            if (!allowed(url)) throw std::runtime_error("Unexpected catalog request destination");
            auto result = exchange(url, L"GET", referring_url, policy, headers, {},
                [&](HINTERNET request, DWORD status) -> std::optional<Result> {
                if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                    if (const auto location = header(request, WINHTTP_QUERY_LOCATION); !location.empty()) {
                        if (const auto next = response_policy(request); !next.empty()) policy = next;
                        url = combine(url, location);
                        return {};
                    }
                }
                return reply(request, status, url);
            });
            if (result) return std::move(*result);
        }
        throw std::runtime_error("Too many catalog redirects");
    }

    static std::string response_policy(HINTERNET request) {
        return std::string(referrer_policy(utf8(header(request, WINHTTP_QUERY_CUSTOM, L"Referrer-Policy"))));
    }

    static std::string read(HINTERNET request, std::size_t cap) {
        std::array<char, 8192> block{};
        std::string body;
        while (true) {
            DWORD count = 0;
            const auto wanted = static_cast<DWORD>(std::min(block.size(), cap + 1 - body.size()));
            if (!live_smoke::read(request,block.data(),wanted,&count)) fail("body read");
            if (!count) return body;
            body.append(block.data(), count);
            if (body.size() > cap) throw std::runtime_error("MediaFire response exceeded its byte limit");
        }
    }

    Handle session_;

public:
    explicit MediaFireHttp(const wchar_t* agent = http_user_agent, int timeout = 7000) {
        session_.value = WinHttpOpen(agent, live_smoke_enabled()?WINHTTP_ACCESS_TYPE_NO_PROXY:WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session_.value && !live_smoke_enabled() && GetLastError() == ERROR_INVALID_PARAMETER)
            session_.value = WinHttpOpen(agent, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session_.value) fail("session");
        if (!WinHttpSetTimeouts(session_.value, 6000, timeout, timeout, timeout)) fail("timeout configuration");
        DWORD http2 = WINHTTP_PROTOCOL_FLAG_HTTP2;
        WinHttpSetOption(session_.value, WINHTTP_OPTION_ENABLE_HTTP_PROTOCOL, &http2, sizeof http2);
    }

    Json resolve(const Json& args) {
        return get(args.at("url").get<std::string>(), args.value("referrer", ""), "",
            L"Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.1\r\nAccept-Encoding: identity\r\n",
            [](std::string_view url) { return mediafire_index_url(url, true); },
            [](HINTERNET request, DWORD status, const std::string& effective) -> Json {
                if (status != 200) return {{"status", status}};
                const auto page = read(request, 1024 * 1024);
                const auto link = extract_mediafire_download(page, response_policy(request));
                const auto base = link.base.empty() ? effective : combine(effective, wide(link.base));
                const auto target = combine(base, wide(link.href));
                if (!mediafire_index_url(target, false))
                    throw std::runtime_error("Unexpected MediaFire download destination");
                return {{"status", status}, {"url", target}, {"referrer", effective}, {"policy", link.policy}};
            });
    }

    struct Range {
        DWORD status;
        ContentRange span{};
        std::string header, body, etag, modified, disposition;
    };

    Range range_bytes(const std::string& url, const std::string& referrer, const std::string& policy,
                      std::optional<std::uint64_t> offset, std::uint64_t length, const std::string& validator) {
        const auto start = offset.value_or(0);
        const bool suffix = !offset;
        if (!length || length > 2 * 1024 * 1024 || start > UINT64_MAX - length)
            throw std::runtime_error("Invalid MediaFire range request");
        auto headers = L"Accept-Encoding: identity\r\nRange: bytes=" + (suffix ? L"-" + std::to_wstring(length)
            : std::to_wstring(start) + L"-" + std::to_wstring(start + length - 1)) + L"\r\n";
        if (!validator.empty()) {
            if (validator.find_first_of("\r\n\0", 0, 3) != validator.npos || validator.size() > 4096)
                throw std::runtime_error("Invalid MediaFire range validator");
            headers += L"If-Range: " + wide(validator) + L"\r\n";
        }
        return get(url, referrer, policy, headers, [](std::string_view url) { return mediafire_index_url(url, false); },
                   [&](HINTERNET request, DWORD status, const std::string&) -> Range {
            if (status != 206) {
                Range response{};
                response.status = status;
                return response;
            }
            auto range = utf8(header(request, WINHTTP_QUERY_CONTENT_RANGE));
            ContentRange span{};
            try {
                span = check_mediafire_range(range, utf8(header(request, WINHTTP_QUERY_CONTENT_ENCODING)),
                    utf8(header(request, WINHTTP_QUERY_CONTENT_LENGTH)), start, length, suffix);
            } catch (const std::runtime_error& error) {
                throw MediaFireRangeError(error.what());
            }
            const auto expected = static_cast<std::size_t>(span.last - span.first + 1);
            auto body = read(request, expected);
            if (body.size() != expected) throw MediaFireRangeError("Archive range body is shorter than Content-Range");
            if(auto* observed=live_smoke::current) {
                observed->range_first=span.first;observed->range_last=span.last;observed->resource_size=span.size;
            }
            return {status, span, std::move(range), std::move(body), utf8(header(request, WINHTTP_QUERY_ETAG)),
                utf8(header(request, WINHTTP_QUERY_LAST_MODIFIED)), utf8(header(request, WINHTTP_QUERY_CONTENT_DISPOSITION))};
        });
    }

    // Stream a complete MediaFire archive over the validated CDN origin.  The
    // callback owns the exclusive temporary file, validates the magic bytes and
    // publishes only after the final length check.  Never buffer the full file.
    template <typename Consumer>
    std::uint64_t download_stream(const std::string& url, const std::string& referrer,
                                  const std::string& policy, Consumer&& consume) {
        const auto headers = L"Accept-Encoding: identity\r\n";
        return get(url, referrer, policy, headers,
            [](std::string_view next) { return mediafire_index_url(next, false); },
            [&](HINTERNET request, DWORD status, const std::string&) -> std::uint64_t {
                if (status != 200)
                    throw std::runtime_error("MediaFire archive download returned HTTP " + std::to_string(status));
                auto encoding = lower_ascii(utf8(header(request, WINHTTP_QUERY_CONTENT_ENCODING)));
                if (!encoding.empty() && encoding != "identity")
                    throw std::runtime_error("MediaFire archive was unexpectedly encoded");
                mediafire::LengthValidator length(utf8(header(request, WINHTTP_QUERY_CONTENT_LENGTH)));
                std::array<char, 65536> chunk{};
                while (true) {
                    DWORD n = 0;
                    if (!live_smoke::read(request, chunk.data(), static_cast<DWORD>(chunk.size()), &n))
                        fail("MediaFire download read");
                    if (!n) break;
                    length.append(n);
                    if (live_smoke_enabled() && live_smoke::current->body_bytes>128ULL*1024*1024)
                        throw std::runtime_error("Live archive capture exceeds 128 MiB");
                    consume(std::string_view(chunk.data(), n));
                }
                return length.finish();

            });
    }

    struct MegaResponse { unsigned status; std::string body; };

    MegaResponse mega_storage_range(const mega::PublicFile& file, std::uint64_t first, std::size_t amount) {
        const auto url = mega::range_url(file, first, amount);
        return exchange(url, L"GET", "", "", L"Accept-Encoding: identity\r\n", {},
            [&](HINTERNET request, DWORD status) -> MegaResponse {
                if (status != 200 && status != 206)
                    throw std::runtime_error("MEGA storage range returned HTTP " + std::to_string(status));
                const auto encoding = utf8(header(request, WINHTTP_QUERY_CONTENT_ENCODING));
                if (!encoding.empty() && lower_ascii(encoding) != "identity")
                    throw std::runtime_error("MEGA storage returned compressed ciphertext");
                const auto length = utf8(header(request, WINHTTP_QUERY_CONTENT_LENGTH));
                if (!length.empty() && length != std::to_string(amount))
                    throw std::runtime_error("MEGA storage returned unexpected Content-Length");
                const auto expected = "bytes " + std::to_string(first) + "-" +
                    std::to_string(first + amount - 1) + "/" + std::to_string(file.size);
                const auto range = utf8(header(request, WINHTTP_QUERY_CONTENT_RANGE));
                if (status == 206 && range != expected)
                    throw std::runtime_error("MEGA storage returned mismatched Content-Range");
                auto raw = read(request, amount);
                if (raw.size() != amount) throw std::runtime_error("Truncated MEGA ciphertext range");
                return {status, std::move(raw)};
            });
    }

    template<class Amount, class Consume>
    void mega_full_stream(const mega::PublicFile& file, Amount&& next_amount, Consume&& consume) {
        if (!mega::storage_endpoint(file.endpoint)) throw std::runtime_error("Unsafe MEGA download endpoint");
        (void)exchange(file.endpoint, L"GET", "", "", L"Accept-Encoding: identity\r\n", {},
            [&](HINTERNET request, DWORD status) {
                if (status != 200) throw std::runtime_error("MEGA download returned HTTP " + std::to_string(status));
                const auto encoding = utf8(header(request, WINHTTP_QUERY_CONTENT_ENCODING));
                if (!encoding.empty() && lower_ascii(encoding) != "identity")
                    throw std::runtime_error("MEGA download returned compressed ciphertext");
                const auto length = utf8(header(request, WINHTTP_QUERY_CONTENT_LENGTH));
                if (!length.empty() && length != std::to_string(file.size))
                    throw std::runtime_error("MEGA full download length mismatch");
                std::uint64_t at = 0;
                while (at < file.size) {
                    const auto wanted = static_cast<std::size_t>(next_amount(file.size));
                    if (!wanted || wanted > 1048576 || wanted > file.size - at)
                        throw std::runtime_error("Invalid MEGA verification chunk schedule");
                    std::string data(wanted, '\0');
                    std::size_t received = 0;
                    while (received < wanted) {
                        DWORD count = 0;
                        if (!WinHttpReadData(request, data.data() + received,
                                             static_cast<DWORD>(wanted - received), &count)) fail("MEGA download read");
                        if (!count) throw std::runtime_error("MEGA download ended early");
                        received += count;
                    }
                    consume(at, data);
                    at += wanted;
                }
                char surplus = 0;
                DWORD extras = 0;
                if (!WinHttpReadData(request, &surplus, 1, &extras)) fail("MEGA download final read");
                if (extras) throw std::runtime_error("MEGA download exceeded advertised length");
                return true;
            });
    }

    Json range(const Json& args) {
        const auto& start = args.at("start");
        const auto result = range_bytes(args.at("url").get<std::string>(), args.value("referrer", ""),
            args.value("policy", ""), start.is_null() ? std::nullopt : std::optional(start.get<std::uint64_t>()),
            args.at("length").get<std::uint64_t>(), args.value("validator", ""));
        if (result.status != 206) return {{"status", result.status}};
        return {{"status", result.status}, {"body", encode_hex(result.body)}, {"headers", {
            {"Content-Range", result.header}, {"ETag", result.etag}, {"Last-Modified", result.modified},
            {"Content-Disposition", result.disposition}}}};
    }

    template<typename Allowed>
    std::string catalog_page(const std::string& url, std::size_t cap, Allowed allowed) {
        auto document = catalog_document(url, cap, allowed);
        if (document.status != 200)
            throw std::runtime_error("DoujinStyle catalog returned HTTP " + std::to_string(document.status));
        return std::move(document.body);
    }

    template<typename Allowed>
    discovery::ReleaseDocument catalog_document(const std::string& url, std::size_t cap, Allowed allowed,
                                                 const std::string& referrer = "", const std::string& policy = "") {
        return get(url, referrer, policy,
            L"Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.1\r\nAccept-Encoding: identity\r\n",
            allowed, [cap](HINTERNET request, DWORD status, const std::string& effective) {
                return discovery::ReleaseDocument{status, status == 200 ? read(request, cap) : std::string{},
                                                   effective, response_policy(request)};
            });
    }

    discovery::OfferResponse catalog_post(const std::string& action, const std::string& document,
                                          std::string_view policy, const std::string& body) {
        auto origin = navigation_referrer(document, document, "origin");
        origin.pop_back();
        if (policy == "no-referrer") origin = "null";
        const auto headers = L"Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.1\r\n"
            L"Accept-Encoding: identity\r\nContent-Type: application/x-www-form-urlencoded\r\nOrigin: " +
            wide(origin) + L"\r\n";
        return exchange(action, L"POST", document, policy, headers, body, [](HINTERNET request, DWORD status) {
            return discovery::OfferResponse{status, status < 400 ? offer_header(request) : std::string{}};
        });
    }

    std::pair<unsigned, std::string> json_request(const std::string& url, const std::string& body, std::size_t cap) {
        std::wstring headers = L"Accept: application/json\r\nAccept-Encoding: identity\r\n";
        if (!body.empty()) headers += L"Content-Type: application/json\r\n";
        return exchange(url, body.empty() ? L"GET" : L"POST", "", "", headers, body,
            [cap](HINTERNET request, DWORD status) -> std::pair<unsigned, std::string> {
                if (status != 200) return {status, {}};
                const auto encoding = header(request, WINHTTP_QUERY_CONTENT_ENCODING);
                if (!encoding.empty() && lstrcmpiW(encoding.c_str(), L"identity") != 0)
                    throw std::runtime_error("Unexpected encoding in catalog JSON response");
                return {status, read(request, cap)};
            });
    }
};
}
#endif
