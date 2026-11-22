#pragma once

#include "descriptor.hpp"
#include "third_party/json.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>

namespace doujin::mega {

struct PublicFile {
    std::string endpoint;
    std::uint64_t size = 0;
    std::string attributes;
};

inline bool storage_endpoint(std::string_view url) {
    // The storage host is controlled by untrusted public-file API JSON, not by the link owner.
    // Reject credentials, ports other than 443, fragments and non-HTTPS destinations.
    if (!url.starts_with("https://") || url.size() > 8192) return false;
    auto authority = url.substr(8, url.find_first_of("/?#", 8) - 8);
    if (authority.empty() || authority.find('@') != authority.npos) return false;
    if (authority.ends_with(":443")) authority.remove_suffix(4);
    if (authority.find(':') != authority.npos) return false;
    auto host = std::string(authority);
    for (auto& ch : host) if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch + 'a' - 'A');
    const bool allowed = host.ends_with(".userstorage.mega.co.nz") ||
        host.ends_with(".userstorage.mega.nz") || host.ends_with(".userstorage.mega.io");
    if (!allowed || url.find('#') != url.npos || url.find('\\') != url.npos) return false;
    for (unsigned char ch : url) if (ch < 33 || ch == 127) return false;
    return true;
}

inline void limit_json_depth(std::string_view body, unsigned maximum) {
    unsigned depth = 0;
    bool quoted = false, escaped = false;
    for (const char ch : body) {
        if (quoted) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') quoted = false;
        } else if (ch == '"') quoted = true;
        else if (ch == '{' || ch == '[') {
            if (++depth > maximum) throw std::runtime_error("MEGA JSON nesting limit exceeded");
        } else if (ch == '}' || ch == ']') {
            if (depth == 0) throw std::runtime_error("MEGA JSON nesting is invalid");
            --depth;
        }
    }
    if (quoted || depth != 0) throw std::runtime_error("Incomplete MEGA JSON document");
}

inline PublicFile parse_public_file(std::string_view body) {
    if (body.empty() || body.size() > 65536 || body.find('\0') != body.npos)
        throw std::runtime_error("MEGA API response exceeds its bound or contains NUL");
    limit_json_depth(body, 64);
    auto reply = nlohmann::json::parse(body, nullptr, false);
    if (reply.is_discarded()) throw std::runtime_error("Invalid MEGA public-file API response");
    if (reply.is_array()) {
        if (reply.size() != 1) throw std::runtime_error("Invalid MEGA public-file API result count");
        reply = std::move(reply[0]);
    }
    if (reply.is_number_integer()) {
        const auto error = reply.get<std::int64_t>();
        if (error == -9) throw std::runtime_error("MEGA public file is unavailable");
        if (error == -16) throw std::runtime_error("MEGA public file is blocked");
        if (error == -17) throw std::runtime_error("MEGA download quota reached");
        if (error == -18) throw std::runtime_error("MEGA public file temporarily unavailable");
        throw std::runtime_error("MEGA public-file API error");
    }
    if (!reply.is_object()) throw std::runtime_error("Invalid MEGA public-file API record");
    if (reply.contains("e") && reply["e"].is_number_integer() && reply["e"].get<std::int64_t>() < 0)
        throw std::runtime_error("MEGA public-file API error");
    if (!reply.contains("g") || !reply["g"].is_string() || !reply.contains("s") ||
        !reply["s"].is_number_unsigned() || !reply.contains("at") || !reply["at"].is_string())
        throw std::runtime_error("Incomplete MEGA public-file metadata");
    PublicFile out{reply["g"].get<std::string>(), reply["s"].get<std::uint64_t>(),
                   reply["at"].get<std::string>()};
    if (!storage_endpoint(out.endpoint) || !out.size || out.attributes.size() > 32768 || out.attributes.empty())
        throw std::runtime_error("Unsafe MEGA storage destination or invalid file metadata");
    return out;
}

