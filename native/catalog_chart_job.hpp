#pragma once
// Native chart result projection: validate bounded chart stages, join committed
// catalog listings and provide UI dates without nested Python IPC requests.
#include "catalog_ingest.hpp"
#include "catalog_ui_date.hpp"
#include "site_discovery.hpp"
#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace doujin::chart_job {
using Json = nlohmann::ordered_json;
inline constexpr std::int64_t month_ttl = 6 * 3600;
inline constexpr std::int64_t all_ttl = 24 * 3600;
inline std::int64_t ttl(const std::string& period) {
    if (period == "month") return month_ttl;
    if (period == "all") return all_ttl;
    if (period == "prefetch") return month_ttl;
    throw std::runtime_error("Invalid native chart period");
}
inline bool valid_snapshot(const Json& snapshot) {
    if (!snapshot.is_object() || !snapshot.contains("charts") || !snapshot["charts"].is_object())
        return false;
    for (const auto* key : {"month", "all"})
        if (!snapshot["charts"].contains(key) || !snapshot["charts"][key].is_array() ||
            snapshot["charts"][key].empty() || snapshot["charts"][key].size() > 4096) return false;
    return true;
}
inline Json chart_listing(const Json& row) {
    if (!row.is_object() || !row.contains("id") || !row["id"].is_number_integer() ||
        row["id"].get<std::int64_t>() <= 0 || !row.contains("album") || !row["album"].is_string() ||
        !row.contains("chart_rank") || !row["chart_rank"].is_number_integer() ||
        row["chart_rank"].get<std::int64_t>() < 1 || !row.contains("chart_downloads") ||
        !row["chart_downloads"].is_number_integer() || row["chart_downloads"].get<std::int64_t>() < 0)
        throw std::runtime_error("Invalid persisted chart ranking");
    auto listing = Json::object();
    for (auto it = row.begin(); it != row.end(); ++it)
        if (!it.key().starts_with("chart_")) listing[it.key()] = it.value();
    listing["url"] = std::string("https://doujinstyle.com/?p=page&type=1&id=") +
        std::to_string(row["id"].get<std::int64_t>());
    return listing;
}
inline Json stage(sqlite3* db, const Json& snapshot, const std::string& period,
                  const std::string& source, bool persist_missing, std::int64_t now) {
    if (!valid_snapshot(snapshot)) throw std::runtime_error("Invalid chart snapshot");
    auto ranks = Json::array();
    std::vector<std::int64_t> order;
    std::map<std::int64_t, Json> fallbacks;
    std::map<std::int64_t, Json> selected_ranks;
    const bool prefetch = period == "prefetch";
    for (const auto* key : {"month", "all"}) {
        if (!prefetch && period != key) continue;
        std::set<std::int64_t> once;
        for (const auto& row : snapshot["charts"][key]) {
            const auto listing = chart_listing(row);
            const auto id = listing["id"].get<std::int64_t>();
            if (!once.insert(id).second) throw std::runtime_error("Duplicate chart rank identity");
            if (!fallbacks.contains(id)) order.push_back(id);
            fallbacks[id] = listing; // prefetch: all-time overrides month, matching Python dict union
            if (!prefetch) { ranks.push_back(row); selected_ranks[id] = row; }
        }
    }
    if (order.size() > 8192) throw std::runtime_error("Chart projection exceeds release bound");
    // All missing release seeds are committed as one unit before the streamed
    // stage. A crash cannot produce a partially seeded chart batch.
    std::unique_ptr<doujin::Transaction> write_transaction;
    if (persist_missing) write_transaction = std::make_unique<doujin::Transaction>(db);
    auto releases = Json::array();
    for (auto id : order) {
        auto listing = fallbacks.at(id);
        auto current = doujin::document(db, "SELECT listing FROM releases WHERE id=?", id);
        if (current.is_object()) {
            for (auto it = current.begin(); it != current.end(); ++it)
                listing[it.key()] = it.value();
        } else if (persist_missing) {
            doujin::save_listing(db, doujin::combine_listing(db, listing), now);
        }
        // UI rank belongs to chart projection, NOT permanent listing metadata.
        if (!prefetch) {
            const auto& rank = selected_ranks.at(id);
            listing["chart_rank"] = rank["chart_rank"];
            listing["chart_downloads"] = rank["chart_downloads"];
        }
        listing["_native_ui_date"] = doujin::ui_date::choose(listing)["date"];
        releases.push_back(std::move(listing));
    }
    if (write_transaction) write_transaction->commit();
    return Json{{"kind", "chart_stage"}, {"period", period}, {"source", source},
                {"ranks", std::move(ranks)}, {"releases", std::move(releases)},
                {"site_icon", snapshot.value("site_icon", Json())}};
}
} // namespace doujin::chart_job
