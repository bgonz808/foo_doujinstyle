#pragma once

#include "checkpoint_cache_budget.hpp"
#include "deflate_stream.hpp"
#include <algorithm>
#include <bit>
#include <filesystem>
#include <fstream>
#include <map>

namespace doujin {
inline constexpr std::uint64_t checkpoint_mib = 1024 * 1024;

inline std::uint64_t normalize_checkpoint_mib(std::uint64_t spacing) {
    return std::bit_ceil(std::clamp<std::uint64_t>(spacing, 2, 32));
}

class CheckpointIndex {
public:
    CheckpointIndex(ZipMember entry, DeflateApi api, std::filesystem::path folder = {}, std::string temp_suffix = {},
                    CheckpointCacheBudget* budget = nullptr, std::uint64_t minimum_disk_spacing = 0) :
        entry_(entry), api_(api), folder_(std::move(folder)), temp_suffix_(std::move(temp_suffix)),
        spacing_(std::max<std::uint64_t>(2 * 1024 * 1024, (std::uint64_t(entry_.size) + 127) / 128)),
        disk_stride_(std::bit_ceil(std::max<std::uint64_t>(1, (minimum_disk_spacing + spacing_ - 1) / spacing_))),
        budget_(budget) {}

    std::uint64_t next_after(std::uint64_t offset) const { return (offset / spacing_ + 1) * spacing_; }
    std::uint64_t disk_spacing() const { return spacing_ * disk_stride_; }
    bool can_persist() const { return !folder_.empty(); }

    void apply_retention() {
        if (retention_applied_ || folder_.empty()) return;
        retention_applied_ = true;
        const auto trim = [&] {
            std::error_code error;
            for (std::filesystem::directory_iterator it(folder_, error), end; !error && it != end; it.increment(error)) {
                const auto slot = checkpoint_slot(it->path().filename().native());
                if (slot && slot % disk_stride_ != 0 && std::filesystem::is_regular_file(it->symlink_status(error))) {
                    std::error_code removal_error;
                    std::filesystem::remove(it->path(), removal_error);
                }
            }
        };
        if (budget_) budget_->mutate(trim); else trim();
    }

    bool have_saved_at(std::uint64_t mark) {
        if (folder_.empty()) return false;
        const auto slot = mark / spacing_;
        if (load(slot)) return true;
        const auto point = points_.find(slot);
        if (point == points_.end() || !point->second) return false;
        save(slot, *point->second);
        return load(slot).has_value();
    }

    std::uint64_t next_missing_after(std::uint64_t mark) {
        if (!can_persist()) return entry_.size;
        const auto step = disk_spacing();
        for (auto candidate = (mark / step + 1) * step; candidate < entry_.size; candidate += step)
            if (!have_saved_at(candidate)) return candidate;
        return entry_.size;
    }

    const DeflateCheckpoint* at_or_before(std::uint64_t target) {
        load_slots();
        auto it = points_.upper_bound(target / spacing_);
        while (it != points_.begin()) {
            --it;
            if (!it->second) {
                it->second = load(it->first);
                if (it->second && it->second->output <= target) {
                    std::error_code error;
                    std::filesystem::last_write_time(filename(it->first), std::filesystem::file_time_type::clock::now(), error);
                }
            }
            if (it->second && it->second->output <= target) return &*it->second;
        }
        return nullptr;
    }

    void upsert(DeflateCheckpoint point) {
        if (!valid_checkpoint(point, entry_) || point.output < spacing_) return;
        load_slots();
        const auto slot = point.output / spacing_;
        auto& current = points_[slot];
        if (!current) current = load(slot);
        if (current && current->output <= point.output) return;
        current = std::move(point);
        save(slot, *current);
    }

private:
    using Bytes = std::vector<unsigned char>;
    static constexpr std::string_view magic = "DJTZCP01";
    static constexpr std::size_t fixed = 8 + 8 + 8 + 4 + 1 + 1 + 4;

