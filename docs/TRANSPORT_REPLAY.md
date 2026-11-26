# Paired replay and provider/network qualification

Status: transport fixture instrumentation and offline tests; host replay and
common PCM observation remain planned. Do not count simulated host actions as
executed. Related #17 #18 #22 #26 #33.

## Timing and repetition

Use an exact independently extracted archive member (encoded bytes including
headers/tags) as native local control. Same bytes over native direct HTTP
(where qualified), .djt direct HTTP, .djt ZIP STORE and DEFLATE. Same decoder,
output, DSP, trigger and cache-state vector; compare common independent PCM
milestones, not a component-only hook against native file timing. Prepare
reference bytes outside measured interval. Separate local cache state.

Initial directed sequences: first play; 10 stop/replays in same process;
A/B/A same archive; A/B/A different archive same origin; A/B/A across origins;
forward/backward seek; reopen; restart retaining persistent index/checkpoints;
peer close and replay. Repeat passive selection and drag with independent
background-work attribution. Seeded AB/BA order, independent cold cohorts,
warm repetitions retained individually, report median paired delta and
sample count, tail quantiles only with sufficient observations. Report
failures and timeouts; do not drop slow trials. Run performance without
expensive UI snapshots in the measured interval. Requires mutable per-action
correlation and common host observer before multi-attempt qualification.

## Server behavior

Existing loopback HTTP/1.1 fixture supports Range/If-Range, ETag change,
redirect, ignored range, close, truncation, 429/503, delay and pacing.
New optional connection_delay_ms adds first-request setup delay; latency_ms
adds response delay on each valid request; close_after closes after N requests
on a connection. Actual accepted HTTP connection identity is handler-scoped
and assigned under lock; request index, reuse, close flag, HTTP version,
injected delay, server request start/header-write/end timestamps and body
bytes recorded. Server-side timings are not client arrival/RTT, setup delay
is not TCP/TLS handshake, and application write bytes are not physical wire
bytes. Connection IDs scoped to server instance. Aborted-write accounting and
HEAD are outstanding. Tests use real HTTPConnection reuse across assets
and server-triggered reconnect, not urllib's close-per-request behavior.
Illustrative scenarios: 0/20/80/200ms response delay; 0/50/150ms first-request
setup; 1/10/100Mbps nominal transfer pacing; forced close every 1/3/10
requests, validator rotation, 429 Retry-After, truncation, redirect, ignored
range. Choose directed cases rather than full cross product. Actual RTT,
TCP loss/jitter, TLS resumption and HTTP/2 multiplexing need separate
network-emulation or controlled TLS endpoint tests.

## Mock boundary

Simulate external DoujinStyle listing/details/offers and MediaFire landing/
redirect/download and MEGA API/encrypted payload protocols via local
versioned fixtures. Real production parsers/worker/WinHTTP/crypto/index/
archive/decoder remain under test. Existing mock worker response tests are
contract tests, not full provider HTTP tests. Do not weaken production
hostname/cert/redirect validation; use explicit test-only endpoint injection,
independent guest egress deny, synthetic keys/content and strict negative
cases. MEGA range auth is not whole-file MAC.

## Live probes

Offline every PR; no public endpoint dependency in required per-commit
qualification. Manual approval for initial/provider-specific endpoint tests,
provider adapter changes after offline pass, bounded scheduled drift smoke
(e.g. weekly if provider terms permit) and pre-release candidate; failed
live probes classify external drift/outage separately from local regression.
Existing tools/network_e2e.py is disabled by default (quality/network-e2e-allowlist.json)
and supports exact HTTPS host allowlist, DNS global-address validation,
socket pinning, TLS hostname validation, reauthorized redirects and bounded
requests/bytes/time. Its close-per-request Python probe is transport-only,
not proof of native WinHTTP pooling, provider parsing, or playback. No
live requests authorized here. Use separate isolated connected guest with
independent egress policy; explicit approved endpoint/case list, no wildcard
CDN bypass, no personal credentials, no full archive downloads or unknown
POST retries, respect provider terms/rate limits, stop on 429. For actual
host live cases, use authorized known small content, exact member identity
and independent source baseline, sanitized IDs/metrics, strict time and byte
budgets; do not enable until native host policy and telemetry qualified.

## v0.0.281 initial live discovery/inspection lane

