#include "facet_sort.hpp"
#include <algorithm>
#include <cassert>
#include <string>
#include <string_view>
#include <vector>

int main() {
    const auto compare = [](std::wstring_view a, std::wstring_view b) {
        // Portable ASCII fixture comparator. Windows SearchDialog supplies
        // CompareStringOrdinal instead for real Unicode case-insensitivity.
        std::wstring left(a), right(b);
        const auto fold = [](wchar_t ch) {
            return ch >= L'A' && ch <= L'Z' ? ch - L'A' + L'a' : ch;
        };
        std::transform(left.begin(), left.end(), left.begin(), fold);
        std::transform(right.begin(), right.end(), right.begin(), fold);
        const auto v = left.compare(right);
        return (v > 0) - (v < 0);
    };
    using doujin::facet_sort::natural_compare;
    const auto cmp = [&](std::wstring_view a, std::wstring_view b) {
        return natural_compare(a, b, compare);
    };
    assert(cmp(L"C79", L"C100") < 0);
    assert(cmp(L"C100", L"C79") > 0);
    assert(cmp(L"C009", L"C9") == 0);
    assert(cmp(L"M3-9", L"M3-10") < 0);
    assert(cmp(L"M3-2025春", L"M3-2026春") < 0);
    assert(cmp(L"M3-9999999999999999999999999", L"M3-10000000000000000000000000") < 0);
    assert(cmp(L"FLAC", L"FLAC+CUE") < 0);
    assert(cmp(L"wav", L"WAV") == 0);
    assert(cmp(L"ABC99end2", L"ABC99end11") < 0);
    assert(cmp(L"Comiket 100", L"comiket 79") > 0);
    std::vector<std::wstring> tags{L"C100", L"C79", L"C9", L"M3-2024秋", L"M3-2023春", L"M3-2025春"};
    std::stable_sort(tags.begin(), tags.end(), [&](auto& a, auto& b) { return cmp(a,b) < 0; });
    assert((tags == std::vector<std::wstring>{
        L"C9", L"C79", L"C100", L"M3-2023春", L"M3-2024秋", L"M3-2025春"}));
    // Comparator invariants: antisymmetry and transitivity over actual labels.
    for (const auto& a : tags) for (const auto& b : tags) {
        assert(cmp(a,b) == -cmp(b,a));
        for (const auto& c : tags)
            if (cmp(a,b) < 0 && cmp(b,c) < 0) assert(cmp(a,c) < 0);
    }
}
