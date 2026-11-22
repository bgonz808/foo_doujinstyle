#pragma once

#include "catalog_schema.hpp"
#include "post_attempt_ledger.hpp"
#include "catalog_sqlite.hpp"
#include "third_party/json.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <set>
#include <thread>
#include <utility>

namespace doujin {

using Json = nlohmann::ordered_json;

inline void execute(sqlite3* db, const char* sql) {
    check(db, sqlite3_exec(db, sql, nullptr, nullptr, nullptr));
}

class Transaction {
public:
    explicit Transaction(sqlite3* db) : db_(db) { execute(db_, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!committed_) sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr); }
    Transaction(const Transaction&) = delete;
    Transaction& operator=(const Transaction&) = delete;
    void commit() { execute(db_, "COMMIT"); committed_ = true; }
private:
    sqlite3* db_;
    bool committed_ = false;
};

inline Statement query(sqlite3* db, const char* sql, std::initializer_list<Json> values = {}) {
    auto statement = prepare(db, sql);
    int index = 0;
    for (const auto& value : values) {
        ++index;
        if (value.is_number_unsigned() && value.get<std::uint64_t>() >
                static_cast<std::uint64_t>(std::numeric_limits<sqlite3_int64>::max()))
            throw std::runtime_error("Catalog integer is outside SQLite's signed range");
        if (value.is_string()) doujin::bind(statement.get(), index, value.get_ref<const std::string&>());
        else if (value.is_null()) check(db, sqlite3_bind_null(statement.get(), index));
        else if (value.is_number_integer()) check(db, sqlite3_bind_int64(statement.get(), index, value.get<sqlite3_int64>()));
        else throw std::runtime_error("Expected a text value, integer, or null in catalog query");
    }
    return statement;
}

inline void write(sqlite3* db, const char* sql, std::initializer_list<Json> values = {}) {
    auto statement = query(db, sql, values);
    next(statement.get());
}

inline bool truthy(const Json& value) {
    if (value.is_null()) return false;
    if (value.is_boolean()) return value.get<bool>();
    if (value.is_number()) return value.get<double>() != 0;
    if (value.is_string()) return !value.get_ref<const std::string&>().empty();
    return !value.empty();
}

inline Json document(sqlite3* db, const char* sql, const Json& key) {
    auto row = query(db, sql, {key});
    if (!next(row.get()) || sqlite3_column_type(row.get(), 0) == SQLITE_NULL) return nullptr;
    return Json::parse(string_column(row.get(), 0));
}

inline void save_listing(sqlite3* db, const Json& listing, const Json& now) {
    write(db, "INSERT INTO releases (id, listing, seen) VALUES (?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET listing=excluded.listing, seen=excluded.seen",
          {listing.at("id"), listing.dump(), now});
}

inline Json combine_listing(sqlite3* db, const Json& observed) {
    auto old = document(db, "SELECT listing FROM releases WHERE id=?", observed.at("id"));
    if (old.is_null()) old = Json::object();
    auto merged = old;
    for (const auto& [key, value] : observed.items())
        if (!value.is_null()) merged[key] = value;
    if (truthy(old.value("tags", Json())) && !truthy(observed.value("tags", Json()))) {
        merged["tags"] = old.at("tags");
        merged["events"] = old.value("events", Json::array());
    }
    auto evidence = old.value("evidence", Json::object());
    const auto observed_evidence = observed.value("evidence", Json::object());
    for (const auto& [key, value] : observed_evidence.items())
        if (!observed.value(key, Json()).is_null()) evidence[key] = value;
    merged["evidence"] = std::move(evidence);
    auto technical = old.value("technical", Json::object());
    const auto observed_technical = observed.value("technical", Json::object());
    for (const auto& [key, value] : observed_technical.items())
        if (!value.is_null()) technical[key] = value;
    merged["technical"] = std::move(technical);

    auto forms = Json::array();
    for (const auto* listing : {static_cast<const Json*>(&old), &observed}) {
        for (const auto& form : listing->value("download_forms", Json::array())) {
            const auto source = form.value("source", Json("0"));
            auto previous = std::find_if(forms.begin(), forms.end(), [&](const Json& item) {
                return item.value("source", Json("0")) == source;
            });
            if (previous == forms.end()) forms.push_back(form);
            else *previous = form;
        }
    }
    if (!forms.empty()) merged["download_forms"] = std::move(forms);
    return merged;
}

