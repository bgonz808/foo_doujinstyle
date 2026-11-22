#pragma once
#include "mega_mac.hpp"
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>

namespace doujin::mega {
class VerifiedPayload {
public:
    explicit VerifiedPayload(const MegaLink& link, std::uint64_t advertised) : mac_(link), size_(advertised) {
        if (!advertised) throw std::runtime_error("Cannot authenticate an empty MEGA file");
    }
    std::size_t next_size() const { return mac_.next_size(size_); }
    template<class Decrypt, class CBC, class ECB, class Sink>
    void append(std::uint64_t position, std::string& ciphertext,
                Decrypt&& decrypt, CBC&& cbc, ECB&& ecb, Sink&& sink) {
        if (position != mac_.bytes_verified() || ciphertext.size() != next_size())
            throw std::runtime_error("MEGA download chunks are out of order or incomplete");
        decrypt(position, ciphertext);
        mac_.append(ciphertext, size_, std::forward<CBC>(cbc), std::forward<ECB>(ecb));
        sink(ciphertext); // Unverified bytes MUST remain in an exclusive temporary file.
    }
    void finish() const { mac_.verify(size_); }
    std::uint64_t consumed() const { return mac_.bytes_verified(); }
private:
    FileMac mac_;
    std::uint64_t size_;
};
} // namespace doujin::mega
