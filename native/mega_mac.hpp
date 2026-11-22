#pragma once
#include "descriptor.hpp"
#include <array>
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::mega {
class FileMac {
public:
    explicit FileMac(const MegaLink& link) {
        for (std::size_t i = 0; i < 8; ++i) {
            nonce_[i] = link.key[16 + i];
            expected_[i] = link.key[24 + i];
        }
    }
    std::size_t next_size(std::uint64_t total) const {
        if (seen_ >= total) return 0;
        const auto maximum = std::min<std::uint64_t>(std::min<std::uint64_t>(std::min<std::uint64_t>(round_, 8) * 131072, 1048576), total - seen_);
        return static_cast<std::size_t>(maximum);
    }
    template<class CBC, class ECB>
    void append(std::string_view plaintext, std::uint64_t total, CBC&& encrypt_cbc, ECB&& encrypt_ecb) {
        if (plaintext.empty() || plaintext.size() != next_size(total))
            throw std::runtime_error("MEGA integrity chunks arrived out of sequence");
        std::string padded(plaintext);
        padded.resize((padded.size() + 15) & ~std::size_t{15}, '\0');
        std::array<unsigned char, 16> iv{};
        for (std::size_t i = 0; i < 8; ++i) iv[i] = iv[i + 8] = nonce_[i];
        encrypt_cbc(padded, iv);
        std::string combination(16, '\0');
        for (std::size_t i = 0; i < 16; ++i)
            combination[i] = static_cast<char>(accumulator_[i] ^ static_cast<unsigned char>(padded[padded.size() - 16 + i]));
        encrypt_ecb(combination);
        for (std::size_t i = 0; i < 16; ++i) accumulator_[i] = static_cast<unsigned char>(combination[i]);
        seen_ += plaintext.size();
        ++round_;
    }
    void verify(std::uint64_t total) const {
        if (!total || seen_ != total) throw std::runtime_error("Incomplete MEGA file integrity check");
        unsigned difference = 0;
        for (std::size_t i = 0; i < 4; ++i) {
            difference |= static_cast<unsigned>(expected_[i] ^ accumulator_[i] ^ accumulator_[i + 4]);
            difference |= static_cast<unsigned>(expected_[i + 4] ^ accumulator_[i + 8] ^ accumulator_[i + 12]);
        }
        if (difference) throw std::runtime_error("MEGA whole-file authentication failed");
    }
    std::uint64_t bytes_verified() const { return seen_; }
private:
    std::array<unsigned char, 8> nonce_{}, expected_{};
    std::array<unsigned char, 16> accumulator_{};
    std::uint64_t seen_ = 0, round_ = 1;
};
} // namespace doujin::mega
