#pragma once

#include "archive_job_plan.hpp"
#include "catalog_sqlite.hpp"
#include "third_party/json.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace doujin::index_plan {
using Json = nlohmann::json;
inline constexpr std::int64_t retry_ttl = 86400;
inline constexpr std::int64_t offer_ttl = 30 * 86400;
inline constexpr std::size_t max_ids = 512;

// Enforce the same URL rules as archive_download_job, rather than the
// legacy Python host suffix check. The source ZIP parser is not a security
// boundary and all candidate URLs must be revalidated here.
inline bool usable(const std::string& url, const std::string& host, const std::string& offer) {
    if (url.size() > 8192 || offer.size() > 65536) return false;
    const auto record = Json::parse(offer, nullptr, false);
    if (!record.is_object()) return false;
    if (host == "mediafire.com") {
        try { return mediafire_index_url(url, true) || mediafire_index_url(url, false); }
        catch (const std::exception&) { return false; }
    }
    if (host == "mega.nz") {
        try { (void)parse_mega_link(url); return true; }
        catch (const std::exception&) { return false; }
    }
    return false;
}

inline Json select(sqlite3* db, const Json& ids, std::int64_t now, bool include_offers) {
    if (!ids.is_array() || ids.empty() || ids.size() > max_ids || now < 0)
        throw std::runtime_error("Invalid catalog indexing job selection");
    std::set<std::int64_t> dedup;
    for (const auto& item : ids) {
        if (!item.is_number_integer()) throw std::runtime_error("Non-integer indexing release ID");
        const auto id = item.get<std::int64_t>();
        if (id <= 0 || !dedup.insert(id).second)
            throw std::runtime_error("Duplicate or invalid indexing release ID");
    }
    std::string placeholders;
    for (std::size_t i = 0; i < ids.size(); ++i) {
        if (i) placeholders += ',';
        placeholders += '?';
    }
    const auto bind_ids = [&](sqlite3_stmt* stmt, int first) {
        for (const auto& id : ids)
            check(db, sqlite3_bind_int64(stmt, first++, id.get<std::int64_t>()));
    };

    auto offers = Json::array();
    if (include_offers) {
        const auto sql = "SELECT id FROM releases WHERE (offer_checked IS NULL OR "
            "(offer_error IS NOT NULL AND offer_checked < ?) OR "
            "(offer_error IS NULL AND offer_checked < ?)) "
            "AND id IN (" + placeholders + ") "
            "ORDER BY offer_checked IS NOT NULL, offer_checked, id DESC LIMIT ?";
        const auto statement = prepare(db, sql.c_str());
        check(db, sqlite3_bind_int64(statement.get(), 1, now - retry_ttl));
        check(db, sqlite3_bind_int64(statement.get(), 2, now - offer_ttl));
        bind_ids(statement.get(), 3);
        check(db, sqlite3_bind_int64(statement.get(), static_cast<int>(ids.size()) + 3,
                                    static_cast<sqlite3_int64>(ids.size())));
        while (next(statement.get())) offers.push_back(sqlite3_column_int64(statement.get(), 0));
    }

    // Each ZIP's audio probe completeness is checked independently. Use a
    // guarded CASE so malformed stored JSON never crashes the entire job.
    const auto sql = std::string(R"SQL(
        SELECT c.id,c.share_url,c.host,c.offer FROM copies c
        WHERE (c.archive_checked IS NULL
            OR (c.archive_error IS NOT NULL AND c.archive_checked < ?)
            OR (c.archive_error IS NULL AND (
                c.archive_checked < ? OR CASE WHEN json_valid(c.archive) THEN
                   (json_extract(c.archive,'$.format')='zip' AND (
                       json_extract(c.archive,'$.header_probe_pass_complete') IS NOT 1
                       OR json_type(c.archive,'$.audio_probes') IS NULL
                       OR EXISTS (SELECT 1 FROM json_each(c.archive,'$.inventory') AS member
                           WHERE json_extract(member.value,'$.kind')='audio_candidate'
                             AND json_extract(member.value,'$.encrypted') IS NOT 1
                             AND json_extract(member.value,'$.zip_method') IN (0,8)
                             AND NOT EXISTS (SELECT 1 FROM json_each(c.archive,'$.audio_probes') AS probe
                                             WHERE probe.key=json_extract(member.value,'$.path')))
                   )) ELSE c.archive IS NOT NULL END)))
          AND c.host IN ('mediafire.com','mega.nz')
          AND EXISTS (SELECT 1 FROM release_copies rc
                      WHERE rc.copy_id=c.id AND rc.current=1 AND rc.release_id IN ()SQL") +
        placeholders + R"SQL())
        ORDER BY c.archive_checked IS NOT NULL,
                 (SELECT MAX(current) FROM release_copies WHERE copy_id=c.id) DESC,c.id DESC
    )SQL";
    const auto statement = prepare(db, sql.c_str());
    check(db, sqlite3_bind_int64(statement.get(), 1, now - retry_ttl));
    check(db, sqlite3_bind_int64(statement.get(), 2, now - offer_ttl));
    bind_ids(statement.get(), 3);
    auto archives = Json::array();
    while (next(statement.get())) {
        const auto id = sqlite3_column_int64(statement.get(), 0);
        const auto url = string_column(statement.get(), 1);
        const auto host = string_column(statement.get(), 2);
        const auto offer = string_column(statement.get(), 3);
        if (usable(url, host, offer)) archives.push_back({{"id", id}, {"share_url", url}});
    }
    return {{"offers", std::move(offers)}, {"archives", std::move(archives)}};
}
} // namespace doujin::index_plan
