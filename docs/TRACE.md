# Host playback traces and output qualification

## Status (v0.0.200 compiled; v0.0.201 E2E measurement specification)

**Implemented:** Robot-side QPC event capture before dispatch; validated
Python event schema and offline latency comparison; opt-in native component
ETW tracepoints and ETL-to-JSONL exporter. PR #41 independently built and
packaged an **actual x64 ETW-instrumented DLL** on windows-2022 using pinned
MSVC; source/build contracts execute in the host-trace CI workflow.

**Not yet verified:** actual foobar2000 Windows host playback, native ETW
provider registration/capture/decode **inside that host**, live Play/Seek
selectors, verified Null Output selection, correct output PCM or WASAPI.
Compilation is NOT a host runtime test. The full measurement/correlation
and streaming/codec/lifecycle matrix is now in
[LATENCY_MODEL.md](LATENCY_MODEL.md).

## Measurement ladder

| Start / end milestone | Claim |
| --- | --- |
| Robot UI intent -> host acknowledges Play | Automation dispatch + host acceptance; host callback pending |
| Robot UI intent -> first nonempty decoded PCM | Perceived responsiveness up to decoder result, **not audio output** |
| Native track opening -> first nonempty decoded PCM | Input/archive/decoder startup |
| Robot Seek intent -> first nonempty post-seek PCM | Seek responsiveness, not proof that samples correspond to requested position |
| First PCM -> PCM sink accepts first samples | Downstream buffering; **pending sink implementation** |
| First PCM -> WASAPI digital loopback marker | Device-path delivery; **optional dedicated host** |
| Robot Stop intent -> last outstanding range/worker cancellation | Resource reclamation; **pending cancellation hooks** |

