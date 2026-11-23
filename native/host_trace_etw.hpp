#pragma once

// Opt-in E2E-only TraceLogging provider for *this DLL*. Production builds are
// no-op. The provider uses Windows QPC ticks as payload so Robot's QPC stamps
// can be compared without relying on ETW session timestamp clock settings.
//
// Important: this header must be included in exactly one .cpp translation unit
// when DOUJIN_E2E_TRACE is enabled, because it defines a provider handle.
#include <cstdint>

#if defined(DOUJIN_E2E_TRACE)
#include <windows.h>
#include <TraceLoggingProvider.h>

TRACELOGGING_DEFINE_PROVIDER(
    g_doujin_e2e_trace_provider, "DoujinStyle.HostE2E",
    // Provider ID: {a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a}
    (0xa4ab84a0, 0x75db, 0x4f17, 0xb7, 0xe3, 0xf1, 0xaa, 0x0d, 0x26, 0x0d, 0x4a));

namespace doujin::host_trace {
class Provider {
public:
    Provider() noexcept {
        const DWORD count = GetEnvironmentVariableA("DOUJIN_E2E_RUN_ID", run_id_, sizeof(run_id_));
        const DWORD attempt = GetEnvironmentVariableA("DOUJIN_E2E_ATTEMPT_ID", attempt_id_, sizeof(attempt_id_));
        const DWORD scenario = GetEnvironmentVariableA("DOUJIN_E2E_SCENARIO_ID", scenario_id_, sizeof(scenario_id_));
        if (count != 32 || attempt < 8 || attempt >= sizeof(attempt_id_) ||
            scenario < 8 || scenario >= sizeof(scenario_id_)) return;
        for (DWORD i = 0; i < count; ++i) {
            const char ch = run_id_[i];
            if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return;
        }
        const auto valid_id = [](const char* data, DWORD length) noexcept {
            for (DWORD i = 0; i < length; ++i) {
                const char ch = data[i];
                if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'z') ||
                      (ch >= 'A' && ch <= 'Z') || ch == '_' || ch == '-')) return false;
            }
            return true;
        };
        if (!valid_id(attempt_id_, attempt) || !valid_id(scenario_id_, scenario)) return;
        LARGE_INTEGER frequency{};
        if (!QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0) return;
        frequency_ = static_cast<std::uint64_t>(frequency.QuadPart);
        registered_ = TraceLoggingRegister(g_doujin_e2e_trace_provider) == ERROR_SUCCESS;
    }
    ~Provider() {
        if (registered_) TraceLoggingUnregister(g_doujin_e2e_trace_provider);
    }
    Provider(const Provider&) = delete;
    Provider& operator=(const Provider&) = delete;

    void emit(const char* event, std::uint64_t samples = 0) const noexcept {
        if (!registered_) return;
        LARGE_INTEGER stamp{};
        if (!QueryPerformanceCounter(&stamp)) return;
        const auto tick = static_cast<std::uint64_t>(stamp.QuadPart);
        // Only allowlisted stage literals from compiled-in call sites:
        // no URL, title, path, credentials, or source media metadata.
        TraceLoggingWrite(
            g_doujin_e2e_trace_provider, "Milestone",
            TraceLoggingString(event, "Event"),
            TraceLoggingString(run_id_, "RunId"),
            TraceLoggingString(attempt_id_, "AttemptId"),
            TraceLoggingString(scenario_id_, "ScenarioId"),
            TraceLoggingUInt64(tick, "QpcTick"),
            TraceLoggingUInt64(frequency_, "QpcFrequency"),
            TraceLoggingUInt64(samples, "SampleCount"),
            TraceLoggingUInt32(GetCurrentProcessId(), "Pid"),
            TraceLoggingUInt32(GetCurrentThreadId(), "Tid"));
    }

private:
    char run_id_[65]{};
    char attempt_id_[65]{};
    char scenario_id_[65]{};
    std::uint64_t frequency_ = 0;
    bool registered_ = false;
};

inline void emit(const char* event, std::uint64_t samples = 0) noexcept {
    static Provider provider;
    provider.emit(event, samples);
}
} // namespace doujin::host_trace

#else
namespace doujin::host_trace {
inline void emit(const char*, std::uint64_t = 0) noexcept {}
}
#endif