    static void append(Bytes& bytes, std::uint64_t value, unsigned length) {
        for (unsigned i = 0; i < length; ++i) bytes.push_back(static_cast<unsigned char>(value >> (8 * i)));
    }

    static std::uint64_t read(const Bytes& bytes, std::size_t offset, unsigned length) {
        std::uint64_t result = 0;
        for (unsigned i = 0; i < length; ++i) result |= std::uint64_t(bytes[offset + i]) << (8 * i);
        return result;
    }

    std::filesystem::path filename(std::uint64_t slot) const { return folder_ / (std::to_string(slot) + ".cp"); }

    void load_slots() {
        if (loaded_) return;
        loaded_ = true;
        if (folder_.empty() || entry_.size == 0) return;
        std::error_code error;
        for (std::filesystem::directory_iterator it(folder_, error), end; !error && it != end; it.increment(error)) {
            const auto slot = checkpoint_slot(it->path().filename().native());
            if (slot && slot <= (entry_.size - 1) / spacing_) points_.try_emplace(slot);
        }
    }

    std::optional<DeflateCheckpoint> load(std::uint64_t slot) const {
        if (folder_.empty()) return {};
        std::ifstream file(filename(slot), std::ios::binary | std::ios::ate);
        const auto length = file.tellg();
        if (!file || length < static_cast<std::streamoff>(fixed) ||
            length > static_cast<std::streamoff>(fixed + deflate_window)) return {};
        Bytes bytes(static_cast<std::size_t>(length));
        file.seekg(0);
        if (!file.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size())) ||
            !std::equal(magic.begin(), magic.end(), bytes.begin()) ||
            api_.checksum(0, bytes.data(), static_cast<uInt>(bytes.size() - 4)) != read(bytes, bytes.size() - 4, 4))
            return {};
        DeflateCheckpoint point{read(bytes, 8, 8), read(bytes, 16, 8), static_cast<std::uint32_t>(read(bytes, 24, 4)),
                                bytes[28], bytes[29], Bytes(bytes.begin() + 30, bytes.end() - 4)};
        if (!valid_checkpoint(point, entry_) || point.output / spacing_ != slot) return {};
        return point;
    }

    void save(std::uint64_t slot, const DeflateCheckpoint& point) const {
        if (folder_.empty() || slot % disk_stride_ != 0) return;
        if (budget_) {
            budget_->write(folder_.parent_path(), fixed + point.dictionary.size(), [&] { save_file(slot, point); });
        } else {
            save_file(slot, point);
        }
    }

    void save_file(std::uint64_t slot, const DeflateCheckpoint& point) const {
        std::error_code error;
        std::filesystem::create_directories(folder_, error);
        if (error) return;
        Bytes bytes(magic.begin(), magic.end());
        append(bytes, point.input, 8);
        append(bytes, point.output, 8);
        append(bytes, point.prefix_crc, 4);
        bytes.push_back(point.bit_count);
        bytes.push_back(point.bit_value);
        bytes.insert(bytes.end(), point.dictionary.begin(), point.dictionary.end());
        append(bytes, api_.checksum(0, bytes.data(), static_cast<uInt>(bytes.size())), 4);
        auto temp = filename(slot);
        temp += temp_suffix_ + ".tmp";
        std::ofstream file(temp, std::ios::binary | std::ios::trunc);
        file.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
        file.close();
        if (file) std::filesystem::rename(temp, filename(slot), error);
        if (error || !file) std::filesystem::remove(temp, error);
    }

    ZipMember entry_;
    DeflateApi api_;
    std::filesystem::path folder_;
    std::string temp_suffix_;
    std::uint64_t spacing_;
    std::uint64_t disk_stride_;
    CheckpointCacheBudget* budget_;
    bool loaded_ = false;
    bool retention_applied_ = false;
    std::map<std::uint64_t, std::optional<DeflateCheckpoint>> points_;
};
}
