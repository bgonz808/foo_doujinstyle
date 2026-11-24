#include "host_trace_etw.hpp"

#if defined(DOUJIN_E2E_TRACE)
// Single TraceLogging provider definition for the entire component DLL.
TRACELOGGING_DEFINE_PROVIDER(
    g_doujin_e2e_trace_provider, "DoujinStyle.HostE2E",
    (0xa4ab84a0, 0x75db, 0x4f17, 0xb7, 0xe3, 0xf1, 0xaa, 0x0d, 0x26, 0x0d, 0x4a));
#endif
