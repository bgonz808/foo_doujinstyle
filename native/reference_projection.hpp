#pragma once

#include "catalog_ingest.hpp"
#include "site_offers.hpp"
#include "third_party/utf8proc/utf8proc.h"

#include <cmath>
#include <cstdlib>

namespace doujin::projection {

inline const Json& get(const Json& object, const char* key) {
    static const Json missing;
    const auto i = object.is_object() ? object.find(key) : object.end();
    return i == object.end() ? missing : *i;
}

inline std::string_view text(const Json& value) {
    return value.is_string() ? value.get_ref<const std::string&>() : std::string_view{};
}

inline std::string unicode_map(std::string_view value, utf8proc_option_t options) {
    if (value.empty()) return {};
    utf8proc_uint8_t* raw = nullptr;
    const auto count = utf8proc_map(reinterpret_cast<const utf8proc_uint8_t*>(value.data()),
                                    static_cast<utf8proc_ssize_t>(value.size()), &raw, options);
    const std::unique_ptr<utf8proc_uint8_t, decltype(&std::free)> mapped(raw, std::free);
    if (count < 0) throw std::runtime_error("Invalid Unicode in publication metadata");
    return {reinterpret_cast<const char*>(mapped.get()), static_cast<std::size_t>(count)};
}

inline bool space(utf8proc_int32_t ch) {
    const auto category = utf8proc_category(ch);
    return (ch >= 9 && ch <= 13) || (ch >= 0x1c && ch <= 0x1f) || ch == 0x85 ||
        category == UTF8PROC_CATEGORY_ZS || category == UTF8PROC_CATEGORY_ZL || category == UTF8PROC_CATEGORY_ZP;
}

template<typename Visitor>
void characters(std::string_view value, Visitor visit) {
    while (!value.empty()) {
        utf8proc_int32_t ch = 0;
        const auto n = utf8proc_iterate(reinterpret_cast<const utf8proc_uint8_t*>(value.data()),
                                        static_cast<utf8proc_ssize_t>(value.size()), &ch);
        if (n <= 0) throw std::runtime_error("Invalid UTF-8 in publication metadata");
        const auto size = static_cast<std::size_t>(n);
        visit(ch, value.substr(0, size));
        value.remove_prefix(size);
    }
}

inline std::string trim(std::string_view value) {
    std::size_t offset = 0, first = value.size(), last = value.size();
    characters(value, [&](auto ch, auto encoded) {
        if (!space(ch)) {
            first = std::min(first, offset);
            last = offset + encoded.size();
        }
        offset += encoded.size();
    });
    return std::string(value.substr(first, last - first));
}

inline std::string nfkc(std::string_view value) {
    // utf8proc_option_t is a bitmask. The library documents combining these flags.
    // NOLINTNEXTLINE(clang-analyzer-optin.core.EnumCastOutOfRange)
    return unicode_map(value, static_cast<utf8proc_option_t>(UTF8PROC_STABLE | UTF8PROC_COMPOSE | UTF8PROC_COMPAT));
}

inline std::string normalized(std::string_view value) {
    return trim(unicode_map(nfkc(value), UTF8PROC_CASEFOLD));
}

inline std::string event_id(std::string_view value) {
    std::string compact;
    characters(nfkc(value), [&](auto ch, auto encoded) { if (!space(ch)) compact += encoded; });
    return release_event_from_tag(compact);
}

using Texts = std::vector<std::string>;
inline Texts unique_values(const Texts& values) {
    Texts result;
    std::set<std::string> seen;
    for (const auto& value : values) {
        auto key = normalized(value);
        if (!key.empty() && seen.insert(std::move(key)).second) result.push_back(trim(value));
    }
    return result;
}

inline Texts strings(const Json& array) {
    Texts results;
    if (array.is_array()) {
        for (const auto& item : array) {
            if (item.is_string()) results.emplace_back(text(item));
        }
    }
    return results;
}

inline std::string upper_ascii(std::string value) {
    for (auto& ch : value) if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - ('a' - 'A'));
    return value;
}

inline std::string catalog_number(std::string_view value) {
    return catalog_number_from_tag(trim(value));
}

inline std::string portable_slashes(std::string value) {
    std::replace(value.begin(), value.end(), '\\', '/');
    return value;
}

