#pragma once

#include "third_party/json.hpp"
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>
#include <string_view>

namespace doujin {
inline std::uint32_t mpeg_be32(std::string_view value) {
    std::uint32_t word = 0;
    for (const auto byte : value.substr(0, 4)) word = (word << 8) | static_cast<unsigned char>(byte);
    return word;
}

struct MpegLayer3Frame {
    unsigned version, rate, channels, bitrate, bytes, crc_bytes;
};

inline std::optional<MpegLayer3Frame> mpeg_layer3_frame(std::string_view data) {
    if (data.size() < 4) return {};
    const auto word = mpeg_be32(data);
    const auto version = (word >> 19) & 3u;
    const auto rate_index = (word >> 10) & 3u;
    const auto bitrate_index = (word >> 12) & 15u;
    if ((word & 0xffe00000) != 0xffe00000 || ((word >> 17) & 3) != 1 ||
        version == 1 || rate_index == 3 || bitrate_index == 0 || bitrate_index == 15) return {};
    constexpr std::array<unsigned, 3> sample_rates{44100, 48000, 32000};
    constexpr std::array<unsigned, 14> mpeg1_rates{32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320};
    constexpr std::array<unsigned, 14> mpeg2_rates{8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160};
    const auto rate = sample_rates[rate_index] / (version == 3 ? 1 : version == 2 ? 2 : 4);
    const auto bitrate = (version == 3 ? mpeg1_rates : mpeg2_rates)[bitrate_index - 1] * 1000;
    const auto frame_bytes = (version == 3 ? 144 : 72) * bitrate / rate + ((word >> 9) & 1);
    return MpegLayer3Frame{version, rate, ((word >> 6) & 3) == 3 ? 1u : 2u,
                           bitrate, frame_bytes, (word & (1 << 16)) ? 0u : 2u};
}

inline nlohmann::json mp3_prefix_technical(std::string_view data, std::uint64_t tag_end, std::uint64_t file_size) {
    using Json = nlohmann::json;
    if (tag_end > data.size()) return {{"state", "no_frame"}};
    data.remove_prefix(static_cast<std::size_t>(tag_end));
    const auto frame = mpeg_layer3_frame(data);
    if (!frame) return {{"state", "no_frame"}};
    const auto second = frame->bytes <= data.size() ? mpeg_layer3_frame(data.substr(frame->bytes)) : std::nullopt;
    if (!second || second->version != frame->version || second->rate != frame->rate)
        return {{"state", "no_second_frame"}};

    Json metadata{{"codec", "MP3"}, {"encoding", "lossy"}, {"sample_rate_hz", frame->rate},
                  {"channels", frame->channels}, {"first_frame_bitrate_bps", frame->bitrate}};
    const auto side_info = frame->version == 3 ? (frame->channels == 1 ? 17u : 32u)
                                               : (frame->channels == 1 ? 9u : 17u);
    const auto xing = 4 + frame->crc_bytes + side_info;
    const auto first = data.substr(0, frame->bytes);
    std::uint32_t frames = 0;
    if (first.size() >= xing + 12 &&
        (first.substr(xing, 4) == "Xing" || first.substr(xing, 4) == "Info") &&
        (mpeg_be32(first.substr(xing + 4)) & 1)) {
        frames = mpeg_be32(first.substr(xing + 8));
    } else if (first.size() >= 54 && first.substr(36, 4) == "VBRI" && first.substr(40, 2) == std::string_view("\x00\x01", 2)) {
        frames = mpeg_be32(first.substr(50, 4));
    }
    if (frames && file_size >= tag_end && frames <= (file_size - tag_end) / 4) {
        const auto duration = static_cast<double>(frames) * (frame->version == 3 ? 1152 : 576) / frame->rate;
        metadata["duration_seconds"] = duration;
    }
    return {{"state", "ok"}, {"metadata", std::move(metadata)}};
}
}
