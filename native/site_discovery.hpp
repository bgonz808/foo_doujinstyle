#pragma once

#include "catalog_html.hpp"
#include "third_party/json.hpp"
#include <array>
#include <optional>
#include <set>

namespace doujin::discovery {
using Json = nlohmann::ordered_json;
inline constexpr std::string_view origin = "https://doujinstyle.com";
inline constexpr std::string_view guides = "https://doujinstyle.com/?p=guides";

inline bool site_url(std::string_view url) {
    for (const auto host : {"https://doujinstyle.com", "https://www.doujinstyle.com"}) {
        const auto length = std::string_view(host).size();
        if (!url.starts_with(host)) continue;
        auto tail = url.substr(length);
        if (tail.starts_with(":443")) tail.remove_prefix(4);
        if (tail.empty() || tail.starts_with('/') || tail.starts_with('?'))
            return url.find_first_of("\\\r\n\t") == url.npos && url.find('\0') == url.npos;
    }
    return false;
}

inline std::string resolve_url(std::string_view base, std::string_view target) {
    if (target.empty()) return std::string(base);
    if (target.starts_with("//")) return "https:" + std::string(target);
    if (target.find(':') < target.find_first_of("/?#")) return std::string(target);
    base = base.substr(0, base.find_first_of("?#"));
    const auto slash = base.find('/', base.find("://") + 3);
    if (target.starts_with('?')) return std::string(base) + std::string(target);
    const auto host = base.substr(0, slash);
    std::string path;
    if (target.starts_with('/')) path = target;
    else path = (slash == base.npos ? "/" : std::string(base.substr(slash, base.rfind('/') - slash + 1))) + std::string(target);
    const auto suffix = path.find_first_of("?#");
    auto pieces = std::string_view(path).substr(0, suffix);
    std::vector<std::string_view> components;
    while (!pieces.empty()) {
        if (pieces.starts_with('/')) pieces.remove_prefix(1);
        const auto next = pieces.find('/');
        const auto name = pieces.substr(0, next);
        if (name == "..") {
            if (!components.empty()) components.pop_back();
        } else if (name != ".") {
            components.push_back(name);
        }
        if (next == pieces.npos) break;
        pieces.remove_prefix(next);
    }
    std::string output(host);
    for (const auto& part : components) { output += '/'; output += part; }
    if (components.empty()) output += '/';
    if (suffix != path.npos) output += std::string_view(path).substr(suffix);
    return output;
}

inline std::string_view parameter(std::string_view url, std::string_view name) {
    const auto start = url.find('?');
    if (start == url.npos) return {};
    url.remove_prefix(start + 1);
    url = url.substr(0, url.find('#'));
    std::optional<std::string_view> result;
    while (!url.empty()) {
        const auto end = url.find('&');
        const auto pair = url.substr(0, end);
        const auto equals = pair.find('=');
        if (equals != pair.npos && pair.substr(0, equals) == name) {
            if (result) return {};
            result = pair.substr(equals + 1);
        }
        if (end == url.npos) break;
        url.remove_prefix(end + 1);
    }
    return result.value_or(std::string_view{});
}

inline std::optional<std::int64_t> integer(std::string_view value) {
    if (value.empty() || value.find_first_not_of("0123456789") != value.npos) return {};
    std::int64_t result = 0;
    const auto read = std::from_chars(value.data(), value.data() + value.size(), result);
    return read.ec == std::errc{} && read.ptr == value.data() + value.size() ? std::optional(result) : std::nullopt;
}

inline std::optional<std::int64_t> release_id(std::string_view href) {
    if (parameter(href, "p") != "page" || parameter(href, "type") != "1") return {};
    if (!site_url(resolve_url(origin, href))) return {};
    return integer(parameter(href, "id"));
}

inline std::string release_url(std::int64_t id) {
    return std::string(origin) + "/?p=page&type=1&id=" + std::to_string(id);
}

inline Json text_or_null(std::string value) { return value.empty() ? Json() : Json(std::move(value)); }

inline Json events_for(const Json& tags) {
    constexpr std::array dates{
        std::array{"M3-44", "2019-10-27", "2019-10-27", "https://www.m3net.jp/diarypro/index.cgi?no=141"},
        std::array{"M3-54", "2024-10-27", "2024-10-27", "https://www.m3net.jp/diarypro/index.cgi?date=20240522"},
        std::array{"C108", "2026-08-15", "2026-08-16", "https://comiket.co.jp/info-a/TAFO/C108TAFO/index.html"},
        std::array{"C109", "2026-12-29", "2026-12-31", "https://comiket.co.jp/info-a/TAFO/C109TAFO/index.html"},
        std::array{"M3-56", "2025-10-26", "2025-10-26", "https://www.m3net.jp/diarypro/index.cgi?no=268"},
        std::array{"M3-57", "2026-04-26", "2026-04-26", "https://www.m3net.jp/diarypro/index.cgi?no=275"},
        std::array{"M3-58", "2026-10-25", "2026-10-25", "https://www.m3net.jp/diarypro/index.cgi?no=280"},
        std::array{"RTS19", "2022-05-08", "2022-05-08", "https://reitaisai.com/rts19/"},
        std::array{"RTS22", "2025-05-05", "2025-05-05", "https://reitaisai.com/rts22/"},
        std::array{"RTS23", "2026-05-04", "2026-05-04", "https://reitaisai.com/rts23/"},
    };
    auto result = Json::array();
    for (const auto& tag : tags) {
        auto text = std::string_view(tag.get_ref<const std::string&>());
        std::string normalized;
        while (!text.empty()) {
            if (text.size() >= 3 && text.starts_with("\xef\xbc") && static_cast<unsigned char>(text[2]) >= 0x81 &&
                static_cast<unsigned char>(text[2]) <= 0xbf) {
                normalized += static_cast<char>(static_cast<unsigned char>(text[2]) - 0x60);
                text.remove_prefix(3);
            } else if (text.size() >= 3 && text.starts_with("\xef\xbd") &&
                static_cast<unsigned char>(text[2]) >= 0x80 && static_cast<unsigned char>(text[2]) <= 0x9e) {
                normalized += static_cast<char>(static_cast<unsigned char>(text[2]) - 0x20);
                text.remove_prefix(3);
            } else {
                normalized += text.front();
                text.remove_prefix(1);
            }
        }
        const auto code = release_event_from_tag(collapse_html_text(normalized));
        if (code.empty()) continue;
        const auto match = std::find_if(dates.begin(), dates.end(), [&](const auto& entry) { return code == entry[0]; });
        Json event{{"id", code}, {"tag", tag}, {"start", nullptr}, {"end", nullptr}, {"date_source", nullptr}};
        if (match != dates.end()) {
            event["start"] = (*match)[1];
            event["end"] = (*match)[2];
            event["date_source"] = (*match)[3];
        }
        result.push_back(std::move(event));
    }
    return result;
}

inline Json release_row(std::int64_t id, std::string title, std::string artist, Json tags, std::string format) {
    const auto events = events_for(tags);
    return {{"id", id}, {"url", release_url(id)}, {"album", std::move(title)}, {"artist", text_or_null(std::move(artist))},
        {"album_artist", nullptr}, {"publisher", nullptr}, {"catalog_id", nullptr}, {"release_date", nullptr},
        {"tags", std::move(tags)}, {"events", events}, {"technical", {{"advertised_format", text_or_null(std::move(format))}}},
        {"evidence", {{"album", "DoujinStyle"}, {"artist", "DoujinStyle listing credit"}}}};
}

inline Json site_icon(const CatalogHtml& doc) {
    const auto head = doc.first(0, [&](auto i) { return doc.nodes[i].name == "head"; });
    if (!head) return nullptr;
    for (auto i = head + 1; i < doc.nodes[head].end; ++i) {
        if (doc.nodes[i].name != "link" || !html_token(doc.attr(i, "rel"), "icon") || doc.attr(i, "href").empty())
            continue;
        auto url = resolve_url(origin, doc.attr(i, "href"));
        if (const auto fragment = url.find('#'); fragment != url.npos) url.resize(fragment);
        if (site_url(url)) return url;
    }
    return nullptr;
}

inline Json forms(const CatalogHtml& doc, std::size_t scope, std::int64_t id,
                  std::string_view page = {}, std::string_view policy = {}) {
    std::map<std::int64_t, Json> sorted;
    for (auto i = scope + 1; i < doc.nodes[scope].end; ++i) {
        if (doc.nodes[i].name != "form") continue;
        auto data = Json::object();
        std::size_t button = 0;
        bool has_button = false;
        for (auto j = i + 1; j < doc.nodes[i].end; ++j) {
            const auto name = doc.attr(j, "name");
            if (name == "download_link") {
                has_button = true;
                if (!button && !doc.nodes[j].attributes.contains("disabled")) button = j;
            }
            if (doc.nodes[j].name == "input" && doc.attr(j, "type") == "hidden" &&
                (name == "id" || name == "type" || name == "source")) data[name] = doc.attr(j, "value");
        }
        if (!has_button || data.value("id", "") != std::to_string(id) || data.value("type", "") != "1") continue;
        const auto slot = integer(data.value("source", "0"));
        if (!slot) continue;
        if (!page.empty()) {
            if (!button) continue;
            auto action = doc.attr(button, "formaction");
            if (action.empty()) action = doc.attr(i, "action");
            auto method = doc.attr(button, "formmethod");
            if (method.empty()) method = doc.attr(i, "method");
            data["action"] = resolve_url(page, action);
            data["document_url"] = page;
            data["method"] = method.empty() ? "get" : lower_ascii(std::string(method));
            data["button_value"] = doc.attr(button, "value");
            data["referrer_policy"] = policy;
        }
        sorted.try_emplace(*slot, std::move(data));
    }
    auto output = Json::array();
    for (auto& [slot, entry] : sorted) output.push_back(std::move(entry));
    return output;
}

inline std::optional<std::int64_t> comma_number(std::string_view value) {
    std::string number(value);
    std::erase(number, ',');
    return integer(number);
}

// A bounded, read-only projection of observed pagination links.  Neither the
// idle worker nor the transport should derive unobserved crawl destinations.
inline Json browse_links(std::string_view body, std::string_view page_url, std::string_view header_policy) {
    if (!site_url(page_url) || parameter(page_url, "p") != "home")
        throw std::runtime_error("Invalid DoujinStyle browse page URL");
    const auto current = parameter(page_url, "page");
    if (current.size() > 6 || (!current.empty() && !integer(current)))
        throw std::runtime_error("Invalid DoujinStyle browse page offset");
    const CatalogHtml doc(body);
    std::string policy(referrer_policy(header_policy));
    for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
        if (doc.nodes[i].name == "meta" && lower_ascii(std::string(doc.attr(i, "name"))) == "referrer")
            if (const auto next = referrer_policy(doc.attr(i, "content")); !next.empty()) policy = next;
    }
    Json result = Json::object();
    result[std::string(current.empty() ? "0" : current)] = Json::array({page_url, policy});
    for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
        if (doc.nodes[i].name != "a" || doc.attr(i, "href").empty()) continue;
        bool in_navigation = false;
        for (auto parent = doc.nodes[i].parent; parent != 0; parent = doc.nodes[parent].parent)
            if (doc.has_class(parent, "pageScroll")) { in_navigation = true; break; }
        if (!in_navigation) continue;
        const auto url = resolve_url(page_url, doc.attr(i, "href"));
        if (!url.starts_with("https://doujinstyle.com/")) continue;
        const auto page = parameter(url, "page");
        if (parameter(url, "p") != "home" || page.empty() || page.size() > 6 || !integer(page)) continue;
        auto link_policy = std::string(policy);
        if (html_token(lower_ascii(std::string(doc.attr(i, "rel"))), "noreferrer"))
            link_policy = "no-referrer";
        else if (const auto selected = referrer_policy(doc.attr(i, "referrerpolicy")); !selected.empty())
            link_policy = std::string(selected);
        result[std::to_string(*integer(page))] = Json::array({url, link_policy});
    }
    return result;
}