inline Texts path_parts(std::string_view path) {
    Texts parts;
    while (!path.empty()) {
        const auto cut = path.find('/');
        const auto part = path.substr(0, cut);
        if (!part.empty() && part != ".") parts.emplace_back(part);
        if (cut == path.npos) break;
        path.remove_prefix(cut + 1);
    }
    return parts;
}

inline std::string folder(std::string_view path) {
    const auto slash = portable_slashes(std::string(path));
    auto parts = path_parts(slash);
    if (!parts.empty()) parts.pop_back();
    std::string result = slash.starts_with("//") && !slash.starts_with("///") ? "//" :
        slash.starts_with('/') ? "/" : "";
    for (const auto& part : parts) {
        if (!result.empty() && !result.ends_with('/')) result += '/';
        result += part;
    }
    return result;
}

inline std::string legacy_name(std::string_view member, const Json& zip_name) {
    const auto hex = text(get(zip_name, "raw"));
    if (hex.empty()) return std::string(member);
    const auto raw = decode_hex(hex);
    if (raw == member) return raw;
    constexpr std::uint32_t cp437[]{
#include "reference_cp437.inc"
    };
    std::string result;
    for (unsigned char byte : raw) {
        if (byte < 128) result += static_cast<char>(byte);
        else append_html_codepoint(result, cp437[byte - 128]);
    }
    return result;
}

inline bool is_track_digit(utf8proc_int32_t ch) {
    if (utf8proc_category(ch) == UTF8PROC_CATEGORY_ND) return true;
    // Unicode 15 Numeric_Type=Digit ranges. Python isdigit() accepts these in addition to Nd.
    constexpr utf8proc_int32_t ranges[][2]{
        {0xb2, 0xb3}, {0xb9, 0xb9}, {0x1369, 0x1371}, {0x19da, 0x19da}, {0x2070, 0x2070},
        {0x2074, 0x2079}, {0x2080, 0x2089}, {0x2460, 0x2468}, {0x2474, 0x247c}, {0x2488, 0x2490},
        {0x24ea, 0x24ea}, {0x24f5, 0x24fd}, {0x24ff, 0x24ff}, {0x2776, 0x277e}, {0x2780, 0x2788},
        {0x278a, 0x2792}, {0x10a40, 0x10a43}, {0x10e60, 0x10e68}, {0x11052, 0x1105a}, {0x1f100, 0x1f10a},
    };
    return std::ranges::any_of(ranges, [ch](const auto& range) { return ch >= range[0] && ch <= range[1]; });
}

inline Json preferred_track(std::string_view stem, const Json& tracks) {
    const auto first = stem.find(" - ");
    auto second_name = std::string{};
    if (first != stem.npos) {
        const auto number = trim(stem.substr(0, first));
        bool digits = !number.empty();
        characters(number, [&](auto ch, auto) { digits = digits && is_track_digit(ch); });
        if (digits) {
            const auto second = stem.find(" - ", first + 3);
            second_name = normalized(stem.substr((second == stem.npos ? first : second) + 3));
        }
    }
    const auto main_name = normalized(stem);
    const Json* match = nullptr;
    for (const auto& track : tracks) {
        const auto title = normalized(text(get(track, "title")));
        if (title == main_name || (!second_name.empty() && title == second_name)) {
            if (match) return nullptr;
            match = &track;
        }
    }
    return match ? *match : Json{};
}

inline std::pair<std::string, std::string> release_date(const Json& release, const Json& external,
                                                        std::string_view embedded, const Texts& events) {
    const std::array raw{embedded, text(get(release, "release_date")), text(get(external, "release_date"))};
    constexpr std::array names{"Embedded", "DoujinStyle release date", "Bangumi"};
    for (std::size_t i = 0; i < raw.size(); ++i) {
        if (auto date = format_release_date(trim(raw[i])); !date.empty()) return {std::move(date), names[i]};
    }
    const auto year = embedded.size() == 4 && embedded.find_first_not_of("0123456789") == embedded.npos
        ? embedded : std::string_view{};
    const auto matches = [&](std::string_view date) {
        return !date.empty() && (year.empty() || (date.starts_with(year) && date[4] == '-'));
    };
    if (events.size() == 1) {
        const auto standard = event_id(events.front());
        const auto entries = discovery::events_for(Json::array({standard}));
        if (!entries.empty()) {
            const auto start = text(get(entries.front(), "start"));
            if (start == text(get(entries.front(), "end")) && matches(start))
                return {std::string(start), "Release event " + standard};
        }
    }
    auto added = format_release_date(trim(text(get(release, "listed_at"))));
    if (matches(added)) return {std::move(added), "DoujinStyle date added"};
    return {};
}

