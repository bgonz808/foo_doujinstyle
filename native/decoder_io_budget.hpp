#pragma once

#include <cstdint>

namespace doujin {
// Counts logical requests and compressed input supplied, not physical HTTP bytes.
// Default limits preserve the legacy MP3 playback policy. Other operations can
// select a different position ceiling and independent byte allowances.
class DecoderIoBudget {
public:
    static constexpr std::uint64_t byte_limit = 4 * 1024 * 1024;
    static constexpr std::uint64_t compressed_limit = 5 * 1024 * 1024;

    struct Limits {
        std::uint64_t max_position;
        std::uint64_t logical_bytes;
        std::uint64_t compressed_bytes;
    };
    DecoderIoBudget() : DecoderIoBudget({byte_limit, byte_limit, compressed_limit}) {}
    explicit DecoderIoBudget(Limits limits)
        : max_position_(limits.max_position), remaining_(limits.logical_bytes),
          compressed_remaining_(limits.compressed_bytes) {}

    bool allow_seek(std::uint64_t position) {
        return active_ ? accept(position <= max_position_) : true;
    }

    bool allow_read(std::uint64_t position, std::uint64_t bytes) {
        if (!active_) return true;
        if (!accept(position <= max_position_ && bytes <= max_position_ - position && bytes <= remaining_))
            return false;
        remaining_ -= bytes;
        return true;
    }

    std::uint64_t compressed_available() const {
        return active_ ? compressed_remaining_ : UINT64_MAX;
    }

    void consumed_compressed(std::uint64_t bytes) {
        if (!active_) return;
        if (bytes > compressed_remaining_) {
            compressed_remaining_ = 0;
            reject();
            return;
        }
        compressed_remaining_ -= bytes;
    }

    void reject() { rejected_ = true; }
    bool rejected() const { return rejected_; }
    void finish() { active_ = false; }

private:
    bool accept(bool within_limit) {
        if (!within_limit) reject();
        return within_limit && !rejected_;
    }

    bool active_ = true;
    bool rejected_ = false;
    std::uint64_t max_position_;
    std::uint64_t remaining_;
    std::uint64_t compressed_remaining_;
};
}
