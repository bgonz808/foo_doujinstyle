# Corpus acquisition, write-through capture and deterministic replay

Status: design; existing synthetic frozen media corpus and loopback transport
tests are implemented. No external acquisition, real host UIA or live playback
is claimed here. Related #17 #18 #22 #26 #33.

## Candidate discovery through the actual UX

Current native/search.cpp creates an editable artist/title/tag query (tag:
prefix), Result pages 1/2/3 (20/40/60 maximum), Go, sort, tag facet, format
facet (Win32 control ID 142), Ready to drag (143), List and Album Art and
individual facet-clear buttons. IDs are source-derived hints, NOT observed UIA
AutomationIds. The format and tag facets derive their options from returned
rows, so not an exhaustive server-side query. Use both DoujinStyle search
and release detail/offer metadata as acquisition hints, retain advertised
format separately from verified archive member codec/container/metadata.

Robot/FlaUI sequence once UIA selectors qualified:
- Open Search, query a bounded synthetic population with multiple formats/tags.
- Inspect all controls and supported patterns; record tree + screenshot.
- Select each format/tag, verify independent expected row membership/count,
  clear facet and restore list without new index job or network requests.
- Switch List/Album Art, confirm same release identities and selected state.
- Exercise Ready to drag only after independent DB/reference publication
  check; retain discovery candidates before readiness too.
- Open release metadata/offer in isolated provider fixture; check advertised
  format, release ID, host families, distinct copies and archive filename hints.
- Do not depend on browser external navigation in offline regression. Assert
  correct target intent independently; provider live tests are a separate cohort.

## Acquisition and custody

Three distinct stores: reviewed synthetic fixtures in Git/CAS; private
quarantined write-through capture (authorized endpoints/content only);
approved immutable replay corpus manifest. Never auto-publish captured
HTML, media, credentials, tokens, cookies, signed CDN URLs, real user data or
copyrighted releases to public Git. No wholesale release downloads just to
find codecs. Use bounded listing/detail/offer probes, archive directory range
inspection and member header probes, then choose diverse small authorized
specimens, retaining exact complete archive/member only if needed and allowed.
Store originals separately from sanitized replay messages; hash both, bind
recipe/version and redaction record, review licensing/retention and source
provenance. Preserve query/response semantics, redirect relationships and
Range/If-Range/ETag behavior without replaying real credentials. Preserve
exact archive bytes when permitted; local control is independent extraction
of the literal member. Never silently substitute synthetic bytes and claim
identity to the real archive. For nonredistributable media keep private
authorized captures or use synthetic equivalents with explicit labels.
MEGA public link key material and encrypted ciphertext use synthetic keys
for distributable fixtures; preserve full-file MAC vs range verification
distinction.

## Selection coverage

Stratify by advertised/observed codec/container, tag encoding/metadata,
archive format/method/layout/size (including unsupported negative cases),
host family and provider path (MediaFire landing, redirects/CDN; MEGA public
API/encrypted ranges; other unsupported hosts), multiple copies, HTTP
validator/range/close/rate-limit behavior, filename encodings and pathology
IDs. Prefer rare uncovered factor pairs and small specimens; do not cartesian
multiply every dimension. Actual headers/independent archive parser establish
ground truth, not filename extensions or release labels. Include advertised
format mismatch, unknown metadata and wrong extension negative controls.
The canonical media-support.json contains 13 existing audio/container
profiles; recognized extensions are not decoder support claims.

## Replay and refresh

Serve approved immutable captures on loopback with explicit test-only
routing (production validation unchanged), real parser/worker/network/crypto
and archive/decoder paths. No live miss fallback in ordinary tests. Capture
mode is separate, explicit network allowlist, budget, policy and approved
endpoint case list. Write-through puts unapproved observations in quarantine;
only reviewed digest-pinned replay sets become CI fixtures. Record captured
timing distributions separately from tuned delay profiles (see
TRANSPORT_REPLAY.md); model request setup/response delay/pacing, connection
reuse and controlled faults; live HTTP/TLS and CDN drift checks are separate.
Replay fixtures remain immutable until reviewed refresh, retain prior
manifests for reproducibility. No real provider requests authorized here.
