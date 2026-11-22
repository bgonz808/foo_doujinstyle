#pragma once

// Provider-independent ZIP member projection. No HTTP, filesystem writes, or
// Python dependency: the native ZIP reader and the ZIP compatibility path
// feed the exact same bounded records to this classifier.
#include "third_party/json.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

namespace doujin::zip_inventory {
using Json = nlohmann::ordered_json;

inline std::string ascii_lower(std::string_view input) {
    std::string result(input);
    for (auto& character : result)
        if (character >= 'A' && character <= 'Z') character = static_cast<char>(character + ('a' - 'A'));
    return result;
}

inline std::string path_slashes(std::string_view name) {
    std::string path(name);
    std::replace(path.begin(), path.end(), '\\', '/');
    return path;
}

inline std::vector<std::string> path_parts(std::string_view path) {
    std::vector<std::string> parts;
    for (std::size_t at = 0; at < path.size();) {
        const auto end = path.find('/', at);
        const auto count = (end == path.npos ? path.size() : end) - at;
        if (count && path.substr(at, count) != ".") parts.emplace_back(path.substr(at, count));
        if (end == path.npos) break;
        at = end + 1;
    }
    return parts;
}

inline bool sidecar(const std::vector<std::string>& parts) {
    if (parts.empty()) return false;
    if (parts.back().starts_with("._")) return true;
    return std::any_of(parts.begin(), parts.end(), [](const std::string& part) {
        return ascii_lower(part) == "__macosx";
    });
}

inline std::string suffix(const std::string& filename) {
    const auto slash = filename.rfind('/');
    const auto start = slash == filename.npos ? 0 : slash + 1;
    const auto dot = filename.rfind('.');
    if (dot == filename.npos || dot <= start || dot + 1 == filename.size()) return {};
    return ascii_lower(std::string_view(filename).substr(dot));
}

inline std::string kind(std::string_view ext, bool is_sidecar) {
    if (is_sidecar) return "archive_metadata";
    constexpr std::array audio = {
        ".aac", ".ac3", ".adx", ".aif", ".aiff", ".alac", ".ape", ".dff", ".dsf",
        ".flac", ".m4a", ".mka", ".mod", ".mp2", ".mp3", ".mp4", ".mpc", ".nsf",
        ".oga", ".ogg", ".opus", ".spc", ".tak", ".tta", ".vgm", ".vgz", ".wav",
        ".wma", ".wv", ".xma"};
    constexpr std::array art = {
        ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"};
    constexpr std::array metadata = {
        ".cue", ".json", ".log", ".md", ".nfo", ".sfv", ".txt", ".xml"};
    if (std::find(audio.begin(), audio.end(), ext) != audio.end()) return "audio_candidate";
    if (std::find(art.begin(), art.end(), ext) != art.end()) return "artwork";
    if (std::find(metadata.begin(), metadata.end(), ext) != metadata.end()) return "metadata";
    if (ext == ".m3u" || ext == ".m3u8" || ext == ".pls") return "playlist";
    if (ext == ".7z" || ext == ".gz" || ext == ".rar" || ext == ".tar" || ext == ".zip") return "archive";
    if (ext == ".pdf") return "document";
    return "unknown";
}

inline std::uint64_t nonnegative_integer(const Json& row, const char* field,
                                         std::uint64_t upper = std::numeric_limits<std::uint64_t>::max()) {
    const auto& item = row.at(field);
    if (!item.is_number_integer() || (item.is_number_integer() && !item.is_number_unsigned() &&
                                      item.get<std::int64_t>() < 0))
        throw std::runtime_error(std::string("Invalid ZIP numeric field: ") + field);
    const auto result = item.get<std::uint64_t>();
    if (result > upper) throw std::runtime_error(std::string("ZIP numeric field exceeds limit: ") + field);
    return result;
}

inline Json project(const Json& members) {
    if (!members.is_array() || members.size() > 32768)
        throw std::runtime_error("Invalid ZIP inventory size");
    Json inventory = Json::array(), audio = Json::array(), playable = Json::array();
    Json entries = Json::array(), art = Json::array();
    std::map<std::string, std::uint64_t> types, unknown;
    std::set<std::string> roots;
    std::vector<std::tuple<int, std::size_t, std::string, std::size_t>> notes;
    std::size_t total_name_bytes = 0;
    for (std::size_t i = 0; i < members.size(); ++i) {
        const auto& member = members[i];
        const auto& name = member.at("filename").get_ref<const std::string&>();
        if (name.size() > 4096 || name.size() > 2 * 1024 * 1024 - total_name_bytes)
            throw std::runtime_error("ZIP member names exceed directory budget");
        total_name_bytes += name.size();
        const auto path = path_slashes(name);
        const auto parts = path_parts(path);
        const bool is_dir = name.ends_with('/');
        if (!parts.empty() && (is_dir || parts.size() > 1)) roots.insert(parts[0]);
        if (is_dir) continue;
        entries.push_back(i);
        const bool junk = sidecar(parts);
        const auto ext = suffix(path);
        const auto classification = kind(ext, junk);
        const auto packed = nonnegative_integer(member, "compress_size");
        const auto size = nonnegative_integer(member, "file_size");
        const auto method = nonnegative_integer(member, "compress_type", 65535);
        const auto flags = nonnegative_integer(member, "flag_bits", 65535);
        const auto offset = nonnegative_integer(member, "header_offset");
        const auto crc = nonnegative_integer(member, "CRC", UINT32_MAX);
        char crc_buffer[9];
        std::snprintf(crc_buffer, sizeof(crc_buffer), "%08x", static_cast<unsigned>(crc));
        Json record = {{"path", name}, {"suffix", ext.empty() ? Json(nullptr) : Json(ext)},
                       {"kind", classification}, {"compressed_bytes", packed},
                       {"uncompressed_bytes", size}, {"zip_method", method},
                       {"zip_header_offset", offset}, {"crc32", crc_buffer},
                       {"encrypted", (flags & 1u) != 0}};
        const auto raw = member.value("raw_name", std::string());
        if (raw.size() > 8192 || member.value("encoding", std::string()).size() > 64 ||
            member.value("alternate_name", std::string()).size() > 4096)
            throw std::runtime_error("ZIP filename evidence exceeds size limit");
        const bool warning = member.value("name_warning", false);
        if (!raw.empty() && (raw != [&] {
                static constexpr char digits[] = "0123456789abcdef";
                std::string hex; hex.reserve(name.size() * 2);
                for (unsigned char byte : name) { hex.push_back(digits[byte >> 4]); hex.push_back(digits[byte & 15]); }
                return hex;
            }() || warning)) {
            const auto encoding = member.value("encoding", std::string());
            Json zip_name = {{"raw", raw}, {"encoding", encoding}};
            if (warning) zip_name["warning"] = "Invalid or conflicting ZIP Unicode filename evidence";
            const auto alternate = member.value("alternate_name", std::string());
            if (!alternate.empty()) zip_name["legacy_unicode_path"] = alternate;
            record["zip_name"] = std::move(zip_name);
        }
        inventory.push_back(record);
        ++types[classification];
        if (classification == "unknown") ++unknown[ext.empty() ? "(none)" : ext];
        if (junk) continue;
        if (classification == "audio_candidate") {
            playable.push_back(i);
            if (audio.size() < 100) audio.push_back({{"path", name}, {"compressed_bytes", packed},
                {"uncompressed_bytes", size}, {"zip_method", method}, {"encrypted", (flags & 1u) != 0}});
        }
        if (classification == "artwork") art.push_back(record);
        const int priority = ext == ".nfo" ? 0 : ext == ".cue" ? 1 : ext == ".txt" ? 2 : -1;
        if (priority >= 0 && (ext != ".txt" || [&] {
                const auto stem = ascii_lower(parts.empty() ? name : parts.back());
                return stem.find("readme") != stem.npos || stem.find("info") != stem.npos ||
                    stem.find("release") != stem.npos || stem.find("tracklist") != stem.npos ||
                    stem.find("credit") != stem.npos;
            }())) notes.emplace_back(priority, parts.size(), name, i);
    }
    std::sort(notes.begin(), notes.end());
    Json selected = Json::array(), root_names = Json::array(), types_json = Json::object(), unknown_json = Json::object();
    for (std::size_t i = 0; i < std::min<std::size_t>(3, notes.size()); ++i)
        selected.push_back(std::get<3>(notes[i]));
    for (const auto& root : roots) root_names.push_back(root);
    for (const auto& [key, count] : types) types_json[key] = count;
    for (const auto& [key, count] : unknown) unknown_json[key] = count;
    Json report = {{"members", entries.size()}, {"audio_members", playable.size()}, {"audio", std::move(audio)},
            {"inventory", std::move(inventory)}, {"member_types", std::move(types_json)},
            {"unknown_suffixes", std::move(unknown_json)}, {"sidecar_candidates", std::move(selected)},
            {"artwork", std::move(art)}, {"roots", std::move(root_names)},
            {"entry_indices", std::move(entries)}, {"playable_indices", std::move(playable)}};
    if (report.dump().size() > 16 * 1024 * 1024)
        throw std::runtime_error("ZIP inventory response exceeds size limit");
    return report;
}
} // namespace doujin::zip_inventory
