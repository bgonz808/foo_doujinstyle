#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace doujin {
inline constexpr std::uint64_t zip_catalog_max_directory = 2 * 1024 * 1024;
inline constexpr std::uint64_t zip_catalog_max_tail = 22 + 65535 + 20 + 56;

struct ZipCatalogLayout {
    std::uint64_t start, bytes, entries;
};

struct ZipCatalogMember {
    std::string name;
    std::uint64_t packed, size, header;
    std::uint32_t crc;
    std::uint16_t method, flags;
    std::string unicode_path;
    bool bad_unicode_path = false;
};

inline std::uint64_t zip_catalog_le(std::string_view bytes, std::size_t at, unsigned width) {
    std::uint64_t result = 0;
    for (unsigned i = 0; i < width; ++i)
        result |= std::uint64_t(static_cast<unsigned char>(bytes[at + i])) << (8 * i);
    return result;
}

// The caller supplies validated suffix bytes. Multi-disk archives and prepended
// archives are left to the compatibility reader.
inline std::optional<ZipCatalogLayout> zip_catalog_layout(std::uint64_t total, std::string_view tail) {
    if (tail.size() < 22 || tail.size() > total || tail.size() > zip_catalog_max_tail) return {};
    const auto base = total - tail.size();
    for (auto at = tail.size() - 22 + 1; at-- > 0;) {
        if (zip_catalog_le(tail, at, 4) != 0x06054b50 ||
            zip_catalog_le(tail, at + 20, 2) != tail.size() - at - 22) continue;
        if (zip_catalog_le(tail, at + 4, 4)) return {};
        auto on_disk = zip_catalog_le(tail, at + 8, 2);
        ZipCatalogLayout result{zip_catalog_le(tail, at + 16, 4), zip_catalog_le(tail, at + 12, 4),
                                zip_catalog_le(tail, at + 10, 2)};
        auto directory_end = base + at;
        if (at >= 20 && zip_catalog_le(tail, at - 20, 4) == 0x07064b50) {
            const auto record = zip_catalog_le(tail, at - 12, 8);
            if (zip_catalog_le(tail, at - 16, 4) != 0 || zip_catalog_le(tail, at - 4, 4) != 1 ||
                record < base || directory_end - 20 < record || directory_end - 20 - record < 56) return {};
            const auto offset = static_cast<std::size_t>(record - base);
            if (zip_catalog_le(tail, offset, 4) != 0x06064b50 ||
                zip_catalog_le(tail, offset + 4, 8) != directory_end - 20 - record - 12 ||
                zip_catalog_le(tail, offset + 16, 8)) return {};
            const ZipCatalogLayout extended{zip_catalog_le(tail, offset + 48, 8),
                zip_catalog_le(tail, offset + 40, 8), zip_catalog_le(tail, offset + 32, 8)};
            const auto extended_on_disk = zip_catalog_le(tail, offset + 24, 8);
            if ((on_disk != UINT16_MAX && on_disk != extended_on_disk) ||
                (result.entries != UINT16_MAX && result.entries != extended.entries) ||
                (result.bytes != UINT32_MAX && result.bytes != extended.bytes) ||
                (result.start != UINT32_MAX && result.start != extended.start)) return {};
            on_disk = extended_on_disk;
            result = extended;
            directory_end = record;
        } else if (result.entries == UINT16_MAX || result.start == UINT32_MAX || result.bytes == UINT32_MAX) {
            return {};
        }
        if (on_disk != result.entries || result.bytes > zip_catalog_max_directory ||
            result.entries > result.bytes / 46 || result.start > directory_end ||
            result.bytes != directory_end - result.start) return {};
        return result;
    }
    return {};
}

inline bool zip_catalog_expand_member(ZipCatalogMember& member, std::string_view extra) {
    if (member.size != UINT32_MAX && member.packed != UINT32_MAX && member.header != UINT32_MAX) return true;
    while (extra.size() >= 4) {
        const auto id = zip_catalog_le(extra, 0, 2);
        const auto length = zip_catalog_le(extra, 2, 2);
        extra.remove_prefix(4);
        if (length > extra.size()) return false;
        if (id == 1) {
            std::size_t at = 0;
            for (auto* value : {&member.size, &member.packed, &member.header}) {
                if (*value != UINT32_MAX) continue;
                if (length - at < 8) return false;
                *value = zip_catalog_le(extra, at, 8);
                at += 8;
            }
            return true;
        }
        extra.remove_prefix(static_cast<std::size_t>(length));
    }
    return false;
}

