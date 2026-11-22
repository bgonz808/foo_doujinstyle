#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace doujin {
struct ZipLocator {
    std::string raw_name;
    std::uint64_t local_header;
    std::uint32_t crc;
    bool operator==(const ZipLocator&) const = default;
};

struct Descriptor {
    std::string url;
    std::string referrer;
    std::string member;
    std::optional<ZipLocator> zip_locator;
    std::string filename;
    std::string release_id;
    std::map<std::string, std::string> metadata;
    std::map<std::string, std::vector<std::string>> multi_metadata;
    std::map<std::string, std::uint64_t> technical;
    std::map<std::string, std::string> technical_text;
    std::array<std::string, 4> catalog_sources;
    bool has_catalog_sources = false;
};

struct ContentRange {
    std::uint64_t first, last, size;
};

struct MegaLink {
    std::string handle;
    std::array<unsigned char, 32> key;
};
MegaLink parse_mega_link(std::string_view url);
ContentRange mega_range(std::string_view header, std::uint64_t size);
void validate_archive_url(std::string_view url);
std::string format_release_date(std::string_view value);
std::string release_event_from_tag(std::string_view value);
std::string catalog_number_from_tag(std::string_view value);
constexpr std::array catalog_number_tags{"CATALOGNUMBER", "CATALOG NUMBER", "CATALOGID"};
constexpr std::array catalog_source_names{"Track", "Release", "Lookup", "Album"};
std::vector<std::size_t> catalog_source_order(std::string_view order);

struct ZipMember {
    std::uint64_t offset;
    std::uint32_t packed_size, size, crc;
    std::uint16_t method;
};

using ZipReadAt = std::function<void(std::uint64_t, std::span<unsigned char>)>;
std::optional<ZipMember> find_zip_member(std::uint64_t size, std::string_view member, const ZipReadAt& read);
std::optional<ZipMember> find_zip_member(std::uint64_t size, const ZipLocator& locator, const ZipReadAt& read);
ZipLocator parse_zip_locator(std::string_view text);
std::string format_zip_locator(const ZipLocator& locator);

enum class ArtworkType { front, back, disc, artist };
std::vector<std::string> find_zip_artwork(std::uint64_t size, std::string_view track, std::string_view album,
                                         ArtworkType type, std::uint32_t limit, const ZipReadAt& read);
struct SingleMemberZip {
    std::string header, trailer;
};
SingleMemberZip make_single_member_zip(const ZipMember& entry, std::string_view name);

Descriptor parse_descriptor(std::string_view text);
std::string release_id_from_filename(std::string_view path);
std::string release_id_from_reference_path(std::string_view path);
std::string_view referrer_policy(std::string_view value);
std::string navigation_referrer(std::string_view source, std::string_view target, std::string_view policy = {});
ContentRange parse_content_range(std::string_view header);
void validate_url(std::string_view url);
std::string encode_hex(std::string_view text);
std::string decode_hex(std::string_view text, std::size_t max_bytes = 8192);
}
