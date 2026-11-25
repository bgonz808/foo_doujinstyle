// Test-only pass-through DSP. Independent of TrackInput / archive reader.
// Explicitly enable as the sole DSP in the disposable test profile.
#include <foobar2000/SDK/foobar2000.h>
#include <foobar2000/SDK/dsp.h>
#include <foobar2000/SDK/dsp_manager.h>
#include <foobar2000/SDK/output.h>
#include <foobar2000/SDK/initquit.h>
#include <foobar2000/SDK/playlist.h>
#include <foobar2000/SDK/playback_control.h>
#include <foobar2000/SDK/play_callback.h>
#include "host_trace_etw.hpp"
#include <string>
#include <algorithm>
#include <windows.h>
#include <filesystem>

namespace {
bool cli_mode() {
    wchar_t value[4]{};
    return GetEnvironmentVariableW(L"DOUJIN_E2E_CLI_MODE",value,4)==1 && value[0]==L'1';
}
void signal_controller(const wchar_t* suffix) {
    if (!cli_mode()) return;
    wchar_t run[33]{};
    if (GetEnvironmentVariableW(L"DOUJIN_E2E_RUN_ID",run,33)!=32) return;
    for (const wchar_t c : std::wstring(run))
        if (!((c>=L'0'&&c<=L'9')||(c>=L'a'&&c<=L'f'))) return;
    const auto name=std::wstring(L"Local\\DoujinE2E-")+run+suffix;
    const HANDLE event=OpenEventW(EVENT_MODIFY_STATE,FALSE,name.c_str());
    if (event) { SetEvent(event); CloseHandle(event); }
}
std::wstring expected_path() {
    wchar_t buf[4096]{};
    const DWORD n=GetEnvironmentVariableW(L"DOUJIN_E2E_EXPECTED_TRACK",buf,4096);
    if (!n || n>=4096) return {};
    std::wstring result(buf,n);
    std::replace(result.begin(),result.end(),L'/',L'\\');
    return result;
}
bool matches(const char* path, const std::wstring& expected) {
    if (!path || expected.empty()) return false;
    std::string source(path);
    if (source.starts_with("file://")) source.erase(0,7);
    if (source.size()>16384) return false;
    const int size=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,source.c_str(),-1,nullptr,0);
    if (size<=1 || size>4096) return false;
    std::wstring decoded(static_cast<size_t>(size),L'\0');
    if (MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,source.c_str(),-1,decoded.data(),size)!=size) return false;
    decoded.pop_back();
    std::replace(decoded.begin(),decoded.end(),L'/',L'\\');
    return CompareStringOrdinal(decoded.c_str(),-1,expected.c_str(),-1,TRUE)==CSTR_EQUAL;
}
class PcmObserver : public dsp_impl_base {
public:
    PcmObserver(): expected_(expected_path()) {}
    static GUID g_get_guid() { return {0x75e60b5f,0x29d3,0x45cc,{0x88,0xa4,0x21,0xce,0x3d,0x5a,0x91,0x4f}}; }
    static void g_get_name(pfc::string_base& out) { out="DoujinStyle E2E PCM observer (test only)"; }
    bool on_chunk(audio_chunk* chunk,abort_callback&) override {
        if (!observed_ && chunk && chunk->get_sample_count()>0) {
            dsp_track_t track;
            const bool known=get_cur_file(track);
            if (!chunk_seen_) {
                chunk_seen_=true;
                doujin::host_trace::emit("component.observer.chunk_received",chunk->get_sample_count());
            }
            if (known && matches(track->get_path(),expected_)) {
                observed_=true;
                doujin::host_trace::emit("component.observer.first_pcm",chunk->get_sample_count());
                signal_controller(L"-pcm");
            } else if (!mismatch_seen_) {
                mismatch_seen_=true;
                doujin::host_trace::emit(known ? "component.observer.chunk_wrong_track" :
                    "component.observer.chunk_without_track",chunk->get_sample_count());
            }
        }
        return true;
    }
    void on_endoftrack(abort_callback&) override {}
    void on_endofplayback(abort_callback&) override {}
    void flush() override {} // One event per instance, single-attempt host only.
    double get_latency() override { return 0; }
    bool need_track_change_mark() override { return false; }
private:
    std::wstring expected_;
    bool observed_=false, chunk_seen_=false, mismatch_seen_=false;
};
static dsp_factory_nopreset_t<PcmObserver> observer_factory;
bool configure_requested() {
    wchar_t value[4]{};
    return GetEnvironmentVariableW(L"DOUJIN_E2E_CONFIGURE_DISPOSABLE",value,4)==1 && value[0]==L'1';
}
bool disposable_portable() {
    if (!core_api::is_portable_mode_enabled()) return false;
    wchar_t exe[4096]{},expected[4096]{};
    const DWORD n=GetModuleFileNameW(nullptr,exe,4096);
    const DWORD m=GetEnvironmentVariableW(L"DOUJIN_E2E_HOST_EXE",expected,4096);
    if (!n || n>=4096 || !m || m>=4096 ||
        CompareStringOrdinal(exe,-1,expected,-1,TRUE)!=CSTR_EQUAL) return false;
    const auto root=std::filesystem::path(exe).parent_path();
    return std::filesystem::is_regular_file(root/L"portable_mode_enabled");
}
// /add may complete its IPC handoff before the playlist is populated.
// Observe the real playlist on the UI thread, focus the exact fixture, and
// acknowledge only when it can actually be played. No timed Play yet.
UINT_PTR playlist_timer=0;
void stop_playlist_timer() {
    if (playlist_timer) { KillTimer(nullptr,playlist_timer); playlist_timer=0; }
}
void CALLBACK poll_playlist(HWND,UINT,UINT_PTR,DWORD) {
    try {
        auto manager=playlist_manager::get();
        const auto count=manager->activeplaylist_get_item_count();
        if (count==0) return;
        if (count!=1) {
            doujin::host_trace::emit("component.observer.playlist_count",count);
            stop_playlist_timer();
            return;
        }
        const auto track=manager->activeplaylist_get_item_handle(0);
        if (!track.is_valid() || !matches(track->get_path(),expected_path())) {
            doujin::host_trace::emit("component.observer.playlist_mismatch");
            stop_playlist_timer();
            return;
        }
        manager->activeplaylist_set_focus_item(0);
        manager->set_playing_playlist(manager->get_active_playlist());
        if (manager->activeplaylist_get_focus_item()!=0 ||
            manager->get_playing_playlist()!=manager->get_active_playlist()) return;
        stop_playlist_timer();
        doujin::host_trace::emit("component.observer.playlist_ready",1);
        signal_controller(L"-added");
    } catch (...) { stop_playlist_timer(); }
}
class PlaybackDiagnostics : public play_callback_static {
public:
    unsigned get_flags() override {
        return flag_on_playback_starting|flag_on_playback_new_track|flag_on_playback_stop|flag_on_playback_pause;
    }
    void on_playback_starting(play_control::t_track_command,bool paused) override {
        if (cli_mode()) doujin::host_trace::emit("component.observer.play_starting",paused?1:0);
    }
    void on_playback_new_track(metadb_handle_ptr track) override {
        if (cli_mode()) doujin::host_trace::emit(track.is_valid()&&matches(track->get_path(),expected_path())?
            "component.observer.play_new_track":"component.observer.play_wrong_track");
    }
    void on_playback_stop(play_control::t_stop_reason reason) override {
        if (cli_mode()) doujin::host_trace::emit("component.observer.play_stopped",static_cast<std::uint64_t>(reason));
    }
    void on_playback_pause(bool paused) override {
        if (cli_mode()) doujin::host_trace::emit("component.observer.play_paused",paused?1:0);
    }
    void on_playback_seek(double) override {}
    void on_playback_edited(metadb_handle_ptr) override {}
    void on_playback_dynamic_info(const file_info&) override {}
    void on_playback_dynamic_info_track(const file_info&) override {}
    void on_playback_time(double) override {}
    void on_volume_change(float) override {}
};
static play_callback_static_factory_t<PlaybackDiagnostics> diagnostics_factory;
class CliReadiness : public initquit {
public:
    void on_init() override {
        if (!cli_mode() || expected_path().empty()) return;
        try {
            if (configure_requested()) {
                if (!disposable_portable() || playback_control::get()->is_playing() ||
                    playlist_manager::get()->activeplaylist_get_item_count()!=0) return;
                auto desired=output_manager::get()->getCoreConfig();
                desired.m_output=output_id_null;
                desired.m_device=GUID{};
                desired.m_flags &= ~(outputCoreConfig_t::flagUseFades|outputCoreConfig_t::flagUseDither);
                output_manager_v2::get()->setCoreConfig(desired);
                dsp_chain_config_impl only;
                dsp_preset_impl preset;
                preset.set_owner(PcmObserver::g_get_guid());
                only.add_item(preset);
                dsp_config_manager::get()->set_core_settings(only);
            }
            const auto cfg=output_manager::get()->getCoreConfig();
            dsp_chain_config_impl chain;
            dsp_config_manager::get()->get_core_settings(chain);
            if (!IsEqualGUID(cfg.m_output,output_id_null) ||
                (cfg.m_flags & (outputCoreConfig_t::flagUseFades|outputCoreConfig_t::flagUseDither)) ||
                chain.get_count()!=1 || !IsEqualGUID(chain.get_item(0).get_owner(),PcmObserver::g_get_guid()) ||
                chain.get_item(0).get_data_size()!=0 ||
                playback_control::get()->is_playing() ||
                playlist_manager::get()->activeplaylist_get_item_count()!=0) return;
            playlist_timer=SetTimer(nullptr,0,100,poll_playlist);
            if (!playlist_timer) return;
            doujin::host_trace::emit("component.observer.config_ready");
            signal_controller(L"-ready");
        } catch (...) { /* Fail closed: no ready signal. */ }
    }
    void on_quit() override { stop_playlist_timer(); }
};
static initquit_factory_t<CliReadiness> cli_readiness;
}
