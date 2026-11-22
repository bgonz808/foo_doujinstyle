#pragma once

#include "third_party/json.hpp"
#include <cstdint>
#include <string_view>
#include <utility>

namespace doujin {
inline std::uint32_t wave_le(std::string_view bytes) {
    std::uint32_t result = 0;
    for (std::size_t index = 0; index < bytes.size(); ++index)
        result |= static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[index])) << (8 * index);
    return result;
}

inline nlohmann::json wave_prefix(std::string_view data, std::uint64_t file_size) {
    using Json = nlohmann::json;
    if (data.size() < 12 || !data.starts_with("RIFF") || data.substr(8, 4) != "WAVE") return nullptr;

    constexpr std::string_view pcm_guid_tail("\0\0\0\0\x10\0\x80\0\0\xaa\0\x38\x9b\x71", 14);
    Json metadata;
    std::uint32_t rate = 0;
    std::uint32_t align = 0;
    std::size_t pos = 12;
    while (data.size() - pos >= 8) {
        const auto kind = data.substr(pos, 4);
        const auto size = wave_le(data.substr(pos + 4, 4));
        pos += 8;
        const auto available = data.size() - pos;
        if (kind == "fmt " && size >= 16 && available >= 16) {
            auto code = wave_le(data.substr(pos, 2));
            const auto channels = wave_le(data.substr(pos + 2, 2));
            const auto sample_rate = wave_le(data.substr(pos + 4, 4));
            const auto byte_rate = wave_le(data.substr(pos + 8, 4));
            const auto block_align = wave_le(data.substr(pos + 12, 2));
            auto bits = wave_le(data.substr(pos + 14, 2));
            if (code == 0xfffe && size >= 40 && available >= 40 && data.substr(pos + 26, 14) == pcm_guid_tail) {
                code = wave_le(data.substr(pos + 24, 2));
                const auto valid_bits = wave_le(data.substr(pos + 18, 2));
                if (valid_bits) bits = valid_bits;
            }
            if (channels && sample_rate && (code == 1 || code == 3)) {
                rate = sample_rate;
                align = block_align;
                metadata = {{"codec", code == 1 ? "PCM" : "IEEE float PCM"}, {"encoding", "lossless"},
                            {"channels", channels}, {"sample_rate_hz", rate}, {"bits_per_sample", bits}};
                if (align && byte_rate == std::uint64_t(rate) * align)
                    metadata["nominal_bitrate_bps"] = std::uint64_t(byte_rate) * 8;
            }
        }
        if (kind == "data" && !metadata.is_null()) {
            if (align && pos <= file_size && size <= file_size - pos && size % align == 0) {
                const auto frames = size / align;
                metadata["duration_seconds"] = static_cast<double>(frames) / rate;
            }
            break;
        }
        const auto padded_size = std::uint64_t(size) + (size & 1);
        if (padded_size > available) break;
        pos += static_cast<std::size_t>(padded_size);
    }
    if (metadata.is_null()) return nullptr;
    return {{"metadata", std::move(metadata)}};
}
}
