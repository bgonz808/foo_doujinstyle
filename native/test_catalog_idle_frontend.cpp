#include "catalog_idle_frontend.hpp"
#include <cassert>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <set>

using Json = nlohmann::json;
using doujin::idle_frontend::Options;
using Callback = std::function<void(const Json&)>;

struct Fixture {
    std::vector<std::string> calls, output;
    std::set<std::int64_t> committed;
    int page = 0;
    bool interrupted = false;
    bool tamper_details = false, tamper_archive = false;
    int art_count = 0;
    Json submit(const Json& request, const Callback& notify) {
        const auto op = request.at("operation").get<std::string>();
        calls.push_back(op);
        if (op == "idle_wait") return {{"ready", true}};
        if (op == "site_resource") {
            assert(request.at("kind") == "robots");
            assert(request.at("url") == "https://doujinstyle.com/robots.txt");
            return {{"status",200},{"body","557365722d6167656e743a202a0a446973616c6c6f773a202f0a"}};
        }
        if (op == "catalog_idle_plan") {
            const int layers = request.at("layers").get<int>();
            if (layers == 4) return {{"links", {{{"id", 12}}}}};
            return {{"archives", {{{"id", 13}, {"share_url", "https://mega.nz/file/example"},
                                     {"ids", {11}}, {"headers", true}, {"cover", true}}}}};
        }
        if (op == "catalog_idle_detail_job") {
            committed.insert(11); // model the SQLite commit that must precede the event
            if (notify) notify({{"kind", "idle_detail"}, {"id", 11}});
            return {{"events", tamper_details ? Json::array({{{"kind", "idle_detail"}, {"id", 999}}}) : Json::array({{{"kind", "idle_detail"}, {"id", 11}}})}};
        }
        if (op == "catalog_offer_batch") {
            assert(request.at("ids") == Json::array({12}));
            return {{"cancelled", false}, {"rate_limited", false},
                    {"results", {{{"id", 12}, {"offers", Json::array({"offer"})}}}}};
        }
        if (op == "catalog_archive_dispatch") {
            const Json event{{"id",13}, {"ids",{11}}, {"ok",true}, {"error",nullptr},
                             {"cover_payload","ffd8ff"}, {"format","zip"}, {"audio_members",7},
                             {"archive_http",{{"body_bytes",123}}}};
            committed.insert(13);
            if (notify) notify(event);
            if (interrupted) throw std::runtime_error("Stop during event");
            auto sanitized = event;
            sanitized.erase("cover_payload");
            if (tamper_archive) sanitized["id"] = 999;
            return {{"cancelled",false},{"rate_limited",false},{"events",{sanitized}},
                    {"attempted",1},{"selected",1}};
        }
        if (op == "publish_many") {
            assert(committed.contains(13));
            assert(request.at("ids") == Json::array({11}));
            assert(request.at("root") == "output");
            return {{"references",1},{"notifications",Json::array({Json::array({11,"2026/11/11.djt",true})})}};
        }
        if (op == "bangumi_idle") return Json::array({11});
        if (op == "catalog_browse_step") {
            ++page;
            if (page == 1) {
                assert(request.at("state").is_null());
                return {{"done",false},{"saved",1},{"rows",20},{"state",{{"next",1}}}};
            }
            assert(request.at("state").at("next") == 1);
            return {{"done",true},{"saved",0},{"rows",10},{"state",nullptr},
                    {"message","Idle backfill reached the newest page."}};
        }
        throw std::runtime_error("Unexpected native operation: " + op);
    }
    void run(const Options& options) {
        doujin::idle_frontend::run(options,
            [this](const Json& r, const Callback& callback){ return submit(r,callback); },
            [this](const std::string& line) { output.push_back(line); },
            [this](const std::string& directory,std::int64_t id,const std::string& hex){
                assert(directory == "artwork" && id == 11 && hex == "ffd8ff");
                ++art_count;
            });
    }
};

int main() {
    const Options options{"catalog.db","output","output","artwork","hostzlib",63,true};
    {
        Fixture fixture;
        fixture.run(options);
        assert(fixture.art_count == 1);
        assert(fixture.committed == std::set<std::int64_t>({11,13}));
        assert(fixture.page == 2);
        assert(fixture.output.front() == "\x1b");
        assert(fixture.output.back() == "Selected idle indexing pass finished.");
        auto at = [](const std::vector<std::string>& events, std::string value) {
            const auto it = std::find(events.begin(),events.end(),value);
            assert(it != events.end());
            return it-events.begin();
        };
        assert(at(fixture.output,"  11   Release details saved") <
               at(fixture.output,"  12   Download link saved"));
        assert(at(fixture.output,"  11   native archive committed  •  7 audio members  •  123 B read") <
               at(fixture.output,"\x19" "11\t2026/11/11.djt"));
        assert(at(fixture.output,"DoujinStyle's robots.txt excludes this crawler. Continuing the requested rate-limited backfill.") <
               at(fixture.output,"Idle backfill armed. Waiting for 30 seconds of Windows input idle."));
        assert(at(fixture.output,"\x1a" "11") <
               at(fixture.output,"Idle backfill reached the newest page."));
    }
    {
        Fixture fixture;
        fixture.interrupted=true;
        bool threw=false;
        try { fixture.run(options); } catch (const std::runtime_error&) {threw=true;}
        assert(threw);
        assert(fixture.committed.contains(13));
        assert(std::find(fixture.calls.begin(),fixture.calls.end(),"publish_many") == fixture.calls.end());
        assert(fixture.output.back() != "Selected idle indexing pass finished.");
    }
    {
        Fixture fixture;
        auto details = options;
        details.layers = 2;
        fixture.run(details);
        assert(fixture.calls == std::vector<std::string>({"catalog_idle_detail_job"}));
        assert(fixture.output.back() == "Selected idle indexing pass finished.");
    }
    {
        Fixture fixture;
        fixture.tamper_details = true;
        bool rejected = false;
        try { fixture.run(options); } catch (const std::runtime_error&) {rejected = true;}
        assert(rejected && fixture.calls.size() == 1);
        assert(fixture.output.back() != "Selected idle indexing pass finished.");
    }
    {
        Fixture fixture;
        fixture.tamper_archive = true;
        bool rejected = false;
        try { fixture.run(options); } catch (const std::runtime_error&) {rejected = true;}
        assert(rejected);
        assert(std::find(fixture.calls.begin(),fixture.calls.end(),"publish_many") == fixture.calls.end());
    }
    for (int layers : {-1,0,64,100}) {
        bool rejected=false;
        auto invalid=options;
        invalid.layers=layers;
        try {Fixture{}.run(invalid);}catch(const std::runtime_error&){rejected=true;}
        assert(rejected);
    }
    std::cout << "native idle frontend parity and interruption cases: PASS\n";
}
