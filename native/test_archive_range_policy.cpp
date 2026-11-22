#include "archive_range_policy.hpp"
#include "zip_member_cache.hpp"
#include <cassert>

int main() {
    using namespace doujin;
    constexpr auto kib = 1024;
    assert(deflate_input_window(1, false, false) == 64 * kib);
    assert(deflate_input_window(1, false, true) == 64 * kib);
    assert(deflate_input_window(1, true, false) == 64 * kib);
    assert(deflate_input_window(1, true, true) == 256 * kib);
    assert(deflate_input_window(3 * archive_range_window, false, false) == archive_range_window);
    assert(deflate_input_window(3 * archive_range_window, true, false) == coalesced_range_window);
    assert(deflate_input_window(96 * kib, true, false) == 96 * kib);

    ZipMemberCache cache(2);
    unsigned calls = 0;
    const auto resolve = [&] {
        ++calls;
        return std::optional(ZipMember{42, 10, 20, 1, 8});
    };
    const auto missing = [&]() -> std::optional<ZipMember> { ++calls; return {}; };
    const ZipLocator locator{"track.flac", 12, 1};
    const ZipLocator changed{"track.flac", 12, 2};
    assert(cache.locate("track.flac", locator, resolve)->offset == 42);
    assert(cache.locate("track.flac", locator, missing)->offset == 42);
    assert(calls == 1);
    assert(!cache.locate("track.flac", changed, missing));
    assert(!cache.locate("track.flac", changed, missing));
    assert(calls == 3);
    assert(cache.locate("cover.jpg", {}, resolve));
    assert(cache.locate("track.flac", locator, missing));
    assert(cache.locate("other.flac", {}, resolve));
    assert(cache.locate("track.flac", locator, missing));
    assert(!cache.locate("cover.jpg", {}, missing));
    assert(calls == 6);
    ZipMemberCache new_archive;
    assert(!new_archive.locate("track.flac", locator, missing));
    assert(calls == 7);
    const std::string long_name(4097, 'a');
    assert(new_archive.locate(long_name, {}, resolve));
    assert(!new_archive.locate(long_name, {}, missing));
    assert(calls == 9);
}