inline sqlite3_int64 attach_copy(sqlite3* db, const Json& id, const Json& link, const Json& now) {
    const auto& key = link.at("destination");
    const auto& url = link.at("url");
    const auto& offer = link.at("offer");
    const auto source = link.at("source").get<std::string>();
    write(db, "INSERT INTO copies(destination, share_url, host, offer) VALUES (?,?,?,?) "
              "ON CONFLICT(destination) DO NOTHING", {key, url, link.at("host"), offer.dump()});
    auto previous = query(db, "SELECT id, offer FROM copies WHERE destination=?", {key});
    if (!next(previous.get())) throw std::runtime_error("Archive copy was not recorded");
    const auto copy = sqlite3_column_int64(previous.get(), 0);
    auto merged = Json::parse(string_column(previous.get(), 1));
    previous.reset();
    for (const auto& [key, value] : offer.items())
        if (!value.is_null()) merged[key] = value;
    write(db, "UPDATE copies SET share_url=?, offer=? WHERE id=?", {url, merged.dump(), copy});
    if (source == "doujinstyle" || source.starts_with("doujinstyle:"))
        write(db, "UPDATE release_copies SET current=0 "
                  "WHERE release_id=? AND source=? AND copy_id<>? AND current=1", {id, source, copy});
    write(db, "INSERT INTO release_copies VALUES (?,?,?,?,?,1) "
              "ON CONFLICT(release_id, copy_id, source) DO UPDATE SET current=1, last_seen=excluded.last_seen",
          {id, copy, source, now, now});
    return copy;
}

inline Json save_offers(sqlite3* db, const Json& request) {
    const auto& id = request.at("id");
    const auto& now = request.at("now");
    const auto& observation = request.at("observation");
    if (!observation.is_null()) save_listing(db, combine_listing(db, observation), now);
    write(db, "UPDATE releases SET offer_checked=?, offer_error=? WHERE id=?", {now, request.at("error"), id});
    const auto& observed_sources = request.at("observed_sources");
    if (!observed_sources.is_null()) {
        write(db, "UPDATE release_copies SET current=0 WHERE release_id=? AND source LIKE 'doujinstyle%' "
                  "AND current=1 AND source NOT IN (SELECT value FROM json_each(?))", {id, observed_sources.dump()});
    }
    auto copies = Json::array();
    for (const auto& link : request.at("links"))
        copies.push_back(attach_copy(db, id, link, now));
    if (request.at("error").is_null())
        post_attempt::resolve_after_success(db, id.get<std::int64_t>());
    return copies;
}

