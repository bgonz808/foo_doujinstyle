#include "catalog_ingest.hpp"
#include "post_attempt_ledger.hpp"
#include <cassert>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>
#include <atomic>

namespace fs = std::filesystem;
using doujin::Json;
int main() {
    const auto path=(fs::temp_directory_path()/ ("dj-post-ledger-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".db")).string();
    try {
        {
            auto db=doujin::connect(path, SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE);
            doujin::execute(db.get(), R"SQL(
                CREATE TABLE releases(id INTEGER PRIMARY KEY, listing TEXT NOT NULL,
                                      offer_checked INTEGER,offer_error TEXT);
                CREATE TABLE copies(id INTEGER PRIMARY KEY,destination TEXT UNIQUE NOT NULL,
                                    share_url TEXT,host TEXT,offer TEXT);
                CREATE TABLE release_copies(release_id INTEGER, copy_id INTEGER, source TEXT,
                    first_seen INTEGER,last_seen INTEGER,current INTEGER,
                    PRIMARY KEY(release_id,copy_id,source));
                INSERT INTO releases(id,listing) VALUES(1,'{}'),(2,'{}');
            )SQL");
            assert(!doujin::post_attempt::pending(db.get(),1));
        }
        // Simulate a child killed after the pre-POST commit: new connection,
        // new owner must refuse replay even though no offer result exists.
        doujin::post_attempt::before_post(path,1,"worker-A",100);
        {
            auto restarted=doujin::connect(path,SQLITE_OPEN_READONLY);
            assert(doujin::post_attempt::pending(restarted.get(),1));
            auto row=doujin::query(restarted.get(),"SELECT started_at,post_count FROM catalog_post_attempts WHERE release_id=1");
            assert(doujin::next(row.get()) && sqlite3_column_int(row.get(),0)==100 &&
                   sqlite3_column_int(row.get(),1)==1);
        }
        bool blocked=false;
        try {doujin::post_attempt::before_post(path,1,"worker-B",101);}
        catch(const std::runtime_error& e) {blocked=std::string(e.what()).find("unresolved")!=std::string::npos;}
        assert(blocked);
        // The same live worker may submit a second form without losing intent.
        doujin::post_attempt::before_post(path,1,"worker-A",102);
        {
            auto db=doujin::connect(path,SQLITE_OPEN_READWRITE);
            auto count=doujin::query(db.get(),"SELECT post_count FROM catalog_post_attempts WHERE release_id=1");
            assert(doujin::next(count.get()) && sqlite3_column_int(count.get(),0)==2);
            // A resolved offer and marker deletion are ONE SQLite transaction.
            auto out=doujin::ingest(db.get(),"offers",{{"id",1},{"now",103},
                {"observation",nullptr},{"links",Json::array()},{"error",nullptr},
                {"observed_sources",nullptr}});
            assert(out.is_array());
            assert(!doujin::post_attempt::pending(db.get(),1));
            auto row=doujin::query(db.get(),"SELECT offer_checked FROM releases WHERE id=1");
            assert(doujin::next(row.get()) && sqlite3_column_int(row.get(),0)==103);
        }
        doujin::post_attempt::before_post(path,1,"worker-C",104);
        // Error persistence must NOT clear the uncertainty marker.
        {
            auto db=doujin::connect(path,SQLITE_OPEN_READWRITE);
            (void)doujin::ingest(db.get(),"offers",{{"id",1},{"now",105},
                {"observation",nullptr},{"links",Json::array()},{"error","timeout"},
                {"observed_sources",nullptr}});
            assert(doujin::post_attempt::pending(db.get(),1));
        }
        // Two workers race to claim the same release: exactly one owner wins.
        std::atomic<int> winners{0},denied{0};
        std::thread a([&]{try {doujin::post_attempt::before_post(path,2,"worker-D",106);++winners;}
                        catch(const std::exception&){++denied;}});
        std::thread b([&]{try {doujin::post_attempt::before_post(path,2,"worker-E",106);++winners;}
                        catch(const std::exception&){++denied;}});
        a.join();b.join();
        assert(winners==1 && denied==1);
        bool bad=false;
        try {doujin::post_attempt::before_post(path,2,"",107);}
        catch(const std::runtime_error&){bad=true;}
        assert(bad);
    } catch (...) {fs::remove(path);throw;}
    fs::remove(path);
    std::puts("native durable POST ledger: restart, multiple forms, single writer, atomic resolution passed");
}
