#pragma once
#ifdef _WIN32
#include "mega_public.hpp"
#include "mega_mac.hpp"
#include <bcrypt.h>
#include <windows.h>
#include <array>
#include <cstring>
#include <vector>
#include <stdexcept>

namespace doujin::mega {

class WindowsCipher {
public:
    explicit WindowsCipher(const MegaLink& link) : link_(link) {
        if (BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_AES_ALGORITHM, nullptr, 0) < 0)
            throw std::runtime_error("Windows AES provider unavailable");
        auto mode = BCRYPT_CHAIN_MODE_ECB;
        std::array<unsigned char, 16> key{};
        for (std::size_t i = 0; i < key.size(); ++i) key[i] = link.key[i] ^ link.key[i + 16];
        if (BCryptSetProperty(algorithm_, BCRYPT_CHAINING_MODE,
              reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(mode)), sizeof(BCRYPT_CHAIN_MODE_ECB), 0) < 0 ||
            BCryptGenerateSymmetricKey(algorithm_, &key_, nullptr, 0, key.data(),
                                       static_cast<ULONG>(key.size()), 0) < 0) {
            SecureZeroMemory(key.data(), key.size());
            if (key_) BCryptDestroyKey(key_);
            BCryptCloseAlgorithmProvider(algorithm_, 0);
            algorithm_ = nullptr;
            throw std::runtime_error("Windows AES key setup failed");
        }
        if (BCryptOpenAlgorithmProvider(&cbc_algorithm_, BCRYPT_AES_ALGORITHM, nullptr, 0) < 0) {
            SecureZeroMemory(key.data(), key.size());
            BCryptDestroyKey(key_); BCryptCloseAlgorithmProvider(algorithm_, 0);
            throw std::runtime_error("Windows AES-CBC provider unavailable");
        }
        auto cbc = BCRYPT_CHAIN_MODE_CBC;
        if (BCryptSetProperty(cbc_algorithm_, BCRYPT_CHAINING_MODE,
              reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(cbc)), sizeof(BCRYPT_CHAIN_MODE_CBC), 0) < 0 ||
            BCryptGenerateSymmetricKey(cbc_algorithm_, &cbc_key_, nullptr, 0, key.data(),
                                       static_cast<ULONG>(key.size()), 0) < 0) {
            SecureZeroMemory(key.data(), key.size());
            if (cbc_key_) BCryptDestroyKey(cbc_key_);
            BCryptCloseAlgorithmProvider(cbc_algorithm_, 0);
            BCryptDestroyKey(key_); BCryptCloseAlgorithmProvider(algorithm_, 0);
            throw std::runtime_error("Windows AES-CBC key setup failed");
        }
        SecureZeroMemory(key.data(), key.size());
    }
    ~WindowsCipher() {
        if (key_) BCryptDestroyKey(key_);
        if (algorithm_) BCryptCloseAlgorithmProvider(algorithm_, 0);
        if (cbc_key_) BCryptDestroyKey(cbc_key_);
        if (cbc_algorithm_) BCryptCloseAlgorithmProvider(cbc_algorithm_, 0);
    }
    WindowsCipher(const WindowsCipher&) = delete;
    WindowsCipher& operator=(const WindowsCipher&) = delete;

    void encrypt_cbc(std::string& data, std::array<unsigned char, 16> iv) const {
        if (data.empty() || data.size() % 16 || data.size() > 1048576)
            throw std::runtime_error("Invalid MEGA MAC chunk");
        ULONG written = 0;
        if (BCryptEncrypt(cbc_key_, reinterpret_cast<PUCHAR>(data.data()), static_cast<ULONG>(data.size()),
                nullptr, iv.data(), static_cast<ULONG>(iv.size()),
                reinterpret_cast<PUCHAR>(data.data()), static_cast<ULONG>(data.size()), &written, 0) < 0 ||
            written != data.size()) throw std::runtime_error("MEGA CBC MAC calculation failed");
    }

    std::string decrypt_filename(std::string_view attributes) const {
        auto bytes = base64url_decode(attributes);
        if (bytes.empty() || bytes.size() % 16) throw std::runtime_error("Invalid MEGA AES-CBC attributes");
        std::array<unsigned char, 16> previous{}, ciphertext{};
        for (std::size_t at = 0; at < bytes.size(); at += 16) {
            std::memcpy(ciphertext.data(), bytes.data() + at, 16);
            ULONG written = 0;
            if (BCryptDecrypt(key_, reinterpret_cast<PUCHAR>(bytes.data() + at), 16, nullptr,
                    nullptr, 0, reinterpret_cast<PUCHAR>(bytes.data() + at), 16, &written, 0) < 0 || written != 16)
                throw std::runtime_error("MEGA file attribute decryption failed");
            for (std::size_t j = 0; j < 16; ++j) bytes[at + j] ^= static_cast<char>(previous[j]);
            previous = ciphertext;
        }
        return file_name_from_attributes(bytes);
    }

    void encrypt(std::string& blocks) const {
        if (blocks.size() > 2 * 1024 * 1024 + 16 || blocks.size() % 16)
            throw std::runtime_error("Invalid MEGA AES block buffer");
        ULONG written = 0;
        if (BCryptEncrypt(key_, reinterpret_cast<PUCHAR>(blocks.data()), static_cast<ULONG>(blocks.size()),
                nullptr, nullptr, 0, reinterpret_cast<PUCHAR>(blocks.data()), static_cast<ULONG>(blocks.size()),
                &written, 0) < 0 || written != blocks.size())
            throw std::runtime_error("Windows MEGA AES block encryption failed");
    }
    template<class Buffer> void decrypt(std::uint64_t offset, Buffer& bytes) const {
        decrypt_range(link_, offset, bytes, [this](std::string& blocks) { encrypt(blocks); });
    }
private:
    MegaLink link_;
    BCRYPT_ALG_HANDLE algorithm_ = nullptr;
    BCRYPT_KEY_HANDLE key_ = nullptr;
    BCRYPT_ALG_HANDLE cbc_algorithm_ = nullptr;
    BCRYPT_KEY_HANDLE cbc_key_ = nullptr;
};

} // namespace doujin::mega
#endif
