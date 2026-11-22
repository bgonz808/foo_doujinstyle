#pragma once
#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
namespace doujin::archive {
namespace fs = std::filesystem;
class TemporaryArchive {
public:
    explicit TemporaryArchive(const fs::path& output) : root_(output) {
        if (!output.is_absolute()) throw std::runtime_error("Archive download output must be absolute");
        fs::create_directories(output);
        for (int attempt = 0; attempt < 20; ++attempt) {
            std::array<unsigned char, 16> random{};
            if (BCryptGenRandom(nullptr, random.data(), static_cast<ULONG>(random.size()),
                                BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
                throw std::runtime_error("Windows entropy unavailable for temporary archive");
            std::string suffix;
            constexpr char hex[] = "0123456789abcdef";
            for (auto c : random) { suffix.push_back(hex[c >> 4]); suffix.push_back(hex[c & 15]); }
            const auto name = ".doujin-" + suffix + ".part";
            path_ = output / fs::path(std::u8string(name.begin(), name.end()));
            if (_wfopen_s(&file_, path_.c_str(), L"wbx") == 0 && file_) return;
        }
        throw std::runtime_error("Could not create exclusive temporary MEGA archive");
    }
    TemporaryArchive(const TemporaryArchive&) = delete;
    TemporaryArchive& operator=(const TemporaryArchive&) = delete;
    ~TemporaryArchive() {
        if (file_) std::fclose(file_);
        if (!path_.empty()) { std::error_code ignored; fs::remove(path_, ignored); }
    }
    void append(std::string_view bytes) {
        if (!file_ || std::fwrite(bytes.data(), 1, bytes.size(), file_) != bytes.size())
            throw std::runtime_error("Could not write MEGA archive to temporary file");
    }
    fs::path finish(std::string_view filename, std::string_view extension) {
        if (!file_) throw std::runtime_error("Archive file is closed");
        const auto flushed = std::fflush(file_);
        const auto closed = std::fclose(file_);
        file_ = nullptr;
        if (flushed || closed) throw std::runtime_error("Could not finish MEGA archive file");
        auto stem = std::string(filename);
        if (stem.ends_with(extension)) stem.resize(stem.size() - extension.size());
        for (unsigned n = 1; n <= 1000; ++n) {
            const auto text = n == 1 ? std::string(filename) : stem + " (" + std::to_string(n) + ")" + std::string(extension);
            const auto destination = root_ / fs::path(std::u8string(text.begin(), text.end()));
            if (MoveFileW(path_.c_str(), destination.c_str())) {
                path_.clear();
                return destination;
            }
            const auto error = GetLastError();
            if (error != ERROR_ALREADY_EXISTS && error != ERROR_FILE_EXISTS)
                throw std::runtime_error("Could not commit verified MEGA archive: Windows error " + std::to_string(error));
        }
        throw std::runtime_error("No unused MEGA download filename");
    }
private:
    fs::path root_, path_;
    std::FILE* file_ = nullptr;
};

} // namespace doujin::archive
#endif