inline Json listing(std::string_view body, std::size_t limit, bool include_forms) {
    const CatalogHtml doc(body);
    auto rows = Json::array();
    std::size_t count = 0;
    for (std::size_t card = 1; card < doc.nodes.size(); ++card) {
        if (!doc.has_class(card, "gridBox")) continue;
        const auto details = doc.first(card, [&](auto i) { return doc.has_class(i, "gridDetails"); });
        if (!details) continue;
        std::optional<std::int64_t> id;
        const auto title = doc.first(details, [&](auto i) {
            if (doc.nodes[i].name != "a") return false;
            id = release_id(doc.attr(i, "href"));
            return id.has_value();
        });
        if (!id) continue;
        ++count;
        if (rows.size() >= limit) continue;
        const auto artist = doc.first(details, [&](auto i) {
            return doc.nodes[i].name == "a" && parameter(doc.attr(i, "href"), "type") == "artist";
        });
        auto tags = Json::array();
        for (auto i = details + 1; i < doc.nodes[details].end; ++i)
            if (doc.nodes[i].name == "a" && parameter(doc.attr(i, "href"), "type") == "tags")
                tags.push_back(doc.clean(i));
        const auto format = doc.first(card, [&](auto i) { return doc.attr(i, "title").starts_with("Music Format:"); });
        auto row = release_row(*id, doc.clean(title), artist ? doc.clean(artist) : "", std::move(tags),
                               format ? doc.clean(format) : "");
        if (include_forms) row["download_forms"] = forms(doc, card, *id);
        rows.push_back(std::move(row));
    }
    std::optional<std::int64_t> total;
    constexpr std::string_view label = "Total Results Found:";
    const auto text = doc.clean(0);
    if (const auto at = text.find(label); at != text.npos) {
        auto value = std::string_view(text).substr(at + label.size());
        while (!value.empty() && attr_space(value.front())) value.remove_prefix(1);
        total = comma_number(value.substr(0, value.find_first_not_of("0123456789,")));
    }
    if (!count && doc.first(0, [&](auto i) {
            return doc.nodes[i].name == "mainbar" && doc.first(i, [&](auto child) {
                return doc.nodes[child].name.empty() && collapse_html_text(doc.nodes[child].text) == "No Matches Found.";
            });
        })) total = 0;
    if (!count && total != 0) throw std::runtime_error("DoujinStyle returned no recognizable catalog cards");
    Json result{{"total_results", total ? Json(*total) : Json()}, {"rows_on_page", count}, {"releases", std::move(rows)}};
    if (auto icon = site_icon(doc); !icon.is_null()) result["site_icon"] = std::move(icon);
    return result;
}

