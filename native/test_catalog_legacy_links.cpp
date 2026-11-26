#include "catalog_legacy_links.hpp"
#include <cassert>
#include <sqlite3.h>
#include <stdexcept>
#include <string>

int main() {
    using namespace doujin::legacy_links;
    const auto foo = parse_url("https://www.mediafire.com/file/abc123/a.zip/file");
    assert(foo.hostname == "www.mediafire.com");
    assert(foo.path == "/file/abc123/a.zip/file");
    assert(destination("https://www.mediafire.com/file/abc123/a.zip/file", foo) == "mediafire:file:abc123");
    const auto query = parse_url("https://www.mediafire.com/file/abc123/?track=1");
    assert(destination("https://www.mediafire.com/file/abc123/?track=1", query)
           == "url:https://www.mediafire.com/file/abc123/?track=1");
    assert(host_family("www.mega.co.nz") == "mega.nz");
    assert(host_family("drive.google.com") == "google-drive");
    bool failed = false;
    try { (void)parse_url("https://user:password@mediafire.com/file/abc"); }
    catch (const std::runtime_error&) { failed = true; }
    assert(failed);

    sqlite3* raw = nullptr;
    assert(sqlite3_open(":memory:", &raw) == SQLITE_OK);
    assert(sqlite3_exec(raw, "CREATE TABLE releases (id INTEGER,share_url TEXT, offer TEXT, host TEXT,"
        " offer_checked INTEGER, seen INTEGER)", nullptr, nullptr, nullptr) == SQLITE_OK);
    assert(sqlite3_exec(raw, "INSERT INTO releases VALUES "
        "(12,'https://www.mediafire.com/file/abc123/x.zip/file',NULL,'www.mediafire.com',1,1)",
        nullptr, nullptr, nullptr) == SQLITE_OK);
    const auto links = snapshot(raw);
    assert(links.is_array() && links.size() == 1);
    assert(links[0]["id"] == 12);
    assert(links[0]["destination"] == "mediafire:file:abc123");
    assert(links[0]["old_host"] == "www.mediafire.com");
    assert(links[0]["offer"]["host"] == "www.mediafire.com");
    assert(sqlite3_close(raw) == SQLITE_OK);
}
