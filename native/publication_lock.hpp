#pragma once

#include <chrono>
#include <filesystem>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

namespace doujin {
class PublicationLock {
public:
    explicit PublicationLock(std::filesystem::path catalog) {
        catalog += ".publish.lock";
#ifdef _WIN32
        file_ = CreateFileW(catalog.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                            nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file_ == INVALID_HANDLE_VALUE) throw std::runtime_error("Could not open the publication lock");
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(20);
        OVERLAPPED range{};
        while (!LockFileEx(file_, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &range)) {
            if (GetLastError() != ERROR_LOCK_VIOLATION || std::chrono::steady_clock::now() >= deadline) {
                CloseHandle(file_);
                throw std::runtime_error("Could not acquire the publication lock");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
#else
        file_ = open(catalog.c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
        if (file_ < 0) throw std::runtime_error("Could not open the publication lock");
        while (flock(file_, LOCK_EX) != 0) {
            if (errno == EINTR) continue;
            close(file_);
            throw std::runtime_error("Could not acquire the publication lock");
        }
#endif
    }

    ~PublicationLock() {
#ifdef _WIN32
        OVERLAPPED range{};
        UnlockFileEx(file_, 0, 1, 0, &range);
        CloseHandle(file_);
#else
        close(file_);
#endif
    }
    PublicationLock(const PublicationLock&) = delete;
    PublicationLock& operator=(const PublicationLock&) = delete;

private:
#ifdef _WIN32
    HANDLE file_;
#else
    int file_;
#endif
};
} // namespace doujin
