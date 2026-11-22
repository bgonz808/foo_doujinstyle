#pragma once
#include "mega_filename.hpp"
#include <stdexcept>
#include <cstdint>
#include <optional>
#include <charconv>
#include <limits>
#include <string>
#include <string_view>
namespace doujin::mediafire {
// Shared, pure validation for the WinHTTP streaming body and offline fixtures.
class LengthValidator {
    static constexpr std::uint64_t maximum = 16ULL * 1024 * 1024 * 1024;
    std::optional<std::uint64_t> expected_;
    std::uint64_t received_ = 0;
public:
    explicit LengthValidator(std::string_view advertised) {
        if (advertised.empty()) return;
        std::uint64_t value = 0;
        const auto [end, error] = std::from_chars(advertised.data(), advertised.data() + advertised.size(), value);
        if (error != std::errc{} || end != advertised.data() + advertised.size() || !value || value > maximum)
            throw std::runtime_error("Invalid MediaFire Content-Length");
        expected_ = value;
    }
    void append(std::size_t bytes) {
        if (bytes > maximum - received_ || (expected_ && bytes > *expected_ - received_))
            throw std::runtime_error("MediaFire archive is longer than advertised");
        received_ += bytes;
    }
    std::uint64_t finish() const {
        if (!received_ || (expected_ && received_ != *expected_))
            throw std::runtime_error("MediaFire archive was truncated or empty");
        return received_;
    }
};

class PrefixValidator {
    std::string prefix_;
    bool recognized_ = false;
public:
    void append(std::string_view bytes) {
        if (prefix_.size() < 8) {
            const auto n = std::min<std::size_t>(8 - prefix_.size(), bytes.size());
            prefix_.append(bytes.substr(0, n));
        }
        if (prefix_.size() >= 8 && !recognized_) {
            (void)mega::archive_suffix(prefix_);
            recognized_ = true;
        }
    }
    std::string finish() const {
        if (prefix_.empty()) throw std::runtime_error("Empty MediaFire archive");
        return mega::archive_suffix(prefix_);
    }
};
} // namespace doujin::mediafire
