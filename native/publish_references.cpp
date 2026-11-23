#include "doujinstyle_build_version.hpp"
#include "catalog_ingest.hpp"
#include "audio_prefix.hpp"
#include "bangumi.hpp"
#include "catalog_zip_range.hpp"
#include "descriptor.hpp"
#include "reference_projection.hpp"
#include "sha256.hpp"
#include "mediafire_http.hpp"
#include "http_user_agent.hpp"
#include "mega_cng.hpp"
#include "mega_download_job.hpp"
#include "mediafire_download_job.hpp"
#include "archive_job_plan.hpp"
#include "archive_inspection.hpp"
#include "idle_gate.hpp"
#include "catalog_ui_date.hpp"
#include "catalog_index_plan.hpp"
#include "catalog_idle_plan.hpp"
#include "catalog_browse_step.hpp"
#include "catalog_offer_executor.hpp"
#include "catalog_index_dispatch.hpp"
#include "catalog_archive_dispatch.hpp"
#include "publication_lock.hpp"
#include "site_catalog.hpp"
#include "zip_catalog.hpp"
#include "zip_inventory.hpp"
#include "zip_name_resolution.hpp"
#include "chart_refresh.hpp"
#include "catalog_chart_job.hpp"
#include "catalog_idle_details.hpp"
#include "catalog_idle_frontend.hpp"
#include "catalog_search_frontend.hpp"
#include "catalog_chart_frontend.hpp"
#include "catalog_legacy_links.hpp"

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <iostream>
#endif

#include <algorithm>
#include <charconv>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <cstdio>
#include <filesystem>
#include <functional>
#include <fstream>
#include <memory>
#include <optional>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {
namespace fs = std::filesystem;
using doujin::Json;

constexpr std::size_t catalog_frame_limit = 32 * 1024 * 1024;
constexpr int catalog_json_depth_limit = 64;

bool read_catalog_frame(std::string& line, bool& oversized) {
    line.clear();
    oversized = false;
    // The server has a single reader. Use the CRT buffer without locking per byte.
    while (true) {
#ifdef _WIN32
        const int ch = _fgetc_nolock(stdin);
#else
        const int ch = getc_unlocked(stdin);
#endif
        if (ch == EOF) return false;
        if (ch == '\n') return true;
        if (line.size() < catalog_frame_limit) line.push_back(static_cast<char>(ch));
        else oversized = true;
    }
}

bool digits(std::string_view text) {
    return !text.empty() && text.find_first_not_of("0123456789") == text.npos;
}

bool year_month(std::string_view value) {
    return value.size() == 7 && value[4] == '/' && digits(value.substr(0, 4)) &&
        (value.substr(5, 2) >= "01" && value.substr(5, 2) <= "12") && digits(value.substr(5, 2));
}

bool valid_filename(std::string_view filename) {
    const auto dash = filename.find('-');
    return dash != filename.npos && digits(filename.substr(0, dash)) && filename.size() == dash + 29 &&
        filename.substr(dash + 1, 24).find_first_not_of("0123456789abcdef") == filename.npos &&
        filename.ends_with(".djt");
}

bool valid_placement(std::string_view relative, std::string_view filename) {
    if (relative == filename) return true;
    if (relative.size() <= filename.size() || !relative.ends_with(filename)) return false;
    const auto prefix = relative.substr(0, relative.size() - filename.size());
    return prefix == "undated/" || (prefix.size() == 8 && prefix.back() == '/' && year_month(prefix.substr(0, 7)));
}

std::string date_shard(std::string_view contents) {
    auto start = contents.find("\nDATE=");
    if (start == contents.npos) return "undated";
    contents.remove_prefix(start + 6);
    auto date = contents.substr(0, contents.find('\n'));
    if (date.size() != 10 || date[4] != '-' || date[7] != '-' || !digits(date.substr(0, 4)) ||
        !digits(date.substr(5, 2)) || !digits(date.substr(8, 2))) return "undated";
    const std::chrono::year_month_day parsed{
        std::chrono::year(std::stoi(std::string(date.substr(0, 4)))),
        std::chrono::month(static_cast<unsigned>(std::stoi(std::string(date.substr(5, 2))))),
        std::chrono::day(static_cast<unsigned>(std::stoi(std::string(date.substr(8, 2)))))};
    return parsed.ok() && date.substr(0, 4) != "0000"
        ? std::string(date.substr(0, 4)) + "/" + std::string(date.substr(5, 2)) : "undated";
}

std::vector<std::string> existing_shards(const fs::path& root) {
    std::vector<std::string> result;
    for (const auto& year : fs::directory_iterator(root)) {
        const auto y = year.path().filename().u8string();
        const std::string name(y.begin(), y.end());
        if (name.size() != 4 || !digits(name) || !year.is_directory()) continue;
        for (const auto& month : fs::directory_iterator(year.path())) {
            const auto m = month.path().filename().u8string();
            const auto shard = name + "/" + std::string(m.begin(), m.end());
            if (year_month(shard) && month.is_directory()) result.push_back(shard);
        }
    }
    std::sort(result.begin(), result.end());
    result.insert(result.begin(), "undated");
    return result;
}

std::string choose_placement(const fs::path& root, const std::string& filename, const std::string& descriptor,
                             const std::string& saved, std::optional<std::vector<std::string>>& shards) {
    if (fs::is_regular_file(root / filename)) return filename;
    if (!saved.empty() && fs::is_regular_file(root / saved)) return saved;
    if (!shards) shards = existing_shards(root);
    for (const auto& shard : *shards) {
        if (fs::is_regular_file(root / shard / filename)) return shard + "/" + filename;
    }
    return saved.empty() ? date_shard(descriptor) + "/" + filename : saved;
}

// A single native projection of Python's output.resolve()/normcase() key.
// Both the ordinary idle publisher and Bangumi must find existing structured
// placements under exactly the same stable catalog root identity.
std::pair<std::string, std::string> canonical_output_and_key(const std::string& value) {
    const auto input = fs::path(std::u8string(value.begin(), value.end()));
    const auto output = fs::weakly_canonical(input);
    const auto bytes = output.u8string();
#ifdef _WIN32
    const auto& path = output.native();
    const auto required = LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE,
        path.c_str(), static_cast<int>(path.size()), nullptr, 0, nullptr, nullptr, 0);
    if (!required) throw std::runtime_error("Could not normalize the publication path");
    std::wstring lower(static_cast<std::size_t>(required), L'\0');
    if (!LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, path.c_str(),
        static_cast<int>(path.size()), lower.data(), required, nullptr, nullptr, 0))
        throw std::runtime_error("Could not normalize the publication path");
    const auto key = fs::path(lower).u8string();
#else
    const auto& key = bytes;
#endif
    return {{bytes.begin(), bytes.end()}, {key.begin(), key.end()}};
}

