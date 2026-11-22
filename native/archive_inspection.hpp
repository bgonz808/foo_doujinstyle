#pragma once

// Shared archive inspection policy for MediaFire, MEGA, and the local test backend.
// One validated ZIP reader owns ranges, member classification, header probes, and
// cover selection. No provider-specific ZIP traversal and no Python callbacks.
#include "catalog_zip_range.hpp"
#include "zip_inventory.hpp"
#include "zip_name_resolution.hpp"
#include "audio_prefix.hpp"
#include "catalog_filename_hint.hpp"
#include "third_party/json.hpp"
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <stdexcept>

#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace doujin::archive_inspection {
using Json = nlohmann::ordered_json;

struct Options {
    bool headers = false;
    bool cover = false;
    unsigned member_requests = 12;
    std::size_t cover_limit = 96 * 1024;
};

inline std::string format(std::string_view magic) {
    if (magic.starts_with("Rar!\x1a\x07")) return "rar";
    if (magic.starts_with("7z\xbc\xaf\x27\x1c")) return "7z";
    if (magic.starts_with("\x1f\x8b")) return "gzip";
    if (magic.starts_with("PK\x03\x04")) return "zip_invalid_or_incomplete";
    return "unknown";
}

inline Json records(const std::vector<ZipCatalogMember>& members) {
    if (members.size() > 32768) throw std::runtime_error("Too many ZIP records");
    std::vector<zip_name::Evidence> evidence;
    evidence.reserve(members.size());
    for (const auto& row : members) evidence.push_back({row.name, row.flags, row.unicode_path, row.bad_unicode_path});
    const auto names = zip_name::resolve(evidence);
    Json result = Json::array();
    for (std::size_t index = 0; index < members.size(); ++index) {
        const auto& row = members[index];
        const auto& name = names[index];
        if (row.name.size() > 4096 || name.name.size() > 4096) throw std::runtime_error("Oversized ZIP filename");
        const auto hex_name = [&] {
            static constexpr char digits[] = "0123456789abcdef";
            std::string hex; hex.reserve(row.name.size() * 2);
            for (const unsigned char byte : row.name) {
                hex.push_back(digits[byte >> 4]);
                hex.push_back(digits[byte & 15]);
            }
            return hex;
        }();
        result.push_back({{"filename", name.name}, {"compress_size", row.packed}, {"file_size", row.size},
            {"CRC", row.crc}, {"header_offset", row.header}, {"compress_type", row.method},
            {"flag_bits", row.flags}, {"raw_name", hex_name}, {"encoding", name.encoding},
            {"name_warning", name.warning}, {"alternate_name", name.alternate}});
    }
    return result;
}

inline std::string catalog_number(std::string_view name, bool root = false) {
    return catalog_filename::hint(name, root);
}

inline void catalog_candidate(Json& evidence, std::string candidate, std::string source, std::string name) {
    if (candidate.empty()) return;
    std::transform(candidate.begin(), candidate.end(), candidate.begin(), [](unsigned char ch) {
        return static_cast<char>(std::toupper(ch));
    });
    if (!evidence.contains(candidate)) evidence[candidate] = Json::array();
    evidence[candidate].push_back({{"source", std::move(source)}, {"name", std::move(name)}});
}

inline std::string filename_from_disposition(std::string_view header) {
    // Untrusted response header: accept only a bounded filename parameter,
    // never treat it as a disk path or interpret unverified charsets.
    if (header.size() > 4096 || header.find_first_of("\r\n\0", 0, 3) != header.npos) return {};
    const auto lower = zip_inventory::ascii_lower(header);
    for (const auto parameter : {std::string_view("filename*=utf-8''"), std::string_view("filename=")}) {
        const auto at = lower.find(parameter);
        if (at == lower.npos) continue;
        auto value = header.substr(at + parameter.size());
        if (parameter == "filename=" && !value.empty() && value.front() == '"') {
            value.remove_prefix(1);
            value = value.substr(0, value.find('"'));
        } else value = value.substr(0, value.find(';'));
        std::string name;
        for (std::size_t i = 0; i < value.size() && name.size() <= 255; ++i) {
            unsigned char ch = static_cast<unsigned char>(value[i]);
            if (parameter != "filename=" && ch == '%' && i + 2 < value.size()) {
                const auto digit = [](char c) -> int { if (c >= '0' && c <= '9') return c-'0';
                    if (c >= 'a' && c <= 'f') return c-'a'+10;
                    if (c >= 'A' && c <= 'F') return c-'A'+10;
                    return -1; };
                const auto hi = digit(value[i+1]), lo = digit(value[i+2]);
                if (hi < 0 || lo < 0) return {};
                ch = static_cast<unsigned char>((hi << 4) | lo); i += 2;
            }
            if (ch < 32 || ch == 127 || ch == '/' || ch == '\\') return {};
            name.push_back(static_cast<char>(ch));
        }
        if (!name.empty() && name.size() <= 255 && zip_name::valid_utf8(name)) return name;
    }
    return {};
}

inline Json stats(const CatalogZipRange& reader) {
    return { {"requests", reader.requests}, {"body_bytes", reader.fetched},
             {"validator", reader.etag.empty() ? (reader.modified.empty() ? Json() : Json("last-modified")) : Json("etag")},
             {"etag", reader.etag.empty() ? Json() : Json(reader.etag)},
             {"last_modified", reader.modified.empty() ? Json() : Json(reader.modified)},
             {"directory_reader", "native"} };
}

