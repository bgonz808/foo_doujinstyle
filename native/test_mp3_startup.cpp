#include "decoder_io_budget.hpp"
#include <cassert>

int main() {
    using doujin::DecoderIoBudget;
    constexpr auto start = DecoderIoBudget::byte_limit;
    constexpr auto compressed = DecoderIoBudget::compressed_limit;

    DecoderIoBudget tail_seek;
    assert(tail_seek.allow_read(0, 4096));
    assert(!tail_seek.allow_seek(800 * 1024 * 1024));
    assert(tail_seek.rejected());
    assert(!tail_seek.allow_read(0, 4096));

    DecoderIoBudget sequential;
    assert(sequential.allow_read(0, start - 1024));
    assert(sequential.allow_read(start - 1024, 1024));
    assert(!sequential.allow_read(0, 1));

    DecoderIoBudget compressed_input;
    compressed_input.consumed_compressed(compressed - 1);
    assert(compressed_input.compressed_available() == 1);
    compressed_input.consumed_compressed(1);
    assert(compressed_input.compressed_available() == 0);
    compressed_input.reject();
    assert(compressed_input.rejected());

    DecoderIoBudget playback;
    assert(playback.allow_seek(start - 4096));
    assert(playback.allow_read(start - 4096, 4096));
    playback.finish();
    assert(playback.allow_seek(800 * 1024 * 1024));
    assert(playback.allow_read(800 * 1024 * 1024, start * 2));
    assert(playback.compressed_available() == UINT64_MAX);

    // Sparse head/tail metadata reader (e.g. a tail-moov MP4). The old
    // position ceiling rejects this access even when the tail is inexpensive.
    // This tests policy only, not a real decoder or actual archive cache hit.
    constexpr std::uint64_t tail = 100ull * 1024 * 1024 - 4096;
    DecoderIoBudget legacy_policy;
    assert(!legacy_policy.allow_seek(tail));
    DecoderIoBudget sparse({UINT64_MAX, 8192, 128 * 1024});
    assert(sparse.allow_read(0, 4096));
    assert(sparse.allow_seek(tail));
    assert(sparse.allow_read(tail, 4096));
    sparse.consumed_compressed(4096);
    assert(sparse.compressed_available() == 124 * 1024);
    assert(!sparse.allow_read(0, 1));
    assert(sparse.rejected());

    // A read ending beyond the representable position must not wrap.
    DecoderIoBudget overflow({UINT64_MAX, 8192, 128 * 1024});
    assert(!overflow.allow_read(UINT64_MAX - 1, 2));
    DecoderIoBudget overcharge({UINT64_MAX, 8192, 1});
    overcharge.consumed_compressed(2);
    assert(overcharge.compressed_available() == 0);
    assert(overcharge.rejected());
    assert(!overcharge.allow_seek(0));
}
