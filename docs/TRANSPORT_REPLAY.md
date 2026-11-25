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
