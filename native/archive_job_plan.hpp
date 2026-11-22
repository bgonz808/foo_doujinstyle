#pragma once
#include "catalog_sqlite.hpp"
#include "descriptor.hpp"
#include "mediafire_http.hpp"
#include "third_party/json.hpp"
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::archive_job {
using Json = nlohmann::json;

// Read-only job planning. This is shared by Download and Repair; no network
// request or SQLite transaction is held while a worker downloads an archive.
inline Json select(sqlite3* db, std::int64_t release_id, std::string_view excluded = {}) {
    if (release_id <= 0) throw std::runtime_error("Invalid archive job release ID");
    const auto present = doujin::prepare(db, "SELECT 1 FROM releases WHERE id=?1");
    doujin::check(db, sqlite3_bind_int64(present.get(), 1, release_id));
    if (!doujin::next(present.get())) return {{"status", "missing_release"}, {"copy", nullptr}};
    const auto candidates = doujin::prepare(db, R"SQL(
        SELECT c.id, c.share_url, c.host, c.offer FROM copies c
        WHERE EXISTS (SELECT 1 FROM release_copies r
                      WHERE r.copy_id=c.id AND r.release_id=?1 AND r.current=1)
        ORDER BY CASE c.host WHEN 'mediafire.com' THEN 0 WHEN 'mega.nz' THEN 1 ELSE 2 END, c.id
    )SQL");
    doujin::check(db, sqlite3_bind_int64(candidates.get(), 1, release_id));
    while (doujin::next(candidates.get())) {
        const auto id = sqlite3_column_int64(candidates.get(), 0);
        const auto url = doujin::string_column(candidates.get(), 1);
        const auto host = doujin::string_column(candidates.get(), 2);
        if (url == excluded || url.size() > 8192) continue;
        bool supported = false;
        if (host == "mediafire.com") {
            try { supported = doujin::mediafire_index_url(url, true) || doujin::mediafire_index_url(url, false); }
            catch (const std::exception&) { supported = false; }
        } else if (host == "mega.nz") {
            try { (void)doujin::parse_mega_link(url); supported = true; }
            catch (const std::exception&) { supported = false; }
        }
        if (!supported) continue;
        const auto text = doujin::string_column(candidates.get(), 3);
        if (text.size() > 65536) continue;
        const auto offer = Json::parse(text, nullptr, false);
        if (!offer.is_object()) continue;
        std::string filename;
        if (const auto it = offer.find("filename"); it != offer.end() && it->is_string())
            filename = it->get<std::string>();
        if (filename.size() > 4096) filename.clear();
        return {{"status", "ready"}, {"copy", {{"id", id}, {"url", url},
                {"host", host}, {"filename", filename}}}};
    }
    return {{"status", "no_offer"}, {"copy", nullptr}};
}
} // namespace doujin::archive_job
