#include "descriptor.hpp"
#include "zip_catalog.hpp"
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace doujin {
namespace {
std::uint64_t number(std::string_view value) {
    std::uint64_t result = 0;
    auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), result);
    if (error != std::errc{} || end != value.data() + value.size())
        throw std::runtime_error("invalid unsigned integer");
    return result;
}

std::string_view take_line(std::string_view& all) {
    const auto end = all.find('\n');
    auto line = all.substr(0, end);
    all.remove_prefix(end == all.npos ? all.size() : end + 1);
    if (line.ends_with('\r')) line.remove_suffix(1);
    for (unsigned char ch : line) {
        if (ch < 32) throw std::runtime_error("control character in descriptor");
    }
    return line;
}

bool is_field(std::string_view field, const auto& list) {
    for (auto entry : list) if (entry == field) return true;
    return false;
}

bool is_release_id(std::string_view value) {
    return !value.empty() && value.size() <= 16 && value.find_first_not_of("0123456789") == value.npos;
}

std::string http_origin(std::string_view url) {
    const auto authority = url.find("://") + 3;
    std::string origin(url.substr(0, url.find_first_of("/?#", authority)));
    std::transform(origin.begin(), origin.end(), origin.begin(), [](char ch) {
        return ch >= 'A' && ch <= 'Z' ? static_cast<char>(ch + ('a' - 'A')) : ch;
    });
    if (origin.ends_with(url.starts_with("https://") ? ":443" : ":80"))
        origin.resize(origin.find_last_of(':'));
    return origin;
}

std::uint32_t le(std::span<const unsigned char> bytes, std::size_t at, unsigned width) {
    std::uint32_t value = 0;
    for (unsigned i = 0; i < width; ++i) value |= std::uint32_t(bytes[at + i]) << (i * 8);
    return value;
}

void append_le(std::string& bytes, std::uint32_t value, unsigned width) {
    for (unsigned i = 0; i < width; ++i) bytes.push_back(static_cast<char>(value >> (i * 8)));
}

bool walk_zip_directory(std::uint64_t size, const ZipReadAt& read, auto visit, std::uint64_t available_tail = 65557) {
    if (size < 22 || available_tail < 22) return false;
    std::uint64_t directory = 0, directory_end = 0;
    unsigned entries = 0;
    bool found = false;
    for (const auto tail_limit : {std::uint64_t{8192}, available_tail}) {
        const auto base = size - std::min({size, tail_limit, available_tail});
        std::vector<unsigned char> tail(static_cast<std::size_t>(size - base));
        read(base, tail);
        for (std::size_t i = tail.size() - 22 + 1; i-- > 0; ) {
            if (le(tail, i, 4) != 0x06054b50 || i + 22 + le(tail, i + 20, 2) != tail.size()) continue;
            entries = le(tail, i + 10, 2);
            const auto bytes = le(tail, i + 12, 4);
            directory = le(tail, i + 16, 4);
            directory_end = directory + bytes;
            if (le(tail, i + 4, 4) || entries != le(tail, i + 8, 2) || entries == 65535 ||
                bytes > 8 * 1024 * 1024 || directory_end != base + i) return false;
            found = true;
            break;
        }
        if (found) break;
        if (base == 0) return false;
    }
    if (!found) return false;
    auto at = directory;
    for (unsigned i = 0; i < entries; ++i) {
        std::array<unsigned char, 46> row{};
        if (at + row.size() > directory_end) throw std::runtime_error("Invalid ZIP directory bounds");
        read(at, row);
        if (le(row, 0, 4) != 0x02014b50) throw std::runtime_error("Invalid ZIP directory entry");
        const auto name_size = le(row, 28, 2);
        const auto name_at = at + row.size();
        at = name_at + name_size + le(row, 30, 2) + le(row, 32, 2);
        if (at > directory_end) throw std::runtime_error("Invalid ZIP directory entry length");
        if (!visit(row, name_at, name_size, directory)) return true;
    }
    return at == directory_end;
}

bool supported_zip_entry(std::span<const unsigned char> row) {
    const auto flags = le(row, 8, 2), method = le(row, 10, 2);
    return le(row, 34, 2) == 0 && (flags & 0x2061) == 0 && (method == 0 || method == 8) &&
        le(row, 20, 4) != UINT32_MAX && le(row, 24, 4) != UINT32_MAX && le(row, 42, 4) != UINT32_MAX;
}

