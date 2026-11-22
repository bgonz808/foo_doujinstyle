#pragma once

// One resumable, bounded native browse step. No SQLite write lock or cursor
// survives network I/O. Page and checkpoint are committed atomically.
#include "catalog_ingest.hpp"
#include "site_discovery.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <set>
#include <stop_token>
#include <string>
#include <string_view>

namespace doujin::browse_step {
using Json = doujin::Json;
inline constexpr std::size_t max_relocations = 8;
inline constexpr std::string_view home = "https://doujinstyle.com/?p=home&page=0";

inline std::int64_t offset(const std::string& url) {
    if (!discovery::site_url(url) || discovery::parameter(url, "p") != "home")
        throw std::runtime_error("Invalid idle browse URL");
    const auto text = discovery::parameter(url, "page");
    const auto parsed = discovery::integer(text);
    if (!parsed || text.size() > 6 || *parsed < 0 || *parsed > 999999)
        throw std::runtime_error("Idle browse offset out of range");
    return *parsed;
}
inline Json checkpoint(sqlite3* db) {
    auto row = query(db, "SELECT snapshot FROM backfill_checkpoint WHERE id=1");
    if (!next(row.get())) return nullptr;
    auto result = Json::parse(string_column(row.get(), 0), nullptr, false);
    if (!result.is_object()) throw std::runtime_error("Invalid stored browse checkpoint");
    return result;
}
inline Json state(const std::string& target, std::string referrer, std::string policy,
                  Json anchor = nullptr, std::size_t relocations = 0) {
    (void)offset(target);
    if (!referrer.empty() && !discovery::site_url(referrer))
        throw std::runtime_error("Invalid idle browse referrer");
    if (policy.size() > 64 || relocations > max_relocations)
        throw std::runtime_error("Invalid idle browse policy or relocation budget");
    if (!anchor.is_null()) {
        if (!anchor.is_array() || anchor.size() > 20)
            throw std::runtime_error("Invalid idle browse anchor");
        std::set<std::int64_t> unique;
        for (const auto& item : anchor)
            if (!item.is_number_integer() ||
                (item.is_number_unsigned() && item.get<std::uint64_t>() >
                    static_cast<std::uint64_t>(INT64_MAX)) ||
                item.get<std::int64_t>() <= 0 || !unique.insert(item.get<std::int64_t>()).second)
                throw std::runtime_error("Invalid idle browse anchor IDs");
    }
    return {{"target", target}, {"referrer", std::move(referrer)}, {"policy", std::move(policy)},
            {"anchor", std::move(anchor)}, {"relocations", relocations}};
}

// Fetch(url, referrer, policy) returns {HTML bytes, HTTP Referrer-Policy}.
// A test fetch can supply fixture pages; Windows supplies bounded WinHTTP.
template<class Fetch>
Json run(sqlite3* db, std::int64_t now, const Json& requested, Fetch fetch,
         std::stop_token stop = {}) {
    if (stop.stop_requested()) throw std::runtime_error("Native browse cancelled");
    if (now < 0) throw std::runtime_error("Invalid browse observation timestamp");
    Json current;
    if (requested.is_null()) {
        auto saved = checkpoint(db);
        if (saved.is_object()) {
            if (saved.value("complete", false))
                return {{"done", true}, {"state", nullptr}, {"message", "Idle backfill already reached the newest page."}};
            current = state(saved.at("url").get<std::string>(), saved.value("referrer", ""),
                saved.value("referrer_policy", ""), saved.at("ids"));
        } else {
            const auto body = fetch(std::string(home), "", "");
            const auto links = discovery::browse_links(body.first, home, body.second);
            if (links.empty()) throw std::runtime_error("No observed DoujinStyle navigation links");
            std::int64_t last = 0;
            for (auto it = links.begin(); it != links.end(); ++it)
                last = std::max(last, static_cast<std::int64_t>(std::stoll(it.key())));
            const auto selected = links.at(std::to_string(last));
            current = state(selected.at(0).template get<std::string>(), std::string(home),
                            selected.at(1).template get<std::string>());
            if (last != 0) return {{"done", false}, {"state", std::move(current)},
                                    {"message", "Located the oldest observed browse page."}};
            // Reuse the fetched head on the single-page case.
            const auto page = discovery::listing(body.first, 20, true);
            auto ids = Json::array();
            for (const auto& row : page.at("releases")) ids.push_back(row.at("id"));
            if (stop.stop_requested()) throw std::runtime_error("Native browse cancelled before commit");
            ingest(db, "page", {{"url", std::string(home)}, {"page", page}, {"now", now},
                {"checkpoint", {{"url", std::string(home)}, {"referrer", ""}, {"ids", ids},
                    {"referrer_policy", body.second}, {"complete", true}}}, {"seeds", Json::array()}});
            return {{"done", true}, {"state", nullptr}, {"saved", 0},
                {"message", "Idle backfill reached the newest page."}};
        }
    } else {
        if (!requested.is_object()) throw std::runtime_error("Malformed idle browse state");
        current = state(requested.at("target").get<std::string>(),
            requested.at("referrer").get<std::string>(), requested.at("policy").get<std::string>(),
            requested.value("anchor", Json()), requested.at("relocations").get<std::size_t>());
    }
    const auto target = current.at("target").get<std::string>();
    const auto referrer = current.at("referrer").get<std::string>();
    const auto policy = current.at("policy").get<std::string>();
    const auto page_offset = offset(target);
    const auto response = fetch(target, referrer, policy);
    const auto page = discovery::listing(response.first, 20, true);
    const auto links = discovery::browse_links(response.first, target, response.second);
    auto ids = Json::array();
    for (const auto& row : page.at("releases")) ids.push_back(row.at("id"));
    const auto anchor = current.at("anchor");
    if (anchor.is_array()) {
        std::set<std::int64_t> prior;
        for (const auto& id : anchor) {
            if (!id.is_number_integer() || id <= 0) throw std::runtime_error("Invalid browse checkpoint anchor ID");
            prior.insert(id.get<std::int64_t>());
        }
        Json overlap = Json::array(), ordered = Json::array();
        for (const auto& id : ids) if (prior.contains(id.get<std::int64_t>())) overlap.push_back(id);
        for (const auto& id : anchor) if (std::find(overlap.begin(), overlap.end(), id) != overlap.end()) ordered.push_back(id);
        if (overlap.size() < std::min<std::size_t>(3, prior.size()) || overlap != ordered) {
            const auto moves = current.at("relocations").get<std::size_t>();
            if (moves >= max_relocations || ids.empty() || prior.empty())
                throw std::runtime_error("Browse checkpoint relocation budget exhausted");
            std::int64_t previous = INT64_MAX;
            for (const auto& id : ids) {
                const auto value = id.get<std::int64_t>();
                if (value >= previous) throw std::runtime_error("Browse page ordering invalid during relocation");
                previous = value;
            }
            std::int64_t min_id = ids.front().get<std::int64_t>();
            std::int64_t max_id = ids.front().get<std::int64_t>();
            for (const auto& id : ids) {
                min_id = std::min(min_id, id.get<std::int64_t>());
                max_id = std::max(max_id, id.get<std::int64_t>());
            }
            const auto direction = min_id > *prior.rbegin() ? 1 : max_id < *prior.begin() ? -1 : 0;
            if (!direction || page_offset + direction < 0 || page_offset + direction > 999999 ||
                !links.contains(std::to_string(page_offset + direction)))
                throw std::runtime_error("Browse checkpoint overlap too small to relocate");
            const auto neighbor = links.at(std::to_string(page_offset + direction));
            return {{"done", false}, {"state", state(neighbor.at(0).template get<std::string>(), target,
                neighbor.at(1).template get<std::string>(), anchor, moves + 1)},
                {"message", "Relocating saved browse checkpoint."}};
        }
    }
    const auto complete = page_offset == 0;
    if (!complete && !links.contains(std::to_string(page_offset - 1)))
        throw std::runtime_error("Observed navigation does not include the next newer page");
    const auto next = complete ? Json() : links.at(std::to_string(page_offset - 1));
    if (stop.stop_requested()) throw std::runtime_error("Native browse cancelled before commit");
    ingest(db, "page", {{"url", target}, {"page", page}, {"now", now},
        {"checkpoint", {{"url", target}, {"referrer", referrer}, {"ids", ids},
            {"referrer_policy", policy}, {"complete", complete}}}, {"seeds", Json::array()}});
    return {{"done", complete}, {"saved", page_offset}, {"rows", page.at("rows_on_page")},
            {"state", complete ? Json() : state(next.at(0).template get<std::string>(), target,
                next.at(1).template get<std::string>())},
            {"message", complete ? "Idle backfill reached the newest page." : "Idle catalog page saved."}};
}
} // namespace doujin::browse_step
