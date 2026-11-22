#pragma once

#include <windows.h>
#include <array>
#include <filesystem>

namespace doujin {
inline std::filesystem::path foobar_zlib_path() {
    std::array<wchar_t, 32768> executable{};
    const auto length = GetModuleFileNameW(nullptr, executable.data(), static_cast<DWORD>(executable.size()));
    if (!length || length >= executable.size()) return {};
    return std::filesystem::path(executable.data()).parent_path() / L"zlib1.dll";
}
}