std::string matching_name(std::string_view text) {
    std::string result(text);
    for (auto& c : result) {
        if (c == '\\') c = '/';
        else if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    }
    return result;
}

bool normal_archive_path(std::string_view path) {
    if (path.empty() || std::any_of(path.begin(), path.end(), [](unsigned char c) { return c < 32 || c == ':'; }))
        return false;
    for (;;) {
        const auto slash = path.find('/');
        const auto part = path.substr(0, slash);
        if (part.empty() || part == "." || part == ".." || part == "__macosx" || part.starts_with("._"))
            return false;
        if (slash == path.npos) return true;
        path.remove_prefix(slash + 1);
    }
}
}

std::optional<ZipMember> find_zip_member(std::uint64_t size, std::string_view member, const ZipReadAt& read) {
    std::optional<ZipMember> result;
    walk_zip_directory(size, read, [&](auto row, auto name_at, auto name_size, auto directory) {
        if (name_size != member.size()) return true;
        std::vector<unsigned char> name(name_size);
        read(name_at, name);
        if (std::string_view(reinterpret_cast<const char*>(name.data()), name.size()) != member) return true;
        if (!supported_zip_entry(row)) return false;
        const auto flags = le(row, 8, 2);
        const auto method = le(row, 10, 2);
        const auto packed = le(row, 20, 4), unpacked = le(row, 24, 4);
        const std::uint64_t local = le(row, 42, 4);
        std::array<unsigned char, 30> header{};
        if (local + header.size() + name_size > directory) throw std::runtime_error("Invalid ZIP local header bounds");
        read(local, header);
        if (le(header, 0, 4) != 0x04034b50 || flags != le(header, 6, 2) || method != le(header, 8, 2) ||
            name_size != le(header, 26, 2) || (method == 0 && packed != unpacked))
            throw std::runtime_error("ZIP member header does not match its directory entry");
        const auto payload = local + header.size() + name_size + le(header, 28, 2);
        if (payload + packed > directory) throw std::runtime_error("ZIP member exceeds archive bounds");
        std::vector<unsigned char> local_name(name_size);
        read(local + header.size(), local_name);
        if (local_name != name) throw std::runtime_error("ZIP member name changed between headers");
        result = ZipMember{payload, packed, unpacked, le(row, 16, 4), static_cast<std::uint16_t>(method)};
        return false;
    });
    // The host ZIP reader may support legacy filename encodings or ZIP variants.
    return result;
}

std::optional<ZipMember> find_zip_member(std::uint64_t size, const ZipLocator& locator, const ZipReadAt& read) {
    const auto load = [&](std::uint64_t offset, std::size_t length) {
        std::string bytes(length, '\0');
        if (length) read(offset, {reinterpret_cast<unsigned char*>(bytes.data()), bytes.size()});
        return bytes;
    };
    std::optional<ZipCatalogLayout> layout;
    for (const auto length : {std::min<std::uint64_t>(size, 8192), std::min(size, zip_catalog_max_tail)}) {
        layout = zip_catalog_layout(size, load(size - length, static_cast<std::size_t>(length)));
        if (layout || length == size) break;
    }
    if (!layout) return {};
    const auto members = zip_catalog_members(load(layout->start, static_cast<std::size_t>(layout->bytes)),
                                             layout->start, layout->entries);
    if (!members) return {};
    const ZipCatalogMember* found = nullptr;
    auto boundary = layout->start;
    for (const auto& member : *members) {
        if (member.header == locator.local_header) {
            if (found || member.name != locator.raw_name || member.crc != locator.crc) return {};
            found = &member;
        } else if (member.header > locator.local_header) {
            boundary = std::min(boundary, member.header);
        }
    }
    if (!found || found->packed > UINT32_MAX || found->size > UINT32_MAX) return {};
    const auto local = load(found->header, 30 + found->name.size());
    const auto payload = zip_catalog_payload(local, found->name, found->header, boundary, found->packed,
                                             found->size, found->crc, found->method, found->flags);
    if (!payload) return {};
    return ZipMember{*payload, static_cast<std::uint32_t>(found->packed), static_cast<std::uint32_t>(found->size),
                     found->crc, found->method};
}

