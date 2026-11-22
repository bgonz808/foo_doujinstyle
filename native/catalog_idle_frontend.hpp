#pragma once
// Complete idle pass ownership belongs to the native worker process.
// All progress frames are emitted *after* the relevant native catalog commit.
// The caller's Windows Job Object terminates this process on Stop; the
// restarted job reselects candidates from durable SQLite state.
#include "catalog_idle_plan.hpp"
#include "catalog_robots_advisory.hpp"
#include "descriptor.hpp"
#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <iomanip>
#include <sstream>
#include <string_view>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace doujin::idle_frontend {
using Json = nlohmann::json;
struct Options {
    std::string db;
    std::string output;
    std::string root;
    std::string artwork;
    std::string host_zlib;
    int layers = idle_plan::browse;
    bool catalog_signals = false;
};

// Only the first byte of each stdout record may select the foobar2000 UI
// protocol; never forward raw provider error control bytes into that channel.
inline std::string safe_status(std::string_view raw) {
    std::string result;
    result.reserve(std::min<std::size_t>(raw.size(), 200));
    for (unsigned char ch : raw) {
        if (result.size() >= 200) break;
        result.push_back(ch < 32 || ch == 127 ? ' ' : static_cast<char>(ch));
    }
    return result;
}
inline std::string format_bytes(std::uint64_t amount) {
    if (amount < 1024) return std::to_string(amount) + " B";
    constexpr const char* units[] = {"KiB", "MiB", "GiB", "TiB"};
    auto value = static_cast<double>(amount);
    std::size_t unit = 0;
    do { value /= 1024.0; ++unit; } while (value >= 1024.0 && unit < 4);
    std::ostringstream output;
    output << std::fixed << std::setprecision(2) << value << ' ' << units[unit - 1];
    return output.str();
}

