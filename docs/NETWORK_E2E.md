# Controlled real-network E2E transport profile — v0.0.175

**Offline VM host E2E remains default.** This probe is a separate networked
transport-only profile, not a foobar2000 playback result. CI runs offline
unit/mocked threat-model tests and makes no live provider requests.

Set quality/network-e2e-allowlist.json enabled=true and populate exact public
DNS hosts (not IP addresses, wildcards or suffixes). User-provided case manifest
lives OUTSIDE the Git checkout: [{"id":"case-001","url":"https://approved.example/..."}]
Never commit URLs with tokens or sharing secrets. Use explicit per-run network
firewall policy with egress restricted to approved IPs; Python checks alone
do not provide a trusted network isolation boundary.

Run only on a separate approved network-isolated test VM:

    python -m tools.network_e2e --policy quality/network-e2e-allowlist.json \
      --cases C:/e2e/private-cases.json --output C:/e2e/results/run-001.json \
      --confirm-isolated-network-profile

Safety properties: exact HTTPS host/port, certificate validation with SNI,
DNS answer inspection and per-request pinned public IP TCP sockets,
redirect host re-authorization, no environment proxy use, bounded
requests/redirect depth/duration/response bytes, no uploaded response body,
no logged URLs, paths, userinfo, headers or redirect secrets. Every failure
is a sanitized fixed category. Unknown providers/redirects fail closed.

To tune, change only host list and bounded budget fields. No automatic
wildcard promotion, domain suffix matching or alternative provider fallback.
Do not share host fixture test results as audio or native host qualification.
