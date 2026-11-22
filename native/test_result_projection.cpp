#include "result_projection.hpp"

#include <cassert>
#include <string_view>

struct Row {
    std::wstring title, artist, date, format;
    std::vector<std::wstring> tag_values, tracks;
};

int main() {
    const auto compare = [](std::wstring_view a, std::wstring_view b) { return a.compare(b); };
    std::vector<Row> rows{
        {L"Zulu", L"Ana", L"", L"FLAC", {L"Genre, exact"}, {}},
        {L"ABC", L"Mina", L"2020-11-01", L"FLAC", {}, {}},
        {L"Alpha", L"José", L"2019-06-12", L"MP3", {L"Genre, exact"}, {L"3-1.djt"}},
        {L"One", L"Ana", L"2020-11-01", L"FLAC", {L"Genre, exact"}, {L"4-1.djt"}},
    };
    using namespace doujin;
    using Indices = std::vector<std::size_t>;
    assert(project_results(rows, {}, false, compare).indices == Indices({0, 1, 2, 3}));
    assert(project_results(rows, {ResultSort::newest, {}, {}}, true, compare).indices == Indices({1, 3, 2, 0}));
    assert(project_results(rows, {ResultSort::oldest, {}, {}}, true, compare).indices == Indices({2, 1, 3, 0}));
    assert(project_results(rows, {ResultSort::ready, {}, {}}, true, compare).indices == Indices({2, 3, 0, 1}));
    const ResultFilter filtered{ResultSort::title, L"Genre, exact", L"FLAC", true};
    auto result = project_results(rows, filtered, true, compare);
    assert(result.indices == Indices({3, 1, 0}) && result.pending == 2);
    rows[1].tag_values = {L"Genre"};
    result = project_results(rows, filtered, true, compare);
    assert(result.indices == Indices({3, 0}) && result.pending == 1);
    rows[0].tracks = {L"1-1.djt"};
    result = project_results(rows, filtered, true, compare);
    assert(result.indices == Indices({3, 0}) && result.pending == 0);
    rows[0].tracks.clear();
    assert(project_results(rows, filtered, false, compare).indices == Indices({3}));

    assert(automatic_tile_edge(860, 490, 120) == 135);
    assert(automatic_tile_edge(680, 300, 120) == 101);
    assert(automatic_tile_edge(870, 230, 120) == 78);
}