inline std::uint32_t zip_filename_crc(std::string_view name) {
    std::uint32_t crc = UINT32_MAX;
    for (unsigned char byte : name) {
        crc ^= byte;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320u : 0u);
    }
    return ~crc;
}

inline void zip_catalog_unicode_path(ZipCatalogMember& member, std::string_view extra) {
    bool conflict = false;
    while (extra.size() >= 4) {
        const auto id = zip_catalog_le(extra, 0, 2);
        const auto length = static_cast<std::size_t>(zip_catalog_le(extra, 2, 2));
        extra.remove_prefix(4);
        if (length > extra.size()) {
            if (id == 0x7075) member.bad_unicode_path = true;
            break;
        }
        if (id == 0x7075) {
            const auto path = extra.substr(0, length);
            if (path.size() <= 5 || path.size() > 4096 + 5 || path[0] != 1 ||
                zip_catalog_le(path, 1, 4) != zip_filename_crc(member.name)) {
                member.bad_unicode_path = true;
            } else if (const auto candidate = path.substr(5); member.unicode_path.empty()) {
                member.unicode_path = candidate;
            } else if (member.unicode_path != candidate) {
                conflict = true;
            }
        }
        extra.remove_prefix(length);
    }
    if (conflict) {
        member.unicode_path.clear();
        member.bad_unicode_path = true;
    }
}

inline std::optional<std::vector<ZipCatalogMember>> zip_catalog_members(
    std::string_view bytes, std::uint64_t start, std::uint64_t count) {
    if (bytes.size() > zip_catalog_max_directory || count > bytes.size() / 46) return {};
    std::vector<ZipCatalogMember> members;
    members.reserve(static_cast<std::size_t>(count));
    while (members.size() < count) {
        if (bytes.size() < 46 || zip_catalog_le(bytes, 0, 4) != 0x02014b50 ||
            zip_catalog_le(bytes, 34, 2) != 0) return {};
        const auto names = static_cast<std::size_t>(zip_catalog_le(bytes, 28, 2));
        const auto extra = static_cast<std::size_t>(zip_catalog_le(bytes, 30, 2));
        const auto comments = static_cast<std::size_t>(zip_catalog_le(bytes, 32, 2));
        const auto record_bytes = 46 + names + extra + comments;
        if (!names || names > 4096 || record_bytes > bytes.size()) return {};
        const auto name = bytes.substr(46, names);
        if (name.find('\0') != name.npos) return {};
        ZipCatalogMember member{std::string(name), zip_catalog_le(bytes, 20, 4), zip_catalog_le(bytes, 24, 4),
            zip_catalog_le(bytes, 42, 4), static_cast<std::uint32_t>(zip_catalog_le(bytes, 16, 4)),
            static_cast<std::uint16_t>(zip_catalog_le(bytes, 10, 2)), static_cast<std::uint16_t>(zip_catalog_le(bytes, 8, 2)), {}};
        if (!zip_catalog_expand_member(member, bytes.substr(46 + names, extra)) || member.header > start ||
            30 + names > start - member.header || member.packed > start - member.header - 30 - names) return {};
        zip_catalog_unicode_path(member, bytes.substr(46 + names, extra));
        members.push_back(std::move(member));
        bytes.remove_prefix(record_bytes);
    }
    if (!bytes.empty()) return {};
    return members;
}

inline std::optional<std::uint64_t> zip_catalog_payload(std::string_view local, std::string_view name,
    std::uint64_t offset, std::uint64_t boundary, std::uint64_t packed, std::uint64_t size,
    std::uint32_t crc, std::uint16_t method, std::uint16_t flags) {
    if (name.empty() || name.size() > 4096 || local.size() != 30 + name.size() || offset > boundary ||
        zip_catalog_le(local, 0, 4) != 0x04034b50 || zip_catalog_le(local, 6, 2) != flags ||
        zip_catalog_le(local, 8, 2) != method || zip_catalog_le(local, 26, 2) != name.size() ||
        local.substr(30) != name || (flags & 0x2061) || (method != 0 && method != 8) ||
        (method == 0 && packed != size)) return {};
    if (!(flags & 8)) {
        const auto local_packed = zip_catalog_le(local, 18, 4);
        const auto local_size = zip_catalog_le(local, 22, 4);
        if (zip_catalog_le(local, 14, 4) != crc || (local_packed != UINT32_MAX && local_packed != packed) ||
            (local_size != UINT32_MAX && local_size != size)) return {};
    }
    const auto prefix = local.size() + zip_catalog_le(local, 28, 2);
    if (prefix > boundary - offset || packed > boundary - offset - prefix) return {};
    return offset + prefix;
}
}