Events are associated with \`run_id\`, \`scenario_id\`, and \`attempt_id\`.
Initially each fresh host process runs one isolated scenario/attempt. This is
deliberate: do not match simultaneous analyzer, replayed user actions, or
different tracks by nearest timestamp alone. Future multi-attempt host runs
need an explicit attempt-ID propagation mechanism, not heuristic matching.

The native \`TrackInput\` hooks emit only for \`input_flag_playback\`; metadata
and analyzer input initialization must not be silently counted as playing.
The existing console/session startup and seek statistics remain available,
but are not clock-correlated CI evidence by themselves.

## QPC contract

The Robot event schema lives in \`e2e/host/trace.py\`, one JSON record per line.
Windows Python calls \`QueryPerformanceCounter\` and
\`QueryPerformanceFrequency\`; the native TraceLogging provider embeds those
same raw QPC ticks and frequency. Event-time captures precede serialization,
log writing and ETW emission. A \`clock.sync\` record samples approximate UTC
with an uncertainty interval for cross-log navigation. **Use QPC deltas for
same-guest durations**, not wall-clock timestamps or uncalibrated timestamps
from a second VM. The \`win-qpc:<run_id>\` domain may not be reused on another
machine, VM snapshot, or boot. The portable Unix test fallback is process-local
and must not be cross-process compared.

Fields for raw URLs, tokens, paths, release metadata and filenames are forbidden.
Only agreed event names and small nonnegative numeric metrics are allowed. For
measurement, absent milestones -> \`not_measured\`, mismatched domains ->
\`incomparable_clock_domain\`, reversed clocks -> \`invalid\`, never 0 ms.
Read the full error and source hashes before changing performance thresholds.

## Audio backend profiles

| Backend | Prerequisite | Intended assertions | Not proven |
| --- | --- | --- | --- |
| \`null\` (default target) | Verified actual foobar2000 Null Output selection | Real playback, decoder PCM, seeking, stop, pacing behavior | PCM identity or audible output; even the null sink's exact consumption is not observed yet |
| \`pcm-capture\` | Future test-only output component or DSP tap with PCM capture | Sample content, frame count, time-coded marker and post-seek sample correctness, buffering | Physical output latency; foobar2000 Converter WAV export alone is **not** real playback output |
| \`wasapi-loopback\` | Functioning, verified Windows audio endpoint, optionally virtual | First marker delivered to the digital endpoint; output pipeline timing | Acoustic latency from speakers or listener perception |

Set \`output_backend\` in the host manifest as an *intent*. The checkpoint keeps
\`output_backend_verified=null\` until real UIA/host-output assertions prove the
selection and the stream's behavior. **A configured mode is never a verdict.**
Null Output should avoid depending on physical devices, but verify the actual
test guest: the absence of a hardware card is not conclusive evidence that
foobar2000 can run without any Windows audio services in that configuration.

The built-in Null Output is present in the current foobar2000 lineage; its
paused-position jitter bug was fixed in v2.25.11. Test v2.26.3 or another
explicitly pinned approved version, never silently accept host upgrades:
https://www.foobar2000.org/changelog

For PCM, start with generated time-coded WAV fixtures and foobar2000 Converter
to validate decoder reference content. Then implement a test-only sink or DSP
tap that writes bounded PCM plus a sample-indexed trace. Its writer should be
separate from the decoder callback's timestamp path, and should not buffer
unboundedly. Compare fixed format (float vs int, channels, sample rate), DSP,
ReplayGain, seek alignment, resampling and dither policies. Hashes alone are
insufficient if a sample offset or legitimate DSP conversion is present.

## Instrumented native build and ETW capture

Do this only in a disposable, offline Windows guest with a pinned, tested
foobar2000 portable profile and the approved Win32/x64/ARM64EC toolchain.
The project defaults to **no native ETW trace provider**. For a deliberate
instrumented component build, pass \`-E2ETrace\` to \`native/build.ps1\`, alongside
the pinned SDK, LLVM and platform options in [BUILDING.md](BUILDING.md). The
resulting package name includes \`-e2e-trace.fb2k-component\` to distinguish it
from normal artifacts; do not ship it as a release. The extra ETW provider
uses \`Advapi32.lib\` and must compile/qualify on Windows before relying on it.

The provider GUID is
\`{a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a}\`
(provider name \`DoujinStyle.HostE2E\`). Start collecting **before** launching
foobar2000; record any \`logman\` privilege failure as missing native evidence:

\`\`\`powershell
logman start DoujinE2E -p "{a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a}" 0xffffffffffffffff 0xff -o "C:\sandbox\trace\component.etl" -ets
# Execute the isolated host runner; it passes run/scenario/attempt IDs into foobar2000.
logman stop DoujinE2E -ets
.\e2e\host\export_etw.ps1 -EtlPath "C:\sandbox\trace\component.etl" -OutputJsonl "C:\sandbox\runs\smoke-001\trace\component.jsonl" -ExpectedRunId "<run_id from checkpoint.json>"
py -3 -m e2e.host.trace --folder "C:\sandbox\runs\smoke-001\trace" --output "C:\sandbox\runs\smoke-001\trace-combined.json"
\`\`\`

The ETL converter requires a decoded, named, TraceLogging \`EventData\`
payload with exact fields; it rejects unknown native event names, mismatched
runs, ambiguous extraction and absent data instead of inventing events.
Windows ETL decoding and this command sequence are **not yet execution
qualified**. If Windows tools expose the payload under another metadata shape,
inspect the actual event XML before modifying the converter.

Potential observer effect: native ETW provider registration on first playback
instrumentation can perturb cold-start timing. Run a warmup session, report
instrumented versus uninstrumented overhead, and separate cold-start
correctness from controlled-performance budgets. Build provenance must record
whether \`DoujinE2ETrace\` was enabled.

## Next acceptance gate

1. The pinned x64 **MSVC** ETW test build compiled in PR #41. Next prove
   native ETL export from an interactive Windows foobar2000 host, matching
   actual Robot launch / component startup identity.
2. UIA inspect and select Null Output in the isolated profile. Verify Play,
   stop, seek and fresh-profile replay; store screenshots separately from trace.
3. Implement Robot Play and Seek callbacks with **real** UI selectors and
   compare intent to native decoded PCM. Validate a deliberately injected
   delay produces a corresponding change without a false pass.
4. Implement a deterministic PCM sink or tap; verify output content and
   sample-indexed seek markers. Then add a WASAPI digital-loopback scenario
   only for eligible dedicated runner VMs.
5. Extend event capture to worker/network first-byte and cancellation milestones,
   then per-component first-party LLVM/Clang source coverage for the actual
   host-driven DLL and child worker. Keep portable-publisher coverage separate.

Do not impose performance SLA/p95 gates before calibrated runs, adequate sample
sizes and controlled host variance. Source revision alone is not evidence that
the native tracepoints ran.
