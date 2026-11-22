#pragma once

#include <algorithm>
#include <cstdint>

namespace doujin {
inline constexpr std::uint64_t deflate_startup_window = 64 * 1024;
inline constexpr std::uint64_t deflate_sequential_window = 256 * 1024;
inline constexpr std::uint64_t archive_range_window = 1024 * 1024;
inline constexpr std::uint64_t coalesced_range_window = 2 * archive_range_window;

inline std::uint64_t deflate_input_window(std::uint64_t demand, bool playback, bool sequential) {
    const auto minimum = playback && sequential ? deflate_sequential_window : deflate_startup_window;
    const auto maximum = playback ? coalesced_range_window : archive_range_window;
    return std::clamp(demand, minimum, maximum);
}
}
