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
#include <foobar2000/SDK/archive.h>
#include "host_trace_etw.hpp"
#include <string>
#include <algorithm>
#include <windows.h>
#include <filesystem>
#include <string_view>
#include <atomic>
#include <array>
#include <cmath>
#include <cstdint>

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
// The controller supplies an exact local fixture. The separate stock-reader
// arm can construct a whole-archive virtual outer URI ONLY for loopback
// synthetic assets. It never goes through TrackInput or a member envelope.
std::string stock_archive_mode() {
    char mode[16]{};
    const auto n=GetEnvironmentVariableA("DOUJIN_E2E_STOCK_ARCHIVE",mode,sizeof(mode));
    if (!n || n>=sizeof(mode)) return {};
    return mode;
}
std::string stock_remote_outer(bool native_http) {
    char buf[256]{};
    const auto n=GetEnvironmentVariableA("DOUJIN_E2E_STOCK_URL",buf,sizeof(buf));
    if (!n || n>=sizeof(buf)) return {};
    const std::string_view url(buf,n), prefix="http://127.0.0.1:";
    if (!url.starts_with(prefix)) return {};
    const auto rest=url.substr(prefix.size());
    const auto slash=rest.find('/');
    if (slash==rest.npos || slash<1 || slash>5) return {};
    unsigned port=0;
    for (char ch : rest.substr(0,slash)) {
        if (ch<'0'||ch>'9') return {};
        port=port*10+static_cast<unsigned>(ch-'0');
    }
    if (!port || port>65535) return {};
    const auto path=rest.substr(slash);
    if (path!="/asset/fixture-zip-store.zip?mode=normal" &&
        path!="/asset/fixture-zip-deflate.zip?mode=normal") return {};
    if (native_http) return std::string(url);
    constexpr char hex[]="0123456789abcdef";
    std::string outer="doujinarchive://";
    for (unsigned char ch : url) {
        outer.push_back(hex[ch>>4]);outer.push_back(hex[ch&15]);
    }
    return outer+".zip";
}
std::string canonical_expected(const std::wstring& path) {
    if (path.empty() || !std::filesystem::path(path).is_absolute()) return {};
    const int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,path.c_str(),-1,nullptr,0,nullptr,nullptr);
    if(n<=1 || n>16384) return {};
    std::string utf8(static_cast<size_t>(n),'\0');
    if(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,path.c_str(),-1,utf8.data(),n,nullptr,nullptr)!=n) return {};
    utf8.pop_back();
    pfc::string8 result;
    filesystem::g_get_canonical_path(utf8.c_str(),result);
    const auto mode=stock_archive_mode();
    if (mode.empty()) return result.c_str();
    if (!cli_mode() || std::filesystem::path(path).extension()!=L".zip") return {};
    const std::string outer=mode=="local" ? std::string(result.c_str()) :
                            mode=="remote" || mode=="http" ? stock_remote_outer(mode=="http") : std::string{};
    if (outer.empty()) return {};
    pfc::string8 unpack;
    archive_impl::g_make_unpack_path(unpack,outer.c_str(),"disc/Remote tone.wav","zip");
    pfc::string8 parsed_outer,parsed_member,parsed_type;
    if (!archive_impl::g_parse_unpack_path_ex(unpack.c_str(),parsed_outer,parsed_member,parsed_type) ||
        std::string_view(parsed_outer.c_str())!=outer || std::string_view(parsed_member.c_str())!="disc/Remote tone.wav" ||
        std::string_view(parsed_type.c_str())!="zip") return {};
    return unpack.c_str();
}
bool matches(const char* path, const std::wstring& expected) {
    if (!path || expected.empty()) return false;
    const auto canonical=canonical_expected(expected);
    return !canonical.empty() && std::string_view(path)==canonical;
}
bool sdk_ipc() {
    wchar_t value[4]{};
    return cli_mode() && GetEnvironmentVariableW(L"DOUJIN_E2E_SDK_IPC",value,4)==1 && value[0]==L'1';
}
bool consume_command(const wchar_t* suffix) {
    if (!sdk_ipc()) return false;
    wchar_t run[33]{};
    if (GetEnvironmentVariableW(L"DOUJIN_E2E_RUN_ID",run,33)!=32) return false;
    for (const wchar_t c : std::wstring(run))
        if (!((c>=L'0'&&c<=L'9')||(c>=L'a'&&c<=L'f'))) return false;
    const auto name=std::wstring(L"Local\\DoujinE2E-")+run+suffix;
    const HANDLE event=OpenEventW(SYNCHRONIZE,FALSE,name.c_str());
    if (!event) return false;
    const bool ready=WaitForSingleObject(event,0)==WAIT_OBJECT_0;
    CloseHandle(event);
    return ready;
}
constexpr unsigned seek_rate=32000;
constexpr std::array<unsigned,4> seek_seconds{90,10,90,45};
std::atomic<unsigned> seek_armed{0}, seek_active{0};
std::atomic<bool> seek_first_pcm{false};
unsigned seek_next=0;
bool seek_mode() {
    wchar_t value[4]{};
    return sdk_ipc() && GetEnvironmentVariableW(L"DOUJIN_E2E_SEEK_ORACLE",value,4)==1 && value[0]==L'1';
}
std::wstring seek_suffix(unsigned ordinal,const wchar_t* tail) {
    return L"-seek"+std::to_wstring(ordinal)+tail;
}
int oracle_sample(std::uint32_t frame) noexcept {
    std::uint32_t x=frame*0x9e3779b1u;
    x^=x>>16; x*=0x85ebca6bu; x^=x>>13;
    return static_cast<int>(x&0x7fffu)-16384;
}
// Synthetic-only capture; never enabled in production builds. The output is
// fixed to the disposable attempt's fixture directory, not an arbitrary path.
bool disposable_portable();
bool encoded_capture_mode() {
    wchar_t value[4]{};
    return sdk_ipc() && GetEnvironmentVariableW(L"DOUJIN_E2E_ENCODED_PCM",value,4)==1 && value[0]==L'1';
}
class EncodedPcmCapture {
public:
    static constexpr unsigned frames=1024, max_channels=8;
    bool append(const audio_chunk& chunk) {
        if (failed_ || done_) return false;
        const auto rate=chunk.get_srate(), channels=chunk.get_channels();
        if (!rate || !channels || channels>max_channels ||
            (count_ && (rate!=rate_ || channels!=channels_))) { failed_=true; return false; }
        rate_=rate; channels_=channels;
        const auto take=std::min<std::size_t>(frames-count_,chunk.get_sample_count());
        const auto* input=chunk.get_data();
        for (std::size_t i=0;i<take*channels_;++i) {
            const auto value=static_cast<double>(input[i]);
            if (!std::isfinite(value)) { failed_=true; return false; }
            samples_[count_*channels_+i]=value==0.0 ? 0.0 : value;
        }
        count_+=static_cast<unsigned>(take);
        if (count_!=frames) return false;
        done_=true;
        return persist();
    }
private:
    bool persist() const {
        if (!disposable_portable()) return false;
        wchar_t exe[4096]{};
        const auto n=GetModuleFileNameW(nullptr,exe,4096);
        if (!n || n>=4096) return false;
        const auto root=std::filesystem::path(exe).parent_path().parent_path()/L"fixture";
        if (expected_path().empty() || std::filesystem::path(expected_path()).parent_path()!=root) return false;
        const auto target=root/L"first-pcm.bin";
        const HANDLE file=CreateFileW(target.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
        if (file==INVALID_HANDLE_VALUE) return false;
        const std::array<std::uint32_t,4> header{0x314d4350u,rate_,channels_,frames};
        const auto write=[&](const void* data,DWORD bytes) {
            DWORD written=0;
            return WriteFile(file,data,bytes,&written,nullptr) && written==bytes;
        };
        const bool ok=write(header.data(),static_cast<DWORD>(sizeof(header))) &&
            write(samples_.data(),static_cast<DWORD>(frames*channels_*sizeof(double)));
        CloseHandle(file);
        return ok;
    }
    std::array<double,frames*max_channels> samples_{};
    unsigned rate_=0,channels_=0,count_=0;
    bool failed_=false,done_=false;
};
class PcmObserver : public dsp_impl_base {
public:
    PcmObserver(): expected_(canonical_expected(expected_path())) {}
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
            if (known && !expected_.empty() && track->get_subsong_index()==0 && std::string_view(track->get_path())==expected_) {
                observed_=true;
                if (!seek_mode() || !seek_first_pcm.exchange(true)) {
                    doujin::host_trace::emit("component.observer.first_pcm",chunk->get_sample_count());
                    if (!encoded_capture_mode()) signal_controller(L"-pcm");
                }
            } else if (!mismatch_seen_) {
                mismatch_seen_=true;
                doujin::host_trace::emit(known ? "component.observer.chunk_wrong_track" :
                    "component.observer.chunk_without_track",chunk->get_sample_count());
            }
        }
        if (encoded_capture_mode() && observed_ && chunk && chunk->get_sample_count()>0) {
            dsp_track_t track;
            if (get_cur_file(track) && track->get_subsong_index()==0 &&
                std::string_view(track->get_path())==expected_ && encoded_.append(*chunk))
                signal_controller(L"-pcm");
        }
        if (seek_mode() && chunk && chunk->get_srate()==seek_rate &&
            chunk->get_channels()==1 && chunk->get_sample_count()>=32) {
            unsigned ordinal=seek_armed.load();
            if (ordinal && ordinal<=seek_seconds.size()) {
                dsp_track_t track;
                if (get_cur_file(track) && track->get_subsong_index()==0 &&
                    std::string_view(track->get_path())==expected_) {
                    const auto target=seek_seconds[ordinal-1]*seek_rate;
                    const auto* data=chunk->get_data();
                    bool match=true;
                    for (unsigned i=0;i<32;++i) {
                        const double expected=static_cast<double>(oracle_sample(target+i))/32768.0;
                        if (!(std::abs(static_cast<double>(data[i])-expected)<0.000001)) {match=false;break;}
                    }
                    if (match && seek_armed.compare_exchange_strong(ordinal,0)) {
                        doujin::host_trace::emit("component.observer.seek_pcm_match",target);
                        signal_controller(seek_suffix(ordinal,L"-pcm").c_str());
                    }
                }
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
    std::string expected_;
    EncodedPcmCapture encoded_;
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
bool playlist_ready=false;
void stop_playlist_timer() {
    if (playlist_timer) { KillTimer(nullptr,playlist_timer); playlist_timer=0; }
}
void CALLBACK poll_playlist(HWND,UINT,UINT_PTR,DWORD) {
    try {
        auto manager=playlist_manager::get();
        if (sdk_ipc() && !playlist_ready && consume_command(L"-add-command")) {
            const auto path=expected_path();
            const auto canonical=canonical_expected(path);
            if (!disposable_portable() || canonical.empty() ||
                !std::filesystem::is_regular_file(std::filesystem::path(path)) ||
                manager->activeplaylist_get_item_count()!=0) {
                doujin::host_trace::emit("component.observer.sdk_add_failed");
                stop_playlist_timer();
                return;
            }
            metadb_handle_list tracks;
            tracks.add_item(metadb::get()->handle_create(canonical.c_str(),0));
            manager->activeplaylist_insert_items(0,tracks,bit_array_true());
            doujin::host_trace::emit("component.observer.sdk_add_return");
        }
        if (playlist_ready) {
            if (consume_command(L"-play-command")) {
                if (!seek_mode()) stop_playlist_timer();
                if (manager->activeplaylist_get_item_count()!=1 ||
                    !matches(manager->activeplaylist_get_item_handle(0)->get_path(),expected_path()) ||
                    manager->activeplaylist_get_focus_item()!=0 ||
                    manager->get_playing_playlist()!=manager->get_active_playlist()) {
                    doujin::host_trace::emit("component.observer.playlist_mismatch");
                    return;
                }
                doujin::host_trace::emit("component.observer.sdk_play_call");
                playback_control::get()->start(playback_control::track_command_play,false);
            }
            if (seek_mode() && seek_next<seek_seconds.size() && seek_armed.load()==0 &&
                consume_command(seek_suffix(seek_next+1,L"-command").c_str())) {
                if (!disposable_portable() || !playback_control::get()->is_playing() || !seek_first_pcm.load()) {
                    doujin::host_trace::emit("component.observer.sdk_controller_error");stop_playlist_timer();return;
                }
                ++seek_next;
                seek_active.store(seek_next);
                seek_armed.store(seek_next);
                doujin::host_trace::emit("component.observer.sdk_seek_call",seek_seconds[seek_next-1]*seek_rate);
                playback_control::get()->playback_seek(static_cast<double>(seek_seconds[seek_next-1]));
            }
            return;
        }
        const auto count=manager->activeplaylist_get_item_count();
        if (count==0) return;
        if (count!=1) {
            doujin::host_trace::emit("component.observer.playlist_count",count);
            stop_playlist_timer();
            return;
        }
        const auto track=manager->activeplaylist_get_item_handle(0);
        if (!track.is_valid() || track->get_subsong_index()!=0 || !matches(track->get_path(),expected_path())) {
            doujin::host_trace::emit("component.observer.playlist_mismatch");
            stop_playlist_timer();
            return;
        }
        manager->activeplaylist_set_focus_item(0);
        manager->set_playing_playlist(manager->get_active_playlist());
        if (manager->activeplaylist_get_focus_item()!=0 ||
            manager->get_playing_playlist()!=manager->get_active_playlist()) return;
        playlist_ready=true;
        if (!sdk_ipc()) stop_playlist_timer();
        doujin::host_trace::emit("component.observer.playlist_ready",1);
        signal_controller(L"-added");
    } catch (...) { doujin::host_trace::emit("component.observer.sdk_controller_error"); stop_playlist_timer(); }
}
class PlaybackDiagnostics : public play_callback_static {
public:
    unsigned get_flags() override {
        return flag_on_playback_starting|flag_on_playback_new_track|flag_on_playback_stop|flag_on_playback_pause|flag_on_playback_seek;
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
    void on_playback_seek(double seconds) override {
        if (!seek_mode()) return;
        const unsigned ordinal=seek_active.load();
        if (ordinal && ordinal<=seek_seconds.size() && std::abs(seconds-seek_seconds[ordinal-1])<0.5/seek_rate) {
            doujin::host_trace::emit("component.observer.seek_callback",seek_seconds[ordinal-1]*seek_rate);
            signal_controller(seek_suffix(ordinal,L"-callback").c_str());
        }
    }
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
            playlist_timer=SetTimer(nullptr,0,sdk_ipc()?10:100,poll_playlist);
            if (!playlist_timer) return;
            doujin::host_trace::emit("component.observer.config_ready");
            signal_controller(L"-ready");
        } catch (...) { /* Fail closed: no ready signal. */ }
    }
    void on_quit() override { stop_playlist_timer(); }
};
static initquit_factory_t<CliReadiness> cli_readiness;
}
