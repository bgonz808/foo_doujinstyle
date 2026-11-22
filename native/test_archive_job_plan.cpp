#include "archive_job_plan.hpp"
#include <cassert>
#include <stdexcept>
#include <string>

int main() {
    const auto db = doujin::connect(":memory:", SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
    auto exec = [&](const char* sql) {
        char* error = nullptr;
        if (sqlite3_exec(db.get(), sql, nullptr, nullptr, &error) != SQLITE_OK) {
            std::string message = error ? error : "SQL error";
            sqlite3_free(error);
            throw std::runtime_error(message);
        }
    };
    exec("CREATE TABLE releases(id INTEGER PRIMARY KEY);"
         "CREATE TABLE copies(id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT);"
         "CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,current INTEGER);");
    exec("INSERT INTO releases VALUES (101),(102);"
         "INSERT INTO copies VALUES"
         "(1,'https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8','mega.nz','{\"filename\":\"mega.7z\"}'),"
         "(2,'https://www.mediafire.com/file/abc/Test/file','mediafire.com','{\"filename\":\"mf.zip\"}'),"
         "(3,'https://mediafire.com.evil.example/file/abc','mediafire.com','{\"filename\":\"evil.zip\"}'),"
         "(4,'https://www.mediafire.com/file/deprecated','mediafire.com','{\"filename\":\"old.zip\"}'),"
         "(5,'https://www.mediafire.com/file/bad','mediafire.com','not-json');"
         "INSERT INTO release_copies VALUES (101,1,1),(101,2,1),(101,3,1),(101,4,0),(101,5,1);"
    );
    using doujin::archive_job::select;
    const auto preferred = select(db.get(), 101);
    assert(preferred["status"] == "ready");
    assert(preferred["copy"]["id"] == 2);
    assert(preferred["copy"]["host"] == "mediafire.com");
    assert(preferred["copy"]["filename"] == "mf.zip");
    auto backup = select(db.get(), 101, preferred["copy"]["url"].get<std::string>());
    assert(backup["copy"]["id"] == 1);
    assert(backup["copy"]["host"] == "mega.nz");
    assert(select(db.get(), 102)["status"] == "no_offer");
    assert(select(db.get(), 103)["status"] == "missing_release");
    try { (void)select(db.get(), -1); assert(false); }
    catch (const std::runtime_error&) { }
}
