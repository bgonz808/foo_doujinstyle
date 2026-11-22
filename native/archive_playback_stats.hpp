#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>

namespace doujin {
class ArchivePlaybackStats {
public:
    bool start(std::string_view identity, bool saved_metadata, std::uint64_t ms) {
        const auto key = std::hash<std::string_view>{}(identity);
        std::lock_guard lock(mutex_);
        const auto familiar = saved_metadata || seen_.contains(key);
        if (seen_.size() == 8192 && !seen_.contains(key)) seen_.clear();
        seen_.insert(key);
        (familiar ? familiar_ : first_).add(ms);
        return familiar;
    }

    void seek(std::uint64_t ms, bool deflate, bool looked_up, bool restored, std::uint64_t compressed_advance) {
        std::lock_guard lock(mutex_);
        if (deflate) {
            (restored ? restored_ : ordinary_).add(ms);
            if (looked_up) ++looked_up_;
        } else {
            other_.add(ms);
        }
        compressed_advance_ += compressed_advance;
    }

    std::vector<std::string> report() const {
        std::lock_guard lock(mutex_);
        const auto seeks = ordinary_.count + restored_.count;
        return {"Archive playback this session",
                "First audio, new reference  " + first_.text(),
                "First audio, familiar reference  " + familiar_.text(),
                "DEFLATE seeks restored from checkpoints  " + std::to_string(restored_.count) + "/" + std::to_string(seeks) +
                    (seeks ? " (" + std::to_string(100 * restored_.count / seeks) + "%)" : ""),
                "Seek-to-audio with restore  " + restored_.text(),
                "DEFLATE seek-to-audio without restore  " + ordinary_.text(),
                "DEFLATE seeks that queried the index  " + std::to_string(looked_up_),
                "Other archive seek-to-audio  " + other_.text(),
                "Compressed bytes skipped by restores during seeks  " + std::to_string(compressed_advance_)};
    }

private:
    struct Timing {
        std::uint64_t count = 0, sum_ms = 0;
        void add(std::uint64_t ms) { ++count; sum_ms += ms; }
        std::string text() const {
            return std::to_string(count) + " samples" +
                (count ? ", mean " + std::to_string(sum_ms / count) + " ms" : "");
        }
    };
    mutable std::mutex mutex_;
    std::unordered_set<std::size_t> seen_;
    Timing first_, familiar_, restored_, ordinary_, other_;
    std::uint64_t looked_up_ = 0;
    std::uint64_t compressed_advance_ = 0;
};

inline ArchivePlaybackStats archive_playback_stats;
}
