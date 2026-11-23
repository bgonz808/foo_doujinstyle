#include "catalog_chart_frontend.hpp"
#include <cassert>
#include <string>
#include <vector>

using Json = doujin::chart_frontend::Json;

int main() {
    doujin::search_frontend::Options opt;
    opt.db = "mock.db";
    opt.output = "/tmp/out";
    opt.result_rows = true;
    std::vector<std::string> messages;
    int publishing = 0;
    const Json row{{"id", 52}, {"album", "Fixture Chart Album"},
                   {"_native_ui_date", "2026/11/23"},
                   {"chart_rank", 1}, {"chart_downloads", 125000}};
    Json chart{{"kind", "chart_stage"}, {"period", "month"},
               {"source", "cached"}, {"releases", Json::array({row})},
               {"ranks", Json::array({Json{{"id",52}, {"chart_rank",1}, {"chart_downloads",125000}}})},
               {"site_icon", nullptr}};
    auto submit = [&](Json request, const std::function<void(const Json&)>& on_event) -> Json {
        const auto operation = request.at("operation").get<std::string>();
        if (operation == "catalog_chart_job") {
            assert(on_event);
            on_event(chart);
            return {{"stages", Json::array({chart})}, {"warnings", Json::array()}};
        }
        if (operation == "publish_many") {
            ++publishing;
            return {{"references", 0}, {"written", 0}, {"notifications", Json::array()}};
        }
        if (operation == "catalog_index_dispatch")
            return {{"events", Json::array()}, {"offers_count", 0},
                    {"initial_archives_count", 0}, {"archives", Json::array()},
                    {"cancelled", false}, {"rate_limited", false}};
        throw std::runtime_error("Unexpected chart operation");
    };
    auto emit=[&](std::string line){ messages.push_back(std::move(line)); };
    const auto totals = doujin::chart_frontend::run(
        opt, "month", submit, emit,
        [&](std::int64_t) -> Json { return row; },
        [&](std::int64_t, std::string){},
        [&](std::int64_t, std::string){});
    assert(totals.listings == 1 && publishing == 2);
    bool saw_ids=false,saw_rank=false,saw_caption=false;
    for (const auto& line:messages){
        saw_ids |= line == "\x15" "52";
        saw_rank |= line.find("#1 · 125,000 downloads") != std::string::npos;
        saw_caption |= line.find("This Month  •  1 ranked releases  •  cached") != std::string::npos;
    }
    assert(saw_ids && saw_rank && saw_caption);
    bool rejected=false;
    try { (void)doujin::chart_frontend::run(opt,"not_a_chart",submit,emit,
                [&](std::int64_t)->Json{return row;},
                [&](std::int64_t,std::string){},[&](std::int64_t,std::string){}); }
    catch (const std::runtime_error&){ rejected=true; }
    assert(rejected);
}
