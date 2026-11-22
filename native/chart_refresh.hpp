#pragma once

// Non-blocking cross-process fetch lease. Never hold a SQLite transaction over HTTP.
#include <cerrno>
#include <filesystem>
#include <string>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

namespace doujin::discovery {
class ChartRefreshLease {
public:
    explicit ChartRefreshLease(const std::string& db) {
        if (db.empty() || db == ":memory:") { acquired_ = true; return; }
#ifdef _WIN32
        const auto path = std::filesystem::path(std::u8string(db.begin(), db.end())).concat(L".charts.lock");
        handle_ = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                              OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle_ != INVALID_HANDLE_VALUE) acquired_ = true;
        else if (GetLastError() != ERROR_SHARING_VIOLATION && GetLastError() != ERROR_LOCK_VIOLATION)
            throw std::runtime_error("Could not open chart refresh lock");
#else
        const auto path = std::filesystem::path(db).concat(".charts.lock");
        fd_ = ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
        if (fd_ < 0) throw std::runtime_error("Could not open chart refresh lock");
        if (::flock(fd_, LOCK_EX | LOCK_NB) == 0) acquired_ = true;
        else if (errno != EWOULDBLOCK && errno != EAGAIN)
            throw std::runtime_error("Could not acquire chart refresh lock");
#endif
    }
    ~ChartRefreshLease() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
        if (fd_ >= 0) { if (acquired_) ::flock(fd_, LOCK_UN); ::close(fd_); }
#endif
    }
    ChartRefreshLease(const ChartRefreshLease&) = delete;
    ChartRefreshLease& operator=(const ChartRefreshLease&) = delete;
    bool acquired() const { return acquired_; }
private:
    bool acquired_ = false;
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int fd_ = -1;
#endif
};
} // namespace doujin::discovery
