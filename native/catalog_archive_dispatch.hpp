#pragma once

// Native archive work queue. The SQLite owner validates job identity, drives
// fetch concurrency and commits each result BEFORE emitting a bounded event.
// Network workers never own a writer or call the UI. The selected release IDs
// are derived from current DB links, never accepted from a remote response.
#include "catalog_ingest.hpp"
#include "catalog_index_plan.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <set>
#include <stop_token>
#include <string>
#include <thread>
#include <vector>

namespace doujin::archive_dispatch {
using Json = nlohmann::json;
inline constexpr std::size_t max_jobs = 2048; // bounded input; terminal events are compact
inline constexpr int max_parallel = 3;
inline constexpr std::size_t max_archive_record = 8 * 1024 * 1024;
inline constexpr std::size_t max_cover_hex = 256 * 1024;

struct Task {
    std::int64_t id;
    std::string url;
    std::string referrer;
    Json ids;
    bool headers;
    bool cover;
};
struct Completed { Task task; Json reply; };

inline std::vector<Task> select(sqlite3* db, const Json& jobs, const Json& allowed_ids,
                                bool default_headers, bool default_cover) {
    if (!jobs.is_array() || jobs.size() > max_jobs)
        throw std::runtime_error("Invalid native archive job list");
    std::set<std::int64_t> allowed;
    if (!allowed_ids.is_null()) {
        if (!allowed_ids.is_array() || allowed_ids.empty() || allowed_ids.size() > 512)
            throw std::runtime_error("Invalid native archive release filter");
        for (const auto& id : allowed_ids) {
            if (!id.is_number_integer() || id <= 0 || !allowed.insert(id.get<std::int64_t>()).second)
                throw std::runtime_error("Duplicate or invalid native archive release filter");
        }
    }
    std::set<std::int64_t> unique;
    std::vector<Task> selected;
    for (const auto& input : jobs) {
        if (!input.is_object() || !input.contains("id") || !input.at("id").is_number_integer())
            throw std::runtime_error("Malformed native archive task");
        const auto copy = input.at("id").get<std::int64_t>();
        if (copy <= 0 || !unique.insert(copy).second)
            throw std::runtime_error("Duplicate or invalid native archive copy ID");
        auto row = query(db, "SELECT share_url,host,offer FROM copies WHERE id=?", {copy});
        if (!next(row.get())) throw std::runtime_error("Archive copy missing from catalog");
        const auto url = string_column(row.get(), 0);
        const auto host = string_column(row.get(), 1);
        const auto offer = string_column(row.get(), 2);
        row.reset();
        if (!index_plan::usable(url, host, offer) ||
            (input.contains("share_url") && input.at("share_url") != url))
            throw std::runtime_error("Archive job URL or capability no longer matches catalog");
        auto members = prepare(db, "SELECT rc.release_id,r.listing FROM release_copies rc "
            "JOIN releases r ON r.id=rc.release_id WHERE rc.copy_id=? AND rc.current=1 ORDER BY rc.release_id");
        check(db, sqlite3_bind_int64(members.get(), 1, copy));
        Json ids = Json::array();
        std::string referrer;
        while (next(members.get())) {
            const auto id = sqlite3_column_int64(members.get(), 0);
            if (!allowed.empty() && !allowed.contains(id)) continue;
            ids.push_back(id);
            if (referrer.empty() && sqlite3_column_type(members.get(), 1) == SQLITE_TEXT) {
                const auto raw = string_column(members.get(), 1);
                if (raw.size() <= 65536) {
                    const auto listing = Json::parse(raw, nullptr, false);
                    if (listing.is_object() && listing.value("url", Json()).is_string()) {
                        const auto candidate = listing.at("url").get<std::string>();
                        if (candidate.size() <= 2048 && discovery::site_url(candidate))
                            referrer = candidate;
                    }
                }
            }
        }
        if (ids.empty()) throw std::runtime_error("Archive no longer linked to requested releases");
        if (input.contains("ids") && input.at("ids") != ids)
            throw std::runtime_error("Stale archive selection release set");
        if (input.contains("headers") && !input.at("headers").is_boolean())
            throw std::runtime_error("Invalid archive header policy");
        if (input.contains("cover") && !input.at("cover").is_boolean())
            throw std::runtime_error("Invalid archive cover policy");
        selected.push_back({copy, std::move(url), std::move(referrer), std::move(ids),
            input.value("headers", default_headers), input.value("cover", default_cover)});
    }
    return selected;
}

inline Json commit(sqlite3* db, const Completed& finished, std::int64_t now) {
    const auto& reply = finished.reply;
    if (!reply.is_object() || !reply.contains("archive") || !reply.contains("error"))
        throw std::runtime_error("Malformed native archive response");
    auto archive = reply.at("archive");
    const auto& error = reply.at("error");
    if ((!error.is_null() && (!error.is_string() || error.get_ref<const std::string&>().size() > 2048)) ||
        (!archive.is_null() && !archive.is_object()) ||
        (archive.is_object() && archive.dump().size() > max_archive_record) ||
        (archive.is_null() == error.is_null()))
        throw std::runtime_error("Invalid native archive response evidence");
    auto cover = reply.value("cover_payload", Json());
    if (!cover.is_null() && (!cover.is_string() || cover.get_ref<const std::string&>().size() > max_cover_hex ||
        cover.get_ref<const std::string&>().size() % 2))
        throw std::runtime_error("Invalid native archive cover evidence");
    if (!error.is_null() && !cover.is_null())
        throw std::runtime_error("Archive error cannot publish artwork");
    if (!finished.task.cover && !cover.is_null())
        throw std::runtime_error("Unrequested archive artwork rejected");
    if (cover.is_string()) {
        const auto& bytes = cover.get_ref<const std::string&>();
        if (bytes.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
            throw std::runtime_error("Malformed hexadecimal archive artwork");
    }
    if (archive.is_object()) {
        const auto& kind = archive.value("format", Json());
        const auto& http = archive.value("archive_http", Json());
        if (!kind.is_string() || kind.get_ref<const std::string&>().size() > 24 ||
            !http.is_object() || !http.contains("body_bytes") ||
            !(http.at("body_bytes").is_number_unsigned() || http.at("body_bytes").is_number_integer()) ||
            http.at("body_bytes") < 0 ||
            (kind == "zip" && (!archive.value("audio_members", Json()).is_number_integer() ||
                archive.at("audio_members") < 0)))
            throw std::runtime_error("Malformed native archive inspection statistics");
    }
    // A current URL must still match the network snapshot. Never attach the
    // bytes of an old provider URL to a replaced copy identity.
    auto latest = query(db, "SELECT share_url FROM copies WHERE id=?", {finished.task.id});
    if (!next(latest.get()) || string_column(latest.get(), 0) != finished.task.url)
        throw std::runtime_error("Archive copy changed while probing");
    latest.reset();
    for (const auto& id : finished.task.ids) {
        auto association = query(db, "SELECT 1 FROM release_copies WHERE copy_id=? AND release_id=? AND current=1",
            {finished.task.id, id});
        if (!next(association.get()))
            throw std::runtime_error("Archive copy was unlinked while probing");
    }
    if (archive.is_object() && finished.task.cover && archive.value("format", Json()) == "zip")
        archive["cover_probe_attempted"] = true;
    ingest(db, "archive", {{"id", finished.task.id}, {"now", now},
        {"archive", archive}, {"error", error}});
    // Do not duplicate provider headers/large diagnostic payloads in the IPC
    // stream. Full provenance remains in the SQLite archive record.
    const auto compact_http = archive.is_object()
        ? Json{{"body_bytes", archive.at("archive_http").at("body_bytes")}} : Json();
    Json event{{"id", finished.task.id}, {"ids", finished.task.ids},
        {"error", error}, {"ok", error.is_null()}, {"cover_payload", cover},
        {"format", archive.is_object() ? archive.value("format", Json()) : Json()},
        {"audio_members", archive.is_object() ? archive.value("audio_members", Json()) : Json()},
        {"archive_http", compact_http}};
    return event;
}

// Fetch operates on a worker-owned transport instance. An event callback is
// called from the SQLite owner thread, synchronously after commit. If stopped
// during an SQLite transaction, the committed result is still emitted once.
template<class Fetch, class Report>
Json run(sqlite3* db, const Json& jobs, const Json& ids, int workers, std::int64_t now,
         Fetch fetch, Report report, std::stop_token stop = {},
         bool headers = true, bool cover = false) {
    if (workers < 1 || workers > max_parallel || now < 0)
        throw std::runtime_error("Invalid native archive execution budget");
    auto tasks = select(db, jobs, ids, headers, cover);
    std::atomic<std::size_t> next{0};
    std::atomic<bool> halt{false};
    std::atomic<bool> rate_limited{false};
    const auto threads = std::min<std::size_t>(workers, tasks.size());
    std::atomic<std::size_t> running{threads};
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<Completed> completed;
    const auto worker = [&] {
        while (!halt.load() && !stop.stop_requested()) {
            const auto at = next.fetch_add(1);
            if (at >= tasks.size()) break;
            const auto& task = tasks[at];
            Json reply;
            try { reply = fetch(task, stop); }
            catch (const std::exception& exc) {
                const std::string reason = exc.what();
                reply = {{"archive", nullptr}, {"error", reason.substr(0, 2048)}};
                if (reason.find("HTTP 429") != std::string::npos) reply["rate_limited"] = true;
            }
            if (reply.is_object() && reply.contains("rate_limited") && reply.at("rate_limited") == true) {
                halt = true;
                rate_limited = true;
            }
            {
                std::lock_guard lock(mutex);
                completed.push_back({task, std::move(reply)});
            }
            cv.notify_one();
        }
        --running;
        cv.notify_one();
    };
    std::vector<std::jthread> pool;
    pool.reserve(threads);
    for (std::size_t i=0; i<threads; ++i) pool.emplace_back(worker);
    auto events = Json::array();
    while (events.size() < tasks.size() && !stop.stop_requested()) {
        Completed item{};
        {
            std::unique_lock lock(mutex);
            cv.wait_for(lock, std::chrono::milliseconds(25), [&] {
                return !completed.empty() || stop.stop_requested() || running.load() == 0;
            });
            if (stop.stop_requested()) break;
            if (completed.empty()) {
                if (running.load() == 0) break;
                continue;
            }
            item = std::move(completed.front());
            completed.pop_front();
        }
        try {
            auto event = commit(db, item, now);
            report(event); // commit already happened, even if stop races it
            event.erase("cover_payload"); // never duplicate the cover in terminal reply
            events.push_back(std::move(event));
        } catch (...) {
            halt = true;
            throw;
        }
    }
    halt = true;
    const auto attempted = events.size();
    return {{"events", std::move(events)}, {"attempted", attempted},
        {"selected", tasks.size()}, {"rate_limited", rate_limited.load()},
        {"cancelled", stop.stop_requested()}};
}
} // namespace doujin::archive_dispatch
