#pragma once
#ifdef _WIN32
#include "mega_cng.hpp"
#include "mediafire_http.hpp"
#include "mega_transfer.hpp"
#include "mega_filename.hpp"
#include "archive_download_sink.hpp"
#include "archive_digest.hpp"
#include <windows.h>
#include <algorithm>
#include <array>
#include <cstdio>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::mega {
namespace fs = std::filesystem;


inline nlohmann::json download(MediaFireHttp& transport, const std::string& url,
                               const std::string& output, std::uint64_t id) {
    const auto link = parse_mega_link(url);
    const auto request = nlohmann::json::array({{{"a", "g"}, {"p", link.handle}, {"g", 1}, {"ssl", 2}}}).dump();
    const auto api_url = "https://g.api.mega.co.nz/cs?id=" + std::to_string(GetTickCount64());
    const auto [status, body] = transport.json_request(api_url, request, 65536);
    if (status != 200) throw std::runtime_error("MEGA public API returned HTTP " + std::to_string(status));
    const auto public_file = parse_public_file(body);
    WindowsCipher cipher(link);
    const auto original_name = cipher.decrypt_filename(public_file.attributes);
    VerifiedPayload verified(link, public_file.size);
    archive::TemporaryArchive target(fs::path(std::u8string(output.begin(), output.end())));
    std::optional<std::string> suffix;
    archive::Sha256Stream hash;
    transport.mega_full_stream(public_file, [&](auto) { return verified.next_size(); },
        [&](std::uint64_t offset, std::string& encrypted) {
            verified.append(offset, encrypted,
                [&](auto first, std::string& data) { cipher.decrypt(first, data); },
                [&](std::string& data, std::array<unsigned char, 16> iv) { cipher.encrypt_cbc(data, iv); },
                [&](std::string& data) { cipher.encrypt(data); },
                [&](std::string_view data) {
                    if (!suffix) suffix = archive_suffix(data);
                    hash.update(data);
                    target.append(data);
                });
        });
    verified.finish();  // NEVER publish before whole-file authentication.
    const auto digest = hash.finish();
    const auto name = safe_filename(original_name, std::to_string(id), *suffix);
    const auto saved = target.finish(name, *suffix);
    const auto bytes = saved.u8string();
    return {{"path", std::string(bytes.begin(), bytes.end())},
            {"filename", name}, {"size", public_file.size}, {"authenticated", true}, {"sha256", digest}};
}
} // namespace doujin::mega
#endif
