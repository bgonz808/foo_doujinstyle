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

    // Build a single raw DEFLATE stream with a non-final stored block,
    // followed by a compressed final stream. Test fixed and dynamic Huffman
    // encodings separately, including a checkpoint across the stored boundary.
    const doujin::DeflateApi checkpoint_api{inflateInit2_,inflate,inflateEnd,crc32,
        inflatePrime,inflateSetDictionary,inflateGetDictionary};
    std::vector<unsigned char> prefix(32768);
    for (std::size_t i=0;i<prefix.size();++i) prefix[i]=static_cast<unsigned char>(i*73u);
    std::vector<unsigned char> suffix(256*1024);
    for (std::size_t i=0;i<suffix.size();++i) suffix[i]=static_cast<unsigned char>((i/17)%13);
    auto combined=prefix;
    combined.insert(combined.end(),suffix.begin(),suffix.end());
    for (const int strategy : {Z_FIXED,Z_DEFAULT_STRATEGY}) {
        z_stream enc{};
        assert(deflateInit2(&enc,6,Z_DEFLATED,-MAX_WBITS,8,strategy)==Z_OK);
        std::vector<unsigned char> compressed(deflateBound(&enc,suffix.size()));
        enc.next_in=suffix.data();enc.avail_in=static_cast<uInt>(suffix.size());
        enc.next_out=compressed.data();enc.avail_out=static_cast<uInt>(compressed.size());
        assert(deflate(&enc,Z_FINISH)==Z_STREAM_END);
        compressed.resize(enc.total_out);
        assert(deflateEnd(&enc)==Z_OK);
        // Confirm fixture actually exercises the requested block type.
        assert(((compressed[0]>>1)&3u)==(strategy==Z_FIXED?1u:2u));
        std::vector<unsigned char> mixed{0,0,128,255,127}; // BFINAL=0, LEN=32768, NLEN=32767
        mixed.insert(mixed.end(),prefix.begin(),prefix.end());
        mixed.insert(mixed.end(),compressed.begin(),compressed.end());
        const doujin::ZipMember member{0,static_cast<std::uint32_t>(mixed.size()),
            static_cast<std::uint32_t>(combined.size()),
            static_cast<std::uint32_t>(crc32(0,combined.data(),static_cast<uInt>(combined.size()))),8};
        doujin::DeflateStream initial(checkpoint_api,member);
        std::vector<unsigned char> first_output(prefix.size()+1);
        auto first=initial.step(mixed,first_output,true);
        assert(first.produced==prefix.size());
        assert(std::equal(prefix.begin(),prefix.end(),first_output.begin()));
        auto checkpoint=initial.checkpoint(prefix.size());
        assert(checkpoint && checkpoint->output==prefix.size() && checkpoint->input==first.consumed);
        for (bool restored : {false,true}) {
            doujin::DeflateStream resumed(checkpoint_api,member,restored?&*checkpoint:nullptr);
            std::vector<unsigned char> output(restored?suffix.size()+1:combined.size()+1);
            auto chunk=resumed.step(std::span(mixed).subspan(restored?checkpoint->input:0),output);
            assert(resumed.finished());
            const auto& expected_bytes=restored?suffix:combined;
            assert(chunk.produced==expected_bytes.size());
            assert(std::equal(expected_bytes.begin(),expected_bytes.end(),output.begin()));
        }
        // Concatenating complete raw DEFLATE streams is not one valid ZIP
        // method-8 member. Early end must fail declared length validation.
        auto trailing=mixed;
        trailing.insert(trailing.end(),compressed.begin(),compressed.end());
        auto bad=member;bad.packed_size=static_cast<std::uint32_t>(trailing.size());
        try {
            doujin::DeflateStream decoder(checkpoint_api,bad);
            std::vector<unsigned char> out(combined.size()+1);
            static_cast<void>(decoder.step(trailing,out));
            assert(false && "Unexpected trailing raw DEFLATE stream accepted");
        } catch (const std::runtime_error&) {}
    }

}
