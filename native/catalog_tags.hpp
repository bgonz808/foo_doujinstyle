#pragma once

#include "sqlite3.h"

#include <memory>
#include <optional>
#include <stop_token>
#include <string>
#include <vector>

namespace doujin {

inline std::optional<std::vector<std::string>> catalog_strings(const std::string& path,
                                                              const char* query, std::stop_token stop) {
    if (stop.stop_requested()) return std::nullopt;
    sqlite3* connection = nullptr;
    const auto opened = sqlite3_open_v2(path.c_str(), &connection, SQLITE_OPEN_READONLY | SQLITE_OPEN_PRIVATECACHE, nullptr);
    const std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db(connection, sqlite3_close);
    if (opened != SQLITE_OK) return std::nullopt;
    sqlite3_progress_handler(db.get(), 1000, [](void* state) {
        return static_cast<int>(static_cast<std::stop_token*>(state)->stop_requested());
    }, &stop);

    sqlite3_stmt* statement = nullptr;
    if (sqlite3_prepare_v2(db.get(), query, -1, &statement, nullptr) != SQLITE_OK) return std::nullopt;
    const std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)> tags(statement, sqlite3_finalize);

    std::vector<std::string> result;
    int status = SQLITE_OK;
    while (!stop.stop_requested() && (status = sqlite3_step(tags.get())) == SQLITE_ROW) {
        const auto* bytes = reinterpret_cast<const char*>(sqlite3_column_text(tags.get(), 0));
        const int count = sqlite3_column_bytes(tags.get(), 0);
        if (bytes && count > 0) result.emplace_back(bytes, count);
    }
    if (stop.stop_requested() || status != SQLITE_DONE) return std::nullopt;
    return result;
}

inline std::optional<std::vector<std::string>> catalog_tags(const std::string& path,
                                                            std::stop_token stop = {}) {
    constexpr char query[] = R"sql(
        SELECT tag.value FROM releases AS release
        CROSS JOIN json_each(release.listing, '$.tags') AS tag
        WHERE json_type(release.listing, '$.tags')='array'
          AND tag.type='text' AND length(tag.value) BETWEEN 1 AND 120
        GROUP BY tag.value COLLATE NOCASE
        ORDER BY count(DISTINCT release.id) DESC, tag.value COLLATE NOCASE
        LIMIT 4096
    )sql";
    return catalog_strings(path, query, stop);
}

} // namespace doujin
