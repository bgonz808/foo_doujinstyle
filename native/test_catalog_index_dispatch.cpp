#include "catalog_index_dispatch.hpp"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <stop_token>
#include <string>
#include <thread>
#include <vector>

using doujin::Json;
using doujin::offer_executor::Task;

static auto database() {
    auto db = doujin::connect(":memory:", SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
    doujin::execute(db.get(), R"SQL(
        CREATE TABLE releases(id INTEGER PRIMARY KEY, listing TEXT NOT NULL,
            offer_checked INTEGER, offer_error TEXT);
        CREATE TABLE copies(id INTEGER PRIMARY KEY, destination TEXT UNIQUE NOT NULL,
            share_url TEXT, host TEXT, offer TEXT,
            archive_checked INTEGER, archive_error TEXT, archive TEXT);
        CREATE TABLE release_copies(release_id INTEGER, copy_id INTEGER, source TEXT,
            first_seen INTEGER, last_seen INTEGER, current INTEGER,
            PRIMARY KEY(release_id,copy_id,source));
    )SQL");
    for (int id = 1; id <= 8; ++id)
        doujin::write(db.get(), "INSERT INTO releases(id,listing) VALUES (?,?)",
            {id, Json{{"id", id}, {"url", doujin::discovery::release_url(id)}}.dump()});
    doujin::execute(db.get(), R"SQL(
        INSERT INTO copies(id,destination,share_url,host,offer) VALUES
        (99,'known','https://www.mediafire.com/file/known/known.zip/file','mediafire.com','{}');
        INSERT INTO release_copies VALUES (1,99,'doujinstyle',1,1,1), (2,99,'doujinstyle',1,1,1);
    )SQL");
    return db;
}

static Json good(const Task& task) {
    // Two offers lead to the same mediafire destination and copy ID.
    const auto identity = task.id == 2 ? 1 : task.id;
    return {{"observation", nullptr}, {"offers", Json::array({Json::array({
        "doujinstyle", Json{{"host","www.mediafire.com"},{"filename","album.zip"},{"format_hint","zip"}},
        "https://www.mediafire.com/file/FILE" + std::to_string(identity) + "/album.zip"})})},
        {"error", nullptr}};
}

int main() {
    {
        auto db = database();
        std::atomic<int> active{0}, max_active{0}, fetched{0};
        std::vector<int> reported;
        const auto result = doujin::index_dispatch::run(db.get(), Json::array({1,2,3,4,5,6,7,8}),
            3, 1'000'000,
            [&](const Task& task, std::stop_token stop) -> Json {
                assert(!stop.stop_requested());
                ++fetched;
                const auto now = ++active;
                max_active.store(std::max(max_active.load(), now));
                std::this_thread::sleep_for(std::chrono::milliseconds(task.id == 1 ? 25 : 2));
                --active;
                return good(task);
            }, [&](const Json& event) {
                const auto id = event.at("id").get<int>();
                auto row = doujin::query(db.get(), "SELECT offer_checked FROM releases WHERE id=?", {id});
                assert(doujin::next(row.get()) && sqlite3_column_int(row.get(), 0) == 1'000'000);
                assert(event.at("statuses").size() == event.at("offers").size());
                assert(event.at("statuses").at(0) == "Found www.mediafire.com offer");
                reported.push_back(id);
            });
        assert(result.at("offers_count") == 8);
        assert(result.at("initial_archives_count") == 1);
        assert(result.at("events").size() == 8 && reported.size() == 8);
        assert(!result.at("cancelled").get<bool>() && !result.at("rate_limited").get<bool>());
        assert(fetched == 8 && max_active > 1 && max_active <= 3);
        assert(reported.front() != 1); // completion order, not submit order
        // Eight completed offers, one duplicate destination, one known archive.
        assert(result.at("archives").size() == 8);
        std::set<std::int64_t> unique;
        for (const auto& archive : result.at("archives"))
            assert(unique.insert(archive.at("id").get<std::int64_t>()).second);
        auto copies = doujin::query(db.get(), "SELECT COUNT(*) FROM copies");
        assert(doujin::next(copies.get()) && sqlite3_column_int(copies.get(), 0) == 8);
    }
    {
        auto db = database();
        std::stop_source stop;
        int reported = 0;
        const auto result = doujin::index_dispatch::run(db.get(), Json::array({1,2,3,4,5,6,7,8}),
            2, 1'000'000, [&](const Task& task, std::stop_token) { return good(task); },
            [&](const Json&) { ++reported; stop.request_stop(); }, stop.get_token());
        assert(result.at("cancelled").get<bool>());
        assert(reported == 1 && result.at("events").size() == 1);
        auto committed = doujin::query(db.get(), "SELECT COUNT(*) FROM releases WHERE offer_checked=1000000");
        assert(doujin::next(committed.get()) && sqlite3_column_int(committed.get(), 0) == 1);
    }
    {
        // Stop may arrive while SQLite is writing. The transaction must not
        // become an invisible durable commit: exactly one event is delivered.
        auto db = database();
        std::stop_source stop;
        struct Hook { std::stop_source* stop; bool fired = false; } hook{&stop};
        sqlite3_update_hook(db.get(), [](void* data, int, const char*, const char* table, sqlite3_int64) {
            auto& h = *static_cast<Hook*>(data);
            if (!h.fired && std::string(table) == "releases") {
                h.fired = true;
                h.stop->request_stop();
            }
        }, &hook);
        int reported = 0;
        const auto result = doujin::index_dispatch::run(db.get(), Json::array({1,2}),
            1, 1'000'000, [&](const Task& task, std::stop_token) { return good(task); },
            [&](const Json&) { ++reported; }, stop.get_token());
        assert(hook.fired && result.at("cancelled").get<bool>());
        assert(reported == 1 && result.at("events").size() == 1);
        auto committed = doujin::query(db.get(), "SELECT COUNT(*) FROM releases WHERE offer_checked=1000000");
        assert(doujin::next(committed.get()) && sqlite3_column_int(committed.get(), 0) == 1);
    }
    {
        auto db = database();
        int fetched = 0;
        const auto limited = doujin::index_dispatch::run(db.get(), Json::array({1,2,3,4,5,6,7,8}),
            1, 1'000'000,
            [&](const Task& task, std::stop_token) -> Json {
                ++fetched;
                assert(task.id == 8);  // SQL selects latest unobserved release first
                return {{"observation", nullptr}, {"offers", Json::array()},
                        {"error", "HTTP 429"}, {"rate_limited", true}};
            }, [](const Json&) {});
        assert(limited.at("rate_limited").get<bool>() && !limited.at("cancelled").get<bool>());
        assert(fetched == 1 && limited.at("events").size() == 1);
        auto committed = doujin::query(db.get(), "SELECT COUNT(*) FROM releases WHERE offer_checked=1000000");
        assert(doujin::next(committed.get()) && sqlite3_column_int(committed.get(), 0) == 1);
    }
    {
        auto db = database();
        for (auto invalid : {Json::array({1,1}), Json::array({-1}), Json::array({100}), Json::array()}) {
            bool rejected = false;
            try { (void)doujin::index_dispatch::run(db.get(), invalid, 3, 1'000'000,
                  [](const Task& task, std::stop_token) { return good(task); }, [](const Json&) {}); }
            catch (const std::runtime_error&) { rejected = true; }
            assert(rejected);
        }
    }
    std::puts("native index dispatch: completion, commit-order, dedup, 429 and stop passed");
}
