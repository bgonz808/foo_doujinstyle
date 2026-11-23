#include "catalog_explicit_job_frontend.hpp"
#include <algorithm>
#include <cassert>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

using namespace doujin::explicit_job;
using Callback = std::function<void(const Json&)>;

static Options opts(Action action) {
    Options o;
    o.db = "catalog.db";
    o.output = "/tmp/download";
    o.root = "canonical-root";
    o.release_id = 52;
    o.action = action;
    return o;
}
static Json listing(std::int64_t id) {
    assert(id == 52);
    return {{"id", id}, {"url", "https://doujinstyle.com/?p=page&type=1&id=52"}};
}
static void must_reject(const std::function<void()>& action) {
    bool rejected = false;
    try { action(); } catch (const std::exception&) { rejected = true; }
    assert(rejected);
}
static Json offer_reply(std::string error = "") {
    const Json item{{"id",52},{"offers",error.empty() ? Json::array({Json{{"host","mediafire.com"}}}) : Json::array()},
                    {"error",error.empty() ? Json() : Json(error)}, {"statuses",Json::array()}};
    return {{"results",Json::array({item})},{"cancelled",false},{"rate_limited",false}};
}
static Json copy(std::int64_t id=71, std::string url="https://www.mediafire.com/file/abc123/pack.zip/file") {
    return {{"id",id},{"url",std::move(url)},{"host","mediafire.com"},{"filename","pack.zip"}};
}
static Json archive_event(std::int64_t copy_id, bool ok = true, std::string error = "") {
    return {{"id",copy_id},{"ids",Json::array({52})},
            {"ok",ok},{"error",ok ? Json() : Json(error)},
            {"format",ok ? Json("zip"):Json()},
            {"audio_members",ok ? Json(2):Json()},
            {"archive_http",ok ? Json{{"body_bytes",4096}}:Json()},
            {"cover_payload",Json()}};
}
static Json archive_terminal(Json event) {
    auto compact=event;compact.erase("cover_payload");
    return {{"events",Json::array({compact})},{"attempted",1},{"selected",1},
            {"cancelled",false},{"rate_limited",false}};
}
static Json publication() {
    return {{"references",2},{"written",1},
            {"notifications",Json::array({Json::array({52,"52-first.djt",true}),
                                           Json::array({52,"52-second.djt",false})})}};
}
int main() {
    std::vector<std::string> messages;
    auto emit=[&](std::string line){messages.push_back(std::move(line));};
    {
        int requests=0;
        auto submit=[&](Json cmd,const Callback&) -> Json {
            ++requests;
            assert(cmd.at("db")=="catalog.db");
            assert(cmd.at("operation")=="archive_download_job");
            return {{"status","saved"},{"archive",{{"path","/tmp/download/abc.zip"},
                    {"size",2048},{"authenticated",false},{"integrity","transport_and_length_only"}}}};
        };
        download(opts(Action::download),submit,listing,emit);
        assert(requests==1);
        assert(messages.back().find("2.00 KiB HTTPS transfer; no whole-file MAC)")!=std::string::npos);
    }
    messages.clear();
    {
        int offers=0,downloads=0;
        auto submit=[&](Json cmd,const Callback&) -> Json {
            const auto op=cmd.at("operation").get<std::string>();
            if(op=="catalog_offer_batch"){
                ++offers;assert(cmd.at("all_forms")==true);
                return offer_reply();
            }
            assert(op=="archive_download_job");
            ++downloads;
            if(downloads==1)return {{"status","no_offer"}};
            return {{"status","saved"},{"archive",{{"path","/tmp/download/mega.zip"},
                {"size",0},{"authenticated",true}}}};
        };
        download(opts(Action::download),submit,listing,emit);
        assert(offers==1 && downloads==2);
        assert(messages.back().find("authenticated with MEGA MAC")!=std::string::npos);
    }
    messages.clear();
    {
        int offers=0,downloads=0;
        auto submit=[&](Json cmd,const Callback&) -> Json {
            if(cmd.at("operation")=="archive_download_job"){++downloads;return {{"status","no_offer"}};}
            ++offers;return offer_reply("Previous POST outcome unresolved; manual review required");
        };
        must_reject([&]{ download(opts(Action::download),submit,listing,emit); });
        assert(offers==1 && downloads==1 && messages.empty());
    }
    {
        auto bad=[](Json,const Callback&) -> Json {
            return {{"status","saved"},{"archive",{{"path","/tmp/x"},{"size",1},
                       {"authenticated",true},{"integrity","transport_and_length_only"}}}};
        };
        must_reject([&]{download(opts(Action::download),bad,listing,emit);});
    }
    {
        messages.clear();
        std::vector<std::string> calls;
        auto submit=[&](Json cmd,const Callback& cb)->Json{
            const auto op=cmd.at("operation").get<std::string>();
            calls.push_back(op);
            if(op=="archive_download_plan")return {{"status","ready"},{"copy",copy()}};
            if(op=="catalog_archive_dispatch"){
                assert(cmd.at("workers")==1 && cmd.at("cover")==false);
                assert(cmd.at("ids")==Json::array({52}));
                auto e=archive_event(71);cb(e);return archive_terminal(e);
            }
            assert(op=="publish_many");
            assert(cmd.at("copy_id")==71 && cmd.at("root")=="canonical-root");
            return publication();
        };
        repair(opts(Action::repair),submit,listing,emit);
        assert(calls==std::vector<std::string>({"archive_download_plan","catalog_archive_dispatch","publish_many"}));
        assert(messages.at(messages.size()-3)=="\x1c" "52\t52-first.djt");
        assert(messages.at(messages.size()-2)=="\x1c" "52\t52-second.djt");
        assert(messages.back().find("1 references updated, 2 queued for reload")!=std::string::npos);
    }
    {
        messages.clear();
        int plans=0,offers=0,probes=0,publishes=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json {
            const auto op=cmd.at("operation").get<std::string>();
            if(op=="archive_download_plan"){
                ++plans;
                if(plans==1) return {{"status","ready"},{"copy",copy()}};
                assert(cmd.at("exclude")=="https://www.mediafire.com/file/abc123/pack.zip/file");
                return {{"status","ready"},{"copy",copy(72,"https://www.mediafire.com/file/replacement/pack.zip/file")}};
            }
            if(op=="catalog_offer_batch"){++offers;assert(cmd.at("all_forms")==false);return offer_reply();}
            if(op=="catalog_archive_dispatch"){
                ++probes;auto e=archive_event(probes==1?71:72,probes!=1,"HTTP 403");cb(e);return archive_terminal(e);
            }
            ++publishes;assert(cmd.at("copy_id")==72);return publication();
        };
        repair(opts(Action::repair),submit,listing,emit);
        assert(plans==2 && offers==1 && probes==2 && publishes==1);
    }
    {
        messages.clear();
        int submits=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json{
            ++submits; const auto op=cmd.at("operation").get<std::string>();
            if(op=="archive_download_plan")return {{"status","ready"},{"copy",copy()}};
            if(op=="catalog_archive_dispatch") {
                auto e=archive_event(71);
                cb(e);
                auto result=archive_terminal(e);
                result["cancelled"]=true;
                return result;
            }
            throw std::runtime_error("Publication after cancellation");
        };
        must_reject([&]{repair(opts(Action::repair),submit,listing,emit);});
        assert(submits==2 && messages.empty());
    }
    {
        messages.clear();
        int submits=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json{
            ++submits; const auto op=cmd.at("operation").get<std::string>();
            if(op=="archive_download_plan")return {{"status","ready"},{"copy",copy()}};
            if(op=="catalog_archive_dispatch"){auto e=archive_event(71);cb(e);return archive_terminal(e);}
            return {{"references",2},{"written",1},
                    {"notifications",Json::array({Json::array({99,"wrong.djt",true})})}};
        };
        must_reject([&]{repair(opts(Action::repair),submit,listing,emit);});
        assert(submits==3);
        assert(std::none_of(messages.begin(),messages.end(),[](const auto& s){return s.starts_with("\x1c");}));
    }
}
