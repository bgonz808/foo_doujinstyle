# Search → indexing → drag/play: cache and transport scenario matrix

This is a **design and executable plan generator**, not evidence that foobar2000
has executed any of the generated scenarios. The canonical source is
[quality/cache-scenarios.json](../quality/cache-scenarios.json); its consumer is
[e2e/host/cache_matrix.py](../e2e/host/cache_matrix.py). It must remain useful
after Python retirement because it describes native host behavior, not migration.

## 1. Verified behavior in the current source

In `native/search.cpp`:

- Search uses a child worker and asynchronously accepts catalog rows, artwork,
  track-reference notifications and updated metadata. Chart prefetch can also
  update visible rows.
- List and Album Art item selection updates focus/selection and enables
  **Open release**. It **does not explicitly launch ZIP inspection**.
- **Open release** launches a browser for the release page. It is not an
  "add to playlist" action.
- **Drag** creates foobar2000 handles from already *published* `.djt` paths.
  Without published tracks, it reports that the selected release has no tracks
  ready yet.
- An explicit **Repair track metadata** context command starts reinspection.
  Idle backfill has configurable browse/details/links/ZIP/tags/covers layers.
- A previous chart/search worker can continue progressing while a row is
  selected. A nearby background event is not automatically *caused by*
  selection. Tests must quiesce independent work or use correlation-aware
  attribution.

Also in `native/component.cpp` and `native/checkpoint_index.hpp`, archive
playback reads ZIP directory/member data and can persist/restore budgeted
DEFLATE checkpoints. These are *distinct* from catalog records, downloaded
bytes, foobar2000 decoder caches, Windows file cache and WinHTTP keepalive.

The new test-only native ETW events distinguish:
`component.ui.search.selection.changed`,
`component.ui.search.worker.started/exited`,
`component.ui.search.track.projected/updated`,
`component.ui.search.drag.request/no_tracks/has_tracks/drop_accepted`
and `component.ui.idle.worker.started` from decoder/PCM milestones.
**A projected track notification does not alone prove an independent SQLite
commit**: assert the database using a second connection and verify the
published file separately. Current ETW phase needs real Windows build/trace
qualification.

## 2. Cache state is a vector, not a single cold/warm bit

| Layer | Cold / intermediate / warm evidence | Reconditioning |
| --- | --- | --- |
| Source discovery / catalog | missing listing, metadata-only, published `.djt` refs | Independent known-version SQLite snapshot, row assertions |
| ZIP directory | no persisted index, committed directory and member offsets | Check DB fields and current archive identity |
| DEFLATE checkpoints | missing, partial, complete *for expected target seeks* | Inspect validated checkpoint files and input/output offsets, not file count alone |
| HTTP transport | new connection, reused, peer-closed/reconnected | Record request IDs, socket/connection IDs, TLS/HTTP version and close reasons |
| Byte-range cache | cold, process-memory hit | Count requested/returned/consumed bytes and range cache hit/miss; persistent general HTTP range caching is not implemented |
| Host lifetime | fresh profile, warm running host, restart retaining disk state | Restore signed-off fixture snapshot or close/relaunch host |
| OS and DNS/TLS | separate noncontrolled or measured states | Cold Windows standby cache needs privileged image/reset; profile deletion is insufficient |
| Decoder/analyzer/art | separate decoding, Wavebar/Minibar, artwork, foobar metadb reads | Run independent analyzer variants and observable cache counters |

**Never equate** a warm TCP socket with a warm response cache, or a stored ZIP
directory with a decoded PCM frame. WinHTTP handle pools, HTTP/2 multiplexing,
TLS session resumption, intermediary/CDN caches and origin TCP connection reuse
are distinct. Log actual network state; labels in a fixture manifest are only
requests for that state.

The component currently keeps HTTP range bytes in live in-process RangeObject
spans and ZIP tails, but it does **not** expose a general persistent HTTP response
cache after restart. Saved DEFLATE checkpoints are seek metadata, not cached
HTTP response bodies. Hence disk-backed HTTP range-cache states are disallowed.
MEGA responses also do not currently expose the strong ETag required by the
persisted checkpoint directory mechanism.

The matrix's `fresh` means a new profile and no known *application* cache:
it does not certify that OS page cache, DNS, network device, TLS state or VM
page cache is cold. Its `restarted` factor explicitly kills memory-backed
caches but preserves approved disk snapshots.

## 3. Coverage strategy

Do not run an unconstrained Cartesian product. Generate:
1. **Mandatory lifecycle anchors**: cold search, cold play with seeded
   reference, fully warmed seek, restart with persisted checkpoints, passive
   selection, drag after publication, idle indexing, pooled reconnection,
   isolated MediaFire and MEGA mock-contract discovery.
