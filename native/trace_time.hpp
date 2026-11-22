#pragma once

#include <array>
#include <charconv>
#include <cmath>
#include <string>
#include <string_view>

namespace doujin {
inline std::string trace_duration(double seconds) {
    if (!std::isfinite(seconds) || seconds < 0) return "n/a";
    if (seconds == 0) return "0 ms";
    constexpr std::array scales{1e-9, 1e-6, 1e-3, 1.0, 60.0, 3600.0, 86400.0};
    constexpr std::array<std::string_view, scales.size()> units{" ns", " µs", " ms", " s", " min", " h", " d"};
    std::size_t unit = 0;
    while (unit + 1 < scales.size() && seconds / scales[unit] >= 999.5) ++unit;
    const auto value = seconds / scales[unit];
    const int decimals = value >= 99.95 ? 0 : (value >= 9.995 ? 1 : 2);
    std::array<char, 32> buffer{};
    const auto [end, error] = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value,
                                            std::chars_format::fixed, decimals);
    if (error != std::errc{}) return "n/a";
    auto text = std::string(buffer.data(), end);
    text += units[unit];
    return text;
}
} // namespace doujin
