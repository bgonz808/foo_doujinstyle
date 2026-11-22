#include "mega_public.hpp"
#include "catalog_zip_range.hpp"
#include <openssl/evp.h>
#include <cassert>
#include <cstring>
#include <string>
#include <stdexcept>

int main() {
    const auto link = doujin::parse_mega_link("https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8");
    auto crypt = [&](std::string& blocks) {
        unsigned char key[16]{};
        for (int i = 0; i < 16; ++i) key[i] = link.key[i] ^ link.key[i + 16];
        auto* context = EVP_CIPHER_CTX_new(); assert(context);
        assert(EVP_EncryptInit_ex(context, EVP_aes_128_ecb(), nullptr, key, nullptr));
        assert(EVP_CIPHER_CTX_set_padding(context, 0));
        std::string output(blocks.size() + 16, '\0');
        int count = 0, tail = 0;
        assert(EVP_EncryptUpdate(context, reinterpret_cast<unsigned char*>(output.data()), &count,
                                 reinterpret_cast<const unsigned char*>(blocks.data()), static_cast<int>(blocks.size())));
        assert(EVP_EncryptFinal_ex(context, reinterpret_cast<unsigned char*>(output.data()) + count, &tail));
        assert(static_cast<std::size_t>(count + tail) == blocks.size());
        EVP_CIPHER_CTX_free(context);
        blocks.assign(output.data(), blocks.size());
    };
    std::string clear(250 * 1024, '\0');
    for (std::size_t i=0;i<clear.size();++i) clear[i] = static_cast<char>((i*59 + i/7) & 255);
    auto* src = reinterpret_cast<unsigned char*>(clear.data());
    z_stream encoder{};
    assert(deflateInit2(&encoder, Z_BEST_COMPRESSION, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY) == Z_OK);
    std::string packed(deflateBound(&encoder, static_cast<uLong>(clear.size())), '\0');
    encoder.next_in = src;
    encoder.avail_in = static_cast<uInt>(clear.size());
    encoder.next_out = reinterpret_cast<Bytef*>(packed.data());
    encoder.avail_out = static_cast<uInt>(packed.size());
    assert(deflate(&encoder, Z_FINISH) == Z_STREAM_END);
    packed.resize(encoder.total_out);
    assert(deflateEnd(&encoder) == Z_OK);
    const auto crc = static_cast<std::uint32_t>(crc32(0, src, static_cast<uInt>(clear.size())));
    const doujin::ZipMember entry{0, static_cast<std::uint32_t>(packed.size()),
                                  static_cast<std::uint32_t>(clear.size()), crc, 8};
    const auto wrapper = doujin::make_single_member_zip(entry, "01-Album.flac");
    const std::string zip = wrapper.header + packed + wrapper.trailer;
    std::string ciphertext = zip;
    doujin::mega::decrypt_range(link, 0, ciphertext, crypt);
    const auto file = doujin::mega::parse_public_file(
        "{\"g\":\"https://a.userstorage.mega.nz/abc\",\"s\":" +
        std::to_string(zip.size()) + ",\"at\":\"aGVsbG8\"}");
    const doujin::DeflateApi api{inflateInit2_, inflate, inflateEnd, crc32};
    unsigned requests = 0;
    const auto fetch = [&](std::optional<std::uint64_t> offset, std::size_t length, const std::string& validator) {
        assert(validator.empty());
        const auto first = offset.value_or(file.size - std::min<std::uint64_t>(file.size, length));
        const auto amount = std::min<std::size_t>(length, file.size - first);
        assert(doujin::mega::range_url(file, first, amount).ends_with(
            std::to_string(first) + "-" + std::to_string(first + amount - 1)));
        auto raw = ciphertext.substr(first, amount);
        doujin::mega::decrypt_range(link, first, raw, crypt);
        ++requests;
        return doujin::CatalogRangeReply{{first, first+amount-1, file.size}, std::move(raw), "", "", ""};
    };
    doujin::CatalogZipRange reader(fetch, api, 2 * 1024 * 1024, 24);
    assert(reader.open(8192));
    assert(reader.rows.size() == 1 && reader.rows[0].name == "01-Album.flac");
    assert(requests <= 4);
    reader.begin_probe(512 * 1024, 24);
    assert(reader.member(0, 4096) == clear.substr(0, 4096));
    reader.begin_probe(512 * 1024, 24);
    assert(reader.member(0, clear.size()) == clear);
    assert(requests <= 70);

    // A single corrupted ciphertext byte must not be accepted as a valid complete member.
    ciphertext[wrapper.header.size() + 28] ^= 1;
    doujin::CatalogZipRange corrupt(fetch, api, 2 * 1024 * 1024, 24);
    assert(corrupt.open(8192));
    corrupt.begin_probe(512 * 1024, 100);
    bool rejected = false;
    try { (void)corrupt.member(0, clear.size()); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
}
