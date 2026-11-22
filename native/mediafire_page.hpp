#pragma once

#include "descriptor.hpp"

#include <algorithm>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin {
inline std::string unescape_amp(std::string value) {
    for (std::size_t pos = 0; (pos = value.find("&amp;", pos)) != std::string::npos; ++pos)
        value.replace(pos, 5, "&");
    return value;
}

inline bool attr_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f'; }

inline std::string lower_ascii(std::string value) {
    for (auto& c : value) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    return value;
}

using HtmlAttributes = std::map<std::string, std::string>;

inline HtmlAttributes read_attributes(std::string_view& rest) {
    HtmlAttributes attributes;
    auto skip_space = [&] { while (!rest.empty() && attr_space(rest.front())) rest.remove_prefix(1); };
    while (!rest.empty() && rest.front() != '>') {
        skip_space();
        if (rest.empty() || rest.front() == '>') break;
        auto end = rest.find_first_of(" \t\r\n\f=>/");
        if (end == rest.npos) end = rest.size();
        if (!end) { rest.remove_prefix(1); continue; }
        const auto name = lower_ascii(std::string(rest.substr(0, end)));
        rest.remove_prefix(end);
        skip_space();
        std::string_view value;
        if (!rest.empty() && rest.front() == '=') {
            rest.remove_prefix(1);
            skip_space();
            if (!rest.empty() && (rest.front() == '\'' || rest.front() == '"')) {
                const auto quote = rest.front();
                rest.remove_prefix(1);
                end = rest.find(quote);
                value = rest.substr(0, end);
                rest.remove_prefix(end == rest.npos ? rest.size() : end + 1);
            } else {
                end = rest.find_first_of(" \t\r\n\f>");
                value = rest.substr(0, end);
                rest.remove_prefix(value.size());
            }
        }
        attributes.try_emplace(name, value);
    }
    return attributes;
}

struct DownloadLink { std::string href, base, policy; };

inline DownloadLink extract_mediafire_download(std::string_view page, std::string policy) {
    const auto searchable = lower_ascii(std::string(page));
    std::string href, base, link_policy;
    for (std::size_t pos = 0; (pos = searchable.find('<', pos)) != searchable.npos; ++pos) {
        const auto name_start = pos + 1;
        if (std::string_view(searchable).substr(name_start).starts_with("!--")) {
            const auto end = searchable.find("-->", name_start + 3);
            if (end == searchable.npos) break;
            pos = end + 2;
            continue;
        }
        const auto end = searchable.find_first_of(" \t\r\n\f<>/", name_start);
        if (end == searchable.npos) break;
        if (searchable[end] == '<') {
            pos = end - 1;
            continue;
        }
        const auto tag = std::string_view(searchable).substr(name_start, end - name_start);
        if (tag == "script" || tag == "style") {
            const auto close = searchable.find(tag == "script" ? "</script" : "</style", end);
            if (close == searchable.npos) break;
            pos = close;
            continue;
        }
        if (tag != "a" && tag != "meta" && tag != "base") continue;
        auto rest = page.substr(end);
        auto attributes = read_attributes(rest);
        pos = page.size() - rest.size();
        if (tag == "base" && base.empty()) base = unescape_amp(attributes["href"]);
        if (tag == "meta" && lower_ascii(attributes["name"]) == "referrer") {
            if (const auto next = doujin::referrer_policy(attributes["content"]); !next.empty()) policy = next;
        }
        if (tag == "a" && href.empty() && attributes["id"] == "downloadButton") {
            href = unescape_amp(attributes["href"]);
            auto rel = " " + lower_ascii(attributes["rel"]) + " ";
            std::replace_if(rel.begin(), rel.end(), attr_space, ' ');
            link_policy = rel.find(" noreferrer ") != rel.npos ? "no-referrer" :
                          std::string(doujin::referrer_policy(attributes["referrerpolicy"]));
        }
    }
    if (href.empty()) throw std::runtime_error("MediaFire returned no static download link; open the release in a browser");
    return {href, base, link_policy.empty() ? policy : link_policy};
}

} // namespace doujin
