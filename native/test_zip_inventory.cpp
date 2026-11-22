#include "zip_inventory.hpp"
#include <cassert>
#include <iostream>

int main() {
    using doujin::zip_inventory::Json;
    auto member = [](std::string name, std::uint64_t size, unsigned flags = 0) {
        return Json{{"filename", std::move(name)}, {"compress_size", size / 2},
            {"file_size", size}, {"CRC", 0xdeadbeef}, {"header_offset", 10},
            {"compress_type", 8}, {"flag_bits", flags}};
    };
    auto archive = Json::array({member("Album/", 0), member("Album/01.FLAC", 64000),
        member("Album/cover.JPG", 1024), member("__MACOSX/._01.flac", 5),
        member("Album/README.txt", 52), member("Album/.hidden", 11),
        member("Album/sub/sound.MP3", 1024, 1)});
    const auto report = doujin::zip_inventory::project(archive);
    assert(report["members"] == 6);
    assert(report["audio_members"] == 2);
    assert(report["playable_indices"] == Json::array({1, 6}));
    assert(report["entry_indices"] == Json::array({1, 2, 3, 4, 5, 6}));
    assert(report["sidecar_candidates"] == Json::array({4}));
    assert(report["roots"] == Json::array({"Album", "__MACOSX"}));
    assert(report["member_types"]["archive_metadata"] == 1);
    assert(report["member_types"]["artwork"] == 1);
    assert(report["unknown_suffixes"]["(none)"] == 1);
    assert(report["inventory"][0]["crc32"] == "deadbeef");
    assert(report["inventory"][5]["encrypted"] == true);
    assert(report["artwork"].size() == 1);
    for (const auto& bad : {Json::object(), Json(nullptr)}) {
        try { (void)doujin::zip_inventory::project(bad); assert(false); }
        catch (const std::runtime_error&) { }
    }
    auto invalid = Json::array({member(std::string(5000, 'A'), 1)});
    try { (void)doujin::zip_inventory::project(invalid); assert(false); }
    catch (const std::runtime_error&) { }
    std::cout << "ZIP inventory native fixture: PASS\n";
}