bool write_reference(const fs::path& path, std::string_view data) {
    if (fs::is_regular_file(path)) {
        std::ifstream original(path, std::ios::binary | std::ios::ate);
        const auto length = static_cast<std::streamsize>(data.size());
        if (original.tellg() == std::streampos(length)) {
            std::string contents(data.size(), '\0');
            if (original.seekg(0).read(contents.data(), length) && contents == data &&
                original.peek() == std::char_traits<char>::eof()) return false;
        }
    }
    fs::create_directories(path.parent_path());
    static thread_local std::mt19937_64 random(std::random_device{}());
    auto temp = path;
    temp += "." + std::to_string(random()) + ".tmp";
#ifdef _WIN32
    std::FILE* file = nullptr;
    if (_wfopen_s(&file, temp.c_str(), L"wbx") != 0) file = nullptr;
#else
    std::FILE* file = std::fopen(temp.c_str(), "wbx");
#endif
    if (!file) throw std::runtime_error("Could not create a temporary track file");
    try {
        const auto written = std::fwrite(data.data(), 1, data.size(), file);
        const auto closed = std::fclose(file);
        if (written != data.size() || closed) throw std::runtime_error("Could not write a complete track file");
#ifdef _WIN32
        if (!MoveFileExW(temp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            throw std::runtime_error("Could not replace a track file (Windows error " + std::to_string(GetLastError()) + ")");
#else
        fs::rename(temp, path);
#endif
    } catch (...) {
        std::error_code ignored;
        fs::remove(temp, ignored);
        throw;
    }
    return true;
}

struct Reference {
    std::string filename, descriptor, relative;
    bool changed = false;
};

Json zip_rows(const std::vector<doujin::ZipCatalogMember>& rows) {
    if (rows.size() > 32768) throw std::runtime_error("Too many ZIP filename records");
    std::vector<doujin::zip_name::Evidence> evidence;
    evidence.reserve(rows.size());
    for (const auto& member : rows)
        evidence.push_back({member.name, member.flags, member.unicode_path, member.bad_unicode_path});
    const auto names = doujin::zip_name::resolve(evidence);
    auto result = Json::array();
    for (std::size_t at = 0; at < rows.size(); ++at) {
        const auto& member = rows[at];
        const auto& name = names[at];
        result.push_back(Json::array({doujin::encode_hex(member.name), member.packed, member.size,
            member.crc, member.header, member.method, member.flags,
            doujin::encode_hex(member.unicode_path), member.bad_unicode_path,
            name.name, name.encoding, name.warning, name.alternate}));
    }
    return result;
}

#ifdef _WIN32
class CatalogZlib {
public:
    CatalogZlib() = default;
    ~CatalogZlib() { if (module_) FreeLibrary(module_); }
    CatalogZlib(const CatalogZlib&) = delete;
    CatalogZlib& operator=(const CatalogZlib&) = delete;

    bool load(const std::string& name) {
        if (module_) return api.init && api.inflate_chunk && api.end && api.checksum;
        const fs::path path(std::u8string(name.begin(), name.end()));
        if (!path.is_absolute() || path.filename() != L"zlib1.dll") return false;
        module_ = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!module_) return false;
        api = {reinterpret_cast<decltype(&inflateInit2_)>(GetProcAddress(module_, "inflateInit2_")),
               reinterpret_cast<decltype(&inflate)>(GetProcAddress(module_, "inflate")),
               reinterpret_cast<decltype(&inflateEnd)>(GetProcAddress(module_, "inflateEnd")),
               reinterpret_cast<decltype(&crc32)>(GetProcAddress(module_, "crc32"))};
        return api.init && api.inflate_chunk && api.end && api.checksum;
    }
    doujin::DeflateApi api{};
private:
    HMODULE module_ = nullptr;
};

Json archive_counts(const doujin::CatalogZipRange& reader) {
    return {{"size", reader.size}, {"fetched", reader.fetched}, {"requests", reader.requests},
            {"etag", reader.etag}, {"modified", reader.modified}, {"disposition", reader.disposition}};
}
#endif

class Publisher {
public:
    Json run(const Json& request, const std::function<void(const Json&)>& on_event = {}) {
        const auto operation = request.at("operation").get<std::string>();
        // No alternate unjournaled POST RPC, on any platform. Journaled
        // catalog offer batch is the sole production network POST path.
        if (operation == "site_release_offers")
            throw std::runtime_error("Unjournaled single-release POST disabled; use native catalog offer batch");
        if (operation == "ui_date")
            return doujin::ui_date::choose(request.at("release"));
        if (operation == "provider_capability") {
            const auto& offer = request.at("offer");
            if (!offer.is_object() || offer.dump().size() > 65536)
                throw std::runtime_error("Invalid provider offer metadata");
            const auto supported = doujin::index_plan::usable(
                request.at("url").get<std::string>(),
                request.at("host").get<std::string>(), offer.dump());
            return {{"supported", supported}};
        }
        if (operation == "idle_wait") {
            if (request.contains("fixture_idle_ms")) {
                const auto ms = request.at("fixture_now_ms").get<std::uint64_t>();
                const auto since = std::chrono::steady_clock::time_point{std::chrono::milliseconds(ms)};
                return {{"ready", idle_gate_.ready(request.at("fixture_idle_ms").get<std::uint64_t>(), since)}};
            }
#ifdef _WIN32
            for (;;) {
                LASTINPUTINFO last{};
                last.cbSize = sizeof last;
                if (!GetLastInputInfo(&last)) throw std::runtime_error("Windows input idle query failed");
                const DWORD idle = GetTickCount() - last.dwTime;
                if (idle_gate_.ready(idle, std::chrono::steady_clock::now()))
                    return {{"ready", true}};
                Sleep(1000);
            }
#else
            throw std::runtime_error("Windows idle scheduling unavailable on this platform");
#endif
        }
        if (operation == "site_resource") {
            const auto url = request.at("url").get<std::string>();
            const auto kind = request.at("kind").get<std::string>();
            const auto cap = request.value("cap", std::size_t{64 * 1024});
            if (!doujin::discovery::site_url(url) || cap == 0 || cap > 96 * 1024 ||
                (kind != "art" && kind != "robots"))
                throw std::runtime_error("Invalid bounded native site resource request");
            if (kind == "robots" && url != "https://doujinstyle.com/robots.txt")
                throw std::runtime_error("Invalid robots.txt origin");
            if (kind == "art" && (url.find('?') != url.npos || url.find('#') != url.npos ||
                (url.find("/thumbs/") == url.npos && url.find("/favicon") == url.npos &&
                 url.find("/wp-content/") == url.npos)))
                throw std::runtime_error("Site artwork URL is not an approved path");
            doujin::discovery::ReleaseDocument document{};
            if (request.contains("fixture_body")) {
                document = {request.value("fixture_status", 200u),
                    read_hex(request, "fixture_body", cap), url, ""};
            } else {
#ifdef _WIN32
                if (!site_http_) site_http_ = std::make_unique<doujin::MediaFireHttp>();
                document = site_http_->catalog_document(url, cap, doujin::discovery::site_url);
#else
                throw std::runtime_error("Native site HTTPS requires Windows");
#endif
            }
            if (document.url != url) return {{"status", 302}, {"body", ""}};
            return {{"status", document.status}, {"body", doujin::encode_hex(document.body)}};
        }
        if (operation == "site_parse") {
            const auto body = read_hex(request, "html", doujin::catalog_html_limit);
            const auto kind = request.at("kind").get<std::string>();
            if (kind == "listing") return doujin::discovery::listing(body, request.value("limit", std::size_t{20}),
                                                                     request.value("forms", false));
            if (kind == "browse_links") return doujin::discovery::browse_links(body,
                request.at("url").get_ref<const std::string&>(), request.value("referrer_policy", ""));
            if (kind == "charts") return doujin::discovery::charts(body);
            if (kind == "detail") return doujin::discovery::detail(body, request.at("id").get<std::int64_t>(),
                                                                   request.value("url", ""), request.value("policy", ""));
            if (kind == "offers") {
                const auto id = request.at("id").get<std::int64_t>();
                const auto& responses = request.at("responses");
                std::size_t next = 0;
                doujin::discovery::ReleaseOffers release(id, {200, body, request.value("url", ""),
                                                               request.value("policy", "")});
                auto result = release.resolve(request.value("all_forms", false), doujin::discovery::site_url,
                    [&](const auto&, const auto&, const auto&, const auto&) -> doujin::discovery::OfferResponse {
                        const auto& response = responses.at(next++);
                        if (response.contains("error")) throw std::runtime_error(response.at("error").get<std::string>());
                        return {response.at("status").get<unsigned>(), response.value("location", "")};
                    });
                result["posts"] = next;
                return result;
            }
            throw std::runtime_error("Unknown DoujinStyle page kind");
        }
        if (operation == "zip_name_resolve") {
            const auto& rows = request.at("records");
            if (!rows.is_array() || rows.size() > 4096)
                throw std::runtime_error("Invalid ZIP filename evidence count");
            std::vector<std::string> names, extras;
            std::vector<std::uint16_t> flags;
            std::vector<bool> invalid;
            names.reserve(rows.size()); extras.reserve(rows.size());
            flags.reserve(rows.size()); invalid.reserve(rows.size());
            for (const auto& record : rows) {
                if (!record.is_array() || record.size() != 4)
                    throw std::runtime_error("Invalid ZIP filename evidence fields");
                names.push_back(doujin::decode_hex(record.at(0).get_ref<const std::string&>(), 4096));
                if (!record.at(1).is_number_integer() || record.at(1).get<std::int64_t>() < 0 ||
                    record.at(1).get<std::int64_t>() > UINT16_MAX)
                    throw std::runtime_error("ZIP filename flags out of range");
                flags.push_back(record.at(1).get<std::uint16_t>());
                extras.push_back(doujin::decode_hex(record.at(2).get_ref<const std::string&>(), 4096));
                invalid.push_back(record.at(3).get<bool>());
            }
            std::vector<doujin::zip_name::Evidence> evidence;
            evidence.reserve(rows.size());
            for (std::size_t at = 0; at < rows.size(); ++at)
                evidence.push_back({names[at], flags[at], extras[at], invalid[at]});
            const auto resolved = doujin::zip_name::resolve(evidence);
            auto result = Json::array();
            for (const auto& item : resolved)
                result.push_back({item.name, item.encoding, item.warning,
                                  item.alternate.empty() ? Json() : Json(item.alternate)});
            return result;
        }
        if (operation == "zip_inventory") return doujin::zip_inventory::project(request.at("members"));
        if (operation == "zip_catalog_layout") {
            const auto tail = read_hex(request, "tail", doujin::zip_catalog_max_tail);
            const auto layout = doujin::zip_catalog_layout(request.at("size").get<std::uint64_t>(), tail);
            return layout ? Json::array({layout->start, layout->bytes, layout->entries}) : Json();
        }
        if (operation == "zip_catalog_members") {
            const auto directory = read_hex(request, "directory", doujin::zip_catalog_max_directory);
            const auto rows = doujin::zip_catalog_members(directory, request.at("start").get<std::uint64_t>(),
                                                          request.at("entries").get<std::uint64_t>());
            return rows ? zip_rows(*rows) : Json();
        }
        if (operation == "zip_catalog_payload") {
            const auto local = read_hex(request, "local", 30 + 4096);
            const auto name = read_hex(request, "name", 4096);
            const auto payload = doujin::zip_catalog_payload(local, name,
                request.at("offset").get<std::uint64_t>(), request.at("boundary").get<std::uint64_t>(),
                request.at("packed").get<std::uint64_t>(), request.at("size").get<std::uint64_t>(),
                request.at("crc").get<std::uint32_t>(), request.at("method").get<std::uint16_t>(),
                request.at("flags").get<std::uint16_t>());
            return payload ? Json(*payload) : Json();
        }
        if (operation == "audio_prefix") {
            const auto data = read_hex(request, "prefix", 4096);
            return doujin::catalog_audio_prefix(data, request.at("suffix").get_ref<const std::string&>(),
                                               request.at("file_size").get<std::uint64_t>());
        }
#ifdef _WIN32
        if (operation == "catalog_offer_batch" || operation == "catalog_index_dispatch") {
            const auto db_path = request.at("db").get<std::string>();
            auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const bool all_forms = request.value("all_forms", false);
            const auto fetch = [all_forms, db_path](const doujin::offer_executor::Task& task, std::stop_token stop) -> Json {
                if (stop.stop_requested())
                    throw std::runtime_error("Native offer job cancelled");
                // Refuse to replay a POST from an earlier interrupted worker.
                // The SQLite read connection is released before network I/O.
                {
                    auto audit = doujin::connect(db_path, SQLITE_OPEN_READONLY);
                    if (doujin::post_attempt::pending(audit.get(), task.id))
                        return {{"observation", nullptr}, {"offers", Json::array()},
                            {"error", "Previous POST outcome unresolved; manual review required"}};
                }
                // Per-release owner is stable for multiple forms within this
                // job, but cannot coincide with an owner from an earlier run.
                std::random_device entropy;
                std::string owner;
                for (int i = 0; i != 4; ++i) {
                    char hex[16];
                    std::snprintf(hex, sizeof hex, "%08x", entropy());
                    owner += hex;
                }
                // Cookies for a detail GET and its POST must belong to the
                // same release, not leak to another concurrent release.
                doujin::MediaFireHttp session;
                const auto document = session.catalog_document(task.url, doujin::catalog_html_limit,
                                                                 doujin::discovery::site_url);
                if (document.status == 429)
                    return {{"observation", nullptr}, {"offers", Json::array()},
                            {"error", "DoujinStyle release returned HTTP 429"}, {"rate_limited", true}};
                if (document.status != 200)
                    throw std::runtime_error("DoujinStyle release returned HTTP " + std::to_string(document.status));
                doujin::discovery::ReleaseOffers release(task.id, document);
                auto result = release.resolve(all_forms, doujin::discovery::site_url,
                    [&](const auto& action, const auto& source, const auto& policy, const auto& body) {
                        if (stop.stop_requested())
                            throw std::runtime_error("Native offer job cancelled before POST");
                        const auto started = std::chrono::duration_cast<std::chrono::seconds>(
                            std::chrono::system_clock::now().time_since_epoch()).count();
                        // Durable marker MUST commit before bytes can leave the host.
                        doujin::post_attempt::before_post(db_path, task.id, owner, started);
                        if (stop.stop_requested())
                            throw std::runtime_error("Native offer job cancelled after POST intent");
                        return session.catalog_post(action, source, policy, body);
                    });
                if (result.at("error").is_string() &&
                    result.at("error").get_ref<const std::string&>().find("HTTP 429") != std::string::npos)
                    result["rate_limited"] = true;
                return result;
            };
            if (operation == "catalog_index_dispatch")
                return doujin::index_dispatch::run(db.get(), request.at("ids"),
                    request.value("workers", 3), now, fetch,
                    request.value("stream_events", false) ? on_event :
                        std::function<void(const Json&)>{[](const Json&) {}});
            return doujin::offer_executor::run(db.get(), request.at("ids"), request.value("workers", 3), now,
                fetch, [](const Json&) {});
        }
        if (operation == "site_release") {
            const auto url = request.at("url").get<std::string>();
            const auto id = request.at("id").get<std::int64_t>();
            if (!doujin::discovery::site_url(url) || doujin::discovery::release_id(url) != id)
                throw std::runtime_error("Unexpected DoujinStyle release URL");
            std::optional<doujin::MediaFireHttp> session;
            std::optional<doujin::discovery::ReleaseOffers> release;
            try {
                session.emplace();
                const auto document = session->catalog_document(url, doujin::catalog_html_limit,
                                                                 doujin::discovery::site_url);
                if (document.status != 200) return {{"status", document.status}};
                release.emplace(id, document);
            } catch (const std::runtime_error&) {
                throw;
            }
            return release->page;
        }
        if (operation == "mediafire_resolve" || operation == "mediafire_range") {
            if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
            if (operation == "mediafire_resolve") return mediafire_->resolve(request);
            return mediafire_->range(request);
        }
        if (operation == "archive_inspect_job") {
            const auto url = request.at("url").get<std::string>();
            const auto size = request.at("budget").get<std::size_t>();
            const auto ranges = request.at("requests").get<unsigned>();
            if (!size || size > 2 * 1024 * 1024 || !ranges || ranges > 64)
                throw std::runtime_error("Invalid native archive inspection budget");
            Json args = {{"url", url}, {"budget", size}, {"requests", ranges},
                         {"suffix", request.value("suffix", std::size_t{65557})},
                         {"host_zlib", request.at("host_zlib")}};
            Json opened;
            bool mega = false;
            if ((url.starts_with("https://mega.nz/") || url.starts_with("https://www.mega.nz/") ||
                 url.starts_with("https://mega.co.nz/") || url.starts_with("https://www.mega.co.nz/"))) {
                mega = true;
                opened = open_mega_zip(args);
            } else {
                if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
                if (doujin::mediafire_index_url(url, true)) {
                    const auto resolved = mediafire_->resolve({{"url", url}, {"referrer", request.value("referrer", "")}});
                    if (resolved.at("status") != 200)
                        throw std::runtime_error("MediaFire archive link resolution failed");
                    args["url"] = resolved.at("url");
                    args["referrer"] = resolved.value("referrer", "");
                    args["policy"] = resolved.value("policy", "");
                } else if (!doujin::mediafire_index_url(url, false)) {
                    throw std::runtime_error("Unsupported archive inspection destination");
                }
                opened = open_mediafire_zip(args);
            }
            if (opened.is_null()) throw std::runtime_error("Native archive reader unavailable");
            if (!zip_ || opened.value("format", "") != "zip") {
                Json report = {{"format", opened.value("format", "unknown")},
                               {"archive_bytes", opened.at("size")},
                               {"inspection", "No ZIP directory available"}};
                report["archive_http"] = {{"requests", opened.at("requests")},
                                         {"body_bytes", opened.at("fetched")}};
                if (mega) report["archive_http"]["mega_mac"] = "not_verified_for_range_probes";
                return report;
            }
            auto report = doujin::archive_inspection::inspect(*zip_,
                {request.value("headers", false), request.value("cover", false), ranges, 96 * 1024});
            report["response_filename"] = opened.value("filename", "");
            const auto filename = opened.value("filename", std::string{});
            const auto name = filename.substr(filename.find_last_of("/\\") == filename.npos
                ? 0 : filename.find_last_of("/\\") + 1);
            doujin::archive_inspection::catalog_candidate(report["catalog_id_candidate_evidence"],
                doujin::archive_inspection::catalog_number(name), "archive_filename", name);
            report["catalog_id_candidates"] = Json::array();
            for (auto it = report["catalog_id_candidate_evidence"].begin();
                 it != report["catalog_id_candidate_evidence"].end(); ++it)
                report["catalog_id_candidates"].push_back(it.key());
            report["archive_http"]["transport"] = "winhttp";
            report["archive_http"]["member_reader"] = "native-batch";
            if (mega) report["archive_http"]["mega_mac"] = "not_verified_for_range_probes";
            return report;
        }
        if (operation == "mediafire_zip_open") return open_mediafire_zip(request);
        if (operation == "mega_zip_open") return open_mega_zip(request);
        if (operation == "archive_download_job") {
            const auto id = request.at("id").get<std::int64_t>();
            auto db = doujin::connect(request.at("db").get<std::string>(), SQLITE_OPEN_READONLY);
            const auto selected = doujin::archive_job::select(db.get(), id);
            // Drop the SQLite connection before any network I/O or file writes.
            db.reset();
            if (selected.at("status") != "ready") return Json::parse(selected.dump());
            const auto& copy = selected.at("copy");
            if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
            nlohmann::json saved;
            if (copy.at("host") == "mediafire.com") {
                saved = doujin::mediafire::download(*mediafire_, copy.at("url").get<std::string>(),
                    request.value("referrer", ""), copy.value("filename", ""),
                    request.at("output").get<std::string>(), static_cast<std::uint64_t>(id));
            } else if (copy.at("host") == "mega.nz") {
                saved = doujin::mega::download(*mediafire_, copy.at("url").get<std::string>(),
                    request.at("output").get<std::string>(), static_cast<std::uint64_t>(id));
            } else {
                throw std::runtime_error("Native archive job selected an unsupported provider");
            }
            return Json::parse(nlohmann::json{{"status", "saved"}, {"archive", std::move(saved)}}.dump());
        }
        if (operation == "mediafire_download_archive") {
            if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
            const auto id = request.at("id").get<std::int64_t>();
            if (id <= 0) throw std::runtime_error("Invalid MediaFire download release ID");
            return doujin::mediafire::download(*mediafire_, request.at("url").get<std::string>(),
                request.at("referrer").get<std::string>(), request.value("filename", ""),
                request.at("output").get<std::string>(), static_cast<std::uint64_t>(id));
        }
        if (operation == "mega_download_archive") {
            if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
            const auto id = request.at("id").get<std::int64_t>();
            if (id < 0) throw std::runtime_error("Invalid release ID for MEGA download");
            return doujin::mega::download(*mediafire_, request.at("url").get<std::string>(),
                request.at("output").get<std::string>(), static_cast<std::uint64_t>(id));
        }
        if (operation == "mediafire_zip_probes" || operation == "mega_zip_probes") {
            if (active_zip_ != (operation == "mega_zip_probes" ? ZipProvider::mega : ZipProvider::mediafire))
                throw std::runtime_error("ZIP provider changed during archive probe");
            return probe_mediafire_zip(request);
        }
        if (operation == "mediafire_zip_member" || operation == "mega_zip_member") {
            if (active_zip_ != (operation == "mega_zip_member" ? ZipProvider::mega : ZipProvider::mediafire))
                throw std::runtime_error("ZIP provider changed during member read");
            if (!zip_) throw std::runtime_error("No open native MediaFire ZIP");
            const auto limit = request.at("limit").get<std::size_t>();
            const auto budget = request.at("budget").get<std::size_t>();
            const auto requests = request.at("requests").get<unsigned>();
            if (!limit || limit > 96 * 1024 + 1 || budget > 2 * 1024 * 1024 || requests > 64)
                throw std::runtime_error("Invalid native ZIP member read limit");
            zip_->begin_probe(budget, requests);
            Json result = Json::object();
            try {
                result["data"] = doujin::encode_hex(zip_->member(request.at("offset").get<std::uint64_t>(), limit));
            } catch (const std::runtime_error& error) {
                result["error"] = error.what();
            }
            result.update(archive_counts(*zip_));
            return result;
        }
#endif
        const auto db_path = request.at("db").get<std::string>();
        if (operation == "catalog_browse_step") {
            auto db = doujin::connect(request.at("db").get<std::string>(), SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const auto fetch = [&](const std::string& url, const std::string& referrer,
                                   const std::string& policy) -> std::pair<std::string, std::string> {
                (void)doujin::browse_step::offset(url);
                if (request.contains("fixture_pages")) return {site_body(request, url), ""};
#ifdef _WIN32
                if (!site_http_) site_http_ = std::make_unique<doujin::MediaFireHttp>();
                const auto document = site_http_->catalog_document(url, doujin::catalog_html_limit,
                    doujin::discovery::site_url, referrer, policy);
                if (document.status != 200 || document.url != url)
                    throw std::runtime_error("Idle browse HTTP response or redirect rejected");
                return {document.body, document.policy};
#else
                (void)referrer;
                (void)policy;
                throw std::runtime_error("Native idle browse HTTPS requires Windows");
#endif
            };
            return doujin::browse_step::run(db.get(), now, request.value("state", Json()), fetch);
        }
        if (operation == "catalog_archive_dispatch") {
            const auto& jobs = request.at("jobs");
            const auto ids = request.value("ids", Json());
            const auto idle = request.value("idle", false);
            const auto cover = request.value("cover", false);
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
#ifndef _WIN32
            if (!jobs.empty())
                throw std::runtime_error("Native WinHTTP archive probing requires Windows");
#endif
            const auto fetch = [&, idle](const doujin::archive_dispatch::Task& task,
                                         std::stop_token stop) -> Json {
                if (stop.stop_requested()) throw std::runtime_error("Archive job cancelled");
#ifdef _WIN32
                // Every archive probe has its OWN transport/ZIP reader; the
                // publisher's IPC frontend remains on the coordinator thread.
                if (idle) (void)run({{"operation", "idle_wait"}});
                if (stop.stop_requested()) throw std::runtime_error("Archive job cancelled");
                Publisher peer;
                auto archive = peer.run({{"operation", "archive_inspect_job"},
                    {"url", task.url}, {"referrer", task.referrer},
                    {"host_zlib", request.at("host_zlib")},
                    {"headers", task.headers}, {"cover", task.cover},
                    {"budget", 2 * 1024 * 1024}, {"requests", 12}, {"suffix", 65557}});
                const auto payload = archive.value("cover_payload", Json());
                archive.erase("cover_payload");
                return {{"archive", std::move(archive)}, {"cover_payload", payload},
                        {"error", nullptr}};
#else
                (void)task;
                (void)idle;
                throw std::runtime_error("Native WinHTTP archive probing requires Windows");
#endif
            };
            return doujin::archive_dispatch::run(db.get(), jobs, ids,
                idle ? 1 : request.value("workers", 3), now, fetch,
                request.value("stream_events", false) ? on_event :
                    std::function<void(const Json&)>{[](const Json&) {}},
                {}, request.value("headers", true), cover);
        }
        if (operation == "catalog_idle_detail_job") {
            auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const auto fetch = [&](std::int64_t id) -> Json {
                const std::string url = "https://doujinstyle.com/?p=page&type=1&id=" + std::to_string(id);
                if (request.contains("fixture_pages")) {
                    const auto& pages = request.at("fixture_pages");
                    if (!pages.is_object() || pages.size() > doujin::idle_plan::max_jobs ||
                        !pages.contains(std::to_string(id)) ||
                        !pages.at(std::to_string(id)).is_string())
                        throw std::runtime_error("Invalid native idle detail fixture");
                    const auto body = doujin::decode_hex(
                        pages.at(std::to_string(id)).get_ref<const std::string&>(),
                        doujin::catalog_html_limit);
                    return doujin::discovery::detail(body, id, url, "");
                }
#ifdef _WIN32
                // Native IdleGate enforces human-idle and per-request spacing.
                // No SQLite writer transaction spans this network operation.
                (void)run({{"operation", "idle_wait"}});
                auto parsed = run({{"operation", "site_release"}, {"url", url}, {"id", id}});
                if (parsed.contains("status"))
                    throw std::runtime_error("DoujinStyle release returned HTTP " +
                        std::to_string(parsed.at("status").get<unsigned>()));
                return parsed;
#else
                throw std::runtime_error("Native idle detail HTTPS requires Windows");
#endif
            };
            return doujin::idle_detail::run(db.get(), now, fetch,
                request.value("stream_events", false) ? on_event :
                    std::function<void(const Json&)>{[](const Json&) {}});
        }
        if (operation == "catalog_idle_plan") {
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
            return doujin::idle_plan::select(db.get(), request.at("layers").get<int>());
        }
        if (operation == "catalog_post_attempts") {
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
            auto result = Json::array();
            auto table = doujin::prepare(db.get(), "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_post_attempts'");
            if (!doujin::next(table.get())) return result;
            auto rows = doujin::prepare(db.get(), "SELECT release_id,started_at,post_count FROM catalog_post_attempts ORDER BY started_at,release_id LIMIT 1000");
            while (doujin::next(rows.get())) result.push_back({
                {"id", sqlite3_column_int64(rows.get(),0)},
                {"started_at", sqlite3_column_int64(rows.get(),1)},
                {"post_count", sqlite3_column_int(rows.get(),2)}});
            return result;
        }
#ifndef _WIN32
        if (operation == "catalog_index_dispatch") {
            auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            // Only already-known archives can be dispatched in the portable
            // helper. Live offer GET/POST requires the Windows WinHTTP backend.
            auto plan = doujin::index_plan::select(db.get(), request.at("ids"), now, true);
            if (!plan.at("offers").empty())
                throw std::runtime_error("Native WinHTTP offer discovery is required");
            return doujin::index_dispatch::run(db.get(), request.at("ids"),
                request.value("workers", 3), now,
                [](const doujin::offer_executor::Task&, std::stop_token) -> Json {
                    throw std::runtime_error("Native WinHTTP offer discovery is required");
                }, request.value("stream_events", false) ? on_event :
                        std::function<void(const Json&)>{[](const Json&) {}});
        }
#endif
        if (operation == "catalog_index_plan") {
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            return doujin::index_plan::select(db.get(), request.at("ids"), now,
                                              request.value("offers", true));
        }
        if (operation == "archive_download_plan") {
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
            return doujin::archive_job::select(db.get(), request.at("id").get<std::int64_t>(),
                request.value("exclude", ""));
        }
        if (operation == "bangumi") return bangumi_job(db_path, request);
        if (operation == "bangumi_idle") {
            const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
            return doujin::bangumi::idle_candidates(db.get(), doujin::bangumi::unix_seconds());
        }
        if (operation == "publish") return publish(db_path, request);
        if (operation == "publication_snapshot") return publication_snapshot(db_path, request);
        if (operation == "publish_release") return publish_release(db_path, request);
        if (operation == "publish_many") return publish_many(db_path, request);
        if (operation == "catalog_chart_job") {
            const auto period = request.at("period").get<std::string>();
            const auto ttl = doujin::chart_job::ttl(period);
            const bool prefetch = period == "prefetch";
            const auto path = request.at("db").get<std::string>();
            const auto refresh = request.value("refresh", false);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            auto state = run({{"operation", "site_chart_snapshot"}, {"db", path}});
            auto saved = state.at("snapshot");
            if (!doujin::chart_job::valid_snapshot(saved)) saved = nullptr;
            const auto fetched = state.at("fetched").get<std::int64_t>();
            auto stages = Json::array();
            auto warnings = Json::array();
            const auto show = [&](const Json& page, const std::string& source) {
                auto db = doujin::connect(path, SQLITE_OPEN_READWRITE);
                // Database joins and optional prefetch seeding complete before UI notification.
                const auto event = doujin::chart_job::stage(db.get(), page, period, source, prefetch, now);
                if (request.value("stream_events", false) && on_event) on_event(event);
                stages.push_back(event);
            };
            const bool fresh = saved.is_object() && now >= fetched && now - fetched < ttl;
            if (!prefetch && saved.is_object()) show(saved, fresh ? "cached" : "cached, refresh due");
            if (refresh || !fresh || saved.is_null()) {
                try {
                    const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::seconds(saved.is_null() ? 10 : 0);
                    Json updated;
                    do {
                        Json args{{"operation", "site_chart_refresh"}, {"db", path}, {"previous", fetched}};
                        if (request.contains("html")) args["html"] = request["html"];
                        const auto reply = run(args);
                        if (!reply.at("busy").get<bool>()) {
                            updated = reply.at("snapshot");
                            break;
                        }
                        if (saved.is_object() || std::chrono::steady_clock::now() >= deadline) break;
                        std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    } while (true);
                    if (!doujin::chart_job::valid_snapshot(updated)) {
                        if (saved.is_null()) throw std::runtime_error("Another worker is refreshing the charts. Open again shortly");
                    } else if (updated != saved) {
                        show(updated, "updated");
                        saved = std::move(updated);
                    }
                } catch (const std::exception& error) {
                    if (saved.is_null()) throw;
                    warnings.push_back(std::string("Chart refresh failed (") + error.what() + "). Showing saved rankings.");
                }
            }
            if (prefetch && saved.is_object()) show(saved, "prefetch");
            return {{"stages", std::move(stages)}, {"warnings", std::move(warnings)}};
        }
        if (operation == "site_chart_snapshot" || operation == "site_chart_refresh") {
            const auto path = request.at("db").get<std::string>();
            if (path.empty() || path == "None") throw std::runtime_error("Chart catalog path missing");
            const auto db = doujin::connect(path, operation == "site_chart_snapshot"
                                              ? SQLITE_OPEN_READONLY : SQLITE_OPEN_READWRITE);
            const auto current = doujin::discovery::snapshot(db.get(), std::string(doujin::discovery::guides));
            const auto snapshot_reply = [](const auto& saved, bool busy, bool updated) {
                return Json{{"snapshot", saved.page.is_object() ? saved.page : Json()},
                            {"fetched", saved.fetched}, {"busy", busy}, {"updated", updated}};
            };
            if (operation == "site_chart_snapshot") return snapshot_reply(current, false, false);
            const auto previous = request.at("previous").get<std::int64_t>();
            if (previous < 0) throw std::runtime_error("Invalid previous chart snapshot timestamp");
            if (current.fetched > previous) return snapshot_reply(current, false, false);
            doujin::discovery::ChartRefreshLease lease(path);
            if (!lease.acquired()) return snapshot_reply(current, true, false);
            // Recheck after taking the lease; another worker may have finished meanwhile.
            const auto saved = doujin::discovery::snapshot(db.get(), std::string(doujin::discovery::guides));
            if (saved.fetched > previous) return snapshot_reply(saved, false, false);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const auto updated_at = std::max<std::int64_t>(now, saved.fetched + 1);
            const auto fetch = [&](const std::string& url) { return site_body(request, url); };
            (void)doujin::discovery::chart_page(db.get(), updated_at, true, fetch);
            const auto updated = doujin::discovery::snapshot(db.get(), std::string(doujin::discovery::guides));
            return snapshot_reply(updated, false, true);
        }
        if (operation == "site_search" || operation == "site_search_job_page" || operation == "site_search_job" || operation == "site_charts") {
            auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            const auto now = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const auto fetch = [&](const std::string& url) { return site_body(request, url); };
            if (operation == "site_search")
                return doujin::discovery::search_page(db.get(), request.at("url").get<std::string>(), now,
                                                       request.value("refresh", false), fetch);
            if (operation == "site_search_job_page")
                return doujin::discovery::search_job_page(db.get(), now, request, fetch);
            if (operation == "site_search_job")
                return doujin::discovery::search_job(db.get(), now, request, fetch,
                    request.value("stream_events", false) ? on_event : std::function<void(const Json&)>{});
            return doujin::discovery::chart_page(db.get(), now, request.value("persist", true), fetch);
        }
        const auto flags = SQLITE_OPEN_READWRITE | (operation == "initialize" ? SQLITE_OPEN_CREATE : 0);
        auto db = doujin::connect(db_path, flags);
        if (operation == "initialize") {
            doujin::initialize_catalog(db.get(), request);
            return nullptr;
        }
        return doujin::ingest(db.get(), operation, request);
    }

private:
    doujin::IdleGate idle_gate_;
    Json bangumi_job(const std::string& db_path, const Json& request) {
        const auto& ids = request.at("ids");
        if (!ids.is_array() || ids.empty() || ids.size() > doujin::bangumi::batch_limit)
            throw std::runtime_error("Bangumi lookup requires one to six release IDs");
        for (const auto& id : ids)
            if (!id.is_number_integer() || id < 0 || (id.is_number_unsigned() &&
                id.get<std::uint64_t>() > static_cast<std::uint64_t>(INT64_MAX)))
                throw std::runtime_error("Invalid Bangumi release ID");

        const auto source = publication_output(request).u8string();
        const auto [output_name, root_key] = canonical_output_and_key(
            std::string(source.begin(), source.end()));
        const auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
        auto log = Json::array(), references = Json::array(), calls = Json::array();
        const auto fixture = request.find("responses");
#ifdef _WIN32
        // Keep the transport scoped to this job rather than global mutable HTTP state.
        std::unique_ptr<doujin::MediaFireHttp> http;
#endif
        const auto fetch = [&](const std::string& path, const Json& body) -> doujin::bangumi::Response {
            if (fixture != request.end()) {
                const auto index = calls.size();
                calls.push_back({{"path", path}, {"body", body}});
                const auto& reply = fixture->at(index);
                return {reply.at("status").get<unsigned>(), reply.value("raw", reply.value("json", Json{}).dump())};
            }
#ifdef _WIN32
            if (!http) http = std::make_unique<doujin::MediaFireHttp>(doujin::http_user_agent, 6000);
            auto [status, content] = http->json_request(doujin::bangumi::origin + path,
                body.is_null() ? std::string{} : body.dump(), doujin::bangumi::body_limit);
            return {status, std::move(content)};
#else
            throw std::runtime_error("Native Bangumi HTTPS is available on Windows");
#endif
        };
        doujin::bangumi::enrich(db.get(), ids, fetch, [&](auto line) { log.push_back(std::move(line)); });
        for (const auto& id : ids) {
            doujin::PublicationLock lock(fs::path(std::u8string(db_path.begin(), db_path.end())));
            const auto published = publish_release(db_path, {{"id", id}, {"output", output_name}, {"root", root_key}});
            for (const auto& entry : published)
                if (entry.at(1) == true) references.push_back(Json::array({id, entry.at(0)}));
        }
        return {{"messages", std::move(log)}, {"references", std::move(references)}, {"requests", std::move(calls)}};
    }

    std::string site_body(const Json& request, [[maybe_unused]] const std::string& url) {
        if (request.contains("fixture_pages")) {
            const auto& pages = request.at("fixture_pages");
            if (!pages.is_object() || pages.size() > 3)
                throw std::runtime_error("Invalid native search fixture pages");
            const auto found = pages.find(url);
            if (found == pages.end()) throw std::runtime_error("Fixture page unavailable");
            return doujin::decode_hex(found->get<std::string>(), doujin::catalog_html_limit);
        }
        if (request.contains("html")) return read_hex(request, "html", doujin::catalog_html_limit);
#ifdef _WIN32
        if (!site_http_) site_http_ = std::make_unique<doujin::MediaFireHttp>();
        return site_http_->catalog_page(url, doujin::catalog_html_limit, doujin::discovery::site_url);
#else
        throw std::runtime_error("Native DoujinStyle HTTPS is available on Windows");
#endif
    }

#ifdef _WIN32
    std::unique_ptr<doujin::MediaFireHttp> mediafire_;
    std::unique_ptr<doujin::MediaFireHttp> site_http_;
    CatalogZlib zlib_;
    enum class ZipProvider { none, mediafire, mega };
    ZipProvider active_zip_ = ZipProvider::none;
    std::unique_ptr<doujin::CatalogZipRange> zip_;
    std::unique_ptr<doujin::mega::WindowsCipher> mega_cipher_;

    Json open_remote_zip(const Json& args, doujin::CatalogFetch fetch, std::string filename = {}) {
        zip_.reset();
        active_zip_ = ZipProvider::none;
        if (!zlib_.load(args.at("host_zlib").get<std::string>())) return nullptr;
        const auto budget = args.at("budget").get<std::size_t>();
        const auto requests = args.at("requests").get<unsigned>();
        const auto suffix = args.at("suffix").get<std::size_t>();
        if (!budget || budget > 2 * 1024 * 1024 || !requests || requests > 64 ||
            !suffix || suffix > doujin::zip_catalog_max_tail)
            throw std::runtime_error("Invalid native ZIP inspection budget");
        auto reader = std::make_unique<doujin::CatalogZipRange>(std::move(fetch), zlib_.api, budget, requests);
        if (!reader->open(suffix)) {
            const auto magic = reader->signature();
            std::string format = "unknown";
            if (magic.starts_with("Rar!\x1a\x07")) format = "rar";
            else if (magic.starts_with("7z\xbc\xaf\x27\x1c")) format = "7z";
            else if (magic.starts_with("\x1f\x8b")) format = "gzip";
            else if (magic.starts_with("PK\x03\x04")) format = "zip_invalid_or_incomplete";
            auto result = archive_counts(*reader);
            result["format"] = format;
            if (filename.empty()) filename = doujin::archive_inspection::filename_from_disposition(reader->disposition);
            if (!filename.empty()) result["filename"] = std::move(filename);
            return result;
        }
        for (const auto& row : reader->rows)
            if (row.method == 8 && (row.size > UINT32_MAX || row.packed > UINT32_MAX)) return nullptr;
        auto result = archive_counts(*reader);
        result["members"] = zip_rows(reader->rows);
        result["format"] = "zip";
        if (filename.empty()) filename = doujin::archive_inspection::filename_from_disposition(reader->disposition);
        if (!filename.empty()) result["filename"] = std::move(filename);
        zip_ = std::move(reader);
        return result;
    }

    Json open_mediafire_zip(const Json& args) {
        if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
        mega_cipher_.reset();
        auto result = open_remote_zip(args, [this, url = args.at("url").get<std::string>(),
            referrer = args.value("referrer", ""), policy = args.value("policy", "")]
            (auto offset, auto length, const auto& validator) {
                auto range = mediafire_->range_bytes(url, referrer, policy, offset, length, validator);
                if (range.status != 206)
                    throw doujin::MediaFireRangeError("MediaFire archive range returned HTTP " +
                        std::to_string(range.status) + "; response body skipped");
                return doujin::CatalogRangeReply{range.span, std::move(range.body), std::move(range.etag),
                                                std::move(range.modified), std::move(range.disposition)};
            });
        if (zip_) active_zip_ = ZipProvider::mediafire;
        return result;
    }

    Json open_mega_zip(const Json& args) {
        // MEGA API sends the public file HANDLE only; never transmit the fragment key.
        const auto link = doujin::parse_mega_link(args.at("url").get<std::string>());
        if (!mediafire_) mediafire_ = std::make_unique<doujin::MediaFireHttp>();
        const auto api_url = "https://g.api.mega.co.nz/cs?id=" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        const auto body = Json::array({{{"a", "g"}, {"p", link.handle}, {"g", 1}, {"ssl", 2}}}).dump();
        const auto [status, api_reply] = mediafire_->json_request(api_url, body, 65536);
        if (status != 200) throw std::runtime_error("MEGA public-file API returned HTTP " + std::to_string(status));
        auto file = doujin::mega::parse_public_file(api_reply);
        auto cipher = std::make_unique<doujin::mega::WindowsCipher>(link);
        const auto name = cipher->decrypt_filename(file.attributes);
        mega_cipher_ = std::move(cipher);
        // Same validated ZIP reader and probe limits used for MediaFire.
        // Range responses are unauthenticated until the whole-file MEGA MAC is checked;
        // inspect only bounded metadata and verify ZIP CRC on complete members.
        auto result = open_remote_zip(args, [this, file = std::move(file)]
            (auto offset, auto length, const std::string& validator) {
                if (!validator.empty()) throw std::runtime_error("MEGA range cannot use an HTTP validator");
                const auto first = offset.value_or(file.size - std::min<std::uint64_t>(file.size, length));
                const auto amount = static_cast<std::size_t>(std::min<std::uint64_t>(length, file.size - first));
                auto response = mediafire_->mega_storage_range(file, first, amount);
                mega_cipher_->decrypt(first, response.body);
                return doujin::CatalogRangeReply{{first, first + amount - 1, file.size},
                                                 std::move(response.body), "", "", ""};
            }, name);
        if (zip_) active_zip_ = ZipProvider::mega;
        return result;
    }

    Json probe_mediafire_zip(const Json& args) {
        if (!zip_) throw std::runtime_error("No open native MediaFire ZIP");
        const auto& members = args.at("members");
        const auto requests = args.at("requests").get<unsigned>();
        if (!members.is_array() || members.size() > 32 || !requests || requests > 64)
            throw std::runtime_error("Invalid native ZIP probe batch");
        auto observations = Json::array();
        for (const auto& target : members) {
            const auto offset = target.at(0).get<std::uint64_t>();
            const auto size = target.at(1).get<std::uint64_t>();
            const auto& suffix = target.at(2).get_ref<const std::string&>();
            zip_->begin_probe(4096 + 128 * 1024, requests);
            try {
                const auto data = zip_->member(offset, 4096);
                observations.push_back(Json(doujin::catalog_audio_prefix(data, suffix, size)));
            } catch (const doujin::CatalogRangeChanged&) {
                throw;
            } catch (const doujin::MediaFireRangeError&) {
                throw;
            } catch (const std::runtime_error& error) {
                observations.push_back({{"prefix_status", error.what()}});
            }
        }
        auto result = archive_counts(*zip_);
        result["observations"] = std::move(observations);
        return result;
    }
#endif

    static std::string read_hex(const Json& request, const char* key, std::size_t limit) {
        return doujin::decode_hex(request.at(key).get_ref<const std::string&>(), limit);
    }

    static Json publication_snapshot(const std::string& path, const Json& request) {
        auto db = doujin::connect(path, SQLITE_OPEN_READONLY);
        // A single read statement observes the release, its selected copy, and tracklists together.
        auto row = doujin::query(db.get(), R"sql(
            SELECT r.listing, c.share_url, c.archive, (
                SELECT json_group_array(json(tracks)) FROM (
                    SELECT tracks FROM tracklists WHERE release_id=?1 ORDER BY source_url
                )
            )
            FROM release_copies AS rc
            JOIN releases AS r ON r.id=rc.release_id
            JOIN copies AS c ON c.id=rc.copy_id
            WHERE r.id=?1 AND (?2 IS NULL OR c.id=?2)
              AND COALESCE(c.archive_error,'')=''
              AND json_extract(NULLIF(c.archive,''),'$.format')='zip'
            ORDER BY (rc.source='doujinstyle' AND rc.current=1) DESC,
                     rc.current DESC, rc.last_seen DESC, c.id
            LIMIT 1
        )sql", {request.at("id"), request.value("copy_id", Json{})});
        if (!doujin::next(row.get())) return nullptr;
        const auto url = doujin::string_column(row.get(), 1);
        if (!url.starts_with("http://") && !url.starts_with("https://")) return nullptr;
        if (url.find('#') != url.npos) {
            try { doujin::parse_mega_link(url); }
            catch (const std::runtime_error&) { return nullptr; }
        }
        auto tracks = Json::array();
        // Sorted object keys make track equality independent of the source's field order.
        std::set<nlohmann::json> seen;
        for (const auto& source : nlohmann::json::parse(doujin::string_column(row.get(), 3))) {
            for (const auto& track : source) {
                if (seen.insert(track).second) tracks.push_back(Json(track));
            }
        }
        return Json::array({Json::parse(doujin::string_column(row.get(), 0)), url,
                            Json::parse(doujin::string_column(row.get(), 2)), std::move(tracks)});
    }

    static fs::path publication_output(const Json& request) {
        const auto name = request.at("output").get<std::string>();
        fs::path path(std::u8string(name.begin(), name.end()));
        if (!path.is_absolute()) throw std::runtime_error("The output path must be absolute");
        return path;
    }

    static std::string stored_placement(sqlite3_stmt* lookup, const std::string& root,
                                        const std::string& filename) {
        doujin::bind(lookup, 1, root);
        doujin::bind(lookup, 2, filename);
        auto saved = doujin::next(lookup) ? doujin::string_column(lookup, 0) : std::string{};
        doujin::check(sqlite3_db_handle(lookup), sqlite3_reset(lookup));
        return valid_placement(saved, filename) ? saved : std::string{};
    }

    static Json publish_release(const std::string& db_path, const Json& request) {
        const auto snapshot = publication_snapshot(db_path, request);
        if (snapshot.is_null()) return nullptr;
        const auto output = publication_output(request);
        const auto root_key = request.at("root").get<std::string>();
        fs::create_directories(output);
        const auto& archive = snapshot.at(2);
        const auto members = archive.find("inventory");
        const auto& items = members == archive.end() ? doujin::projection::get(archive, "audio") : *members;
        const auto& release = snapshot.at(0);
        const auto id = request.at("id").dump();
        if (!digits(id)) throw std::runtime_error("Invalid release ID for publication");
        const auto& url = snapshot.at(1).get_ref<const std::string&>();
        const doujin::projection::ReleaseProjection projection(release, archive, snapshot.at(3));
        const auto db = doujin::connect(db_path, SQLITE_OPEN_READONLY);
        auto lookup = doujin::prepare(db.get(), "SELECT path FROM published_references WHERE root=? AND filename=?");
        std::optional<std::vector<std::string>> shards;
        const auto file_name = [&](std::string_view member) {
            const auto key = doujin::sha256_key(member);
            if (key.empty()) throw std::runtime_error("Could not hash the archive member name");
            return id + "-" + key.substr(0, 24) + ".djt";
        };
        std::vector<Reference> references;
        for (const auto& item : items) {
            using doujin::projection::get;
            using doujin::projection::text;
            const auto kind = item.find("kind");
            if ((kind != item.end() && *kind != "audio_candidate") || doujin::truthy(get(item, "encrypted"))) continue;
            auto descriptor = projection.descriptor(item, url);
            if (descriptor.empty()) continue;
            const auto member = text(get(item, "path"));
            const auto& zip_name = get(item, "zip_name");
            const auto previous = doujin::projection::legacy_name(member, zip_name);
            const auto alias = text(get(zip_name, "legacy_unicode_path"));
            auto filename = file_name(previous);
            if (previous != member || !alias.empty()) {
                std::vector<std::string> candidates{previous, std::string(member)};
                if (!alias.empty()) candidates.emplace_back(alias);
                const auto original_count = candidates.size();
                for (std::size_t i = 0; i < original_count; ++i) {
                    auto normalized = doujin::projection::portable_slashes(candidates[i]);
                    if (std::find(candidates.begin(), candidates.end(), normalized) == candidates.end())
                        candidates.push_back(std::move(normalized));
                }
                for (const auto& candidate : candidates) {
                    auto possible = candidate == previous ? filename : file_name(candidate);
                    const auto saved = root_key.empty() ? std::string{} : stored_placement(lookup.get(), root_key, possible);
                    if (fs::is_regular_file(output / choose_placement(output, possible, "", saved, shards))) {
                        filename = std::move(possible);
                        break;
                    }
                }
            }
            references.push_back({std::move(filename), std::move(descriptor), {}});
        }
        return publish_references(db_path, output, root_key, references);
    }

    static Json publish_many(const std::string& db_path, const Json& request) {
        // A single publication lock covers every release in this batch. Keep the
        // existing per-release snapshot and stable filename contracts unchanged.
        (void)publication_output(request);
        const auto root_key = request.at("root").get<std::string>();
        const auto& ids = request.at("ids");
        if (!ids.is_array() || ids.size() > 4096)
            throw std::runtime_error("Invalid publication batch size");
        for (const auto& id : ids) {
            if (!id.is_number_unsigned() && !(id.is_number_integer() && id.get<std::int64_t>() >= 0))
                throw std::runtime_error("Publication batch contains an invalid release ID");
        }
        doujin::PublicationLock lock(fs::path(std::u8string(db_path.begin(), db_path.end())));
        Json results = Json::array();
        std::size_t total = 0, written = 0, skipped = 0;
        for (const auto& id : ids) {
            Json one = {{"id", id}, {"output", request.at("output")}, {"root", root_key},
                        {"copy_id", request.value("copy_id", Json())}};
            const auto files = publish_release(db_path, one);
            if (files.is_null()) { ++skipped; continue; }
            for (const auto& entry : files) {
                if (!entry.is_array() || entry.size() != 2 || !entry.at(0).is_string() || !entry.at(1).is_boolean())
                    throw std::runtime_error("Native publisher returned a malformed reference notification");
                const bool changed = entry.at(1).get<bool>();
                ++total;
                if (changed) ++written;
                results.push_back(Json::array({id, entry.at(0), changed}));
            }
        }
        return {{"references", total}, {"written", written},
                {"skipped_releases", skipped}, {"notifications", std::move(results)}};
    }

    static Json publish(const std::string& db_path, const Json& request) {
        const auto output = publication_output(request);
        const auto root_key = request.at("root").get<std::string>();
        std::vector<Reference> references;
        for (const auto& track : request.at("references")) {
            auto filename = track.at(0).get<std::string>();
            auto descriptor = track.at(1).get<std::string>();
            if (!valid_filename(filename) || !descriptor.starts_with("DOUJIN-TRACK 1\n"))
                throw std::runtime_error("Invalid track reference in publication request");
            references.push_back({std::move(filename), std::move(descriptor), {}});
        }

        return publish_references(db_path, output, root_key, references);
    }

    static Json publish_references(const std::string& db_path, const fs::path& output,
                                   const std::string& root_key, std::vector<Reference>& references) {
        fs::create_directories(output);
        if (!references.empty() && !root_key.empty()) {
            auto db = doujin::connect(db_path, SQLITE_OPEN_READWRITE);
            auto lookup = doujin::prepare(db.get(), "SELECT path FROM published_references WHERE root=? AND filename=?");
            std::optional<std::vector<std::string>> shards;
            for (auto& reference : references) {
                const auto saved = stored_placement(lookup.get(), root_key, reference.filename);
                reference.relative = choose_placement(output, reference.filename, reference.descriptor, saved, shards);
                reference.changed = write_reference(output / reference.relative, reference.descriptor);
            }
            update_placements(db.get(), root_key, references);
        } else {
            for (auto& reference : references) {
                reference.relative = reference.filename;
                reference.changed = write_reference(output / reference.relative, reference.descriptor);
            }
        }
        auto result = Json::array();
        for (const auto& reference : references)
            result.push_back(Json::array({reference.relative, reference.changed}));
        return result;
    }

    static void update_placements(sqlite3* db, const std::string& root, const std::vector<Reference>& references) {
        doujin::Transaction transaction(db);
        auto upsert = doujin::prepare(db, "INSERT INTO published_references VALUES (?,?,?) "
            "ON CONFLICT(root,filename) DO UPDATE SET path=excluded.path WHERE path<>excluded.path");
        for (const auto& reference : references) {
            doujin::bind(upsert.get(), 1, root);
            doujin::bind(upsert.get(), 2, reference.filename);
            doujin::bind(upsert.get(), 3, reference.relative);
            doujin::next(upsert.get());
            doujin::check(db, sqlite3_reset(upsert.get()));
        }
        transaction.commit();
    }
};
}

// Query frontend now uses the same persistent C++ worker as ZIP indexing,
// offer dispatch, charts and idle backfill. Its output protocol is kept
// identical to the legacy Python SearchDialog process.
class ForegroundArtworkPool {
public:
    using Emit = std::function<void(const std::string&)>;
    ForegroundArtworkPool(std::string artwork, Emit emit) :
        root_(fs::path(std::u8string(artwork.begin(), artwork.end()))), emit_(std::move(emit)) {
        fs::create_directories(root_);
        for (int n = 0; n < 3; ++n)
            workers_.emplace_back([this] { consume(); });
    }
    ~ForegroundArtworkPool() { finish(); }

    void enqueue(std::int64_t id, std::string url) {
        if (id < 0) throw std::runtime_error("Invalid native artwork identity");
        {
            std::lock_guard lock(lock_);
            if (closing_ || tasks_.size() >= 64) throw std::runtime_error("Native artwork queue exceeded budget");
            tasks_.push_back({id, std::move(url)});
        }
        ready_.notify_one();
    }

    void finish() noexcept {
        {
            std::lock_guard lock(lock_);
            closing_ = true;
        }
        ready_.notify_all();
        for (auto& worker : workers_)
            if (worker.joinable()) worker.join();
    }

private:
    struct Task { std::int64_t id; std::string url; };
    fs::path root_;
    Emit emit_;
    std::mutex lock_;
    std::mutex file_lock_;
    std::condition_variable ready_;
    std::deque<Task> tasks_;
    std::vector<std::thread> workers_;
    bool closing_ = false;

    void consume() noexcept {
        Publisher fetcher;
        for (;;) {
            Task item{};
            {
                std::unique_lock lock(lock_);
                ready_.wait(lock, [&] { return closing_ || !tasks_.empty(); });
                if (tasks_.empty()) return;
                item = std::move(tasks_.front());
                tasks_.pop_front();
            }
            try {
                const auto name = item.id
                    ? std::to_string(item.id) + ".jpg" : std::string("site-icon.img");
                const auto target = root_ / std::u8string(name.begin(), name.end());
                const auto valid_cache = [&] {
                    if (!fs::is_regular_file(target)) return false;
                    const auto age = fs::file_time_type::clock::now() - fs::last_write_time(target);
                    return age < std::chrono::hours(24 * 7);
                };
                bool ready = valid_cache();
                if (!ready) {
                    const auto url = item.id
                        ? "https://doujinstyle.com/thumbs/" + std::to_string(item.id) + ".jpg"
                        : (item.url.empty() ? "https://doujinstyle.com/favicon.ico" : item.url);
                    const auto response = fetcher.run({{"operation", "site_resource"},
                        {"kind", "art"}, {"url", url}, {"cap", 96 * 1024}});
                    if (response.value("status", 0) == 200 && response.contains("body") &&
                        response.at("body").is_string()) {
                        const auto bytes = doujin::decode_hex(response.at("body").get_ref<const std::string&>(),
                                                              96 * 1024);
                        const bool jpeg = bytes.size() >= 3 &&
                            static_cast<unsigned char>(bytes[0]) == 0xff &&
                            static_cast<unsigned char>(bytes[1]) == 0xd8 &&
                            static_cast<unsigned char>(bytes[2]) == 0xff;
                        const bool png = bytes.starts_with("\x89PNG\r\n\x1a\n");
                        const bool gif = bytes.starts_with("GIF87a") || bytes.starts_with("GIF89a");
                        const bool ico = bytes.size() >= 4 && bytes[0] == '\0' && bytes[1] == '\0' &&
                            bytes[2] == '\x01' && bytes[3] == '\0';
                        if (jpeg || (!item.id && (png || gif || ico))) {
                            std::lock_guard files(file_lock_);
                            (void)write_reference(target, bytes);
                            ready = true;
                        }
                    }
                }
                if (ready || fs::is_regular_file(target))
                    emit_(item.id ? "\x1d" + std::to_string(item.id) : "\x17");
            } catch (const std::exception&) {
                // Artwork is optional; native search, SQLite publication and
                // track references must not fail due to a temporary JPG fetch.
            }
        }
    }
};

int native_foreground(const std::vector<std::string>& args) {
    try {
        doujin::search_frontend::Options options;
        std::string chart_period;
        std::set<std::string> keys;
        for (std::size_t i = 2; i < args.size(); ++i) {
            const auto& name = args[i];
            if (name.starts_with("--query=")) {
                if (!keys.insert("--query").second || !chart_period.empty()) throw std::runtime_error("Conflicting native search mode");
                options.query = name.substr(8);
                continue;
            }
            if (!keys.insert(name).second) throw std::runtime_error("Duplicate native search argument");
            if (name == "--refresh") { options.refresh = true; continue; }
            if (name == "--result-rows") { options.result_rows = true; continue; }
            if (name == "--prefetch-charts") {
                if (!options.query.empty() || !chart_period.empty()) throw std::runtime_error("Conflicting native prefetch mode");
                chart_period = "prefetch";
                options.workers = 1;
                continue;
            }
            if (i + 1 == args.size()) throw std::runtime_error("Missing native search argument value");
            const auto& value = args[++i];
            if (name == "--cache") options.db = value;
            else if (name == "--popular") {
                if (!options.query.empty() || !chart_period.empty() || (value != "month" && value != "all"))
                    throw std::runtime_error("Invalid or conflicting chart period");
                chart_period = value;
            }
            else if (name == "--output") options.output = value;
            else if (name == "--art-cache") options.artwork = value;
            else if (name == "--host-zlib") options.host_zlib = value;
            else if (name == "--pages") {
                const auto parse = std::from_chars(value.data(), value.data() + value.size(), options.pages);
                if (parse.ec != std::errc{} || parse.ptr != value.data() + value.size())
                    throw std::runtime_error("Invalid native page count");
            } else throw std::runtime_error("Unsupported native search argument");
        }
        if (options.db.empty() || options.output.empty() ||
            (options.query.empty() == chart_period.empty()))
            throw std::runtime_error("Exactly one native search or chart mode is required");
        auto cache = fs::path(std::u8string(options.db.begin(), options.db.end()));
        if (!cache.is_absolute()) throw std::runtime_error("Native search catalog must be absolute");
        const auto output = fs::path(std::u8string(options.output.begin(), options.output.end()));
        if (!output.is_absolute()) throw std::runtime_error("Native search output must be absolute");
        fs::create_directories(cache.parent_path());
        const auto [canonical, root] = canonical_output_and_key(options.output);
        options.output = canonical;
        options.root = root;

        Json legacy = Json::array();
        if (fs::is_regular_file(cache)) {
            auto prior = doujin::connect(options.db, SQLITE_OPEN_READONLY);
            legacy = doujin::legacy_links::snapshot(prior.get());
        }
#ifdef _WIN32
        const auto type = GetDriveTypeW(cache.root_path().c_str());
        const bool wal = type == DRIVE_FIXED || type == DRIVE_REMOVABLE || type == DRIVE_RAMDISK;
#else
        const bool wal = true;
#endif
        Publisher worker;
        (void)worker.run({{"operation", "initialize"}, {"db", options.db},
                         {"wal", wal}, {"legacy", std::move(legacy)}});
        const auto emit = [](const std::string& line) {
            if (std::fwrite(line.data(), 1, line.size(), stdout) != line.size() ||
                std::fputc('\n', stdout) == EOF || std::fflush(stdout) != 0)
                throw std::runtime_error("Search UI progress pipe closed");
        };
        std::mutex stdout_lock;
        const auto serialized_emit = [&](const std::string& line) {
            std::lock_guard lock(stdout_lock);
            emit(line);
        };
        std::unique_ptr<ForegroundArtworkPool> art;
        if (!options.artwork.empty())
            art = std::make_unique<ForegroundArtworkPool>(options.artwork, serialized_emit);
        auto read_db = doujin::connect(options.db, SQLITE_OPEN_READONLY);
        const auto submit = [&](const Json& command, const std::function<void(const Json&)>& event) {
            return worker.run(command, event);
        };
        const auto read_listing = [&](std::int64_t id) -> Json {
            return doujin::document(read_db.get(), "SELECT listing FROM releases WHERE id=?", id);
        };
        const auto schedule_artwork = [&](std::int64_t id, std::string url) {
            if (art) art->enqueue(id, std::move(url));
        };
        const auto write_archive_art = [&](std::int64_t id, const std::string& hex) {
            if (options.artwork.empty()) return;
            const auto bytes = doujin::decode_hex(hex, 128 * 1024);
            const auto filename = std::to_string(id) + "-archive.img";
            const auto path = fs::path(std::u8string(options.artwork.begin(), options.artwork.end())) /
                fs::path(std::u8string(filename.begin(), filename.end()));
            (void)write_reference(path, bytes);
        };
        const auto result = chart_period.empty()
            ? doujin::search_frontend::run(options, submit, serialized_emit,
                  read_listing, schedule_artwork, write_archive_art)
            : doujin::chart_frontend::run(options, chart_period, submit, serialized_emit,
                  read_listing, schedule_artwork, write_archive_art);
        if (art) art->finish();
        serialized_emit("Done. " + std::to_string(result.listings) + " listings, " +
            std::to_string(result.archives_indexed) + "/" +
            std::to_string(result.archives_attempted) + " archives inspected, " +
            std::to_string(result.references_written) + " references written.");
        serialized_emit("Coverage: " + std::to_string(result.releases_with_tracks) + "/" +
            std::to_string(result.listings) + " releases have published tracks.");
        return 0;
    } catch (const std::exception& exc) {
        // Provider exceptions may include URLs, secrets or arbitrary HTML.
        // Redact the diagnostic in the UI; immutable ETW evidence remains local.
        std::fputs("Search stopped: Native foreground operation failed; inspect local diagnostics.\n", stdout);
        (void)exc;
        return 2;
    }
}

int worker_main(const std::vector<std::string>& args) {
    if (args.size() == 2 && args[1] == "--version") {
        std::printf("%s\nsource=%s\ncommit=%s\n", DOUJIN_FULL_VERSION,
                    DOUJIN_SOURCE_SHA256, DOUJIN_GIT_COMMIT);
        return 0;
    }
    if (args.size() >= 2 && args[1] == "--foreground-search")
        return native_foreground(args);
    if (args.size() >= 2 && args[1] == "--idle-backfill") {
        try {
            doujin::idle_frontend::Options options;
            std::set<std::string> used;
            for (std::size_t i = 2; i < args.size(); ++i) {
                const auto& key = args[i];
                if (!used.insert(key).second) throw std::runtime_error("Duplicate native idle argument");
                if (key == "--idle-catalog-signals") { options.catalog_signals = true; continue; }
                if (i + 1 == args.size()) throw std::runtime_error("Missing native idle argument value");
                const auto& value = args[++i];
                if (key == "--cache") options.db = value;
                else if (key == "--output") options.output = value;
                else if (key == "--art-cache") options.artwork = value;
                else if (key == "--host-zlib") options.host_zlib = value;
                else if (key == "--idle-layers") {
                    int layers = 0;
                    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), layers);
                    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size())
                        throw std::runtime_error("Invalid native idle layer mask");
                    options.layers = layers;
                } else throw std::runtime_error("Unsupported native idle argument");
            }
            const auto [output, root] = canonical_output_and_key(options.output);
            options.output = output;
            options.root = root;
            Publisher worker;
            const auto emit = [](const std::string& line) {
                if (std::fwrite(line.data(), 1, line.size(), stdout) != line.size() ||
                    std::fputc('\n', stdout) == EOF || std::fflush(stdout) != 0)
                    throw std::runtime_error("Native idle stdout closed");
            };
            doujin::idle_frontend::run(options,
                [&](const Json& command, const std::function<void(const Json&)>& event) {
                    return worker.run(command, event);
                }, emit, [&](const std::string& directory, std::int64_t release, const std::string& hex) {
                    const auto binary = doujin::decode_hex(hex, 128 * 1024);
                    (void)write_reference(fs::path(std::u8string(directory.begin(), directory.end())) /
                            (std::to_string(release) + "-archive.img"), binary);
                });
            return 0;
        } catch (const std::exception& error) {
            std::fputs("Idle indexing stopped: ", stderr);
            std::fputs(error.what(), stderr);
            std::fputc('\n', stderr);
            return 2;
        }
    }
    if (args.size() >= 2 && args[1] == "--bangumi") {
        try {
            if (args.size() < 5 || args.size() > 4 + doujin::bangumi::batch_limit)
                throw std::runtime_error("Expected catalog path, output path, and one to six release IDs");
            auto ids = Json::array();
            for (std::size_t i = 4; i < args.size(); ++i) {
                const auto& text = args[i];
                std::int64_t id = 0;
                const auto result = std::from_chars(text.data(), text.data() + text.size(), id);
                if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || id < 0)
                    throw std::runtime_error("Invalid Bangumi release ID");
                ids.push_back(id);
            }
            const auto result = Publisher{}.run({{"operation", "bangumi"}, {"db", args[2]},
                                                 {"output", args[3]}, {"ids", std::move(ids)}});
            for (const auto& line : result.at("messages")) std::puts(line.get_ref<const std::string&>().c_str());
            for (const auto& entry : result.at("references"))
                std::printf("\x19%s\t%s\n", entry.at(0).dump().c_str(), entry.at(1).get_ref<const std::string&>().c_str());
            if (std::fflush(stdout) != 0) return 3;
            return 0;
        } catch (const std::exception&) {
            std::fputs("Bangumi catalog job could not complete.\n", stderr);
            return 2;
        }
    }