// The caller owns the reader and can invoke this for a fixture or a remote
// provider. No SQLite transactions may remain open during this operation.
inline Json inspect(CatalogZipRange& reader, Options options = {}) {
    if (options.member_requests < 1 || options.member_requests > 64 || options.cover_limit > 96 * 1024)
        throw std::runtime_error("Invalid ZIP inspection limits");
    Json result{{"archive_bytes", reader.size}, {"format", "zip"}};
    const auto source = records(reader.rows);
    const auto projected = zip_inventory::project(source);
    for (const auto* key : {"members", "audio_members", "audio", "inventory", "member_types", "unknown_suffixes"})
        result[key] = projected.at(key);
    result["sidecar_candidates"] = Json::array();
    for (const auto& index : projected.at("sidecar_candidates"))
        result["sidecar_candidates"].push_back(source.at(index.get<std::size_t>()).at("filename"));
    result["sidecars"] = Json::array();
    result["catalog_id_candidates"] = Json::array();
    auto evidence = Json::object();
    for (const auto& root : projected.at("roots")) {
        auto name = root.get<std::string>();
        catalog_candidate(evidence, catalog_number(name, true), "zip_root", name);
    }
    // Lightweight sidecar reads happen only for explicit whole-directory
    // inspection, not the normal directory-only playback indexing path.
    if (options.headers) {
        result["artwork"] = projected.at("artwork");
        result["audio_probes"] = Json::object();
        bool complete = true;
        // Important: each probe receives a separate allowance, so the first
        // huge member cannot starve later tracks of network budget.
        for (const auto& index : projected.at("playable_indices")) {
            const auto at = index.get<std::size_t>();
            const auto& row = reader.rows.at(at);
            const auto& name = source.at(at).at("filename").get_ref<const std::string&>();
            if ((row.flags & 1) || (row.method != 0 && row.method != 8)) continue;
            reader.begin_probe(4096 + 128 * 1024, options.member_requests);
            try {
                const auto prefix = reader.member(row.header, 4096);
                const auto extension = zip_inventory::suffix(zip_inventory::path_slashes(name));
                result["audio_probes"][name] = catalog_audio_prefix(prefix, extension, row.size);
            } catch (const CatalogRangeChanged&) {
                throw;
            } catch (const std::runtime_error& error) {
                result["audio_probes"][name] = {{"prefix_status", error.what()}};
                complete = false;
            }
        }
        result["header_probe_pass_complete"] = complete;
    }
    if (options.cover) {
        result["artwork"] = projected.at("artwork");
        // Artwork candidate selection and payload extraction must remain in
        // the same bounded native context. Return binary as hex to the caller.
        struct Candidate { std::size_t depth, bytes, index; std::string name; };
        std::vector<Candidate> covers;
        for (std::size_t i = 0; i < source.size(); ++i) {
            const auto& item = source.at(i);
            const auto name = item.at("filename").get<std::string>();
            const auto ext = zip_inventory::suffix(zip_inventory::path_slashes(name));
            if (ext != ".jpg" && ext != ".jpeg" && ext != ".png") continue;
            const auto& row = reader.rows[i];
            if ((row.flags & 1) || (row.method != 0 && row.method != 8) || row.size > options.cover_limit) continue;
            const auto slash = name.find_last_of("/\\");
            const auto filename = name.substr(slash == name.npos ? 0 : slash + 1);
            const auto dot = filename.find_last_of('.');
            std::string stem = filename.substr(0, dot);
            std::erase_if(stem, [](unsigned char ch) { return ch == '_' || ch == '-' || ch == ' '; });
            std::transform(stem.begin(), stem.end(), stem.begin(), [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
            if (stem != "front" && stem != "frontcover" && stem != "cover" && stem != "folder" && stem != "jacket") continue;
            const auto depth = static_cast<std::size_t>(std::count(name.begin(), name.end(), '/'));
            covers.push_back({depth, static_cast<std::size_t>(row.size), i, name});
        }
        std::sort(covers.begin(), covers.end(), [](const auto& a, const auto& b) {
            return std::tie(a.depth, a.bytes, a.name) < std::tie(b.depth, b.bytes, b.name);
        });
        if (!covers.empty()) {
            const auto& target = covers.front();
            reader.begin_probe(128 * 1024, options.member_requests);
            try {
                const auto data = reader.member(reader.rows.at(target.index).header, options.cover_limit + 1);
                if (data.size() <= options.cover_limit && (data.starts_with("\xff\xd8\xff") || data.starts_with("\x89PNG\r\n\x1a\n"))) {
                    static constexpr char digits[] = "0123456789abcdef";
                    std::string hex; hex.reserve(data.size() * 2);
                    for (unsigned char byte : data) { hex.push_back(digits[byte >> 4]); hex.push_back(digits[byte & 15]); }
                    result["cached_front_cover"] = target.name;
                    result["cover_payload"] = std::move(hex);
                }
            } catch (const std::runtime_error&) {
                // Optional cover must never prevent audio index publication.
            }
        }
    }
    for (auto it = evidence.begin(); it != evidence.end(); ++it)
        result["catalog_id_candidates"].push_back(it.key());
    result["catalog_id_candidate_evidence"] = std::move(evidence);
    result["archive_http"] = stats(reader);
    return result;
}

} // namespace doujin::archive_inspection
