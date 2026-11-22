#pragma once

#include "mediafire_page.hpp"
#include <charconv>
#include <cstdint>
#include <iterator>
#include <utility>
#include <vector>

namespace doujin {
inline constexpr std::size_t catalog_html_limit = 1024 * 1024;

inline void append_html_codepoint(std::string& out, std::uint32_t value) {
    if (value < 0x80) {
        out += static_cast<char>(value);
        return;
    }
    if (!value || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) value = 0xfffd;
    const unsigned bytes = value < 0x800 ? 2 : value < 0x10000 ? 3 : 4;
    const unsigned lead = bytes == 2 ? 0xc0 : bytes == 3 ? 0xe0 : 0xf0;
    out += static_cast<char>(lead | (value >> (6 * (bytes - 1))));
    for (unsigned i = bytes - 1; i > 0; --i)
        out += static_cast<char>(0x80 | ((value >> (6 * (i - 1))) & 63));
}

inline std::string decode_html(std::string_view text) {
    constexpr std::pair<std::string_view, std::uint32_t> entities[]{
#include "html_entities.inc"
    };
    std::string output;
    while (!text.empty()) {
        const auto amp = text.find('&');
        output.append(text.substr(0, amp));
        if (amp == text.npos) break;
        text.remove_prefix(amp + 1);
        const auto end = text.substr(0, 32).find(';');
        if (end != text.npos) {
            auto field = text.substr(0, end);
            if (field.starts_with('#')) {
                field.remove_prefix(1);
                const bool hex = field.starts_with('x') || field.starts_with('X');
                if (hex) field.remove_prefix(1);
                std::uint32_t scalar = 0;
                const auto parsed = std::from_chars(field.data(), field.data() + field.size(), scalar, hex ? 16 : 10);
                if (parsed.ec == std::errc{} && parsed.ptr == field.data() + field.size()) {
                    append_html_codepoint(output, scalar ? scalar : 0xfffd);
                    text.remove_prefix(end + 1);
                    continue;
                }
            } else {
                const auto match = std::lower_bound(std::begin(entities), std::end(entities), field,
                    [](const auto& entry, std::string_view key) { return entry.first < key; });
                if (match != std::end(entities) && match->first == field) {
                    append_html_codepoint(output, match->second);
                    text.remove_prefix(end + 1);
                    continue;
                }
            }
        }
        output += '&';
    }
    return output;
}

inline std::string collapse_html_text(std::string_view text) {
    std::string output;
    bool space = false;
    while (!text.empty()) {
        const auto byte = text.front();
        if (attr_space(byte) || byte == '\v') {
            space = true;
            text.remove_prefix(1);
            continue;
        }
        if (text.starts_with("\xc2\xa0") || text.starts_with("\xc2\x85")) {
            space = true;
            text.remove_prefix(2);
            continue;
        }
        if (text.starts_with("\xe3\x80\x80") || text.starts_with("\xe2\x80\xaf") ||
            (text.size() >= 3 && text.starts_with("\xe2\x80") &&
             static_cast<unsigned char>(text[2]) >= 0x80 && static_cast<unsigned char>(text[2]) <= 0x8a)) {
            space = true;
            text.remove_prefix(3);
            continue;
        }
        if (space && !output.empty()) output += ' ';
        space = false;
        output += byte;
        text.remove_prefix(1);
    }
    return output;
}

inline bool html_token(std::string_view tokens, std::string_view wanted) {
    while (!tokens.empty()) {
        const auto end = tokens.find_first_of(" \t\r\n\f");
        if (tokens.substr(0, end) == wanted) return true;
        if (end == tokens.npos) return false;
        tokens.remove_prefix(end + 1);
    }
    return false;
}

// A bounded element tree for the site's catalog pages. Text nodes retain
// document order; raw script/style and comments never produce catalog records.
class CatalogHtml {
public:
    struct Node {
        std::string name;
        HtmlAttributes attributes;
        std::string text;
        std::size_t parent = 0, end = 0;
    };

