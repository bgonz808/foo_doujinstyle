#include "catalog_offer_executor.hpp"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <stop_token>
#include <string>
#include <thread>

using doujin::Json;
using doujin::offer_executor::Task;
namespace fs = std::filesystem;

static Json successful(const Task& task) {
    return {{"observation", nullptr},
        {"offers", Json::array({Json::array({"doujinstyle", Json{{"host","www.mediafire.com"},
            {"filename", "album.zip"}, {"format_hint", "zip"}},
            "https://www.mediafire.com/file/ABC" + std::to_string(task.id) + "/album.zip"})})},
        {"error", nullptr}};
}

int main() {
    const auto path = fs::temp_directory_path() / ("dj-offer-test-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".db");
    try {
        auto db = doujin::connect(path.string(), SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
        doujin::execute(db.get(), R"SQL(
            CREATE TABLE releases(id INTEGER PRIMARY KEY, listing TEXT NOT NULL,
                offer_checked INTEGER, offer_error TEXT);
            CREATE TABLE copies(id INTEGER PRIMARY KEY, destination TEXT UNIQUE NOT NULL,
                share_url TEXT, host TEXT, offer TEXT);
            CREATE TABLE release_copies(release_id INTEGER, copy_id INTEGER, source TEXT,
                first_seen INTEGER, last_seen INTEGER, current INTEGER,
                PRIMARY KEY(release_id,copy_id,source));
        )SQL");
        for (int id = 1; id <= 6; ++id)
            doujin::write(db.get(), "INSERT INTO releases(id,listing) VALUES (?,?)",
                {id, Json{{"id",id},{"url",doujin::discovery::release_url(id)}}.dump()});
        std::atomic<int> concurrent{0}, peak{0}, calls{0};
        auto fetch = [&](const Task& task, std::stop_token) -> Json {
            ++calls;
            const auto active = ++concurrent;
            peak.store(std::max(peak.load(), active));
            std::this_thread::sleep_for(std::chrono::milliseconds(task.id == 1 ? 35 : 4));
            --concurrent;
            if (task.id == 4) throw std::runtime_error("uncertain POST timeout");
            return successful(task);
        };
        std::vector<int> reported;
        const auto events = doujin::offer_executor::run(db.get(), Json::array({1,2,3,4,5,6}),
            3, 100, fetch, [&](const Json& event) {
                const auto id = event.at("id").get<int>();
                // Every callback must observe the committed offer/error state.
                const auto row = doujin::query(db.get(),
                    "SELECT offer_checked,offer_error FROM releases WHERE id=?", {id});
                assert(doujin::next(row.get()));
                assert(sqlite3_column_int(row.get(), 0) == 100);
                if (id == 4) assert(sqlite3_column_type(row.get(), 1) == SQLITE_TEXT);
                reported.push_back(id);
            });
        assert(!events.at("cancelled").get<bool>());
        assert(events.at("results").size() == 6 && reported.size() == 6);
        assert(peak.load() > 1 && peak.load() <= 3 && calls.load() == 6);
        assert(reported.front() != 1); // completion order, not submission order
        auto row = doujin::query(db.get(), "SELECT COUNT(*) FROM copies");
        assert(doujin::next(row.get()) && sqlite3_column_int(row.get(), 0) == 5);
        auto bad = doujin::query(db.get(), "SELECT offer_error FROM releases WHERE id=4");
        assert(doujin::next(bad.get()) && doujin::string_column(bad.get(),0) == "uncertain POST timeout");
        for (auto ids : {Json::array({1,1}), Json::array({0}), Json::array({-1}), Json::array({999})}) {
            bool rejected = false;
            try { (void)doujin::offer_executor::run(db.get(), ids, 1, 200, fetch, [](const Json&) {}); }
            catch (const std::runtime_error&) { rejected = true; }
            assert(rejected);
        }
        // Stop after the first committed result; workers may finish network I/O
        // but must not emit another event or write late results to SQLite.
        std::stop_source stop;
        int callbacks = 0;
        const auto stopped = doujin::offer_executor::run(db.get(), Json::array({1,2,3,4}),
            2, 300, fetch, [&](const Json&) { ++callbacks; stop.request_stop(); }, stop.get_token());
        assert(stopped.at("cancelled") && callbacks == 1 && stopped.at("results").size() == 1);
        auto remaining = doujin::query(db.get(), "SELECT COUNT(*) FROM releases WHERE offer_checked=300");
        assert(doujin::next(remaining.get()) && sqlite3_column_int(remaining.get(),0) == 1);
        // Reject malformed provider evidence before inserting any copy.
        bool invalid = false;
        try { (void)doujin::offer_executor::link(Json::array({"doujinstyle",
            Json{{"host","www.mediafire.com"}}, "https://attacker.test/evil.zip"})); }
        catch (const std::runtime_error&) { invalid = true; }
        assert(invalid);
        assert(doujin::offer_executor::catalog_hint("[NBCD-025] Album.zip") == "NBCD-025");
        assert(doujin::offer_executor::catalog_hint("［ＮＢＣＤ-０２５］ Album.zip") == "NBCD-025");
        assert(doujin::offer_executor::destination_key(
            "https://www.mediafire.com/file/ABC12/album.zip/file", "www.mediafire.com") ==
            "mediafire:file:ABC12");
        assert(doujin::offer_executor::destination_key(
            "https://www.mediafire.com/file/ABC12/extra/invalid/path", "www.mediafire.com").starts_with("url:"));
        bool bad_source = false;
        try { (void)doujin::offer_executor::link(Json::array({"evil-source",
            Json{{"host","www.mediafire.com"}}, "https://www.mediafire.com/file/ABC"})); }
        catch (const std::runtime_error&) { bad_source = true; }
        assert(bad_source);
        int requests_after_429 = 0;
        const auto limited = doujin::offer_executor::run(db.get(), Json::array({1,2,3}),
            1, 400, [&](const Task& task, std::stop_token) -> Json {
                ++requests_after_429;
                assert(task.id == 1);
                return {{"observation", nullptr}, {"offers", Json::array()},
                        {"error", "HTTP 429"}, {"rate_limited", true}};
            }, [](const Json&) {});
        assert(requests_after_429 == 1 && limited.at("rate_limited").get<bool>());
        assert(limited.at("results").size() == 1);
        auto rate_row = doujin::query(db.get(), "SELECT offer_checked,offer_error FROM releases WHERE id=1");
        assert(doujin::next(rate_row.get()) && sqlite3_column_int64(rate_row.get(),0) == 400);
        assert(doujin::string_column(rate_row.get(),1) == "HTTP 429");
        std::puts("native bounded offer executor: passed");
    } catch (...) {
        fs::remove(path);
        throw;
    }
    fs::remove(path);
}
