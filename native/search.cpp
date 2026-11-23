#include <foobar2000/SDK/foobar2000.h>
#include <foobar2000/SDK/coreDarkMode.h>
#include <foobar2000/SDK/message_loop.h>
#include <foobar2000/SDK/ole_interaction.h>
#include "catalog_status.hpp"
#include "archive_playback_stats.hpp"
#include "descriptor.hpp"
#include "host_zlib.hpp"
#include "result_projection.hpp"
#include "host_trace_etw.hpp"

#include <commdlg.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shlobj.h>
#include <uxtheme.h>
#include <wincodec.h>
#include <windowsx.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <optional>
#include <unordered_map>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
#include "search_worker_payload.inc"

namespace fs = std::filesystem;
constexpr int id_query = 101, id_pages = 102, id_cache = 103, id_output = 104;
constexpr int id_log = 105, id_refresh = 106, id_stop = 107, id_cache_browse = 108;
constexpr int id_results = 109, id_results_label = 110, id_back = 111, id_activity = 112;
constexpr int id_release = 113, id_list_view = 114, id_idle = 115;
constexpr int id_index = 116, id_index_contents = 117;
constexpr int id_idle_layers = 118, first_layer = 119;
constexpr int id_tracks_browse = 125, id_catalog_folder = 126, id_tracks_folder = 127, id_pref_status = 128;
constexpr int id_search_view = 129, id_art_view = 130, id_query_label = 131, id_pages_label = 132;
constexpr int id_coverage_label = 134;
constexpr int id_bangumi = 135;
constexpr int id_grid = 136, id_month = 137, id_all_time = 138, id_forward = 139;
constexpr int id_sort = 140, id_tag_filter = 141, id_format_filter = 142, id_ready_filter = 143;
constexpr int id_sort_label = 144;
constexpr std::array layer_labels{L"Browse pages", L"Release details", L"Download links",
                                  L"ZIP directories", L"Track metadata", L"Covers"};
// These indices are persisted as a bitmask. The worker deepens the catalog before browsing.
constexpr std::array idle_layer_display_order{1, 2, 3, 4, 5, 0};
constexpr UINT_PTR poll_timer = 1, tags_timer = 2, index_timer = 3, charts_timer = 4;
constexpr UINT charts_start_delay = 2000, charts_poll_delay = 300, charts_recheck_delay = 15 * 60 * 1000;
constexpr std::size_t history_limit = 12, recent_limit = 24;
enum class SearchSource { query, month, all_time };
constexpr int list_view = 1, album_art_view = 2;
constexpr LONG dialog_width = 456, dialog_height = 364;

cfg_string cache_path({0xf5b5cf42,0x3354,0x43cd,{0xad,0x67,0x2b,0x0e,0xb2,0x81,0x4a,0x3e}}, "");
cfg_string export_path({0xcc02d9cf,0x8b66,0x45e8,{0x9d,0x10,0x91,0xd1,0x2c,0x57,0x09,0xfa}}, "");
cfg_string recent_queries({0x816e36ba,0xba3f,0x441f,{0x83,0xa6,0x5b,0xe8,0xe7,0x42,0x48,0x08}}, "");
cfg_int result_view({0x8cf54cae,0x7cdd,0x45c8,{0x93,0xf2,0xa0,0x96,0x48,0x0e,0x1f,0xda}}, -1);
cfg_int list_zoom({0xd8bc53de,0xbddf,0x47ac,{0x91,0xca,0xe7,0x16,0x95,0x64,0xd3,0x11}}, 100);
cfg_int album_zoom({0x887dc7aa,0xd4a8,0x43f6,{0xac,0xf5,0x37,0xcd,0x1c,0x9d,0x3f,0xe4}}, 100);
cfg_bool join_bangumi({0xde80255e,0x87fd,0x4d27,{0xbd,0x74,0x58,0x40,0xef,0x96,0xc3,0x2d}}, false);
cfg_int idle_layers({0x43ff58b4,0x9620,0x4316,{0x91,0xa6,0x2a,0x39,0x97,0x0c,0xb5,0x3e}}, 1);

struct Handle {
    explicit Handle(HANDLE handle = nullptr) : value(handle) {}
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE value = nullptr;
    ~Handle() { reset(); }
    void reset(HANDLE next = nullptr) {
        if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value);
        value = next;
    }
};

COLORREF mix(COLORREF base, COLORREF tint, unsigned percent) {
    const auto part = [percent](unsigned a, unsigned b) { return (a * (100 - percent) + b * percent + 50) / 100; };
    return RGB(part(GetRValue(base), GetRValue(tint)), part(GetGValue(base), GetGValue(tint)),
               part(GetBValue(base), GetBValue(tint)));
}

double luminance(COLORREF color) {
    const auto linear = [](BYTE channel) {
        const double value = channel / 255.0;
        return value <= .04045 ? value / 12.92 : std::pow((value + .055) / 1.055, 2.4);
    };
    return .2126 * linear(GetRValue(color)) + .7152 * linear(GetGValue(color)) + .0722 * linear(GetBValue(color));
}

COLORREF legible(COLORREF preferred, COLORREF background, COLORREF fallback) {
    const auto light = luminance(preferred), dark = luminance(background);
    const auto ratio = (std::max(light, dark) + .05) / (std::min(light, dark) + .05);
    return ratio >= 4.5 ? preferred : fallback;
}

struct ReleaseRow {
    std::wstring id, title, artist, format, tags, date;
    std::wstring rank;
    std::vector<std::wstring> tracks{};
    std::shared_ptr<std::remove_pointer_t<HBITMAP>> cover{};
    int tile_image = I_IMAGENONE;
    std::vector<std::wstring> tag_values{};
    bool changed = true;
};
struct SearchLocation {
    SearchSource source = SearchSource::query;
    std::wstring query;
    LRESULT pages = 0;
    fs::path track_folder;
    bool operator==(const SearchLocation&) const = default;
};

struct ResultPosition {
    std::vector<std::wstring> selected;
    std::wstring focused, top;
};

struct SearchVisit {
    SearchLocation location;
    std::vector<ReleaseRow> rows;
    std::wstring log;
    doujin::ResultFilter filter;
    ResultPosition position;
};

std::wstring trim(std::wstring_view text) {
    const auto first = text.find_first_not_of(L" \t\r\n");
    if (first == text.npos) return {};
    return std::wstring(text.substr(first, text.find_last_not_of(L" \t\r\n") - first + 1));
}

int compare_text(std::wstring_view left, std::wstring_view right) {
    if (left.empty() || right.empty()) return left.empty() ? -!right.empty() : 1;
    const int order = CompareStringOrdinal(left.data(), static_cast<int>(left.size()), right.data(),
                                            static_cast<int>(right.size()), TRUE);
    return order ? order - CSTR_EQUAL : left.compare(right);
}

bool same_text(std::wstring_view left, std::wstring_view right) {
    return compare_text(left, right) == 0;
}

bool matches_prefix(std::wstring_view value, std::wstring_view prefix) {
    return value.size() >= prefix.size() && same_text(value.substr(0, prefix.size()), prefix);
}


enum Anchors { fixed = 0, right = 1, bottom = 2, grow_width = 4, grow_height = 8 };

struct PlacedControl {
    HWND window;
    int id;
    RECT units;
    unsigned anchors;
};

std::wstring wide(std::string_view text) {
    if (text.empty()) return {};
    int size = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    std::wstring out(size, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), size);
    return out;
}

std::string utf8(std::wstring_view text) {
    if (text.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string out(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), size, nullptr, nullptr);
    return out;
}

std::wstring quote_command_arg(std::wstring_view argument) {
    std::wstring result(1, L'"');
    std::size_t slashes = 0;
    for (wchar_t c : argument) {
        if (c == L'\\') { ++slashes; continue; }
        result.append(c == L'"' ? 2 * slashes + 1 : slashes, L'\\');
        slashes = 0;
        result += c;
    }
    result.append(2 * slashes, L'\\');
    return result + L'"';
}

std::wstring text_of(HWND dialog, int id) {
    HWND control = GetDlgItem(dialog, id);
    const int size = GetWindowTextLengthW(control);
    std::wstring out(size + 1, L'\0');
    out.resize(GetWindowTextW(control, out.data(), size + 1));
    return out;
}

fs::path profile_directory() {
    return fs::path(wide(filesystem::g_get_native_path(core_api::get_profile_path()).c_str())) / L"DoujinStyle";
}

fs::path default_output() {
    PWSTR music = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Music, 0, nullptr, &music))) {
        fs::path folder(music);
        CoTaskMemFree(music);
        return folder / L"DoujinStyle";
    }
    return profile_directory() / L"Tracks";
}

fs::path default_catalog() { return profile_directory() / L"doujin.sqlite"; }

fs::path downloads_folder() {
    PWSTR value = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_Downloads, KF_FLAG_CREATE, nullptr, &value)))
        throw std::runtime_error("Could not find the Windows Downloads folder.");
    const auto path = fs::path(value);
    CoTaskMemFree(value);
    return path;
}

fs::path configured_path(cfg_string& setting, fs::path (*default_path)()) {
    const auto value = setting.get();
    return value.is_empty() ? default_path() : fs::path(wide(value.c_str()));
}

struct alignas(DWORD) DialogTemplate {
    DLGTEMPLATE dialog;
    WORD menu = 0, window_class = 0, title = 0, point_size = 9;
    wchar_t face[9] = L"Segoe UI";
};

HWND make_control(HWND window, const wchar_t* klass, const wchar_t* value, DWORD style, int id,
                  RECT bounds, DWORD ex = 0) {
    MapDialogRect(window, &bounds);
    HWND item = CreateWindowExW(ex, klass, value, WS_CHILD | WS_VISIBLE | style,
                                bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top,
                                window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
                                core_api::get_my_instance(), nullptr);
    if (!item) throw std::runtime_error("Could not create a DoujinStyle control.");
    return item;
}

void open_index_status(HWND owner);
void configure_catalog_queue();

class StoragePreferences : public preferences_page_instance {
public:
    explicit StoragePreferences(preferences_page_callback::ptr callback) : callback_(std::move(callback)) {}

    void open(HWND parent) {
        const DialogTemplate spec{{WS_CHILD | DS_CONTROL | DS_SETFONT, WS_EX_CONTROLPARENT, 0, 0, 0, 300, 208}};
        HWND window = CreateDialogIndirectParamW(core_api::get_my_instance(), &spec.dialog, parent,
                                                 dispatch, reinterpret_cast<LPARAM>(this));
        if (!window || failed_) {
            if (window) DestroyWindow(window);
            throw std::runtime_error("Could not create DoujinStyle preferences.");
        }
    }

    HWND get_wnd() override { return window_; }

    t_uint32 get_state() override {
        auto state = preferences_state::resettable | preferences_state::dark_mode_supported;
        if (window_ && (text_of(window_, id_cache) != configured_path(cache_path, default_catalog).wstring() ||
                        text_of(window_, id_output) != configured_path(export_path, default_output).wstring() ||
                        (IsDlgButtonChecked(window_, id_bangumi) == BST_CHECKED) != join_bangumi.get()))
            state |= preferences_state::changed;
        return state;
    }

    void apply() override {
        const auto cache = text_of(window_, id_cache), tracks = text_of(window_, id_output);
        if (!fs::path(cache).is_absolute() || !fs::path(tracks).is_absolute()) {
            MessageBoxW(window_, L"Enter full paths for the catalog and track folder.",
                        L"DoujinStyle", MB_OK | MB_ICONWARNING);
            return;
        }
        cache_path = utf8(cache).c_str();
        export_path = utf8(tracks).c_str();
        join_bangumi = IsDlgButtonChecked(window_, id_bangumi) == BST_CHECKED;
        configure_catalog_queue();
        callback_->on_state_changed();
    }

    void reset() override {
        SetDlgItemTextW(window_, id_cache, default_catalog().c_str());
        SetDlgItemTextW(window_, id_output, default_output().c_str());
        CheckDlgButton(window_, id_bangumi, BST_UNCHECKED);
        callback_->on_state_changed();
    }

private:
    static INT_PTR CALLBACK dispatch(HWND window, UINT msg, WPARAM wp, LPARAM lp) noexcept {
        auto self = reinterpret_cast<StoragePreferences*>(GetWindowLongPtrW(window, DWLP_USER));
        if (msg == WM_INITDIALOG) {
            self = reinterpret_cast<StoragePreferences*>(lp);
            SetWindowLongPtrW(window, DWLP_USER, reinterpret_cast<LONG_PTR>(self));
            self->window_ = window;
        }
        if (!self) return FALSE;
        const preferences_page_instance::ptr keep_alive(self);
        try {
            if (msg == WM_INITDIALOG) self->init();
            if (msg == WM_SIZE && self->ready_) self->layout();
            if (msg == WM_COMMAND && self->ready_) {
                const auto id = LOWORD(wp);
                if (id == id_cache_browse) self->browse(id_cache);
                if (id == id_tracks_browse) self->browse(id_output);
                if (id == id_catalog_folder) self->open_folder(id_cache);
                if (id == id_tracks_folder) self->open_folder(id_output);
                if (id == id_pref_status) open_index_status(GetAncestor(window, GA_ROOT));
                if ((id == id_cache || id == id_output) && HIWORD(wp) == EN_CHANGE)
                    self->callback_->on_state_changed();
                if (id == id_bangumi && HIWORD(wp) == BN_CLICKED) self->callback_->on_state_changed();
            }
            if (msg == WM_NCDESTROY) self->window_ = nullptr;
        } catch (const std::exception&) {
            if (msg == WM_INITDIALOG) self->failed_ = true;
            else MessageBoxW(window, L"Could not open the selected location.", L"DoujinStyle", MB_OK | MB_ICONWARNING);
        }
        return FALSE;
    }

    void init() {
        const auto catalog = configured_path(cache_path, default_catalog), tracks = configured_path(export_path, default_output);
        const auto add = [&](const wchar_t* type, const wchar_t* label, DWORD style, int id, RECT position, DWORD ex = 0) {
            const auto child = make_control(window_, type, label, style, id, position, ex);
            SendMessageW(child, WM_SETFONT, SendMessageW(window_, WM_GETFONT, 0, 0), TRUE);
        };
        add(L"STATIC", L"SQLite &catalog", 0, -1, {8, 8, 290, 20});
        add(L"EDIT", catalog.c_str(), WS_TABSTOP | ES_AUTOHSCROLL, id_cache, {8, 22, 234, 39}, WS_EX_CLIENTEDGE);
        add(L"BUTTON", L"&Browse…", WS_TABSTOP, id_cache_browse, {240, 24, 292, 38});
        add(L"BUTTON", L"Open catalog folder", WS_TABSTOP, id_catalog_folder, {8, 43, 95, 57});
        add(L"STATIC", L"Published &tracks", 0, -1, {8, 67, 210, 79});
        add(L"EDIT", tracks.c_str(), WS_TABSTOP | ES_AUTOHSCROLL, id_output, {8, 81, 234, 98}, WS_EX_CLIENTEDGE);
        add(L"BUTTON", L"Bro&wse…", WS_TABSTOP, id_tracks_browse, {240, 83, 292, 97});
        add(L"BUTTON", L"Open tracks folder", WS_TABSTOP, id_tracks_folder, {8, 102, 95, 116});
        add(L"STATIC", L"Use a folder watched by the Media Library.", SS_NOPREFIX, -1, {8, 126, 228, 140});
        add(L"STATIC", L"Index coverage and idle backfill", 0, -1, {8, 145, 228, 157});
        add(L"BUTTON", L"&Index status…", WS_TABSTOP, id_pref_status, {8, 160, 95, 174});
        add(L"BUTTON", L"Look up release metadata in Bangumi", WS_TABSTOP | BS_AUTOCHECKBOX,
            id_bangumi, {8, 184, 263, 200});
        CheckDlgButton(window_, id_bangumi, join_bangumi.get() ? BST_CHECKED : BST_UNCHECKED);
        hooks_.AddDialogWithControls(window_);
        ready_ = true;
        layout();
    }