#ifdef _WIN32
    if (_setmode(_fileno(stdin), _O_BINARY) == -1) return 2;
#endif
    if (args.size() == 2 && args[1] == "--serve") {
#ifdef _WIN32
        constexpr std::string_view hello = "DoujinStyle catalog 1\n";
        DWORD written = 0;
        if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), hello.data(),
                static_cast<DWORD>(hello.size()), &written, nullptr) || written != hello.size())
            return 2;
#else
        std::cout << "DoujinStyle catalog 1\n" << std::flush;
#endif
    }
    Publisher publisher;
    // Progress frames are emitted synchronously on the coordinator thread,
    // *after* SQLite commits. The last frame for each request is result/error.
    // An interrupted process may leave committed progress but never a false
    // success frame. Consumers must not automatically replay POSTs.
    const auto frame = [](const Json& value) {
        const auto bytes = value.dump(-1, ' ', false, Json::error_handler_t::replace);
        if (std::fwrite(bytes.data(), 1, bytes.size(), stdout) != bytes.size() ||
            std::fputc('\n', stdout) == EOF || std::fflush(stdout) != 0)
            throw std::runtime_error("Catalog event pipe closed");
    };
    std::string line;
    bool oversized = false;
    while (read_catalog_frame(line, oversized)) {
        Json response;
        try {
            if (oversized) {
                std::string{}.swap(line);
                throw std::runtime_error("Catalog request exceeds the frame limit.");
            }
            if (line.find('\0') != line.npos) throw std::runtime_error("Catalog request contains a raw NUL byte.");
            auto request = Json::parse(line, [](int depth, Json::parse_event_t, const Json&) {
                if (depth > catalog_json_depth_limit)
                    throw std::runtime_error("Catalog request exceeds the JSON nesting limit.");
                return true;
            });
            response = Json{{"result", publisher.run(request, [&](const Json& event) {
                frame(Json{{"event", event}});
            })}};
        } catch (const Json::exception&) {
            // Parser diagnostics can contain pieces of a share URL or its key.
            response = Json{{"error", "Invalid catalog request or stored catalog data."}};
        } catch (const std::exception& error) {
            response = Json{{"error", error.what()}};
        }
        try { frame(response); }
        catch (const std::exception&) { return 3; }
    }
    return 0;
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        const auto count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
        if (!count) return 2;
        std::string value(static_cast<std::size_t>(count), '\0');
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, value.data(), count, nullptr, nullptr)) return 2;
        value.pop_back();
        args.push_back(std::move(value));
    }
    return worker_main(args);
}
#else
int main(int argc, char** argv) { return worker_main({argv, argv + argc}); }
#endif
