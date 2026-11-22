#pragma once

#include "catalog_ingest.hpp"
#include "site_offers.hpp"
#include "catalog_ui_date.hpp"

namespace doujin::discovery {
inline constexpr sqlite3_int64 listing_ttl = 600;
inline constexpr std::size_t listing_limit = 20;
inline constexpr std::size_t search_page_limit = 3;

struct Snapshot {
    Json page;
    sqlite3_int64 fetched = 0;
};

inline Snapshot snapshot(sqlite3* db, const std::string& url) {
    auto saved = query(db, "SELECT snapshot,fetched FROM pages WHERE url=?", {url});
    if (!next(saved.get())) return {};
    return {Json::parse(string_column(saved.get(), 0)), sqlite3_column_int64(saved.get(), 1)};
}

template<typename Fetch>
Json search_page(sqlite3* db, const std::string& url, sqlite3_int64 now, bool refresh, Fetch fetch) {
    if (!site_url(url) || parameter(url, "p") != "search")
        throw std::runtime_error("Unexpected DoujinStyle search URL");
    if (!refresh) {
        auto saved = snapshot(db, url);
        if (saved.page.is_object() && saved.page.contains("releases") && saved.fetched >= now - listing_ttl)
            return {{"snapshot", std::move(saved.page)}, {"cache_hit", true}};
    }
    auto page = listing(fetch(url), listing_limit, true);
    Transaction transaction(db);
    save_page(db, {{"url", url}, {"page", page}, {"now", now}, {"checkpoint", nullptr}});
    transaction.commit();
    return {{"snapshot", std::move(page)}, {"cache_hit", false}};
}

template<typename Fetch>
Json search_job_page(sqlite3* db, sqlite3_int64 now, const Json& request, Fetch fetch) {
    const auto& number = request.at("page");
    const auto& seen = request.at("seen");
    const auto& kind = request.at("kind").get_ref<const std::string&>();
    if (!number.is_number_integer() || number < 1 || number > search_page_limit || !seen.is_array() ||
        seen.size() > search_page_limit * listing_limit ||
        (kind != "blanket" && kind != "tags"))
        throw std::runtime_error("Invalid DoujinStyle search page request");
    const auto page = number.get<std::size_t>();
    std::set<std::int64_t> ids;
    for (const auto& id : seen) {
        if (!id.is_number_integer() || id < 0 || id > std::numeric_limits<std::int64_t>::max())
            throw std::runtime_error("Invalid release ID in DoujinStyle search page request");
        ids.insert(id.get<std::int64_t>());
    }
    const auto& query = request.at("query").get_ref<const std::string&>();
    const auto url = std::string(origin) + "/?p=search&source=1&type=" + kind +
        "&result=" + percent_encode(query, {}, true) + "&page=" + std::to_string(page - 1);
    Json response = search_page(db, url, now, request.value("refresh", false), fetch);
    auto result = std::move(response.at("snapshot"));
    auto releases = Json::array();
    std::size_t examined = 0;
    for (auto& row : result.at("releases")) {
        if (examined++ == listing_limit) break;
        if (!ids.insert(row.at("id").get<std::int64_t>()).second) continue;
        row.erase("download_form");
        row.erase("download_forms");
        releases.push_back(std::move(row));
    }
    result["releases"] = std::move(releases);
    result["cache_hit"] = response.at("cache_hit");
    const auto& total = result.at("total_results");
    result["more"] = result.at("rows_on_page").get<std::size_t>() != 0 &&
        (total.is_null() || total > page * listing_limit);
    return result;
}

// One native foreground search transaction at a time. Each page is committed
// before it becomes visible to the coordinator; a later-page failure returns
// the earlier pages with their deduplicated release identities intact.
// The helper owns URL construction, pagination, and stop/continue decisions.
template<typename Fetch, typename Report>
Json search_job(sqlite3* db, sqlite3_int64 now, const Json& request, Fetch fetch, Report report) {
    const auto& original = request.at("query").get_ref<const std::string&>();
    const auto pages = request.at("pages").get<int>();
    if (original.empty() || original.size() > 2048 || pages < 1 || pages > static_cast<int>(search_page_limit))
        throw std::runtime_error("Invalid native foreground search request");
    auto query = original;
    std::string kind = "blanket";
    if (query.size() >= 4 && lower_ascii(query.substr(0, 4)) == "tag:") {
        kind = "tags";
        query.erase(0, 4);
        const auto first = query.find_first_not_of(" \t\r\n");
        if (first == query.npos) throw std::runtime_error("Empty DoujinStyle tag search");
        query.erase(0, first);
        query.erase(query.find_last_not_of(" \t\r\n") + 1);
    }
    std::set<std::int64_t> seen;
    auto steps = Json::array();
    std::string late_error;
    for (int page = 1; page <= pages; ++page) {
        auto ids = Json::array();
        for (const auto id : seen) ids.push_back(id);
        auto page_request = Json{{"query", query}, {"kind", kind}, {"page", page},
                                  {"seen", std::move(ids)}, {"refresh", request.value("refresh", false)}};
        Json batch;
        try {
            batch = search_job_page(db, now, page_request, fetch);
        } catch (const std::exception& error) {
            if (page == 1) throw;
            late_error = error.what();
            break;
        }
        for (const auto& row : batch.at("releases")) seen.insert(row.at("id").get<std::int64_t>());
        const bool more = batch.at("more").get<bool>();
        // The page transaction has already committed. All UI projection
        // needed during the callback is part of the event, so the Python
        // callback never has to re-enter the busy native IPC channel.
        if (request.value("stream_events", false)) {
            Json dates = Json::object();
            for (const auto& row : batch.at("releases")) {
                const auto id = row.at("id").get<std::int64_t>();
                const auto stored = document(db, "SELECT listing FROM releases WHERE id=?", id);
                if (!stored.is_object()) throw std::runtime_error("Committed search listing missing");
                dates[std::to_string(id)] = ui_date::choose(stored).at("date");
            }
            report(Json{{"kind", "search_page"}, {"page", page}, {"batch", batch},
                        {"ui_dates", std::move(dates)}});
        }
        steps.push_back(std::move(batch));
        if (!more) break;
    }
    return {{"pages", std::move(steps)}, {"late_error", std::move(late_error)}};
}

template<typename Fetch>
Json search_job(sqlite3* db, sqlite3_int64 now, const Json& request, Fetch fetch) {
    return search_job(db, now, request, fetch, [](const Json&) {});
}

template<typename Fetch>
Json chart_page(sqlite3* db, sqlite3_int64 now, bool persist, Fetch fetch) {
    const std::string url(guides);
    auto page = charts(fetch(url));
    if (persist) {
        auto seeds = Json::array();
        std::map<std::int64_t, std::size_t> by_id;
        for (const auto period : {"month", "all"}) {
            for (Json row : page["charts"][period]) {
                const auto id = row.at("id").get<std::int64_t>();
                row.erase("chart_rank");
                row.erase("chart_downloads");
                const auto [at, inserted] = by_id.try_emplace(id, seeds.size());
                if (inserted) seeds.push_back(std::move(row));
                else seeds[at->second] = std::move(row);
            }
        }
        Transaction transaction(db);
        save_page(db, {{"url", url}, {"page", page}, {"now", now}, {"checkpoint", nullptr}, {"seeds", std::move(seeds)}});
        transaction.commit();
    }
    return page;
}
}
