# Archive access and CI toolchain research — durable engineering record

Status: measured research, not a production adoption decision. Canonical shared
reference for remote archive support (#14, #18, #25, #22, #33) and CI performance
(#59, #70, #73, #74, #77, #79). The live issue tracks delivery; this document
and the [machine-readable evidence](archive-research/2026-11-24.json) retain
findings after workflow artifacts expire. All results remain scoped to the
actual source commit, workload, runner and qualification stated in the evidence.
See [ARCHIVE_TOUCH](ARCHIVE_TOUCH.md), [STREAMING_IO_MATRIX](STREAMING_IO_MATRIX.md),
[CACHE_MATRIX](CACHE_MATRIX.md), [BUILDING](BUILDING.md) and
[BUILD_ATTESTATION](BUILD_ATTESTATION.md).

## 1. Design model: distinct layers, not one archive abstraction

| Layer | Relevant identity / capability |
| --- | --- |
| Hosting transport | HTTP range/206/416 semantics, immutable object/version, validator, authentication, throttling, retry, request coalescing and actual transferred bytes; MEGA integrity semantics differ from HTTPS-only providers |
| Archive container | Member metadata, offsets, directory location, path encoding, independent versus solid compression groups |
| Codec and seek unit | Stored extent, compressed member, solid group, independent frame, decoder checkpoint; distinct from read granularity |
| Read adapter | read_at/seek/reopen, bounded output, cancellation, cache, optional filesystem adapter |
| Consumer | Foobar2000 audio decoder, archive metadata/indexer, or compiler/analyzer; distinguish actual requested bytes from decoded bytes |
| Trust | Physical compressed digest, exact logical stream digest, canonical semantic member identity, authenticated index/frames and independent provenance |

Never infer network sparsity or actual decoder seeking from the presence of a
seek API. Preserve original raw filename bytes and decoded name, per-file
metadata/content identity, compression method, offsets, sizes, solid group
and format capability where known. Reject ambiguous/colliding names rather than
silently normalize. For unknown providers or formats, fail explicitly or use
a bounded full-download fallback with reason; no silent capability success.

### Format capability matrix (architecture, not host qualification)

| Format | Seek model | Index and caveat |
| --- | --- | --- |
| ZIP STORE | Direct member offsets | Central directory, bounded range reads; current native RangeCursor |
| ZIP DEFLATE | Per-member compressed stream | Checkpoint dictionary/bit offset + validated compressed identity; current DeflateCursor; portable checkpoint sidecar remains #14 |
| ZIP other methods | Method-specific | Capability detection and negative tests; not implied by ZIP support |
| 7z COPY/non-solid | Member/folder mapping | Directory and host decoder constraints; actual host path unqualified |
| 7z LZMA/LZMA2 solid | Solid folder/group | Shared history; adjacent members can require decoding preceding data; independent groups or bounded checkpoints if supported |
| RAR4/RAR5 | Solid/non-solid groups | Current foobar2000 UnRAR input requires physical archive; remote virtual path unqualified |
| TAR | File extent in raw stream | Build bounded member-offset index; sequential header traversal to discover |
| TAR.ZST ordinary | Frame(s), potentially huge history | TAR index alone insufficient for cheap random decode |
| TAR.ZST seekable | Independent frames + seek table | Two indexes: TAR member extents and compressed frame offsets; authenticated range retrieval |
| TAR.XZ | XZ stream/block index | Inspect actual block independence and decoder support; not assumed qualified |
| OCI zstd:chunked | File-aware independent chunks | Related design, not interchangeable with official seekable Zstd |

Provider capabilities and archive capabilities are orthogonal. Test the same
audio across host/provider/format combinations, not different audio contents.
For existing upstream/user archives, do not silently repack or assume control
over layout. When we control derived CI artifacts, optimize layout separately.

## 2. Measurements: LLVM case study

Source: LLVM 23.1.6 Windows x64 release, 276,226,344 compressed bytes, exact
logical TAR 4,283,111,424 bytes, one Zstd frame, 1 GiB window, no seek table,
96,466 blocks, checksum present, unknown declared content size. Source and
logical SHA256, exact commits and run IDs are in the JSON record.

### Extraction and filesystem footprint (Windows #73)

| Baseline | Files | Intermediate TAR | Extracted bytes | Extract + verify |
| --- | ---: | ---: | ---: | ---: |
| v0.0.247 full | 4701 | 4,283,111,424 | 4,279,049,939 | 18.977 s |
| v0.0.249 selective | 332 | 0 | 517,650,454 | 15.104 s |

Runs 45488976837 and 45490731341; six strict analyzer contexts passed.
94% logical writes avoided; different runners, not a controlled causal timing
comparison. Defender contribution not measured. Production selective streaming
still decodes full upstream stream. Compare future improvements to this current
baseline, not merely the obsolete full extraction.

### Selected corpus, level 3 (run 45508115628)

| Fixed frame | Compressed bytes | Random 4KiB p95 ms | Decode amplification |
| --- | ---: | ---: | ---: |
| 64 KiB | 199110821 | .128 | 14.06 |
| 256 KiB | 188936373 | .391 | 43.5 |
| 1 MiB | 185479616 | 1.384 | 161.5 |
| 4 MiB | 183056387 | 5.072 | 672 |
| 16 MiB | 182354670 | 20.997 | 2892.24 |
| 64 MiB | 182249698 | 114.435 | 11481.21 |

Actual 332-member closure; normalized selected TAR (not identical to full TAR);
64 MiB decoded LRU; synthetic local reads. Hybrid 1/4MiB also measured.
All eight layouts reconstructed selected TAR and matched trace reads.

### Full logical TAR, independent equivalence (run 45514230591)

4MiB frames, level3: 1,407,280,548 bytes, 1,022 frames, official seek table.
Independent runners reacquired exact upstream, independently decoded and
compared whole logical SHA256 and length. Verifier owned frame/index/physical
hashes. Both used same libzstd 1.5.5 identity, not independent codec diversity.
Unsigned research only; no production authorization. Distinguish physical,
logical and semantic identities.

### Full TAR compression controls (run 45520696299)

| Recipe | Frame | Compressed bytes | Encode seconds |
| --- | --- | ---: | ---: |
| L3 | single | 1395122178 | 16.42 |
| L3 | 256MiB | 1398619110 | 19.28 |
| L9 | single | 1232313413 | 64.08 |
| L9 | 256MiB | 1237035463 | 62.99 |
| L15 | 256MiB | 1212029967 | 673.53 |
| L9 LDM window128MiB | single | 626326131 | 41.81 |
| L9 LDM window128MiB | 256MiB | 792060082 | 48.22 |

All 13 cases verified exact full logical digest. Frame size alone at level3
changes size <1%; matching history is major. Level3/9/15 windows 2/4/4MiB.
LDM 128MiB saves 36% at level9 256MiB frame. Timing across different runners
is not strictly controlled. Full and selected corpora must not be compared
as equivalent compressed content.

Upstream signed annotated tag resolves to
`5b10d2d74929eae78912771222c9855af97496f4`.
[Exact release recipe](https://github.com/llvm/llvm-project/blob/5b10d2d74929eae78912771222c9855af97496f4/.github/workflows/release-binaries.yml#L370-L381):
`xz -dc ... | zstd --ultra -21 --long=30 -T4 -B1024M -o ...`.
-B is MT job size, not independent frames. Exact x64 upstream encoder version
not confirmed. The same flags under Linux Zstd CLI 1.5.7 failed allocation
at 10,815,131,648-byte address-space cap (run 45527044695); do not relax
resource limits silently. 128/256/512/1024MiB independent frame experiments
remain in flight at snapshot time. Separate upstream bundle verification job
passed; full JSON not yet copied into repository; do not claim verified
derived artifacts or exact upstream encoder identity.

### Decoded cache (run 45520696299)

Repeated 512 random 4KiB requests (total milliseconds, synthetic):

| Frame | 64MiB | 256MiB | 512MiB |
| --- | ---: | ---: | ---: |
| 64KiB | 2.02 | 2.04 | 2.00 |
| 256KiB | 84.01 | 2.01 | 1.98 |
| 1MiB | 316.40 | 1.97 | 2.00 |
| 4MiB | 1356.08 | 661.27 | 1.93 |
| 16MiB | 5869.71 | 2775.04 | 1.87 |
| 64MiB | 37561.24 | 20152.18 | 1.81 |

144 replays passed, 512MiB fits selected corpus. Oversize frames bypass
admission without evicting the hot set. Cache budget is not total RSS.
OS cache not flushed. Distinguish context reuse, session reuse, content
reuse. Next: retained streaming cursor, decoded page cache, single-flight,
workload-aware admission, coalescing/prefetch and cancellation.

### Linux disk versus native tmpfs (run 45527044695, RAM lane passed)

| Corpus | Backend | Extraction trials s | Cleanup trials s | Process write_bytes |
| --- | --- | --- | --- | ---: |
| selected 332 files / 18 dirs | disk | 4.382 / 3.829 | .018 / .018 | 518406144 |
| selected | tmpfs | 3.872 / 3.776 | .023 / .024 | 0 |
| full 4701 files / 285 dirs | disk | 10.474 / 8.665 | .596 / 1.288 | 4289331200 |
| full | tmpfs | 4.620 / 4.759 | .208 / .205 | 0 |

All backend manifests and read traces matched within each corpus; two
alternating trials on same Linux runner. Selected disk mean 4.106s vs tmpfs
3.824s (~7%); full disk mean 9.570s vs tmpfs 4.689s (~51%). Random read
traces ~15ms across disk/tmpfs because extraction and hashing warmed OS cache.
Header app cache ~9ms replay, ~7.6ms prep selected / ~20ms prep full: benefit
requires repeated accesses; not a cold end-to-end win. Native tmpfs data only,
no mount or driver added, no PE execution, no Windows/Defender claims.
Process write_bytes accounting is not physical device telemetry; child excluded.
Full extraction here is streaming, no intermediate TAR. RAM consumes actual
resident/committed resources; filesystem metadata and double caching remain.

## 3. Transferable design and decision gates

- Shared bounded random-access reader contract and authenticated object identity;
  cache compressed bytes, active decoder sessions and decoded pages separately.
  Track cache lifetime and key by source digest/validator plus method/index
  version and offsets; no cross-object reuse without identity proof.
- Host-side test matrix: provider, range capability, archive method/solid group,
  member order, audio codec/container, source size, cold/restart/warm, metadata
  and first/seek PCM; actual server byte-union and redundant ranges, not merely
  request count. See ARCHIVE_TOUCH and STREAMING_IO_MATRIX. Use current ZIP
  path as control, explicit unsupported cases for other formats.
- CI: compare verified original streaming, selected disk, RAM-backed headers,
  selective PE materialization, independent frames and authenticated derived
  artifacts. Record wall/CPU, first usable tool, actual analyzer coverage,
  total memory/commit/OS page cache, logical and physical I/O, file operations,
  cleanup, preparation amortization and transfer. Windows RAM driver or
  filesystem adapter requires separate security qualification. Clang YAML
  overlay alone is not arbitrary byte-backed file access.
- Security: #79 is mandatory for any derived toolchain promotion; upstream
  authenticity, full logical equivalence or reviewed semantic subset, index
  and frame hashes, bounded decoder, independently pinned offline transform
  and verifier, separated signer, immutable publication, revocation and
  consumer verification. No driver install or Defender exclusions. For
  remote audio, preserve host-specific authenticity semantics, not an
  unsupported cryptographic claim about every range.

## 4. Evidence retention contract

Every new experiment records exact source commit, runner/job/run, corpus
identity, recipe/tool hashes and versions, resource limits, trace/cache
state, metrics with units, correctness oracle, qualification and failures.
Retain small JSON/CSV summaries in version control; large raw traces can
expire if their digests, extraction method and key results are retained.
Do not commit credentials, personal URLs, raw media or toolchain binaries.
Append a new dated evidence snapshot, do not overwrite measured history.
Link the shared record from relevant issues and docs; update this decision
summary when a measured result changes the recommendation. Keep research,
production and host qualification separate. Source changes require unique
VERSION and normal gates. Avoid generalizing synthetic evidence to host audio.

## 5. Cursor reuse research candidate (v0.0.257)
Bounded reuse of expensive state; avoid redundant work. Compare whole-frame LRU against 64KiB shared decoded pages and 1/2/4 retained Zstd streaming sessions, 4/16/64MiB frames, 64MiB decoded budget and 8MiB decoder history cap. Real codec tests forward/backward and interleaved frames; same corpus and read hashes. Retain compatible forward sessions, serve cache without changing position, choose nearest preceding session, restart only on miss with no viable cursor; share compressed blob among sessions for same frame. Measure restarts, bytes, context and retained compressed memory, latency, RSS. Serial interleaving only, not thread-safe concurrency; no single-flight or adaptive controller yet. Research only. Full-tree RAM extraction excluded from future deployment/default experiments. Results pending.
