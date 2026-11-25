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
