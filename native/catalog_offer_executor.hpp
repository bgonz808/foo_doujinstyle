#pragma once

// Shared bounded foreground/idle offer worker. Only the coordinator thread
// owns SQLite and emits progress. Network tasks never hold a writer lock.
#include "catalog_ingest.hpp"
#include "catalog_index_plan.hpp"
#include "site_offers.hpp"
#include "reference_projection.hpp"
#include "catalog_filename_hint.hpp"
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <exception>
#include <mutex>
#include <set>
#include <stop_token>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace doujin::offer_executor {
inline constexpr std::size_t max_batch = 64;
inline constexpr int max_parallel = 4;
struct Task { std::int64_t id; std::string url; };
struct Completed { std::int64_t id; Json reply; };

inline std::vector<Task> candidates(sqlite3* db, const Json& ids) {
    if (!ids.is_array() || ids.empty() || ids.size() > max_batch)
        throw std::runtime_error("Invalid native offer batch size");
    std::vector<Task> tasks;
    std::set<std::int64_t> unique;
    tasks.reserve(ids.size());
    for (const auto& item : ids) {
        if (!item.is_number_integer() ||
            (item.is_number_unsigned() && item.get<std::uint64_t>() >
                static_cast<std::uint64_t>(INT64_MAX)))
            throw std::runtime_error("Invalid native offer release ID");
        const auto id = item.get<std::int64_t>();
        if (id <= 0 || !unique.insert(id).second)
            throw std::runtime_error("Duplicate or invalid native offer release ID");
        const auto record = query(db, "SELECT listing FROM releases WHERE id=?", {id});
        if (!next(record.get())) throw std::runtime_error("Offer release not found in catalog");
        const auto value = string_column(record.get(), 0);
        if (value.size() > 65536) throw std::runtime_error("Offer release listing exceeds byte limit");
        auto listing = Json::parse(value, nullptr, false);
        if (!listing.is_object() || !listing.contains("url") || !listing.at("url").is_string())
            throw std::runtime_error("Invalid offer release listing");
        const auto& url = listing.at("url").get_ref<const std::string&>();
        if (url.size() > 2048 || !discovery::site_url(url) || discovery::release_id(url) != id)
            throw std::runtime_error("Offer release URL mismatches catalog ID");
        tasks.push_back({id, url});
    }
    return tasks;
}

// Match the Python catalog's MediaFire destination identity: URLs to the same
// public file collapse to one copy, but two distinct archives never do.
inline std::string destination_key(std::string_view url, std::string_view host) {
    if (host == "mediafire.com" || host == "www.mediafire.com") {
        // This is exactly the public /file/<id> identity accepted by the
        // legacy catalog, not arbitrary extra path components.
        const auto path_start = url.find('/', 8);
        if (path_start != url.npos && url.find_first_of("?#") == url.npos) {
            auto path = url.substr(path_start);
            if (path.starts_with("/file/")) {
                path.remove_prefix(6);
                const auto id_end = path.find('/');
                const auto id = path.substr(0, id_end);
                if (!id.empty() && id.find_first_not_of(
                        "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") == id.npos) {
                    auto tail = id_end == path.npos ? std::string_view{} : path.substr(id_end);
                    if (tail == "/" || tail.empty()) return "mediafire:file:" + std::string(id);
                    if (tail.starts_with('/')) {
                        tail.remove_prefix(1);
                        if (!tail.empty() && tail.back() == '/') tail.remove_suffix(1);
                        const auto slash = tail.find('/');
                        if (slash == tail.npos && !tail.empty())
                            return "mediafire:file:" + std::string(id);
                        if (slash != tail.npos && slash > 0 && tail.substr(slash) == "/file")
                            return "mediafire:file:" + std::string(id);
                    }
                }
            }
        }
    }
    return "url:" + std::string(url);
}

inline std::string catalog_hint(std::string_view filename) {
    return catalog_filename::hint(filename);
}

inline Json link(const Json& offer_record) {
    if (!offer_record.is_array() || offer_record.size() != 3 ||
        !offer_record[0].is_string() || !offer_record[1].is_object() || !offer_record[2].is_string())
        throw std::runtime_error("Malformed native offer record");
    const auto& url = offer_record[2].get_ref<const std::string&>();
    const auto& source = offer_record[0].get_ref<const std::string&>();
    if (source != "doujinstyle") {
        if (!source.starts_with("doujinstyle:"))
            throw std::runtime_error("Invalid native offer source");
        const auto suffix = std::string_view(source).substr(12);
        if (suffix.empty() || suffix.size() > 6 || suffix.find_first_not_of("0123456789") != suffix.npos)
            throw std::runtime_error("Invalid native offer source index");
    }
    auto offer = offer_record[1];
    if (offer.contains("filename") && offer.at("filename").is_string()) {
        const auto code = catalog_hint(offer.at("filename").get_ref<const std::string&>());
        if (!code.empty()) offer["catalog_id_candidate"] = code;
    }
    if (url.size() > 8192 || !url.starts_with("https://"))
        throw std::runtime_error("Invalid native offer URL");
    const auto& hostname = offer.at("host").get_ref<const std::string&>();
    // The original hostname is retained in the offer; the normalized family
    // must agree with the destination, preventing poisoned catalog links.
    const auto actual = discovery::offer_host(url);
    if (actual != hostname) throw std::runtime_error("Offer hostname mismatch");
    std::string family = hostname;
    if (hostname == "www.mediafire.com" || hostname.ends_with(".mediafire.com")) family = "mediafire.com";
    else if (hostname == "mega.nz" || hostname.ends_with(".mega.nz") || hostname == "mega.co.nz") family = "mega.nz";
    return {{"source", offer_record[0]}, {"offer", offer}, {"url", url},
            {"destination", destination_key(url, actual)}, {"host", family}};
}