ZipLocator parse_zip_locator(std::string_view text) {
    const auto first = text.find(':');
    const auto second = first == text.npos ? text.npos : text.find(':', first + 1);
    if (second == text.npos) throw std::runtime_error("invalid ZIP member locator");
    auto raw = decode_hex(text.substr(0, first), 4096);
    const auto header = number(text.substr(first + 1, second - first - 1));
    const auto crc = number(text.substr(second + 1));
    if (raw.empty() || raw.find('\0') != raw.npos || crc > UINT32_MAX)
        throw std::runtime_error("invalid ZIP member locator");
    return {std::move(raw), header, static_cast<std::uint32_t>(crc)};
}

std::string format_zip_locator(const ZipLocator& locator) {
    return encode_hex(locator.raw_name) + ":" + std::to_string(locator.local_header) + ":" + std::to_string(locator.crc);
}

std::vector<std::string> find_zip_artwork(std::uint64_t size, std::string_view track, std::string_view album,
                                         ArtworkType type, std::uint32_t limit, const ZipReadAt& read) {
    const auto track_path = matching_name(track);
    if (!normal_archive_path(track_path)) return {};
    const auto slash = track_path.find_last_of('/');
    const auto folder = std::string_view(track_path).substr(0, slash == track_path.npos ? 0 : slash + 1);
    const auto filename = std::string_view(track_path).substr(folder.size());
    const auto stem = filename.substr(0, filename.find_last_of('.'));
    const auto album_name = matching_name(album);
    std::vector<std::tuple<std::size_t, std::size_t, std::string>> candidates;
    walk_zip_directory(size, read, [&](auto row, auto name_at, auto name_size, auto) {
        if (!supported_zip_entry(row) || name_size < 5 || le(row, 24, 4) > limit || le(row, 20, 4) > limit)
            return true;
        std::string name(name_size, '\0');
        read(name_at, {reinterpret_cast<unsigned char*>(name.data()), name.size()});
        const auto matching = matching_name(name);
        if (!normal_archive_path(matching)) return true;
        const auto last = matching.find_last_of('/');
        const auto directory = std::string_view(matching).substr(0, last == matching.npos ? 0 : last + 1);
        if (!folder.starts_with(directory)) return true;
        const auto base = std::string_view(matching).substr(directory.size());
        const auto dot = base.find_last_of('.');
        if (dot == base.npos || !is_field(base.substr(dot), std::array{".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}))
            return true;
        const auto image = base.substr(0, dot);
        const auto rank = [&](const auto& patterns) {
            return static_cast<std::size_t>(std::find(patterns.begin(), patterns.end(), image) - patterns.begin());
        };
        std::size_t priority = 0, pattern_count = 0;
        switch (type) {
        case ArtworkType::front: {
            const std::array<std::string_view, 7> patterns{"front", "cover", directory == folder ? stem : "",
                album_name, "folder", "frontcover", "jacket"};
            priority = rank(patterns);
            pattern_count = patterns.size();
            break;
        }
        case ArtworkType::back: priority = rank(std::array{"back", "backcover"}); pattern_count = 2; break;
        case ArtworkType::disc: priority = rank(std::array{"disc", "cd"}); pattern_count = 2; break;
        case ArtworkType::artist: priority = rank(std::array{"artist"}); pattern_count = 1; break;
        }
        if (!image.empty() && priority < pattern_count)
            candidates.emplace_back(folder.size() - directory.size(), priority, std::move(name));
        return true;
    });
    std::sort(candidates.begin(), candidates.end());
    std::vector<std::string> result;
    for (auto& candidate : candidates) result.push_back(std::move(std::get<2>(candidate)));
    return result;
}

SingleMemberZip make_single_member_zip(const ZipMember& entry, std::string_view name) {
    SingleMemberZip zip;
    auto attributes = [&](std::string& out) {
        append_le(out, 20, 2);
        append_le(out, 0x0800, 2); // UTF-8 filename, no data descriptor needed
        append_le(out, entry.method, 2);
        append_le(out, 0x00210000, 4); // 1980-01-01
        append_le(out, entry.crc, 4);
        append_le(out, entry.packed_size, 4);
        append_le(out, entry.size, 4);
        append_le(out, static_cast<std::uint32_t>(name.size()), 2);
        append_le(out, 0, 2); // extra fields
    };
    append_le(zip.header, 0x04034b50, 4);
    attributes(zip.header);
    zip.header += name;
    append_le(zip.trailer, 0x02014b50, 4);
    append_le(zip.trailer, 20, 2);
    attributes(zip.trailer);
    zip.trailer.append(14, '\0'); // comment length, disk, attributes and local header offset
    zip.trailer += name;
    const auto directory_size = static_cast<std::uint32_t>(zip.trailer.size());
    if (zip.header.size() + std::uint64_t(entry.packed_size) + directory_size >= UINT32_MAX)
        throw std::runtime_error("Member exceeds single-entry ZIP size limit");
    append_le(zip.trailer, 0x06054b50, 4);
    append_le(zip.trailer, 0, 4);
    append_le(zip.trailer, 1, 2);
    append_le(zip.trailer, 1, 2);
    append_le(zip.trailer, directory_size, 4);
    append_le(zip.trailer, static_cast<std::uint32_t>(zip.header.size()) + entry.packed_size, 4);
    append_le(zip.trailer, 0, 2);
    return zip;
}

std::string release_id_from_filename(std::string_view path) {
    const auto slash = path.find_last_of("/\\");
    const auto name = path.substr(slash == path.npos ? 0 : slash + 1);
    const auto dash = name.find('-');
    if (dash == name.npos || !is_release_id(name.substr(0, dash)) || name.size() != dash + 29 ||
        !name.ends_with(".djt") ||
        name.substr(dash + 1, 24).find_first_not_of("0123456789abcdef") != name.npos)
        return {};
    return std::string(name.substr(0, dash));
}

std::string release_id_from_reference_path(std::string_view path) {
    if (path.find('\\') != path.npos) return {};
    const auto slash = path.find_last_of('/');
    if (slash == path.npos) return release_id_from_filename(path);
    const auto scope = path.substr(0, slash);
    if (scope != "undated") {
        if (scope.size() != 7 || scope[4] != '/' || scope.substr(0, 4).find_first_not_of("0123456789") != scope.npos ||
            scope.substr(5).find_first_not_of("0123456789") != scope.npos ||
            scope.substr(5) < "01" || scope.substr(5) > "12") return {};
    }
    return release_id_from_filename(path.substr(slash + 1));
}

void validate_url(std::string_view url) {
    if (url.size() > 8192 || (!url.starts_with("https://") && !url.starts_with("http://")))
        throw std::runtime_error("expected an HTTP(S) file URL");
    const auto authority = url.substr(url.find("://") + 3);
    const auto host = authority.substr(0, authority.find_first_of("/?#"));
    if (host.empty() || host.find('@') != host.npos || url.find('#') != url.npos)
        throw std::runtime_error("URL contains credentials, a fragment, or no host");
    for (unsigned char ch : url) {
        if (ch <= 32 || ch == '\\' || ch == 127) throw std::runtime_error("invalid URL character");
    }
}

MegaLink parse_mega_link(std::string_view url) {
    const auto hash = url.find('#');
    if (hash == url.npos) throw std::runtime_error("MEGA public file link is missing its key");
    const auto share = url.substr(0, hash);
    validate_url(share);
    const auto origin = http_origin(share);
    if (origin != "https://mega.nz" && origin != "https://www.mega.nz" &&
        origin != "https://mega.co.nz" && origin != "https://www.mega.co.nz")
        throw std::runtime_error("Expected an HTTPS public MEGA file link");
    const auto slash = share.find('/', 8);
    auto path = slash == share.npos ? std::string_view{} : share.substr(slash);
    auto key = url.substr(hash + 1);
    std::string_view handle;
    if (path.starts_with("/file/")) {
        path.remove_prefix(6);
        if (path.ends_with('/')) path.remove_suffix(1);
        handle = path;
    } else if (path == "/" && key.starts_with('!') && key.size() > 9 && key[9] == '!') {
        handle = key.substr(1, 8);
        key.remove_prefix(10);
    }
    constexpr std::string_view alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    if (handle.size() != 8 || key.size() != 43 || handle.find_first_not_of(alphabet) != handle.npos ||
        key.find_first_not_of(alphabet) != key.npos)
        throw std::runtime_error("Expected a single MEGA public file with a valid file key");
    MegaLink result{std::string(handle), {}};
    unsigned pending = 0, bits = 0, offset = 0;
    for (const char ch : key) {
        pending = (pending << 6) | static_cast<unsigned>(alphabet.find(ch));
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            result.key[offset++] = static_cast<unsigned char>(pending >> bits);
        }
    }
    return result;
}

void validate_archive_url(std::string_view url) {
    if (url.find('#') == url.npos) validate_url(url);
    else parse_mega_link(url);
}

ContentRange mega_range(std::string_view header, std::uint64_t size) {
    if (!header.starts_with("bytes=") || size == 0) throw std::runtime_error("Invalid MEGA range");
    header.remove_prefix(6);
    const auto dash = header.find('-');
    if (dash == header.npos) throw std::runtime_error("Invalid MEGA range");
    const auto last = number(header.substr(dash + 1));
    if (dash == 0) {
        if (last == 0) throw std::runtime_error("Empty MEGA range");
        return {size - std::min(last, size), size - 1, size};
    }
    const auto first = number(header.substr(0, dash));
    if (first >= size || last < first) throw std::runtime_error("MEGA range outside the archive");
    return {first, std::min(last, size - 1), size};
}

std::string format_release_date(std::string_view value) {
    if (value.size() < 8 || value.size() > 10 || std::string_view("-./").find(value[4]) == value.npos) return {};
    const auto after_month = value.find_first_of("-./", 5);
    if (after_month == value.npos || after_month < 6 || after_month > 7 ||
        value.size() - after_month < 2 || value.size() - after_month > 3) return {};
    try {
        const auto year = number(value.substr(0, 4));
        const auto month = number(value.substr(5, after_month - 5));
        const auto day = number(value.substr(after_month + 1));
        if (year == 0 || !std::chrono::year_month_day{std::chrono::year(static_cast<int>(year)),
            std::chrono::month(static_cast<unsigned>(month)), std::chrono::day(static_cast<unsigned>(day))}.ok()) return {};
        char result[11]{};
        std::snprintf(result, sizeof result, "%04u-%02u-%02u", static_cast<unsigned>(year),
                      static_cast<unsigned>(month), static_cast<unsigned>(day));
        return result;
    } catch (const std::runtime_error&) { return {}; }
}

std::string release_event_from_tag(std::string_view value) {
    std::string key;
    for (std::size_t i = 0; i < value.size(); ++i) {
        const auto part = value.substr(i);
        if (part.starts_with("‐") || part.starts_with("‑") || part.starts_with("–")) {
            key += '-';
            i += 2;
        } else if (const auto ch = value[i]; ch != ' ' && ch != '\t') {
            key += ch >= 'a' && ch <= 'z' ? static_cast<char>(ch - ('a' - 'A')) : ch;
        }
    }
    const auto code = [&](std::string_view prefix, std::size_t min, std::size_t max, bool dash) {
        if (!key.starts_with(prefix)) return std::string{};
        auto count = std::string_view(key).substr(prefix.size());
        if (dash && count.starts_with('-')) count.remove_prefix(1);
        if (count.size() < min || count.size() > max ||
            count.find_first_not_of("0123456789") != count.npos) return std::string{};
        return std::string(prefix) + (prefix == "M3" ? "-" : "") + std::string(count);
    };
    if (auto event = code("M3", 1, 3, true); !event.empty()) return event;
    if (auto event = code("C", 2, 3, true); !event.empty()) return event;
    for (const auto prefix : {"ARTS", "RTS", "THK", "TMKS"})
        if (auto event = code(prefix, 1, 2, false); !event.empty()) return event;
    return code("RTSSP", 0, 1, false);
}

std::string catalog_number_from_tag(std::string_view value) {
    constexpr std::string_view whitespace = " \t\n\r\f\v";
    const auto first = value.find_first_not_of(whitespace);
    if (first == value.npos) return {};
    value = value.substr(first, value.find_last_not_of(whitespace) - first + 1);
    if (value.size() < 4 || value.size() > 32) return {};

    std::string code;
    while (!value.empty()) {
        if (value.starts_with("‐") || value.starts_with("‑") || value.starts_with("–")) {
            code += '-';
            value.remove_prefix(3);
        } else {
            const auto ch = value.front();
            code += ch >= 'a' && ch <= 'z' ? static_cast<char>(ch - ('a' - 'A')) : ch;
            value.remove_prefix(1);
        }
    }
    const auto dash = code.find('-');
    if (dash == code.npos || dash < 2 || dash > 18 || code.front() < 'A' || code.front() > 'Z') return {};
    const auto parts = std::string_view(code);
    const auto suffix = parts.substr(dash + 1);
    constexpr std::string_view alphanumeric = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    if (parts.substr(0, dash).find_first_not_of(alphanumeric) != parts.npos || suffix.empty() ||
        suffix.size() > 11 || suffix.find_first_not_of(alphanumeric) != suffix.npos) return {};
    // The numeric digit needs at most five alphanumeric characters on either side.
    const auto start = suffix.size() > 6 ? suffix.size() - 6 : 0;
    if (suffix.substr(start, 6 - start).find_first_of("0123456789") == suffix.npos ||
        !release_event_from_tag(code).empty()) return {};
    return code;
}

std::vector<std::size_t> catalog_source_order(std::string_view order) {
    constexpr std::array names{"track", "release", "lookup", "album"};
    const auto clean = [](std::string_view token) -> std::string {
        const auto first = token.find_first_not_of(" \t");
        if (first == token.npos) return {};
        return matching_name(token.substr(first, token.find_last_not_of(" \t") - first + 1));
    };
    if (clean(order) == "none") return {};
    std::vector<std::size_t> result;
    for (;;) {
        const auto comma = order.find(',');
        const auto name = clean(order.substr(0, comma));
        const auto at = std::find(names.begin(), names.end(), name);
        if (at == names.end()) return {0, 1, 2, 3};
        const auto index = static_cast<std::size_t>(at - names.begin());
        if (std::find(result.begin(), result.end(), index) == result.end()) result.push_back(index);
        if (comma == order.npos) return result;
        order.remove_prefix(comma + 1);
    }
}

std::string_view referrer_policy(std::string_view value) {
    constexpr std::array policies{"no-referrer", "no-referrer-when-downgrade", "origin", "origin-when-cross-origin",
                                  "same-origin", "strict-origin", "strict-origin-when-cross-origin", "unsafe-url"};
    std::string_view selected;
    while (!value.empty()) {
        auto candidate = value.substr(0, value.find(','));
        const auto first = candidate.find_first_not_of(" \t");
        if (first != candidate.npos) {
            candidate = candidate.substr(first, candidate.find_last_not_of(" \t") - first + 1);
            if (is_field(candidate, policies)) selected = candidate;
        }
        if (const auto comma = value.find(','); comma != value.npos) value.remove_prefix(comma + 1);
        else break;
    }
    return selected;
}

std::string navigation_referrer(std::string_view source, std::string_view target, std::string_view policy) {
    if (source.empty() || policy == "no-referrer") return {};
    validate_url(source);
    validate_url(target);
    const auto downgrade = source.starts_with("https://") && target.starts_with("http://");
    const auto origin = http_origin(source);
    const auto cross_origin = origin != http_origin(target);
    if (policy == "same-origin" && cross_origin) return {};
    if (downgrade && policy != "origin" && policy != "origin-when-cross-origin" && policy != "unsafe-url") return {};
    if (policy == "origin" || policy == "strict-origin" ||
        (cross_origin && policy != "no-referrer-when-downgrade" && policy != "unsafe-url")) return origin + '/';
    const auto path = source.find_first_of("/?", source.find("://") + 3);
    if (path == source.npos) return origin + '/';
    return origin + (source[path] == '?' ? "/" : "") + std::string(source.substr(path));
}

Descriptor parse_descriptor(std::string_view text) {
    if (text.size() > 65536) throw std::runtime_error("descriptor is too large");
    if (text.starts_with("\xef\xbb\xbf")) text.remove_prefix(3);
    if (take_line(text) != "DOUJIN-TRACK 1") throw std::runtime_error("invalid descriptor signature");
    Descriptor result;
    constexpr std::array tags{"TITLE", "ARTIST", "ALBUM", "ALBUM ARTIST", "DATE", "DATE SOURCE", "RELEASE YEAR",
                              "TRACKNUMBER", "DISCNUMBER", "TOTALTRACKS", "TOTALDISCS", "LABEL", "PUBLISHER",
                              "CATALOGNUMBER", "DOUJINSTYLE DATE ADDED", "BANGUMI URL"};
    constexpr std::array multi_tags{"GENRE", "RELEASE GENRE", "RELEASE EVENT", "DOUJINSTYLE TAG", "COMMENT"};
    constexpr std::array tech{"DURATION_MS", "SAMPLERATE", "CHANNELS", "BITSPERSAMPLE", "BITRATE_KBPS",
                              "SOURCE_BYTES", "ZIP_MEMBER_COMPRESSED_BYTES"};
    constexpr std::array tech_text{"CODEC", "ENCODING"};
    while (!text.empty()) {
        auto line = take_line(text);
        if (line.empty()) continue;
        const auto eq = line.find('=');
        if (eq == line.npos || eq == 0) throw std::runtime_error("invalid descriptor field");
        const auto key = line.substr(0, eq), value = line.substr(eq + 1);
        if (key == "URL") result.url = value;
        else if (key == "REFERRER") result.referrer = value;
        else if (key == "MEMBER") result.member = value;
        else if (key == "MEMBER_LOCATOR") {
            if (result.zip_locator) throw std::runtime_error("duplicate ZIP member locator");
            result.zip_locator = parse_zip_locator(value);
        }
        else if (key == "FILENAME") result.filename = value;
        else if (key == "RELEASE_ID") {
            if (!is_release_id(value)) throw std::runtime_error("invalid DoujinStyle release ID");
            result.release_id = value;
        }
        else if (key == "CATALOG SOURCES") result.has_catalog_sources = value == "1";
        else if (key == "CATALOG SOURCE TRACK") result.catalog_sources[0] = value;
        else if (key == "CATALOG SOURCE RELEASE") result.catalog_sources[1] = value;
        else if (key == "CATALOG SOURCE LOOKUP") result.catalog_sources[2] = value;
        else if (key == "CATALOG SOURCE ALBUM") result.catalog_sources[3] = value;
        else if (is_field(key, tags)) result.metadata[std::string(key)] = value;
        else if (is_field(key, multi_tags) && !value.empty()) result.multi_metadata[std::string(key)].emplace_back(value);
        else if (is_field(key, tech)) result.technical[std::string(key)] = number(value);
        else if (is_field(key, tech_text)) result.technical_text[std::string(key)] = value;
    }
    if (result.zip_locator && result.member.empty()) throw std::runtime_error("ZIP locator requires a member");
    if (result.member.empty()) validate_url(result.url);
    else validate_archive_url(result.url);
    if (!result.referrer.empty()) validate_url(result.referrer);
    if (result.filename.empty() && !result.member.empty()) {
        const auto last = result.member.find_last_of("/\\");
        result.filename = result.member.substr(last == std::string::npos ? 0 : last + 1);
    }
    if (result.filename.empty() || result.filename.size() > 512 ||
        result.filename.find_first_of("/\\:?#|") != std::string::npos ||
        result.filename.find('.') == std::string::npos)
        throw std::runtime_error("expected a decoder filename with an extension");
    if (result.filename.ends_with(".djt") || result.filename.ends_with(".DJT"))
        throw std::runtime_error("nested Doujin track descriptor");
    return result;
}

ContentRange parse_content_range(std::string_view header) {
    if (!header.starts_with("bytes ")) throw std::runtime_error("invalid Content-Range unit");
    header.remove_prefix(6);
    const auto dash = header.find('-');
    const auto slash = header.find('/');
    if (dash == header.npos || slash == header.npos || dash >= slash)
        throw std::runtime_error("invalid Content-Range span");
    const ContentRange result{number(header.substr(0, dash)), number(header.substr(dash + 1, slash - dash - 1)),
                              number(header.substr(slash + 1))};
    if (result.first > result.last || result.last >= result.size)
        throw std::runtime_error("invalid Content-Range bounds");
    return result;
}

std::string encode_hex(std::string_view text) {
    constexpr std::string_view digits = "0123456789abcdef";
    std::string output;
    output.reserve(text.size() * 2);
    for (unsigned char byte : text) {
        output += digits[byte >> 4];
        output += digits[byte & 15];
    }
    return output;
}

std::string decode_hex(std::string_view text, std::size_t max_bytes) {
    if (text.size() % 2 || text.size() / 2 > max_bytes)
        throw std::runtime_error("invalid hexadecimal byte sequence");
    auto digit = [](unsigned char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        throw std::runtime_error("invalid hexadecimal byte sequence");
    };
    std::string output;
    output.reserve(text.size() / 2);
    for (std::size_t i = 0; i < text.size(); i += 2)
        output += static_cast<char>((digit(text[i]) << 4) | digit(text[i + 1]));
    return output;
}
}
