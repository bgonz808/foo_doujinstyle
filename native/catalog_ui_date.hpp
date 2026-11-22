#pragma once
#include "site_discovery.hpp"
#include "descriptor.hpp"
#include "third_party/json.hpp"
#include <set>
#include <array>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::ui_date {
using Json = nlohmann::json;
inline std::string text(const Json& object, std::string_view key) {
    if (!object.is_object()) return {};
    auto it = object.find(std::string(key));
    return it != object.end() && it->is_string() ? it->get<std::string>() : std::string{};
}
inline Json choose(const Json& release) {
    if (!release.is_object()) throw std::runtime_error("Invalid UI release record");
    auto nested = release.value("external_lookup", Json::object());
    if (!nested.is_object()) nested = Json::object();
    nested = nested.value("bangumi", Json::object());
    if (!nested.is_object()) nested = Json::object();
    auto external = nested.value("fields", Json::object());
    if (!external.is_object()) external = Json::object();
    for (const auto& [value, source] : {std::pair{text(release, "release_date"), "DoujinStyle release date"},
                                       std::pair{text(external, "release_date"), "Bangumi"}}) {
        if (const auto date = format_release_date(value); !date.empty())
            return {{"date", date}, {"source", source}};
    }
    std::set<std::string> ids;
    for (const Json* group : std::array<const Json*, 2>{&release, &external}) {
        if (!group->contains("events") || !group->at("events").is_array()) continue;
        for (const auto& entry : group->at("events")) {
            const auto id = text(entry, "id");
            if (!id.empty()) ids.insert(id);
        }
    }
    if (release.contains("tags") && release.at("tags").is_array()) {
        for (const auto& parsed : discovery::events_for(release.at("tags"))) {
            const auto id = text(parsed, "id");
            if (!id.empty()) ids.insert(id);
        }
    }
    if (ids.size() == 1) {
        const auto& id = *ids.begin();
        for (const auto& item : discovery::events_for(Json::array({id}))) {
            const auto start = text(item, "start");
            const auto end = text(item, "end");
            if (!start.empty() && start == end)
                return {{"date", start}, {"source", "Release event " + id}};
        }
    }
    if (auto date = format_release_date(text(release, "listed_at")); !date.empty())
        return {{"date", date}, {"source", "DoujinStyle date added"}};
    return {{"date", ""}, {"source", ""}};
}
}
