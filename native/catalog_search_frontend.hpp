#pragma once

// Native foreground SearchDialog coordinator. Heavy HTTP/ZIP/SQLite operations
// already belong to Publisher; this code replaces Python's *post-commit*
// event adaptation / reference publication / progress framing without adding
// network requests inside a SQLite writer callback.
#include "third_party/json.hpp"
#include <algorithm>
#include <cstdint>
#include <functional>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace doujin::search_frontend {
using Json = nlohmann::ordered_json;

struct Options {
    std::string db;
    std::string output;
    std::string root;
    std::string artwork;
    std::string host_zlib;
    std::string query;
    int pages = 1;
    int workers = 3;
    bool refresh = false;
    bool result_rows = true;
};

struct Totals {
    std::size_t listings = 0;
    std::size_t archives_attempted = 0;
    std::size_t archives_indexed = 0;
    std::uint64_t archive_body_bytes = 0;
    std::size_t references_written = 0;
    std::size_t releases_with_tracks = 0;
};

inline std::string safe_text(std::string_view raw, std::size_t max_bytes = 512) {
    std::string result;
    result.reserve(std::min(max_bytes, raw.size()));
    bool pending_space = false;
    for (unsigned char byte : raw) {
        if (byte <= 32 || byte == 127) {
            pending_space = !result.empty();
            continue;
        }
        if (pending_space && result.size() < max_bytes) result += ' ';
        pending_space = false;
        if (result.size() >= max_bytes) break;
        result += static_cast<char>(byte);
    }
    // Never split a multibyte UTF-8 character at the output boundary.
    if (result.size() == max_bytes && max_bytes < raw.size()) {
        while (!result.empty() && (static_cast<unsigned char>(result.back()) & 0xc0) == 0x80)
            result.pop_back();
        if (!result.empty() && static_cast<unsigned char>(result.back()) >= 0xc2)
            result.pop_back();
    }
    return result;
}

inline std::string printable_size(std::uint64_t bytes) {
    if (bytes < 1024) return std::to_string(bytes) + " B";
    constexpr const char* units[]{"KiB", "MiB", "GiB", "TiB"};
    double number = static_cast<double>(bytes);
    std::size_t scale = 0;
    do { number /= 1024.0; ++scale; } while (number >= 1024.0 && scale < 4);
    std::ostringstream output;
    output << std::fixed << std::setprecision(2) << number << ' ' << units[scale - 1];
    return output.str();
}

inline std::string text(const Json& row, std::string_view key) {
    const auto it = row.find(std::string(key));
    return it != row.end() && it->is_string() ? it->get<std::string>() : std::string{};
}

inline std::string record(const Json& release, std::string_view date = {}) {
    const auto id = release.at("id").get<std::int64_t>();
    if (id <= 0) throw std::runtime_error("Invalid native release ID");
    const auto album = safe_text(text(release, "album"));
    if (album.empty()) throw std::runtime_error("Empty native release title");
    std::vector<std::string> tags;
    if (auto field = release.find("tags"); field != release.end() && field->is_array()) {
        if (field->size() > 256) throw std::runtime_error("Too many native release tags");
        for (const auto& value : *field) {
            if (!value.is_string()) throw std::runtime_error("Malformed native release tag");
            tags.push_back(safe_text(value.get_ref<const std::string&>(), 120));
        }
    }
    std::string joined, facets;
    for (const auto& tag : tags) {
        if (!joined.empty()) joined += ", ";
        joined += tag;
        if (!facets.empty()) facets += '\x1f';
        facets += tag;
    }
    const auto& technical = release.value("technical", Json::object());
    const auto format = technical.is_object() ? safe_text(text(technical, "advertised_format")) : "";
    return "\x1e" + std::to_string(id) + "\t" + album + "\t" +
           safe_text(text(release, "artist")) + "\t" + format + "\t" +
           safe_text(joined) + "\t" + safe_text(date, 128) + "\t\t" + facets;
}