    void layout() {
        RECT client{}, nominal{0, 0, 300, 160};
        if (!GetClientRect(window_, &client) || !MapDialogRect(window_, &nominal)) return;
        const LONG delta = client.right - nominal.right;
        const auto fit = [&](int id, RECT bounds, bool move) {
            MapDialogRect(window_, &bounds);
            if (move) bounds.left += delta;
            bounds.right += delta;
            MoveWindow(GetDlgItem(window_, id), bounds.left, bounds.top,
                       std::max<LONG>(0, bounds.right - bounds.left), bounds.bottom - bounds.top, TRUE);
        };
        fit(id_cache, {8, 22, 234, 39}, false);
        fit(id_cache_browse, {240, 21, 292, 40}, true);
        fit(id_output, {8, 81, 234, 98}, false);
        fit(id_tracks_browse, {240, 80, 292, 99}, true);
    }

    static fs::path existing_ancestor(fs::path path) {
        while (!path.empty()) {
            std::error_code error;
            if (fs::is_directory(path, error)) return path;
            const auto parent = path.parent_path();
            if (parent == path) break;
            path = parent;
        }
        return {};
    }

    void browse(int field) {
        const bool choose_catalog = field == id_cache;
        pfc::com_ptr_t<IFileOpenDialog> picker;
        if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_IFileOpenDialog, reinterpret_cast<void**>(picker.receive_ptr()))))
            throw std::runtime_error("Location picker unavailable.");
        picker->SetOptions((choose_catalog ? FOS_FILEMUSTEXIST : FOS_PICKFOLDERS) |
            FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_DONTADDTORECENT);
        const fs::path current(text_of(window_, field));
        if (choose_catalog) {
            const COMDLG_FILTERSPEC types[]{{L"SQLite catalogs", L"*.sqlite;*.sqlite3;*.db"},
                                            {L"All files", L"*.*"}};
            picker->SetFileTypes(static_cast<UINT>(std::size(types)), types);
            if (!current.filename().empty()) picker->SetFileName(current.filename().c_str());
        }
        const auto start = existing_ancestor(choose_catalog ? current.parent_path() : current);
        pfc::com_ptr_t<IShellItem> location;
        if (!start.empty() && SUCCEEDED(SHCreateItemFromParsingName(start.c_str(), nullptr,
                IID_IShellItem, reinterpret_cast<void**>(location.receive_ptr()))))
            picker->SetFolder(location.get_ptr());
        if (FAILED(picker->Show(window_)) || !window_) return;
        pfc::com_ptr_t<IShellItem> selected;
        PWSTR native = nullptr;
        if (SUCCEEDED(picker->GetResult(selected.receive_ptr())) &&
            SUCCEEDED(selected->GetDisplayName(SIGDN_FILESYSPATH, &native))) {
            SetDlgItemTextW(window_, field, native);
            CoTaskMemFree(native);
        }
    }

    void open_folder(int field) {
        const fs::path current(text_of(window_, field));
        const auto folder = field == id_cache ? current.parent_path() : current;
        if (folder.empty() || !folder.is_absolute() || !fs::is_directory(folder) ||
            reinterpret_cast<INT_PTR>(ShellExecuteW(window_, L"open", folder.c_str(), nullptr, nullptr, SW_SHOWNORMAL)) <= 32)
            throw std::runtime_error("Folder unavailable.");
    }

    HWND window_ = nullptr;
    bool ready_ = false, failed_ = false;
    preferences_page_callback::ptr callback_;
    fb2k::CCoreDarkModeHooks hooks_;
};

class DoujinPreferences : public preferences_page_v3 {
    const char* get_name() override { return "DoujinStyle"; }
    GUID get_guid() override {
        return {0xc21a29bd, 0x7937, 0x4134, {0x9d,0xcc,0xb5,0x1f,0x79,0x62,0x14,0xa2}};
    }
    GUID get_parent_guid() override { return guid_tools; }
    preferences_page_instance::ptr instantiate(HWND parent, preferences_page_callback::ptr callback) override {
        auto page = fb2k::service_new<StoragePreferences>(std::move(callback));
        page->open(parent);
        return page;
    }
};

preferences_page_factory_t<DoujinPreferences> doujin_preferences;

fs::path python_launcher() {
    std::array<wchar_t, MAX_PATH> windows{};
    if (auto amount = GetWindowsDirectoryW(windows.data(), static_cast<UINT>(windows.size()));
        amount && amount < windows.size()) {
        auto path = fs::path(windows.data()) / L"py.exe";
        if (fs::is_regular_file(path)) return path;
    }
    const DWORD size = GetEnvironmentVariableW(L"PATH", nullptr, 0);
    std::wstring path_env(size, L'\0');
    std::array<wchar_t, 32768> result{};
    if (size && GetEnvironmentVariableW(L"PATH", path_env.data(), size)) {
        const DWORD length = SearchPathW(path_env.c_str(), L"py.exe", nullptr,
                                        static_cast<DWORD>(result.size()), result.data(), nullptr);
        if (length && length < result.size()) return fs::path(result.data());
    }
    throw std::runtime_error("Windows Python launcher (py.exe) was not found. Install Python 3.10 or newer.");
}

fs::path install_worker(const fs::path& profile, bool native = false) {
    fs::create_directories(profile);
    // Use a private per-run file so a second foobar2000 instance cannot replace this run's payload.
    std::array<wchar_t, MAX_PATH> name{};
    if (!GetTempFileNameW(profile.c_str(), L"djs", 0, name.data()))
        throw std::runtime_error("Could not create the search worker in the foobar2000 profile.");
    fs::path worker(name.data());
    if (native) {
        auto executable = worker;
        executable += L".exe";
        if (!MoveFileW(worker.c_str(), executable.c_str())) {
            DeleteFileW(worker.c_str());
            throw std::runtime_error("Could not reserve the native catalog worker path.");
        }
        worker = std::move(executable);
    }
    std::ofstream out(worker, std::ios::binary | std::ios::trunc);
    static_assert(native_worker_offset + native_worker_size <= sizeof search_worker_payload);
    const auto bytes = native ? search_worker_payload + native_worker_offset : search_worker_payload;
    const auto size = native ? native_worker_size : sizeof search_worker_payload;
    out.write(reinterpret_cast<const char*>(bytes), static_cast<std::streamsize>(size));
    out.close();
    if (!out) {
        DeleteFileW(worker.c_str());
        throw std::runtime_error("Could not write the search worker.");
    }
    return worker;
}

void launch_process(const fs::path& launcher, const fs::path& profile, std::wstring command,
                    Handle& job, Handle& running, Handle& reader, bool background) {
    SECURITY_ATTRIBUTES inheritance{sizeof inheritance, nullptr, TRUE};
    Handle write, input;
    if (!CreatePipe(&reader.value, &write.value, &inheritance, 0) ||
        !SetHandleInformation(reader.value, HANDLE_FLAG_INHERIT, 0))
        throw std::runtime_error("Could not open the search output pipe.");
    input.reset(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                            &inheritance, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (input.value == INVALID_HANDLE_VALUE)
        throw std::runtime_error("Could not open the search worker's standard input.");
    job.reset(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!job.value || !SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof limits))
        throw std::runtime_error("Could not set up search cancellation.");

    SIZE_T size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
    std::vector<unsigned char> attributes(size);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof startup;
    startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
    if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &size))
        throw std::runtime_error("Could not set up the search worker handles.");
    HANDLE inherited[]{write.value, input.value};
    const bool ready = UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        inherited, sizeof inherited, nullptr, nullptr);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = input.value;
    startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = write.value;
    PROCESS_INFORMATION process{};
    const DWORD creation = CREATE_NO_WINDOW | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT |
        (background ? BELOW_NORMAL_PRIORITY_CLASS : 0);
    const bool started = ready && CreateProcessW(launcher.c_str(), command.data(), nullptr, nullptr, TRUE,
        creation, nullptr, profile.c_str(),
        &startup.StartupInfo, &process);
    DeleteProcThreadAttributeList(startup.lpAttributeList);
    if (!started) throw std::runtime_error("Could not launch the DoujinStyle search worker.");
    Handle thread{process.hThread};
    running.reset(process.hProcess);
    if (!AssignProcessToJobObject(job.value, running.value)) {
        TerminateProcess(running.value, 1);
        throw std::runtime_error("Could not attach search cancellation to the worker.");
    }
    if (ResumeThread(thread.value) == static_cast<DWORD>(-1))
        throw std::runtime_error("Could not start the DoujinStyle search worker.");
}

void launch_child(const fs::path& cache, const std::wstring& arguments, Handle& job, Handle& running,
                  Handle& reader, fs::path& worker, bool background = false) {
    // The entire idle job is a native executable, not a Python entrypoint.
    // Keep the legacy launcher only for query/chart jobs until their own
    // C++ frontend and foobar2000 event contracts are independently qualified.
    const bool native_idle = arguments.starts_with(L" --idle-backfill ");
    const bool native_foreground = arguments.find(L" --query=") != std::wstring::npos;
    // Foreground search and idle indexing now execute the same native
    // publisher binary. Chart/prefetch remain Python until their own native
    // UI event parity is qualified and the packaged zipapp can be removed.
    const bool native = native_idle || native_foreground;
    const auto profile = profile_directory();
    worker = install_worker(profile, native);
    const auto launcher = native ? worker : python_launcher();
    auto command = native_idle
        ? quote_command_arg(worker.wstring()) + L" --idle-backfill --cache " +
          quote_command_arg(cache.wstring()) + arguments.substr(std::wstring_view(L" --idle-backfill").size())
        : native_foreground
        ? quote_command_arg(worker.wstring()) + L" --foreground-search --cache " +
          quote_command_arg(cache.wstring()) + arguments
        : quote_command_arg(launcher.wstring()) + L" -3 -E -u " + quote_command_arg(worker.wstring()) +
          L" --cache " + quote_command_arg(cache.wstring()) + arguments;
    if (const auto zlib = doujin::foobar_zlib_path(); !zlib.empty())
        command += L" --host-zlib " + quote_command_arg(zlib.wstring());
    launch_process(launcher, profile, std::move(command), job, running, reader, background);
}

std::string read_child_output(HANDLE pipe) {
    std::string output;
    std::array<char, 8192> buffer{};
    DWORD available = 0, read = 0;
    while (PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr) && available &&
           ReadFile(pipe, buffer.data(), static_cast<DWORD>(std::min<std::size_t>(available, buffer.size())), &read, nullptr) && read)
        output.append(buffer.data(), read);
    return output;
}

class CatalogLookupQueue {
public:
    static CatalogLookupQueue& get() {
        static CatalogLookupQueue queue;
        return queue;
    }

    void configure() {
        stop();
        if (!join_bangumi.get()) return;
        scanning_ = true;
        playlist_ = item_ = 0;
        arm();
    }

    // Discovery can be followed by stronger evidence when the ZIP is indexed.
    void signal(std::string id, unsigned strength = 1) {
        if (closed_ || !join_bangumi.get() || id.empty() || id.size() > 16 ||
            id.find_first_not_of("0123456789") != id.npos) return;
        if (const auto active = active_.find(id); active != active_.end() && strength <= active->second) return;
        if (const auto prior = memo_.find(id); prior != memo_.end() && strength <= prior->second.strength &&
            GetTickCount64() - prior->second.at < 120000) return;
        const auto [next, created] = pending_.try_emplace(std::move(id), strength);
        if (!created) next->second = std::max(next->second, strength);
        arm();
    }

    void signal_track(const metadb_handle_ptr& track) {
        if (track.is_valid() && join_bangumi.get()) signal(doujin::release_id_from_filename(track->get_path()));
    }

    void stop() {
        disarm();
        clear_child();
        pending_.clear();
        memo_.clear();
        scanning_ = false;
    }

    void shutdown() { closed_ = true; stop(); }

private:
    struct Completed { unsigned strength; ULONGLONG at; };

    void arm() {
        if (!timer_) timer_ = SetTimer(nullptr, 0, 150, tick);
    }

    void disarm() {
        if (timer_) KillTimer(nullptr, timer_);
        timer_ = 0;
    }

    static void CALLBACK tick(HWND, UINT, UINT_PTR, DWORD) noexcept {
        auto& queue = get();
        try { queue.run(); }
        catch (const std::exception& error) {
            console::print((std::string("DoujinStyle: background catalog lookup unavailable: ") + error.what()).c_str());
            queue.stop();
        } catch (...) {
            queue.stop();
        }
    }

    void scan_playlists() {
        const auto manager = playlist_manager::get();
        for (unsigned work = 0; work < 512 && pending_.size() < 128; ++work) {
            if (playlist_ >= manager->get_playlist_count()) {
                scanning_ = false;
                return;
            }
            if (item_ >= manager->playlist_get_item_count(playlist_)) {
                ++playlist_;
                item_ = 0;
                continue;
            }
            signal_track(manager->playlist_get_item_handle(playlist_, item_++));
        }
    }

    void run() {
        if (!join_bangumi.get()) {
            stop();
            return;
        }
        if (scanning_) scan_playlists();
        if (process_.value) {
            output_text_ += read_child_output(reader_.value);
            if (WaitForSingleObject(process_.value, 0) != WAIT_OBJECT_0) return;
            output_text_ += read_child_output(reader_.value);
            publish_results();
            for (const auto& [id, strength] : active_) memo_[id] = {strength, GetTickCount64()};
            clear_child();
        }
        if (pending_.empty()) {
            if (!scanning_) disarm();
            return;
        }
        const auto cache = configured_path(cache_path, default_catalog);
        output_ = configured_path(export_path, default_output);
        if (!cache.is_absolute() || !output_.is_absolute() || !fs::exists(cache)) {
            stop();
            return;
        }
        const auto profile = profile_directory();
        worker_ = install_worker(profile, true);
        std::wstring command = quote_command_arg(worker_.wstring()) + L" --bangumi " +
            quote_command_arg(cache.wstring()) + L" " + quote_command_arg(output_.wstring());
        while (!pending_.empty() && active_.size() < 6) {
            auto entry = pending_.extract(pending_.begin());
            command += L" " + wide(entry.key());
            active_.insert(std::move(entry));
        }
        // Deferred off the playback callback and independent of the Search dialog.
        launch_process(worker_, profile, std::move(command), job_, process_, reader_, true);
    }

    void publish_results() {
        std::string_view all(output_text_);
        metadb_handle_list tracks;
        while (!all.empty()) {
            const auto end = all.find('\n');
            auto line = all.substr(0, end);
            all.remove_prefix(end == all.npos ? all.size() : end + 1);
            if (line.ends_with('\r')) line.remove_suffix(1);
            if (!line.starts_with('\x19')) {
                if (!line.empty()) console::print((std::string("DoujinStyle: ") + std::string(line)).c_str());
                continue;
            }
            line.remove_prefix(1);
            const auto tab = line.find('\t');
            if (tab == line.npos) continue;
            const auto id = line.substr(0, tab), name = line.substr(tab + 1);
            if (!active_.contains(std::string(id)) || doujin::release_id_from_reference_path(name) != id) continue;
            pfc::string8 path;
            filesystem::g_get_canonical_path(utf8((output_ / wide(name)).wstring()).c_str(), path);
            tracks.add_item(metadb::get()->handle_create(path.c_str(), 0));
        }
        if (tracks.get_count())
            metadb_io_v2::get()->load_info_async(tracks, metadb_io::load_info_force, core_api::get_main_window(),
                                                metadb_io_v2::op_flag_silent, nullptr);
    }

    void clear_child() {
        job_.reset();
        if (process_.value) WaitForSingleObject(process_.value, 100);
        process_.reset();
        reader_.reset();
        if (!worker_.empty()) DeleteFileW(worker_.c_str());
        worker_.clear();
        active_.clear();
        output_text_.clear();
    }

