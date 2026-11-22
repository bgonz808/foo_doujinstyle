#include "catalog_index_plan.hpp"
#include <cassert>
#include <stdexcept>
#include <string>

int main() {
    const auto db = doujin::connect(":memory:", SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
    auto exec = [&](const char* sql) {
        char* error = nullptr;
        if (sqlite3_exec(db.get(), sql, nullptr, nullptr, &error) != SQLITE_OK) {
            const std::string message(error ? error : "SQL error");
            sqlite3_free(error);
            throw std::runtime_error(message);
        }
    };
    exec("CREATE TABLE releases (id INTEGER PRIMARY KEY,offer_checked INTEGER,offer_error TEXT);"
         "CREATE TABLE copies (id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT,"
         "archive_checked INTEGER,archive_error TEXT,archive TEXT);"
         "CREATE TABLE release_copies (release_id INTEGER,copy_id INTEGER,current INTEGER);"
         "INSERT INTO releases VALUES(1,NULL,NULL),(2,990000,NULL),(3,0,'timeout'),(4,999999,NULL);"
         "INSERT INTO copies VALUES"
         "(10,'https://www.mediafire.com/file/abc/Test/file','mediafire.com','{}',NULL,NULL,NULL),"
         "(11,'https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8','mega.nz','{}',NULL,NULL,NULL),"
         "(12,'https://mediafire.com.evil.org/file/abc','mediafire.com','{}',NULL,NULL,NULL),"
         "(13,'https://www.mediafire.com/file/def/Test/file','mediafire.com','not-json',NULL,NULL,NULL),"
         "(14,'https://www.mediafire.com/file/ghi/Test/file','mediafire.com','{}',10,NULL,'not-json'),"
         "(15,'https://www.mediafire.com/file/jkl/Test/file','mediafire.com','{}',999999,NULL,'{\"format\":\"zip\",\"audio_probes\":{},\"header_probe_pass_complete\":true,\"inventory\":[]}');"
         "INSERT INTO release_copies VALUES(1,10,1),(1,11,1),(1,12,1),(1,13,1),"
         "(2,14,1),(2,15,1),(3,10,0),(4,10,0);");
    using doujin::index_plan::select;
    auto plan = select(db.get(), nlohmann::json::array({1,2,3,4}), 1000000, true);
    assert((plan["offers"] == nlohmann::json::array({1,3}))); // 2/4 still fresh
    assert(plan["archives"].size() == 3); // current only; unsafe URLs and JSON excluded
    assert(plan["archives"][0]["id"] == 11);
    assert(plan["archives"][1]["id"] == 10);
    assert(plan["archives"][2]["id"] == 14); // corrupted stored archive gets re-probed
    assert(select(db.get(), nlohmann::json::array({1}), 1000000, false)["offers"].empty());
    assert(select(db.get(), nlohmann::json::array({4}), 1000000, true)["archives"].empty());
    for (const auto& bad : {nlohmann::json::array(), nlohmann::json::array({1,1}),
                            nlohmann::json::array({-1}), nlohmann::json::array({"1"})}) {
        try { (void)select(db.get(), bad, 1000000, true); assert(false); }
        catch (const std::runtime_error&) {}
    }
    auto oversized = nlohmann::json::array();
    for (int i=1;i<=513;++i) oversized.push_back(i);
    try { (void)select(db.get(), oversized, 1000000, true); assert(false); }
    catch (const std::runtime_error&) {}
}
