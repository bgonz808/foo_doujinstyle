#pragma once

#include "reference_projection.hpp"
#include "sha256.hpp"

#include <chrono>
#include <random>
#include <regex>

namespace doujin::bangumi {
using projection::get;
using projection::text;
using StringSet = std::set<std::string>;
inline constexpr auto origin = "https://api.bgm.tv";
inline constexpr sqlite3_int64 match_ttl = 30 * 86400, miss_ttl = 7 * 86400, lease_ttl = 120;
inline constexpr std::size_t batch_limit = 6, search_limit = 8, body_limit = 512 * 1024;

inline sqlite3_int64 unix_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

inline bool word_at(std::string_view value, std::size_t pos) {
    if (pos >= value.size()) return false;
    utf8proc_int32_t ch = 0;
    if (utf8proc_iterate(reinterpret_cast<const utf8proc_uint8_t*>(value.data() + pos),
                         static_cast<utf8proc_ssize_t>(value.size() - pos), &ch) <= 0) return false;
    const auto category = utf8proc_category(ch);
    return ch == '_' || (category >= UTF8PROC_CATEGORY_LU && category <= UTF8PROC_CATEGORY_LO) ||
        (category >= UTF8PROC_CATEGORY_ND && category <= UTF8PROC_CATEGORY_NO);
}

inline void add_event_ids(StringSet& ids, const std::vector<std::string>& values) {
    static const std::regex pattern(
        R"((?:M3(?:-|‐|‑|–| )?[0-9]{1,3}|C(?:-|‐|‑|–| )?[0-9]{2,3}|(?:ARTS|RTS|THK|TMKS)[0-9]{1,2}|RTSSP[0-9]?))",
        std::regex::icase);
    for (const auto& value : values) {
        for (std::sregex_iterator it(value.begin(), value.end(), pattern), end; it != end; ++it) {
            const auto start = static_cast<std::size_t>(it->position());
            auto previous = start;
            if (previous > 0) {
                --previous;
                while (previous > 0 && (static_cast<unsigned char>(value[previous]) & 0xc0) == 0x80) --previous;
            }
            if ((start > 0 && word_at(value, previous)) ||
                word_at(value, start + static_cast<std::size_t>(it->length()))) continue;
            if (auto code = projection::event_id(it->str()); !code.empty()) ids.insert(std::move(code));
        }
    }
}

inline StringSet release_events(const Json& release) {
    StringSet result;
    add_event_ids(result, projection::strings(get(release, "tags")));
    for (const auto& event : get(release, "events"))
        if (const auto id = text(get(event, "id")); !id.empty()) result.emplace(id);
    return result;
}

inline StringSet artist_names(const std::vector<std::string>& credits) {
    StringSet result;
    for (const auto& credit : credits) {
        std::string part;
        const auto add = [&] {
            if (auto name = projection::normalized(part); !name.empty()) result.insert(std::move(name));
            part.clear();
        };
        projection::characters(credit, [&](auto ch, auto encoded) {
            switch (ch) {
                case '(' : case ')' : case '/' : case ',' : case ';' :
                case 0xff08: case 0xff09: case 0x3001: case 0xff0c: case 0xff1b: add(); break;
                default: part += encoded;
            }
        });
        add();
    }
    return result;
}

inline bool overlaps(const StringSet& a, const StringSet& b) {
    return std::ranges::any_of(a, [&](const auto& value) { return b.contains(value); });
}

using Infobox = std::map<std::string, std::vector<std::string>>;
inline Infobox infobox(const Json& subject) {
    Infobox result;
    for (const auto& entry : get(subject, "infobox")) {
        auto key = projection::normalized(text(get(entry, "key")));
        if (key.empty()) continue;
        const auto append = [&](const Json& field) {
            if (auto value = projection::trim(text(field)); !value.empty()) result[key].push_back(std::move(value));
        };
        const auto& value = get(entry, "value");
        if (value.is_string()) append(value);
        else if (value.is_array()) for (const auto& item : value) append(get(item, "v"));
    }
    return result;
}

inline std::vector<std::string> values(const Infobox& box, std::initializer_list<const char*> keys) {
    std::vector<std::string> result;
    for (const auto key : keys) {
        const auto found = box.find(key);
        if (found != box.end()) result.insert(result.end(), found->second.begin(), found->second.end());
    }
    return result;
}

inline bool same_title(std::string_view normalized, const Json& subject) {
    return projection::normalized(text(get(subject, "name"))) == normalized ||
        projection::normalized(text(get(subject, "name_cn"))) == normalized;
}

inline Json match(const Json& release, const Json& subject, std::string_view title,
                  const StringSet& observed, const StringSet& local_events, const std::string& local_date) {
    const auto& type = get(subject, "type");
    if (!type.is_number_integer() || type != 3 || !same_title(title, subject)) return nullptr;
    const auto box = infobox(subject);
    const auto peers = artist_names(values(box, {"艺术家", "アーティスト", "artist", "album artist", "制作人"}));
    const auto artists = artist_names({std::string(text(get(release, "album_artist"))),
                                       std::string(text(get(release, "artist")))});
    if (!overlaps(artists, peers)) return nullptr;
    const auto labels = values(box, {"厂牌", "レーベル", "label", "publisher", "出版方"});
    StringSet catalogs, events;
    for (const auto& value : values(box, {"品番", "商品番号", "目录编号", "catalog number", "catalog"}))
        if (auto code = projection::catalog_number(value); !code.empty()) catalogs.insert(std::move(code));
    const auto dates = values(box, {"发售日期", "発売日", "release date"});
    add_event_ids(events, dates);
    add_event_ids(events, values(box, {"イベント", "event", "release event"}));
    auto released = format_release_date(projection::trim(text(get(subject, "date"))));
    for (const auto& value : dates) {
        if (!released.empty()) break;
        released = format_release_date(std::string_view(value).substr(0, value.find(' ')));
    }
    const auto same_event = overlaps(events, local_events);
    const auto same_catalog = overlaps(catalogs, observed);
    const auto dates_known = !local_date.empty() && !released.empty();
    const auto same_date = dates_known && local_date == released;
    if ((!local_events.empty() && !events.empty() && !same_event) || (dates_known && !same_date) ||
        (!catalogs.empty() && !observed.empty() && !same_catalog) || (!same_event && !same_catalog && !same_date) ||
        labels.size() > 1 || catalogs.size() > 1) return nullptr;
    auto result = Json::object();
    if (!labels.empty()) result["publisher"] = labels.front();
    if (!catalogs.empty()) result["catalog_id"] = *catalogs.begin();
    if (!released.empty()) result["release_date"] = std::move(released);
    events.insert(local_events.begin(), local_events.end());
    if (auto entries = discovery::events_for(Json(events)); !entries.empty()) result["events"] = std::move(entries);
    return result;
}

struct Response {
    unsigned status;
    std::string body;
};

class HttpError : public std::runtime_error {
public:
    explicit HttpError(unsigned code) : std::runtime_error("HTTP " + std::to_string(code)), status(code) {}
    unsigned status;
};

template<typename Fetch>
Json get_json(Fetch& fetch, const std::string& path, const Json& payload = nullptr) {
    auto response = fetch(path, payload);
    if (response.status != 200) throw HttpError(response.status);
    if (response.body.size() > body_limit) throw std::runtime_error("Bangumi response exceeded its byte limit");
    return Json::parse(response.body, [](int depth, Json::parse_event_t, const Json&) {
        if (depth > 64) throw std::runtime_error("Bangumi response exceeds the JSON nesting limit");
        return true;
    });
}

template<typename Fetch>
Json find_release(Fetch& fetch, const Json& release, const StringSet& catalogs,
                  const StringSet& events, const std::string& date) {
    const auto title = text(get(release, "album"));
    const auto normalized = projection::normalized(title);
    if (normalized.empty()) return nullptr;
    Json search;
    std::string_view key = "data";
    try {
        search = get_json(fetch, "/v0/search/subjects?limit=8",
                          {{"keyword", title}, {"sort", "match"}, {"filter", {{"type", Json::array({3})}}}});
    } catch (const HttpError& error) {
        if (error.status != 401 && error.status != 403 && error.status != 404 && error.status != 405) throw;
        search = get_json(fetch, "/search/subject/" + discovery::percent_encode(title) + "?type=3&max_results=8");
        key = "list";
    }
    const auto& hits = get(search, key.data());
    if (!hits.is_array()) return nullptr;
    std::set<std::int64_t> seen;
    Json result;
    bool ambiguous = false;
    for (std::size_t i = 0; i < std::min(hits.size(), search_limit); ++i) {
        const auto& hit = hits.at(i);
        const auto& raw = get(hit, "id");
        if (!raw.is_number_integer() || !same_title(normalized, hit)) continue;
        const auto id = raw.get<std::int64_t>();
        if (id <= 0 || !seen.insert(id).second) continue;
        auto fields = match(release, get_json(fetch, "/v0/subjects/" + std::to_string(id)),
                            normalized, catalogs, events, date);
        if (fields.is_null()) continue;
        if (result.is_null()) result = {{"url", "https://bgm.tv/subject/" + std::to_string(id)}, {"fields", std::move(fields)}};
        else ambiguous = true;
    }
    if (ambiguous) return nullptr;
    return result;
}

inline StringSet archive_catalogs(sqlite3* db, const Json& release) {
    if (auto explicit_code = projection::catalog_number(text(get(release, "catalog_id"))); !explicit_code.empty())
        return {std::move(explicit_code)};
    StringSet tagged, hints;
    auto copies = query(db, "SELECT DISTINCT c.archive FROM copies c JOIN release_copies rc ON c.id=rc.copy_id "
                            "WHERE rc.release_id=? AND c.archive IS NOT NULL AND c.archive<>''", {release.at("id")});
    while (next(copies.get())) {
        const auto archive = Json::parse(string_column(copies.get(), 0));
        StringSet codes;
        const auto collect = [&](const Json& probe) {
            for (const auto field : catalog_number_tags)
                for (const auto& value : projection::strings(get(get(probe, "tags"), field)))
                    if (auto code = projection::catalog_number(value); !code.empty()) codes.insert(std::move(code));
        };
        for (const auto& probe : get(archive, "audio_probes")) collect(probe);
        collect(get(archive, "audio_sample"));
        if (codes.size() == 1) tagged.insert(*codes.begin());
        for (const auto& value : projection::strings(get(archive, "catalog_id_candidates")))
            if (auto code = projection::catalog_number(value); !code.empty()) hints.insert(std::move(code));
    }
    return tagged.empty() ? hints : tagged;
}

// Keep the separators and ASCII escaping used for Python's existing evidence hashes.
inline std::string evidence_json(const Json& values) {
    std::string result = "[";
    for (const auto& value : values) {
        if (result.size() > 1) result += ", ";
        result += value.is_array() ? evidence_json(value) : value.dump(-1, ' ', true);
    }
    result += ']';
    return result;
}

class LookupLease {
public:
    LookupLease(sqlite3* db, const Json& id, sqlite3_int64 now) : db_(db),
        request_{{"provider", "bangumi"}, {"id", id}, {"now", now}, {"expires", now + lease_ttl}} {
        std::random_device random;
        std::array<std::random_device::result_type, 4> entropy{};
        for (auto& word : entropy) word = random();
        request_["token"] = encode_hex({reinterpret_cast<const char*>(entropy.data()), sizeof entropy});
        held_ = ingest(db_, "claim", request_) == true;
    }

