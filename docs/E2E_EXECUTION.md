# E2E execution plan and qualification ledger

Current baseline main v0.0.266 (f02aaf5ec6c2fdb53fa95999e32bbe820afdf211):
all six postmerge workflows passed. #90 actual WPF UIA nine nodes, Invoke and
password suppression; native ETW QPC passed. Neither is actual foobar UI or
audio. #91 v0.0.267 ETW build and host contracts passed; production/portable
inventory rejected new conditional host_pcm_observer.cpp. v0.0.268 explicitly
qualifies only that exact test-only project condition, keeps production TU
inventory unchanged, extends opt-in isolation contracts, and makes paired
readiness require common PCM + observed host PID on each arm. New CI pending.

## Critical path: first real timing (#22 #33 #18 #26)

1. Qualify #91 and exact-main test package/exporter. Verify actual compiler
   source inventory, production exclusion; separately analyze test-only code.
2. Obtain independently authenticated publisher portable x64 host and pinned
   dependency wheel closure in disposable interactive Windows guest. No
   self-hosted public PR execution with network/credentials. Keep guest
   offline, hypervisor egress denied; reviewed package/exporter pins.
3. Configure and independently verify sole pass-through DSP, Null Output,
   empty isolated playlist, playback order, no ReplayGain/fades/resampling,
   no analyzers/background indexing for measurement cohort. Template digest
   pinned after configuration. Confirm path matching and actual now-playing.
4. Run local / direct HTTP .djt / ZIP STORE / ZIP DEFLATE with same exact
   encoded member bytes and same CLI trigger. Opt-in --paired on readiness
   requires common DSP marker in all arms and preserves checkpoint/manifest
   hashes. Current runner still requires Robot/FlaUI capture; separate CLI-only
   performance path with exact spawned PID, robust cleanup and no UI snapshots
   in measurement interval is next. No actual host executed yet.
5. Independent pairing validator: compare exact member/source identities,
   archive digest/member offset, decoder and verified output profile, trigger,
   fixture network parameters, actual host PID/attempt and template. Report
   common DSP latency with signed deltas, not component-only marker vs local;
   no claim of sink delivery or PCM content correctness. First trial per arm
   is an anchor, not a statistical regression gate.
6. Independent PCM sample oracle (local host decode same encoded bytes for
   lossy formats; sample rate/channels/frame count/alignment, EOF, seek);
   common DSP observer is test-only currently inside trace DLL, not a separate
   reusable component yet. Verify Null Output, then capture sink/optional
   WASAPI shared-mode loopback; distinguish first nonempty PCM from first
   actual content and endpoint acceptance. Output-device timing separate.
7. Mutable per-action correlation, one host per compatible series, stop/play
   x10, A/B/A same archive/different archive same origin/cross origin,
   forward/back seek, reopen/restart, server close/reconnect. Prevent stale
   or analyzer events from qualifying new attempt. State-driven wait not fixed
   sleep. Seeded AB/BA, cold state vector separate from warm, preserve failures,
   median paired deltas and supported quantiles. Match baseline local cache.
8. Test actual socket/pool/cache telemetry vs server ID/request ordinal,
   per-action bytes, decoded/discard bytes, seek checkpoint restore,
   CPU/memory and amplification. Expose configurable 0/20/80/200ms response,
   setup delay, bandwidth/forced close and faults through host runner.
   Application response delay is not measured network RTT; true TLS,
   jitter/loss, HTTP/2 separate.

## Parallel: UI and corpus (#17 #18 #33)

- Actual Robot/FlaUI Invoke/Select/Value synthetic qualification, then real
  foobar window trees, screenshots, stable semantic selectors scoped to
  dialog/role/AutomationId with uniqueness. Layout/order/DPI/theme separate
  visual regression; source IDs hints only. Win32 custom ownerdraw accessibility
  where needed. Do not depend on Search UI for first audio timing.
- Preferences/Advanced, Search list/art, charts, indexing idle/active/partial,
  loading/empty/error, filter/clear/sort/ready, keyboard/drag/selection,
  cancellation; selection must not spuriously perform network or indexing.
- Existing This Month/All Time charts through actual plugin UI; retain
  rank/download/snapshot per chart, deduplicate releases, stratify popularity,
  rare format/tag and pathology. Check per-copy archive directory inventory
  independently of advertised metadata. Readout is merged; actual UI view and
  full member codec/hash verification pending.
- Use existing context menu Download archive to Downloads -> native archive
  download job, not external downloader. Disposable guest Downloads, ensure
  selected/downloaded/streamed copy identity (current UI selects release,
  not necessarily explicit copy). Verify completion, integrity grade (MEGA
  MAC vs MediaFire transport only), exact bytes/hash, independent literal
  extraction; cancel/conflict/incomplete negative tests. No bulk acquisition.
- Authorized private write-through capture quarantine -> reviewed pinned CAS
  corpus; synthetic/licensed public fixtures only. Capture sanitized protocol
  and original digest separately, exact authorized archive bytes for replay,
  no live miss fallback. Actual provider parsers/worker/network/crypto
  rather than mocks at internal dispatch. Per-copy archive, audio, tags,
  filename encodings and host coverage; unsupported archive negative.
- Live endpoint checks separately approved, exact host allowlist,
  rate/request/byte/time limits, external egress, no unknown POST retry,
  offline every PR, provider changes after offline, bounded scheduled drift
  and pre-release. Real live timing not equivalent to tuned delay.

## Later, not forgotten

- #10 Wavebar/Minibar concurrency, lookahead and seek/network sharing;
  real analyzers must not invalidate foreground timing or cache fairness.
- #26 release 19732 slow WAV: actual RIFF chunks, ZIP method, exact member
  and local control; no assumption of cause. #20 release 28590: raw archive
  filename encoding, decoded name, file bytes, embedded metadata separately.
