#pragma once
#include "descriptor.hpp"
#include <array>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#endif

namespace doujin::archive {
// The digest identifies downloaded plaintext bytes. It is NOT a signature
// or proof that a MediaFire source is trustworthy; it can prove mirror equality.
class Sha256Stream {
#ifdef _WIN32
    BCRYPT_ALG_HANDLE algorithm_ = nullptr;
    BCRYPT_HASH_HANDLE hash_ = nullptr;
#else
    EVP_MD_CTX* hash_ = nullptr;
#endif
    bool finished_ = false;
public:
    Sha256Stream() {
#ifdef _WIN32
        if (BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
            throw std::runtime_error("SHA256 provider unavailable");
        if (BCryptCreateHash(algorithm_, &hash_, nullptr, 0, nullptr, 0, 0) < 0) {
            BCryptCloseAlgorithmProvider(algorithm_, 0);
            algorithm_ = nullptr;
            throw std::runtime_error("Could not create SHA256 hash");
        }
#else
        hash_ = EVP_MD_CTX_new();
        if (!hash_ || EVP_DigestInit_ex(hash_, EVP_sha256(), nullptr) != 1) {
            EVP_MD_CTX_free(hash_);
            hash_ = nullptr;
            throw std::runtime_error("Could not initialize SHA256");
        }
#endif
    }
    Sha256Stream(const Sha256Stream&) = delete;
    Sha256Stream& operator=(const Sha256Stream&) = delete;
    ~Sha256Stream() {
#ifdef _WIN32
        if (hash_) BCryptDestroyHash(hash_);
        if (algorithm_) BCryptCloseAlgorithmProvider(algorithm_, 0);
#else
        EVP_MD_CTX_free(hash_);
#endif
    }
    void update(std::string_view data) {
        if (finished_) throw std::runtime_error("SHA256 already finalized");
#ifdef _WIN32
        if (data.size() > std::numeric_limits<ULONG>::max() || BCryptHashData(hash_,
              reinterpret_cast<PUCHAR>(const_cast<char*>(data.data())), static_cast<ULONG>(data.size()), 0) < 0)
            throw std::runtime_error("SHA256 update failed");
#else
        if (EVP_DigestUpdate(hash_, data.data(), data.size()) != 1)
            throw std::runtime_error("SHA256 update failed");
#endif
    }
    std::string finish() {
        if (finished_) throw std::runtime_error("SHA256 already finalized");
        finished_ = true;
        std::array<unsigned char, 32> result{};
#ifdef _WIN32
        if (BCryptFinishHash(hash_, result.data(), static_cast<ULONG>(result.size()), 0) < 0)
            throw std::runtime_error("SHA256 finalization failed");
#else
        unsigned int size = 0;
        if (EVP_DigestFinal_ex(hash_, result.data(), &size) != 1 || size != result.size())
            throw std::runtime_error("SHA256 finalization failed");
#endif
        return encode_hex(std::string_view(reinterpret_cast<const char*>(result.data()), result.size()));
    }
};
} // namespace doujin::archive
