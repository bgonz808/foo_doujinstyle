#pragma once
#include <chrono>
#include <cstdint>
#include <optional>

namespace doujin {
class IdleGate {
    std::optional<std::chrono::steady_clock::time_point> prior_;
public:
    bool ready(std::uint64_t input_idle_ms, std::chrono::steady_clock::time_point now) {
        using namespace std::chrono;
        if (input_idle_ms < 30000 || (prior_ && now - *prior_ < seconds(10))) return false;
        prior_ = now;
        return true;
    }
};
}
