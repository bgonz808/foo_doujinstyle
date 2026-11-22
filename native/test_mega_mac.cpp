#include "mega_mac.hpp"
#include <openssl/evp.h>
#include <array>
#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
void encrypt(const unsigned char key[16], const EVP_CIPHER* mode, std::string& data,
             const unsigned char* iv = nullptr) {
    auto* context = EVP_CIPHER_CTX_new(); assert(context);
    assert(EVP_EncryptInit_ex(context, mode, nullptr, key, iv));
    assert(EVP_CIPHER_CTX_set_padding(context, 0));
    std::string out(data.size() + 16, '\0'); int len = 0, end = 0;
    assert(EVP_EncryptUpdate(context, reinterpret_cast<unsigned char*>(out.data()), &len,
                             reinterpret_cast<const unsigned char*>(data.data()), static_cast<int>(data.size())));
    assert(EVP_EncryptFinal_ex(context, reinterpret_cast<unsigned char*>(out.data()) + len, &end));
    assert(static_cast<std::size_t>(len+end) == data.size());
    data.assign(out.data(), data.size());
    EVP_CIPHER_CTX_free(context);
}
}
int main() {
    doujin::MegaLink link{};
    for (unsigned i=0;i<link.key.size();++i) link.key[i] = static_cast<unsigned char>(i);
    constexpr std::array<unsigned char, 8> reference_mac{0xac,0x58,0x6f,0x25,0x8b,0xd5,0xf2,0x1a};
    for (unsigned i = 0; i < 8; ++i) {
        link.key[8+i] ^= static_cast<unsigned char>(link.key[24+i] ^ reference_mac[i]);
        link.key[24+i] = reference_mac[i];
    }
    std::array<unsigned char,16> key{};
    for (unsigned i=0;i<16;++i) key[i] = link.key[i] ^ link.key[i+16];
    auto ecb = [&](std::string& data) { encrypt(key.data(), EVP_aes_128_ecb(), data); };
    auto cbc = [&](std::string& data, std::array<unsigned char,16> iv) {
        encrypt(key.data(), EVP_aes_128_cbc(), data, iv.data());
    };
    std::string plain(2500000, '\0');
    for (std::size_t i=0;i<plain.size();++i) plain[i] = static_cast<char>((i*173+i/7)&255);
    doujin::mega::FileMac verifier(link);
    std::size_t offset = 0;
    while (offset < plain.size()) {
        const auto size = verifier.next_size(plain.size());
        verifier.append(std::string_view(plain).substr(offset, size), plain.size(), cbc, ecb);
        offset += size;
    }
    assert(verifier.bytes_verified() == plain.size());
    // Cryptography Python independently produced this whole-file MAC vector.
    verifier.verify(plain.size());
    doujin::MegaLink wrong = link;
    wrong.key[24] ^= 1;
    doujin::mega::FileMac tampered(wrong);
    offset = 0;
    while (offset < plain.size()) {
        const auto size = tampered.next_size(plain.size());
        tampered.append(std::string_view(plain).substr(offset, size), plain.size(), cbc, ecb);
        offset += size;
    }
    bool rejected = false;
    try { tampered.verify(plain.size()); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    // Construct a valid link MAC from a reference Python/cryptography vector separately in parity fixtures.
    doujin::mega::FileMac incomplete(link);
    rejected = false;
    try { incomplete.append("incorrect length", plain.size(), cbc, ecb); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
}