Manual owner dispatch only, standard Windows runner, exact same-run native publisher hash/commit and pinned host zlib. Offline/loopback qualification remains required first. Native worker uses real chart prefetch, journaled offer batch (six unique chart releases, one worker), at most three MediaFire copies and 512 KiB/12 range requests per archive. Explicit live worker mode limits operations, HTTPS/443 DoujinStyle and validated MediaFire DNS family, GET and site-only POST, no proxy fallback, 48 request attempts and 16 MiB application response reads per process; stop after HTTP 429. Native per-hop log includes host only, method, HTTP status, headers/first body/elapsed microseconds, bytes; no raw links/headers/body. Not independent firewall or DNS pinning, not full transport timing, not actual host live playback. Public artifacts contain only projected counts/digests and opaque IDs, scratch catalog remains ephemeral. For durable exact replay, use authorized private content-addressed store (archive SHA-256 + original member locator/hash, response range ledger and validators, capture UTC, provenance and rights); never publish raw third-party archive, signed URLs, cookie or catalog on public GHA. Write-through capture of complete bytes is not implemented or claimed; full native plugin Search/Charts→download/stream→local pairing remains open.

## v0.0.282 first live host pairing (owner-approved)

Explicit one-candidate owner-only same-repository PR run, version and branch gated, plus existing manual workflow mode. Synthetic playback still runs first. Up to six chart findings and three MediaFire copies. For each eligible ZIP (<=128 MiB, supported audio member <=64 MiB), construct a descriptor from exact inspected raw name, header offset, CRC and share copy; SDK IPC actual foobar2000 streams first, with provider-only HTTPS policy on each hop, no proxy, 48 request and 16 MiB host body budget. Disable background metadata joins only in this isolated live playback mode. Then invoke the identical production MediaFire download implementation on the exact copy (separate 128 MiB capped acquisition worker), independently hash full ZIP and verify every complete observed range against exact offsets of acquired bytes. Extract only selected ZIP member by header offset, raw filename, CRC, method, compressed/uncompressed length into a fixed filename; native local playback same common DSP boundary. Hash-addressed archive scratch, not persistent private capture. Public evidence only sanitized timing/count/digest rows, no raw archive, member names, signed URLs, cookies or Robot outputs. Per-hop DNS/connect/TLS/request-to-headers where WinHTTP provides them, connection-first/index, protocol, first body, range SHA and coverage; grouped per-host distributions, not a calibrated replay yet. Incomplete attempts retained; live K=1 per specimen initially, synthetic K=5 unchanged. No independent OS egress boundary, no physical output/content claim, native worker chart discovery and direct descriptor not GUI or production publication UX. MEGA and metadata JOIN hosts (including Bangumi) separate future lanes; distinguish service role even when shared transport, capture cache/lookup state and join hit/miss, never conflate provider CDN with catalog/metadata service.

## v0.0.283 capture review corrections

Fix live reference routing before host launch; synthetic fixture generation is bypassed for live arms, without changing synthetic gates. Live Robot stdout/stderr is private scratch, worker raw stderr suppressed in live mode. Preserve failed acquisition observations and incomplete pairs; stop all live phases if a 429 is observed, require previous host exit. Add last successful nonempty body-read timestamp and nonempty read count, retain per-request phase and role (catalog, hoster resolution, hoster storage), separate headers-to-first-body and multi-read body span; do not interpret one read or decoder/hash/write time as wire throughput. Bangumi native API is api.bgm.tv, not a MediaFire host: future explicit metadata JOIN lane profiles lookup hits/misses, freshness, retry, cache and rate limits; other metadata services/hosters only when actual supported production adapter exists. Current one-shot owner-approved PR trigger updated to v0.0.283; no other PR or rerun may trigger live mode automatically.

## v0.0.284 live evidence and correction

Run #120 (45698055900), v0.0.283: real chart 193.98 ms, six offers 1546.29 ms, two ZIPs 111399495/521347095 bytes each indexed by one 65557-byte range; 344024376-byte 7z recognized with 73825 bytes/3 range requests, not audio indexed. One eligible host stream failed before HTTP headers (3.283 ms, status 0); no qualified live PCM. Retained sanitized artifact 15061334252. Native worker successfully used HTTP/2 or HTTP/1.x, so initial live host diagnostic uses same allowed protocol set (no production change); exact sanitized failure stage/Win32 error captured, no presumed root cause. Add raw QPC ticks, frequency, PID/TID, worker RPC span timestamps, separate archive inspection wall time, actual range extents and overlap/coverage summary with completed-only PCM cutoff. Keep missing phases missing. Revert packaged Python +1 line, redirect live harness output outside public artifacts instead. Reserve body budget atomically, not lock across WinHTTP I/O. UI search/row projection/index queue/publish milestones, cursor-operation parents and actual seek coverage still pending. No kernel ETW collection yet. WinHTTP connection flags can repeat connection setup timestamps on reused requests; retain raw semantics and do not classify all first flags as fresh connections without independent evidence.
