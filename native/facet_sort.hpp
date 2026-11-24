#pragma once

// Portable comparison for human-facing event/facet labels such as C79/C100,
// M3-8/M3-12. Unlike locale-sensitive collation or integer parsing, this
// compares arbitrary-length decimal runs without numeric overflow.
#include <string_view>

namespace doujin::facet_sort {

inline bool ascii_digit(wchar_t c) noexcept {
    return c >= L'0' && c <= L'9';
}

template<class TextCompare>
int natural_compare(std::wstring_view left, std::wstring_view right, TextCompare compare) {
    std::size_t i = 0, j = 0;
    while (i < left.size() && j < right.size()) {
        if (ascii_digit(left[i]) && ascii_digit(right[j])) {
            const auto a_start = i, b_start = j;
            while (i < left.size() && ascii_digit(left[i])) ++i;
            while (j < right.size() && ascii_digit(right[j])) ++j;
            auto a = a_start, b = b_start;
            while (a < i && left[a] == L'0') ++a;
            while (b < j && right[b] == L'0') ++b;
            const auto a_len = i - a, b_len = j - b;
            if (a_len != b_len) return a_len < b_len ? -1 : 1;
            const auto cmp = left.substr(a, a_len).compare(right.substr(b, b_len));
            if (cmp) return cmp < 0 ? -1 : 1;
            // E.g. C007 and C7 compare as the same numeric event.
            // std::stable_sort preserves arrival order for equivalent keys.
        } else {
            // Compare the nonnumeric portions using the caller's established
            // Unicode case-insensitive comparator (CompareStringOrdinal on Win).
            const auto a_start = i, b_start = j;
            if (ascii_digit(left[i])) ++i;
            else while (i < left.size() && !ascii_digit(left[i])) ++i;
            if (ascii_digit(right[j])) ++j;
            else while (j < right.size() && !ascii_digit(right[j])) ++j;
            const auto cmp = compare(left.substr(a_start, i - a_start),
                                     right.substr(b_start, j - b_start));
            if (cmp) return cmp < 0 ? -1 : 1;
        }
    }
    return i == left.size() && j == right.size() ? 0 :
           i == left.size() ? -1 : 1;
}

} // namespace doujin::facet_sort
