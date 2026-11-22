#pragma once
// Advisory-only robots.txt interpretation for the public catalog homepage.
// This does not authorize crawling. Network routing, pacing and explicit user
// initiation are enforced independently; ambiguous directives are never
// represented as an unqualified allow.
#include "http_user_agent.hpp"
#include <algorithm>
#include <cctype>
#include <string>
#include <string_view>
#include <vector>

namespace doujin::robots_advisory {
inline std::string trim(std::string_view value) {
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front()))) value.remove_prefix(1);
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back()))) value.remove_suffix(1);
    return std::string(value);
}
inline std::string lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return text;
}
struct Verdict { bool excluded = false; bool ambiguous = false; };
// Evaluate simple prefix directives (the common case), with longest rule and
// allow-on-equal precedence. A wildcard or '$' cannot be precisely evaluated
// here; report ambiguity rather than claiming policy compliance.
inline Verdict inspect(std::string_view text, std::string_view path = "/?p=home&page=0") {
    struct Rule { std::string value; bool allow; };
    struct Group { bool exact = false, wildcard = false; std::vector<Rule> rules; };
    std::vector<Group> groups;
    bool agents = false, rules = false;
    Group current;
    auto finish = [&] {
        if (agents) groups.push_back(std::move(current));
        current = {}; agents = false; rules = false;
    };
    while (!text.empty()) {
        const auto end = text.find('\n');
        const auto line = trim(text.substr(0, end));
        text.remove_prefix(end == text.npos ? text.size() : end + 1);
        if (line.empty()) { if (rules) finish(); continue; }
        const auto comment = line.find('#');
        const auto clean = trim(std::string_view(line).substr(0, comment));
        const auto colon = clean.find(':');
        if (colon == std::string::npos) continue;
        const auto key = lower(trim(std::string_view(clean).substr(0, colon)));
        const auto value = trim(std::string_view(clean).substr(colon + 1));
        if (key == "user-agent") {
            if (rules) finish();
            agents = true;
            const auto normalized = lower(value);
            if (normalized == "*") current.wildcard = true;
            else if (!normalized.empty() && doujin::robots_user_agent.find(normalized) != std::string::npos)
                current.exact = true;
        } else if (agents && (key == "allow" || key == "disallow")) {
            rules = true;
            current.rules.push_back({value, key == "allow"});
        }
    }
    finish();
    const bool exact_exists = std::any_of(groups.begin(), groups.end(), [](const Group& group) {return group.exact;});
    Verdict result;
    std::size_t best = 0;
    bool matched = false, permitted = true;
    for (const auto& group : groups) {
        if (!(exact_exists ? group.exact : group.wildcard)) continue;
        for (const auto& rule : group.rules) {
            if (rule.value.empty()) continue; // Empty Disallow never blocks.
            if (rule.value.find_first_of("*$") != std::string::npos) {
                result.ambiguous = true;
                continue;
            }
            if (!path.starts_with(rule.value)) continue;
            if (!matched || rule.value.size() > best || (rule.value.size() == best && rule.allow)) {
                matched = true; best = rule.value.size(); permitted = rule.allow;
            }
        }
    }
    result.excluded = matched && !permitted;
    return result;
}
} // namespace doujin::robots_advisory