inline std::string_view period(std::string label) {
    label = lower_ascii(std::move(label));
    if (label == "this month") return "month";
    if (label == "all-time" || label == "all time") return "all";
    return {};
}

inline std::optional<std::int64_t> download_count(std::string_view text) {
    const auto lower = lower_ascii(std::string(text));
    for (auto word = lower.find(" download"); word != lower.npos; word = lower.find(" download", word + 1)) {
        auto boundary = word + std::string_view(" download").size();
        if (boundary < lower.size() && lower[boundary] == 's') ++boundary;
        if (boundary < lower.size() && std::string_view("abcdefghijklmnopqrstuvwxyz0123456789_").find(lower[boundary]) !=
                                       std::string_view::npos) continue;
        auto begin = word;
        while (begin && std::string_view("0123456789,").find(lower[begin - 1]) != lower.npos) --begin;
        if (const auto count = comma_number(std::string_view(lower).substr(begin, word - begin))) return count;
    }
    return {};
}

inline Json charts(std::string_view body) {
    const CatalogHtml doc(body);
    std::map<std::size_t, std::string_view> headings;
    for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
        if (!html_token("h1 h2 h3 h4 h5 h6", doc.nodes[i].name)) continue;
        if (const auto range = period(doc.clean(i)); !range.empty()) headings.emplace(i, range);
    }
    if (headings.size() < 2) {
        headings.clear();
        for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
            if (doc.nodes[i].name.empty() || doc.first(i, [&](auto j) { return !doc.nodes[j].name.empty(); })) continue;
            if (const auto range = period(doc.clean(i)); !range.empty()) headings.emplace(i, range);
        }
    }
    std::vector<unsigned> anchors(doc.nodes.size());
    for (auto i = doc.nodes.size(); i-- > 1;) {
        if (doc.nodes[i].name == "a" && release_id(doc.attr(i, "href"))) ++anchors[i];
        anchors[doc.nodes[i].parent] += anchors[i];
    }
    auto rows = Json{{"month", Json::array()}, {"all", Json::array()}};
    std::string_view active;
    for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
        if (const auto found = headings.find(i); found != headings.end()) active = found->second;
        if (active.empty() || doc.nodes[i].name != "a" || rows[active].size() >= 10) continue;
        const auto id = release_id(doc.attr(i, "href"));
        if (!id) continue;
        const auto title = doc.clean(i);
        if (title.empty()) continue;
        for (auto j = doc.nodes[i].parent; j && anchors[j] <= 1; j = doc.nodes[j].parent) {
            std::string words, artist;
            for (auto k = j + 1; k < doc.nodes[j].end; ++k) {
                const auto fragment = collapse_html_text(doc.nodes[k].text);
                if (fragment.empty()) continue;
                if (!words.empty()) words += ' ';
                words += fragment;
                if (k >= doc.nodes[i].end && artist.empty() &&
                    lower_ascii(fragment).find("download") == fragment.npos && !comma_number(fragment)) artist = fragment;
            }
            const auto downloads = download_count(words);
            if (!downloads) continue;
            auto& rankings = rows[active];
            if (std::none_of(rankings.begin(), rankings.end(), [&](const Json& previous) { return previous["id"] == *id; })) {
                auto row = release_row(*id, title, std::move(artist), Json::array(), "");
                row["chart_rank"] = rankings.size() + 1;
                row["chart_downloads"] = *downloads;
                rankings.push_back(std::move(row));
            }
            break;
        }
    }
    if (rows["month"].empty() || rows["all"].empty())
        throw std::runtime_error("DoujinStyle did not return both recognizable popularity charts");
    return {{"charts", std::move(rows)}, {"site_icon", site_icon(doc)}, {"releases", Json::array()}};
}

