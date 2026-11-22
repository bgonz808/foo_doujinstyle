#pragma once

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

namespace doujin {

enum class ResultSort { source, title, artist, newest, oldest, ready };

struct ResultFilter {
    ResultSort sort = ResultSort::source;
    std::wstring tag, format;
    bool ready = false;
};

enum class FilterMatch { yes, pending, no };

template<typename Row, typename Compare>
FilterMatch match_result(const Row& row, const ResultFilter& filter, bool loading, Compare compare) {
    bool incomplete = false;
    if (!filter.tag.empty()) {
        if (row.tag_values.empty()) incomplete = true;
        else if (std::none_of(row.tag_values.begin(), row.tag_values.end(), [&](const auto& tag) {
            return compare(tag, filter.tag) == 0;
        })) return FilterMatch::no;
    }
    if (!filter.format.empty()) {
        if (row.format.empty()) incomplete = true;
        else if (compare(row.format, filter.format) != 0) return FilterMatch::no;
    }
    incomplete |= filter.ready && row.tracks.empty();
    if (!incomplete) return FilterMatch::yes;
    return loading ? FilterMatch::pending : FilterMatch::no;
}

struct ResultProjection {
    std::vector<std::size_t> indices;
    std::size_t pending = 0;
};

template<typename Row, typename Compare>
ResultProjection project_results(const std::vector<Row>& rows, const ResultFilter& filter,
                                 bool loading, Compare compare) {
    ResultProjection projection;
    std::vector<std::size_t> pending;
    for (std::size_t index = 0; index < rows.size(); ++index) {
        const auto match = match_result(rows[index], filter, loading, compare);
        if (match == FilterMatch::yes) projection.indices.push_back(index);
        if (match == FilterMatch::pending) pending.push_back(index);
    }
    const auto less = [&](std::size_t left_index, std::size_t right_index) {
        const auto& left = rows[left_index];
        const auto& right = rows[right_index];
        const auto ascending = [&](const auto& a, const auto& b, bool descending = false) {
            if (a.empty() || b.empty()) return b.empty() && !a.empty();
            const auto order = compare(a, b);
            return descending ? order > 0 : order < 0;
        };
        switch (filter.sort) {
        case ResultSort::title: return ascending(left.title, right.title);
        case ResultSort::artist: return ascending(left.artist, right.artist);
        case ResultSort::newest: return ascending(left.date, right.date, true);
        case ResultSort::oldest: return ascending(left.date, right.date);
        case ResultSort::ready: return !left.tracks.empty() && right.tracks.empty();
        case ResultSort::source: return false;
        }
        return false;
    };
    if (filter.sort != ResultSort::source) {
        std::stable_sort(projection.indices.begin(), projection.indices.end(), less);
        std::stable_sort(pending.begin(), pending.end(), less);
    }
    projection.pending = pending.size();
    projection.indices.insert(projection.indices.end(), pending.begin(), pending.end());
    return projection;
}

// All measurements are in device pixels. Keep two rows and five columns in
// view at default zoom whenever the viewport can fit the smallest tiles.
inline int automatic_tile_edge(int width, int height, int dpi) {
    const auto px = [dpi](int units) { return (units * dpi + 48) / 96; };
    const int available = std::min(width / 5 - px(28), height / 2 - px(39));
    return std::clamp(available, px(62), px(108));
}

} // namespace doujin
