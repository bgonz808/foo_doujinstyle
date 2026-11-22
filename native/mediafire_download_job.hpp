#pragma once
#ifdef _WIN32
#include "mediafire_http.hpp"
#include "mega_filename.hpp"
#include "archive_download_sink.hpp"
#include "archive_digest.hpp"
#include "mediafire_download_policy.hpp"
#include <optional>
#include <string>

namespace doujin::mediafire {
inline nlohmann::json download(MediaFireHttp& transport, const std::string& share,
                               const std::string& referer, const std::string& offered_name,
                               const std::string& output, std::uint64_t id) {
    const bool share_page = mediafire_index_url(share, true);
    if (!share_page && !mediafire_index_url(share, false))
        throw std::runtime_error("MediaFire job requires a supported HTTPS URL");
    nlohmann::json resolved = {{"url", share}, {"referrer", referer}, {"policy", ""}};
    if (share_page) {
        resolved = transport.resolve({{"url", share}, {"referrer", referer}});
        if (resolved.at("status") != 200)
            throw std::runtime_error("MediaFire download link resolution failed");
    }
    const auto direct = resolved.at("url").get<std::string>();
    if (!mediafire_index_url(direct, false))
        throw std::runtime_error("MediaFire download link left its allowed origin");
    archive::TemporaryArchive target(std::filesystem::path(std::u8string(output.begin(), output.end())));
    PrefixValidator validation;
    archive::Sha256Stream hash;
    const auto size = transport.download_stream(direct, resolved.at("referrer").get<std::string>(),
        resolved.at("policy").get<std::string>(), [&](std::string_view bytes) {
            validation.append(bytes);
            hash.update(bytes);
            target.append(bytes);
        });
    const auto extension = validation.finish();
    const auto digest = hash.finish();
    const auto name = mega::safe_filename(offered_name, std::to_string(id), extension);
    const auto saved = target.finish(name, extension);
    const auto saved_u8 = saved.u8string();
    return {{"path", std::string(saved_u8.begin(), saved_u8.end())},
            {"filename", name}, {"size", size}, {"authenticated", false},
            {"integrity", "transport_and_length_only"}, {"sha256", digest}};
}
} // namespace doujin::mediafire
#endif