class ReleaseProjection {
public:
    ReleaseProjection(const Json& release, const Json& archive, const Json& tracks)
        : release_(release), archive_(archive), tracks_(tracks),
          lookup_(get(get(release, "external_lookup"), "bangumi")), external_(get(lookup_, "fields")),
          release_tags_(unique_values(strings(get(release, "tags")))) {
        for (const auto* source : {&release_, &external_})
            for (const auto& event : get(*source, "events"))
                if (auto id = text(get(event, "id")); !id.empty()) events_.emplace_back(id);
        for (const auto& tag : strings(get(release, "tags"))) {
            if (auto code = event_id(tag); !code.empty()) events_.push_back(std::move(code));
        }
        constexpr std::string_view genres =
            "ambient|artcore|breakbeat|breakcore|chiptune|classical|dance|denpa|dnb|drum and bass|dubstep|"
            "edm|electro|electro house|electro pop|electronic|eurobeat|experimental|folk|footcore|footwork|"
            "future bass|gabber|glitch|happy hardcore|hard trance|hardcore|hardstyle|hip-hop|house|industrial|"
            "j-core|j-pop|jazz|juke|metal|nightcore|orchestral|pop|power metal|progressive house|psy-trance|"
            "rock|schranz|soundtrack|speedcore|symphonic|techno|trance|uk hardcore|world";
        for (const auto& tag : release_tags_) {
            const auto key = normalized(tag);
            auto list = genres;
            while (!list.empty()) {
                const auto bar = list.find('|');
                if (list.substr(0, bar) == key) { genres_.push_back(tag); break; }
                if (bar == list.npos) break;
                list.remove_prefix(bar + 1);
            }
        }
        std::map<std::string, std::string> artists;
        const auto collect_artist = [&](const Json& probe) {
            const auto& tags = get(probe, "tags");
            auto names = strings(get(tags, "ALBUMARTIST"));
            if (names.empty()) names = strings(get(tags, "ALBUM ARTIST"));
            for (const auto& artist : names) {
                auto key = normalized(artist);
                if (!key.empty()) artists.try_emplace(std::move(key), artist);
            }
        };
        for (const auto& [path, probe] : get(archive_, "audio_probes").items()) {
            collect_artist(probe);
            auto& numbers = folder_catalogs_[folder(path)];
            for (const auto field : catalog_number_tags) {
                for (const auto& value : strings(get(get(probe, "tags"), field))) {
                    auto number = catalog_number(value);
                    if (!number.empty()) numbers.insert(std::move(number));
                }
            }
        }
        collect_artist(get(archive_, "audio_sample"));
        if (artists.size() == 1) album_artist_ = artists.begin()->second;
    }

