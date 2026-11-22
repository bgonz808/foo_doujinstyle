#include "catalog_status.hpp"
#include <algorithm>
#include <cassert>
#include <filesystem>
#include <iostream>
#include <string_view>

void test_layouts(const char* path) {
    sqlite3* db = nullptr;
    assert(sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nullptr) == SQLITE_OK);
    constexpr auto script = R"sql(
        CREATE TABLE pages(snapshot);
        CREATE TABLE releases(id, seen, listing);
        CREATE TABLE backfill_checkpoint(id, snapshot);
        CREATE TABLE copies(id, archive, offer);
        CREATE TABLE release_copies(copy_id, release_id, current);
        INSERT INTO copies VALUES
            (1,'{"format":"zip","inventory":[{"kind":"audio_candidate","zip_method":8,"uncompressed_bytes":8388608}]}',NULL),
            (2,'{"format":"zip","inventory":[{"kind":"audio_candidate","zip_method":0,"uncompressed_bytes":8388608}]}',NULL),
            (3,'{"format":"zip"}',NULL),(4,'{"format":"7z"}',NULL),(5,NULL,NULL),(6,'{"format":"rar"}',NULL);
        INSERT INTO release_copies VALUES (1,1,1),(1,2,1),(2,3,1),(3,4,1),(4,5,1),(5,6,1),(6,7,0);
    )sql";
    assert(sqlite3_exec(db, script, nullptr, nullptr, nullptr) == SQLITE_OK);
    assert(sqlite3_close(db) == SQLITE_OK);
    const auto lines = doujin::catalog_status(path);
    assert(lines);
    for (const std::string_view label : {"ZIP with DEFLATE audio over 4 MiB", "ZIP with STORE-only audio",
        "ZIP with other or unclassified audio", "7z/RAR, solid status unknown", "Format unobserved"}) {
        assert(std::any_of(lines->begin(), lines->end(), [label](const std::string& line) {
            return line.starts_with(label) && line.ends_with("1 / 5 (20.0%)");
        }));
    }
}

int main(int argc, char** argv) {
    if (argc != 2 && argc != 3) return 2;
    assert(std::string_view(sqlite3_libversion()) == "3.54.1");
    if (argc == 3 && std::string_view(argv[2]) == "layout-fixture") {
        test_layouts(argv[1]);
        return 0;
    }
    std::stop_source cancelled;
    cancelled.request_stop();
    assert(!doujin::catalog_tags(argv[1], cancelled.get_token()));
    assert(!doujin::catalog_status(argv[1], cancelled.get_token()));
    const auto missing = std::string(argv[1]) + "/missing.sqlite";
    assert(!doujin::catalog_tags(missing));
    assert(!std::filesystem::exists(missing));
    const bool status = argc == 3 && std::string_view(argv[2]) == "status";
    const auto lines = status ? doujin::catalog_status(argv[1]) : doujin::catalog_tags(argv[1]);
    if (!lines) return 1;
    for (const auto& line : *lines) std::cout << line << '\n';
}
