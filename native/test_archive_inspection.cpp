#include "archive_inspection.hpp"
#include <zlib.h>
#include <cassert>
#include <iostream>
#include <fstream>
#include <iterator>

int main(int argc, char** argv) {
    using doujin::archive_inspection::filename_from_disposition;
    using doujin::archive_inspection::catalog_number;
    assert(filename_from_disposition("attachment; filename=\"[NBCD-025] Release.zip\"") == "[NBCD-025] Release.zip");
    assert(filename_from_disposition("attachment; filename*=UTF-8''%5BNBCD-025%5D%20Release.zip") == "[NBCD-025] Release.zip");
    assert(filename_from_disposition("attachment; filename=\"../evil.zip\"").empty());
    assert(filename_from_disposition("attachment; filename*=UTF-8''%ZZ").empty());
    assert(filename_from_disposition("attachment; filename=\"line\rbreak.zip\"").empty());
    assert(catalog_number("［ＮＢＣＤ－０２５］ Album", true) == "NBCD-025");
    assert(catalog_number("[NBCD-025] Album", true) == "NBCD-025");
    assert(catalog_number("X [NBCD-025] Album", true).empty());
    if (argc != 2) return 1;
    std::ifstream stream(argv[1], std::ios::binary);
    const std::string contents((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    const doujin::DeflateApi z{&inflateInit2_, &inflate, &inflateEnd, &crc32};
    auto fetch = [&](std::optional<std::uint64_t> start, std::size_t length, const std::string& validator) {
        assert(validator.empty() || validator == "\"revision1\"");
        const auto at = start.value_or(contents.size() - std::min(contents.size(), length));
        const auto count = std::min(length, contents.size() - static_cast<std::size_t>(at));
        return doujin::CatalogRangeReply{{at, at + count - 1, contents.size()},
            contents.substr(static_cast<std::size_t>(at), count), "\"revision1\"", {}, ""};
    };
    doujin::CatalogZipRange reader(fetch, z, 2 * 1024 * 1024, 64);
    if (!reader.open(65557)) {
        std::cout << nlohmann::json{{"format", doujin::archive_inspection::format(reader.signature())}}.dump() << '\n';
    } else {
        const auto result = doujin::archive_inspection::inspect(reader, {true, true, 12, 96 * 1024});
        std::cout << result.dump() << '\n';
    }
}
