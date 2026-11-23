#pragma once

// Native chart/prefetch adapter atop the same search publication/index queue.
// Chart snapshots and rank joins are committed in Publisher::run before events
// reach this frontend; output stream preserves the old foobar2000 protocol.
#include "catalog_search_frontend.hpp"
#include <functional>
#include <map>
#include <set>
#include <stdexcept>
#include <string>

namespace doujin::chart_frontend {
using Json = search_frontend::Json;

template<class Submit, class Emit, class ReadListing, class Artwork, class WriteArtwork>
search_frontend::Totals run(const search_frontend::Options& options,
         std::string period, Submit submit, Emit emit, ReadListing read_listing,
         Artwork schedule_artwork, WriteArtwork write_archive_art) {
    if (period != "month" && period != "all" && period != "prefetch")
        throw std::runtime_error("Invalid native chart frontend period");
    if (options.db.empty() || options.output.empty() || options.workers < 1 || options.workers > 3)
        throw std::runtime_error("Invalid native chart frontend paths");
    const bool prefetch = period == "prefetch";
    Json stages = Json::array();
    std::map<std::int64_t, Json> found;
    std::set<std::int64_t> scheduled_artwork;
    const auto reply = submit({{"operation", "catalog_chart_job"}, {"db", options.db},
                                {"period", period}, {"refresh", options.refresh},
                                {"stream_events", true}},
        std::function<void(const Json&)>([&](const Json& stage) {
            if (!stage.is_object() || stage.value("kind", "") != "chart_stage" ||
                stage.value("period", "") != period ||
                !stage.contains("source") || !stage.at("source").is_string() ||
                !stage.contains("ranks") || !stage.at("ranks").is_array() ||
                !stage.contains("releases") || !stage.at("releases").is_array() ||
                stage.at("releases").size() > 8192)
                throw std::runtime_error("Malformed committed native chart stage");
            if (!prefetch && stage.at("ranks").size() != stage.at("releases").size())
                throw std::runtime_error("Incomplete native chart ranking stage");
            std::map<std::int64_t, Json> fresh;
            std::string rank_ids;
            for (const auto& rank : stage.at("ranks")) {
                if (!rank.is_object() || !rank.contains("id") ||
                    !rank.at("id").is_number_integer() || rank.at("id") <= 0)
                    throw std::runtime_error("Invalid native chart rank");
                if (!rank_ids.empty()) rank_ids += '\t';
                rank_ids += std::to_string(rank.at("id").get<std::int64_t>());
            }
            for (const auto& row : stage.at("releases")) {
                if (!row.is_object() || !row.contains("id") ||
                    !row.at("id").is_number_integer() || row.at("id") <= 0 ||
                    !row.contains("_native_ui_date") || !row.at("_native_ui_date").is_string())
                    throw std::runtime_error("Invalid native chart release row");
                const auto id = row.at("id").get<std::int64_t>();
                if (!fresh.emplace(id, row).second)
                    throw std::runtime_error("Duplicate committed chart release");
            }
            if (!prefetch) {
                for (const auto& rank : stage.at("ranks"))
                    if (!fresh.contains(rank.at("id").get<std::int64_t>()))
                        throw std::runtime_error("Chart ranking absent from released rows");
                emit("\x15" + rank_ids);
            }
            found = std::move(fresh);
            for (const auto& [id, row] : found) {
                if (options.result_rows)
                    emit(search_frontend::record(row, search_frontend::text(row, "_native_ui_date")));
                if (!options.artwork.empty() && scheduled_artwork.insert(id).second)
                    schedule_artwork(id, {});
            }
            if (!options.artwork.empty() && stages.empty()) {
                const auto icon = search_frontend::text(stage, "site_icon");
                schedule_artwork(0, icon);
            }
            if (!prefetch) {
                const auto label = period == "month" ? "This Month" : "All Time";
                emit(std::string(label) + "  •  " + std::to_string(stage.at("ranks").size()) +
                     " ranked releases  •  " + stage.at("source").get<std::string>());
            }
            stages.push_back(stage);
        }));
    if (!reply.is_object() || !reply.contains("stages") ||
        reply.at("stages") != stages || !reply.contains("warnings") ||
        !reply.at("warnings").is_array())
        throw std::runtime_error("Invalid native chart job terminal response");
    for (const auto& warning : reply.at("warnings")) {
        if (!warning.is_string())
            throw std::runtime_error("Malformed native chart warning");
        emit(search_frontend::safe_text(warning.get_ref<const std::string&>(), 250));
    }
    if (found.empty())
        throw std::runtime_error("Native chart job returned no committed rankings");
    return search_frontend::run_index(options, found, submit, emit, read_listing, write_archive_art);
}

} // namespace doujin::chart_frontend
