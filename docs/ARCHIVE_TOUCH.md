# Archive reader: remote-touch measurements for foobar2000

This extends [STREAMING_IO_MATRIX.md](STREAMING_IO_MATRIX.md) and #25.
The measured input is the **actual loopback fixture HTTP server ledger**, not a
guess based on the archive's compression ratio or file extension. The analyzer
is `e2e/host/archive_touch.py`. The server is in `tools/media_matrix.py`.

## What is already established

Peter Pawlowski described foobar2000's archive readers on 2026-06-27:
https://www.foobar2000.org/blag/2026-06-27-Archive%2Bformats%2Bin%2Bfoobar2000

- ZIP: virtual input/output callbacks, transparent stored-member reads and
  selected-member extraction.
- 7z: virtual input callbacks and selected-member extraction, but no
  transparent stored-member reads. Supported C++ output callbacks on Windows.
- RAR: bundled modified UnRAR requires a **physical archive input file**;
  foobar2000 downloads network-hosted archives before passing them to UnRAR.
  Transparent stored-member access and targeted extraction do **not** avoid
  downloading the remote outer archive.

Virtual input support alone is **not proof of sparse HTTP range usage**:
the archive reader may seek extensively or extract a full compressed/solid
folder. Never infer a smaller transfer from the fact that a seek API exists.

The present doujin plugin has a custom ZIP STORED `RangeCursor` and ZIP
DEFLATE `DeflateCursor`, plus a ZIP host-reader fallback. Both the current
virtual outer-file path and published track references are ZIP-specific.
No remote RAR/7z playback has been qualified in this project.

## Controlled A/B experiment

Use identical generated audio bytes, a reproducible synthetic archive layout,
an independent source SHA-256 and a unique case/attempt identity for each path:

| Variant | Open path | Expected architectural behavior |
| --- | --- | --- |
| ZIP STORE, native DoujinStyle | Direct member offset through RangeCursor | Candidate for minimal outer-archive reads |
| ZIP STORE, foobar2000 ZIP reader | Virtual outer archive via host archive_impl | Candidate for transparent member range reads |
| ZIP DEFLATE, native DoujinStyle | Custom DeflateCursor with checkpoints | Sparse compressed ranges possible, inflation costs remain |
| ZIP DEFLATE, host reader | Virtual ZIP input via archive_impl | May decompress whole member; must measure |
| 7z COPY/non-solid | Future virtual outer path + foobar 7z host reader | No transparent store-mode, measured selection required |
| 7z LZMA/LZMA2, non-solid | Same after capability check | Selected folder may be fully decompressed |
| 7z solid, first/middle/last item | Same with verified solid group mapping | Expect shared work per solid group; quantify rather than assume |
| RAR4/RAR5, store/compressed | Host physical-file control | Full outer-archive download expected by host design |
| RAR remote virtual input | Current host reader | Unsupported: do not present as sparse-streaming success |

Also permute archive member order, selected first versus last file, unrelated
large files before/after target, directory/header compression, file sizes,
random seeks, sibling solid-folder playback, and cold/warm/restart lifetimes.
Keep the **actual audio bytes constant** when comparing archive layout effects.

## Observability, semantics and integrity

The loopback server now records `start_ns`, `end_ns`,
`response_offset`, `bytes_sent`, `response_complete`, response status,
the archive's declared size and a unique request ID per response. Those records
share one Python `time.perf_counter_ns` clock domain **when served and
observed within one guest**. Do not compare them directly against raw QPC ticks:
calibrate Python perf-counter ns against raw QPC for native milestones, with
an explicit uncertainty bound and run/attempt identity.

For each milestone (index ready, decoded first PCM and first post-seek PCM),
`archive_touch.analyze` reports:

- Completed server-written body bytes and **union of archive offsets actually
  delivered** (not sums of duplicate HTTP ranges);
- Redundant transferred bytes, fraction of full archive touched, request
  count, completed full HTTP 200 and union-entire-archive flags;
- In-flight, incomplete, failed or clock-ambiguous requests separately.

The analyzer excludes in-flight responses from exact byte totals and marks
partial observations. `bytes_sent` means successful application-level writes
to the server's socket buffer, **not TCP-acknowledged bytes nor decoder
consumption**. It is not equivalent to decompressed bytes, RAM/disk temporary
materialization, CPU time, archive reader `read` calls, or listening output.
For these, add cursor read/seek counters, per-process disk I/O, decoded PCM
oracles and optional output PCM capture. Fail closed if the HTTP ledger loses
requests or fixture isolation breaks.

**Current status:** The fixture ledger and pure analysis can execute in
portable CI. There is no real foobar2000 RAR/7z host execution, no calibrated
native QPC to Python clock bridge, no per-action mutable ETW attempt ID, and
no qualified host-side RAR virtual file input. All of those remain acceptance
gates, not completed measurements.

## Session batching

Within a long-running host, reuse the same fixture server but assign a unique
asset URL and logical attempt for each case. The server-side identity must be
fixed before the archive is handed to the reader. Preserve the request ledger
and the phase boundaries for every attempt, rather than resetting a single
global counter and risking attribution across concurrent indexing jobs.

A warm rerun can follow each cold-by-asset observation in one host process.
Use a host restart only for persisted-state qualification, plus a small number
of true cold-process/OS baselines. Do not equate merely different URLs with a
fresh network connection or cold DNS/TLS state.

First qualifying Windows experiment: ZIP STORE **host reader vs native
RangeCursor** for one sparse-layout WAV fixture. Then ZIP DEFLATE and 7z.
RAR's stock reader is a full-download control until a different, bounded
physical-file-backed retrieval strategy is explicitly built and tested.


## Shared archive and CI research reference

See [ARCHIVE_RESEARCH.md](ARCHIVE_RESEARCH.md) for the durable format/transport,
seek, decoder/cache, RAM backing and CI toolchain findings, including exact
run/commit-linked [machine-readable evidence](archive-research/2026-11-24.json).
Research qualification does not establish actual host audio or production
derived-toolchain qualification. #77 and #79 track remaining work.
