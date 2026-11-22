#include "mega_public.hpp"
#include "mega_transfer.hpp"
#include "mega_filename.hpp"
#include <openssl/evp.h>
#include <cassert>
#include <array>
#include <string>
#include <string_view>

int main() {
    doujin::MegaLink link{};
    for (unsigned i=0;i<32;++i) link.key[i] = static_cast<unsigned char>(i);
    constexpr std::array<unsigned char,8> mac{0xac,0x58,0x6f,0x25,0x8b,0xd5,0xf2,0x1a};
    for (unsigned i=0;i<8;++i) {
        link.key[8+i] ^= static_cast<unsigned char>(link.key[24+i] ^ mac[i]);
        link.key[24+i] = mac[i];
    }
    std::array<unsigned char,16> key{};
    for (unsigned i=0;i<16;++i) key[i] = link.key[i] ^ link.key[i+16];
    auto crypt = [&](std::string& blocks, const EVP_CIPHER* mode, const unsigned char* iv) {
        EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new(); assert(ctx);
        assert(EVP_EncryptInit_ex(ctx, mode, nullptr, key.data(), iv));
        assert(EVP_CIPHER_CTX_set_padding(ctx, 0));
        std::string out(blocks.size()+16,'\0'); int count=0,extra=0;
        assert(EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char*>(out.data()), &count,
            reinterpret_cast<const unsigned char*>(blocks.data()), static_cast<int>(blocks.size())));
        assert(EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(out.data())+count, &extra));
        assert(static_cast<std::size_t>(count+extra)==blocks.size());
        blocks.resize(static_cast<std::size_t>(count+extra));
        blocks.assign(out.data(), blocks.size());
        EVP_CIPHER_CTX_free(ctx);
    };
    const auto ecb = [&](std::string& blocks) { crypt(blocks, EVP_aes_128_ecb(), nullptr); };
    const auto cbc = [&](std::string& blocks, std::array<unsigned char,16> iv) {
        crypt(blocks, EVP_aes_128_cbc(), iv.data());
    };
    auto plain = std::string(2500000, '\0');
    for (std::size_t i=0;i<plain.size();++i) plain[i] = static_cast<char>((i*173+i/7)&255);
    auto ciphertext = plain;
    for (std::size_t at = 0; at < ciphertext.size(); at += 1024*1024) {
        auto chunk = ciphertext.substr(at, 1024*1024);
        doujin::mega::decrypt_range(link, at, chunk, ecb);
        ciphertext.replace(at, chunk.size(), chunk);
    }
    auto replay = [&](const std::string& encrypted, bool corrupt, bool truncate, bool reorder) {
        doujin::mega::VerifiedPayload verified(link, plain.size());
        std::string staged;
        std::size_t position = 0;
        while (position < plain.size()) {
            const auto count = verified.next_size();
            std::string chunk = encrypted.substr(position, count);
            if (corrupt && position == 0) chunk[100] ^= 1;
            if (truncate && position > 0) break;
            verified.append(position + (reorder ? 1 : 0), chunk,
                [&](auto first, std::string& body) { doujin::mega::decrypt_range(link, first, body, ecb); },
                cbc, ecb, [&](std::string_view body) { staged.append(body); });
            position += count;
        }
        verified.finish();
        assert(staged == plain);
    };
    replay(ciphertext, false, false, false);
    for (const auto problem : {0, 1, 2}) {
        bool rejected = false;
        try { replay(ciphertext, problem==0, problem==1, problem==2); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
}
