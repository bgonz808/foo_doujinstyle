#pragma once

// User-invoked archive download and metadata repair coordinator.
// All selection, POST-attempt recording, network transport, ZIP parsing, and
// database commits live in the native Publisher. This frontend issues only
// bounded requests and converts verified responses to UI notifications.
#include "catalog_search_frontend.hpp"
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace doujin::explicit_job {
using Json = nlohmann::ordered_json;

enum class Action { download, repair };
struct Options {
    std::string db;
    std::string output;
    std::string root;
    std::string host_zlib;
    std::int64_t release_id = 0;
    Action action = Action::download;
};

inline void check_options(const Options& option) {
    if (option.db.empty() || option.output.empty() || option.release_id <= 0)
        throw std::runtime_error("Invalid native archive action configuration");
}

inline std::string checked_path(const Json& row) {
    if (!row.is_string()) throw std::runtime_error("Malformed native archive path");
    const auto path = row.get<std::string>();
    if (path.empty() || path.size() > 32768 ||
        path.find_first_of("\r\n\t") != path.npos ||
        path.find('\0') != path.npos)
        throw std::runtime_error("Unsafe native archive path");
    return path;
}

inline Json checked_offer(const Json& reply, std::int64_t id) {
    if (!reply.is_object() || !reply.contains("results") ||
        !reply.at("results").is_array() || reply.at("results").size() != 1 ||
        !reply.contains("cancelled") || !reply.at("cancelled").is_boolean() ||
        !reply.contains("rate_limited") || !reply.at("rate_limited").is_boolean() ||
        reply.at("cancelled") == true || reply.at("rate_limited") == true)
        throw std::runtime_error("Native offer discovery was interrupted or incomplete");
    const auto& item = reply.at("results").at(0);
    if (!item.is_object() || !item.contains("id") || !item.at("id").is_number_integer() ||
        item.at("id") != id || !item.contains("offers") || !item.at("offers").is_array() ||
        !item.contains("error") ||
        (!item.at("error").is_null() && !item.at("error").is_string()))
        throw std::runtime_error("Invalid native committed offer result");
    if (!item.at("error").is_null() && item.at("offers").empty())
        throw std::runtime_error("No usable offer; native POST state or provider failure requires review");
    return item;
}

template<class Submit>
void refresh_offer_once(const Options& option, Submit submit, bool& refreshed, bool all_forms) {
    if (refreshed)
        throw std::runtime_error("Archive action already refreshed offers; refusing repeated POST");
    // An ambiguous outcome propagates as failure. In particular do NOT
    // retry after transport/IPC failure or re-submit an uncertain POST.
    refreshed = true;
    const Json request{{"operation", "catalog_offer_batch"}, {"db", option.db},
                       {"ids", Json::array({option.release_id})},
                       {"workers", 1}, {"all_forms", all_forms}};
    (void)checked_offer(submit(request, {}), option.release_id);
}

template<class Submit, class ReadListing, class Emit>
void download(const Options& option, Submit submit, ReadListing read_listing, Emit emit) {
    check_options(option);
    const auto listing = read_listing(option.release_id);
    if (!listing.is_object())
        throw std::runtime_error("Release is missing from the native catalog");
    const auto referrer = search_frontend::text(listing, "url");
    bool refreshed = false;
    const auto attempt = [&] {
        return submit(Json{{"operation", "archive_download_job"},
            {"db", option.db}, {"id", option.release_id},
            {"output", option.output}, {"referrer", referrer}}, {});
    };
    auto result = attempt();
    if (!result.is_object() || !result.contains("status") || !result.at("status").is_string())
        throw std::runtime_error("Incomplete native archive download response");
    if (result.at("status") == "no_offer") {
        refresh_offer_once(option, submit, refreshed, true);
        result = attempt();
    }
    if (!result.is_object() || !result.contains("status") || !result.at("status").is_string())
        throw std::runtime_error("Incomplete native archive download retry response");
    if (result.at("status") != "saved")
        throw std::runtime_error(result.at("status") == "missing_release"
            ? "Release is missing from the native catalog"
            : "No supported current archive offer; use Open release");
    const auto& archive = result.at("archive");
    if (!archive.is_object() || !archive.contains("path") ||
        !archive.contains("size") || !archive.at("size").is_number_integer() ||
        archive.at("size") < 0 || !archive.contains("authenticated") ||
        !archive.at("authenticated").is_boolean())
        throw std::runtime_error("Malformed native archive download accounting");
    const auto path = checked_path(archive.at("path"));
    const auto bytes = archive.at("size").template get<std::uint64_t>();
    const bool authenticated = archive.at("authenticated").template get<bool>();
    const auto integrity = search_frontend::text(archive, "integrity");
    // MEGA's whole-file MAC differs categorically from HTTPS transport
    // protection. Never accidentally promote MediaFire to authenticated.
    if (authenticated && !integrity.empty())
        throw std::runtime_error("Contradictory native MEGA archive integrity claim");
    if (!authenticated && integrity != "transport_and_length_only")
        throw std::runtime_error("Missing or contradictory native HTTP integrity claim");
    emit("Saved " + search_frontend::safe_text(path, 2048) + " (" +
         search_frontend::printable_size(bytes) +
         (authenticated ? " authenticated with MEGA MAC)" :
                          " HTTPS transfer; no whole-file MAC)"));
}

inline Json checked_copy(const Json& plan, std::int64_t id) {
    if (!plan.is_object() || !plan.contains("status") || !plan.at("status").is_string())
        throw std::runtime_error("Malformed native archive selection");
    if (plan.at("status") == "missing_release")
        throw std::runtime_error("Release is missing from this catalog; search first");
    if (plan.at("status") == "no_offer") return nullptr;
    if (plan.at("status") != "ready" || !plan.contains("copy") ||
        !plan.at("copy").is_object())
        throw std::runtime_error("Malformed native archive selection state");
    const auto& copy = plan.at("copy");
    if (!copy.contains("id") || !copy.at("id").is_number_integer() ||
        copy.at("id") <= 0 || !copy.contains("url") ||
        !copy.at("url").is_string() || copy.at("url").get_ref<const std::string&>().size() > 8192 ||
        !copy.contains("host") || !copy.at("host").is_string() ||
        (copy.at("host") != "mediafire.com" && copy.at("host") != "mega.nz"))
        throw std::runtime_error("Malformed native archive copy identity");
    (void)id;
    return copy;
}

template<class Submit, class Emit>
Json probe(const Options& option, const Json& copy, Submit submit, Emit emit) {
    Json observed = Json::array();
    Json ids = Json::array({option.release_id});
    const Json request{{"operation", "catalog_archive_dispatch"}, {"db", option.db},
        {"jobs", Json::array({Json{{"id", copy.at("id")}, {"share_url", copy.at("url")}}})},
        {"ids", ids}, {"workers", 1}, {"headers", true}, {"cover", false},
        {"host_zlib", option.host_zlib}, {"stream_events", true}};
    const auto completed = submit(request, std::function<void(const Json&)>([&](const Json& event) {
        if (!event.is_object() || !event.contains("id") || event.at("id") != copy.at("id") ||
            !event.contains("ids") || !event.at("ids").is_array() ||
            event.at("ids").empty() || !event.contains("ok") ||
            !event.at("ok").is_boolean() || !event.contains("error") ||
            (!event.at("error").is_null() && !event.at("error").is_string()) ||
            (event.at("ok") == true && !event.at("error").is_null()))
            throw std::runtime_error("Malformed native committed archive event");
        bool contains_release = false;
        for (const auto& associated : event.at("ids")) {
            if (!associated.is_number_integer() || associated <= 0)
                throw std::runtime_error("Malformed archive release association");
            if (associated == option.release_id) contains_release = true;
        }
        if (!contains_release || !observed.empty())
            throw std::runtime_error("Unexpected native archive event identity/count");
        auto compact = event;
        compact.erase("cover_payload");
        observed.push_back(std::move(compact));
    }));
    if (!completed.is_object() || !completed.contains("events") ||
        completed.at("events") != observed || observed.size() != 1 ||
        !completed.contains("attempted") || completed.at("attempted") != 1 ||
        !completed.contains("selected") || completed.at("selected") != 1 ||
        !completed.contains("cancelled") || completed.at("cancelled") != false ||
        !completed.contains("rate_limited") || completed.at("rate_limited") != false)
        throw std::runtime_error("Native repair archive dispatch incomplete or cancelled");
    const auto& event = observed.at(0);
    if (event.at("ok") == true) {
        if (!event.contains("archive_http") || !event.at("archive_http").is_object() ||
            !event.at("archive_http").contains("body_bytes") ||
            !event.at("archive_http").at("body_bytes").is_number_integer() ||
            event.at("archive_http").at("body_bytes") < 0)
            throw std::runtime_error("Missing native repair HTTP accounting");
        emit("  " + std::to_string(option.release_id) + "   Native archive metadata committed  •  " +
             search_frontend::printable_size(event.at("archive_http").at("body_bytes").get<std::uint64_t>()) + " read");
    }
    return event;
}

template<class Submit, class ReadListing, class Emit>
void repair(const Options& option, Submit submit, ReadListing read_listing, Emit emit) {
    check_options(option);
    if (option.root.empty()) throw std::runtime_error("Native repair publication root missing");
    if (!read_listing(option.release_id).is_object())
        throw std::runtime_error("Release is missing from this catalog; search first");
    bool refreshed = false;
    const auto plan = [&](std::string excluded) {
        return checked_copy(submit(Json{{"operation", "archive_download_plan"},
            {"db", option.db}, {"id", option.release_id}, {"exclude", std::move(excluded)}}, {}),
            option.release_id);
    };
    auto copy = plan("");
    if (copy.is_null()) {
        refresh_offer_once(option, submit, refreshed, false);
        copy = plan("");
    }
    if (copy.is_null())
        throw std::runtime_error("No supported current archive offer for repair");
    auto event = probe(option, copy, submit, emit);
    const auto error = event.at("error").is_string()
        ? event.at("error").template get<std::string>() : std::string{};
    if (event.at("ok") == false && copy.at("host") == "mediafire.com" &&
        error.find("HTTP 403") != std::string::npos && !refreshed) {
        emit("  " + std::to_string(option.release_id) +
             "   MediaFire share returned HTTP 403; checking current offer");
        refresh_offer_once(option, submit, refreshed, false);
        const auto alternative = plan(copy.at("url").template get<std::string>());
        if (alternative.is_null() || alternative.at("id") == copy.at("id"))
            throw std::runtime_error("MediaFire 403; no distinct replacement offer was found");
        copy = alternative;
        event = probe(option, copy, submit, emit);
    }
    if (event.at("ok") != true)
        throw std::runtime_error("Native archive metadata repair could not complete");

    const auto published = submit(Json{{"operation", "publish_many"}, {"db", option.db},
        {"ids", Json::array({option.release_id})}, {"output", option.output},
        {"root", option.root}, {"copy_id", copy.at("id")}}, {});
    if (!published.is_object() || !published.contains("notifications") ||
        !published.at("notifications").is_array() || published.at("notifications").empty() ||
        !published.contains("references") || !published.at("references").is_number_integer() ||
        published.at("references") != published.at("notifications").size() ||
        !published.contains("written") || !published.at("written").is_number_integer() ||
        published.at("written") < 0 || published.at("written") > published.at("references"))
        throw std::runtime_error("Incomplete native repair reference publication");
    std::vector<std::string> notices;
    for (const auto& notice : published.at("notifications")) {
        if (!notice.is_array() || notice.size() != 3 || notice.at(0) != option.release_id ||
            !notice.at(1).is_string() || !notice.at(2).is_boolean())
            throw std::runtime_error("Invalid native repair reference notification");
        const auto& filename = notice.at(1).template get_ref<const std::string&>();
        if (filename.empty() || filename.size() > 1024 ||
            filename.find_first_of("\r\n\t") != filename.npos ||
            filename.find('\0') != filename.npos)
            throw std::runtime_error("Unsafe native repair publication filename");
        notices.push_back("\x1c" + std::to_string(option.release_id) + "\t" + filename);
    }
    for (const auto& line : notices) emit(line);
    emit("Repair complete  •  " + published.at("written").dump() +
         " references updated, " + std::to_string(notices.size()) + " queued for reload");
}

} // namespace doujin::explicit_job
