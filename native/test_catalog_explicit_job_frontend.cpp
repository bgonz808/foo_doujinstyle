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
    // Input validation is a security boundary, not only a pretty error.
    {
        auto invalid = opts(Action::download); invalid.release_id = 0;
        must_reject([&]{ check_options(invalid); });
        invalid = opts(Action::download); invalid.db.clear();
        must_reject([&]{ check_options(invalid); });
        must_reject([&]{ (void)checked_path(Json(7)); });
        must_reject([&]{ (void)checked_path(Json("/tmp/bad\npath")); });
        must_reject([&]{ (void)checked_path(Json("")); });
        must_reject([&]{ (void)checked_copy(Json{{"status","missing_release"}},52); });
        must_reject([&]{ (void)checked_copy(Json{{"status","ready"},{"copy",{{"id",17},
            {"url","https://www.mediafire.com/file/abc"},{"host","fake.example"}}}},52); });
        assert(checked_copy(Json{{"status","no_offer"}},52).is_null());
    }
    // Ambiguous native POST state must fail and never trigger another request.
    {
        const auto good = offer_reply();
        auto bad=good; bad["cancelled"]=true;
        must_reject([&]{ (void)checked_offer(bad,52); });
        bad=good; bad["rate_limited"]=true;
        must_reject([&]{ (void)checked_offer(bad,52); });
        bad=good; bad["results"]=Json::array();
        must_reject([&]{ (void)checked_offer(bad,52); });
        bad=good; bad["results"][0]["id"]=53;
        must_reject([&]{ (void)checked_offer(bad,52); });
        int requests=0;
        bool previously_refreshed=false;
        auto submit=[&](Json req,const Callback&)->Json {
            ++requests; assert(req.at("all_forms")==false); return offer_reply();
        };
        auto o=opts(Action::repair);
        refresh_offer_once(o,submit,previously_refreshed,false);
        must_reject([&]{ refresh_offer_once(o,submit,previously_refreshed,false); });
        assert(requests==1);
    }
    // A completed request that still finds no offer cannot enter a POST loop.
    {
        messages.clear();
        int offers=0,downloads=0;
        auto submit=[&](Json cmd,const Callback&)->Json {
            if(cmd.at("operation")=="archive_download_job"){
                ++downloads; return {{"status","no_offer"}};
            }
            ++offers; return offer_reply();
        };
        must_reject([&]{ download(opts(Action::download),submit,listing,emit); });
        assert(offers==1 && downloads==2 && messages.empty());
    }
    // No published output on malformed integrity; don't claim a MAC on HTTPS.
    {
        messages.clear();
        auto submit=[&](Json,const Callback&)->Json {
            return {{"status","saved"},{"archive",{{"path","/tmp/claimed.zip"},
                {"size",10},{"authenticated",false},{"integrity","verified_mac"}}}};
        };
        must_reject([&]{ download(opts(Action::download),submit,listing,emit); });
        assert(messages.empty());
    }
    {
        messages.clear();
        int refreshes=0,plans=0;
        auto submit=[&](Json cmd,const Callback&)->Json {
            if(cmd.at("operation")=="archive_download_plan"){
                ++plans; return {{"status","no_offer"}};
            }
            ++refreshes; return offer_reply();
        };
        must_reject([&]{ repair(opts(Action::repair),submit,listing,emit); });
        assert(refreshes==1 && plans==2 && messages.empty());
    }
    // The event must be committed, for the selected copy, and present in the
    // final response. Neither a duplicate nor terminal mismatch is publishable.
    {
        messages.clear();
        int requested=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json {
            ++requested;
            if(cmd.at("operation")=="archive_download_plan")
                return {{"status","ready"},{"copy",copy()}};
            if(cmd.at("operation")=="catalog_archive_dispatch"){
                auto evt=archive_event(72); cb(evt);
                return archive_terminal(evt);
            }
            throw std::runtime_error("Unexpected publish after forged event");
        };
        must_reject([&]{ repair(opts(Action::repair),submit,listing,emit); });
        assert(requested==2 && messages.empty());
    }
    {
        messages.clear();
        int requested=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json {
            ++requested;
            if(cmd.at("operation")=="archive_download_plan")
                return {{"status","ready"},{"copy",copy()}};
            if(cmd.at("operation")=="catalog_archive_dispatch"){
                auto evt=archive_event(71); cb(evt);
                auto term=archive_terminal(evt);
                term["attempted"]=0;
                return term;
            }
            throw std::runtime_error("Unexpected publish after incomplete dispatch");
        };
        must_reject([&]{ repair(opts(Action::repair),submit,listing,emit); });
        assert(requested==2 && messages.empty());
    }
    // 403 with a replacement identical to the original must never silently
    // reuse the stale share or perform unlimited provider POST retries.
    {
        messages.clear();
        int plans=0, refreshes=0, archives=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json {
            const auto op=cmd.at("operation").get<std::string>();
            if(op=="archive_download_plan"){++plans;return {{"status","ready"},{"copy",copy()}};}
            if(op=="catalog_offer_batch"){++refreshes;return offer_reply();}
            if(op=="catalog_archive_dispatch"){
                ++archives;auto evt=archive_event(71,false,"HTTP 403");
                cb(evt);return archive_terminal(evt);
            }
            throw std::runtime_error("Unexpected publish after no replacement");
        };
        must_reject([&]{ repair(opts(Action::repair),submit,listing,emit); });
        assert(plans==2 && refreshes==1 && archives==1);
    }
    // A malformed notification must not partially emit playlist reloads.
    {
        messages.clear();
        int calls=0;
        auto submit=[&](Json cmd,const Callback& cb)->Json {
            ++calls;
            const auto op=cmd.at("operation").get<std::string>();
            if(op=="archive_download_plan")return {{"status","ready"},{"copy",copy()}};
            if(op=="catalog_archive_dispatch"){
                auto evt=archive_event(71);cb(evt);return archive_terminal(evt);
            }
            auto published=publication();
            published["notifications"][1][1]="evil\nname.djt";
            return published;
        };
        must_reject([&]{ repair(opts(Action::repair),submit,listing,emit); });
        assert(calls==3);
        assert(std::none_of(messages.begin(),messages.end(),[](const auto& line){
            return line.starts_with("\x1c");
        }));
    }

}