inline std::string site_date(std::string value) {
    std::erase(value, '.');
    for (std::size_t i = 1; i + 1 < value.size(); ++i) {
        const auto end = i + 2;
        if (value[i - 1] < '0' || value[i - 1] > '9' ||
            (end < value.size() && ((value[end] >= 'a' && value[end] <= 'z') ||
                                    (value[end] >= 'A' && value[end] <= 'Z') ||
                                    (value[end] >= '0' && value[end] <= '9') || value[end] == '_'))) continue;
        if (html_token("st nd rd th", std::string_view(value).substr(i, 2))) value.erase(i, 2);
    }
    if (value.size() < 8 || value[3] != ' ') return value;
    constexpr std::array<std::string_view, 12> months{"jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"};
    const auto month = std::find(months.begin(), months.end(), lower_ascii(value.substr(0, 3)));
    const auto comma = value.find(',');
    if (month == months.end() || comma == value.npos || comma < 5 || comma + 6 != value.size() || value[comma + 1] != ' ')
        return value;
    const auto day = integer(std::string_view(value).substr(4, comma - 4));
    const auto year = std::string_view(value).substr(comma + 2);
    if (!day || !integer(year)) return value;
    auto formatted = format_release_date(std::string(year) + "-" + std::to_string(month - months.begin() + 1) +
                                         "-" + std::to_string(*day));
    return formatted.empty() ? value : formatted;
}

