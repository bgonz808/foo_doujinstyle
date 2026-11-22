#pragma once
#include <algorithm>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::mega {
inline std::string archive_suffix(std::string_view bytes) {
    if (bytes.starts_with("PK\x03\x04") || bytes.starts_with("PK\x05\x06")) return ".zip";
    if (bytes.starts_with("Rar!\x1a\x07")) return ".rar";
    if (bytes.starts_with("7z\xbc\xaf\x27\x1c")) return ".7z";
    if (bytes.starts_with("\x1f\x8b")) return ".gz";
    throw std::runtime_error("Archive has an unsupported or invalid signature");
}

inline std::string safe_filename(std::string_view filename, std::string_view id, std::string_view extension) {
    std::string name;
    for (const unsigned char byte : filename) {
        if (byte < 32 || byte == 127 || std::string_view("<>:\"/\\\\|?*").find(static_cast<char>(byte)) != std::string::npos)
            name.push_back('_');
        else name.push_back(static_cast<char>(byte));
    }
    const auto first = name.find_first_not_of(" .");
    if (first == name.npos) name.clear();
    else {
        name.erase(0, first);
        while (!name.empty() && (name.back() == ' ' || name.back() == '.')) name.pop_back();
    }
    // Windows device names are forbidden even when followed by an extension.
    std::string stem = name.substr(0, name.find('.'));
    for (auto& ch : stem) if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 32);
    const bool device = stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
        (stem.size() == 4 && (stem.starts_with("COM") || stem.starts_with("LPT")) && stem[3] >= '1' && stem[3] <= '9');
    if (device || name.empty()) name = "DoujinStyle-" + std::string(id);
    if (name.size() < extension.size() ||
        !std::equal(extension.rbegin(), extension.rend(), name.rbegin(), [](char a, char b) {
            auto lower = [](char ch) { return ch >= 'A' && ch <= 'Z' ? static_cast<char>(ch + 32) : ch; };
            return lower(a) == lower(b);
        })) name += extension;
    if (name.size() > 150 + extension.size()) {
        const auto base = name.substr(0, name.size() - extension.size());
        const auto cap = std::min<std::size_t>(150, base.size());
        std::size_t at = 0, safe = 0;
        while (at < cap) {
            const auto first = static_cast<unsigned char>(base[at]);
            const std::size_t width = first < 0x80 ? 1 : (first & 0xe0) == 0xc0 ? 2 :
                (first & 0xf0) == 0xe0 ? 3 : (first & 0xf8) == 0xf0 ? 4 : 0;
            if (!width || width > cap - at) break;
            bool valid = true;
            for (std::size_t i = 1; i < width; ++i)
                valid &= (static_cast<unsigned char>(base[at + i]) & 0xc0) == 0x80;
            if (!valid) break;
            at += width;
            safe = at;
        }
        name = base.substr(0, safe) + std::string(extension);
    }
    return name;
}


} // namespace doujin::mega
