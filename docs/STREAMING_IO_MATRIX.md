# Streaming cursor × archive method × audio technical format

Source of truth: [quality/decoder-io-profiles.json](../quality/decoder-io-profiles.json).
Planner: [e2e/host/decoder_io_matrix.py](../e2e/host/decoder_io_matrix.py).
This is a **test plan only** until a real Windows foobar2000 run produces
validated telemetry and PCM evidence.

## Current native data path (confirmed by source)

- Direct: foobar2000 \`filesystem::g_open_read\` and decoder.
- ZIP STORE method 0: \`RangeCursor\` maps a seekable uncompressed member
  offset to the compressed archive's original byte range. It exposes
  \`read\`, \`seek\`, \`get_size\`, \`get_position\`, \`reopen\`,
  \`can_seek\` and \`is_remote\`.
- ZIP DEFLATE method 8: \`DeflateCursor\` exposes the same \`file_readonly\`
  API but maps requested offsets through a cached inflater and optionally
  persisted DEFLATE checkpoints. The cursor is logically seekable; seeking
  forward over missing checkpoints may require substantial re-inflation.
- Large MP3 inside DEFLATE has a narrower \`Mp3PlaybackFile\` startup budget
  and retry path; this is an existing special case, not a pattern to repeat
  speculatively for WAV or FLAC.
- The actual WAV/FLAC/MP3/Opus decoding should stay delegated to
  foobar2000's \`input_entry::g_open_for_decoding\`. The plugin should provide
  a correct, efficient stream abstraction; do not invent a second decoder.
- ZIP is not a solid-compression format in this implementation: method 8
  applies DEFLATE to individual ZIP members. Solid RAR/7z are separate
  container formats, and the current remote ZIP pipeline does not support
  their random-access semantics. RAR/7z are negative capability cases, not
  assumed successful audio plays.

## Planned matrix

3 cursor paths × 9 technical audio profiles = **27 source combinations**.

Audio profiles include WAV PCM 16-bit, WAV PCM 24-bit, WAV float32, WAV with
extra RIFF chunks, FLAC with/without SEEKTABLE, MP3 CBR, MP3 VBR with Xing,
and Ogg Opus. This describes **test assets**, not a claim that every pinned
foobar2000 host has an Opus input decoder installed.

For each asset, the plan has 4 lifetime/cache phases and 6 I/O actions:
open, first decoded PCM, three seek positions, and stop during a pending
read. That yields **648 planned observations**, still **zero executed**.
Correctness depends on a reproducible source WAV/PCM oracle and explicit
sample-format/resampling rules.

The currently provided plan groups most work into just two host processes:

1. Fresh disposable host. For **each independent synthetic source identity**,
   test first-access *cold-by-asset*, then warm and reopen. After the first
   operation the host, WinHTTP pools, DNS/TLS and OS page cache are no longer
   globally cold: these are not interchangeable with a truly cold host.
2. Relaunch preserving controlled catalog/ZIP/checkpoint state. For each
   asset, verify volatile range bytes and handles are gone while permitted
   disk state survives, then repeat direct/ZIP playback and seeking.

A genuine **cold-process** baseline requires new processes; a genuinely cold
OS/network baseline may require additional independent VM snapshots. Keep a
small number of such anchor tests, not an OS reboot for every combination.
Each recorded state must prove cache identity/hits and durable checkpoint
preconditions, not merely label itself warmed.

### Important instrumentation blocker

The current ETW provider reads an attempt ID from process environment at
initialization. It **cannot correctly distinguish many changing attempts
within one running foobar2000 process**. Before multi-case qualified timing,
introduce a test-only mechanism to assign a mutable, provenance-checked
scenario/attempt identity at the UI operation boundary and propagate it to
the native cursor and worker. The asset/reference identity must also be
recorded to separate decoder metadata probes from user Play and prefetch.

Until then, multiple cases in one process can run as exploratory exercises,
but **must not generate validated cross-layer latency statistics**.

## What to instrument at cursor boundaries

Record bounded per-phase *aggregates*, not per-\`read()\` ETW events in hot
loops. Each cursor should publish:

- \`open\`, \`read\`, \`seek\`, \`reopen\` counts and ranges/positions, a
  histogram of read sizes, total requested, returned and short-read bytes.
- ZIP member identity and compression method, \`packed_size\`, \`size\`,
  validator identity (hash/redacted rather than raw ETag), and cursor type.
- Ranges fetched and exact body bytes (compressed) versus cached ranges
  served; **do not treat compression ratio as HTTP read amplification**.
- Inflated output bytes generated versus decoder bytes consumed; checkpoints
  queried/restored, inflate distance, persisted checkpoint age and size.
- First decoder open, initialize, nonempty PCM, later PCM, seek requested,
  seek completed, first *correctly aligned* post-seek PCM, and failure paths.
- Source decoder identity and observed format (sample rate, channel count,
  bit depth/sample format), no raw filenames, URLs, credentials or track
  metadata. An unsupported codec is an explicit capability failure/skip,
  never an implicit pass.
- HTTP request timings, protocol, origin handle reuse, connection outcome and
  peer close. \`WinHttpConnect\` handle reuse does **not** prove reuse of one
  physical TCP connection; synthetic server connection IDs can prove that.

In the current DEFLATE path the sequential range coalescing mode is enabled
*after* first nonempty PCM; investigate startup request granularity separately
from steady-state playback. A larger pre-first-PCM fetch may help some formats
but waste bandwidth for others, and can worsen first byte/first audio latency.
Constrain experiments to byte budgets and verify correctness before tuning.

## Real-world regression: release 19732

The user confirms that the affected audio is WAV with delayed startup. The WAV technical subtype and ZIP member compression method (STORE, DEFLATE, or another method) remain unverified. No internet
lookup is required to diagnose it; we should capture **locally observed**
manifest facts for the actual selected track: source archive format, ZIP
member method, compressed/uncompressed member bytes, WAV header layout,
decoder name, first-read offsets and sizes, compressed HTTP transfer bytes,
checkpoint presence and whether the original/alternate copy has the same
contents. Do not assume the member is method 8 just because the archive is ZIP.

Then use synthetic fixtures that reproduce the observed shape; run:
- actual problematic release as a user-authorized non-CI reproduction,
- deterministic local HTTP fixture with identical technical layout,
- ZIP STORE control of identical decoded WAV bytes,
- direct-file control of identical WAV bytes,
- ZIP DEFLATE cold-by-asset, warm and restarted with checkpoint variation.

Compare first decoded PCM, ranges, inflater work and wasted bytes. If a
performance policy changes, report its p50/p95 impact, CPU time, transfer
bytes and cache footprint, not only the best-case first audio.

## Prioritization

1. Real Windows test build and ETW export, pinned asset/cursor identity,
   stable per-action trace context. Without the last, session batching cannot
   be reliably analyzed.
2. Synthetic waveform fixtures with source hashes and full decode reference,
   with Null Output for pipeline execution and a later PCM tap for content.
3. File-cursor aggregate counters and request pattern histograms.
4. Broad 27-combination test batch plus a few truly cold-host anchor runs.
5. Controlled A/B performance policy tests (pre-first-PCM coalescing, checkpoint
   spacing, bounded metadata warming) with fault-injection and byte budgets.

Never regress playback correctness to improve a single startup timing.


## Shared archive and CI research reference

See [ARCHIVE_RESEARCH.md](ARCHIVE_RESEARCH.md) for the durable format/transport,
seek, decoder/cache, RAM backing and CI toolchain findings, including exact
run/commit-linked [machine-readable evidence](archive-research/2026-11-24.json).
Research qualification does not establish actual host audio or production
derived-toolchain qualification. #77 and #79 track remaining work.

## v0.0.287 seek oracle candidate

After three distinct real MediaFire DEFLATE pairs (#99), add independent deterministic 120s/32kHz mono PCM16 source, position-coded 32-frame windows, target at middle of ZIP surrounded by 512 KiB stored padding before and after (not tail-local). Actual fb2k SDK playback_seek to 90,10,90,45s, exact target sample pattern at start of common DSP chunk and actual SDK seek callback, same literal local/direct/STORE/DEFLATE audio bytes, QPC intent/SDK→matching PCM, fixture HTTP ledger. New distinct seek profile per process; not output-device proof, not full-file PCM oracle, not live seek yet, not multi-cursor or restart/retention qualification. Preserve first-PCM baseline K=5. Gate on all four arms. Physical layout, codec, compression unit, member offsets and raw identities, cache lifetimes and competing cursor actions are coverage axes, search source merely discovery provenance. Next: causal cursor/read/seek/cache/inflater spans, multi-reader overlap and cancellation, more layouts/codec/solid cases, live exact-copy local PCM seek oracle and controlled checkpoint state A/B. No additional live network traffic in this candidate.

## v0.0.288 seek qualification continuation

#130 v0.0.287 local SDK seek oracle passed 4/4 (SDK→matched PCM 0.4338/0.3836/0.2116/0.3249 ms, callback after PCM in all four). All startup anchors passed; other five workflows green. Remote seek arms did not run because fixture_server default 4 MiB cap rejected 7.32 MiB WAV. Explicit bounded 16 MiB cap only for synthetic seek mode; default remains 4 MiB, no live/production change. Preserve per-arm preflight failures and continue independent arms if host cleanup proven. Relative seek coverage: pinned host menu/shortcut discovery, SDK playback_seek_delta, menu GUIDs, +/-1/5/10/30s (and longer supported steps), fractional absolute timeline and scrub/rapid supersession; distinguish requested relative delta vs actual resolved target and notification vs verified PCM; local oracle for each, boundary/paused/cancel cases. Existing native cursor audit: RangeCursor and DeflateCursor own positions; RangeObject shared by URL/referrer weak reference + per-key open lock; 16 spans /16 MiB compressed range LRU plus pinned tail; fetch lock serializes misses, rechecks cache; DeflatedMember shared by RangeObject identity and member offset, prefix lane + one resumed lane, serialized read/pump with temp files, checkpoint cache 128 MiB; MP3 wrapper separate startup policy. Test these invariants before changes, especially divergent concurrent readers, cache eviction, lock wait, shared cancellation and retained state across restart. No production performance change.

## v0.0.289 stock reader comparison candidate

#100 v288 merged a99f15d81f07ec7cd96c430f1745eaaaa72dd061; all 16 actual seeks passed, artifact 15077189295. SDK→matched PCM ms local .2085/.2425/.2058/.2829, direct 10.6456/7.4231/6.6181/7.2559, STORE 11.2024/13.2631/5.5156/12.2234, DEFLATE 39.2759/3.376/.3151/1.374. Total body bytes 0/54592196/1572992/7253216; direct 7.108x unique source bytes, STORE/DEFLATE no duplicate completed ranges. Single trial, loopback, no audible endpoint or sustained/relative/restart proof. v289 adds same archive bytes through stock local ZIP STORE/DEFLATE and stock ZIP reader over existing whole-outer RangeObject/RangeCursor (not single-member envelope). SDK unpack path with synthetic loopback-only outer, no native TrackInput, whole-outer ETW marker required for remote, same exact position oracle, HTTP ledger and common DSP. Record actual installed and loaded foo_unpack*.dll hashes relative to pinned portable root, distinguish built-in functionality from separate module. Stock remote capability not yet proven; failures retained, no weakening previous gates. K=1 first comparison, K=5 rotated paired stock trials after route qualification. No live traffic.

## v0.0.290 native HTTP unpack URI candidate

#101 v289 merged 69df4143c92601c077e025c562dc2e3aa7a63e33; 8/8 paths, 32/32 seeks and six workflows green. Loaded stock components/foo_unpack.dll SHA256 68d509a1d2b9c93718dfac121427b686188efdaaad1bd46df4b1078a7ca68bf0. Intent→PCM native remote STORE/DEFLATE 31.6488/31.7441 ms vs stock virtual outer 365.4486/2652.7756 ms; request totals 26/14 vs 92/528. K=1 loopback. New two arms stock unpack://zip over literal loopback HTTP archive URL, same member bytes and exact seek oracle; stock SDK URI roundtrip, no TrackInput/whole virtual outer markers, HTTP evidence required. No TLS bypass, no live traffic. Test negative capability if fails; no claims of direct HTTP support until qualified. Nested local/HTTP ZIP, controlled HTTPS, repeated K=5 and resource metrics remain follow-ups. SDK historical syntax unpack://TYPE|UTF8 byte length|outer path|member path, recursively pack outer paths; generate via SDK rather than manual string assembly.

## v0.0.291 HTTP phase attribution and latency sensitivity

#102 v290 merged d16fde7866929b6f8821898fbabdddaca8501eb4; all ten routes and 40 exact seeks. Stock native HTTP ZIP works; stock STORE 140 requests / 84,702,346 body bytes vs native STORE 22 / 1,507,456; stock DEFLATE virtual 528 disjoint requests, native 11. Native HTTP STORE 9.70x and DEFLATE 2.92x body amplification. Repeated backwards 1 KiB directory reads in stock virtual outer; native own startup includes 95/12 B misses. Hypothesis: round-trip costs dominate, not proven until controlled delay. v291 calibrates QPC vs fixture perf_counter before/after with uncertainty intersection, records pre-Play, Play→first PCM and four seek windows separately, crossing requests excluded and identified, union unique/duplicate bytes, tiny requests, response active union/sum (not critical path), connections and incomplete requests. Adds 20ms per-request server delay to four native/stock-virtual archive arms, preserves all ten zero-delay controls. This is injected request service latency, not full WAN RTT/TLS simulation; no additional live network. Candidate 14 arms/56 seeks, K=1, no production tuning or full causal cursor instrumentation yet. Next causal cursor/operation IDs, lock waits, inflater byte counts, process/kernel resource trace, actual output and analyzer contention.

## v0.0.292 structural diversity candidate

#103 v291 merged dba470398ecbe001a3ae2d04caa5e2d5389e2c61, 14/14 arms and 56/56 exact seeks. With +20ms/request, native/stock virtual DEFLATE first PCM 65.834/13762.911ms; pre-PCM completed requests 2/528 and body 327701/7700530 bytes. K=1, no actual WAN RTT or endpoint claim. Start structural diversity with portable real mixed audio STORE/DEFLATE ZIP (one central directory, independent member offsets, exact bytes and repeat read); strict current rejection of unrebased prefix/concatenated standalone ZIPs and multi-disk marker; raw method-8 stream containing stored→fixed and stored→dynamic Huffman blocks, exact decode/checkpoint restoration and reject trailing complete raw stream. Existing host fixture already mixes stored padding and compressed audio, not proof of audio sibling transitions. These portable tests do not establish host multi-cursor correctness or support for concatenated ZIPs. Next diversity sweep: distinct compression group/method and codecs, physical layout/data descriptor/ZIP64/large directory/duplicate names, nesting, multipart/SFX/concatenation classification; RAR/7z/MEGA capability gaps. Live selection use sanitized catalog structural inventory, not query count; no extra live traffic in candidate. Trace gap: causal reader IDs, queue/locks and cancellation, cache/inflater bytes, process/thread scheduling, disk/memory, network transport, UI readiness and actual endpoint continuity; distinguish required vs speculative work.

## v0.0.293 version-addressed performance evidence

#104 v292 merged 26d5246a781e9cdbe8b031c15bb1119b72f90cf8, six green, 14 routes/56 exact seeks; structural portable tests qualified. v293 extracts all existing seek phases from hash-bound traces/fixture HTTP ledgers, rechecks calibration/phase results, binds source checkout + test DLL/package/exporter receipt + host/template + workflow run/attempt + fixture hashes and cache state. Includes intent and SDK startup timing, each seek timing, HTTP requests/connection counts/body/unique/duplicate/tiny responses/active interval/crossing counts, byte fractions, local/direct/local archive/remote archive comparisons, explicit nulls for unmeasured CPU/disk/memory/TCP/endpoint. Compare two reports offline with python -m e2e.host.performance_report compare --baseline A.json --candidate B.json --output comparison.json; rejects mismatched fixture/protocol/host/context. No automatic latency threshold or historical artifact fetch; K=1 descriptive only. Next same-run alternating A/B K>=5, per-scenario distributions, robust paired deltas and uncertainty, stable baseline registry and release digest binding, immutable artifact retention beyond existing 14d. Report identity is consistency binding, not independent attestation. Known regression cases #26 release 19732 WAV ZIP method/subtype unknown, #20 release 28590 raw-name vs tag vs UI, #10 Minibar concurrent analysis failure; no diagnosis yet. Prioritize exact reproduction and capture, not new search labels.

## v0.0.294 decoder I/O budget extraction (candidate)

Generic DecoderIoBudget/BudgetedMemberFile replaces MP3-named implementation; legacy MP3-only activation, limits and fallback are preserved, including legacy diagnostic wording for comparison. Explicit alternative limits permit bounded sparse head/tail access in portable policy tests; no other codec is enabled in production by this change. Unit tests cover legacy boundaries, sticky rejection, finish, sparse metadata policy and arithmetic bounds. Next: actual host large CBR/VBR MP3 fallback, tail-moov M4A local/STORE/DEFLATE controls, cold/warm/reopened cache, native incremental-cost accounting and seek-preserving policy qualification (#107 #108 #22). Do not infer improved M4A playback from portable policy tests.

Candidate v0.0.295 corrects the portable invocation placement: explicit fail-closed compile/run after the existing lane join, with separate timings. No additional runtime behavior changes.

### Consolidated candidate
v0.0.302 combines #105 performance extraction, #109 budget refactor, #110 encoded first PCM, #111/#112 native runtime migration and #113 coordinator parity/ETW diagnostics. Requalify the combined exact source; earlier separate green runs do not qualify the composition. No new generic decoder policy, GUI migration parity or statistical regression threshold claim.
