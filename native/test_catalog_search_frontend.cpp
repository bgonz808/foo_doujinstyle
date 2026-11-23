#include "catalog_search_frontend.hpp"
#include <cassert>
#include <algorithm>
#include <set>
#include <string>
#include <vector>

using doujin::search_frontend::Json;

int main() {
    using namespace doujin::search_frontend;
    const auto escaped = record(Json{{"id", 52}, {"album", "Japanese\talbum\nname"},
        {"artist", "artist"}, {"tags", Json::array({"C100", "M3-2026"})},
        {"technical", {{"advertised_format", "WAV"}}}}, "2026/11/23");
    assert(escaped.front() == '\x1e');
    assert(escaped.find("Japanese album name") != std::string::npos);
    assert(std::count(escaped.begin(), escaped.end(), '\t') == 7);
    assert(escaped.find("C100\x1fM3-2026") != std::string::npos);
    assert(printable_size(0) == "0 B");
    assert(printable_size(1024) == "1.00 KiB");

    Options options;
    options.db = "fixture.sqlite";
    options.output = "/tmp/fixture";
    options.root = "fixture-key";
    options.query = "tag:m3";
    options.result_rows = true;
    const Json row{{"id", 52}, {"album", "Fixture album"},
                   {"artist", "Artist"}, {"tags", Json::array({"C100"})}};
    Json batch{{"releases", Json::array({row})}, {"rows_on_page", 1},
               {"total_results", 1}, {"cache_hit", false},
               {"more", false}, {"site_icon", nullptr}};
    Json page{{"kind", "search_page"}, {"page", 1}, {"batch", batch},
              {"ui_dates", {{"52", "2026/11/23"}}}};
    const Json ref = Json::array({52, "52-012345678901234567890123.djt", true});
    std::vector<std::string> output;
    std::set<std::string> calls;
    int publishes = 0;
    auto submit = [&](Json cmd, const std::function<void(const Json&)>& event) -> Json {
        assert(cmd.at("db") == "fixture.sqlite");
        const auto op = cmd.at("operation").get<std::string>();
        calls.insert(op);
        if (op == "site_search_job") {
            assert(cmd.at("stream_events") == true);
            assert(cmd.at("query") == "tag:m3");
            event(page);
            return {{"pages", Json::array({batch})}, {"late_error", ""}};
        }
        if (op == "publish_many") {
            assert(cmd.at("ids") == Json::array({52}));
            ++publishes;
            return {{"references", 1}, {"written", publishes == 1 ? 1 : 0},
                    {"notifications", Json::array({ref})}};
        }
        if (op == "catalog_index_dispatch") {
            Json offer{{"id", 52}, {"had_observation", false},
                       {"offers", Json::array()}, {"statuses", Json::array()},
                       {"error", nullptr}};
            event(offer);
            return {{"events", Json::array({offer})}, {"archives", Json::array()},
                    {"offers_count", 1}, {"initial_archives_count", 0},
                    {"cancelled", false}, {"rate_limited", false}};
        }
        throw std::runtime_error("Unexpected operation");
    };
    auto emit = [&](std::string text) { output.push_back(std::move(text)); };
    const auto totals = run(options, submit, emit,
        [&](std::int64_t) -> Json { return row; },
        [&](std::int64_t, std::string) {},
        [&](std::int64_t, std::string) {});
    assert(totals.listings == 1 && totals.references_written == 1);
    assert(totals.releases_with_tracks == 1);
    assert(totals.archives_attempted == 0);
    assert(publishes == 2);
    assert(calls == std::set<std::string>({"site_search_job", "publish_many", "catalog_index_dispatch"}));
    assert(std::any_of(output.begin(), output.end(), [](const auto& line) {
        return line.starts_with("\x1e" "52\tFixture album");
    }));
    assert(std::find(output.begin(), output.end(),
        "Page 1  •  1 results of 1 matches  •  saved by native catalog") != output.end());
    assert(std::find(output.begin(), output.end(),
        "  52   Offer response committed") != output.end());
    assert(std::find(output.begin(), output.end(),
        "Already indexed: 1 track references (1 written).") != output.end());
    assert(std::count_if(output.begin(), output.end(), [](const auto& line) {
        return line.starts_with("\x1c" "52\t");
    }) == 1);

    auto broken = submit;
    bool rejected = false;
    try {
        Options invalid = options;
        invalid.pages = 4;
        (void)run(invalid, broken, emit, [&](std::int64_t) -> Json { return row; },
                  [&](std::int64_t, std::string) {}, [&](std::int64_t, std::string) {});
    } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
}
