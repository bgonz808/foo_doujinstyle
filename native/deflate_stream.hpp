#pragma once

#include "descriptor.hpp"
#define Z_SOLO  // Only the stream ABI, independent of the build host's gzip-file declarations.
#include "zlib/zlib.h"
#undef Z_SOLO
#include <stdexcept>
#include <vector>

namespace doujin {
struct DeflateCheckpoint {
    std::uint64_t input, output;
    std::uint32_t prefix_crc;
    unsigned char bit_count, bit_value;
    std::vector<unsigned char> dictionary;
};

inline constexpr std::size_t deflate_window = 32768;

inline bool valid_checkpoint(const DeflateCheckpoint& point, const ZipMember& entry) {
    return point.input > 0 && point.input < entry.packed_size && point.output > 0 && point.output < entry.size &&
        point.bit_count <= 7 && point.bit_value < (1u << point.bit_count) &&
        point.dictionary.size() == (point.output < deflate_window ? point.output : deflate_window);
}

struct DeflateApi {
    decltype(&inflateInit2_) init;
    decltype(&inflate) inflate_chunk;
    decltype(&inflateEnd) end;
    decltype(&crc32) checksum;
    decltype(&inflatePrime) prime = nullptr;
    decltype(&inflateSetDictionary) set_dictionary = nullptr;
    decltype(&inflateGetDictionary) get_dictionary = nullptr;

    bool supports_checkpoints() const { return prime && set_dictionary && get_dictionary; }
};

class DeflateStream {
public:
    struct Chunk { std::size_t consumed, produced; };

    DeflateStream(const DeflateApi& api, ZipMember entry, const DeflateCheckpoint* start = nullptr) :
        api_(api), entry_(entry) {
        if (start && (!api_.supports_checkpoints() || !valid_checkpoint(*start, entry_)))
            throw std::runtime_error("Invalid ZIP DEFLATE checkpoint");
        if (api_.init(&state_, -MAX_WBITS, ZLIB_VERSION, sizeof state_) != Z_OK)
            throw std::runtime_error("Could not initialize ZIP DEFLATE stream");
        if (start) {
            if (api_.prime(&state_, start->bit_count, start->bit_value) != Z_OK ||
                api_.set_dictionary(&state_, start->dictionary.data(), static_cast<uInt>(start->dictionary.size())) != Z_OK) {
                api_.end(&state_);
                throw std::runtime_error("Could not restore ZIP DEFLATE checkpoint");
            }
            input_bytes_ = start->input;
            output_bytes_ = start->output;
            checksum_ = start->prefix_crc;
            last_input_byte_ = static_cast<unsigned char>(start->bit_value << (8 - start->bit_count));
        }
    }
    ~DeflateStream() { api_.end(&state_); }
    DeflateStream(const DeflateStream&) = delete;
    DeflateStream& operator=(const DeflateStream&) = delete;

    Chunk step(std::span<const unsigned char> input, std::span<unsigned char> output, bool learn = false) {
        state_.next_in = const_cast<Bytef*>(input.data());
        state_.avail_in = static_cast<uInt>(input.size());
        state_.next_out = output.data();
        state_.avail_out = static_cast<uInt>(output.size());
        const auto status = api_.inflate_chunk(&state_, learn ? Z_BLOCK : Z_NO_FLUSH);
        const Chunk chunk{input.size() - state_.avail_in, output.size() - state_.avail_out};
        if (chunk.consumed) last_input_byte_ = input[chunk.consumed - 1];
        input_bytes_ += chunk.consumed;
        output_bytes_ += chunk.produced;
        if (input_bytes_ > entry_.packed_size || output_bytes_ > entry_.size)
            throw std::runtime_error("ZIP DEFLATE member exceeded its declared size");
        if (chunk.produced)
            checksum_ = api_.checksum(checksum_, output.data(), static_cast<uInt>(chunk.produced));
        if (status == Z_STREAM_END) {
            if (input_bytes_ != entry_.packed_size || output_bytes_ != entry_.size || checksum_ != entry_.crc)
                throw std::runtime_error("ZIP DEFLATE member failed length or CRC validation");
            finished_ = true;
        } else if (status != Z_OK || (!chunk.consumed && !chunk.produced && !(learn && (state_.data_type & 128)))) {
            throw std::runtime_error("Invalid or truncated ZIP DEFLATE stream");
        }
        return chunk;
    }

    bool finished() const { return finished_; }

    std::optional<DeflateCheckpoint> checkpoint(std::uint64_t minimum_output) {
        if (!api_.supports_checkpoints() || finished_ || (state_.data_type & 192) != 128 ||
            output_bytes_ < minimum_output || input_bytes_ >= entry_.packed_size || output_bytes_ >= entry_.size)
            return {};
        const auto bits = static_cast<unsigned char>(state_.data_type & 7);
        DeflateCheckpoint point{input_bytes_, output_bytes_, static_cast<std::uint32_t>(checksum_), bits,
                               static_cast<unsigned char>(last_input_byte_ >> (8 - bits)),
                               std::vector<unsigned char>(deflate_window)};
        uInt length = 0;
        if (api_.get_dictionary(&state_, point.dictionary.data(), &length) != Z_OK || length > point.dictionary.size())
            return {};
        point.dictionary.resize(length);
        if (!valid_checkpoint(point, entry_)) return {};
        return point;
    }

private:
    DeflateApi api_;
    ZipMember entry_;
    z_stream state_{};
    std::uint64_t input_bytes_ = 0, output_bytes_ = 0;
    uLong checksum_ = 0;
    unsigned char last_input_byte_ = 0;
    bool finished_ = false;
};
}
