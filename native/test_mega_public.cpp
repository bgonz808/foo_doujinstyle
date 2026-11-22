#include "mega_public.hpp"
#include <openssl/evp.h>
#include <cassert>
#include <functional>
#include <string>
#include <stdexcept>

namespace {
template <class Fn> void bad(Fn fn) {
    try { fn(); } catch (const std::runtime_error&) { return; }
    throw std::runtime_error("Expected invalid MEGA input rejection");
}
}
int main() {
    auto file = doujin::mega::parse_public_file(R"([{"g":"https:\/\/a.userstorage.mega.nz\/download\/abc","s":196617,"at":"dGVzdA"}])");
    assert(file.size == 196617);
    assert(file.endpoint == "https://a.userstorage.mega.nz/download/abc");
    assert(doujin::mega::range_url(file, 17, 20) == "https://a.userstorage.mega.nz/download/abc/17-36");
    assert(doujin::mega::range_url(file, file.size - 9, 9) == "https://a.userstorage.mega.nz/download/abc/196608-196616");
    bad([] { doujin::mega::parse_public_file(R"({"g":"https://evil.com/a","s":100,"at":"abc"})"); });
    bad([] { doujin::mega::parse_public_file(R"([{"g":"http://a.userstorage.mega.nz/a","s":100,"at":"abc"}])"); });
    bad([] { doujin::mega::parse_public_file(R"({"g":"https://a.userstorage.mega.nz@evil.org/a","s":100,"at":"abc"})"); });
    bad([] { doujin::mega::parse_public_file(R"({"g":"https://a.userstorage.mega.nz/a","s":-1,"at":"abc"})"); });
    bad([] { doujin::mega::parse_public_file(R"({"g":"https://a.userstorage.mega.nz/a","s":100,"at":null})"); });
    bad([] { doujin::mega::parse_public_file(R"([1,2])"); });
    bad([] { doujin::mega::parse_public_file("-17"); });
    bad([] { doujin::mega::parse_public_file(std::string("{}\0", 3)); });
    bad([] { doujin::mega::parse_public_file(std::string(100, '[') +
             std::string(100, ']')); });
    bad([] { doujin::mega::parse_public_file(R"({"g":"https://a.userstorage.mega.nz/a","s":1,"at":"abc","nested":[[[[[[})"); });
    bad([&] { doujin::mega::range_url(file, file.size, 1); });
    bad([&] { doujin::mega::range_url(file, 0, 3 * 1024 * 1024); });

    // Produce independent AES-ECB keystream using OpenSSL to check unaligned offsets.
    auto link = doujin::parse_mega_link("https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8");
    const auto crypt = [&](std::string& blocks) {
        std::array<unsigned char, 16> key{};
        for (int i=0;i<16;++i) key[i] = link.key[i] ^ link.key[i+16];
        EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
        assert(ctx);
        assert(EVP_EncryptInit_ex(ctx, EVP_aes_128_ecb(), nullptr, key.data(), nullptr));
        assert(EVP_CIPHER_CTX_set_padding(ctx, 0));
        int done = 0, extra = 0;
        std::string out(blocks.size()+16, '\0');
        assert(EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char*>(out.data()), &done,
                                 reinterpret_cast<const unsigned char*>(blocks.data()), static_cast<int>(blocks.size())));
        assert(EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(out.data())+done, &extra));
        assert(static_cast<std::size_t>(done+extra) == blocks.size());
        EVP_CIPHER_CTX_free(ctx);
        blocks.assign(out.data(), blocks.size());
    };
    std::string clear(1024, '\0');
    for (std::size_t i=0;i<clear.size();++i) clear[i] = static_cast<char>((i*107+11)&255);
    std::string encrypted = clear;
    doujin::mega::decrypt_range(link, 0, encrypted, crypt);
    for (std::size_t first : {0u, 1u, 15u, 16u, 17u, 255u, 512u, 1020u}) {
        auto slice = encrypted.substr(first, 1024-first);
        doujin::mega::decrypt_range(link, first, slice, crypt);
        assert(slice == clear.substr(first));
    }
    bad([&] {
        std::string huge(2*1024*1024+1, 'x');
        doujin::mega::decrypt_range(link, 0, huge, crypt);
    });
}
