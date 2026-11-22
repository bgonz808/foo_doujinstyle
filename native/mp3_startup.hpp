#pragma once

#include <cstdint>

namespace doujin {
class Mp3StartupBudget {
public:
    static constexpr std::uint64_t byte_limit = 4 * 1024 * 1024;
    static constexpr std::uint64_t compressed_limit = 5 * 1024 * 1024;

    bool allow_seek(std::uint64_t position) {
        return active_ ? accept(position <= byte_limit) : true;
    }

    bool allow_read(std::uint64_t position, std::uint64_t bytes) {
        if (!active_) return true;
        if (!accept(position <= byte_limit && bytes <= byte_limit - position && bytes <= remaining_))
            return false;
        remaining_ -= bytes;
        return true;
    }

    std::uint64_t compressed_available() const {
        return active_ ? compressed_remaining_ : UINT64_MAX;
    }

    void consumed_compressed(std::uint64_t bytes) {
        if (active_) compressed_remaining_ -= bytes;
    }

    void reject() { rejected_ = true; }
    bool rejected() const { return rejected_; }
    void finish() { active_ = false; }

private:
    bool accept(bool within_limit) {
        if (!within_limit) reject();
        return within_limit && !rejected_;
    }

    std::uint64_t remaining_ = byte_limit;
    std::uint64_t compressed_remaining_ = compressed_limit;
    bool active_ = true;
    bool rejected_ = false;
};
}