    explicit CatalogHtml(std::string_view page) {
        if (page.size() > catalog_html_limit) throw std::runtime_error("DoujinStyle response exceeds the HTML limit");
        const auto searchable = lower_ascii(std::string(page));
        std::vector<std::size_t> stack{0};
        const auto close = [&] {
            nodes[stack.back()].end = nodes.size();
            stack.pop_back();
        };
        const auto add = [&](Node node) {
            if (nodes.size() >= 48000) throw std::runtime_error("DoujinStyle HTML has too many nodes");
            node.parent = stack.back();
            node.end = nodes.size() + 1;
            nodes.push_back(std::move(node));
        };
        std::size_t pos = 0;
        while (pos < page.size()) {
            if (page[pos] != '<') {
                auto stop = page.find('<', pos);
                if (stop == page.npos) stop = page.size();
                auto part = page.substr(pos, stop - pos);
                add(Node{"", {}, decode_html(part)});
                pos = stop;
                continue;
            }
            if (page.substr(pos).starts_with("<!--")) {
                const auto stop = page.find("-->", pos + 4);
                if (stop == page.npos) break;
                pos = stop + 3;
                continue;
            }
            ++pos;
            if (pos >= page.size()) break;
            if (page[pos] == '!' || page[pos] == '?') {
                const auto stop = page.find('>', pos);
                if (stop == page.npos) break;
                pos = stop + 1;
                continue;
            }
            const bool closing = page[pos] == '/';
            if (closing) ++pos;
            const auto name_end = page.find_first_of(" \t\r\n\f/<>", pos);
            if (name_end == page.npos) break;
            if (page[name_end] == '<') {
                pos = name_end;
                continue;
            }
            const auto name = std::string_view(searchable).substr(pos, name_end - pos);
            if (name.empty() || name.front() < 'a' || name.front() > 'z') continue;
            if (closing) {
                const auto stop = page.find('>', name_end);
                if (stop == page.npos) break;
                pos = stop + 1;
                for (auto i = stack.size(); i > 1; --i) {
                    if (nodes[stack[i - 1]].name != name) continue;
                    while (stack.size() >= i) close();
                    break;
                }
                continue;
            }
            auto rest = page.substr(name_end);
            auto attributes = read_attributes(rest);
            if (rest.empty()) break;
            pos = page.size() - rest.size() + 1;
            if (name == "script" || name == "style") {
                const auto stop = searchable.find(name == "script" ? "</script" : "</style", pos);
                if (stop == searchable.npos) break;
                pos = stop;
                continue;
            }
            if ((name == "a" || name == "li" || name == "p") && nodes[stack.back()].name == name) close();
            for (auto& [key, value] : attributes) value = decode_html(value);
            add(Node{std::string(name), std::move(attributes), {}});
            if (html_token("area base br col embed hr img input link meta param source track wbr", name)) continue;
            if (stack.size() >= 128) throw std::runtime_error("DoujinStyle HTML nesting exceeds its limit");
            stack.push_back(nodes.size() - 1);
        }
        while (stack.size() > 1) close();
        nodes.front().end = nodes.size();
    }

    std::string_view attr(std::size_t index, std::string_view key) const {
        const auto& attrs = nodes[index].attributes;
        const auto match = attrs.find(std::string(key));
        return match == attrs.end() ? std::string_view{} : std::string_view(match->second);
    }
    bool has_class(std::size_t index, std::string_view name) const { return html_token(attr(index, "class"), name); }
    std::string clean(std::size_t index) const {
        std::string raw;
        for (auto i = index + 1; i < nodes[index].end; ++i) raw += nodes[i].text;
        return collapse_html_text(raw);
    }
    template<typename Predicate>
    std::size_t first(std::size_t index, Predicate pred) const {
        for (auto i = index + 1; i < nodes[index].end; ++i) if (pred(i)) return i;
        return 0;
    }

    std::vector<Node> nodes{Node{"", {}, {}}};
};
}