    std::string descriptor(const Json& item, std::string_view url) const {
        const auto member = text(get(item, "path"));
        const auto parts = path_parts(portable_slashes(std::string(member)));
        if (parts.empty() || std::any_of(member.begin(), member.end(), [](unsigned char ch) { return ch < 32; }))
            return {};
        const auto& name = parts.back();
        if (name.starts_with("._") || name.find_first_of(":?#|") != name.npos ||
            std::any_of(parts.begin(), parts.end(), [](const auto& part) { return lower_ascii(part) == "__macosx"; }))
            return {};
        const auto suffix = name.find_last_of('.');
        if (suffix == name.npos || suffix == 0 || suffix + 1 == name.size()) return {};
        const auto stem = name.substr(0, suffix);
        const auto track = preferred_track(stem, tracks_);
        const auto& sample = get(archive_, "audio_sample");
        const auto& probes = get(archive_, "audio_probes");
        const Json no_sample;
        const auto& existing = text(get(sample, "path")) == member ? sample : no_sample;
        const auto& observed = get(probes, std::string(member).c_str());
        const auto merge = [&](const char* key) {
            auto result = Json::object();
            for (const auto* source : {&existing, &observed}) {
                const auto& fields = get(*source, key);
                if (fields.is_object()) result.update(fields);
            }
            return result;
        };
        std::map<std::string, Texts> tags;
        const auto source_tags = merge("tags");
        for (const auto& [name, values] : source_tags.items())
            tags[upper_ascii(name)] = unique_values(strings(values));
        const auto stream = merge("metadata");
        const auto preferred = [&](std::initializer_list<const char*> names, Json fallback = nullptr) {
            for (const auto name : names) if (!tags[name].empty()) return Json(tags[name].front());
            return fallback;
        };
        auto events = events_;
        Texts catalog_tags = tags["CATALOGNUMBER"];
        catalog_tags.insert(catalog_tags.end(), tags["CATALOG NUMBER"].begin(), tags["CATALOG NUMBER"].end());
        const auto add_event = [&](std::string_view tag) {
            if (auto code = event_id(tag); !code.empty()) events.push_back(std::move(code));
        };
        for (const auto& value : tags["COMMENT"]) add_event(value);
        for (const auto& value : catalog_tags) add_event(value);
        add_event(text(get(release_, "catalog_id")));
        add_event(text(get(external_, "catalog_id")));
        events = unique_values(events);

        const auto raw_date = preferred({"DATE", "YEAR"});
        const auto [date, date_source] = release_date(release_, external_, text(raw_date), events);
        const auto year = text(raw_date);
        const auto catalog = [](std::string_view value) {
            return !value.empty() && event_id(value).empty() ? std::string(value) : std::string{};
        };
        std::string track_catalog;
        for (const auto& tag : catalog_tags) if (!(track_catalog = catalog(tag)).empty()) break;
        if (track_catalog.empty()) {
            for (const auto& tag : tags["CATALOGID"])
                if (!(track_catalog = catalog_number(tag)).empty()) break;
        }
        const auto album_catalogs = folder_catalogs_.find(folder(member));
        const auto album_catalog = album_catalogs != folder_catalogs_.end() && album_catalogs->second.size() == 1 ?
            catalog(*album_catalogs->second.begin()) : std::string{};
        const auto release_catalog = catalog(text(get(release_, "catalog_id")));
        const auto lookup_catalog = catalog(text(get(external_, "catalog_id")));
        const std::array<const std::string*, 4> catalog_candidates{
            &track_catalog, &release_catalog, &lookup_catalog, &album_catalog};

        std::string result = "DOUJIN-TRACK 1\n";
        const auto add_text = [&](std::string_view key, std::string_view value) {
            result += key;
            result += '=';
            for (unsigned char ch : value) if (ch >= 32) result += static_cast<char>(ch);
            result += '\n';
        };
        const auto add = [&](std::string_view key, const Json& value) {
            if (value.is_string()) add_text(key, text(value));
            else if (value.is_number()) add_text(key, value.dump());
            else if (value.is_boolean()) add_text(key, value.get<bool>() ? "True" : "False");
        };
        const auto optional = [&](std::string_view key, std::string_view value) {
            if (!value.empty()) add_text(key, value);
        };
        const auto either = [](const Json& left, const Json& right) -> const Json& { return truthy(left) ? left : right; };
        add_text("URL", discovery::percent_encode(url, ":/?&=%+#[]!$'()*+,;@~-"));
        add("REFERRER", get(release_, "url"));
        add_text("MEMBER", member);
        const auto raw = text(get(get(item, "zip_name"), "raw"));
        const auto method = get(item, "zip_method");
        const auto& bytes = get(item, "uncompressed_bytes");
        const auto& packed = get(item, "compressed_bytes");
        if (!raw.empty() && decode_hex(raw) != member && (method == 0 || method == 8) &&
            bytes.is_number_unsigned() && packed.is_number_unsigned() &&
            bytes.get<std::uint64_t>() <= UINT32_MAX && packed.get<std::uint64_t>() <= UINT32_MAX) {
            const auto crc_text = text(get(item, "crc32"));
            std::uint32_t crc = 0;
            const auto parsed = std::from_chars(crc_text.data(), crc_text.data() + crc_text.size(), crc, 16);
            if (parsed.ec != std::errc{} || parsed.ptr != crc_text.data() + crc_text.size())
                throw std::runtime_error("Invalid archive member CRC for publication");
            add_text("MEMBER_LOCATOR", std::string(raw) + ":" + item.at("zip_header_offset").dump() + ":" +
                                        std::to_string(crc));
        }
        add_text("FILENAME", name);
        add("RELEASE_ID", release_.at("id"));
        add("TITLE", preferred({"TITLE"}, either(get(track, "title"), Json(stem))));
        add("ARTIST", preferred({"ARTIST"}, get(track, "artist")));
        add("ALBUM", preferred({"ALBUM"}, release_.at("album")));
        add("ALBUM ARTIST", preferred({"ALBUMARTIST", "ALBUM ARTIST"}, either(Json(album_artist_),
            either(get(release_, "album_artist"), get(release_, "artist")))));
        optional("DATE", date);
        optional("DATE SOURCE", date_source);
        if (date.empty() && year.size() == 4 && year.find_first_not_of("0123456789") == year.npos)
            add_text("RELEASE YEAR", year);
        add("LABEL", preferred({"LABEL", "PUBLISHER"}, either(get(release_, "publisher"), get(external_, "publisher"))));
        for (const auto* candidate : catalog_candidates) {
            if (!candidate->empty()) { add_text("CATALOGNUMBER", *candidate); break; }
        }
        add_text("CATALOG SOURCES", "1");
        optional("CATALOG SOURCE TRACK", track_catalog);
        optional("CATALOG SOURCE RELEASE", release_catalog);
        optional("CATALOG SOURCE LOOKUP", lookup_catalog);
        optional("CATALOG SOURCE ALBUM", album_catalog);
        add("BANGUMI URL", get(lookup_, "url"));
        add("TRACKNUMBER", preferred({"TRACKNUMBER"}, get(track, "number")));
        add("DISCNUMBER", preferred({"DISCNUMBER"}));
        add("TOTALTRACKS", preferred({"TOTALTRACKS", "TRACKTOTAL"}));
        add("TOTALDISCS", preferred({"TOTALDISCS", "DISCTOTAL"}));
        add("DOUJINSTYLE DATE ADDED", get(release_, "listed_at"));
        add("SAMPLERATE", get(stream, "sample_rate_hz"));
        add("CHANNELS", get(stream, "channels"));
        add("BITSPERSAMPLE", get(stream, "bits_per_sample"));
        add("CODEC", get(stream, "codec"));
        add("ENCODING", get(stream, "encoding"));
        add("SOURCE_BYTES", bytes);
        add("ZIP_MEMBER_COMPRESSED_BYTES", packed);
        const auto& seconds = get(stream, "duration_seconds");
        if (seconds.is_number() && seconds.get<double>() > 0) {
            const auto duration = seconds.get<double>();
            const auto rounded = [&](const char* key, double value) {
                const auto n = std::nearbyint(value);
                if (n >= 0 && n < std::ldexp(1.0, 64)) add(key, static_cast<std::uint64_t>(n));
            };
            rounded("DURATION_MS", duration * 1000);
            auto average = get(stream, "file_average_bitrate_bps");
            if (average.is_null() && bytes.is_number()) average = bytes.get<double>() * 8 / duration;
            if (average.is_number() && truthy(average)) rounded("BITRATE_KBPS", average.get<double>() / 1000);
        }
        for (const auto& value : tags["GENRE"].empty() ? genres_ : tags["GENRE"]) add_text("GENRE", value);
        for (const auto& value : genres_) add_text("RELEASE GENRE", value);
        for (const auto& value : events) add_text("RELEASE EVENT", value);
        for (const auto& value : release_tags_) add_text("DOUJINSTYLE TAG", value);
        for (const auto& value : tags["COMMENT"]) add_text("COMMENT", value);
        return result;
    }

private:
    const Json &release_, &archive_, &tracks_, &lookup_, &external_;
    Texts release_tags_, genres_, events_;
    std::string album_artist_;
    std::map<std::string, std::set<std::string>> folder_catalogs_;
};

} // namespace doujin::projection
