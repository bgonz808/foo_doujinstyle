#pragma once
#include <string_view>

namespace doujin {
// Stable network identity: source/build version is generated, not maintained
// as a stale literal in each HTTP client.
inline constexpr wchar_t http_user_agent[] = L"foobar2000 DoujinStyle";
inline constexpr std::string_view robots_user_agent = "foobar2000 doujinstyle";
} // namespace doujin
