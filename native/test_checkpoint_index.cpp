#include "checkpoint_index.hpp"
#include "archive_range_policy.hpp"
#include "archive_playback_stats.hpp"
#include <array>
#include <cassert>
#include <chrono>
#include <iostream>
#ifdef DOUJIN_SEEK_TEST_HTTP
#include <curl/curl.h>
#endif

namespace {
constexpr std::uint64_t mib = 1024 * 1024;
using Bytes = std::vector<unsigned char>;

void test_policy(const std::filesystem::path& folder, const doujin::DeflateApi& api) {
    assert(doujin::normalize_checkpoint_mib(4) == 4);
    assert(doujin::normalize_checkpoint_mib(9) == 16);
    const doujin::ZipMember entry{0, 32 * mib, 40 * mib, 0, 8};
    const auto point = [](std::uint64_t at) {
        return doujin::DeflateCheckpoint{at / 2, at, 0, 0, 0, Bytes(doujin::deflate_window)};
    };
    doujin::CheckpointIndex fine(entry, api, folder, "-test", nullptr, 4 * mib);
    fine.apply_retention();
    for (auto at = 4 * mib; at < entry.size; at += 4 * mib) fine.upsert(point(at));
    assert(fine.next_missing_after(0) == entry.size);
    {
        doujin::CheckpointIndex coarse(entry, api, folder, "-test", nullptr, 16 * mib);
        coarse.apply_retention();
        assert(std::filesystem::exists(folder / "8.cp") && std::filesystem::exists(folder / "16.cp"));
        assert(!std::filesystem::exists(folder / "2.cp") && !std::filesystem::exists(folder / "18.cp"));
        assert(coarse.at_or_before(31 * mib)->output == 16 * mib);
        assert(coarse.next_missing_after(0) == entry.size);
    }
    doujin::CheckpointIndex expanded(entry, api, folder, "-test", nullptr, 4 * mib);
    expanded.apply_retention();
    assert(expanded.next_missing_after(0) == 4 * mib);
    assert(expanded.at_or_before(20 * mib)->output == 16 * mib);
    assert(expanded.next_missing_after(12 * mib) == 20 * mib);
    expanded.upsert(point(20 * mib));
    assert(expanded.next_missing_after(12 * mib) == 24 * mib);
    std::filesystem::remove(folder / "10.cp");
    assert(expanded.have_saved_at(20 * mib));
    assert(std::filesystem::file_size(folder / "10.cp") == 32802);

    doujin::ArchivePlaybackStats stats;
    assert(!stats.start("first", false, 100));
    assert(stats.start("first", false, 50));
    assert(stats.start("after-restart", true, 30));
    stats.seek(80, true, true, true, 123456);
    stats.seek(180, true, false, false, 0);
    stats.seek(10, false, false, false, 0);
    const auto lines = stats.report();
    assert(lines[2].find("mean 40 ms") != std::string::npos);
    assert(lines[3].find("1/2 (50%)") != std::string::npos);
    assert(lines.back().ends_with("123456"));
}

#ifdef DOUJIN_SEEK_TEST_HTTP
class Loopback {
public:
    explicit Loopback(std::string port) : address_("http://127.0.0.1:" + port) {
        assert(!port.empty() && port.find_first_not_of("0123456789") == std::string::npos);
        assert(curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK);
        client_ = curl_easy_init();
        assert(client_);
        assert(curl_easy_setopt(client_, CURLOPT_PROXY, "") == CURLE_OK);
        assert(curl_easy_setopt(client_, CURLOPT_TIMEOUT, 10L) == CURLE_OK);
        assert(curl_easy_setopt(client_, CURLOPT_WRITEFUNCTION, &receive) == CURLE_OK);
    }
    ~Loopback() {
        curl_easy_cleanup(client_);
        curl_global_cleanup();
    }
    Loopback(const Loopback&) = delete;
    Loopback& operator=(const Loopback&) = delete;