- #25 RAR4/RAR5/7z/solid/nested/ZIP64/encrypted/multipart: inventory and
  unsupported outcomes first, streaming only when implemented/qualified.
  #14 portable checkpoints, bounded reuse, eviction and concurrent readers.
- #42 per-DLL/worker host coverage and fixture/source attribution; no
  invented coverage. #1 migrate Search/Charts and Download/Repair only with
  parity and actual gates; stale draft #27/#28 must be recovered/rebased,
  then retire superseded runtime Python. Use same host harness after migration,
  extract reusable generic pieces for foo_qobuz/foo_input_opus_hd.
- Expand frozen corpus beyond current 13 codec/container profiles; targeted
  WAV/FLAC/MP3/Opus 9-profile IO matrix, 648 original planned observations,
  local/direct controls and selected factor pairs rather than full Cartesian
  product. Actual host matrix execution remains zero.

## v0.0.269 candidate: version-bound UI change evidence

Main v0.0.268 all six postmerge passed. UI state manifest binds source commit, component digest/version and host version. Compare approved same-state folders using `py -3 -m e2e.host.ui_diff --before <old-state> --after <new-state> --output <new-report.json>`; report additions/removals/semantic changes separately from geometry, ignore sibling order, flag unkeyed/ambiguous, reject incomplete trees. UX-affecting candidates should capture named states before merge and require reviewed baseline approval rather than auto-updating selectors; automatic dispatch, actual host snapshots and live selectors remain pending. Synthetic data only; no personal UI capture. CLI-only audio execution remains a separate workstream.

## v0.0.270 candidate: CLI-only audio anchors

Stacked on UI evidence candidate. `run_readiness --paired --cli-only` uses Robot but no FlaUI interaction or screenshot requirement. Exact spawned process, run-scoped local events synchronize SDK init and first PCM. SDK init verifies Null Output GUID, no fades/dither, sole observer preset, empty active playlist and not playing; actual ETW config_ready before play and common PCM matching PID required. Graceful exit or fail on forced child cleanup. Still requires isolated interactive Windows guest, manually prepared pinned template and dependencies; ReplayGain, playback order, later configuration change and PCM content not yet independently verified. No real host execution. Separate UI and timing qualification.

## v0.0.272: source-bound guest execution

#94 merged v0.0.271 all six exact-head gates. Dedicated Windows test build now emits a receipt for actual checkout, run/attempt/ref, component/package/exporter digests; independently obtain receipt hash from trusted exact-main run log (not self-attestation). `guest_execute` validates receipt, source, offline desktop and base pins, prepares a copy, executes four CLI anchors, revalidates paired report. Explicit `--accept-disposable-configuration` passes `--configure-disposable` only to per-arm fresh portable copies; test-only SDK uses portable flag/exe/marker, requires no playback and empty active playlist, sets Null Output/no fades or dither/sole observer, then reads back configuration before signaling readiness. Default existing paths remain non-mutating. No actual foobar host or configuration execution yet; requires manually provisioned offline Windows guest, publisher portable base, exact installed Robot/FlaUI versions and trusted test artifacts. No real endpoint or physical output qualification. See CLI --help; failure preserves execution stage and existing evidence.

## v0.0.273 portable paired-report integration

Construct all four synthetic evidence arms using actual fixture extraction and manifest writers, valid trace records and bound checkpoints. Exercise full reporter and reject modified local bytes, rehashed incorrect archive member locator and rehashed mismatched playback trigger. These tests exercise the actual evidence pipeline but no host playback; never publish their synthetic timings as measured. Actual guest execution is still required.

## v0.0.274: licensed package receipt and snapshot reuse

v0.0.273 failed only receipt inventory in Windows ETW build after successful compilation and synthetic probes: actual package includes two required license files. Exact allowlist now contains both license members and x64 DLL, bounded license CRC read, rejects extras/duplicates/missing licenses; portable tests match actual layout. No broad wildcard. Python stays preinstalled in reusable offline guest; [HOST_SNAPSHOTS.md](HOST_SNAPSHOTS.md) records invariant snapshot, candidate input, fresh/warm cohorts, QPC/rollback boundaries and turnaround measurement. No VM provisioned or measured.

## v0.0.275 hosted pilot

Existing free windows-2022 runner, separate playback job after exact-run ETW artifact build. Pinned publisher x64 installer and Robot wheel, portable v2 profile/user-components-x64 layout, same-run receipt output digest, explicit hosted synthetic CLI mode and exact host executable firewall readback. No live provider requests, no secrets, no independent egress claim. Preserve evidence on failure. First real playback attempted by CI, pending results. Offline gate unchanged; FlaUI UI lane separate. Snapshot reuse after observed baseline.

## v0.0.276 hosted pilot repair

#96 v0.0.275 run 45586244129: all non-host workflows passed, native ETW and synthetic UIA passed; host provisioning 10273 ms, valid Authenticode, same-run artifact transfers passed. First anchor blocked before launching foobar2000 by invalid firewall address prefix (0x80070057). Replace special prefixes with documented Any remote-address block scoped to exact host executable; require rule readback and actual loopback fixture playback, do not relax on failure. No independent isolation claim. Stage-level provisioning, preflight/preparation/anchors/report elapsed and per-anchor wall times retained. Initial measured build 105.9 s, SDK ~6.7 s, Spectre ~11.6 s, native ETW probe ~11.5 s, UIA probe ~7 s, transfer ~2.1 s. Prioritize actual playback then parallel provisioning and trusted invariant compilation reuse, no paid runner or vendor binary redistribution. Full search/chart -> index -> stream -> plugin download -> independent exact extraction -> native local remains #18/#22/#33; per-request cursor trace and optional output lane tracked there.
