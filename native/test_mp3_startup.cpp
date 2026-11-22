#include "mp3_startup.hpp"
#include <cassert>

int main() {
    using doujin::Mp3StartupBudget;
    constexpr auto start = Mp3StartupBudget::byte_limit;
    constexpr auto compressed = Mp3StartupBudget::compressed_limit;

    Mp3StartupBudget tail_seek;
    assert(tail_seek.allow_read(0, 4096));
    assert(!tail_seek.allow_seek(800 * 1024 * 1024));
    assert(tail_seek.rejected());
    assert(!tail_seek.allow_read(0, 4096));

    Mp3StartupBudget sequential;
    assert(sequential.allow_read(0, start - 1024));
    assert(sequential.allow_read(start - 1024, 1024));
    assert(!sequential.allow_read(0, 1));

    Mp3StartupBudget compressed_input;
    compressed_input.consumed_compressed(compressed - 1);
    assert(compressed_input.compressed_available() == 1);
    compressed_input.consumed_compressed(1);
    assert(compressed_input.compressed_available() == 0);
    compressed_input.reject();
    assert(compressed_input.rejected());

    Mp3StartupBudget playback;
    assert(playback.allow_seek(start - 4096));
    assert(playback.allow_read(start - 4096, 4096));
    playback.finish();
    assert(playback.allow_seek(800 * 1024 * 1024));
    assert(playback.allow_read(800 * 1024 * 1024, start * 2));
    assert(playback.compressed_available() == UINT64_MAX);
}
