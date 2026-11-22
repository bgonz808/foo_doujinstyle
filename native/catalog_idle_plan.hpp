#pragma once

#include "catalog_index_plan.hpp"
#include "catalog_ingest.hpp"
#include <cstdint>
#include <stdexcept>
#include <string>

namespace doujin::idle_plan {
using Json = nlohmann::json;
inline constexpr int browse = 1, details = 2, links = 4, zip = 8, tags = 16, covers = 32;
inline constexpr int max_jobs = 6;

inline Json select(sqlite3* db, int layers) {
    if (layers < 1 || layers > 63) throw std::runtime_error("Invalid idle indexing layer mask");
    Json result{{"details", Json::array()}, {"links", Json::array()}, {"archives", Json::array()}};
    if (layers & details) {
        auto rows = doujin::prepare(db, "SELECT id FROM releases WHERE json_valid(listing) AND "
            "json_type(listing,'$.download_offer') IS NULL AND json_type(listing,'$.listed_at') IS NULL "
            "ORDER BY id LIMIT 6");
        while (doujin::next(rows.get())) result["details"].push_back(sqlite3_column_int64(rows.get(), 0));
    }
    if (layers & links) {
        auto rows = doujin::prepare(db, "SELECT id,listing FROM releases WHERE offer_checked IS NULL "
            "AND json_valid(listing) AND json_extract(listing,'$.download_offer')=1 "
            "AND json_array_length(listing,'$.download_forms')>0 ORDER BY id LIMIT 6");
        while (doujin::next(rows.get())) {
            auto text = doujin::string_column(rows.get(), 1);
            if (text.size() > 65536) continue;
            auto record = Json::parse(text, nullptr, false);
            if (!record.is_object()) continue;
            result["links"].push_back({{"id", sqlite3_column_int64(rows.get(), 0)}, {"listing", record}});
        }
    }
    if (!(layers & (zip | tags | covers))) return result;
    auto rows = doujin::prepare(db, R"SQL(
        SELECT c.id,c.share_url,c.host,c.offer,c.archive,c.archive_checked,
          CASE WHEN json_valid(c.archive) THEN
            (json_extract(c.archive,'$.format')='zip' AND (
                 json_extract(c.archive,'$.header_probe_pass_complete') IS NOT 1
                 OR json_type(c.archive,'$.audio_probes') IS NULL
                 OR EXISTS (SELECT 1 FROM json_each(c.archive,'$.inventory') member
                     WHERE json_extract(member.value,'$.kind')='audio_candidate'
                       AND json_extract(member.value,'$.encrypted') IS NOT 1
                       AND json_extract(member.value,'$.zip_method') IN (0,8)
                       AND NOT EXISTS (SELECT 1 FROM json_each(c.archive,'$.audio_probes') probe
                                       WHERE probe.key=json_extract(member.value,'$.path')))))
               ELSE 0 END AS needs_headers
        FROM copies c WHERE c.host IN ('mediafire.com','mega.nz')
          AND (c.archive_checked IS NULL OR c.archive_error IS NULL)
          AND EXISTS(SELECT 1 FROM release_copies rc WHERE rc.copy_id=c.id AND rc.current=1)
        ORDER BY c.id
    )SQL");
    while (doujin::next(rows.get()) && result["archives"].size() < max_jobs) {
        const auto id = sqlite3_column_int64(rows.get(), 0);
        const auto url = doujin::string_column(rows.get(), 1);
        const auto host = doujin::string_column(rows.get(), 2);
        const auto offer = doujin::string_column(rows.get(), 3);
        if (!index_plan::usable(url, host, offer)) continue;
        Json archive = nullptr;
        if (sqlite3_column_type(rows.get(), 4) != SQLITE_NULL) {
            const auto raw = doujin::string_column(rows.get(), 4);
            if (raw.size() > 2 * 1024 * 1024) continue;
            archive = Json::parse(raw, nullptr, false);
        }
        if (!archive.is_object()) archive = Json::object();
        const auto kind = archive.value("format", Json());
        const bool indexed = kind.is_string() && kind.get<std::string>() == "zip";
        const bool new_directory = (layers & zip) && sqlite3_column_type(rows.get(), 5) == SQLITE_NULL;
        const bool headers = (layers & tags) && (new_directory || (indexed && sqlite3_column_int(rows.get(), 6)));
        const bool cover = (layers & covers) && (indexed || new_directory) &&
            archive.value("cover_probe_attempted", Json()) != true &&
            archive.value("cached_front_cover", Json()) != true;
        if (!(new_directory || headers || cover)) continue;
        auto releases = doujin::prepare(db, "SELECT DISTINCT release_id FROM release_copies "
                                           "WHERE copy_id=? AND current=1 ORDER BY release_id");
        doujin::check(db, sqlite3_bind_int64(releases.get(), 1, id));
        auto ids = Json::array();
        while (doujin::next(releases.get())) ids.push_back(sqlite3_column_int64(releases.get(), 0));
        if (!ids.empty()) result["archives"].push_back({{"id", id}, {"share_url", url},
            {"ids", std::move(ids)}, {"headers", headers}, {"cover", cover}});
    }
    return result;
}
} // namespace doujin::idle_plan