    ~LookupLease() {
        if (held_) {
            try { ingest(db_, "release", request_); } catch (const std::exception&) {}
        }
    }
    LookupLease(const LookupLease&) = delete;
    LookupLease& operator=(const LookupLease&) = delete;

    bool held() const { return held_; }

    bool commit(const Json& prior, const Json& record, sqlite3_int64 now) {
        Transaction tx(db_);
        auto owner = query(db_, "SELECT 1 FROM catalog_lookup_claims WHERE provider=? AND release_id=? AND token=?",
                           {request_.at("provider"), request_.at("id"), request_.at("token")});
        if (!next(owner.get())) return false;
        owner.reset();
        const auto current = document(db_, "SELECT listing FROM releases WHERE id=?", request_.at("id"));
        auto external = get(current, "external_lookup");
        if (current.is_null() || get(external, "bangumi") != prior) return false;
        external["bangumi"] = record;
        save_listing(db_, combine_listing(db_, {{"id", request_.at("id")}, {"external_lookup", std::move(external)}}), now);
        tx.commit();
        return true;
    }

private:
    sqlite3* db_;
    Json request_;
    bool held_ = false;
};

template<typename Fetch, typename Emit>
void enrich(sqlite3* db, const Json& ids, Fetch fetch, Emit emit) {
    for (const auto& id : ids) {
        const auto now = unix_seconds();
        const auto release = document(db, "SELECT listing FROM releases WHERE id=?", id);
        if (release.is_null()) continue;
        const auto& prior = get(get(release, "external_lookup"), "bangumi");
        const auto ttl = truthy(get(prior, "url")) ? match_ttl : miss_ttl;
        const auto catalogs = archive_catalogs(db, release);
        const auto events = release_events(release);
        const auto date = format_release_date(projection::trim(text(get(release, "release_date"))));
        if (catalogs.empty() && events.empty() && date.empty()) continue;
        const auto stamp = sha256_key(evidence_json(Json::array({release.at("album"), get(release, "artist"),
            get(release, "album_artist"), get(release, "release_date"), Json(catalogs), Json(events)})));
        if (stamp.empty()) throw std::runtime_error("Could not hash Bangumi lookup evidence");
        const auto& checked = get(prior, "checked");
        const auto& evidence = get(prior, "evidence");
        if (checked.is_number_integer() && checked > now - ttl &&
            (evidence == stamp || (truthy(get(prior, "url")) && !truthy(evidence)))) continue;
        LookupLease lease(db, id, now);
        if (!lease.held()) continue;
        const auto current = document(db, "SELECT listing FROM releases WHERE id=?", id);
        if (get(get(current, "external_lookup"), "bangumi") != prior) continue;
        Json record;
        try {
            record = find_release(fetch, release, catalogs, events, date);
        } catch (const HttpError& error) {
            emit("  " + id.dump() + "   Bangumi lookup unavailable (" + error.what() + ")");
            if (error.status == 429) return;
            continue;
        } catch (const std::exception&) {
            emit("  " + id.dump() + "   Bangumi lookup unavailable (invalid response or transport error)");
            continue;
        }
        const auto matched = record.is_object();
        if (!matched) {
            record = truthy(get(prior, "url")) && (evidence.is_null() || evidence == stamp) ? prior : Json::object();
        }
        record["checked"] = now;
        record["evidence"] = stamp;
        if (lease.commit(prior, record, now) && matched) {
            const auto url = text(record.at("url"));
            emit("  " + id.dump() + "   Matched Bangumi album " + std::string(url.substr(url.rfind('/') + 1)));
        }
    }
}

inline Json idle_candidates(sqlite3* db, sqlite3_int64 now) {
    auto rows = query(db, R"sql(
        SELECT r.id FROM releases r
        WHERE COALESCE(json_extract(r.listing, '$.external_lookup.bangumi.checked'), 0) <
              CASE WHEN json_type(r.listing, '$.external_lookup.bangumi.url') IS NULL THEN ? ELSE ? END
          AND (json_type(r.listing, '$.publisher') IS NULL OR json_type(r.listing, '$.catalog_id') IS NULL
               OR json_type(r.listing, '$.release_date') IS NULL)
          AND EXISTS (SELECT 1 FROM release_copies rc JOIN copies c ON c.id=rc.copy_id
                      WHERE rc.release_id=r.id AND rc.current=1 AND c.archive_error IS NULL
                        AND json_extract(c.archive, '$.format')='zip')
        ORDER BY r.id DESC LIMIT ?
    )sql", {now - miss_ttl, now - match_ttl, batch_limit});
    auto result = Json::array();
    while (next(rows.get())) result.push_back(sqlite3_column_int64(rows.get(), 0));
    return result;
}
} // namespace doujin::bangumi
