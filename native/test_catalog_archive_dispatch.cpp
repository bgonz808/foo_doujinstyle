#include "catalog_archive_dispatch.hpp"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <stop_token>
#include <string>
#include <thread>
#include <vector>

using doujin::Json;
using doujin::archive_dispatch::Task;
static constexpr char archive_url[] = "https://www.mediafire.com/file/abc123/file.zip/file";
static auto database() {
    auto db = doujin::connect(":memory:", SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
    doujin::execute(db.get(), R"SQL(
        CREATE TABLE releases(id INTEGER PRIMARY KEY, listing TEXT NOT NULL);
        CREATE TABLE copies(id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT,
                            archive TEXT,archive_checked INTEGER,archive_error TEXT);
        CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,current INTEGER);
    )SQL");
    for (int id=1; id<=5; ++id)
        doujin::write(db.get(), "INSERT INTO releases VALUES (?,?)", {id,
            Json{{"id",id},{"url",doujin::discovery::release_url(id)}}.dump()});
    for (int id=10; id<=13; ++id) {
        doujin::write(db.get(), "INSERT INTO copies(id,share_url,host,offer) VALUES (?,?,?,?)",
            {id,archive_url,"mediafire.com","{}"});
        doujin::write(db.get(), "INSERT INTO release_copies VALUES (?,?,1)", {id-9,id});
    }
    doujin::write(db.get(), "INSERT INTO release_copies VALUES (?,?,1)", {5,10});
    return db;
}
static Json jobs() {
    auto result=Json::array();
    for (int id=10; id<=13; ++id) result.push_back({{"id",id},{"share_url",archive_url}});
    return result;
}
static Json success(const Task& task) {
    return {{"archive",{{"format","zip"},{"audio_members",task.id-9},
        {"inventory",Json::array()},{"archive_http",{{"body_bytes",123}}}}},
        {"cover_payload","ffd8ff"},{"error",nullptr}};
}
int main() {
    {
        auto db=database();
        std::vector<int> observed;
        std::atomic<int> active=0,peak=0;
        auto result = doujin::archive_dispatch::run(db.get(),jobs(),Json::array({1,2,3,4,5}),3,100,
            [&](const Task& task,std::stop_token) {
                const int now=++active; peak.store(std::max(peak.load(),now));
                std::this_thread::sleep_for(std::chrono::milliseconds(task.id==10?30:3));
                --active;
                return success(task);
            }, [&](const Json& event) {
                const int id=event.at("id").get<int>();
                auto row=doujin::query(db.get(),"SELECT archive_checked,archive FROM copies WHERE id=?",{id});
                assert(doujin::next(row.get()));
                assert(sqlite3_column_int64(row.get(),0)==100);
                assert(doujin::string_column(row.get(),1).find("cover_payload") == std::string::npos);
                assert(event.at("cover_payload")=="ffd8ff");
                observed.push_back(id);
            },{},true,true);
        assert(result.at("attempted")==4 && result.at("selected")==4);
        assert(result.at("events").size()==4 && observed.size()==4 && peak.load()>1 && peak.load()<=3);
        assert(observed.front()!=10);
        assert(result.at("events").at(0).contains("cover_payload") == false);
        assert(result.at("events").at(0).at("ids").is_array());
        auto original = doujin::query(db.get(), "SELECT archive FROM copies WHERE id=10");
        assert(doujin::next(original.get()));
        assert(Json::parse(doujin::string_column(original.get(),0)).at("cover_probe_attempted")==true);
    }
    {
        auto db=database();
        std::stop_source stop;
        int callbacks=0;
        const auto result=doujin::archive_dispatch::run(db.get(),jobs(),nullptr,2,100,
            [](const Task& task, std::stop_token) {return success(task);},
            [&](const Json&) {++callbacks;stop.request_stop();},stop.get_token(),true,true);
        assert(result.at("cancelled")==true && callbacks==1 && result.at("attempted")==1);
        auto saved=doujin::query(db.get(),"SELECT COUNT(*) FROM copies WHERE archive_checked=100");
        assert(doujin::next(saved.get()) && sqlite3_column_int(saved.get(),0)==1);
    }
    {
        // Cancellation DURING commit still reports the durable row exactly once.
        auto db=database(); std::stop_source stop;
        struct Hook {std::stop_source* stop; bool fired=false;} hook{&stop};
        sqlite3_update_hook(db.get(),[](void* ctx,int,const char*,const char* table,sqlite3_int64) {
            auto& h=*static_cast<Hook*>(ctx);
            if(!h.fired && std::string(table)=="copies") {h.fired=true;h.stop->request_stop();}
        }, &hook);
        int reported=0;
        auto result=doujin::archive_dispatch::run(db.get(),jobs(),nullptr,1,100,
            [](const Task& task,std::stop_token){return success(task);},
            [&](const Json&){++reported;},stop.get_token(),true,true);
        assert(hook.fired && reported==1 && result.at("attempted")==1);
    }
    {
        auto db=database();
        auto invalid=jobs(); invalid.push_back(invalid.at(0));
        for (auto candidate : {invalid,Json::array({{{"id",10},{"share_url","https://evil.test/archive"}}}),
                Json::array({{{"id",999}}})}) {
            bool rejected=false;
            try {(void)doujin::archive_dispatch::run(db.get(),candidate,nullptr,1,100,
                [](const Task& task,std::stop_token){return success(task);},[](const Json&){});}
            catch(const std::runtime_error&){rejected=true;}
            assert(rejected);
        }
        bool wrong_ids=false;
        try {(void)doujin::archive_dispatch::run(db.get(),Json::array({{{"id",10},{"ids",Json::array({1})}}}),
            nullptr,1,100,[](const Task& task,std::stop_token){return success(task);},[](const Json&){});}
        catch(const std::runtime_error&){wrong_ids=true;}
        assert(wrong_ids); // shared copy must include 1 AND 5
    }
    {
        auto db=database();
        int fetched=0;
        auto result=doujin::archive_dispatch::run(db.get(),jobs(),nullptr,1,100,
            [&](const Task&,std::stop_token)->Json{++fetched;return {{"archive",nullptr},
                {"error","HTTP 429"},{"rate_limited",true}};},[](const Json&){});
        assert(fetched==1 && result.at("rate_limited")==true && result.at("attempted")==1);
    }
    std::puts("native archive dispatch: ordering, commits, shared copies, validation, 429, cancellation passed");
}
