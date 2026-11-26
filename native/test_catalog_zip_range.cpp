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

    // One real central directory containing independent STORE and DEFLATE
    // audio members, not merely separate homogeneous single-member ZIPs.
    auto put = [](std::string& data, std::size_t at, std::uint64_t value, unsigned width) {
        assert(at + width <= data.size());
        for (unsigned i=0; i<width; ++i) data[at+i]=static_cast<char>(value >> (8*i));
    };
    const doujin::ZipMember stored{0,static_cast<std::uint32_t>(audio.size()),
        static_cast<std::uint32_t>(audio.size()),crc,0};
    const doujin::ZipMember deflated{0,static_cast<std::uint32_t>(packed.size()),
        static_cast<std::uint32_t>(audio.size()),crc,8};
    auto left=doujin::make_single_member_zip(stored,"disc/01.wav");
    auto right=doujin::make_single_member_zip(deflated,"disc/02.wav");
    const std::string first=left.header+audio;
    const std::string second=right.header+packed;
    assert(left.trailer.size()>22 && right.trailer.size()>22);
    auto dir1=left.trailer.substr(0,left.trailer.size()-22);
    auto dir2=right.trailer.substr(0,right.trailer.size()-22);
    put(dir2,42,first.size(),4);
    auto end=right.trailer.substr(right.trailer.size()-22);
    put(end,8,2,2);put(end,10,2,2);
    put(end,12,dir1.size()+dir2.size(),4);
    put(end,16,first.size()+second.size(),4);
    const auto mixed=first+second+dir1+dir2+end;
    auto check_archive=[&](const std::string& file,bool supported) {
        auto fetch=[&](auto offset,auto limit,const auto&) {
            const auto start=offset.value_or(file.size()-std::min(limit,file.size()));
            auto body=file.substr(start,limit);
            return doujin::CatalogRangeReply{{start,start+body.size()-1,file.size()},
                std::move(body),"\"mixed-v1\"","",""};
        };
        doujin::CatalogZipRange reader(fetch,api,2*1024*1024,32);
        assert(reader.open(8192)==supported);
        if (!supported) return;
        assert(reader.rows.size()==2);
        assert(reader.rows[0].method==0 && reader.rows[1].method==8);
        assert(reader.rows[0].header==0 && reader.rows[1].header==first.size());
        for (auto header : {std::uint64_t(0),std::uint64_t(first.size()),std::uint64_t(0)}) {
            reader.begin_probe(2*1024*1024,256);
            assert(reader.member(header,audio.size())==audio);
        }
    };
    check_archive(mixed,true);
    // Independently valid ZIPs glued together are not automatically one
    // merged directory. Current sparse parser deliberately rejects rebasing.
    check_archive(mixed+mixed,false);
    check_archive(std::string(4096,'X')+mixed,false);
    // Split archive disk-number marker: reject rather than reinterpret offsets.
    auto split=mixed;
    put(split,split.size()-22+4,1,2);
    check_archive(split,false);

}
