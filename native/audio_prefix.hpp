#pragma once

#include "mp3_prefix.hpp"
#include "wave_prefix.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>

namespace doujin {
namespace audio_probe {
using Json = nlohmann::json;

inline Json comment_tags(std::string_view data) {
    const auto word = [&] {
        if (data.size() < 4) throw std::runtime_error("Truncated FLAC comment length");
        const auto value = wave_le(data.substr(0, 4));
        data.remove_prefix(4);
        return value;
    };
    const auto next = [&] {
        const auto length = word();
        if (length > data.size()) throw std::runtime_error("Truncated FLAC comment");
        const auto raw = data.substr(0, length);
        data.remove_prefix(length);
        return raw;
    };
    next(); // Vendor string.
    const auto count = word();
    if (count > data.size() / 4) throw std::runtime_error("Invalid FLAC comment count");
    auto tags = Json::object();
    for (std::uint32_t i = 0; i < count; ++i) {
        const auto field = next();
        const auto split = field.find('=');
        if (split == field.npos || !split || split + 1 == field.size()) continue;
        std::string key(field.substr(0, split));
        for (auto& byte : key) if (byte >= 'a' && byte <= 'z') byte -= 'a' - 'A';
        tags[key].push_back(field.substr(split + 1));
    }
    return tags;
}

inline void add_comments(Json& result, std::string_view data) {
    try {
        auto tags = comment_tags(data);
        if (tags.erase("METADATA_BLOCK_PICTURE"))
            result["artwork"].push_back({{"source", "Vorbis comment"}});
        result["tags"] = std::move(tags);
    } catch (const std::runtime_error& error) {
        result["prefix_status"] = error.what();
    }
}

inline Json flac(std::string_view data) {
    if (data.size() < 42 || (data[4] & 127) || data.substr(5, 3) != std::string_view("\0\0\x22", 3))
        return nullptr;
    const auto packed = (std::uint64_t(mpeg_be32(data.substr(18, 4))) << 32) | mpeg_be32(data.substr(22, 4));
    const auto rate = packed >> 44;
    const auto samples = packed & ((std::uint64_t{1} << 36) - 1);
    if (!rate) return nullptr;
    Json result{{"metadata", {{"codec", "FLAC"}, {"encoding", "lossless"}, {"sample_rate_hz", rate},
        {"channels", ((packed >> 41) & 7) + 1}, {"bits_per_sample", ((packed >> 36) & 31) + 1},
        {"duration_seconds", samples ? Json(std::nearbyint(double(samples) / double(rate) * 1000.0) / 1000.0) : Json()}}}};
    bool last = (data[4] & 128) != 0;
    data.remove_prefix(42);
    while (!last && data.size() >= 4) {
        const auto block = data[0] & 127;
        last = (data[0] & 128) != 0;
        const auto size = mpeg_be32(data.substr(1, 3));
        data.remove_prefix(4);
        const auto payload = data.substr(0, size);
        if (block == 6) {
            Json picture{{"source", "FLAC PICTURE"}, {"block_bytes", size}};
            if (payload.size() >= 8) {
                const auto length = mpeg_be32(payload.substr(4, 4));
                if (length <= payload.size() - 8) {
                    std::string mime;
                    for (unsigned char byte : payload.substr(8, length)) {
                        if (byte < 128) mime.push_back(static_cast<char>(byte));
                        else mime.append("\xef\xbf\xbd");
                    }
                    picture["mime"] = std::move(mime);
                }
            }
            result["artwork"].push_back(std::move(picture));
        }
        if (block == 3) result["seek_table_entries"] = size / 18;
        if (payload.size() != size) {
            result["prefix_status"] = "metadata block extends beyond probe";
            break;
        }
        if (block == 4) add_comments(result, payload);
        if (result.contains("prefix_status")) break;
        data.remove_prefix(size);
    }
    if (!last && !result.contains("prefix_status")) result["prefix_status"] = "metadata continues beyond probe";
    return result;
}

inline Json ogg_identification(std::string_view packet) {
    const auto vorbis = std::string_view("\x01vorbis", 7);
    if (packet.size() >= 19 && packet.starts_with("OpusHead") && packet[9]) {
        Json result{{"codec", "Opus"}, {"encoding", "lossy"},
                    {"channels", static_cast<unsigned char>(packet[9])}, {"sample_rate_hz", 48000}};
        if (const auto rate = wave_le(packet.substr(12, 4))) result["input_sample_rate_hz"] = rate;
        return result;
    }
    if (packet.size() >= 30 && packet.starts_with(vorbis) && packet[11]) {
        const auto rate = wave_le(packet.substr(12, 4));
        if (!rate) return nullptr;
        Json result{{"codec", "Vorbis"}, {"encoding", "lossy"},
                    {"channels", static_cast<unsigned char>(packet[11])}, {"sample_rate_hz", rate}};
        const auto nominal = wave_le(packet.substr(20, 4));
        if (nominal && nominal <= INT32_MAX) result["nominal_bitrate_bps"] = nominal;
        return result;
    }
    return nullptr;
}

inline Json ogg(std::string_view data) {
    if (data.size() < 27) return nullptr;
    const auto serial = data.substr(14, 4);
    const auto page_marker = std::string_view("OggS\0", 5);
    Json result;
    std::string packet;
    while (data.size() >= 27 && data.starts_with(page_marker)) {
        const auto count = static_cast<unsigned char>(data[26]);
        if (count > data.size() - 27) break;
        const auto segments = data.substr(27, count);
        const bool stream = data.substr(14, 4) == serial;
        data.remove_prefix(27 + count);
        std::size_t bytes = 0;
        for (unsigned char size : segments) bytes += size;
        if (bytes > data.size()) break;
        if (!stream) {
            data.remove_prefix(bytes);
            continue;
        }
        for (unsigned char size : segments) {
            packet.append(data.substr(0, size));
            data.remove_prefix(size);
            if (size == 255) continue;
            if (result.is_null()) {
                auto metadata = ogg_identification(packet);
                if (metadata.is_null()) return nullptr;
                result = {{"metadata", std::move(metadata)}};
            } else if (packet.starts_with("OpusTags") || packet.starts_with("\x03vorbis")) {
                add_comments(result, std::string_view(packet).substr(packet.starts_with("OpusTags") ? 8 : 7));
                return result;
            }
            packet.clear();
        }
    }
    return result;
}

inline std::optional<std::uint32_t> synchsafe(std::string_view data) {
    std::uint32_t size = 0;
    for (unsigned char byte : data) {
        if (byte >= 128) return {};
        size = (size << 7) | byte;
    }
    return size;
}

inline void append_codepoint(std::string& out, unsigned value) {
    if (value < 0x80) {
        out.push_back(static_cast<char>(value));
        return;
    }
    const unsigned bytes = value < 0x800 ? 2 : value < 0x10000 ? 3 : 4;
    const unsigned lead = bytes == 2 ? 0xc0 : bytes == 3 ? 0xe0 : 0xf0;
    out.push_back(static_cast<char>(lead | (value >> (6 * (bytes - 1)))));
    for (unsigned index = bytes - 1; index > 0; --index)
        out.push_back(static_cast<char>(0x80 | ((value >> (6 * (index - 1))) & 63)));
}

inline std::string id3_text(std::string_view bytes, unsigned encoding) {
    std::string result;
    if (encoding == 0) {
        for (unsigned char byte : bytes) append_codepoint(result, byte);
    } else if (encoding == 3) {
        try {
            static_cast<void>(Json(bytes).dump());
            result = bytes;
        } catch (const Json::type_error&) {}
    } else {
        if (bytes.size() % 2) return {};
        auto little = encoding == 1;
        if (encoding == 1 && (bytes.starts_with("\xfe\xff") || bytes.starts_with("\xff\xfe"))) {
            little = bytes.starts_with("\xff\xfe");
            bytes.remove_prefix(2);
        }
        const auto read = [&] {
            const auto unit = little ? wave_le(bytes.substr(0, 2)) : mpeg_be32(bytes.substr(0, 2));
            bytes.remove_prefix(2);
            return unit;
        };
        while (!bytes.empty()) {
            auto value = read();
            if (value >= 0xd800 && value <= 0xdbff) {
                if (bytes.size() < 2) return {};
                const auto low = read();
                if (low < 0xdc00 || low > 0xdfff) return {};
                value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
            } else if (value >= 0xdc00 && value <= 0xdfff) {
                return {};
            }
            append_codepoint(result, value);
        }
    }
    const auto first = result.find_first_not_of('\0');
    if (first == result.npos) return {};
    return result.substr(first, result.find_last_not_of('\0') - first + 1);
}

inline Json mp3(std::string_view data, std::uint64_t file_size) {
    Json result = Json::object();
    std::uint64_t end = 0;
    if (data.size() >= 10 && data.starts_with("ID3")) {
        if (const auto bytes = synchsafe(data.substr(6, 4))) {
            const auto version = data[3];
            end = 10 + *bytes;
            const bool footer = version == 4 && (data[5] & 16);
            auto frames = data.substr(10, *bytes);
            constexpr std::pair<std::string_view, std::string_view> mapped[]{
                {"TIT2", "TITLE"}, {"TPE1", "ARTIST"}, {"TPE2", "ALBUMARTIST"}, {"TALB", "ALBUM"},
                {"TRCK", "TRACKNUMBER"}, {"TPOS", "DISCNUMBER"}, {"TCON", "GENRE"},
            };
            std::uint32_t remaining = *bytes;
            while ((version == 3 || version == 4) && !(data[5] & 0xc0) && frames.size() >= 10) {
                const auto name = frames.substr(0, 4);
                if (name == std::string_view("\0\0\0\0", 4)) break;
                const auto length = version == 4 ? synchsafe(frames.substr(4, 4))
                                                  : std::optional(mpeg_be32(frames.substr(4, 4)));
                const auto flags = frames[9];
                frames.remove_prefix(10);
                remaining -= 10;
                if (!length || !*length || *length > remaining) break;
                if (name == "APIC") result["artwork"].push_back({{"source", "ID3 APIC"}, {"block_bytes", *length}});
                if (*length > frames.size()) break;
                const auto encoding = static_cast<unsigned char>(frames[0]);
                if (!flags && encoding < 4) {
                    const auto tag = std::ranges::find_if(mapped, [name](const auto& entry) { return entry.first == name; });
                    if (tag != std::end(mapped)) {
                        auto text = id3_text(frames.substr(1, *length - 1), encoding);
                        if (!text.empty()) {
                            auto values = Json::array();
                            std::string_view parts(text);
                            for (;;) {
                                const auto zero = parts.find('\0');
                                values.push_back(parts.substr(0, zero));
                                if (zero == parts.npos) break;
                                parts.remove_prefix(zero + 1);
                            }
                            result["tags"][tag->second] = std::move(values);
                        }
                    }
                }
                frames.remove_prefix(*length);
                remaining -= *length;
            }
            if (footer) end += 10;
            result["id3v2_bytes"] = end;
        }
    }
    auto observation = mp3_prefix_technical(data, end, file_size);
    if (observation["state"] == "ok") result["metadata"] = std::move(observation["metadata"]);
    else if (!result.empty() && observation["state"] == "no_frame") result["prefix_status"] = "audio frame unavailable in probe";
    return result.empty() ? Json() : result;
}
}

inline nlohmann::json catalog_audio_prefix(std::string_view data, std::string_view suffix, std::uint64_t file_size) {
    using audio_probe::Json;
    Json result;
    if (data.starts_with("fLaC")) result = audio_probe::flac(data);
    else if (data.starts_with("RIFF")) result = wave_prefix(data, file_size);
    else if (data.starts_with("OggS")) result = audio_probe::ogg(data);
    else if (suffix == ".mp3") result = audio_probe::mp3(data, file_size);
    if (result.is_null()) return {{"prefix_status", "no supported audio header in probe"}};
    if (auto metadata = result.find("metadata"); metadata != result.end()) {
        const auto duration = metadata->find("duration_seconds");
        if (duration != metadata->end() && duration->is_number() && duration->get<double>() > 0) {
            const auto bitrate = std::nearbyint(double(file_size) * 8.0 / duration->get<double>());
            if (bitrate < double(std::numeric_limits<std::uint64_t>::max()))
                (*metadata)["file_average_bitrate_bps"] = static_cast<std::uint64_t>(bitrate);
        }
    }
    return result;
}
}