inline Json detail(std::string_view body, std::int64_t id, const std::string& url, std::string policy) {
    const CatalogHtml doc(body);
    const auto main = doc.first(0, [&](auto i) { return doc.nodes[i].name == "mainbar"; });
    const auto heading = main ? doc.first(main, [&](auto i) { return doc.nodes[i].name == "h2"; }) : 0;
    const auto title = heading ? doc.clean(heading) : "";
    if (title.empty()) throw std::runtime_error("DoujinStyle did not return a recognizable release page");
    std::map<std::string, std::size_t> fields;
    for (auto label = main + 1; label < doc.nodes[main].end; ++label) {
        if (!doc.has_class(label, "pageSpan1")) continue;
        const auto parent = doc.nodes[label].parent;
        auto next = doc.nodes[label].end;
        while (next < doc.nodes[parent].end && doc.nodes[next].name.empty()) ++next;
        if (next >= doc.nodes[parent].end || !doc.has_class(next, "pageSpan2")) continue;
        auto name = lower_ascii(doc.clean(label));
        while (name.ends_with(':')) name.pop_back();
        fields.insert_or_assign(std::move(name), next);
    }
    const auto value = [&](const char* field) {
        const auto at = fields.find(field);
        return at == fields.end() ? std::string{} : doc.clean(at->second);
    };
    auto tags = Json::array();
    if (const auto tag = fields.find("tags"); tag != fields.end()) {
        for (auto i = tag->second + 1; i < doc.nodes[tag->second].end; ++i)
            if (doc.nodes[i].name == "a") tags.push_back(doc.clean(i));
    }
    auto row = release_row(id, title, value("artist"), std::move(tags), value("format"));
    row["listed_at"] = text_or_null(site_date(value("date added")));
    row["updated_at"] = text_or_null(site_date(value("last updated")));
    for (const auto& [label, index] : fields) {
        auto key = label;
        std::erase_if(key, [](char c) { return c < 'a' || c > 'z'; });
        const char* field = nullptr;
        if (key == "albumartist") field = "album_artist";
        else if (key == "publisher" || key == "label" || key == "recordlabel") field = "publisher";
        else if (html_token("catalog catalogid catalognumber catalogno catno", key)) field = "catalog_id";
        else if (key == "releasedate") field = "release_date";
        if (field) {
            auto text = doc.clean(index);
            if (text.empty()) continue;
            row[field] = std::move(text);
            row["evidence"][field] = "DoujinStyle detail";
        }
    }
    policy = std::string(referrer_policy(policy));
    for (std::size_t i = 1; i < doc.nodes.size(); ++i) {
        if (doc.nodes[i].name == "meta" && lower_ascii(std::string(doc.attr(i, "name"))) == "referrer")
            if (const auto next = referrer_policy(doc.attr(i, "content")); !next.empty()) policy = next;
    }
    auto downloads = forms(doc, main, id, url.empty() ? release_url(id) : url, policy);
    row["download_offer"] = !downloads.empty();
    return {{"row", std::move(row)}, {"forms", std::move(downloads)}};
}
}