// Submit(request, on_committed_event) returns a terminal native response.
// No network or SQLite writer may be held by emit(), and no Python callback
// can execute inside the coordinator.
template<class Submit, class Emit, class WriteArt>
void run(const Options& options, Submit submit, Emit emit, WriteArt write_art) {
    if (options.db.empty() || options.output.empty() || options.layers < 1 || options.layers > 63)
        throw std::runtime_error("Invalid native idle job configuration");
    using std::to_string;
    const auto request = [&](Json data, const std::function<void(const Json&)>& callback = {}) -> Json {
        if (!data.contains("db")) data["db"] = options.db;
        return submit(data, callback);
    };
    const auto gate = [&] {
        const auto ready = request({{"operation", "idle_wait"}});
        if (!ready.is_object() || ready.value("ready", false) != true)
            throw std::runtime_error("Idle gate declined a scheduled request");
    };
    emit("\x1b");
    if (options.layers & idle_plan::details) {
        // Native detail job already selects eligible rows within its own job.
        // Avoid a second independent plan/SELECT: another worker could advance
        // the catalog between two calls and yield a false terminal mismatch.
        std::set<std::int64_t> seen;
        auto streamed = Json::array();
        auto completed = request({{"operation", "catalog_idle_detail_job"}, {"stream_events", true}},
            [&](const Json& event) {
                if (!event.is_object() || event.value("kind", "") != "idle_detail" ||
                    !event.contains("id") || !event.at("id").is_number_integer())
                    throw std::runtime_error("Malformed native idle detail event");
                auto id = event.at("id").template get<std::int64_t>();
                if (id <= 0 || seen.size() >= idle_plan::max_jobs || !seen.insert(id).second)
                    throw std::runtime_error("Unexpected or duplicate native idle detail event");
                streamed.push_back(event);
                emit("  " + to_string(id) + "   Release details saved");
            });
        if (completed.at("events") != streamed)
            throw std::runtime_error("Mismatched committed idle detail terminal response");
    }
    if (options.layers & idle_plan::links) {
        const auto jobs = request({{"operation", "catalog_idle_plan"}, {"layers", idle_plan::links}}).at("links");
        if (!jobs.is_array() || jobs.size() > idle_plan::max_jobs)
            throw std::runtime_error("Invalid idle offer selection");
        if (!jobs.empty()) {
            gate();
            auto ids = Json::array();
            std::set<std::int64_t> selected_ids;
            for (const auto& job : jobs) {
                if (!job.is_object() || !job.contains("id") || !job.at("id").is_number_integer())
                    throw std::runtime_error("Malformed idle offer selection");
                const auto id = job.at("id").template get<std::int64_t>();
                if (id <= 0 || !selected_ids.insert(id).second)
                    throw std::runtime_error("Duplicate idle offer selection");
                ids.push_back(id);
            }
            const auto result = request({{"operation", "catalog_offer_batch"}, {"ids", ids}, {"workers", 3}});
            const auto& entries = result.at("results");
            if (result.value("cancelled", true) || result.value("rate_limited", false) ||
                !entries.is_array() || entries.size() != ids.size())
                throw std::runtime_error("Incomplete or rate-limited native idle offer batch");
            std::set<std::int64_t> seen;
            for (const auto& event : entries) {
                const auto id = event.at("id").template get<std::int64_t>();
                if (!seen.insert(id).second || std::find(ids.begin(), ids.end(), id) == ids.end())
                    throw std::runtime_error("Invalid idle offer completion identity");
                emit("  " + to_string(id) + (event.at("offers").empty() ?
                     "   Download link unavailable" : "   Download link saved"));
            }
        }
    }
    if (options.layers & (idle_plan::zip | idle_plan::tags | idle_plan::covers)) {
        const auto jobs = request({{"operation", "catalog_idle_plan"},
                                   {"layers", options.layers & (idle_plan::zip | idle_plan::tags | idle_plan::covers)}}).at("archives");
        if (!jobs.is_array() || jobs.size() > idle_plan::max_jobs)
            throw std::runtime_error("Invalid idle archive selection");
        if (!jobs.empty()) {
            std::set<std::int64_t> expected, committed, committed_releases_;
            auto streamed = Json::array();
            for (const auto& job : jobs) {
                auto id = job.at("id").template get<std::int64_t>();
                if (id <= 0 || !expected.insert(id).second)
                    throw std::runtime_error("Duplicate idle archive selection");
            }
            auto stages = request({{"operation", "catalog_archive_dispatch"}, {"jobs", jobs},
                    {"ids", nullptr}, {"idle", true}, {"cover", !options.artwork.empty()},
                    {"workers", 1}, {"host_zlib", options.host_zlib}, {"stream_events", true}},
                [&](const Json& event) {
                    if (!event.is_object() || !event.contains("id") || !event.at("id").is_number_integer() ||
                        !expected.contains(event.at("id").template get<std::int64_t>()) ||
                        !committed.insert(event.at("id").template get<std::int64_t>()).second ||
                        !event.contains("ok") || !event.at("ok").is_boolean() ||
                        !event.contains("ids") || !event.at("ids").is_array())
                        throw std::runtime_error("Malformed committed idle archive event");
                    const auto& ids = event.at("ids");
                    std::string labels;
                    for (const auto& id : ids) {
                        if (!id.is_number_integer() || id.template get<std::int64_t>() <= 0)
                            throw std::runtime_error("Invalid committed idle archive release identity");
                        if (!labels.empty()) labels += ", ";
                        labels += to_string(id.template get<std::int64_t>());
                    }
                    if (event.at("ok") == true) {
                        const auto cover = event.value("cover_payload", Json());
                        if (!options.artwork.empty() && cover.is_string()) {
                            const auto& bytes = cover.template get_ref<const std::string&>();
                            if (bytes.size() > 256 * 1024 || bytes.size() % 2 != 0 ||
                                bytes.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
                                throw std::runtime_error("Malformed committed archive artwork");
                            for (const auto& id : ids)
                                write_art(options.artwork, id.template get<std::int64_t>(), bytes);
                        }
                        const auto format = event.at("format").template get<std::string>();
                        const auto& http = event.at("archive_http");
                        if (!http.is_object() || !http.contains("body_bytes") ||
                            !http.at("body_bytes").is_number_integer() || http.at("body_bytes") < 0)
                            throw std::runtime_error("Malformed idle archive HTTP statistics");
                        const auto bytes = http.at("body_bytes").template get<std::uint64_t>();
                        if (format == "zip") {
                            const auto members = event.at("audio_members").template get<std::int64_t>();
                            if (members < 0) throw std::runtime_error("Invalid idle archive audio count");
                            emit("  " + labels + "   native archive committed  •  " +
                                 to_string(members) + " audio members  •  " + format_bytes(bytes) + " read");
                        } else {
                            emit("  " + labels + "   " + safe_status(format) + " archive  •  " +
                                 format_bytes(bytes) + " read");
                        }
                        for (const auto& id : ids) committed_releases_.insert(id.template get<std::int64_t>());
                    } else {
                        emit("  " + labels + "   Archive unavailable (" +
                             safe_status(event.at("error").template get<std::string>()) + ")");
                    }
                    auto terminal_event = event;
                    terminal_event.erase("cover_payload");
                    streamed.push_back(std::move(terminal_event));
                });
            if (stages.value("cancelled", true) || stages.value("rate_limited", false) ||
                stages.at("events") != streamed || committed.size() != expected.size() ||
                stages.at("attempted") != streamed.size() ||
                stages.at("selected") != expected.size())
                throw std::runtime_error("Incomplete native idle archive dispatch");
            if (!committed_releases_.empty()) {
                Json ids = Json::array();
                for (auto id : committed_releases_) ids.push_back(id);
                const auto publication = request({{"operation", "publish_many"}, {"ids", ids},
                    {"output", options.output}, {"root", options.root}, {"copy_id", nullptr}});
                if (publication.at("notifications").size() != publication.at("references"))
                    throw std::runtime_error("Incomplete idle publication notifications");
                for (const auto& item : publication.at("notifications"))
                    if (item.at(2) == true)
                        emit("\x19" + to_string(item.at(0).template get<std::int64_t>()) + "\t" + item.at(1).template get<std::string>());
            }
        }
    }
    if (options.catalog_signals && (options.layers & idle_plan::tags)) {
        for (const auto& id : request({{"operation", "bangumi_idle"}}))
            emit("\x1a" + to_string(id.template get<std::int64_t>()));
    }
    emit("\x1b");
    if (options.layers & idle_plan::browse) {
        const auto advisory = request({{"operation", "site_resource"}, {"kind", "robots"},
                {"url", "https://doujinstyle.com/robots.txt"}, {"cap", 64 * 1024}});
        const auto status = advisory.at("status").template get<int>();
        if (status == 404 || status == 410) {
            emit("robots.txt is unavailable. Continuing the requested idle backfill.");
        } else if (status == 302) {
            emit("robots.txt redirected. Continuing the requested idle backfill.");
        } else if (status != 200) {
            emit("robots.txt was not available for an advisory. Continuing the requested idle backfill.");
        } else {
            const auto bytes = doujin::decode_hex(advisory.at("body").template get_ref<const std::string&>(), 64 * 1024);
            const auto rules = doujin::robots_advisory::inspect(bytes);
            if (rules.excluded)
                emit("DoujinStyle's robots.txt excludes this crawler. Continuing the requested rate-limited backfill.");
            if (rules.ambiguous)
                emit("robots.txt contains advanced patterns that require manual policy review.");
        }
        emit("Idle backfill armed. Waiting for 30 seconds of Windows input idle.");
        Json state = nullptr;
        std::size_t saved = 0;
        // The backfill may intentionally traverse thousands of pages, but a
        // bogus cyclic continuation must not spin indefinitely.
        for (std::size_t page = 0; page < 1000000; ++page) {
            gate();
            const auto step = request({{"operation", "catalog_browse_step"}, {"state", state}});
            if (step.contains("saved")) {
                ++saved;
                emit("Idle catalog page " + to_string(step.at("saved").template get<std::int64_t>() + 1) +
                     "  •  " + to_string(step.value("rows", 0)) + " listings saved");
                if (saved % 10 == 0) emit("\x1b");
            }
            if (!step.contains("saved") && step.value("message", "").size())
                emit(step.at("message").template get<std::string>());
            if (step.value("done", false)) {
                if (step.contains("saved") && step.value("message", "").size())
                    emit(step.at("message").template get<std::string>());
                break;
            }
            if (!step.contains("state") || step.at("state").is_null())
                throw std::runtime_error("Native idle browse job returned no continuation");
            state = step.at("state");
            if (page == 999999) throw std::runtime_error("Idle browse continuation limit exceeded");
        }
        emit("\x1b");
    }
    emit("Selected idle indexing pass finished.");
}
} // namespace doujin::idle_frontend
