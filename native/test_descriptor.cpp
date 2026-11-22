#include "descriptor.hpp"
#include "trace_time.hpp"
#include "zip_catalog.hpp"
#include "mediafire_http.hpp"
#include <cassert>
#include <cstring>
#include <fstream>
#include <initializer_list>
#include <stdexcept>

template<class Fn>
void expect_error(Fn&& fn) {
    try { fn(); }
    catch (const std::runtime_error&) { return; }
    throw std::runtime_error("expected rejection");
}

std::string zip_with_artwork(std::initializer_list<std::string_view> names) {
    std::string files, directory;
    const auto set = [](std::string& target, std::size_t at, std::uint32_t value, unsigned bytes) {
        for (unsigned i = 0; i < bytes; ++i) target[at+i] = static_cast<char>(value >> (8*i));
    };
    for (auto name : names) {
        const auto entry = doujin::make_single_member_zip({0, 3, 3, 0x8c736521, 0}, name);
        const auto at = directory.size();
        directory += entry.trailer.substr(0, entry.trailer.size() - 22);
        set(directory, at + 42, static_cast<std::uint32_t>(files.size()), 4);
        files += entry.header + "foo";
    }
    auto tail = doujin::make_single_member_zip({0, 0, 0, 0, 0}, "x").trailer;
    tail.erase(0, tail.size() - 22);
    set(tail, 8, static_cast<std::uint32_t>(names.size()), 2);
    set(tail, 10, static_cast<std::uint32_t>(names.size()), 2);
    set(tail, 12, static_cast<std::uint32_t>(directory.size()), 4);
    set(tail, 16, static_cast<std::uint32_t>(files.size()), 4);
    return files + directory + tail;
}

void verify_published_reference(const char* reference, const char* archive) {
    const auto read_file = [](const char* path) {
        std::ifstream stream(path, std::ios::binary);
        if (!stream) throw std::runtime_error("Could not open a published reference fixture");
        return std::string(std::istreambuf_iterator<char>(stream), {});
    };
    const auto track = doujin::parse_descriptor(read_file(reference));
    const auto bytes = read_file(archive);
    const auto read = [&](std::uint64_t offset, std::span<unsigned char> buffer) {
        if (offset > bytes.size() || buffer.size() > bytes.size() - offset)
            throw std::runtime_error("Fixture attempted to read past the archive");
        std::memcpy(buffer.data(), bytes.data() + offset, buffer.size());
    };
    const auto member = track.zip_locator ? doujin::find_zip_member(bytes.size(), *track.zip_locator, read)
                                        : doujin::find_zip_member(bytes.size(), track.member, read);
    if (!member || member->size != track.technical.at("SOURCE_BYTES") ||
        member->packed_size != track.technical.at("ZIP_MEMBER_COMPRESSED_BYTES"))
        throw std::runtime_error("The published reference does not resolve to its original ZIP member");
}

