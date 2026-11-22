#pragma once

#include "sqlite3.h"
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin {

using Database = std::unique_ptr<sqlite3, decltype(&sqlite3_close)>;
using Statement = std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)>;

inline void check(sqlite3* db, int code) {
    if (code != SQLITE_OK) throw std::runtime_error(sqlite3_errmsg(db));
}

inline Database connect(const std::string& path, int flags) {
    sqlite3* raw = nullptr;
    const auto opened = sqlite3_open_v2(path.c_str(), &raw, flags | SQLITE_OPEN_PRIVATECACHE, nullptr);
    Database db(raw, sqlite3_close);
    if (!raw) throw std::runtime_error("Could not allocate a SQLite connection");
    check(raw, opened);
    check(raw, sqlite3_busy_timeout(raw, 2000));
    check(raw, sqlite3_db_config(raw, SQLITE_DBCONFIG_TRUSTED_SCHEMA, 0, nullptr));
    return db;
}

inline Statement prepare(sqlite3* db, const char* sql) {
    sqlite3_stmt* raw = nullptr;
    check(db, sqlite3_prepare_v2(db, sql, -1, &raw, nullptr));
    return Statement(raw, sqlite3_finalize);
}

inline void bind(sqlite3_stmt* stmt, int column, std::string_view value) {
    check(sqlite3_db_handle(stmt),
          sqlite3_bind_text64(stmt, column, value.data(), value.size(), SQLITE_TRANSIENT, SQLITE_UTF8));
}

inline bool next(sqlite3_stmt* stmt) {
    const auto status = sqlite3_step(stmt);
    if (status == SQLITE_ROW) return true;
    if (status != SQLITE_DONE) check(sqlite3_db_handle(stmt), status);
    return false;
}

inline std::string string_column(sqlite3_stmt* stmt, int column) {
    if (sqlite3_column_type(stmt, column) != SQLITE_TEXT)
        throw std::runtime_error("Expected a text value");
    const auto* bytes = reinterpret_cast<const char*>(sqlite3_column_text(stmt, column));
    return std::string(bytes, static_cast<std::size_t>(sqlite3_column_bytes(stmt, column)));
}

} // namespace doujin
