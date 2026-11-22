#include "deflate_stream.hpp"
#include <algorithm>
#include <array>
#include <cassert>
#include <vector>

int main() {
    std::vector<unsigned char> audio(2 * 1024 * 1024);
    unsigned state = 17;
    for (auto& byte : audio) {
        state = state * 1664525u + 1013904223u;
        byte = static_cast<unsigned char>(state >> 24);
    }

    z_stream encoder{};
    assert(deflateInit2(&encoder, Z_DEFAULT_COMPRESSION, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY) == Z_OK);
    std::vector<unsigned char> packed(deflateBound(&encoder, audio.size()));
    encoder.next_in = audio.data();
    encoder.avail_in = static_cast<uInt>(audio.size());
    encoder.next_out = packed.data();
    encoder.avail_out = static_cast<uInt>(packed.size());
    assert(deflate(&encoder, Z_FINISH) == Z_STREAM_END);
    packed.resize(encoder.total_out);
    deflateEnd(&encoder);

    const doujin::DeflateApi api{inflateInit2_, inflate, inflateEnd, crc32};
    const doujin::ZipMember expected{0, static_cast<std::uint32_t>(packed.size()),
                                    static_cast<std::uint32_t>(audio.size()),
                                    static_cast<std::uint32_t>(crc32(0, audio.data(), audio.size())), 8};
    auto run = [&](doujin::ZipMember entry, std::span<const unsigned char> compressed) {
        doujin::DeflateStream decoder(api, entry);
        std::array<unsigned char, 8192> block{};
        std::vector<unsigned char> decoded;
        std::size_t offset = 0;
        while (!decoder.finished()) {
            const auto piece = compressed.subspan(offset, std::min<std::size_t>(16384, compressed.size() - offset));
            const auto chunk = decoder.step(piece, block);
            offset += chunk.consumed;
            if (decoded.empty() && chunk.produced) assert(offset < compressed.size() / 16);
            decoded.insert(decoded.end(), block.begin(), block.begin() + chunk.produced);
        }
        assert(decoded == audio);
    };
    run(expected, packed);

    auto expect_error = [&](doujin::ZipMember entry, std::span<const unsigned char> compressed) {
        try { run(entry, compressed); }
        catch (const std::runtime_error&) { return; }
        throw std::runtime_error("Expected an invalid ZIP member to be rejected");
    };
    expect_error(expected, std::span(packed).first(packed.size() - 1));
    auto wrong_crc = expected;
    wrong_crc.crc ^= 1;
    expect_error(wrong_crc, packed);
    auto wrong_length = expected;
    --wrong_length.size;
    expect_error(wrong_length, packed);
}
