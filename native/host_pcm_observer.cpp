// Test-only pass-through DSP. Independent of TrackInput / archive reader.
// Explicitly enable as the sole DSP in the disposable test profile.
#include <foobar2000/SDK/foobar2000.h>
#include <foobar2000/SDK/dsp.h>
#include "host_trace_etw.hpp"
#include <string>
#include <algorithm>
#include <windows.h>

namespace {
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
            if (get_cur_file(track) && matches(track->get_path(),expected_)) {
                observed_=true;
                doujin::host_trace::emit("component.observer.first_pcm",chunk->get_sample_count());
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
    bool observed_=false;
};
static dsp_factory_nopreset_t<PcmObserver> observer_factory;
}
