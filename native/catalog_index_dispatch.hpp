#pragma once

// A single authoritative native foreground dispatch coordinator.
// The caller still owns archive network probes / UI projection until those
// implementations are migrated. Fetch workers MUST NOT touch SQLite or emit
// UI callbacks. An event is observable only after the corresponding commit.
#include "catalog_index_plan.hpp"
#include "catalog_offer_executor.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <set>
#include <stop_token>

namespace doujin::index_dispatch {
inline constexpr std::size_t offer_batch_size = 6;

// The stop token is checked between batches and before issuing each network
// request by offer_executor. A stopped job never dispatches further batches.
// Do not automatically retry a failed POST: its outcome may be unknowable.
template<class Fetch, class Report>
Json run(sqlite3* db, const Json& ids, int workers, std::int64_t now,
         Fetch fetch, Report report, std::stop_token stop = {}) {
    if (workers < 1 || workers > offer_executor::max_parallel)
        throw std::runtime_error("Invalid foreground indexing concurrency");
    // A missing ID must not silently produce a successful no-op. The catalog
    // planner accepts arbitrary positive IDs for read-only queries, whereas
    // dispatch is a user-visible write-capable job with a stronger contract.
    for (const auto& item : ids) {
        if (!item.is_number_integer() || item <= 0)
            throw std::runtime_error("Invalid foreground indexing release ID");
        auto present = query(db, "SELECT 1 FROM releases WHERE id=?", {item});
        if (!next(present.get())) throw std::runtime_error("Foreground indexing release not found");
    }
    auto plan = index_plan::select(db, ids, now, true);
    const auto pending = plan.at("offers");
    auto archives = plan.at("archives");
    std::set<std::int64_t> queued;
    for (const auto& job : archives) queued.insert(job.at("id").get<std::int64_t>());
    auto events = Json::array();
    bool rate_limited = false;
    for (std::size_t start = 0; start < pending.size() && !stop.stop_requested(); start += offer_batch_size) {
        auto group = Json::array();
        for (std::size_t at = start; at < std::min(start + offer_batch_size, pending.size()); ++at)
            group.push_back(Json(pending.at(at)));
        Json completed = offer_executor::run(db, group, workers, now, fetch,
            [&](const Json& event) {
                // Called synchronously by the committing thread only; an
                // exception stops the pool and propagates to the caller.
                report(event);
            }, stop);
        for (auto& event : completed.at("results")) events.push_back(std::move(event));
        rate_limited |= completed.at("rate_limited").get<bool>();
        if (stop.stop_requested() || completed.at("cancelled").get<bool>() || rate_limited) break;
        // Re-plan only the affected releases, not the whole catalog. A copy
        // can be shared between multiple releases, so deduplicate by copy ID.
        auto refreshed = index_plan::select(db, group, now, false);
        for (auto& archive : refreshed.at("archives")) {
            if (queued.insert(archive.at("id").get<std::int64_t>()).second)
                archives.push_back(std::move(archive));
        }
    }
    return Json{{"events", std::move(events)}, {"archives", std::move(archives)},
        {"offers_count", pending.size()}, {"initial_archives_count", plan.at("archives").size()},
        {"cancelled", stop.stop_requested()}, {"rate_limited", rate_limited}};
}
} // namespace doujin::index_dispatch
