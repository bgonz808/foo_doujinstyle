#pragma once

// Preserve legacy version-0 catalog link migration when removing Python from
// the foreground search path. The native SQLite migrator verifies this snapshot
// against the live rows under its own writer transaction before committing.
#include "catalog_ingest.hpp"
#include <algorithm>
#include <cctype>
#include <charconv>
#include <regex>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::legacy_links {
struct ParsedUrl {
    std::string hostname;
    std::string path;
    unsigned port = 0;
    bool query = false;
    bool fragment = false;
};

inline std::string lower_ascii(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    while (!value.empty() && value.back() == '.') value.pop_back();
    return value;
}

inline ParsedUrl parse_url(std::string_view url) {
    const auto start = url.starts_with("https://") ? 8 :
                       url.starts_with("http://") ? 7 : 0;
    if (!start || url.size() > 8192 || url.find_first_of("\0\r\n\t") != url.npos)
        throw std::runtime_error("Legacy share URL must be HTTP(S)");
    const auto end_authority = url.find_first_of("/?#", start);
    auto authority = url.substr(start, end_authority == url.npos ? url.npos : end_authority - start);
    if (authority.empty() || authority.find('@') != authority.npos ||
        authority.find('\\') != authority.npos)
        throw std::runtime_error("Legacy URL has invalid authority");
    ParsedUrl result;
    std::string_view hostname = authority;
    if (authority.front() == '[') {
        const auto closing = authority.find(']');
        if (closing == authority.npos) throw std::runtime_error("Invalid legacy IPv6 authority");
        hostname = authority.substr(1, closing - 1);
        if (closing + 1 < authority.size()) {
            if (authority[closing + 1] != ':')
                throw std::runtime_error("Invalid legacy URL port");
            auto port = authority.substr(closing + 2);
            unsigned value = 0;
            const auto parsed = std::from_chars(port.data(), port.data() + port.size(), value);
            if (port.empty() || parsed.ec != std::errc{} || parsed.ptr != port.data() + port.size() || value > 65535)
                throw std::runtime_error("Invalid legacy URL port");
            result.port = value;
        }
    } else if (const auto colon = authority.find(':'); colon != authority.npos) {
        hostname = authority.substr(0, colon);
        auto port = authority.substr(colon + 1);
        unsigned value = 0;
        const auto parsed = std::from_chars(port.data(), port.data() + port.size(), value);
        if (port.empty() || parsed.ec != std::errc{} || parsed.ptr != port.data() + port.size() || value > 65535)
            throw std::runtime_error("Invalid legacy URL port");
        result.port = value;
    }
    if (hostname.empty() || hostname.find_first_of(" \r\n\t\\") != hostname.npos)
        throw std::runtime_error("Invalid legacy URL hostname");
    result.hostname = lower_ascii(std::string(hostname));
    const auto tail = end_authority == url.npos ? std::string_view{} : url.substr(end_authority);
    result.query = tail.find('?') != tail.npos;
    result.fragment = tail.find('#') != tail.npos;
    result.path = tail.empty() || tail.front() != '/' ? "/" :
        std::string(tail.substr(0, tail.find_first_of("?#")));
    return result;
}

inline bool domain(std::string_view host, std::string_view expected) {
    return host == expected || (host.size() > expected.size() &&
           host.substr(host.size() - expected.size() - 1) == "." + std::string(expected));
}

inline std::string host_family(std::string_view host) {
    if (domain(host, "mediafire.com")) return "mediafire.com";
    if (domain(host, "mega.nz") || domain(host, "mega.co.nz")) return "mega.nz";
    if (domain(host, "drive.google.com") || domain(host, "drive.usercontent.google.com")) return "google-drive";
    if (domain(host, "dropbox.com") || domain(host, "dropboxusercontent.com")) return "dropbox.com";
    return std::string(host);
}

inline std::string destination(std::string_view url, const ParsedUrl& parsed) {
    if (!parsed.query && !parsed.fragment &&
        (parsed.hostname == "mediafire.com" || parsed.hostname == "www.mediafire.com" ||
         parsed.hostname == "m.mediafire.com") &&
        (!parsed.port || parsed.port == 80 || parsed.port == 443)) {
        static const std::regex share(R"(^/file/([A-Za-z0-9]+)(?:/[^/]+)?(?:/file)?/?$)");
        std::smatch matched;
        if (std::regex_match(parsed.path, matched, share))
            return "mediafire:file:" + matched[1].str();
    }
    return "url:" + std::string(url);
}

inline Json snapshot(sqlite3* db) {
    auto version = query(db, "PRAGMA user_version");
    if (!next(version.get())) throw std::runtime_error("Missing SQLite catalog version");
    const auto number = sqlite3_column_int(version.get(), 0);
    if (number >= 2) {
        if (number != 2) throw std::runtime_error("Unsupported catalog schema revision");
        return Json::array();
    }
    if (number != 0 && number != 1)
        throw std::runtime_error("Unexpected legacy SQLite catalog version");
    if (number == 1) return Json::array();
    auto table = query(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='releases'");
    if (!next(table.get())) return Json::array();
    auto rows = query(db, "SELECT id,share_url,offer,host FROM releases WHERE share_url IS NOT NULL "
                          "ORDER BY COALESCE(offer_checked,seen),id");
    Json links = Json::array();
    while (next(rows.get())) {
        const auto id = sqlite3_column_int64(rows.get(), 0);
        const auto url = string_column(rows.get(), 1);
        const auto old_offer = sqlite3_column_type(rows.get(), 2) == SQLITE_NULL
            ? Json() : Json(string_column(rows.get(), 2));
        const auto old_host = sqlite3_column_type(rows.get(), 3) == SQLITE_NULL
            ? Json() : Json(string_column(rows.get(), 3));
        const auto parsed = parse_url(url);
        const auto offer = old_offer.is_null()
            ? Json{{"host", old_host}} : Json::parse(old_offer.get_ref<const std::string&>());
        links.push_back({{"id", id}, {"url", url}, {"old_offer", old_offer},
            {"old_host", old_host}, {"destination", destination(url, parsed)},
            {"host", host_family(parsed.hostname)}, {"offer", offer}, {"source", "doujinstyle"}});
        if (links.size() > 200000)
            throw std::runtime_error("Legacy catalog migration exceeds safe snapshot size");
    }
    return links;
}
} // namespace doujin::legacy_links