2. **All feasible factor pairs** over trigger, archive type, audio format,
   catalog readiness, ZIP directory, checkpoints, host lifetime, HTTP pool,
   byte-range cache and provider class. Impossible combinations such as
   checkpoint-on-direct-stream, replayed in-memory cache after restart, or
   drag without published references are rejected.
3. **Directed higher-order fault cases**: ETag change after checkpoint
   persistence, truncated byte ranges, 429 during indexing, Stop during
   outstanding HTTP, SQLite commit/notification interruption, and archive
   identity replacement with existing checkpoints, and selection while an indexing worker is still active.

The pairwise generator reports **coverage of feasible factor pairs only**,
not code coverage, executed UI coverage, failure-injection effectiveness or
third-order interaction coverage. Its extra cases do not claim the mock
providers or decoders work; all verdicts are `not_run`.

Use:

```sh
python3 -m unittest -v e2e.host.test_cache_matrix
python3 -m e2e.host.cache_matrix \
  --manifest quality/cache-scenarios.json --output /tmp/doujinstyle-cache-plan.json
```

The generated output contains stable scenario IDs, state setup obligations,
expected outcomes and source-manifest digest, without a pass result. A future
Robot fixture adapter must resolve each scenario's synthetic release, verified
host architecture, pinned output sink and expected decoder support.

## 4. Event boundaries and assertions

For **Search**:
`robot.search.intent` → `search.worker.started` → committed listing /
offers → ZIP-directory update → published reference → projected track → UI
ready to drag. Record each milestone independently: slow metadata enrichment
must not block unrelated ready-to-drag tracks unless explicitly required.
The current Robot automation does not yet generate search-specific events.
Do not infer them from the host-launch smoke.

For **Select**:
`robot.select.intent` → actual UI selection-change event → visible selection
state. After independently quiescing pre-existing workers, ensure selection
alone does **not** start new HTTP/ZIP jobs. A separate directed scenario selects
while an existing worker is active and verifies row-selection stability and no
duplicate selection-attributed work. That is the current regression
expectation, not a promise of a future selection-prefetch feature.

For **Drag**:
`robot.drag.intent` → drag request → has tracks/no tracks → actual drop
accepted → playlist entry observed. The present `drop_accepted` native marker
only records a `DROPEFFECT_COPY` result from the drag operation; **playlist
membership still requires an independent host assertion**.

For **Play/Seek**:
Robot intent → decoded first PCM / first correct post-seek PCM → future PCM
output capture. Compare cold vs warm byte counts and milestone times, plus
checkpoint restore counts and cache identity. Null Output is a suitable
planned no-hardware sink; PCM and WASAPI remain separate qualification levels.

For **Stop/cancel and freshness**:
Stop intent → no more unowned work, outstanding range budget zero, worker
cancellation observed, durable partial checkpoint consistency. A changed
ETag/size/archive copy must invalidate stale ZIP offset and checkpoint use,
including after restart. Unknown remote POST outcomes are never blindly
replayed.

## 5. Future selected-release prewarming: explicit policy decision

It may be beneficial to prioritize a selected release for background ZIP
directory or track-reference preparation. **Do not turn this on implicitly in
the current tests.** A policy needs:

- Debounce and supersession (rapid arrow-key navigation can select tens of
  rows per second); one selected item should not cause uncontrolled fanout.
- Distinct budgets for metadata, archive-directory range reads, checkpoint
  generation and speculative audio reads. A ZIP central directory does not
  need a whole-file download.
- Per-origin concurrency and rate-limit backoff; HTTP request coalescing and
  cancellation when another selection wins; keepalive reuse only when allowed.
- Lifecycle handling (selected → drag → playlist → play) with explicit priority
  promotion and no duplication of in-flight archive jobs.
- Optional selection-prefetch mode with its own experiment cohort. In the
  *disabled* cohort, no selection-caused network indexing is permitted. In
  the *enabled* cohort, trace exact work, bytes, bytes unused, TTFPCM improvement,
  stale-cache risk and per-release fair scheduling.
- Avoid background work from mere hover or keyboard focus unless explicitly
  intended and user-visible.

**First qualification milestone:** real foobar2000 x64 Windows build with
ETW, Null Output, synthetic WAV direct/ZIP STORED/DEFLATE, selection → drag →
Play. Then restart/hot-cache and controlled fault variants. Do not gate
performance on an unqualified GUI selector or uncalibrated network timing.


## Shared archive and CI research reference

See [ARCHIVE_RESEARCH.md](ARCHIVE_RESEARCH.md) for the durable format/transport,
seek, decoder/cache, RAM backing and CI toolchain findings, including exact
run/commit-linked [machine-readable evidence](archive-research/2026-11-24.json).
Research qualification does not establish actual host audio or production
derived-toolchain qualification. #77 and #79 track remaining work.
