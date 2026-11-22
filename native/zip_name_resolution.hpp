#pragma once

#include <algorithm>
#include <array>
#include <climits>
#include <iterator>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <iconv.h>
#include <cerrno>
#endif

namespace doujin::zip_name {
struct Evidence {
    std::string_view raw;
    std::uint16_t flags;
    std::string_view unicode_path;
    bool bad_unicode_path;
};
struct Resolved {
    std::string name;
    std::string encoding;
    bool warning;
    std::string alternate;
};

inline void append_utf8(std::string& output, std::uint32_t code) {
    if (code < 0x80) output += static_cast<char>(code);
    else if (code < 0x800) {
        output += static_cast<char>(0xc0 | (code >> 6));
        output += static_cast<char>(0x80 | (code & 63));
    } else if (code < 0x10000) {
        output += static_cast<char>(0xe0 | (code >> 12));
        output += static_cast<char>(0x80 | ((code >> 6) & 63));
        output += static_cast<char>(0x80 | (code & 63));
    } else {
        output += static_cast<char>(0xf0 | (code >> 18));
        output += static_cast<char>(0x80 | ((code >> 12) & 63));
        output += static_cast<char>(0x80 | ((code >> 6) & 63));
        output += static_cast<char>(0x80 | (code & 63));
    }
}

inline bool valid_utf8(std::string_view input, std::vector<std::uint32_t>* points = nullptr) {
    while (!input.empty()) {
        const auto first = static_cast<std::uint8_t>(input.front());
        unsigned length = 0;
        std::uint32_t code = 0;
        if (first < 0x80) { length = 1; code = first; }
        else if (first >= 0xc2 && first <= 0xdf) { length = 2; code = first & 31; }
        else if (first >= 0xe0 && first <= 0xef) { length = 3; code = first & 15; }
        else if (first >= 0xf0 && first <= 0xf4) { length = 4; code = first & 7; }
        else return false;
        if (length > input.size()) return false;
        for (unsigned at = 1; at < length; ++at) {
            const auto ch = static_cast<std::uint8_t>(input[at]);
            if ((ch & 0xc0) != 0x80) return false;
            code = (code << 6) | (ch & 63);
        }
        if ((length == 2 && code < 0x80) || (length == 3 && code < 0x800) ||
            (length == 4 && code < 0x10000) || (code >= 0xd800 && code <= 0xdfff) ||
            code > 0x10ffff || code < 32 || code == 127) return false;
        if (points) points->push_back(code);
        input.remove_prefix(length);
    }
    return true;
}

inline std::optional<std::string> decode_utf8(std::string_view raw) {
    if (raw.empty() || !valid_utf8(raw)) return {};
    return std::string(raw);
}

inline bool is_ascii(std::string_view raw) {
    for (unsigned char ch : raw) if (ch > 127) return false;
    return true;
}

inline std::optional<std::string> decode_cp932(std::string_view raw) {
    if (raw.empty()) return {};
#ifdef _WIN32
    // Validate by round trip: legacy CP932 conversions otherwise silently replace bad bytes.
    if (raw.size() > static_cast<std::size_t>(INT_MAX)) return {};
    auto length = MultiByteToWideChar(932, 0, raw.data(), static_cast<int>(raw.size()), nullptr, 0);
    if (length <= 0) return {};
    std::wstring wide(static_cast<std::size_t>(length), L'\0');
    if (MultiByteToWideChar(932, 0, raw.data(), static_cast<int>(raw.size()), wide.data(), length) != length) return {};
    BOOL substituted = FALSE;
    const auto back_size = WideCharToMultiByte(932, 0, wide.data(), length, nullptr, 0, nullptr, &substituted);
    if (back_size <= 0 || substituted || static_cast<std::size_t>(back_size) != raw.size()) return {};
    std::string roundtrip(static_cast<std::size_t>(back_size), '\0');
    if (WideCharToMultiByte(932, 0, wide.data(), length, roundtrip.data(), back_size,
                            nullptr, &substituted) != back_size || substituted || roundtrip != raw) return {};
    const auto utf8_size = WideCharToMultiByte(CP_UTF8, 0, wide.data(), length, nullptr, 0, nullptr, nullptr);
    if (utf8_size <= 0) return {};
    std::string result(static_cast<std::size_t>(utf8_size), '\0');
    if (WideCharToMultiByte(CP_UTF8, 0, wide.data(), length, result.data(), utf8_size, nullptr, nullptr) != utf8_size ||
        !valid_utf8(result)) return {};
    return result;
#else
    iconv_t converter = iconv_open("UTF-8", "CP932");
    if (converter == reinterpret_cast<iconv_t>(-1)) return {};
    std::string result(raw.size() * 4 + 4, '\0');
    char* from = const_cast<char*>(raw.data());
    std::size_t left = raw.size();
    char* to = result.data();
    std::size_t room = result.size();
    const auto ok = iconv(converter, &from, &left, &to, &room) != static_cast<std::size_t>(-1) && left == 0;
    iconv_close(converter);
    if (!ok) return {};
    result.resize(result.size() - room);
    return valid_utf8(result) ? std::optional(std::move(result)) : std::nullopt;
#endif
}

inline std::string decode_cp437(std::string_view raw) {
    constexpr std::uint32_t upper[] = {
#include "reference_cp437.inc"
    };
    static_assert(std::size(upper) == 128);
    std::string result;
    result.reserve(raw.size() * 2);
    for (unsigned char ch : raw) append_utf8(result, ch < 128 ? ch : upper[ch - 128]);
    return result;
}

inline std::size_t japanese_count(std::string_view value, bool* kana = nullptr) {
    std::vector<std::uint32_t> points;
    if (!valid_utf8(value, &points)) return 0;
    std::size_t count = 0;
    for (const auto code : points) {
        const bool in_kana = code >= 0x3041 && code <= 0x30fa;
        count += in_kana || (code >= 0x4e00 && code <= 0x9fff);
        if (kana && in_kana) *kana = true;
    }
    return count;
}

inline std::vector<Resolved> resolve(std::span<const Evidence> input) {
    struct Candidate {
        std::optional<std::string> native;
        std::optional<std::string> unicode;
        std::optional<std::string> cp932;
        bool warning = false;
        bool japanese = false;
    };
    std::vector<Candidate> candidates;
    candidates.reserve(input.size());
    bool japanese_archive = false;
    for (const auto& record : input) {
        Candidate entry;
        entry.native = (record.flags & 0x800) ? decode_utf8(record.raw) : std::nullopt;
        entry.unicode = !record.unicode_path.empty() ? decode_utf8(record.unicode_path) : std::nullopt;
        const auto cp932 = !(record.flags & 0x800) && !is_ascii(record.raw)
            ? decode_cp932(record.raw) : std::nullopt;
        bool kana = false;
        const auto count = cp932 ? japanese_count(*cp932, &kana) : 0;
        if (count) entry.cp932 = cp932;
        if (cp932 && (entry.unicode == cp932 || count >= 3 || (count >= 2 &&
            (kana || std::count(record.raw.begin(), record.raw.end(), '\\') >
                     std::count(cp932->begin(), cp932->end(), '\\'))))) japanese_archive = true;
        entry.warning = record.bad_unicode_path || (!record.unicode_path.empty() && !entry.unicode) ||
            (entry.native && entry.unicode && entry.native != entry.unicode) ||
            ((record.flags & 0x800) && !entry.native);
        candidates.push_back(std::move(entry));
    }
    std::vector<Resolved> output;
    output.reserve(input.size());
    for (std::size_t at = 0; at < input.size(); ++at) {
        const auto& item = candidates[at];
        if (item.native) output.push_back({*item.native, "utf-8", item.warning,
                                        item.unicode && *item.unicode != *item.native ? *item.unicode : ""});
        else if (item.unicode) output.push_back({*item.unicode, "unicode-path", item.warning, ""});
        else if (japanese_archive && item.cp932) output.push_back({*item.cp932, "cp932-inferred", item.warning, ""});
        else output.push_back({decode_cp437(input[at].raw), "cp437", item.warning, ""});
    }
    return output;
}
} // namespace doujin::zip_name
