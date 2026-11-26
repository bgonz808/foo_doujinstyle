#include "mediafire_download_policy.hpp"
#include "mediafire_http.hpp"
#include <cassert>
#include <cstdio>
#include <stdexcept>
#include <string>

int main() {
    using namespace doujin::mediafire;
    PrefixValidator zip;
    zip.append("P"); zip.append("K\x03"); zip.append("\x04rest");
assert(zip.finish() == ".zip");
    PrefixValidator seven;
    seven.append("7z\xbc\xaf"); seven.append("\x27\x1c" "last");
assert(seven.finish() == ".7z");
    PrefixValidator bad;
    try { bad.append("<html>bad archive</html>"); assert(false); } catch (const std::runtime_error&) { }
    try { (void)bad.finish(); assert(false); } catch (const std::runtime_error&) { }
    LengthValidator fixed("8");
    fixed.append(1); fixed.append(7);
    assert(fixed.finish() == 8);
    LengthValidator unknown(""); unknown.append(7); assert(unknown.finish() == 7);
    LengthValidator truncated("8"); truncated.append(7);
    try { (void)truncated.finish(); assert(false); } catch (const std::runtime_error&) { }
    LengthValidator excess("8");
    try { excess.append(9); assert(false); } catch (const std::runtime_error&) { }
    for (auto bad_length: {"0", "-1", "8x", "18446744073709551616", "17179869185", " 8", "+8"}) {
        try { (void)LengthValidator(bad_length); assert(false); } catch (const std::runtime_error&) { }
    }
    assert(doujin::live_smoke_host("doujinstyle.com"));
    assert(doujin::live_smoke_host("download123.mediafire.com"));
    assert(doujin::live_smoke_host("mediafire.com"));
    for (auto host : {"mediafire.com.evil.example","evilmediafire.com","a..mediafire.com",
                      "-a.mediafire.com","mediafire.com.","127.0.0.1","mega.nz","foo.doujinstyle.com"})
        assert(!doujin::live_smoke_host(host));
    assert(doujin::mediafire_index_url("https://www.mediafire.com/file/abc/x", true));
    assert(doujin::mediafire_index_url("https://download123.mediafire.com/a", false));
    assert(!doujin::mediafire_index_url("https://mediafire.com.evil.example/a", false));
    assert(!doujin::mediafire_index_url("http://download123.mediafire.com/a", false));
}
