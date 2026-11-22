#pragma once

// The local POST-attempt ledger does NOT make remote operations exactly once.
// It prevents automatic replay of a POST whose outcome was lost on a prior
// worker/process. The marker commits on a separate SQLite connection before
// issuing the network request. Successful offer persistence clears the marker
// in the same transaction as that successful catalog update.
#include "catalog_sqlite.hpp"
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::post_attempt {
inline constexpr char create_sql[] = R"SQL(
CREATE TABLE IF NOT EXISTS catalog_post_attempts (
 release_id INTEGER PRIMARY KEY,
 owner TEXT NOT NULL,
 started_at INTEGER NOT NULL,
 post_count INTEGER NOT NULL CHECK(post_count >= 1)
);
)SQL";

// For old v2 databases, creation is deferred to the first POST. Do not infer
// a missing table as a failed attempt, and do not create on read-only checks.
inline bool pending(sqlite3* db, std::int64_t id) {
    if (id <= 0) throw std::runtime_error("Invalid POST ledger release ID");
    auto table = prepare(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_post_attempts'");
    if (!next(table.get())) return false;
    auto row = prepare(db, "SELECT 1 FROM catalog_post_attempts WHERE release_id=?");
    check(db, sqlite3_bind_int64(row.get(), 1, id));
    return next(row.get());
}

inline void before_post(const std::string& path, std::int64_t id,
                        std::string_view owner, std::int64_t now) {
    if (id <= 0 || now < 0 || owner.empty() || owner.size() > 128)
        throw std::runtime_error("Invalid POST attempt identity");
    auto db = connect(path, SQLITE_OPEN_READWRITE);
    // CREATE is idempotent and is intentionally outside the transaction to
    // avoid a driver-specific implicit-commit hazard.
    check(db.get(), sqlite3_exec(db.get(), create_sql, nullptr, nullptr, nullptr));
    check(db.get(), sqlite3_exec(db.get(), "BEGIN IMMEDIATE", nullptr, nullptr, nullptr));
    try {
        auto existing = prepare(db.get(), "SELECT owner FROM catalog_post_attempts WHERE release_id=?");
        check(db.get(), sqlite3_bind_int64(existing.get(), 1, id));
        if (next(existing.get())) {
            const auto held = string_column(existing.get(), 0);
            existing.reset();
            if (held != owner) throw std::runtime_error("Previous POST attempt outcome unresolved; manual review required");
            auto update = prepare(db.get(), "UPDATE catalog_post_attempts SET post_count=post_count+1 WHERE release_id=?");
            check(db.get(), sqlite3_bind_int64(update.get(), 1, id));
            if (sqlite3_step(update.get()) != SQLITE_DONE) throw std::runtime_error("Could not update POST attempt count");
        } else {
            existing.reset();
            auto insert = prepare(db.get(), "INSERT INTO catalog_post_attempts VALUES (?,?,?,1)");
            check(db.get(), sqlite3_bind_int64(insert.get(), 1, id));
            bind(insert.get(), 2, owner);
            check(db.get(), sqlite3_bind_int64(insert.get(), 3, now));
            if (sqlite3_step(insert.get()) != SQLITE_DONE) throw std::runtime_error("Could not record POST intent");
        }
        check(db.get(), sqlite3_exec(db.get(), "COMMIT", nullptr, nullptr, nullptr));
    } catch (...) {
        sqlite3_exec(db.get(), "ROLLBACK", nullptr, nullptr, nullptr);
        throw;
    }
}

// Called INSIDE a successful offer-ingest transaction. Never clear pending
// intents for error/timeout/HTTP429 outcomes or on a mere UI event.
inline void resolve_after_success(sqlite3* db, std::int64_t id) {
    if (!pending(db, id)) return;
    auto statement = prepare(db, "DELETE FROM catalog_post_attempts WHERE release_id=?");
    check(db, sqlite3_bind_int64(statement.get(), 1, id));
    if (sqlite3_step(statement.get()) != SQLITE_DONE)
        throw std::runtime_error("Could not resolve committed POST intent");
}
} // namespace doujin::post_attempt
