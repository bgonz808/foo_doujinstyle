#include "catalog_browse_step.hpp"
#include <cassert>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <map>
#include <stop_token>
#include <string>

using doujin::Json;
namespace fs = std::filesystem;

static std::string page(int id, int linked) {
    return "<mainbar>Total Results Found: 2</mainbar>"
           "<div class='gridBox'><div class='gridDetails'><a href='/?p=page&amp;type=1&amp;id=" +
           std::to_string(id) + "'>Release " + std::to_string(id) + "</a></div></div>"
           "<div class='pageScroll'><a href='/?p=home&amp;page=" +
           std::to_string(linked) + "'>Next</a></div>";
}
int main() {
    const auto filename = fs::temp_directory_path() / ("dj-browse-test-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".db");
    try {
        auto db = doujin::connect(filename.string(), SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE);
        doujin::execute(db.get(), catalog_schema);
        const auto head = std::string(doujin::browse_step::home);
        const auto older = std::string("https://doujinstyle.com/?p=home&page=1");
        const auto fetch = [&](const std::string& url, const std::string&, const std::string&) {
            if (url == head) return std::pair{page(2,1), std::string{}};
            if (url == older) return std::pair{page(1,0), std::string{}};
            throw std::runtime_error("Unexpected browse fixture URL");
        };
        const auto initial = doujin::browse_step::run(db.get(), 100, Json(), fetch);
        assert(initial.at("state").at("target") == older);
        auto not_yet = doujin::query(db.get(), "SELECT COUNT(*) FROM pages");
        assert(doujin::next(not_yet.get()) && sqlite3_column_int(not_yet.get(),0) == 0);
        const auto saved = doujin::browse_step::run(db.get(), 101, initial.at("state"), fetch);
        assert(saved.at("saved") == 1 && !saved.at("done"));
        auto checked = doujin::query(db.get(), "SELECT listing FROM releases WHERE id=1");
        assert(doujin::next(checked.get()) && doujin::string_column(checked.get(),0).find("Release 1") != std::string::npos);
        std::stop_source stop;
        stop.request_stop();
        bool cancelled = false;
        try { (void)doujin::browse_step::run(db.get(), 102, saved.at("state"), fetch, stop.get_token()); }
        catch (const std::runtime_error&) { cancelled = true; }
        assert(cancelled);
        auto before_final = doujin::query(db.get(), "SELECT COUNT(*) FROM pages");
        assert(doujin::next(before_final.get()) && sqlite3_column_int(before_final.get(),0) == 1);
        const auto done = doujin::browse_step::run(db.get(), 103, saved.at("state"), fetch);
        assert(done.at("done") && done.at("saved") == 0);
        assert(doujin::browse_step::run(db.get(), 104, Json(), fetch).at("done"));
        std::puts("native browse checkpoint state machine: passed");
    } catch (...) {
        fs::remove(filename);
        throw;
    }
    fs::remove(filename);
}
