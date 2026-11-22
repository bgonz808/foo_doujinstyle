#pragma once
#include "reference_projection.hpp"
#include <string>
#include <string_view>

namespace doujin::catalog_filename {
// Canonical filename-derived catalog evidence. Used by offers and ZIP archive
// inspection; rely on the descriptor's shared NFKC and catalog validator.
inline std::string hint(std::string_view filename, bool bracketed_only = false) {
    const auto name = projection::nfkc(filename);
    for (std::size_t at = name.find('['); at != std::string::npos; at = name.find('[', at + 1)) {
        if (bracketed_only && at != 0) return {};
        const auto close = name.find(']', at + 1);
        if (close == std::string::npos || close - at > 48) continue;
        const auto result = catalog_number_from_tag(name.substr(at + 1, close - at - 1));
        if (!result.empty()) return result;
    }
    if (bracketed_only) return {};
    const auto end = name.find_first_of(" ._\t\r\n[");
    return catalog_number_from_tag(name.substr(0, end));
}
} // namespace doujin::catalog_filename
