#include "catalog_idle_details.hpp"
#include <cassert>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

using doujin::Json;

int main() {
    const auto flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_URI;
    auto db = doujin::connect("file:idle_details_contract?mode=memory&cache=shared", flags);
    auto observer = doujin::connect("file:idle_details_contract?mode=memory&cache=shared", flags);
    doujin::execute(db.get(), R"SQL(
        CREATE TABLE releases(id INTEGER PRIMARY KEY,listing TEXT NOT NULL,seen INTEGER NOT NULL);
        INSERT INTO releases VALUES
            (101,'{"id":101,"album":"Original"}',1),
            (102,'{"id":102,"album":"Second"}',1);
    )SQL");
    std::vector<std::int64_t> fetched, observed;
    const auto fetch = [&](std::int64_t id) -> Json {
        fetched.push_back(id);
        if (id == 102 && observed.size() == 1)
            throw std::runtime_error("Injected network interruption before commit");
        return {{"row",{{"id",id},{"album","Verified"},{"listed_at","2026-11-21"}}},
                {"forms",Json::array()}};
    };
    const auto event = [&](const Json& committed) {
        const auto id = committed.at("id").get<std::int64_t>();
        // A separate connection must see the committed row before the callback.
        auto row = doujin::query(observer.get(),"SELECT listing FROM releases WHERE id=?",{id});
        assert(doujin::next(row.get()));
        const auto saved = Json::parse(doujin::string_column(row.get(),0));
        assert(saved.at("album") == "Verified");
        assert(saved.at("listed_at") == "2026-11-21");
        observed.push_back(id);
    };
    bool interrupted = false;
    try { (void)doujin::idle_detail::run(db.get(), 1000, fetch, event); }
    catch (const std::runtime_error&) { interrupted = true; }
    assert(interrupted && fetched == std::vector<std::int64_t>({101,102}));
    assert(observed == std::vector<std::int64_t>({101}));
    // No replay of the committed item, despite a lost terminal result.
    const auto replay = doujin::idle_detail::run(db.get(), 1001,
        [&](std::int64_t id)->Json {
            fetched.push_back(id);
            return {{"row",{{"id",id},{"album","Verified"},{"listed_at","2026-11-21"}}},
                    {"forms",Json::array()}};
        }, event);
    assert(replay.at("events") == Json::array({{{"kind","idle_detail"},{"id",102}}}));
    assert(observed == std::vector<std::int64_t>({101,102}));
    assert(fetched == std::vector<std::int64_t>({101,102,102}));
    assert(doujin::idle_detail::run(db.get(), 1002,
        [](std::int64_t)->Json {throw std::runtime_error("Unexpected refetch");},
        [](const Json&){assert(false);}).at("events").empty());
    std::puts("native idle details: real SQLite cross-connection commit, interrupt and restart: PASS");
}