inline Json combine_archive(Json archive, const Json& old) {
    auto previous = Json::object();
    for (const auto& row : old.value("inventory", Json::array())) {
        previous[row.at("path").get<std::string>()] =
            Json::array({row.value("crc32", Json()), row.value("uncompressed_bytes", Json())});
    }
    std::set<std::string> unchanged;
    std::set<std::string> candidates;
    for (const auto& row : archive.value("inventory", Json::array())) {
        const auto path = row.at("path").get<std::string>();
        const auto crc = row.value("crc32", Json());
        if (truthy(crc) && previous.value(path, Json()) == Json::array({crc, row.value("uncompressed_bytes", Json())}))
            unchanged.insert(path);
        const auto method = row.value("zip_method", Json());
        if (row.value("kind", Json()) == "audio_candidate" && !truthy(row.value("encrypted", Json())) &&
            (method == 0 || method == 8)) candidates.insert(path);
    }
    auto samples = Json::object();
    const auto old_samples = old.value("audio_probes", Json::object());
    const auto new_samples = archive.value("audio_probes", Json::object());
    for (const auto& [path, data] : old_samples.items())
        if (unchanged.contains(path)) samples[path] = data;
    for (const auto& [path, data] : new_samples.items()) {
        auto sample = samples.value(path, Json::object());
        sample.update(data);
        samples[path] = std::move(sample);
    }
    const auto legacy = old.value("audio_sample", Json());
    const auto legacy_path = legacy.is_object() ? legacy.value("path", Json()) : Json();
    if (!archive.contains("audio_sample") && legacy_path.is_string() && unchanged.contains(legacy_path.get<std::string>()))
        archive["audio_sample"] = legacy;
    if (truthy(old.value("header_probe_pass_complete", Json())) &&
        !truthy(archive.value("header_probe_pass_complete", Json())) &&
        std::all_of(candidates.begin(), candidates.end(), [&](const auto& path) { return samples.contains(path); }))
        archive["header_probe_pass_complete"] = true;
    if (!samples.empty()) archive["audio_probes"] = std::move(samples);
    const auto cover = old.value("cached_front_cover", Json());
    if (!archive.contains("cached_front_cover") && cover.is_string() && unchanged.contains(cover.get<std::string>()))
        archive["cached_front_cover"] = cover;
    return archive;
}

inline void save_archive(sqlite3* db, const Json& request) {
    const auto& id = request.at("id");
    auto archive = request.at("archive");
    if (archive.is_object() && archive.value("format", Json()) == "zip") {
        const auto old = document(db, "SELECT archive FROM copies WHERE id=?", id);
        if (old.is_object()) archive = combine_archive(std::move(archive), old);
    }
    write(db, "UPDATE copies SET archive=COALESCE(?,archive), archive_checked=?, archive_error=? WHERE id=?",
          {archive.is_null() ? Json() : Json(archive.dump()), request.at("now"), request.at("error"), id});
}

