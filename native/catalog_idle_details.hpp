#pragma once
// Native idle detail coordinator: select -> paced fetch -> per-item SQLite
// commit -> one progress notification. No Python fetch or write fallback.
#include "catalog_idle_plan.hpp"
#include "catalog_ingest.hpp"
#include <chrono>
#include <cstdint>
#include <stdexcept>

namespace doujin::idle_detail {
using Json = nlohmann::json;

template<typename Fetch, typename Notify>
Json run(sqlite3* db, std::int64_t now, Fetch fetch, Notify notify) {
    // Reuse the same bounded eligibility policy as the link and archive stages.
    const auto ids = idle_plan::select(db, idle_plan::details).at("details");
    if (!ids.is_array() || ids.size() > idle_plan::max_jobs)
        throw std::runtime_error("Invalid native idle detail plan");
    auto committed = Json::array();
    for (const auto& selected : ids) {
        if (!selected.is_number_integer() || selected.get<std::int64_t>() <= 0)
            throw std::runtime_error("Invalid native idle detail release ID");
        const auto id = selected.get<std::int64_t>();
        const auto parsed = fetch(id); // Includes Windows input-idle pacing and HTTPS, outside transaction.
        if (!parsed.is_object() || !parsed.contains("row") || !parsed["row"].is_object() ||
            parsed["row"].value("id", std::int64_t(0)) != id ||
            !parsed.contains("forms") || !parsed["forms"].is_array() ||
            parsed["forms"].size() > 256)
            throw std::runtime_error("Invalid native idle detail response");
        auto observation = parsed.at("row");
        observation["download_forms"] = parsed.at("forms");
        {
            Transaction transaction(db);
            save_listing(db, combine_listing(db, observation), now);
            transaction.commit();
        }
        const auto event = Json{{"kind", "idle_detail"}, {"id", id}};
        notify(event); // Event observers may query a second connection to verify commit.
        committed.push_back(event);
    }
    return Json{{"events", std::move(committed)}};
}
} // namespace doujin::idle_detail