template<class Submit, class Emit, class ReadListing, class Artwork, class WriteArtwork>
Totals run(const Options& options, Submit submit, Emit emit, ReadListing read_listing,
           Artwork schedule_artwork, WriteArtwork write_archive_art) {
    if (options.db.empty() || options.output.empty() || options.query.empty() ||
        options.query.size() > 512 || options.pages < 1 || options.pages > 3 ||
        options.workers < 1 || options.workers > 3)
        throw std::runtime_error("Invalid native foreground search configuration");
    std::map<std::int64_t, Json> found;
    auto streamed = Json::array();
    auto pending_artwork = std::set<std::int64_t>{};
    auto request = [&](Json value, const std::function<void(const Json&)>& committed = {}) {
        value["db"] = options.db;
        return submit(value, committed);
    };

    // The native search job persists each page before its event is emitted.
    const auto search = request({{"operation", "site_search_job"},
            {"query", options.query}, {"pages", options.pages},
            {"refresh", options.refresh}, {"stream_events", true}},
        [&](const Json& event) {
            if (!event.is_object() || event.value("kind", "") != "search_page" ||
                !event.contains("page") || !event.at("page").is_number_integer() ||
                event.at("page") != static_cast<int>(streamed.size()) + 1 ||
                streamed.size() >= static_cast<std::size_t>(options.pages) ||
                !event.contains("batch") || !event.at("batch").is_object() ||
                !event.contains("ui_dates") || !event.at("ui_dates").is_object())
                throw std::runtime_error("Invalid committed native search page event");
            const auto& batch = event.at("batch");
            if (!batch.contains("releases") || !batch.at("releases").is_array() ||
                batch.at("releases").size() > 20 || !batch.contains("rows_on_page") ||
                !batch.at("rows_on_page").is_number_integer() ||
                batch.at("rows_on_page") < 0 || batch.at("rows_on_page") > 20 ||
                !batch.contains("cache_hit") || !batch.at("cache_hit").is_boolean() ||
                !batch.contains("more") || !batch.at("more").is_boolean())
                throw std::runtime_error("Invalid committed native search page payload");
            const auto& total = batch.value("total_results", Json());
            if (!total.is_null() && (!total.is_number_integer() || total < 0))
                throw std::runtime_error("Invalid native search result count");
            auto header = "Page " + std::to_string(streamed.size() + 1) +
                "  •  " + std::to_string(batch.at("rows_on_page").template get<int>()) + " results";
            if (!total.is_null()) header += " of " + total.dump() + " matches";
            header += std::string("  •  ") + (batch.at("cache_hit") == true ? "cached" : "saved") +
                " by native catalog";
            emit(header);
            if (streamed.empty() && !options.artwork.empty())
                schedule_artwork(0, batch.value("site_icon", std::string{}));
            for (const auto& observed : batch.at("releases")) {
                if (!observed.is_object() || !observed.contains("id") ||
                    !observed.at("id").is_number_integer())
                    throw std::runtime_error("Invalid native search release");
                const auto id = observed.at("id").template get<std::int64_t>();
                if (id <= 0 || !found.emplace(id, observed).second ||
                    !event.at("ui_dates").contains(std::to_string(id)) ||
                    !event.at("ui_dates").at(std::to_string(id)).is_string())
                    throw std::runtime_error("Duplicate native search ID or invalid date");
                const auto merged = read_listing(id);
                const auto& row = merged.is_object() ? merged : observed;
                if (options.result_rows) emit(record(row, event.at("ui_dates").at(std::to_string(id)).template get<std::string>()));
                else emit("  " + std::to_string(id) + "   " + safe_text(text(row, "album")));
                if (!options.artwork.empty() && pending_artwork.insert(id).second)
                    schedule_artwork(id, {});
            }
            if (event.at("ui_dates").size() != batch.at("releases").size())
                throw std::runtime_error("Unexpected native search date mapping");
            streamed.push_back(batch);
        });
    if (!search.is_object() || !search.contains("pages") || search.at("pages") != streamed ||
        !search.contains("late_error") || !search.at("late_error").is_string() ||
        streamed.empty())
        throw std::runtime_error("Incomplete committed native search result");
    if (!search.at("late_error").template get_ref<const std::string&>().empty()) {
        emit("Results page " + std::to_string(streamed.size() + 1) + " unavailable (" +
             safe_text(search.at("late_error").template get_ref<const std::string&>(), 200) +
             "). Continuing with saved results.");
    }
    Totals totals;
    totals.listings = found.size();
    if (found.empty()) return totals;

    Json ids = Json::array();
    for (const auto& [id, value] : found) { (void)value; ids.push_back(id); }
    std::set<std::int64_t> published_ids;
    std::set<std::string> reported_refs;
    auto publish = [&] {
        const auto result = request({{"operation", "publish_many"}, {"ids", ids},
            {"output", options.output}, {"root", options.root}, {"copy_id", nullptr}});
        if (!result.is_object() || !result.contains("notifications") ||
            !result.at("notifications").is_array() || !result.contains("references") ||
            !result.at("references").is_number_integer() ||
            result.at("references") != result.at("notifications").size() ||
            !result.contains("written") || !result.at("written").is_number_integer() ||
            result.at("written") < 0)
            throw std::runtime_error("Incomplete native batch publication");
        for (const auto& notice : result.at("notifications")) {
            if (!notice.is_array() || notice.size() != 3 ||
                !notice.at(0).is_number_integer() || !notice.at(1).is_string() ||
                !notice.at(2).is_boolean() ||
                !found.contains(notice.at(0).template get<std::int64_t>()))
                throw std::runtime_error("Invalid native publication identity");
            const auto id = notice.at(0).template get<std::int64_t>();
            const auto& filename = notice.at(1).template get_ref<const std::string&>();
            if (filename.empty() || filename.find_first_of("\n\r\t") != filename.npos ||
                filename.size() > 1024)
                throw std::runtime_error("Invalid publication filename");
            published_ids.insert(id);
            if (options.result_rows && reported_refs.insert(filename).second)
                emit("\x1c" + std::to_string(id) + "\t" + filename);
            if (options.result_rows && notice.at(2) == true)
                emit("\x19" + std::to_string(id) + "\t" + filename);
        }
        totals.references_written += result.at("written").template get<std::size_t>();
        return result.at("references").template get<std::size_t>();
    };

    const auto already = publish();
    if (already)
        emit("Already indexed: " + std::to_string(already) + " track references (" +
             std::to_string(totals.references_written) + " written).");
    auto offered = Json::array();
    std::set<std::int64_t> offers_seen;
    const auto dispatch = request({{"operation", "catalog_index_dispatch"},
          {"ids", ids}, {"workers", options.workers}, {"stream_events", true}},
        [&](const Json& event) {
            if (!event.is_object() || !event.contains("id") ||
                !event.at("id").is_number_integer() ||
                !found.contains(event.at("id").template get<std::int64_t>()) ||
                !offers_seen.insert(event.at("id").template get<std::int64_t>()).second ||
                !event.contains("offers") || !event.at("offers").is_array() ||
                !event.contains("statuses") || !event.at("statuses").is_array() ||
                event.at("offers").size() != event.at("statuses").size() ||
                !event.contains("had_observation") || !event.at("had_observation").is_boolean())
                throw std::runtime_error("Malformed native committed offer event");
            offered.push_back(event);
            emit("  " + event.at("id").dump() + "   Offer response committed");
        });
    if (!dispatch.is_object() || dispatch.value("cancelled", true) ||
        !dispatch.contains("events") || dispatch.at("events") != offered ||
        !dispatch.contains("archives") || !dispatch.at("archives").is_array() ||
        !dispatch.contains("offers_count") || !dispatch.at("offers_count").is_number_integer() ||
        !dispatch.contains("initial_archives_count") ||
        !dispatch.at("initial_archives_count").is_number_integer() ||
        dispatch.at("offers_count") < offered.size() ||
        (!dispatch.value("rate_limited", false) && dispatch.at("offers_count") != offered.size()))
        throw std::runtime_error("Incomplete native offer dispatch terminal response");
    const auto offered_count = dispatch.at("offers_count").template get<std::size_t>();
    const auto archive_count = dispatch.at("initial_archives_count").template get<std::size_t>();
    if (offered_count || !dispatch.at("archives").empty()) {
        emit("Indexing " + std::to_string(offered_count) + " unresolved releases and " +
             std::to_string(archive_count) + " known archives.");
        for (const auto& event : offered) {
            const auto id = event.at("id").template get<std::int64_t>();
            if (options.result_rows && event.at("had_observation") == true) {
                const auto row = read_listing(id);
                if (!row.is_object()) throw std::runtime_error("Missing native committed listing");
                emit(record(row));
            }
            for (const auto& status : event.at("statuses")) {
                if (!status.is_string()) throw std::runtime_error("Malformed native offer status");
                emit("  " + std::to_string(id) + "   " +
                     safe_text(status.template get_ref<const std::string&>(), 256));
            }
            if (event.at("offers").empty() && event.value("error", Json()).is_string())
                emit("  " + std::to_string(id) + "   Offer unavailable (" +
                     safe_text(event.at("error").template get_ref<const std::string&>(), 200) + ")");
        }
    }
    if (dispatch.value("rate_limited", false))
        throw std::runtime_error("DoujinStyle HTTP 429: stopped new offer requests");

    if (!dispatch.at("archives").empty()) {
        std::set<std::int64_t> archive_seen;
        auto streamed_archives = Json::array();
        const auto complete = request({{"operation", "catalog_archive_dispatch"},
             {"jobs", dispatch.at("archives")}, {"ids", ids}, {"cover", !options.artwork.empty()},
             {"workers", options.workers}, {"host_zlib", options.host_zlib},
             {"stream_events", true}},
            [&](const Json& event) {
                if (!event.is_object() || !event.contains("id") ||
                    !event.at("id").is_number_integer() ||
                    !archive_seen.insert(event.at("id").template get<std::int64_t>()).second ||
                    !event.contains("ids") || !event.at("ids").is_array() ||
                    event.at("ids").empty() || !event.contains("ok") ||
                    !event.at("ok").is_boolean())
                    throw std::runtime_error("Malformed native archive completion");
                std::string labels;
                for (const auto& id : event.at("ids")) {
                    if (!id.is_number_integer() || !found.contains(id.template get<std::int64_t>()))
                        throw std::runtime_error("Invalid archive release association");
                    if (!labels.empty()) labels += ", ";
                    labels += id.dump();
                }
                ++totals.archives_attempted;
                if (event.at("ok") == true) {
                    if (!event.contains("archive_http") || !event.at("archive_http").is_object() ||
                        !event.at("archive_http").contains("body_bytes") ||
                        !event.at("archive_http").at("body_bytes").is_number_integer() ||
                        event.at("archive_http").at("body_bytes") < 0)
                        throw std::runtime_error("Missing native archive HTTP accounting");
                    const auto bytes = event.at("archive_http").at("body_bytes").template get<std::uint64_t>();
                    totals.archive_body_bytes += bytes;
                    ++totals.archives_indexed;
                    const auto format = event.at("format").template get<std::string>();
                    if (format == "zip") {
                        const auto members = event.at("audio_members").template get<std::int64_t>();
                        if (members < 0) throw std::runtime_error("Invalid archive member count");
                        emit("  " + labels + "   native archive committed  •  " +
                             std::to_string(members) + " audio members  •  " +
                             printable_size(bytes) + " read");
                    } else {
                        emit("  " + labels + "   " + safe_text(format, 24) + " archive  •  " +
                             printable_size(bytes) + " read");
                    }
                    if (!options.artwork.empty() && event.value("cover_payload", Json()).is_string())
                        for (const auto& id : event.at("ids"))
                            write_archive_art(id.template get<std::int64_t>(), event.at("cover_payload").template get<std::string>());
                } else {
                    if (!event.value("error", Json()).is_string())
                        throw std::runtime_error("Missing native archive error");
                    emit("  " + labels + "   Archive unavailable (" +
                         safe_text(event.at("error").template get_ref<const std::string&>(), 200) + ")");
                }
                auto compact = event;
                compact.erase("cover_payload");
                streamed_archives.push_back(std::move(compact));
            });
        if (!complete.is_object() || complete.value("cancelled", true) ||
            complete.value("rate_limited", false) ||
            complete.at("events") != streamed_archives ||
            complete.at("attempted") != streamed_archives.size() ||
            complete.at("selected") != dispatch.at("archives").size())
            throw std::runtime_error("Incomplete native archive dispatch");
    }
    (void)publish();
    totals.releases_with_tracks = published_ids.size();
    return totals;
}

} // namespace doujin::search_frontend