inline void save_page(sqlite3* db, const Json& request) {
    const auto& page = request.at("page");
    const auto& now = request.at("now");
    write(db, "INSERT OR REPLACE INTO pages VALUES (?,?,?)", {request.at("url"), page.dump(), now});
    auto merged = Json::array();
    for (const auto& observed : page.at("releases"))
        merged.push_back(combine_listing(db, observed));
    for (const auto& listing : merged) save_listing(db, listing, now);
    if (const auto seeds = request.find("seeds"); seeds != request.end()) {
        for (const auto& row : *seeds)
            write(db, "INSERT INTO releases (id,listing,seen) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                  {row.at("id"), row.dump(), now});
    }
    const auto& checkpoint = request.at("checkpoint");
    if (!checkpoint.is_null())
        write(db, "INSERT OR REPLACE INTO backfill_checkpoint VALUES (1,?)", {checkpoint.dump()});
}

inline void migrate_links(sqlite3* db, const Json& links) {
    auto previous = prepare(db, "SELECT id,share_url,COALESCE(NULLIF(offer_checked,0),seen),"
        "archive,archive_checked,archive_error,offer,host FROM releases WHERE share_url IS NOT NULL "
        "ORDER BY COALESCE(offer_checked,seen),id");
    std::size_t index = 0;
    while (next(previous.get())) {
        const auto id = sqlite3_column_int64(previous.get(), 0);
        const auto url = string_column(previous.get(), 1);
        const auto now = sqlite3_column_int64(previous.get(), 2);
        auto optional_text = [&](int column) -> Json {
            return sqlite3_column_type(previous.get(), column) == SQLITE_NULL
                 ? Json() : Json(string_column(previous.get(), column));
        };
        if (index >= links.size()) throw std::runtime_error("Legacy catalog changed during startup. Try again.");
        const auto& link = links.at(index++);
        if (link.at("id") != id || link.at("url") != url ||
            link.at("old_offer") != optional_text(6) || link.at("old_host") != optional_text(7))
            throw std::runtime_error("Legacy catalog changed during startup. Try again.");
        const auto copy = attach_copy(db, id, link, now);
        const auto archive = optional_text(3);
        if (!archive.is_null() || sqlite3_column_type(previous.get(), 4) != SQLITE_NULL) {
            const auto checked = sqlite3_column_int64(previous.get(), 4);
            write(db, "UPDATE copies SET archive=CASE WHEN archive IS NULL OR ? >= COALESCE(archive_checked,0) "
                      "THEN COALESCE(?,archive) ELSE archive END, archive_error=CASE WHEN ? >= COALESCE(archive_checked,0) "
                      "THEN ? ELSE archive_error END, archive_checked=MAX(COALESCE(archive_checked,0),?) WHERE id=?",
                  {checked, archive, checked, optional_text(5), checked, copy});
        }
    }
    if (index != links.size()) throw std::runtime_error("Legacy catalog changed during startup. Try again.");
}

inline std::string journal_mode(sqlite3* db) {
    auto row = prepare(db, "PRAGMA journal_mode");
    next(row.get());
    return string_column(row.get(), 0);
}

inline void initialize_catalog(sqlite3* db, const Json& request) {
    const bool wal = request.at("wal").get<bool>();
    auto mode = journal_mode(db);
    if (!wal && mode == "wal")
        throw std::runtime_error("The catalog is in WAL mode. Use a local volume for the live WAL catalog.");
    auto version = [&] {
        auto row = prepare(db, "PRAGMA user_version");
        next(row.get());
        return sqlite3_column_int(row.get(), 0);
    };
    if (version() < 2) {
        Transaction transaction(db);
        const auto old = version();
        if (old < 2) {
            execute(db, catalog_schema);
            if (old == 0) migrate_links(db, request.at("legacy"));
            execute(db, "PRAGMA user_version=2");
        }
        transaction.commit();
    }
    if (wal && mode != "wal") {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        do {
            sqlite3_stmt* raw = nullptr;
            const auto compiled = sqlite3_prepare_v2(db, "PRAGMA journal_mode=WAL", -1, &raw, nullptr);
            Statement statement(raw, sqlite3_finalize);
            const auto status = compiled == SQLITE_OK ? sqlite3_step(raw) : compiled;
            if (status == SQLITE_ROW && string_column(raw, 0) == "wal") return;
            if (status != SQLITE_BUSY && status != SQLITE_LOCKED && status != SQLITE_ROW) check(db, status);
            statement.reset();
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        } while (std::chrono::steady_clock::now() < deadline);
        throw std::runtime_error("Catalog is busy. SQLite could not switch it to WAL.");
    }
}

inline Json ingest(sqlite3* db, const std::string& operation, const Json& request) {
    // All normal mutations take the writer only after the worker has prepared its request.
    Transaction transaction(db);
    Json result;
    if (operation == "page") save_page(db, request);
    else if (operation == "observe") save_listing(db, combine_listing(db, request.at("row")), request.at("now"));
    else if (operation == "listing") save_listing(db, request.at("row"), request.at("now"));
    else if (operation == "offers") result = save_offers(db, request);
    else if (operation == "archive") save_archive(db, request);
    else if (operation == "copy") {
        const auto& listing = request.at("row");
        const auto& id = listing.at("id");
        const auto& now = request.at("now");
        if (document(db, "SELECT listing FROM releases WHERE id=?", id).is_null()) save_listing(db, listing, now);
        result = attach_copy(db, id, request.at("link"), now);
    } else if (operation == "claim") {
        write(db, "INSERT INTO catalog_lookup_claims VALUES (?,?,?,?) "
                  "ON CONFLICT(provider,release_id) DO UPDATE SET token=excluded.token,expires=excluded.expires "
                  "WHERE catalog_lookup_claims.expires<=?",
              {request.at("provider"), request.at("id"), request.at("token"), request.at("expires"), request.at("now")});
        result = sqlite3_changes(db) == 1;
    } else if (operation == "release") {
        write(db, "DELETE FROM catalog_lookup_claims WHERE provider=? AND release_id=? AND token=?",
              {request.at("provider"), request.at("id"), request.at("token")});
    } else {
        throw std::runtime_error("Unknown catalog operation");
    }
    transaction.commit();
    return result;
}

} // namespace doujin