    Bytes get(const char* path, std::uint64_t offset = 0, std::uint64_t size = 0) const {
        const auto url = address_ + path;
        auto range = size ? std::to_string(offset) + "-" + std::to_string(offset + size - 1) : std::string{};
        Bytes response;
        assert(curl_easy_setopt(client_, CURLOPT_URL, url.c_str()) == CURLE_OK);
        assert(curl_easy_setopt(client_, CURLOPT_RANGE, size ? range.c_str() : nullptr) == CURLE_OK);
        assert(curl_easy_setopt(client_, CURLOPT_WRITEDATA, &response) == CURLE_OK);
        const auto result = curl_easy_perform(client_);
        if (result != CURLE_OK) throw std::runtime_error(curl_easy_strerror(result));
        long status = 0;
        assert(curl_easy_getinfo(client_, CURLINFO_RESPONSE_CODE, &status) == CURLE_OK);
        assert(status == (size ? 206 : 200) && (!size || response.size() == size));
        return response;
    }

private:
    static std::size_t receive(char* data, std::size_t size, std::size_t count, void* userdata) noexcept {
        auto& bytes = *static_cast<Bytes*>(userdata);
        try {
            bytes.insert(bytes.end(), data, data + size * count);
            return size * count;
        } catch (...) {
            return 0;
        }
    }
    const std::string address_;
    CURL* client_ = nullptr;
};

doujin::ZipMember find_member(std::span<const unsigned char> archive) {
    const auto entry = doujin::find_zip_member(archive.size(), "disc/Remote tone.wav", [&](auto offset, auto bytes) {
        const auto source = archive.subspan(offset, bytes.size());
        std::copy(source.begin(), source.end(), bytes.begin());
    });
    assert(entry);
    return *entry;
}
#endif

void test_budget(const std::filesystem::path& root, doujin::ZipMember entry, const doujin::DeflateApi& api,
                 const std::filesystem::path& fixture) {
    const auto first = root / std::string(64, 'a');
    const auto second = root / std::string(64, 'b');
    const auto unrelated = root / "unrelated";
    for (const auto& dir : {first, unrelated}) std::filesystem::create_directories(dir);
    for (const auto& name : {"1.cp", "2.cp"})
        assert(std::filesystem::copy_file(fixture / name, first / name, std::filesystem::copy_options::overwrite_existing));
    std::ofstream(unrelated / "1.cp") << "unrelated file";
    const auto now = std::filesystem::file_time_type::clock::now();
    std::filesystem::last_write_time(first / "1.cp", now - std::chrono::hours(2));
    std::filesystem::last_write_time(first / "2.cp", now - std::chrono::hours(1));
    const auto bytes = std::filesystem::file_size(first / "1.cp");
    doujin::CheckpointCacheBudget budget(2 * bytes);
    doujin::CheckpointIndex old(entry, api, fixture);
    const auto* point = old.at_or_before(3 * mib);
    assert(point);
    doujin::CheckpointIndex writer(entry, api, second, "-test", &budget);
    writer.upsert(*point);
    assert(!std::filesystem::exists(first / "1.cp"));
    assert(std::filesystem::exists(first / "2.cp") && std::filesystem::exists(unrelated / "1.cp"));
    assert(std::filesystem::file_size(second / "1.cp") == bytes);
    doujin::CheckpointIndex restarted(entry, api, second);
    assert(restarted.at_or_before(3 * mib)->output == point->output);

    doujin::CheckpointCacheBudget too_small(bytes - 1);
    const auto disabled = root / std::string(64, 'c');
    doujin::CheckpointIndex memory_only(entry, api, disabled, "-test", &too_small);
    memory_only.upsert(*point);
    assert(memory_only.at_or_before(3 * mib)->output == point->output);
    assert(!std::filesystem::exists(disabled));

    const auto sparse = root / std::string(64, 'd');
    doujin::CheckpointIndex sparse_writer(entry, api, sparse, "-test", nullptr, 4 * mib);
    const auto* later = old.at_or_before(5 * mib);
    assert(later);
    sparse_writer.upsert(*point);
    sparse_writer.upsert(*later);
    assert(sparse_writer.at_or_before(3 * mib)->output == point->output);
    assert(!std::filesystem::exists(sparse / "1.cp") && std::filesystem::exists(sparse / "2.cp"));
    doujin::CheckpointIndex sparse_restart(entry, api, sparse, "-test", nullptr, 4 * mib);
    assert(!sparse_restart.at_or_before(3 * mib));
    assert(sparse_restart.at_or_before(5 * mib)->output == later->output);
    std::filesystem::copy_file(fixture / "1.cp", sparse / "1.cp");
    std::filesystem::last_write_time(sparse / "1.cp", now - std::chrono::hours(2));
    doujin::CheckpointIndex legacy_restart(entry, api, sparse, "-test", nullptr, 4 * mib);
    assert(legacy_restart.at_or_before(3 * mib)->output == point->output);
    assert(std::filesystem::last_write_time(sparse / "1.cp") > now - std::chrono::hours(1));
    std::cout << "checkpoint budget passed, including disk eviction and memory-only recovery\n";
}

struct SeekResult {
    std::uint64_t fetched, ranges, microseconds;
};

SeekResult seek(std::span<const unsigned char> audio, doujin::ZipMember entry, const doujin::DeflateApi& api,
                const std::filesystem::path& folder, std::uint64_t target, std::uint64_t range_limit,
                const auto& fetch) {
    const auto started = std::chrono::steady_clock::now();
    doujin::CheckpointIndex index(entry, api, folder);
    const auto* point = index.at_or_before(target);
    doujin::DeflateStream stream(api, entry, point);
    auto input = point ? point->input : 0;
    const auto first_input = input;
    auto fetched = input;
    auto output = point ? point->output : 0;
    const auto end = target + 16384;
    std::uint64_t ranges = 0;
    std::array<unsigned char, 64 * 1024> buffer{};
    std::span<const unsigned char> range;
    while (output < end) {
        if (input == fetched) {
            const auto window = range_limit == doujin::archive_range_window || range_limit == doujin::coalesced_range_window
                ? doujin::deflate_input_window(end - output, range_limit == doujin::coalesced_range_window, false)
                : std::clamp<std::uint64_t>(end - output, doujin::deflate_startup_window, range_limit);
            const auto length = std::min<std::uint64_t>(window, entry.packed_size - fetched);
            assert(length);
            range = fetch(fetched, length);
            assert(range.size() == length);
            fetched += range.size();
            ++ranges;
        }
        const auto chunk = stream.step(range, buffer);
        range = range.subspan(chunk.consumed);
        assert(chunk.consumed || chunk.produced);
        const auto after = output + chunk.produced;
        if (after > target && output < end) {
            const auto first = std::max(output, target);
            assert(std::equal(buffer.data() + (first - output), buffer.data() + (std::min(after, end) - output),
                              audio.data() + first));
        }
        input += chunk.consumed;
        output = after;
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();
    return {fetched - first_input, ranges, static_cast<std::uint64_t>(elapsed)};
}
}

int main(int argc, char** argv) {
    bool http = false;
#ifdef DOUJIN_SEEK_TEST_HTTP
    http = argc == 4 && std::string_view(argv[2]) == "--http-benchmark";
#endif
    const bool benchmark = http || (argc == 3 && std::string_view(argv[2]) == "--benchmark");
    if (argc != 2 && !benchmark) return 1;
    const std::filesystem::path location(argv[1]);
    Bytes audio, packed;
    doujin::ZipMember entry{};
#ifdef DOUJIN_SEEK_TEST_HTTP
    std::optional<Loopback> loopback;
    if (http) {
        loopback.emplace(argv[3]);
        audio = loopback->get("/direct.wav");
        entry = find_member(loopback->get("/deflated.zip"));
        assert(entry.method == 8 && entry.size == audio.size());
        packed = loopback->get("/deflated.zip", entry.offset, entry.packed_size);
    }
#endif
    if (!http) {
        audio.resize(benchmark ? 40 * mib : 7 * mib);
        std::uint32_t state = 17;
        for (std::size_t i = 0; i < audio.size(); ++i) {
            state = state * 1664525u + 1013904223u;
            audio[i] = !benchmark && i >= 8192 && i % 101 != 0 ? audio[i - 8192] : static_cast<unsigned char>(state >> 24);
        }
        z_stream encoder{};
        assert(deflateInit2(&encoder, 6, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY) == Z_OK);
        packed.resize(audio.size() + 100000);
        encoder.next_out = packed.data();
        encoder.avail_out = static_cast<uInt>(packed.size());
        for (std::size_t input = 0; input < audio.size();) {
            const auto count = std::min<std::size_t>(71999, audio.size() - input);
            encoder.next_in = audio.data() + input;
            encoder.avail_in = static_cast<uInt>(count);
            input += count;
            const auto last = input == audio.size();
            assert(deflate(&encoder, last ? Z_FINISH : Z_BLOCK) == (last ? Z_STREAM_END : Z_OK));
            assert(encoder.avail_in == 0);
        }
        packed.resize(encoder.total_out);
        deflateEnd(&encoder);
        entry = {0, static_cast<std::uint32_t>(packed.size()), static_cast<std::uint32_t>(audio.size()),
                 static_cast<std::uint32_t>(crc32(0, audio.data(), audio.size())), 8};
    }

    const doujin::DeflateApi api{inflateInit2_, inflate, inflateEnd, crc32, inflatePrime,
                                inflateSetDictionary, inflateGetDictionary};
    auto decode = [&](const doujin::DeflateCheckpoint* restore, doujin::CheckpointIndex* index) {
        doujin::DeflateStream stream(api, entry, restore);
        auto in = restore ? restore->input : 0;
        auto out = restore ? restore->output : 0;
        auto expected_checkpoint = index ? index->next_after(out) : 0;
        bool learning = false;
        std::array<unsigned char, 8192> buffer{};
        while (!stream.finished()) {
            const auto input = std::span(packed).subspan(in, std::min<std::uint64_t>(3011, packed.size() - in));
            if (index && out + buffer.size() >= expected_checkpoint) learning = true;
            const auto chunk = stream.step(input, buffer, learning);
            assert(std::equal(buffer.begin(), buffer.begin() + chunk.produced, audio.begin() + out));
            in += chunk.consumed;
            out += chunk.produced;
            if (learning) {
                if (auto point = stream.checkpoint(expected_checkpoint)) {
                    expected_checkpoint = index->next_after(point->output);
                    index->upsert(std::move(*point));
                    learning = false;
                }
            }
        }
        assert(in == packed.size() && out == audio.size());
    };

    {
        doujin::CheckpointIndex writer(entry, api, location, "-test");
        decode(nullptr, &writer);
        assert(!writer.at_or_before(100));
    }
    doujin::CheckpointIndex restarted(entry, api, location, "-test");
    if (!benchmark) test_policy(location / "elastic", api);
    if (benchmark) {
        std::cout << "target_bytes,nominal_spacing_mib,checkpoint_bytes,max_range_kib,fetched_bytes,ranges,median_us\n";
#ifdef DOUJIN_SEEK_TEST_HTTP
        Bytes response;
        if (http) {
            const auto stored = find_member(loopback->get("/stored.zip"));
            assert(stored.method == 0 && stored.size == audio.size());
            constexpr std::uint64_t target = 39 * mib + mib / 2;
            std::array<std::int64_t, 3> times{};
            for (auto& time : times) {
                const auto started = std::chrono::steady_clock::now();
                const auto slice = loopback->get("/stored.zip", stored.offset + target, 16384);
                assert(std::equal(slice.begin(), slice.end(), audio.begin() + target));
                time = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - started).count();
            }
            std::sort(times.begin(), times.end());
            std::cout << target << ",STORE,0,16,16384,1," << times[1] << '\n';
        }
#endif
        auto fetch = [&](std::uint64_t offset, std::uint64_t size) -> std::span<const unsigned char> {
#ifdef DOUJIN_SEEK_TEST_HTTP
            if (http) {
                response = loopback->get("/deflated.zip", entry.offset + offset, size);
                return response;
            }
#endif
            return std::span(packed).subspan(offset, size);
        };
        auto measure = [&](std::uint64_t target, const std::filesystem::path& folder, unsigned spacing,
                           std::uintmax_t disk_bytes, std::uint64_t range_limit) {
            std::vector<std::uint64_t> times(http ? 3 : 5);
            SeekResult result{};
            for (auto& time : times) {
                result = seek(audio, entry, api, folder, target, range_limit, fetch);
                time = result.microseconds;
            }
            std::sort(times.begin(), times.end());
            std::cout << target << ',' << spacing << ',' << disk_bytes << ',' << range_limit / 1024 << ','
                      << result.fetched << ',' << result.ranges << ',' << times[times.size() / 2] << '\n';
            return result;
        };
        for (const auto spacing : {0u, 2u, 4u, 8u, 16u}) {
            std::filesystem::path folder;
            std::uintmax_t disk_bytes = 0;
            if (spacing) {
                folder = location / std::to_string(spacing);
                std::filesystem::create_directory(folder);
                const auto stride = spacing / 2;
                for (auto slot = stride; slot <= (audio.size() - 1) / (2 * mib); slot += stride) {
                    const auto name = std::to_string(slot) + ".cp";
                    assert(std::filesystem::copy_file(location / name, folder / name,
                                                      std::filesystem::copy_options::overwrite_existing));
                    disk_bytes += std::filesystem::file_size(folder / name);
                }
            }
            const std::array targets{12 * mib + mib / 4, 31 * mib + mib / 2, 39 * mib + mib / 2};
            for (const auto target : targets) {
                const auto result = measure(target, folder, spacing, disk_bytes, mib);
                assert(spacing > 8 || spacing == 0 || result.fetched < (spacing + 1) * mib);
            }
            if (http && (spacing == 0 || spacing == 4)) {
                for (const auto limit : {mib / 4, 2 * mib, 4 * mib})
                    measure(targets.back(), folder, spacing, disk_bytes, limit);
            }
        }
        return 0;
    }
    test_budget(location / "budget", entry, api, location);
    unsigned unaligned = 0;
    for (auto target : {3u, 5u, 7u}) {
        const auto* point = restarted.at_or_before(target * 1024 * 1024);
        assert(point && point->output <= target * 1024 * 1024);
        unaligned += point->bit_count != 0;
        decode(point, nullptr);
        std::cout << "restored at " << point->output << " of " << audio.size()
                  << " bytes, bit alignment " << unsigned(point->bit_count) << '\n';
    }
    assert(unaligned > 0);
    std::ofstream(location / "3.cp", std::ios::binary | std::ios::trunc) << "incomplete record";
    doujin::CheckpointIndex damaged(entry, api, location, "-test");
    const auto* previous = damaged.at_or_before(audio.size());
    assert(previous && previous->output < 6 * 1024 * 1024);
    decode(previous, nullptr);
}
