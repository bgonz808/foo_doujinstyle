#pragma once

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <string_view>
#include <vector>

namespace doujin {
inline unsigned checkpoint_slot(std::filesystem::path::string_type const& name) {
    if (name.size() < 4 || name.size() > 6 || name[name.size() - 3] != '.' ||
        name[name.size() - 2] != 'c' || name.back() != 'p' || name.front() == '0') return 0;
    unsigned slot = 0;
    for (std::size_t index = 0; index + 3 < name.size(); ++index) {
        const auto digit = name[index];
        if (digit < '0' || digit > '9') return 0;
        slot = slot * 10 + static_cast<unsigned>(digit - '0');
    }
    return slot < 128 ? slot : 0;
}

class CheckpointCacheBudget {
public:
    explicit CheckpointCacheBudget(std::uintmax_t limit) : limit_(limit) {}

    void mutate(const auto& edit) {
        std::lock_guard lock(mutex_);
        edit();
    }

    template<typename Write>
    void write(const std::filesystem::path& root, std::uintmax_t bytes, Write save) {
        std::lock_guard lock(mutex_);
        if (bytes > limit_) return;
        if (root_ != root || remaining_ < bytes) {
            root_ = root;
            remaining_ = reserve(root, bytes);
        }
        if (remaining_ < bytes) return;
        remaining_ -= bytes;
        save();
    }

private:
    struct Record {
        std::filesystem::path path;
        std::filesystem::file_time_type written;
        std::uintmax_t bytes;
    };

    static bool member_directory(const std::filesystem::path& path) {
        const auto name = path.filename().native();
        return name.size() == 64 && std::all_of(name.begin(), name.end(), [](auto digit) {
            return (digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f');
        });
    }

    std::uintmax_t reserve(const std::filesystem::path& root, std::uintmax_t bytes) const {
        // Amortize directory scans while reserving space before writing to disk.
        const auto batch = std::max(bytes, std::min<std::uintmax_t>(2 * 1024 * 1024, limit_ / 8));
        std::error_code error;
        const auto status = std::filesystem::symlink_status(root, error);
        if (status.type() == std::filesystem::file_type::not_found) return batch;
        if (error || !std::filesystem::is_directory(status)) return 0;

        std::vector<Record> records;
        std::uintmax_t used = 0;
        const std::filesystem::directory_iterator end;
        for (std::filesystem::directory_iterator member(root, error); !error && member != end; member.increment(error)) {
            const auto& directory = *member;
            if (!member_directory(directory.path()) || !std::filesystem::is_directory(directory.symlink_status(error)))
                continue;
            for (std::filesystem::directory_iterator file(directory.path(), error); !error && file != end;
                 file.increment(error)) {
                const auto& entry = *file;
                if (!checkpoint_slot(entry.path().filename().native()) ||
                    !std::filesystem::is_regular_file(entry.symlink_status(error))) continue;
                const auto size = entry.file_size(error);
                if (error) break;
                const auto written = entry.last_write_time(error);
                if (error) break;
                records.push_back({entry.path(), written, size});
                used += size;
            }
            if (error) break;
        }
        if (error) return 0;
        if (used <= limit_ - batch) return batch;
        std::sort(records.begin(), records.end(), [](const Record& left, const Record& right) {
            return left.written < right.written;
        });
        for (const auto& record : records) {
            if (std::filesystem::remove(record.path, error) || !error) {
                used -= record.bytes;
                std::filesystem::remove(record.path.parent_path(), error);
                if (used <= limit_ - batch) return batch;
            }
        }
        return used <= limit_ ? std::min(batch, limit_ - used) : 0;
    }

    const std::uintmax_t limit_;
    std::mutex mutex_;
    std::filesystem::path root_;
    std::uintmax_t remaining_ = 0;
};
}
