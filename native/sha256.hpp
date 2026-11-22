#pragma once

#include "descriptor.hpp"

#include <limits>

#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/sha.h>
#endif

namespace doujin {

inline std::string sha256_key(std::string_view key) {
    std::array<unsigned char, 32> digest{};
#ifdef _WIN32
    if (key.size() > std::numeric_limits<ULONG>::max()) return {};
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
    BCRYPT_HASH_HANDLE hash = nullptr;
    auto status = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0);
    if (status >= 0) {
        status = BCryptHashData(hash, reinterpret_cast<PUCHAR>(const_cast<char*>(key.data())),
                               static_cast<ULONG>(key.size()), 0);
        if (status >= 0) status = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0);
        BCryptDestroyHash(hash);
    }
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status < 0) return {};
#else
    if (!SHA256(reinterpret_cast<const unsigned char*>(key.data()), key.size(), digest.data())) return {};
#endif
    return encode_hex(std::string_view(reinterpret_cast<const char*>(digest.data()), digest.size()));
}

} // namespace doujin