    UINT_PTR timer_ = 0;
    bool scanning_ = false, closed_ = false;
    t_size playlist_ = 0, item_ = 0;
    std::map<std::string, unsigned> pending_, active_;
    std::unordered_map<std::string, Completed> memo_;
    Handle job_, process_, reader_;
    fs::path worker_, output_;
    std::string output_text_;
};

class CatalogPlaylistObserver : public playlist_callback_impl_base {
public:
    CatalogPlaylistObserver() : playlist_callback_impl_base(flag_on_items_added | flag_on_items_replaced) {}
    void on_items_added(t_size, t_size, metadb_handle_list_cref data, const bit_array&) override {
        try { for (const auto& track : data) CatalogLookupQueue::get().signal_track(track); } catch (...) {}
    }
    void on_items_replaced(t_size, const bit_array&, const pfc::list_base_const_t<t_on_items_replaced_entry>& data) override {
        try { for (const auto& item : data) CatalogLookupQueue::get().signal_track(item.m_new); } catch (...) {}
    }
};

void configure_catalog_queue() { CatalogLookupQueue::get().configure(); }

class SearchDialog : private ui_config_callback, private message_filter,
                     public std::enable_shared_from_this<SearchDialog> {
public:
    static INT_PTR CALLBACK dispatch(HWND window, UINT message, WPARAM wp, LPARAM lp) noexcept {
        auto* self = reinterpret_cast<SearchDialog*>(GetWindowLongPtrW(window, DWLP_USER));
        if (message == WM_INITDIALOG) {
            self = reinterpret_cast<SearchDialog*>(lp);
            SetWindowLongPtrW(window, DWLP_USER, reinterpret_cast<LONG_PTR>(self));
            self->window_ = window;
        }
        if (!self) return FALSE;
        const auto keep_alive = self->shared_from_this();
        try { return self->message(message, wp, lp); }
        catch (const std::exception& e) { self->report_error(e.what()); }
        catch (...) { self->report_error("Could not start or read the DoujinStyle search."); }
        return TRUE;
    }

    void open(HWND owner = nullptr) {
        if (window_) {
            SetForegroundWindow(window_);
            return;
        }
        const DialogTemplate spec{{WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | DS_CENTER | DS_SETFONT,
                                   0, 0, 0, 0, dialog_width, dialog_height}};
        if (!CreateDialogIndirectParamW(core_api::get_my_instance(), &spec.dialog,
                                        owner ? owner : core_api::get_main_window(),
                                        dispatch, reinterpret_cast<LPARAM>(this)) || !window_)
            throw std::runtime_error("Could not open the DoujinStyle search dialog.");
        message_loop::get()->add_message_filter(this);
        filter_registered_ = true;
        ShowWindow(window_, SW_SHOWNORMAL);
        SetFocus(GetDlgItem(window_, id_query));
    }

    void open_status(HWND owner) {
        open(owner);
        if (!index_visible_) select_index();
        else refresh_index();
    }

    bool is_open() const { return window_ != nullptr; }
    void close() { if (window_) DestroyWindow(window_); }

    ~SearchDialog() {
        if (theme_registered_) ui_config_->remove_callback(this);
        clear_worker();
        clear_charts_worker();
        clear_tags_worker();
        clear_index_worker();
        if (background_) DeleteObject(background_);
        if (surface_) DeleteObject(surface_);
        if (accent_brush_) DeleteObject(accent_brush_);
        if (bold_font_) DeleteObject(bold_font_);
        if (tiles_) ImageList_Destroy(tiles_);
        if (site_icon_) DestroyIcon(site_icon_);
    }

private:
    void control(const wchar_t* klass, const wchar_t* value, DWORD style, int id,
                 LONG x, LONG y, LONG width, LONG height, DWORD ex = 0, unsigned anchors = fixed) {
        const RECT units{x, y, x + width, y + height};
        HWND item = make_control(window_, klass, value, style, id, units, ex);
        controls_.push_back({item, id, units, anchors});
    }

    void init() {
        SetWindowTextW(window_, L"Search DoujinStyle");

        control(L"BUTTON", L"&Back", WS_TABSTOP, id_back, 12, 9, 39, 14);
        control(L"BUTTON", L"&Forward", WS_TABSTOP, id_forward, 55, 9, 47, 14);
        constexpr DWORD tab = WS_TABSTOP | BS_PUSHLIKE | BS_CHECKBOX;
        control(L"BUTTON", L"&Search", tab, id_search_view, 113, 9, 47, 14);
        control(L"BUTTON", L"This &Month", tab, id_month, 164, 9, 61, 14);
        control(L"BUTTON", L"All &Time", tab, id_all_time, 229, 9, 51, 14);
        control(L"BUTTON", L"&Index status", tab, id_index, 284, 9, 65, 14);
        control(L"BUTTON", L"Run idle &backfill", WS_TABSTOP, id_idle, 366, 35, 78, 14, 0, right);
        view_ = result_view.get() == album_art_view ? album_art_view : list_view;
        control(L"STATIC", L"&Query", 0, id_query_label, 12, 37, 34, 12);
        control(L"COMBOBOX", L"", WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWN | CBS_AUTOHSCROLL | CBS_HASSTRINGS,
                id_query, 49, 33, 203, 137, 0, grow_width);
        SendDlgItemMessageW(window_, id_query, CB_LIMITTEXT, 512, 0);
        COMBOBOXINFO query_info{};
        query_info.cbSize = sizeof query_info;
        if (GetComboBoxInfo(GetDlgItem(window_, id_query), &query_info)) query_edit_ = query_info.hwndItem;
        SendMessageW(query_edit_, EM_SETCUEBANNER, FALSE,
                     reinterpret_cast<LPARAM>(L"Artist, title, or tag: name"));
        load_recent();
        fill_suggestions();
        control(L"STATIC", L"Result &pages", 0, id_pages_label, 260, 37, 58, 12, 0, right);
        control(L"COMBOBOX", L"", WS_TABSTOP | CBS_DROPDOWNLIST, id_pages, 320, 32, 63, 66, 0, right);
        for (auto* option : {L"1 · 20 max", L"2 · 40 max", L"3 · 60 max"}) {
            SendDlgItemMessageW(window_, id_pages, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(option));
        }
        SendDlgItemMessageW(window_, id_pages, CB_SETCURSEL, 0, 0);
        control(L"BUTTON", L"&Go", WS_TABSTOP | BS_DEFPUSHBUTTON, IDOK, 394, 34, 50, 14, 0, right);
        control(L"STATIC", L"S&ort", 0, id_sort_label, 12, 54, 22, 11);
        constexpr DWORD filter_style = WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST;
        control(L"COMBOBOX", L"", filter_style, id_sort, 37, 51, 77, 105);
        for (auto* label : {L"Site order", L"Title A–Z", L"Artist A–Z", L"Newest", L"Oldest", L"Ready first"})
            SendDlgItemMessageW(window_, id_sort, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(label));
        SendDlgItemMessageW(window_, id_sort, CB_SETCURSEL, 0, 0);
        control(L"COMBOBOX", L"", filter_style, id_tag_filter, 119, 51, 94, 135);
        control(L"COMBOBOX", L"", filter_style, id_format_filter, 218, 51, 71, 110);
        control(L"BUTTON", L"&Ready to drag", WS_TABSTOP | BS_AUTOCHECKBOX, id_ready_filter, 295, 51, 88, 14);
        update_filter_options();
        control(L"STATIC", L"Results", SS_NOPREFIX, id_results_label, 12, 74, 259, 11);
        control(L"BUTTON", L"&List", tab, id_list_view, 321, 70, 49, 14, 0, right);
        control(L"BUTTON", L"Album Ar&t", tab, id_art_view, 374, 70, 70, 14, 0, right);
        control(L"LISTBOX", L"", WS_TABSTOP | WS_VSCROLL | LBS_NOTIFY | LBS_HASSTRINGS | LBS_OWNERDRAWFIXED |
                LBS_NOINTEGRALHEIGHT | LBS_EXTENDEDSEL, id_results, 12, 89, 432, 166, WS_EX_CLIENTEDGE,
                grow_width | grow_height);
        INITCOMMONCONTROLSEX commons{sizeof commons, ICC_LISTVIEW_CLASSES};
        InitCommonControlsEx(&commons);
        control(WC_LISTVIEWW, L"", WS_TABSTOP | LVS_ICON | LVS_AUTOARRANGE | LVS_SHAREIMAGELISTS | LVS_SHOWSELALWAYS,
                id_grid, 12, 89, 432, 166, WS_EX_CLIENTEDGE, grow_width | grow_height);
        const auto grid = GetDlgItem(window_, id_grid);
        ListView_SetExtendedListViewStyle(grid, LVS_EX_DOUBLEBUFFER | LVS_EX_INFOTIP);
        const auto get_dpi = reinterpret_cast<UINT(WINAPI*)(HWND)>(
            GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow"));
        dpi_ = get_dpi ? get_dpi(window_) : 96;
        cover_pixels_ = MulDiv(144, dpi_, 96);
        control(L"STATIC", L"Catalog coverage", SS_NOPREFIX, id_coverage_label, 12, 72, 210, 11);
        control(L"STATIC", L"Idle backfill (next run)", SS_NOPREFIX, id_idle_layers, 12, 36, 126, 12);
        constexpr std::array layer_widths{66, 68, 62, 67, 42, 66};
        LONG layer_left = 12;
        for (std::size_t column = 0; column < idle_layer_display_order.size(); ++column) {
            const int layer = idle_layer_display_order[column];
            const int id = first_layer + layer;
            control(L"BUTTON", layer_labels[layer], WS_TABSTOP | BS_AUTOCHECKBOX, id,
                    layer_left, 50, layer_widths[column], 14);
            CheckDlgButton(window_, id, (idle_layers.get() & (1ULL << layer)) ? BST_CHECKED : BST_UNCHECKED);
            layer_left += layer_widths[column];
        }
        control(L"EDIT", L"", WS_TABSTOP | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
                id_index_contents, 12, 85, 432, 170, WS_EX_CLIENTEDGE, grow_width | grow_height);
        control(L"STATIC", L"&Indexing activity", 0, id_activity, 12, 263, 204, 11, 0, bottom);
        control(L"EDIT", L"", WS_TABSTOP | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
                id_log, 12, 278, 432, 50, WS_EX_CLIENTEDGE, grow_width | bottom);
        SendDlgItemMessageW(window_, id_log, EM_SETLIMITTEXT, 524288, 0);
        control(L"BUTTON", L"Refresh cached pages", WS_TABSTOP | BS_AUTOCHECKBOX, id_refresh, 12, 340, 130, 16, 0, bottom);
        control(L"BUTTON", L"&Open release", WS_TABSTOP, id_release, 274, 340, 62, 14, 0, right | bottom);
        control(L"BUTTON", L"Stop", WS_TABSTOP, id_stop, 340, 340, 50, 14, 0, right | bottom);
        control(L"BUTTON", L"Close", WS_TABSTOP, IDCANCEL, 394, 340, 50, 14, 0, right | bottom);
        ui_config_ = ui_config_manager::tryGet();
        if (ui_config_) {
            ui_config_->add_callback(this);
            theme_registered_ = true;
        }
        dark_hooks_.AddDialog(window_);
        for (int id : {id_query, id_pages, id_list_view, id_art_view, id_back, id_forward, id_month, id_all_time,
                       IDOK, id_log, id_search_view, id_index, id_index_contents, id_refresh, id_idle, id_release, id_stop,
                       id_sort, id_tag_filter, id_format_filter, id_ready_filter, IDCANCEL})
            dark_hooks_.AddCtrlAuto(GetDlgItem(window_, id));
        for (std::size_t layer = 0; layer < layer_labels.size(); ++layer)
            dark_hooks_.AddCtrlAuto(GetDlgItem(window_, first_layer + static_cast<int>(layer)));
        if (!SetWindowSubclass(GetDlgItem(window_, id_results), result_input, 1, reinterpret_cast<DWORD_PTR>(this)))
            append(L"Drag and drop is unavailable for this window.");
        SetWindowSubclass(grid, result_input, 1, reinterpret_cast<DWORD_PTR>(this));
        apply_fonts();
        apply_palette();
        fit_work_area();
        layout();
        show_index(false);
        EnableWindow(GetDlgItem(window_, id_stop), FALSE);
        EnableWindow(GetDlgItem(window_, id_release), FALSE);
        apply_site_icon();
        for (auto child : {query_edit_, GetDlgItem(window_, id_query), GetDlgItem(window_, id_pages),
                           GetDlgItem(window_, id_log), GetDlgItem(window_, id_index_contents)})
            if (child) SetWindowSubclass(child, history_input, 2, reinterpret_cast<DWORD_PTR>(window_));
        SetFocus(query_edit_);
        start_tag_suggestions();
        SetTimer(window_, charts_timer, charts_start_delay, nullptr);
    }

    HWND active_control(const PlacedControl& control) const {
        return control.id == -1 ? control.window : GetDlgItem(window_, control.id);
    }

    void fit_work_area() {
        MONITORINFO monitor{};
        monitor.cbSize = sizeof monitor;
        RECT bounds{};
        if (!GetMonitorInfoW(MonitorFromWindow(window_, MONITOR_DEFAULTTONEAREST), &monitor) ||
            !GetWindowRect(window_, &bounds))
            return;
        const RECT work = monitor.rcWork;
        const LONG width = std::min(bounds.right - bounds.left, work.right - work.left);
        const LONG height = std::min(bounds.bottom - bounds.top, work.bottom - work.top);
        const LONG x = std::clamp(bounds.left, work.left, work.right - width);
        const LONG y = std::clamp(bounds.top, work.top, work.bottom - height);
        SetWindowPos(window_, nullptr, x, y, width, height, SWP_NOACTIVATE | SWP_NOZORDER);
    }

    void layout() {
        RECT client{}, standard{0, 0, dialog_width, dialog_height};
        GetClientRect(window_, &client);
        MapDialogRect(window_, &standard);
        const LONG dx = client.right - standard.right;
        const LONG dy = client.bottom - standard.bottom;
        for (const auto& control : controls_) {
            RECT box = control.units;
            if (!index_visible_ && source_ != SearchSource::query) {
                if (control.id == id_results || control.id == id_grid) box.top -= 20;
                if (control.id == id_results_label || control.id == id_list_view || control.id == id_art_view ||
                    (control.id >= id_sort && control.id <= id_sort_label)) OffsetRect(&box, 0, -20);
            }
            MapDialogRect(window_, &box);
            if (control.anchors & (right | grow_width)) box.right += dx;
            if (control.anchors & right) box.left += dx;
            if (control.anchors & (bottom | grow_height)) box.bottom += dy;
            if (control.anchors & bottom) box.top += dy;
            MoveWindow(active_control(control), box.left, box.top,
                       std::max<LONG>(0, box.right - box.left), std::max<LONG>(0, box.bottom - box.top), TRUE);
        }
        if (view_ == album_art_view && !index_visible_) {
            rebuild_tiles();
            ListView_Arrange(GetDlgItem(window_, id_grid), LVA_DEFAULT);
        }
    }

    void apply_fonts() {
        const auto standard = reinterpret_cast<HFONT>(SendMessageW(window_, WM_GETFONT, 0, 0));
        const auto fallback = standard ? standard : static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
        const auto configured = ui_config_ ? ui_config_->query_font(ui_font_default) : nullptr;
        const auto list = ui_config_ ? ui_config_->query_font(ui_font_lists) : nullptr;
        body_font_ = configured ? configured : fallback;
        list_font_ = list ? list : body_font_;
        auto old_bold = bold_font_;
        LOGFONTW spec{};
        bold_font_ = nullptr;
        if (GetObjectW(list_font_, sizeof spec, &spec) == sizeof spec) {
            spec.lfWeight = std::max<LONG>(spec.lfWeight, FW_SEMIBOLD);
            bold_font_ = CreateFontIndirectW(&spec);
        }
        for (const auto& control : controls_) {
            auto font = body_font_;
            if ((control.id == id_results_label || control.id == id_activity ||
                 control.id == id_coverage_label) && bold_font_) font = bold_font_;
            if (control.id == id_results) font = list_font_;
            SendMessageW(active_control(control), WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
        }
        if (old_bold) DeleteObject(old_bold);

        const auto hwnd = GetDlgItem(window_, id_results);
        if (auto dc = GetDC(hwnd)) {
            const auto previous = SelectObject(dc, list_font_);
            TEXTMETRICW metrics{};
            GetTextMetricsW(dc, &metrics);
            SelectObject(dc, previous);
            ReleaseDC(hwnd, dc);
            RECT padding{0, 0, 4, 8};
            MapDialogRect(window_, &padding);
            row_padding_ = padding.right;
            row_text_height_ = std::max<LONG>(metrics.tmHeight + metrics.tmExternalLeading, padding.bottom);
            update_row_height();
        }
    }

    void update_row_height() {
        cover_size_ = MulDiv(row_text_height_ * 3, zoom_percent(list_zoom), 100);
        const LONG text = row_text_height_ * 2;
        SendDlgItemMessageW(window_, id_results, LB_SETITEMHEIGHT, 0,
                            std::max(text, cover_size_) + row_padding_ * 2 + 2);
        InvalidateRect(GetDlgItem(window_, id_results), nullptr, TRUE);
    }

    static int zoom_percent(cfg_int& setting) {
        return static_cast<int>(std::clamp<std::int64_t>(setting.get(), 55, 200));
    }

    void rebuild_tiles() {
        const auto grid = GetDlgItem(window_, id_grid);
        if (!grid) return;
        RECT viewport{};
        GetClientRect(grid, &viewport);
        auto width = viewport.right;
        if (!(GetWindowLongPtrW(grid, GWL_STYLE) & WS_VSCROLL)) width -= GetSystemMetrics(SM_CXVSCROLL);
        const int pixels = album_zoom.get() == 100 ?
            doujin::automatic_tile_edge(static_cast<int>(width), static_cast<int>(viewport.bottom), dpi_) :
            MulDiv(MulDiv(108, dpi_, 96), zoom_percent(album_zoom), 100);
        if (tiles_ && pixels == tile_pixels_) return;
        const auto next = ImageList_Create(pixels, pixels, ILC_COLOR32, 12, 12);
        if (!next) return;
        const auto focused = ListView_GetNextItem(grid, -1, LVNI_FOCUSED);
        const auto old = tiles_;
        tiles_ = next;
        tile_pixels_ = pixels;
        SendMessageW(grid, WM_SETREDRAW, FALSE, 0);
        ListView_SetImageList(grid, tiles_, LVSIL_NORMAL);
        ListView_SetIconSpacing(grid, pixels + MulDiv(26, dpi_, 96), pixels + MulDiv(38, dpi_, 96));
        for (auto& row : rows_) row.tile_image = I_IMAGENONE;
        for (int index = 0; index < static_cast<int>(visible_.size()); ++index) sync_tile(index);
        ListView_Arrange(grid, LVA_DEFAULT);
        if (focused >= 0) ListView_EnsureVisible(grid, focused, FALSE);
        SendMessageW(grid, WM_SETREDRAW, TRUE, 0);
        RedrawWindow(grid, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE);
        if (old) ImageList_Destroy(old);
    }

    void zoom(int steps) {
        auto& setting = view_ == list_view ? list_zoom : album_zoom;
        // A first step away from automatic zoom starts from the size on screen.
        const int previous = view_ == album_art_view && setting.get() == 100 && tiles_
            ? MulDiv(tile_pixels_, 100, MulDiv(108, dpi_, 96)) : zoom_percent(setting);
        const int next = std::clamp(previous + 15 * steps, 55, 200);
        if (next == previous) return;
        setting = view_ == album_art_view && next == 100 ? 101 : next;
        if (view_ == list_view) update_row_height();
        else rebuild_tiles();
    }

    bool consume_zoom(UINT message, WPARAM wp, LPARAM lp) {
        if (index_visible_) return false;
        if (message == WM_MOUSEWHEEL && (GET_KEYSTATE_WPARAM(wp) & MK_CONTROL)) {
            wheel_delta_ += GET_WHEEL_DELTA_WPARAM(wp);
            const int steps = wheel_delta_ / WHEEL_DELTA;
            wheel_delta_ %= WHEEL_DELTA;
            if (steps) zoom(steps);
            return true;
        }
        if (message == WM_GESTURE) {
            const auto handle = reinterpret_cast<HGESTUREINFO>(lp);
            GESTUREINFO info{};
            info.cbSize = sizeof info;
            if (!GetGestureInfo(handle, &info) || info.dwID != GID_ZOOM) return false;
            const auto distance = static_cast<double>(info.ullArguments);
            if ((info.dwFlags & GF_BEGIN) || pinch_distance_ <= 0) pinch_distance_ = distance;
            constexpr double step = 1.14;
            if (distance > 0 && pinch_distance_ > 0) {
                const auto steps = static_cast<int>(std::log(distance / pinch_distance_) / std::log(step));
                if (steps) {
                    zoom(steps);
                    pinch_distance_ *= std::pow(step, steps);
                }
            }
            if (info.dwFlags & GF_END) pinch_distance_ = 0;
            CloseGestureInfoHandle(handle);
            return true;
        }
        return false;
    }

    void apply_palette() {
        HIGHCONTRASTW contrast{};
        contrast.cbSize = sizeof contrast;
        const bool high_contrast = SystemParametersInfoW(SPI_GETHIGHCONTRAST, sizeof contrast, &contrast, 0) &&
            (contrast.dwFlags & HCF_HIGHCONTRASTON);
        const bool dark = !high_contrast && ui_config_ && ui_config_->is_dark_mode();
        const auto from_host = [&](const GUID& id, COLORREF fallback) -> COLORREF {
            t_ui_color result;
            return !high_contrast && ui_config_ && ui_config_->query_color(id, result) ? result : fallback;
        };
        canvas_ = from_host(ui_color_background, dark ? RGB(31, 31, 31) : GetSysColor(COLOR_WINDOW));
        text_ = from_host(ui_color_text, dark ? RGB(235, 235, 235) : GetSysColor(COLOR_WINDOWTEXT));
        selected_ = from_host(ui_color_selection, GetSysColor(COLOR_HIGHLIGHT));
        selected_text_ = high_contrast ? GetSysColor(COLOR_HIGHLIGHTTEXT) :
            (luminance(selected_) >= .179 ? RGB(0, 0, 0) : RGB(255, 255, 255));
        surface_color_ = high_contrast ? canvas_ : mix(canvas_, text_, dark ? 7 : 3);
        muted_ = legible(mix(text_, surface_color_, 23), surface_color_, text_);
        accent_ = high_contrast ? text_ :
            legible(mix(text_, dark ? RGB(114, 179, 195) : RGB(40, 68, 88), 64), canvas_, text_);
        if (high_contrast) muted_ = text_;
        const auto replace_brush = [](HBRUSH& brush, COLORREF color) {
            if (brush) DeleteObject(brush);
            brush = CreateSolidBrush(color);
        };
        replace_brush(background_, canvas_);
        replace_brush(surface_, surface_color_);
        replace_brush(accent_brush_, accent_);
        SetWindowTheme(GetDlgItem(window_, id_results), dark ? L"DarkMode_Explorer" : nullptr, nullptr);
        const auto grid = GetDlgItem(window_, id_grid);
        SetWindowTheme(grid, dark ? L"DarkMode_Explorer" : nullptr, nullptr);
        ListView_SetBkColor(grid, surface_color_);
        ListView_SetTextBkColor(grid, CLR_NONE);
        ListView_SetTextColor(grid, text_);
        RedrawWindow(window_, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN);
    }

    void ui_colors_changed() override { if (window_) apply_palette(); }
    void ui_fonts_changed() override { if (window_) apply_fonts(); }

    INT_PTR control_colors(UINT message, HDC dc, HWND child) const {
        const int id = child == window_ ? 0 : GetDlgCtrlID(child);
        const bool field = message == WM_CTLCOLOREDIT || message == WM_CTLCOLORLISTBOX || id == id_log ||
                           id == id_index_contents;
        SetBkColor(dc, field ? surface_color_ : canvas_);
        SetTextColor(dc, text_);
        return reinterpret_cast<INT_PTR>(field ? surface_ : background_);
    }

    void draw_result(const DRAWITEMSTRUCT& draw) const {
        if (draw.itemID == static_cast<UINT>(-1)) return;
        const auto index = static_cast<std::size_t>(draw.itemID);
        if (index >= visible_.size()) return;
        const auto& row = visible_row(index);
        const RECT row_box = draw.rcItem;
        HDC dc = draw.hDC;
        const int saved = SaveDC(dc);
        if (!saved) return;
        const bool selection = (draw.itemState & ODS_SELECTED) != 0;
        const auto background = selection ? selected_ : surface_color_;
        SetDCBrushColor(dc, background);
        FillRect(dc, &row_box, static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
        const RECT stripe{row_box.left, row_box.top, row_box.left + 2, row_box.bottom};
        if (accent_brush_) FillRect(dc, &stripe, accent_brush_);
        if (selection) {
            const RECT bottom{row_box.left, row_box.bottom - 1, row_box.right, row_box.bottom};
            FillRect(dc, &bottom, accent_brush_ ? accent_brush_ : static_cast<HBRUSH>(GetStockObject(WHITE_BRUSH)));
        }
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, selection ? selected_text_ : text_);
        SelectObject(dc, bold_font_ ? bold_font_ : list_font_);
        constexpr UINT draw_flags = DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_END_ELLIPSIS;
        const LONG row_middle = (row_box.top + row_box.bottom) / 2;
        LONG title_left = row_box.left + row_padding_ * 2;
        {
            RECT tile{title_left, row_middle - cover_size_ / 2,
                      title_left + cover_size_, row_middle - cover_size_ / 2 + cover_size_};
            if (row.cover) {
                if (auto memory = CreateCompatibleDC(dc)) {
                    const auto old = SelectObject(memory, row.cover.get());
                    SetStretchBltMode(dc, HALFTONE);
                    StretchBlt(dc, tile.left, tile.top, cover_size_, cover_size_, memory, 0, 0, cover_pixels_, cover_pixels_, SRCCOPY);
                    SelectObject(memory, old);
                    DeleteDC(memory);
                }
            } else {
                SetDCBrushColor(dc, mix(background, selection ? selected_text_ : text_, 15));
                FillRect(dc, &tile, static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
            }
            title_left += cover_size_ + row_padding_ * 2;
        }
        RECT title{title_left, row_middle - row_text_height_, row_box.right - row_padding_ * 2, row_middle};
        if (!row.format.empty()) {
            SIZE format_size{};
            GetTextExtentPoint32W(dc, row.format.c_str(), static_cast<int>(row.format.size()), &format_size);
            constexpr LONG maximum_chip_fraction = 4;
            const LONG chip_width = std::min<LONG>(format_size.cx + row_padding_ * 2,
                (title.right - title.left) / maximum_chip_fraction);
            RECT badge{title.right - chip_width, title.top, title.right, title.bottom};
            if (!selection) {
                SetDCBrushColor(dc, mix(surface_color_, accent_, 17));
                FillRect(dc, &badge, static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
                SetTextColor(dc, text_);
            }
            DrawTextW(dc, row.format.c_str(), -1, &badge, draw_flags | DT_CENTER);
            title.right -= chip_width + row_padding_;
        }
        DrawTextW(dc, row.title.c_str(), -1, &title, draw_flags);
        SelectObject(dc, list_font_);
        SetTextColor(dc, selection ? selected_text_ : muted_);
        RECT details{title.left, title.bottom, row_box.right - row_padding_ * 2, title.bottom + row_text_height_};
        std::wstring line = row.artist;
        if (!row.rank.empty()) {
            if (!line.empty()) line += L"  ·  ";
            line += row.rank;
        }
        if (!row.date.empty()) {
            if (!line.empty()) line += L"  ·  ";
            line += row.date;
        }
        if (!row.tracks.empty()) {
            if (!line.empty()) line += L"  ·  ";
            line += std::to_wstring(row.tracks.size()) + (row.tracks.size() == 1 ? L" track" : L" tracks");
        }
        if (!row.tags.empty()) {
            if (!line.empty()) line += L"  ·  ";
            line += row.tags;
        }
        if (doujin::match_result(row, result_filter_, search_running_, compare_text) == doujin::FilterMatch::pending) {
            if (!line.empty()) line += L"  ·  ";
            line += L"Checking";
        }
        DrawTextW(dc, line.c_str(), -1, &details, draw_flags);
        if (draw.itemState & ODS_FOCUS) {
            RECT focus = row_box;
            InflateRect(&focus, -1, -1);
            DrawFocusRect(dc, &focus);
        }
        RestoreDC(dc, saved);
    }

    void append(std::wstring_view line) {
        HWND log = GetDlgItem(window_, id_log);
        auto text = std::wstring(line) + L"\r\n";
        const auto end = GetWindowTextLengthW(log);
        SendMessageW(log, EM_SETSEL, end, end);
        SendMessageW(log, EM_REPLACESEL, FALSE, reinterpret_cast<LPARAM>(text.c_str()));
        SendMessageW(log, EM_SCROLLCARET, 0, 0);
    }

    static bool valid_id(std::string_view id) {
        return !id.empty() && id.size() <= 16 &&
            std::all_of(id.begin(), id.end(), [](char c) { return c >= '0' && c <= '9'; });
    }

    auto find_row(const std::wstring& id) {
        return std::find_if(rows_.begin(), rows_.end(), [&](const auto& row) { return row.id == id; });
    }

    ReleaseRow& visible_row(std::size_t index) { return rows_[visible_[index]]; }
    const ReleaseRow& visible_row(std::size_t index) const { return rows_[visible_[index]]; }

    void repaint_row(std::size_t index) {
        RECT rect{};
        const auto list = GetDlgItem(window_, id_results);
        if (SendMessageW(list, LB_GETITEMRECT, index, reinterpret_cast<LPARAM>(&rect)) != LB_ERR)
            InvalidateRect(list, &rect, FALSE);
    }

    using Bitmap = std::shared_ptr<std::remove_pointer_t<HBITMAP>>;

    Bitmap decode_bitmap(const fs::path& path, UINT size) {
        if (!wic_.is_valid() && FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                                        __uuidof(IWICImagingFactory), wic_.receive_void_ptr())))
            return {};
        pfc::com_ptr_t<IWICBitmapDecoder> decoder;
        pfc::com_ptr_t<IWICBitmapFrameDecode> frame;
        pfc::com_ptr_t<IWICBitmapScaler> scaler;
        pfc::com_ptr_t<IWICFormatConverter> pixels;
        UINT width = 0, height = 0;
        if (FAILED(wic_->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand,
                                                  decoder.receive_ptr())) ||
            FAILED(decoder->GetFrame(0, frame.receive_ptr())) || FAILED(frame->GetSize(&width, &height)) ||
            width == 0 || height == 0 || width > 1024 || height > 1024)
            return {};
        if (FAILED(wic_->CreateBitmapScaler(scaler.receive_ptr())) ||
            FAILED(scaler->Initialize(frame.get_ptr(), size, size, WICBitmapInterpolationModeFant)) ||
            FAILED(wic_->CreateFormatConverter(pixels.receive_ptr())) ||
            FAILED(pixels->Initialize(scaler.get_ptr(), GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
                                      nullptr, 0, WICBitmapPaletteTypeCustom)))
            return {};
        BITMAPINFO info{};
        info.bmiHeader.biSize = sizeof info.bmiHeader;
        info.bmiHeader.biWidth = size;
        info.bmiHeader.biHeight = -static_cast<LONG>(size);
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        void* data = nullptr;
        auto bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &data, nullptr, 0);
        if (!bitmap) return {};
        std::shared_ptr<std::remove_pointer_t<HBITMAP>> image(bitmap, [](HBITMAP value) { DeleteObject(value); });
        if (FAILED(pixels->CopyPixels(nullptr, size * 4, size * size * 4, static_cast<BYTE*>(data)))) return {};
        return image;
    }

    void apply_site_icon() {
        if (site_icon_ || !window_) return;
        const auto size = static_cast<UINT>(std::max(16, GetSystemMetrics(SM_CXSMICON)));
        const auto bitmap = decode_bitmap(profile_directory() / L"Artwork" / L"site-icon.img", size);
        if (!bitmap) return;
        const auto stride = ((size + 15) / 16) * 2;
        std::vector<unsigned char> mask_bits(stride * size, 0);
        const auto mask = CreateBitmap(size, size, 1, 1, mask_bits.data());
        if (!mask) return;
        ICONINFO icon{TRUE, 0, 0, mask, bitmap.get()};
        site_icon_ = CreateIconIndirect(&icon);
        DeleteObject(mask);
        if (site_icon_) SendMessageW(window_, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(site_icon_));
    }

    void sync_tile(int index, bool insert = false) {
        auto& row = visible_row(index);
        if (row.cover && row.tile_image < 0 && tiles_) {
            if (const auto scaled = static_cast<HBITMAP>(
                    CopyImage(row.cover.get(), IMAGE_BITMAP, tile_pixels_, tile_pixels_, LR_CREATEDIBSECTION))) {
                row.tile_image = ImageList_Add(tiles_, scaled, nullptr);
                DeleteObject(scaled);
            }
        }
        auto label = row.rank.empty() ? row.title : row.rank.substr(0, row.rank.find(L' ')) + L" " + row.title;
        if (doujin::match_result(row, result_filter_, search_running_, compare_text) == doujin::FilterMatch::pending)
            label += L" · Checking";
        LVITEMW item{};
        item.mask = LVIF_TEXT | LVIF_IMAGE;
        item.iItem = index;
        item.pszText = label.data();
        item.iImage = row.tile_image;
        const auto grid = GetDlgItem(window_, id_grid);
        if (insert) ListView_InsertItem(grid, &item);
        else ListView_SetItem(grid, &item);
    }

    void accept_cover(std::string_view id) {
        if (!valid_id(id)) return;
        auto row = find_row(wide(id));
        if (row == rows_.end() || row->cover || art_folder_.empty()) return;
        row->cover = decode_bitmap(art_folder_ / (row->id + L".jpg"), cover_pixels_);
        if (!row->cover) return;
        row->changed = true;
        projection_dirty_ = true;
    }

    void accept_track(std::string_view line, bool updated = false) {
        const auto tab = line.find('\t');
        if (tab == line.npos || !valid_id(line.substr(0, tab))) return;
        const auto name = line.substr(tab + 1), id = line.substr(0, tab);
        if (doujin::release_id_from_reference_path(name) != id) return;
        auto value = wide(name);
        if (updated) {
            doujin::host_trace::emit("component.ui.search.track.updated");
            CatalogLookupQueue::get().signal(std::string(id), 3);
            updated_tracks_.push_back(std::move(value));
            return;
        }
        auto row = find_row(wide(id));
        if (row == rows_.end()) return;
        if (std::find(row->tracks.begin(), row->tracks.end(), value) == row->tracks.end()) {
            row->tracks.push_back(std::move(value));
            doujin::host_trace::emit("component.ui.search.track.projected");
            row->changed = true;
            projection_dirty_ = true;
        }
    }

    void accept_chart_ids(std::string_view ids) {
        std::vector<std::wstring> next;
        while (!ids.empty()) {
            const auto tab = ids.find('\t');
            const auto id = ids.substr(0, tab);
            if (!valid_id(id)) return;
            next.push_back(wide(id));
            if (tab == ids.npos) break;
            ids.remove_prefix(tab + 1);
        }
        next_chart_ids_ = std::move(next);
    }

    void accept_line(std::string_view line, bool existing_only = false) {
        if (line.ends_with('\r')) line.remove_suffix(1);
        if (line == "\x1b") {
            if (index_visible_) refresh_index();
            return;
        }
        if (line.starts_with('\x15')) { accept_chart_ids(line.substr(1)); return; }
        if (line.starts_with('\x1d')) { accept_cover(line.substr(1)); return; }
        if (line == "\x17") { apply_site_icon(); return; }
        if (line.starts_with('\x1a')) { CatalogLookupQueue::get().signal(std::string(line.substr(1)), 3); return; }
        if (line.starts_with('\x1c')) { accept_track(line.substr(1)); return; }
        if (line.starts_with('\x19')) { accept_track(line.substr(1), true); return; }
        if (!line.starts_with('\x1e')) {
            append(wide(line));
            return;
        }
        line.remove_prefix(1);
        std::array<std::string_view, 7> fields{};
        for (auto& field : fields) {
            const auto delimiter = line.find('\t');
            if (delimiter == line.npos) return;
            field = line.substr(0, delimiter);
            line.remove_prefix(delimiter + 1);
        }
        if (!valid_id(fields[0]) || fields[1].empty() || line.find('\t') != line.npos) return;
        ReleaseRow result{wide(fields[0]), wide(fields[1]), wide(fields[2]), wide(fields[3]), wide(fields[4]),
                          wide(fields[5]), wide(fields[6])};
        const auto previous = find_row(result.id);
        if (existing_only && previous == rows_.end()) return;
        CatalogLookupQueue::get().signal(std::string(fields[0]), 2);
        while (!line.empty()) {
            const auto separator = line.find('\x1f');
            if (separator != 0) result.tag_values.push_back(wide(line.substr(0, separator)));
            if (separator == line.npos) break;
            line.remove_prefix(separator + 1);
        }
        if (previous != rows_.end()) {
            if (existing_only) result.rank = std::move(previous->rank);
            result.tracks = std::move(previous->tracks);
            result.cover = std::move(previous->cover);
            result.tile_image = previous->tile_image;
            *previous = std::move(result);
        } else rows_.push_back(std::move(result));
        projection_dirty_ = true;
        facets_dirty_ = true;
    }

    bool insert_result(int index) {
        const auto& row = visible_row(index);
        auto accessible_text = row.title;
        for (const auto* value : {&row.artist, &row.format, &row.tags, &row.date})
            if (!value->empty()) accessible_text += L" · " + *value;
        if (SendDlgItemMessageW(window_, id_results, LB_ADDSTRING, 0,
                               reinterpret_cast<LPARAM>(accessible_text.c_str())) < 0) return false;
        sync_tile(index, true);
        return true;
    }

    void update_results_label() {
        std::wstring label = source_ == SearchSource::month ? L"This Month" :
                             source_ == SearchSource::all_time ? L"All Time" : L"Results";
        if (location_) {
            label += L" (" + std::to_wstring(visible_.size() - pending_results_);
            if (visible_.size() != rows_.size() || pending_results_) label += L"/" + std::to_wstring(rows_.size());
            label += L")";
            if (pending_results_) label += L"  ·  " + std::to_wstring(pending_results_) + L" checking";
        }
        SetDlgItemTextW(window_, id_results_label, label.c_str());
    }

    void update_filter_options() {
        const auto populate = [&](int id, const wchar_t* any, const auto& picked, auto values) {
            const auto combo = GetDlgItem(window_, id);
            if (SendMessageW(combo, CB_GETDROPPEDSTATE, 0, 0)) return false;
            if (!picked.empty()) values.push_back(picked);
            std::sort(values.begin(), values.end(), [](const auto& a, const auto& b) { return compare_text(a, b) < 0; });
            values.erase(std::unique(values.begin(), values.end(), same_text), values.end());
            SendMessageW(combo, CB_RESETCONTENT, 0, 0);
            SendMessageW(combo, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(any));
            LRESULT selected = 0;
            for (const auto& value : values) {
                const auto index = SendMessageW(combo, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(value.c_str()));
                if (index >= 0 && same_text(value, picked)) selected = index;
            }
            SendMessageW(combo, CB_SETCURSEL, selected, 0);
            return true;
        };
        std::vector<std::wstring> tags, formats;
        for (const auto& row : rows_) {
            tags.insert(tags.end(), row.tag_values.begin(), row.tag_values.end());
            if (!row.format.empty()) formats.push_back(row.format);
        }
        const bool tags_updated = populate(id_tag_filter, L"Any tag", result_filter_.tag, std::move(tags));
        const bool formats_updated = populate(id_format_filter, L"Any format", result_filter_.format, std::move(formats));
        facets_dirty_ = !tags_updated || !formats_updated;
        if (!SendDlgItemMessageW(window_, id_sort, CB_GETDROPPEDSTATE, 0, 0))
            SendDlgItemMessageW(window_, id_sort, CB_SETCURSEL, static_cast<int>(result_filter_.sort), 0);
        CheckDlgButton(window_, id_ready_filter, result_filter_.ready ? BST_CHECKED : BST_UNCHECKED);
    }

    void apply_filters() {
        const auto picked = [&](int id) {
            return SendDlgItemMessageW(window_, id, CB_GETCURSEL, 0, 0) > 0 ? text_of(window_, id) : std::wstring{};
        };
        const auto sort = std::max<LRESULT>(0, SendDlgItemMessageW(window_, id_sort, CB_GETCURSEL, 0, 0));
        result_filter_ = {static_cast<doujin::ResultSort>(sort), picked(id_tag_filter), picked(id_format_filter),
                          IsDlgButtonChecked(window_, id_ready_filter) == BST_CHECKED};
        projection_dirty_ = true;
        apply_pending_results();
    }

    ResultPosition result_position() const {
        ResultPosition position;
        const auto id = [&](int index) -> std::wstring {
            return index >= 0 && static_cast<std::size_t>(index) < visible_.size()
                ? visible_row(index).id : std::wstring{};
        };
        for (const auto index : selection())
            if (auto selected = id(index); !selected.empty()) position.selected.push_back(std::move(selected));
        position.focused = id(focused_result());
        if (view_ == list_view) {
            position.top = id(static_cast<int>(SendDlgItemMessageW(window_, id_results, LB_GETTOPINDEX, 0, 0)));
        } else {
            const auto grid = GetDlgItem(window_, id_grid);
            for (int index = 0; index < static_cast<int>(visible_.size()); ++index) {
                RECT box{};
                if (ListView_GetItemRect(grid, index, &box, LVIR_BOUNDS) && box.bottom > 0) {
                    position.top = id(index);
                    break;
                }
            }
        }
        return position;
    }

    void project_results(std::optional<ResultPosition> restore = std::nullopt) {
        auto projection = doujin::project_results(rows_, result_filter_, search_running_, compare_text);
        const bool pending_changed = pending_results_ != projection.pending;
        if (restore || projection.indices != visible_) {
            const auto position = restore ? std::move(*restore) : result_position();
            const auto list = GetDlgItem(window_, id_results), grid = GetDlgItem(window_, id_grid);
            const auto active = !index_visible_ ? (view_ == list_view ? list : grid) : nullptr;
            if (active) SendMessageW(active, WM_SETREDRAW, FALSE, 0);
            SendMessageW(list, LB_RESETCONTENT, 0, 0);
            ListView_DeleteAllItems(grid);
            visible_ = std::move(projection.indices);
            std::vector<int> selected;
            int focused = -1, top = -1;
            for (int index = 0; index < static_cast<int>(visible_.size()); ++index) {
                const auto& id = visible_row(index).id;
                if (std::find(position.selected.begin(), position.selected.end(), id) != position.selected.end())
                    selected.push_back(index);
                if (id == position.focused) focused = index;
                if (id == position.top) top = index;
                insert_result(index);
                visible_row(index).changed = false;
            }
            ListView_Arrange(grid, LVA_DEFAULT);
            apply_selection(selected, focused, top);
            if (active) SendMessageW(active, WM_SETREDRAW, TRUE, 0);
            InvalidateRect(list, nullptr, TRUE);
            InvalidateRect(grid, nullptr, TRUE);
            EnableWindow(GetDlgItem(window_, id_release), !index_visible_ && !selected.empty());
        } else {
            for (std::size_t index = 0; index < visible_.size(); ++index) {
                if (!std::exchange(visible_row(index).changed, false) && !pending_changed) continue;
                sync_tile(static_cast<int>(index));
                repaint_row(index);
            }
        }
        pending_results_ = projection.pending;
        update_results_label();
        projection_dirty_ = false;
    }

    void apply_pending_results() {
        if (drag_armed_ || dragging_) return;
        std::optional<ResultPosition> position;
        if (!next_chart_ids_.empty()) {
            // The chart frame and its rows can span multiple reads from the pipe.
            if (!std::all_of(next_chart_ids_.begin(), next_chart_ids_.end(),
                             [&](const auto& id) { return find_row(id) != rows_.end(); })) return;
            position = result_position();
            std::vector<ReleaseRow> ordered;
            for (const auto& id : next_chart_ids_)
                ordered.push_back(std::move(*find_row(id)));
            rows_ = std::move(ordered);
            next_chart_ids_.clear();
            projection_dirty_ = facets_dirty_ = true;
        }
        if (facets_dirty_) update_filter_options();
        if (projection_dirty_) project_results(std::move(position));
    }

    std::vector<int> selection() const {
        if (view_ == album_art_view) {
            std::vector<int> selected;
            const auto grid = GetDlgItem(window_, id_grid);
            for (int item = -1; (item = ListView_GetNextItem(grid, item, LVNI_SELECTED)) >= 0;)
                selected.push_back(item);
            return selected;
        }
        const auto list = GetDlgItem(window_, id_results);
        const auto count = SendMessageW(list, LB_GETSELCOUNT, 0, 0);
        if (count <= 0) return {};
        std::vector<int> indices(static_cast<std::size_t>(count));
        const auto read = SendMessageW(list, LB_GETSELITEMS, count, reinterpret_cast<LPARAM>(indices.data()));
        if (read < 0) return {};
        indices.resize(static_cast<std::size_t>(read));
        return indices;
    }

    void open_release() {
        const auto selected = selection();
        if (selected.empty()) return;
        auto index = view_ == album_art_view ? ListView_GetNextItem(GetDlgItem(window_, id_grid), -1, LVNI_FOCUSED) :
            SendDlgItemMessageW(window_, id_results, LB_GETCARETINDEX, 0, 0);
        if (std::find(selected.begin(), selected.end(), index) == selected.end()) index = selected.front();
        if (index < 0 || static_cast<std::size_t>(index) >= visible_.size()) return;
        doujin::host_trace::emit("component.ui.search.open_release");
        open_release_url(visible_row(index).id);
    }

    void open_release_url(const std::wstring& id) {
        const auto url = L"https://doujinstyle.com/?p=page&type=1&id=" + id;
        if (reinterpret_cast<INT_PTR>(ShellExecuteW(window_, L"open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL)) <= 32)
            append(L"Could not open the release in your browser.");
    }

    void drag_tracks(HWND list) {
        doujin::host_trace::emit("component.ui.search.drag.request");
        metadb_handle_list handles;
        for (int index : drag_rows_) {
            if (index < 0 || static_cast<std::size_t>(index) >= visible_.size()) continue;
            for (const auto& track : visible_row(index).tracks) {
                pfc::string8 path;
                filesystem::g_get_canonical_path(utf8((track_folder_ / track).wstring()).c_str(), path);
                handles.add_item(metadb::get()->handle_create(path.c_str(), 0));
            }
        }
        if (handles.get_count() == 0) {
            doujin::host_trace::emit("component.ui.search.drag.no_tracks");
            append(L"Selected releases have no published tracks yet.");
            return;
        }
        auto data = ole_interaction::get()->create_dataobject(handles);
        if (!data.is_valid()) return;
        SendMessageW(list, WM_CANCELMODE, 0, 0);
        DWORD effect = DROPEFFECT_NONE;
        doujin::host_trace::emit("component.ui.search.drag.has_tracks");
        dragging_ = true;
        SHDoDragDrop(list, data.get_ptr(), nullptr, DROPEFFECT_COPY, &effect);
        dragging_ = false;
        if (effect == DROPEFFECT_COPY) doujin::host_trace::emit("component.ui.search.drag.drop_accepted");
        if (window_) apply_pending_results();
    }

    // Share Qobuz's side-button semantics: dispatch once on release and also
    // support drivers that send WM_APPCOMMAND instead of mouse messages.
    static bool handle_history_mouse(HWND dialog, UINT message, WPARAM wp, LPARAM lp) {
        if (message == WM_XBUTTONDOWN || message == WM_XBUTTONDBLCLK)
            return GET_XBUTTON_WPARAM(wp) == XBUTTON1 || GET_XBUTTON_WPARAM(wp) == XBUTTON2;
        int command = 0;
        if (message == WM_XBUTTONUP) {
            if (GET_XBUTTON_WPARAM(wp) == XBUTTON1) command = id_back;
            if (GET_XBUTTON_WPARAM(wp) == XBUTTON2) command = id_forward;
        } else if (message == WM_APPCOMMAND) {
            if (GET_APPCOMMAND_LPARAM(lp) == APPCOMMAND_BROWSER_BACKWARD) command = id_back;
            if (GET_APPCOMMAND_LPARAM(lp) == APPCOMMAND_BROWSER_FORWARD) command = id_forward;
        }
        if (!command) return false;
        SendMessageW(dialog, WM_COMMAND, command, 0);
        return true;
    }

    static LRESULT CALLBACK history_input(HWND child, UINT message, WPARAM wp, LPARAM lp,
                                          UINT_PTR subclass, DWORD_PTR parent) {
        if (message == WM_NCDESTROY) RemoveWindowSubclass(child, history_input, subclass);
        if (handle_history_mouse(reinterpret_cast<HWND>(parent), message, wp, lp)) return TRUE;
        return DefSubclassProc(child, message, wp, lp);
    }

    static LRESULT CALLBACK result_input(HWND list, UINT msg, WPARAM wp, LPARAM lp,
                                          UINT_PTR subclass, DWORD_PTR reference) noexcept {
        auto self = reinterpret_cast<SearchDialog*>(reference);
        const auto keep_alive = self->shared_from_this();
        try {
            if (handle_history_mouse(self->window_, msg, wp, lp)) return TRUE;
            if (msg == WM_GESTURENOTIFY) {
                GESTURECONFIG config{GID_ZOOM, GC_ZOOM, 0};
                SetGestureConfig(list, 0, 1, &config, sizeof config);
            }
            if (self->consume_zoom(msg, wp, lp)) return 0;
            if (msg == WM_CONTEXTMENU) {
                self->result_menu(list, lp);
                return 0;
            }
            if (GetDlgCtrlID(list) == id_grid) {
                if (msg == WM_NCDESTROY) RemoveWindowSubclass(list, result_input, subclass);
                return DefSubclassProc(list, msg, wp, lp);
            }
            if (msg == WM_LBUTTONDOWN) {
                const auto hit = SendMessageW(list, LB_ITEMFROMPOINT, 0, lp);
                self->drag_armed_ = HIWORD(hit) == 0 && LOWORD(hit) < self->visible_.size();
                self->drag_origin_ = {GET_X_LPARAM(lp), GET_Y_LPARAM(lp)};
                auto selected = self->selection();
                if ((wp & (MK_CONTROL | MK_SHIFT)) != 0 ||
                    std::find(selected.begin(), selected.end(), LOWORD(hit)) == selected.end())
                    selected.clear();
                self->drag_rows_ = selected;
                if (self->drag_rows_.empty() && self->drag_armed_) self->drag_rows_.push_back(LOWORD(hit));
                const auto result = DefSubclassProc(list, msg, wp, lp);
                self->drag_rows_ = selected.empty() ? self->selection() : std::move(selected);
                return result;
            }
            if (msg == WM_MOUSEMOVE && self->drag_armed_ && (wp & MK_LBUTTON) &&
                (std::abs(GET_X_LPARAM(lp) - self->drag_origin_.x) >= GetSystemMetrics(SM_CXDRAG) ||
                 std::abs(GET_Y_LPARAM(lp) - self->drag_origin_.y) >= GetSystemMetrics(SM_CYDRAG))) {
                self->drag_armed_ = false;
                self->drag_tracks(list);
                return 0;
            }
            if (msg == WM_LBUTTONUP || msg == WM_CAPTURECHANGED) {
                self->drag_armed_ = false;
                const auto result = DefSubclassProc(list, msg, wp, lp);
                if (self->window_) self->apply_pending_results();
                return result;
            }
            if (msg == WM_NCDESTROY) RemoveWindowSubclass(list, result_input, subclass);
        } catch (const std::exception& error) {
            if (self->window_) self->append(wide(error.what()));
        }
        return DefSubclassProc(list, msg, wp, lp);
    }

    void result_menu(HWND list, LPARAM at) {
        POINT point{GET_X_LPARAM(at), GET_Y_LPARAM(at)};
        LRESULT item = LB_ERR;
        if (GetDlgCtrlID(list) == id_grid) {
            if (point.x == -1 && point.y == -1) {
                item = ListView_GetNextItem(list, -1, LVNI_FOCUSED);
                RECT tile{};
                if (item < 0 || !ListView_GetItemRect(list, item, &tile, LVIR_BOUNDS)) return;
                point = {tile.left + 12, tile.bottom};
                ClientToScreen(list, &point);
            } else {
                LVHITTESTINFO hit{};
                hit.pt = point;
                ScreenToClient(list, &hit.pt);
                item = ListView_HitTest(list, &hit);
            }
        } else if (point.x == -1 && point.y == -1) {
            item = SendMessageW(list, LB_GETCARETINDEX, 0, 0);
            RECT row{};
            if (item == LB_ERR || SendMessageW(list, LB_GETITEMRECT, item, reinterpret_cast<LPARAM>(&row)) == LB_ERR)
                return;
            point = {row.left + 12, row.bottom};
            ClientToScreen(list, &point);
        } else {
            auto client = point;
            ScreenToClient(list, &client);
            const auto hit = SendMessageW(list, LB_ITEMFROMPOINT, 0, MAKELPARAM(client.x, client.y));
            if (HIWORD(hit)) return;
            item = LOWORD(hit);
        }
        if (item < 0 || static_cast<std::size_t>(item) >= visible_.size()) return;
        const auto id = visible_row(item).id;
        const auto menu = CreatePopupMenu();
        if (!menu) return;
        AppendMenuW(menu, MF_STRING, 1, L"&Download archive to Downloads");
        AppendMenuW(menu, MF_STRING, 2, L"&Open release");
        AppendMenuW(menu, (!process_.value || idle_running_) ? MF_STRING : MF_GRAYED, 3, L"&Repair track metadata");
        const auto selected = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                                             point.x, point.y, 0, window_, nullptr);
        DestroyMenu(menu);
        if (!window_) return;
        if (selected == 2) open_release_url(id);
        if (selected == 3 && (!process_.value || idle_running_)) {
            if (idle_running_) finish();
            const auto cache = configured_path(cache_path, default_catalog);
            track_folder_ = configured_path(export_path, default_output);
            if (!cache.is_absolute() || !track_folder_.is_absolute())
                throw std::runtime_error("Set full paths in Preferences → Tools → DoujinStyle.");
            append(L"Rechecking archive headers for release " + id + L"…");
            auto arguments = L" --repair-release " + quote_command_arg(id) +
                L" --output " + quote_command_arg(track_folder_.wstring());
            launch_worker(cache, arguments, false);
            repairing_release_ = id;
        }
        if (selected != 1) return;
        if (idle_running_) finish();
        if (process_.value) {
            download_queue_.push_back(id);
            append(L"Archive download queued for release " + id + L".");
            return;
        }
        start_download(id);
    }

    void start_download(const std::wstring& id) {
        const auto cache = configured_path(cache_path, default_catalog);
        if (!cache.is_absolute()) throw std::runtime_error("Set a full catalog path in Preferences → Tools → DoujinStyle.");
        append(L"Resolving archive for release " + id + L"…");
        launch_worker(cache, L" --download-archive " + quote_command_arg(id) +
                      L" --output " + quote_command_arg(downloads_folder().wstring()), false);
    }

    void show_index(bool visible) {
        index_visible_ = visible;
        for (const int id : {id_list_view, id_art_view, id_results_label, id_release, id_refresh,
                             id_sort, id_sort_label, id_tag_filter, id_format_filter, id_ready_filter})
            ShowWindow(GetDlgItem(window_, id), visible ? SW_HIDE : SW_SHOW);
        for (const int id : {id_index_contents, id_coverage_label, id_idle_layers, id_idle})
            ShowWindow(GetDlgItem(window_, id), visible ? SW_SHOW : SW_HIDE);
        for (std::size_t layer = 0; layer < layer_labels.size(); ++layer)
            ShowWindow(GetDlgItem(window_, first_layer + static_cast<int>(layer)), visible ? SW_SHOW : SW_HIDE);
        configure_source();
        show_results();
        layout();
        update_history_buttons();
    }

    void show_results() {
        ShowWindow(GetDlgItem(window_, id_results), !index_visible_ && view_ == list_view ? SW_SHOW : SW_HIDE);
        ShowWindow(GetDlgItem(window_, id_grid), !index_visible_ && view_ == album_art_view ? SW_SHOW : SW_HIDE);
        CheckDlgButton(window_, id_list_view, view_ == list_view ? BST_CHECKED : BST_UNCHECKED);
        CheckDlgButton(window_, id_art_view, view_ == album_art_view ? BST_CHECKED : BST_UNCHECKED);
        EnableWindow(GetDlgItem(window_, id_release), !index_visible_ && !selection().empty());
    }

    void load_recent() {
        const auto stored = wide(recent_queries.get().c_str());
        std::wstring_view rest(stored);
        while (!rest.empty() && recent_.size() < recent_limit) {
            const auto newline = rest.find(L'\n');
            auto query = trim(rest.substr(0, newline));
            if (!query.empty()) recent_.push_back(std::move(query));
            if (newline == rest.npos) break;
            rest.remove_prefix(newline + 1);
        }
    }

    void remember_query(std::wstring query) {
        std::erase_if(recent_, [&](const auto& item) { return same_text(item, query); });
        recent_.insert(recent_.begin(), std::move(query));
        if (recent_.size() > recent_limit) recent_.pop_back();
        std::string stored;
        for (const auto& item : recent_) stored += utf8(item) + "\n";
        recent_queries = stored.c_str();
        fill_suggestions();
    }

    void fill_suggestions(bool opening = false) {
        const auto combo = GetDlgItem(window_, id_query);
        if (filling_suggestions_ || (!opening && SendMessageW(combo, CB_GETDROPPEDSTATE, 0, 0))) return;
        const auto value = text_of(window_, id_query);
        const auto prefix = trim(value);
        const auto tags_only = matches_prefix(prefix, L"tag:");
        const auto tag_prefix = tags_only ? trim(std::wstring_view(prefix).substr(4)) : prefix;
        DWORD first = 0, last = 0;
        SendMessageW(query_edit_, EM_GETSEL, reinterpret_cast<WPARAM>(&first), reinterpret_cast<LPARAM>(&last));
        filling_suggestions_ = true;
        SendMessageW(combo, CB_RESETCONTENT, 0, 0);
        for (const auto& query : recent_) {
            if (matches_prefix(query, prefix))
                SendMessageW(combo, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(query.c_str()));
        }
        if (tags_only || tag_prefix.size() >= 2) {
            unsigned count = 0;
            for (const auto& tag : tags_) {
                if (!matches_prefix(tag, tag_prefix)) continue;
                const auto query = L"tag: " + tag;
                if (std::any_of(recent_.begin(), recent_.end(), [&](const auto& item) {
                        return matches_prefix(item, prefix) && same_text(item, query);
                    })) continue;
                SendMessageW(combo, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(query.c_str()));
                if (++count == 16) break;
            }
        }
        SetWindowTextW(combo, value.c_str());
        SendMessageW(query_edit_, EM_SETSEL, first, last);
        filling_suggestions_ = false;
    }

    void start_tag_suggestions() {
        if (tags_thread_.joinable()) return;
        try {
            const auto cache = configured_path(cache_path, default_catalog);
            if (!cache.is_absolute() || !fs::is_regular_file(cache)) return;
            tags_ready_.store(false);
            tags_thread_ = std::jthread([this, filename = utf8(cache.wstring())](std::stop_token stop) {
                try { tags_native_ = doujin::catalog_tags(filename, stop); } catch (const std::exception&) {}
                tags_ready_.store(true);
            });
            if (!SetTimer(window_, tags_timer, 100, nullptr)) clear_tags_worker();
        } catch (const std::exception&) {
            clear_tags_worker();
        }
    }

    void poll_tags() {
        if (!tags_ready_.load()) return;
        tags_thread_.join();
        if (tags_native_) {
            tags_.clear();
            for (const auto& tag : *tags_native_)
                if (std::all_of(tag.begin(), tag.end(), [](unsigned char byte) { return byte >= 32; }))
                    tags_.push_back(wide(tag));
            fill_suggestions();
        }
        clear_tags_worker();
    }

    void clear_tags_worker() {
        if (window_) KillTimer(window_, tags_timer);
        if (tags_thread_.joinable()) {
            tags_thread_.request_stop();
            tags_thread_.join();
        }
        tags_native_.reset();
    }

    void configure_source() {
        const bool query = !index_visible_ && source_ == SearchSource::query;
        for (const int id : {id_query, id_query_label, id_pages, id_pages_label, IDOK})
            ShowWindow(GetDlgItem(window_, id), query ? SW_SHOW : SW_HIDE);
        CheckDlgButton(window_, id_search_view, query ? BST_CHECKED : BST_UNCHECKED);
        CheckDlgButton(window_, id_month, !index_visible_ && source_ == SearchSource::month ? BST_CHECKED : BST_UNCHECKED);
        CheckDlgButton(window_, id_all_time, !index_visible_ && source_ == SearchSource::all_time ? BST_CHECKED : BST_UNCHECKED);
        CheckDlgButton(window_, id_index, index_visible_ ? BST_CHECKED : BST_UNCHECKED);
        SendMessageW(window_, DM_SETDEFID, query ? IDOK : 0, 0);
    }

    void clear_results() {
        SendDlgItemMessageW(window_, id_results, LB_RESETCONTENT, 0, 0);
        ListView_DeleteAllItems(GetDlgItem(window_, id_grid));
        if (tiles_) ImageList_RemoveAll(tiles_);
        rows_.clear();
        visible_.clear();
        pending_results_ = 0;
        projection_dirty_ = false;
        facets_dirty_ = true;
        next_chart_ids_.clear();
        update_filter_options();
        SetDlgItemTextW(window_, id_log, L"");
        EnableWindow(GetDlgItem(window_, id_release), FALSE);
    }

    void apply_selection(const std::vector<int>& selected, int focused, int top = -1) {
        const auto grid = GetDlgItem(window_, id_grid);
        const auto list = GetDlgItem(window_, id_results);
        if (view_ == album_art_view) {
            ListView_SetItemState(grid, -1, 0, LVIS_SELECTED | LVIS_FOCUSED);
            for (const auto item : selected) ListView_SetItemState(grid, item, LVIS_SELECTED, LVIS_SELECTED);
            if (focused >= 0) {
                ListView_SetSelectionMark(grid, focused);
                ListView_SetItemState(grid, focused, LVIS_FOCUSED, LVIS_FOCUSED);
            }
            const int anchor = top >= 0 ? top : focused;
            if (anchor >= 0) ListView_EnsureVisible(grid, anchor, FALSE);
        } else {
            SendMessageW(list, LB_SETSEL, FALSE, -1);
            for (const auto item : selected) SendMessageW(list, LB_SETSEL, TRUE, item);
            if (focused >= 0) SendMessageW(list, LB_SETCARETINDEX, focused, FALSE);
            if (top >= 0) SendMessageW(list, LB_SETTOPINDEX, top, 0);
        }
    }

    int focused_result() const {
        if (visible_.empty()) return -1;
        return view_ == album_art_view ? ListView_GetNextItem(GetDlgItem(window_, id_grid), -1, LVNI_FOCUSED) :
            static_cast<int>(SendDlgItemMessageW(window_, id_results, LB_GETCARETINDEX, 0, 0));
    }

    void update_history_buttons() {
        const bool busy = process_.value && !idle_running_;
        EnableWindow(GetDlgItem(window_, id_back), index_visible_ || (!busy && !back_.empty()));
        EnableWindow(GetDlgItem(window_, id_forward), !index_visible_ && !busy && !forward_.empty());
        for (const auto id : {id_month, id_all_time}) EnableWindow(GetDlgItem(window_, id), !busy);
    }

    void save_visit(std::vector<SearchVisit>& history) {
        if (!location_) return;
        if (history.size() == history_limit) history.erase(history.begin());
        auto position = result_position();
        history.push_back({*location_, rows_, text_of(window_, id_log), result_filter_, std::move(position)});
    }

    void restore_visit(SearchVisit entry) {
        clear_results();
        location_ = std::move(entry.location);
        source_ = location_->source;
        track_folder_ = location_->track_folder;
        SetDlgItemTextW(window_, id_query, location_->query.c_str());
        fill_suggestions();
        SendDlgItemMessageW(window_, id_pages, CB_SETCURSEL, location_->pages, 0);
        rows_ = std::move(entry.rows);
        for (auto& row : rows_) row.tile_image = I_IMAGENONE;
        result_filter_ = std::move(entry.filter);
        update_filter_options();
        project_results(entry.position);
        SetDlgItemTextW(window_, id_log, entry.log.c_str());
        SendDlgItemMessageW(window_, id_log, EM_SETSEL, -1, -1);
        SendDlgItemMessageW(window_, id_log, EM_SCROLLCARET, 0, 0);
        update_results_label();
        show_index(false);
        show_results();
        SetFocus(GetDlgItem(window_, view_ == list_view ? id_results : id_grid));
    }

    void navigate(bool forward) {
        if (index_visible_) {
            if (!forward) { show_index(false); SetFocus(GetDlgItem(window_, id_search_view)); }
            return;
        }
        if (process_.value && !idle_running_) return;
        auto& from = forward ? forward_ : back_;
        if (from.empty()) return;
        save_visit(forward ? back_ : forward_);
        auto entry = std::move(from.back());
        from.pop_back();
        restore_visit(std::move(entry));
    }

    void select_search() {
        if (source_ != SearchSource::query && !(process_.value && !idle_running_)) {
            const auto previous = std::find_if(back_.rbegin(), back_.rend(), [](const auto& entry) {
                return entry.location.source == SearchSource::query;
            });
            if (previous != back_.rend()) {
                auto entry = *previous;
                save_visit(back_);
                forward_.clear();
                restore_visit(std::move(entry));
            } else {
                save_visit(back_);
                forward_.clear();
                clear_results();
                location_ = SearchLocation{SearchSource::query, text_of(window_, id_query),
                    SendDlgItemMessageW(window_, id_pages, CB_GETCURSEL, 0, 0), track_folder_};
                source_ = SearchSource::query;
                update_results_label();
            }
        }
        show_index(false);
        if (source_ == SearchSource::query) SetFocus(query_edit_);
    }

    void select_view(int next) {
        if (next == view_) return;
        const auto selected = selection();
        const int focused = focused_result();
        view_ = next;
        wheel_delta_ = 0;
        pinch_distance_ = 0;
        result_view = view_;
        show_results();
        if (view_ == album_art_view) rebuild_tiles();
        apply_selection(selected, focused);
        show_results();
    }

    void select_index() {
        if (index_visible_) return;
        show_index(true);
        refresh_index();
    }

    void refresh_index() {
        if (index_thread_.joinable()) {
            index_refresh_pending_ = true;
            return;
        }
        const auto cache = configured_path(cache_path, default_catalog);
        if (!cache.is_absolute()) throw std::runtime_error("Set a full catalog path in Preferences → Tools → DoujinStyle.");
        if (!fs::exists(cache)) {
            SetDlgItemTextW(window_, id_index_contents, L"No catalog cache yet.");
            return;
        }
        if (index_text_.empty()) SetDlgItemTextW(window_, id_index_contents, L"Reading catalog coverage…");
        index_ready_.store(false);
        index_thread_ = std::jthread([this, filename = utf8(cache.wstring())](std::stop_token stop) {
            try { index_native_ = doujin::catalog_status(filename, stop); } catch (const std::exception&) {}
            index_ready_.store(true);
        });
        if (!SetTimer(window_, index_timer, 100, nullptr)) {
            clear_index_worker();
            SetDlgItemTextW(window_, id_index_contents, L"Could not monitor the catalog reader.");
        }
    }

    void poll_index() {
        if (!index_ready_.load()) return;
        index_thread_.join();
        std::wstring report;
        if (index_native_) {
            for (const auto& line : *index_native_) report += wide(line) + L"\r\n";
        }
        if (report.empty()) report = L"Catalog status unavailable.\r\n";
        report += L"\r\n";
        for (const auto& line : doujin::archive_playback_stats.report()) report += wide(line) + L"\r\n";
        clear_index_worker();
        index_text_ = report.empty() ? L"Catalog status unavailable." : std::move(report);
        SetDlgItemTextW(window_, id_index_contents, index_text_.c_str());
        const bool refresh = std::exchange(index_refresh_pending_, false);
        if (refresh && index_visible_) refresh_index();
    }

    void clear_index_worker() {
        if (window_) KillTimer(window_, index_timer);
        if (index_thread_.joinable()) {
            index_thread_.request_stop();
            index_thread_.join();
        }
        index_native_.reset();
    }

    void report_error(const char* error) {
        if (GetDlgItem(window_, id_log)) {
            append(wide(error));
            finish();
        } else {
            MessageBoxW(window_, wide(error).c_str(), L"Search DoujinStyle", MB_OK | MB_ICONERROR);
            close();
        }
    }

    void start(SearchSource source = SearchSource::query) {
        if (process_.value && idle_running_) finish();
        if (process_.value) return;
        if (source == SearchSource::query && (index_visible_ || source_ != SearchSource::query)) return;
        auto query = trim(text_of(window_, id_query));
        if (source == SearchSource::query && (query.empty() || same_text(query, L"tag:"))) {
            SetFocus(query_edit_);
            return;
        }
        const auto cache = configured_path(cache_path, default_catalog);
        const auto output = configured_path(export_path, default_output);
        if (!cache.is_absolute() || !output.is_absolute())
            throw std::runtime_error("Set full paths in Preferences → Tools → DoujinStyle.");
        const auto pages = std::max<LRESULT>(0, SendDlgItemMessageW(window_, id_pages, CB_GETCURSEL, 0, 0));
        SearchLocation next{source, query, pages, output};
        if (location_ != next) {
            save_visit(back_);
            forward_.clear();
        }
        location_ = std::move(next);
        source_ = source;
        track_folder_ = output;
        result_filter_ = {};
        clear_results();
        update_results_label();
        show_index(false);
        const bool chart = source_ != SearchSource::query;
        append(chart ? L"Reading DoujinStyle's popular downloads…" : L"Searching DoujinStyle…");
        art_folder_ = profile_directory() / L"Artwork";
        auto arguments = L" --output " + quote_command_arg(output.wstring()) +
            L" --art-cache " + quote_command_arg(art_folder_.wstring()) + L" --result-rows";
        if (chart) arguments += source_ == SearchSource::month ? L" --popular month" : L" --popular all";
        else arguments += L" " + quote_command_arg(L"--query=" + query) + L" --pages " + std::to_wstring(pages + 1);
        if (IsDlgButtonChecked(window_, id_refresh) == BST_CHECKED) arguments += L" --refresh";
        launch_worker(cache, arguments, false);
        search_running_ = true;
        if (!chart) remember_query(std::move(query));
    }

    void start_idle() {
        if (process_.value) return;
        const auto cache = configured_path(cache_path, default_catalog);
        const auto output = configured_path(export_path, default_output);
        if (!cache.is_absolute() || !output.is_absolute())
            throw std::runtime_error("Set full paths in Preferences → Tools → DoujinStyle.");
        if (idle_layers.get() == 0) {
            show_index(true);
            append(L"Select at least one idle backfill layer.");
            return;
        }
        show_index(true);
        append(L"Checking idle backfill availability…");
        track_folder_ = output;
        auto arguments = L" --idle-backfill --idle-layers " + std::to_wstring(idle_layers.get()) +
            L" --output " + quote_command_arg(output.wstring()) +
            L" --art-cache " + quote_command_arg((profile_directory() / L"Artwork").wstring());
        if (join_bangumi.get()) arguments += L" --idle-catalog-signals";
        launch_worker(cache, arguments, true);
    }

    void launch_worker(const fs::path& cache, const std::wstring& arguments, bool idle) {
        if (charts_process_.value) charts_due_ = 0;
        clear_charts_worker();
        KillTimer(window_, charts_timer);
        launch_child(cache, arguments, job_, process_, read_, worker_);
        doujin::host_trace::emit(idle ? "component.ui.idle.worker.started" : "component.ui.search.worker.started");
        idle_running_ = idle;
        update_history_buttons();
        EnableWindow(GetDlgItem(window_, IDOK), idle);
        EnableWindow(GetDlgItem(window_, id_idle), FALSE);
        EnableWindow(GetDlgItem(window_, id_stop), TRUE);
        if (!idle) SetFocus(GetDlgItem(window_, id_stop));
        if (!SetTimer(window_, poll_timer, 100, nullptr))
            throw std::runtime_error("Could not monitor the DoujinStyle search.");
    }

    void clear_charts_worker() {
        charts_job_.reset();
        charts_process_.reset();
        charts_read_.reset();
        if (!charts_worker_.empty() && !DeleteFileW(charts_worker_.c_str())) {
            // A canceled launcher may still be releasing its zip file.
            retired_charts_workers_.push_back(std::move(charts_worker_));
        }
        charts_worker_.clear();
        charts_pending_.clear();
    }

    void poll_charts() {
        if (process_.value) return;
        std::erase_if(retired_charts_workers_, [](const auto& path) {
            return DeleteFileW(path.c_str()) || GetLastError() == ERROR_FILE_NOT_FOUND;
        });
        if (!charts_process_.value) {
            try {
                const auto cache = configured_path(cache_path, default_catalog);
                charts_catalog_ = cache;
                charts_output_ = configured_path(export_path, default_output);
                if (!cache.is_absolute() || !charts_output_.is_absolute()) {
                    KillTimer(window_, charts_timer);
                    return;
                }
                const auto artwork = profile_directory() / L"Artwork";
                const auto arguments = L" --prefetch-charts --result-rows --output " +
                    quote_command_arg(charts_output_.wstring()) + L" --art-cache " + quote_command_arg(artwork.wstring());
                launch_child(cache, arguments, charts_job_, charts_process_, charts_read_, charts_worker_, true);
                SetTimer(window_, charts_timer, charts_poll_delay, nullptr);
            } catch (const std::exception& error) {
                console::print((std::string("DoujinStyle chart prefetch: ") + error.what()).c_str());
                clear_charts_worker();
                SetTimer(window_, charts_timer, charts_recheck_delay, nullptr);
            }
            return;
        }
        charts_pending_ += read_child_output(charts_read_.value);
        std::size_t consumed = 0;
        for (auto newline = charts_pending_.find('\n'); newline != charts_pending_.npos;
             newline = charts_pending_.find('\n', consumed)) {
            auto line = std::string_view(charts_pending_).substr(consumed, newline - consumed);
            if (line.ends_with('\r')) line.remove_suffix(1);
            if (line == "\x17") apply_site_icon();
            if (charts_catalog_ == configured_path(cache_path, default_catalog) &&
                (line.starts_with('\x1e') || line.starts_with('\x1d'))) accept_line(line, true);
            if (line.starts_with('\x19') || line.starts_with('\x1c')) {
                const bool updated = line.front() == '\x19';
                line.remove_prefix(1);
                const auto tab = line.find('\t');
                if (tab != line.npos && valid_id(line.substr(0, tab)) &&
                    doujin::release_id_from_reference_path(line.substr(tab + 1)) == line.substr(0, tab)) {
                    if (updated) charts_updated_.push_back(wide(line.substr(tab + 1)));
                    if (charts_output_ == track_folder_) accept_track(line);
                }
            }
            if (line.starts_with("Search stopped:") || line.starts_with("Chart prefetch could not refresh"))
                console::print(("DoujinStyle chart prefetch: " + std::string(line)).c_str());
            consumed = newline + 1;
        }
        charts_pending_.erase(0, consumed);
        apply_pending_results();
        auto updated = std::move(charts_updated_);
        charts_updated_.clear();
        reload_updated_tracks(updated, charts_output_);
        if (WaitForSingleObject(charts_process_.value, 0) != WAIT_OBJECT_0) return;
        DWORD available = 0;
        if (PeekNamedPipe(charts_read_.value, nullptr, 0, nullptr, &available, nullptr) && available) return;
        clear_charts_worker();
        charts_due_ = GetTickCount64() + charts_recheck_delay;
        SetTimer(window_, charts_timer, charts_recheck_delay, nullptr);
    }

    void poll() {
        pending_ += read_child_output(read_.value);
        std::size_t start = 0;
        for (auto next = pending_.find('\n', start); next != pending_.npos; next = pending_.find('\n', start)) {
            accept_line(std::string_view(pending_).substr(start, next - start));
            start = next + 1;
        }
        pending_.erase(0, start);
        apply_pending_results();
        bool exited = WaitForSingleObject(process_.value, 0) == WAIT_OBJECT_0;
        if (!exited) return;
        DWORD available = 0;
        if (PeekNamedPipe(read_.value, nullptr, 0, nullptr, &available, nullptr) && available) return;
        if (!pending_.empty()) accept_line(pending_);
        apply_pending_results();
        DWORD exit_code = 1;
        GetExitCodeProcess(process_.value, &exit_code);
        doujin::host_trace::emit("component.ui.search.worker.exited");
        const auto repaired = repairing_release_;
        const bool refresh_tags = idle_running_ || search_running_;
        auto updated = std::move(updated_tracks_);
        if (exit_code == 0 && !repaired.empty()) {
            const auto row = find_row(repaired);
            if (row != rows_.end()) updated.insert(updated.end(), row->tracks.begin(), row->tracks.end());
            CatalogLookupQueue::get().signal(utf8(repaired), 4);
        }
        finish();
        if (refresh_tags) start_tag_suggestions();
        if (index_visible_) refresh_index();
        reload_updated_tracks(updated);
        if (!download_queue_.empty()) {
            auto id = std::move(download_queue_.front());
            download_queue_.erase(download_queue_.begin());
            start_download(id);
            return;
        }
    }

    void clear_worker() {
        job_.reset();
        if (process_.value) WaitForSingleObject(process_.value, 200);
        process_.reset();
        read_.reset();
        if (!worker_.empty()) DeleteFileW(worker_.c_str());
        worker_.clear();
        pending_.clear();
        updated_tracks_.clear();
    }

    void finish() {
        const bool was_idle = idle_running_;
        idle_running_ = false;
        if (std::exchange(search_running_, false)) {
            next_chart_ids_.clear();
            projection_dirty_ = true;
        }
        apply_pending_results();
        repairing_release_.clear();
        KillTimer(window_, poll_timer);
        clear_worker();
        const auto now = GetTickCount64();
        const auto delay = charts_due_ > now ? static_cast<UINT>(charts_due_ - now) : charts_start_delay;
        SetTimer(window_, charts_timer, delay, nullptr);
        EnableWindow(GetDlgItem(window_, IDOK), TRUE);
        EnableWindow(GetDlgItem(window_, id_idle), TRUE);
        EnableWindow(GetDlgItem(window_, id_stop), FALSE);
        update_history_buttons();
        if (!was_idle) SetFocus(index_visible_ ? GetDlgItem(window_, id_index) :
            source_ == SearchSource::query ? query_edit_ :
                GetDlgItem(window_, source_ == SearchSource::month ? id_month : id_all_time));
    }

    void reload_updated_tracks(std::vector<std::wstring>& names, const fs::path& root) {
        if (names.empty()) return;
        std::sort(names.begin(), names.end());
        names.erase(std::unique(names.begin(), names.end()), names.end());
        metadb_handle_list tracks;
        for (const auto& name : names) {
            pfc::string8 path;
            filesystem::g_get_canonical_path(utf8((root / name).wstring()).c_str(), path);
            tracks.add_item(metadb::get()->handle_create(path.c_str(), 0));
        }
        metadb_io_v2::get()->load_info_async(tracks, metadb_io::load_info_force, window_,
                                            metadb_io_v2::op_flag_silent, nullptr);
    }

    void reload_updated_tracks(std::vector<std::wstring>& names) {
        reload_updated_tracks(names, track_folder_);
    }

    bool pretranslate_message(MSG* msg) override {
        if (!window_ || (msg->hwnd != window_ && !IsChild(window_, msg->hwnd))) return false;
        if (!index_visible_ && msg->message == WM_KEYDOWN && (GetKeyState(VK_CONTROL) & 0x8000)) {
            const auto key = msg->wParam;
            if (key == '0' || key == VK_NUMPAD0) {
                (view_ == list_view ? list_zoom : album_zoom) = 100;
                if (view_ == list_view) update_row_height();
                else rebuild_tiles();
                return true;
            }
            if (key == VK_ADD || key == VK_OEM_PLUS || key == VK_SUBTRACT || key == VK_OEM_MINUS) {
                zoom(key == VK_ADD || key == VK_OEM_PLUS ? 1 : -1);
                return true;
            }
        }
        if ((msg->message == WM_SYSKEYDOWN && (msg->wParam == VK_LEFT || msg->wParam == VK_RIGHT)
             && (msg->lParam & (1L << 29))) ||
            (msg->message == WM_KEYDOWN && (msg->wParam == VK_BROWSER_BACK || msg->wParam == VK_BROWSER_FORWARD))) {
            const bool forward = msg->wParam == VK_RIGHT || msg->wParam == VK_BROWSER_FORWARD;
            if (!(msg->lParam & (1L << 30))) SendMessageW(window_, WM_COMMAND, forward ? id_forward : id_back, 0);
            return true;
        }
        if (msg->hwnd == query_edit_ && msg->message == WM_KEYDOWN && msg->wParam == VK_DOWN &&
            !SendDlgItemMessageW(window_, id_query, CB_GETDROPPEDSTATE, 0, 0)) {
            SendDlgItemMessageW(window_, id_query, CB_SHOWDROPDOWN, TRUE, 0);
            return true;
        }
        if ((msg->hwnd == GetDlgItem(window_, id_results) || msg->hwnd == GetDlgItem(window_, id_grid)) &&
            msg->message == WM_KEYDOWN && msg->wParam == VK_RETURN) {
            SendMessageW(window_, WM_COMMAND, id_release, 0);
            return true;
        }
        return IsDialogMessageW(window_, msg) != FALSE;
    }

    INT_PTR message(UINT message, WPARAM wp, LPARAM lp) {
        switch (message) {
        case WM_INITDIALOG: init(); return FALSE;
        case WM_CTLCOLOREDIT: case WM_CTLCOLORSTATIC: case WM_CTLCOLORDLG: case WM_CTLCOLORLISTBOX:
            return control_colors(message, reinterpret_cast<HDC>(wp), reinterpret_cast<HWND>(lp));
        case WM_MEASUREITEM:
            if (wp == id_results && lp) {
                reinterpret_cast<MEASUREITEMSTRUCT*>(lp)->itemHeight = row_text_height_ * 2 + 12;
                return TRUE;
            }
            break;
        case WM_DRAWITEM:
            if (wp == id_results && lp) {
                draw_result(*reinterpret_cast<const DRAWITEMSTRUCT*>(lp));
                return TRUE;
            }
            break;
        case WM_SIZE:
            if (wp != SIZE_MINIMIZED) layout();
            return TRUE;
        case WM_GETMINMAXINFO: {
            RECT minimum{0, 0, 400, 314};
            MapDialogRect(window_, &minimum);
            AdjustWindowRectEx(&minimum, GetWindowLongW(window_, GWL_STYLE), FALSE, GetWindowLongW(window_, GWL_EXSTYLE));
            auto& metrics = *reinterpret_cast<MINMAXINFO*>(lp);
            metrics.ptMinTrackSize = {minimum.right - minimum.left, minimum.bottom - minimum.top};
            return TRUE;
        }
        case WM_SETTINGCHANGE:
            if (theme_registered_) apply_palette();
            break;
        case WM_NOTIFY: {
            const auto* notice = reinterpret_cast<NMHDR*>(lp);
            if (!notice || notice->idFrom != id_grid) break;
            const auto grid = notice->hwndFrom;
            if (notice->code == LVN_ITEMCHANGED && !index_visible_ && view_ == album_art_view) {
                const auto* change = reinterpret_cast<const NMLISTVIEW*>(lp);
                if ((change->uChanged & LVIF_STATE) && ((change->uOldState ^ change->uNewState) & LVIS_SELECTED))
                    doujin::host_trace::emit("component.ui.search.selection.changed");
                EnableWindow(GetDlgItem(window_, id_release), ListView_GetSelectedCount(grid) > 0);
            } else if (notice->code == LVN_BEGINDRAG) {
                drag_rows_ = selection();
                drag_tracks(grid);
            } else if (notice->code == NM_DBLCLK) {
                open_release();
            } else if (notice->code == LVN_GETINFOTIPW) {
                const auto* note = reinterpret_cast<NMLVGETINFOTIPW*>(lp);
                if (note->iItem >= 0 && static_cast<std::size_t>(note->iItem) < visible_.size() && note->cchTextMax > 0) {
                    const auto& row = visible_row(note->iItem);
                    auto text = row.title + L"\n" + row.artist +
                        (row.rank.empty() ? L"" : L"\n" + row.rank) + (row.tags.empty() ? L"" : L"\n" + row.tags);
                    if (doujin::match_result(row, result_filter_, search_running_, compare_text) ==
                        doujin::FilterMatch::pending) text += L"\nChecking metadata or track availability";
                    lstrcpynW(note->pszText, text.c_str(), note->cchTextMax);
                }
            }
            break;
        }
        case WM_TIMER:
            if (wp == poll_timer && process_.value) poll();
            if (wp == tags_timer && tags_thread_.joinable()) poll_tags();
            if (wp == index_timer && index_thread_.joinable()) poll_index();
            if (wp == charts_timer) poll_charts();
            return TRUE;
        case WM_APPCOMMAND: case WM_XBUTTONDOWN: case WM_XBUTTONDBLCLK: case WM_XBUTTONUP:
            return handle_history_mouse(window_, message, wp, lp) ? TRUE : FALSE;
        case WM_CLOSE: close(); return TRUE;
        case WM_COMMAND:
            if (LOWORD(wp) == IDOK) { start(); return TRUE; }
            if (LOWORD(wp) == id_idle) { start_idle(); return TRUE; }
            if (LOWORD(wp) == id_index) { select_index(); return TRUE; }
            if (LOWORD(wp) == id_search_view) { select_search(); return TRUE; }
            if (LOWORD(wp) >= first_layer && LOWORD(wp) < first_layer + layer_labels.size() && HIWORD(wp) == BN_CLICKED) {
                int mask = 0;
                for (std::size_t layer = 0; layer < layer_labels.size(); ++layer)
                    if (IsDlgButtonChecked(window_, first_layer + static_cast<int>(layer)) == BST_CHECKED) mask |= 1 << layer;
                idle_layers = mask;
                return TRUE;
            }
            if ((LOWORD(wp) == id_sort || LOWORD(wp) == id_tag_filter || LOWORD(wp) == id_format_filter) &&
                HIWORD(wp) == CBN_SELCHANGE) {
                apply_filters();
                return TRUE;
            }
            if ((LOWORD(wp) == id_tag_filter || LOWORD(wp) == id_format_filter) && HIWORD(wp) == CBN_CLOSEUP) {
                apply_pending_results();
                return TRUE;
            }
            if (LOWORD(wp) == id_ready_filter && HIWORD(wp) == BN_CLICKED) {
                apply_filters();
                return TRUE;
            }
            if (LOWORD(wp) == id_list_view) { select_view(list_view); return TRUE; }
            if (LOWORD(wp) == id_art_view) { select_view(album_art_view); return TRUE; }
            if (LOWORD(wp) == id_month) { start(SearchSource::month); return TRUE; }
            if (LOWORD(wp) == id_all_time) { start(SearchSource::all_time); return TRUE; }
            if (LOWORD(wp) == id_back || LOWORD(wp) == id_forward) {
                navigate(LOWORD(wp) == id_forward);
                return TRUE;
            }
            if (LOWORD(wp) == id_query && HIWORD(wp) == CBN_DROPDOWN) {
                fill_suggestions(true);
                return TRUE;
            }
            if (LOWORD(wp) == id_release || (LOWORD(wp) == id_results && HIWORD(wp) == LBN_DBLCLK)) {
                open_release();
                return TRUE;
            }
            if (LOWORD(wp) == id_results && HIWORD(wp) == LBN_SELCHANGE) {
                doujin::host_trace::emit("component.ui.search.selection.changed");
                const auto list = GetDlgItem(window_, id_results);
                EnableWindow(GetDlgItem(window_, id_release), SendMessageW(list, LB_GETSELCOUNT, 0, 0) > 0);
                InvalidateRect(list, nullptr, FALSE);
                return TRUE;
            }
            if (LOWORD(wp) == id_stop) {
                download_queue_.clear();
                finish();
                append(L"Stopped. Saved results remain in the catalog.");
                return TRUE;
            }
            if (LOWORD(wp) == IDCANCEL) { close(); return TRUE; }
            break;
        case WM_NCDESTROY:
            if (filter_registered_) {
                message_loop::get()->remove_message_filter(this);
                filter_registered_ = false;
            }
            if (theme_registered_) {
                ui_config_->remove_callback(this);
                theme_registered_ = false;
            }
            clear_tags_worker();
            clear_index_worker();
            KillTimer(window_, charts_timer);
            clear_charts_worker();
            index_refresh_pending_ = false;
            window_ = nullptr;
            clear_worker();
            break;
        }
        return FALSE;
    }

    HWND window_ = nullptr;
    ui_config_manager::ptr ui_config_;
    fb2k::CCoreDarkModeHooks dark_hooks_;
    bool theme_registered_ = false, filter_registered_ = false;
    bool idle_running_ = false, search_running_ = false;
    bool index_visible_ = false, index_refresh_pending_ = false;
    bool drag_armed_ = false, dragging_ = false;
    int view_ = list_view, cover_pixels_ = 144, tile_pixels_ = 108, dpi_ = 96, wheel_delta_ = 0;
    double pinch_distance_ = 0;
    SearchSource source_ = SearchSource::query;
    std::optional<SearchLocation> location_;
    std::vector<SearchVisit> back_, forward_;
    HWND query_edit_ = nullptr;
    std::vector<std::wstring> recent_, tags_;
    bool filling_suggestions_ = false;
    std::optional<std::vector<std::string>> tags_native_;
    std::atomic_bool tags_ready_ = false;
    std::jthread tags_thread_;
    std::optional<std::vector<std::string>> index_native_;
    std::atomic_bool index_ready_ = false;
    std::jthread index_thread_;
    HIMAGELIST tiles_ = nullptr;
    HICON site_icon_ = nullptr;
    HBRUSH background_ = nullptr, surface_ = nullptr, accent_brush_ = nullptr;
    HFONT body_font_ = nullptr, list_font_ = nullptr, bold_font_ = nullptr;
    COLORREF canvas_ = 0, surface_color_ = 0, text_ = 0, muted_ = 0, accent_ = 0;
    COLORREF selected_ = 0, selected_text_ = 0;
    LONG row_padding_ = 6, row_text_height_ = 14, cover_size_ = 42;
    POINT drag_origin_{};
    std::vector<int> drag_rows_;
    std::vector<PlacedControl> controls_;
    std::vector<ReleaseRow> rows_;
    std::vector<std::size_t> visible_;
    std::vector<std::wstring> next_chart_ids_;
    doujin::ResultFilter result_filter_;
    std::size_t pending_results_ = 0;
    bool projection_dirty_ = false, facets_dirty_ = false;
    std::vector<std::wstring> download_queue_;
    Handle job_, process_, read_;
    Handle charts_job_, charts_process_, charts_read_;
    fs::path worker_, art_folder_, track_folder_;
    fs::path charts_worker_, charts_output_, charts_catalog_;
    ULONGLONG charts_due_ = 0;
    std::vector<fs::path> retired_charts_workers_;
    std::string charts_pending_;
    std::vector<std::wstring> charts_updated_;
    pfc::com_ptr_t<IWICImagingFactory> wic_;
    std::string pending_;
    std::wstring index_text_;
    std::wstring repairing_release_;
    std::vector<std::wstring> updated_tracks_;
};

std::shared_ptr<SearchDialog> search_dialog;

void open_index_status(HWND owner) {
    if (!search_dialog || !search_dialog->is_open()) search_dialog = std::make_shared<SearchDialog>();
    search_dialog->open_status(owner);
}

class SearchShutdown : public initquit {
    void on_init() override {
        observer_ = std::make_unique<CatalogPlaylistObserver>();
        configure_catalog_queue();
    }
    void on_quit() override {
        observer_.reset();
        CatalogLookupQueue::get().shutdown();
        if (search_dialog) search_dialog->close();
        search_dialog.reset();
    }
    std::unique_ptr<CatalogPlaylistObserver> observer_;
};

initquit_factory_t<SearchShutdown> search_shutdown;

class SearchMenu : public mainmenu_commands {
    t_uint32 get_command_count() override { return 1; }
    GUID get_command(t_uint32) override {
        return {0x849de867, 0x91bb, 0x4d86, {0x9c,0x1a,0x32,0xf9,0x88,0x44,0xf0,0xd8}};
    }
    void get_name(t_uint32, pfc::string_base& out) override { out = "Search DoujinStyle…"; }
    bool get_description(t_uint32, pfc::string_base& out) override {
        out = "Search DoujinStyle, cache results, and index a bounded batch of archives.";
        return true;
    }
    GUID get_parent() override { return mainmenu_groups::library; }
    void execute(t_uint32, service_ptr_t<service_base>) override {
        try {
            if (!search_dialog || !search_dialog->is_open()) search_dialog = std::make_shared<SearchDialog>();
            search_dialog->open();
        }
        catch (const std::exception& e) { popup_message::g_show(e.what(), "Search DoujinStyle"); }
    }
};

mainmenu_commands_factory_t<SearchMenu> search_menu;
}

void doujin_lookup_on_playback(std::string id) { CatalogLookupQueue::get().signal(std::move(id)); }