inline std::string range_url(const PublicFile& file, std::uint64_t first, std::uint64_t length) {
    if (!storage_endpoint(file.endpoint) || !length || first >= file.size || length > file.size - first ||
        length > 2 * 1024 * 1024) throw std::runtime_error("Invalid MEGA storage byte range");
    // Keep the original path/query intact: some MEGA endpoints are tokenized.
    const auto query = file.endpoint.find('?');
    auto base = file.endpoint.substr(0, query);
    if (!base.ends_with('/')) base += '/';
    base += std::to_string(first) + '-' + std::to_string(first + length - 1);
    if (query != std::string::npos) base += file.endpoint.substr(query);
    return base;
}

inline std::string base64url_decode(std::string_view encoded, std::size_t max_bytes = 32768) {
    if (encoded.empty() || encoded.size() > (max_bytes * 4 / 3 + 4))
        throw std::runtime_error("Invalid MEGA attribute encoding length");
    constexpr std::string_view alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string result;
    result.reserve(encoded.size() * 3 / 4);
    unsigned bits = 0, pending = 0;
    for (const auto ch : encoded) {
        const auto value = alphabet.find(ch);
        if (value == alphabet.npos) throw std::runtime_error("Invalid MEGA attribute encoding");
        pending = (pending << 6) | static_cast<unsigned>(value);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            result.push_back(static_cast<char>(pending >> bits));
            if (result.size() > max_bytes) throw std::runtime_error("MEGA attributes exceed limit");
        }
    }
    if (bits && (pending & ((1u << bits) - 1)))
        throw std::runtime_error("Invalid MEGA attribute base64 padding");
    return result;
}

inline std::string file_name_from_attributes(std::string_view decrypted) {
    if (!decrypted.starts_with("MEGA{") || decrypted.size() > 32768)
        throw std::runtime_error("Invalid MEGA file attributes");
    decrypted.remove_prefix(4);
    decrypted = decrypted.substr(0, decrypted.find('\0'));
    limit_json_depth(decrypted, 32);
    const auto doc = nlohmann::json::parse(decrypted, nullptr, false);
    if (!doc.is_object() || !doc.contains("n") || !doc["n"].is_string())
        throw std::runtime_error("MEGA attributes have no filename");
    const auto filename = doc["n"].get<std::string>();
    if (filename.empty() || filename.size() > 4096 || filename.find('\0') != std::string::npos)
        throw std::runtime_error("Invalid MEGA filename");
    return filename;
}

// Pure counter generation shared by the foobar2000 input and native catalog worker.
// `encrypt` transforms a multiple of 16 bytes in place using AES-ECB under the derived key.
// It must be supplied by a vetted cryptographic provider, never handwritten AES.
template<class Buffer, class Encrypt>
void decrypt_range(const MegaLink& link, std::uint64_t offset, Buffer& ciphertext, Encrypt&& encrypt) {
    if (ciphertext.empty()) return;
    if (ciphertext.size() > 2 * 1024 * 1024 || offset > std::numeric_limits<std::uint64_t>::max() - ciphertext.size())
        throw std::runtime_error("MEGA range exceeds safe decryption limits");
    const std::size_t skip = static_cast<std::size_t>(offset % 16);
    const std::size_t blocks = (ciphertext.size() + skip + 15) / 16;
    std::string counter(blocks * 16, '\0');
    std::uint64_t number = offset / 16;
    for (std::size_t i = 0; i < counter.size(); i += 16, ++number) {
        for (unsigned j = 0; j != 8; ++j) counter[i + j] = static_cast<char>(link.key[16 + j]);
        for (unsigned j = 0; j != 8; ++j) counter[i + 15 - j] = static_cast<char>(number >> (8 * j));
    }
    encrypt(counter);
    for (std::size_t i = 0; i < ciphertext.size(); ++i) ciphertext[i] ^= counter[i + skip];
}

} // namespace doujin::mega
