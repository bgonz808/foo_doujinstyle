#include "catalog_zip_range.hpp"
#include <cassert>
#include <string>

int main() {
    std::string audio(512 * 1024, '\0');
    unsigned state = 17;
    for (char& byte : audio) {
        state = state * 1664525u + 1013904223u;
        byte = static_cast<char>(state >> 24);
    }
    auto* bytes = reinterpret_cast<unsigned char*>(audio.data());
    z_stream encoder{};
    assert(deflateInit2(&encoder, Z_DEFAULT_COMPRESSION, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY) == Z_OK);
    std::string packed(deflateBound(&encoder, audio.size()), '\0');
    encoder.next_in = bytes;
    encoder.avail_in = static_cast<uInt>(audio.size());
    encoder.next_out = reinterpret_cast<unsigned char*>(packed.data());
    encoder.avail_out = static_cast<uInt>(packed.size());
    assert(deflate(&encoder, Z_FINISH) == Z_STREAM_END);
    packed.resize(encoder.total_out);
    assert(deflateEnd(&encoder) == Z_OK);

    const doujin::DeflateApi api{inflateInit2_, inflate, inflateEnd, crc32};
    const auto crc = static_cast<std::uint32_t>(crc32(0, bytes, static_cast<uInt>(audio.size())));
    for (const std::uint16_t method : {0, 8}) {
        const auto& input = method == 0 ? audio : packed;
        const doujin::ZipMember entry{0, static_cast<std::uint32_t>(input.size()),
            static_cast<std::uint32_t>(audio.size()), crc, method};
        const auto wrapper = doujin::make_single_member_zip(entry, "Vol.8/01.flac");
        auto file = wrapper.header + input + wrapper.trailer;
        auto etag = "\"edition-a\"";
        bool change = false;
        auto fetch = [&](auto offset, auto limit, const auto& validator) {
            const auto start = offset.value_or(file.size() - std::min(limit, file.size()));
            auto body = file.substr(start, limit);
            assert(validator.empty() || validator == etag);
            return doujin::CatalogRangeReply{{start, start + body.size() - 1, file.size()}, std::move(body),
                                            change ? "\"edition-b\"" : etag, "", ""};
        };
        doujin::CatalogZipRange reader(fetch, api, 2 * 1024 * 1024, 12);
        assert(reader.open(8192));
        assert(reader.rows.size() == 1 && reader.rows[0].header == 0 && reader.rows[0].crc == crc);
        reader.begin_probe(4096 + 128 * 1024, 12);
        assert(reader.member(0, 4096) == audio.substr(0, 4096));
        assert(reader.fetched < file.size() / 8);
        reader.begin_probe(1024 * 1024, 128);
        assert(reader.member(0, audio.size()) == audio);
        change = true;
        reader.begin_probe(4096 + 128 * 1024, 12);
        try {
            static_cast<void>(reader.member(0, 4096));
            assert(false && "Changed remote archive should abort the batch");
        } catch (const doujin::CatalogRangeChanged&) {}
        change = false;
        file[30] = 'X';
        reader.begin_probe(4096 + 128 * 1024, 12);
        try {
            static_cast<void>(reader.member(0, 4096));
            assert(false && "Local header must match the central record");
        } catch (const std::runtime_error& error) {
            assert(std::string_view(error.what()) == "ZIP local header failed native validation");
        }
    }
}