int main(int argc, char** argv) {
    if (argc == 3) {
        verify_published_reference(argv[1], argv[2]);
        return 0;
    }
    const auto link = doujin::extract_mediafire_download(
        R"(<!-- <a id="downloadButton" href="ignored"> --><base href="//download.mediafire.com/archives/">)"
        R"(<script><a id="downloadButton" href="ignored"></script>)"
        R"(<a rel="noreferrer" id="downloadButton" href="archive.zip?x=%2B&amp;y=%E3%81%82">)",
        "unsafe-url");
    assert(link.href == "archive.zip?x=%2B&y=%E3%81%82");
    assert(link.base == "//download.mediafire.com/archives/");
    assert(link.policy == "no-referrer");
    std::string malformed(512 * 1024, '<');
    malformed += R"(<a id="downloadButton" href="/file.zip">)";
    assert(doujin::extract_mediafire_download(malformed, "").href == "/file.zip");
    assert(doujin::mediafire_index_url("https://WWW.MediaFire.com:443/file/abc", true));
    assert(!doujin::mediafire_index_url("https://download.mediafire.com/abc", true));
    assert(doujin::mediafire_index_url("https://download123.mediafire.com/file.zip", false));
    assert(!doujin::mediafire_index_url("https://www.mediafire.com.example.com/file/abc", true));
    assert(!doujin::mediafire_index_url("https://www.mediafire.com:8443/file/abc", true));
    expect_error([] { doujin::mediafire_index_url("https://mediafire.com@evil.test/file/abc", true); });
    assert(doujin::check_mediafire_range("bytes 0-9/10", "", "10", 0, 8192, true).size == 10);
    assert(doujin::check_mediafire_range("bytes 95-99/100", "IDENTITY", "", 95, 20, false).first == 95);
    expect_error([] { doujin::check_mediafire_range("bytes 91-99/100", "", "9", 0, 8, true); });
    expect_error([] { doujin::check_mediafire_range("bytes 90-98/100", "", "9", 90, 10, false); });
    expect_error([] { doujin::check_mediafire_range("bytes 90-99/100", "", "9", 90, 10, false); });
    expect_error([] { doujin::check_mediafire_range("bytes 90-99/100", "gzip", "10", 90, 10, false); });
    const auto d = doujin::parse_descriptor(
        "DOUJIN-TRACK 1\r\nURL=https://host.example/file.zip?a=b\r\n"
        "MEMBER=disc/夜桜.flac\r\nRELEASE_ID=19746\r\nREFERRER=https://doujinstyle.com/?p=page&id=19746\r\n"
        "TITLE=夜桜\r\nDURATION_MS=12001\r\n"
        "GENRE=Electronic\r\nGENRE=Breakcore\r\nRELEASE GENRE=Breakcore\r\nRELEASE EVENT=M3-54\r\n"
        "CODEC=FLAC\r\nENCODING=lossless\r\nSOURCE_BYTES=4294967296\r\n"
        "CATALOG SOURCES=1\r\nCATALOG SOURCE RELEASE=AB-123\r\n");
    assert(d.filename == "夜桜.flac");
    assert(d.metadata.at("TITLE") == "夜桜");
    assert((d.multi_metadata.at("GENRE") == std::vector<std::string>{"Electronic", "Breakcore"}));
    assert(d.multi_metadata.at("RELEASE EVENT").front() == "M3-54");
    const auto mega = doujin::parse_mega_link("https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8");
    assert(mega.handle == "PublicID");
    for (unsigned i = 0; i < mega.key.size(); ++i) assert(mega.key[i] == i);
    assert(doujin::parse_mega_link("https://mega.nz/#!PublicID!AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8").key == mega.key);
    const auto tail = doujin::mega_range("bytes=-8192", 10000);
    assert(tail.first == 1808 && tail.last == 9999 && tail.size == 10000);
    const auto subrange = doujin::mega_range("bytes=33-108", 10000);
    assert(subrange.first == 33 && subrange.last == 108);
    expect_error([] { doujin::validate_archive_url("https://example.invalid/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"); });
    assert(doujin::format_release_date("2024/2/29") == "2024-02-29");
    assert(doujin::format_release_date("2023-02-29").empty());
    assert(doujin::format_release_date("2024").empty());
    assert(doujin::release_event_from_tag("m3–44") == "M3-44");
    assert(doujin::release_event_from_tag("C-108") == "C108");
    assert(doujin::release_event_from_tag("DCKSC-0004").empty());
    assert(d.has_catalog_sources && d.catalog_sources[1] == "AB-123");
    assert((doujin::catalog_source_order("lookup, track") == std::vector<std::size_t>{2, 0}));
    assert((doujin::catalog_source_order("release, bogus") == std::vector<std::size_t>{0, 1, 2, 3}));
    assert(doujin::catalog_source_order("none").empty());
    assert(doujin::catalog_number_from_tag(" nbcd‑025\t") == "NBCD-025");
    assert(doujin::catalog_number_from_tag("AB-ABCDE1FGHIJ") == "AB-ABCDE1FGHIJ");
    assert(doujin::catalog_number_from_tag("AB-1ABCDEFGHIJ").empty());
    assert(doujin::catalog_number_from_tag("10989").empty());
    assert(doujin::catalog_number_from_tag("C90").empty());
    assert(doujin::catalog_number_from_tag("M3-54").empty());
    assert(doujin::trace_duration(0) == "0 ms");
    assert(doujin::trace_duration(0.0000428) == "42.8 µs");
    assert(doujin::trace_duration(0.0092349) == "9.23 ms");
    assert(doujin::trace_duration(0.1467596) == "147 ms");
    assert(doujin::trace_duration(0.9999) == "1.00 s");
    assert(doujin::trace_duration(1.135) == "1.14 s");
    assert(doujin::trace_duration(174.121) == "174 s");
    assert(d.technical.at("DURATION_MS") == 12001);
    assert(d.technical.at("SOURCE_BYTES") == 4294967296ULL);
    assert(d.technical_text.at("CODEC") == "FLAC");
    assert(d.release_id == "19746");
    assert(d.referrer == "https://doujinstyle.com/?p=page&id=19746");
    assert(doujin::release_id_from_filename("file://C:/Music/19746-805d66664fe992f16ee485a3.djt") == "19746");
    assert(doujin::release_id_from_filename("file://C:/Music/renamed.djt").empty());
    assert(doujin::release_id_from_filename("19746-805d66664fe992f16ee485a3.djt.bak").empty());
    using doujin::release_id_from_reference_path;
    assert(release_id_from_reference_path("2019/10/19746-805d66664fe992f16ee485a3.djt") == "19746");
    assert(release_id_from_reference_path("undated/19746-805d66664fe992f16ee485a3.djt") == "19746");
    assert(release_id_from_reference_path("2019/../19746-805d66664fe992f16ee485a3.djt").empty());
    assert(release_id_from_reference_path("2019/13/19746-805d66664fe992f16ee485a3.djt").empty());
    assert(release_id_from_reference_path("/19746-805d66664fe992f16ee485a3.djt").empty());
    using doujin::navigation_referrer;
    assert(navigation_referrer("https://DoujinStyle.com:443/?id=19746", "https://doujinstyle.com/search") ==
           "https://doujinstyle.com/?id=19746");
    assert(navigation_referrer("https://doujinstyle.com/?id=19746", "https://www.mediafire.com/file/key") ==
           "https://doujinstyle.com/");
    assert(navigation_referrer("https://www.mediafire.com/file/key", "https://download.mediafire.com/music.zip") ==
           "https://www.mediafire.com/");
    assert(navigation_referrer("https://example.com/p", "https://example.com:8443/q") == "https://example.com/");
    assert(navigation_referrer("http://[::1]:80?query", "http://[::1]/a") == "http://[::1]/?query");
    assert(navigation_referrer("https://example.com/p", "http://example.com/a").empty());
    assert(navigation_referrer("", "https://example.com/a").empty());
    assert(doujin::referrer_policy("origin, strict-origin, unknown") == "strict-origin");
    assert(navigation_referrer("https://example.com/p", "https://example.com/a", "no-referrer").empty());
    assert(navigation_referrer("https://example.com/p", "https://example.com/a", "origin") == "https://example.com/");
    assert(navigation_referrer("https://example.com/p", "https://other.example/a", "unsafe-url") == "https://example.com/p");
    expect_error([] { navigation_referrer("https://example.com/a\r\nInjected: value", "https://example.com/b"); });
    expect_error([] {
        doujin::parse_descriptor("DOUJIN-TRACK 1\nURL=https://host.example/audio.flac\n"
                                 "FILENAME=audio.flac\nRELEASE_ID=../cover");
    });
    const auto span = doujin::parse_content_range("bytes 4294967296-4294967297/4294967300");
    assert(span.first == 4294967296ULL && span.last == 4294967297ULL && span.size == 4294967300ULL);
    assert(doujin::decode_hex(doujin::encode_hex(d.url)) == d.url);
    expect_error([] { doujin::parse_content_range("bytes 4-3/5"); });
    expect_error([] { doujin::parse_content_range("bytes 0-1/*"); });
    expect_error([] { doujin::validate_url("https://account@host.example/file"); });
    expect_error([] { doujin::parse_descriptor("DOUJIN-TRACK 1\nURL=file:///sample.flac\nFILENAME=sample.flac"); });

    constexpr std::string_view member = "CD1/夜桜.flac";
    const auto zip = doujin::make_single_member_zip({0, 5, 3, 0x8c736521, 8}, member);
    const auto bytes = zip.header + std::string("\x4b\xcb\xcf\x07\0", 5) + zip.trailer;
    auto read = [&](std::uint64_t offset, std::span<unsigned char> result) {
        assert(offset + result.size() <= bytes.size());
        std::memcpy(result.data(), bytes.data() + offset, result.size());
    };
    const auto entry = doujin::find_zip_member(bytes.size(), member, read);
    assert(entry && entry->method == 8 && entry->offset == zip.header.size() && entry->packed_size == 5 && entry->size == 3);
    const auto layout = doujin::zip_catalog_layout(bytes.size(), bytes);
    assert(layout && layout->entries == 1);
    const auto directory = doujin::zip_catalog_members(
        std::string_view(bytes).substr(layout->start, layout->bytes), layout->start, layout->entries);
    assert(directory && directory->size() == 1 && directory->front().name == member);
    const auto& row = directory->front();
    assert(row.method == 8 && row.header == 0);
    const auto local = std::string_view(bytes).substr(0, 30 + row.name.size());
    const auto payload = doujin::zip_catalog_payload(
        local, row.name, row.header, layout->start, row.packed, row.size, row.crc, row.method, row.flags);
    assert(payload && *payload == zip.header.size());
    const auto locator = doujin::parse_zip_locator(doujin::encode_hex(member) + ":0:2356372769");
    const auto located = doujin::find_zip_member(bytes.size(), locator, read);
    assert(located && located->offset == zip.header.size());
    assert(doujin::format_zip_locator(locator) == doujin::encode_hex(member) + ":0:2356372769");
    assert(!doujin::find_zip_member(bytes.size(), doujin::ZipLocator{std::string(member), 0, 0}, read));
    assert(!doujin::find_zip_member(bytes.size(), doujin::ZipLocator{std::string(member), 1, row.crc}, read));
    expect_error([] { doujin::parse_zip_locator("00:0:1"); });
    const auto from_descriptor = doujin::parse_descriptor(
        "DOUJIN-TRACK 1\nURL=https://download.example/a.zip\nMEMBER=Japanese.mp3\n"
        "MEMBER_LOCATOR=955c8e862e6d7033:65:16\n");
    assert(from_descriptor.zip_locator && from_descriptor.zip_locator->raw_name ==
        std::string("\x95\x5c\x8e\x86.mp3", 8));
    assert(doujin::zip_filename_crc("123456789") == 0xcbf43926u);
    auto japanese_zip = doujin::make_single_member_zip({0, 3, 3, 0x8c736521, 0}, "xxxx.mp3");
    const auto& raw = from_descriptor.zip_locator->raw_name;
    japanese_zip.header[7] &= ~8;
    japanese_zip.trailer[9] &= ~8;
    japanese_zip.header.replace(30, raw.size(), raw);
    japanese_zip.trailer.replace(46, raw.size(), raw);
    const auto japanese_bytes = japanese_zip.header + "foo" + japanese_zip.trailer;
    const auto japanese_read = [&](std::uint64_t offset, std::span<unsigned char> out) {
        assert(offset + out.size() <= japanese_bytes.size());
        std::memcpy(out.data(), japanese_bytes.data() + offset, out.size());
    };
    assert(!doujin::find_zip_member(japanese_bytes.size(), "表紙.mp3", japanese_read));
    const auto japanese_member = doujin::find_zip_member(
        japanese_bytes.size(), doujin::ZipLocator{raw, 0, 0x8c736521}, japanese_read);
    assert(japanese_member && japanese_member->offset == japanese_zip.header.size());


    assert(!doujin::zip_catalog_payload(local, row.name, row.header, *payload + row.packed - 1,
        row.packed, row.size, row.crc, row.method, row.flags));
    assert(!doujin::zip_catalog_layout(bytes.size() - 1, bytes));
    for (std::size_t count = 0; count < bytes.size(); ++count)
        static_cast<void>(doujin::zip_catalog_layout(bytes.size(), std::string_view(bytes).substr(bytes.size() - count)));
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        auto mutated = bytes;
        mutated[index] ^= static_cast<char>(0xff);
        if (const auto range = doujin::zip_catalog_layout(mutated.size(), mutated))
            static_cast<void>(doujin::zip_catalog_members(
                std::string_view(mutated).substr(range->start, range->bytes), range->start, range->entries));
    }
    assert(!doujin::find_zip_member(bytes.size(), "CD2/夜桜.flac", read));
    auto bad = bytes;
    bad[26] = 0;
    expect_error([&] {
        doujin::find_zip_member(bad.size(), member, [&](std::uint64_t offset, std::span<unsigned char> result) {
            std::memcpy(result.data(), bad.data() + offset, result.size());
        });
    });
    const auto discography = zip_with_artwork({
        "folder.jpg", "Vol.7/front.jpg", "Vol.8/Folder.JPG", "Vol.8/Disc 1/Evolution Schema.png",
        "Vol.8/Disc 1/cover.webp", "Vol.8/back.png", "__MACOSX/Vol.8/front.jpg",
        "Vol.8/Disc 1/../front.jpg", "Vol.8/Disc 2/Evolution Schema.png",
    });
    const auto art_read = [&](std::uint64_t offset, std::span<unsigned char> result) {
        assert(offset + result.size() <= discography.size());
        std::memcpy(result.data(), discography.data() + offset, result.size());
    };
    using doujin::ArtworkType;
    const auto picture = doujin::find_zip_artwork(discography.size(), "Vol.8/Disc 1/Evolution Schema.flac",
                                                   "FREAKIN SOUNDS Vol.8", ArtworkType::front, 1024, art_read);
    assert((picture == std::vector<std::string>{"Vol.8/Disc 1/cover.webp",
        "Vol.8/Disc 1/Evolution Schema.png", "Vol.8/Folder.JPG", "folder.jpg"}));
    assert((doujin::find_zip_artwork(discography.size(), "Vol.8/Disc 1/Evolution Schema.flac", "",
             ArtworkType::back, 1024, art_read) == std::vector<std::string>{"Vol.8/back.png"}));
    assert(doujin::find_zip_artwork(discography.size(), "Vol.8/Disc 1/Evolution Schema.flac", "",
             ArtworkType::front, 2, art_read).empty());
}