// Project offer status once in C++, using the same native URL and provider
// capability checks as the caller. This prevents N additional IPC roundtrips
// and means live commit-event callbacks never call into their own busy pipe.
inline std::string status(const Json& offer_record, const Json& validated) {
    const auto host = offer_record.at(1).at("host").get<std::string>();
    const auto family = validated.at("host").get<std::string>();
    const auto url = validated.at("url").get<std::string>();
    const auto readable = index_plan::usable(url, family, offer_record.at(1).dump());
    if (family == "mega.nz") {
        if (url.find("/folder/") != url.npos || url.find("#F!") != url.npos)
            return "Found MEGA folder link (use Open release)";
        return readable ? "Found MEGA public file (eligible for archive indexing)" :
            "Found MEGA link without a supported public file URL and key (use Open release)";
    }
    return "Found " + host + " offer" + (readable ? "" : " (use Open release)");
}

inline Json commit(sqlite3* db, const Completed& completed, std::int64_t now) {
    const auto& answer = completed.reply;
    if (!answer.is_object()) throw std::runtime_error("Malformed offer response");
    const auto& observation = answer.at("observation");
    const auto& offers = answer.at("offers");
    if (!offers.is_array() || offers.size() > 32 ||
        (!observation.is_null() && (!observation.is_object() || observation.at("id") != completed.id)))
        throw std::runtime_error("Malformed offer response records");
    auto links = Json::array();
    auto statuses = Json::array();
    for (const auto& offer : offers) {
        auto validated = link(offer);
        statuses.push_back(status(offer, validated));
        links.push_back(std::move(validated));
    }
    const auto error = answer.value("error", Json());
    if (!error.is_null() && (!error.is_string() || error.get_ref<const std::string&>().size() > 2048))
        throw std::runtime_error("Malformed offer error");
    const auto copies = ingest(db, "offers", {{"id", completed.id}, {"now", now},
        {"observation", observation}, {"links", std::move(links)}, {"error", error},
        {"observed_sources", nullptr}});
    return {{"id", completed.id}, {"offers", offers}, {"statuses", std::move(statuses)}, {"error", error},
            {"copies", copies}, {"had_observation", !observation.is_null()},
            {"rate_limited", answer.value("rate_limited", false)}};
}

// Fetch is called on at most `workers` native threads. Report is called ONLY
// on the submitting thread and AFTER the corresponding transaction commits.
// An uncertain network POST is recorded once, never replayed by this runner.
// Stop prevents pending work being dispatched and suppresses late commits.
template<class Fetch, class Report>
Json run(sqlite3* db, const Json& ids, int workers, std::int64_t now,
         Fetch fetch, Report report, std::stop_token stop = {}) {
    if (workers < 1 || workers > max_parallel || now < 0)
        throw std::runtime_error("Invalid native offer execution budget");
    const auto tasks = candidates(db, ids);
    std::atomic<std::size_t> next{0};
    std::atomic<bool> halt{false};
    std::atomic<bool> rate_limited{false};
    const auto threads = std::min(static_cast<std::size_t>(workers), tasks.size());
    std::atomic<std::size_t> remaining_threads{threads};
    std::mutex mutex;
    std::condition_variable ready;
    std::deque<Completed> finished;
    const auto work = [&] {
        while (!halt.load() && !stop.stop_requested()) {
            const auto at = next.fetch_add(1);
            if (at >= tasks.size()) break;
            const auto& task = tasks[at];
            Json response;
            try { response = fetch(task, stop); }
            catch (const std::exception& error) {
                const std::string message(error.what());
                response = {{"observation", nullptr}, {"offers", Json::array()},
                    {"error", message.substr(0, 2048)}};
                if (message.find("HTTP 429") != std::string::npos)
                    response["rate_limited"] = true;
            }
            if (response.is_object() && response.value("rate_limited", false)) {
                rate_limited.store(true);
                halt.store(true);
            }
            {
                std::lock_guard guard(mutex);
                finished.push_back({task.id, std::move(response)});
            }
            ready.notify_one();
        }
        --remaining_threads;
        ready.notify_one();
    };
    std::vector<std::jthread> pool;
    pool.reserve(threads);
    for (std::size_t i = 0; i < threads; ++i)
        pool.emplace_back(work);
    auto results = Json::array();
    while (results.size() < tasks.size() && !stop.stop_requested()) {
        Completed completed{};
        {
            std::unique_lock lock(mutex);
            ready.wait_for(lock, std::chrono::milliseconds(25), [&] {
                return !finished.empty() || stop.stop_requested() || remaining_threads.load() == 0;
            });
            if (stop.stop_requested()) break;
            if (finished.empty()) {
                if (remaining_threads.load() == 0) break;
                continue;
            }
            completed = std::move(finished.front());
            finished.pop_front();
        }
        try {
            auto event = commit(db, completed, now);
            // Cancellation can race the SQLite transaction itself. Once the
            // commit has succeeded we MUST publish its event, even if Stop
            // arrived during the write; otherwise the durable change becomes
            // an invisible (and incorrectly retryable) result.
            report(event);
            results.push_back(std::move(event));
        } catch (...) {
            halt.store(true);
            throw;
        }
    }
    halt.store(true);
    // jthreads join here. The parent foobar2000 Job Object can terminate the
    // worker process on Stop while a blocking WinHTTP operation is outstanding.
    return {{"results", std::move(results)}, {"cancelled", stop.stop_requested()},
            {"rate_limited", rate_limited.load()}};
}
} // namespace doujin::offer_executor
