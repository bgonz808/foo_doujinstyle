# foo_doujinstyle — post-GitHub initialization engineering backlog

Status: proposed plan, not executed. Historical source baseline: v0.0.64 source candidate (portable-only; Windows unqualified). Planned development version: `0.0.65-dev.N+gSHA[.dirty]`.

## Operating constraints

- Treat `bgonz808/foo_doujinstyle` as the only authorized project for this work. A chat instruction is not a GitHub access-control mechanism; select only this repository when authorizing the GitHub App. GitHub's ChatGPT connector is read-only; writes require an explicitly approved compatible coding workflow or the owner's local `gh` CLI.
- No new production Python orchestration or adapters. Preserve source-only Python oracle fixtures only while tests demonstrate parity; do not ship them.
- Count **physical Python lines actually packaged and executable**, not annotations or repository-wide test files. v0.0.64 baseline: **1,375 packaged runtime lines**, **287 active-deprecated lines**, **0 fallback lines**.
- Every output records exact source identity; a source-only milestone is not a qualified Windows component.
- No live provider traffic in ordinary push/PR CI; fixture capture must be explicit, read-limited, sanitized, and reviewed.

## P0. Repository/build foundation — before substantive migration

**Issue A — version identity and reproducible builds**
- One checked-in `VERSION` base; derive development SemVer as `0.0.65-dev.N+gSHA[.dirty]`, where N requires stable history and release builds require full history.
- Git-free fallback: `0.0.65-dev.0+src.HASH` (source snapshot identity, never a fabricated Git SHA).
- Generate a C++ header for full SemVer, full source SHA, source-identity kind, dirty boolean, and (if dirty or Git-free) full snapshot SHA-256. Keep Windows numeric VERSIONINFO distinct.
- Maintain approved display string `...+gSHA.dirty`; use a separate snapshot fingerprint in diagnostics and a short `-tHASH` suffix in dirty artifact filenames to disambiguate dirty builds.
- Replace hardcoded C++ version and source-regex parsing in both `native/build.ps1` and `native/build_cross.py`; remove `SOURCE-ATTRIBUTION.json` and its generated-hash machinery.
- Tests: clean Git, dirty tracked file, staged edit, untracked source file, tracked deletion, no Git, shallow checkout, malformed VERSION, repeated generation stable, same source across supported development OSs.

**Issue B — deterministic dirty snapshot serialization (`DS-SOURCE-V1`)**
- Include Git-tracked files plus nonignored untracked source inputs; explicitly exclude generated output, `.git`, caches, external SDKs, and credentials. No ignored file may silently affect the compilation: all external inputs must be separately declared/pinned.
- Sort paths by raw UTF-8 bytes after converting separators to `/`; reject ambiguous paths/case-collisions, invalid encoding, and traversal segments. `.gitattributes` should enforce LF for source/build inputs; do not silently normalize arbitrary binary contents.
- Hash records with SHA-256, using binary length framing (not ambiguous text concatenation): domain prefix `DS-SOURCE-V1\0`, then for each entry `type:u8 || executable:u8 || path_len:u32be || path_utf8 || content_len:u64be || SHA256(content)`; regular-file contents are raw bytes; symlink content is the link target bytes; include a tombstone for a tracked file removed from the working tree. Do **not** encode timestamps, owner IDs, absolute paths or host names.
- Report full 256-bit digest. A shortened identifier is for filenames only; the complete digest is embedded/available in diagnostic output. A dirty snapshot digest attests to inputs, not reproducibility or authenticity. Never stage user files into Git's object database merely to compute the digest.
- Git-free archives use the same record format with an explicit allowlist / packaging rules to select their inputs; test equivalence to clean exported sources. No separate checked-in attribution JSON is needed.

**Issue C — minimal GitHub Actions security**
- Pin `actions/*` to reviewed full commit SHA (or a verified workflow dependency lockfile if account supports it); use Dependabot to propose updates.
- Pin external toolchain releases + archive SHA-256, keep download endpoints documented; avoid mutable curl-to-shell installers.
- Prefer GitHub-hosted ephemeral Ubuntu and Windows runners. Ordinary CI: read-only `contents: read`, `persist-credentials: false`, no provider secrets, no `pull_request_target`, no privileged execution of fork PR artifacts, timeouts and bounded job scope.
- Separate trusted release workflow (protected tag/manual + approval as supported) from PR checks. Grant narrowly scoped write/OIDC permissions only to release/attestation steps. On private Free/Pro/Team repos, check artifact attestation entitlement rather than assuming it is included.
- No unattended live external provider calls. Unit/contract tests use fixtures and local HTTP servers.
- Pin build environment inputs; produce Windows x86/x64 plus portable tests, then independently rebuild and compare **unsigned** DLL, worker and deterministic `.fb2k-component` ZIP byte-for-byte. Signing and CI run ID belong in external release provenance, not deterministic DLL inputs.
- Artifact identity = base version + source identity + target + final SHA-256; preserve environment/toolchain hashes externally. Avoid secret-carrying artifacts/caches.

## P1. Foreground Python-to-C++ migration (first post-init native PR)

**Issue D — native search and chart desktop job entrypoints**
- Create complete C++ foreground entrypoints, launched directly by `native/component.cpp` similarly to existing native `--idle-backfill`, with no new installed Python adapter.
- Native owns search paging, chart refresh/freshness/retry/ranking projection, cache commits, and user-visible result event sequence; preserve existing foobar2000 UI control-byte protocol.
- Fail closed on malformed frames, mismatched page identities, out-of-order or duplicate terminal data; events must be emitted after successful durable SQLite commits and visible to independent connections.
- Validate Stop/cancel at network and commit boundaries; no false success if the process is terminated after a commit; reconcile durable catalog state on restart.
- Differential tests against current source-only Python behavior, plus simulated network 429, partial page, timeout, concurrent searches, stale cache, forced refresh, and interrupted worker.
- After parity, **delete** now-superseded packaged `run_search`, chart launch/projection and foreground worker orchestration. Run AST/inventory checks proving no runtime imports survive; keep standalone parity oracle outside the installed artifact.
- Record packaged runtime Python delta from **1,375** and active-deprecated from **287**, with call-site and test evidence.

**Issue E — native result projection and event lifecycle**
- Make foobar2000-native callbacks/thread affinity explicit. No Python in process/event/control routing.
- Preserve nonreentrant IPC, bounded frame handling, release/date fields, stable playlist references, and cancellation correctness.
- Contract/integration tests include commit-before-event and exactly-once **local UI projection** on replay/reconciliation. Do not claim exactly-once external POST delivery.

## P2. Continue removing the remaining runtime Python

**Issue F — foreground indexing/repair coordination**
- Retire any still-packaged `native_offer_batch`, `schedule_index`, `run_popular`, `repair_release` and wrappers only after native parity; inspect actual current packaged inventory before deleting.
- Preserve durable `catalog_post_attempts`: pre-POST SQLite ledger, conservative unknown-outcome recovery, 429 halt, archive deduplication, and safe manual resolution. Verify Stop vs. commit/event boundary against a real SQLite database and local HTTP test server.

**Issue G — archive download, metadata projection, and zipapp retirement**
- Audit remaining packaged Python for archive download/recovery, MediaFire/MEGA integration, ZIP inspection, metadata/date conversion, and process/launcher plumbing. Migrate complete ownership boundaries to C++; do not add temporary production Python proxies.
- Test path traversal/ZIP bombs and member validation, byte-range resumption, temporary file atomicity, provider identity changes, provider throttling, and failure/rollback semantics.
- Remove Python payload packaging only after all production entrypoints are native and foobar2000 feature parity is verified; keep Python only for development/testing if needed.

## P3. Windows qualification / release criteria

- Restore known-good historical dependencies: foobar2000 SDK 2026-10-01, LLVM 23.1.6, Windows SDK 26100 family and MSVC CRT 14.44 family; verify actual availability and hashes independently rather than asserting restoration.
- Build x86 and x64 `.fb2k-component` on Windows; cross-build on Linux as an independent diagnostic. macOS portable core builds are welcome; macOS cross-build output requires its own validation.
- Run strict clang-tidy/Clang static analysis, C++ portable sanitizers, Windows host smoke tests, foobar2000 UI callbacks, Job Object Stop, WinHTTP/remote provider smoke, and uncommitted POST recovery tests.
- Keep live HTTP out of normal CI. Manual pre-release GET/HEAD probe with a small per-provider budget; stop on 429. Any remote POST/download needs separate human approval.
- Distinguish source-tested / Windows-built / host-validated / release-qualified in the release notes.

## Suggested GitHub issue order

1. `build: implement Git/dirty/Git-free source identity and deterministic packaging`
2. `ci: portable + Windows build matrix with minimal Actions permissions`
3. `test: document fixture capture and manual live-provider testing policy`
4. `refactor(native): eliminate foreground Python search/chart entrypoints`
5. `refactor(native): remove Python result and event projection`
6. `refactor(native): retire remaining foreground indexing/repair wrappers`
7. `refactor(native): remove archive/download/ZIP runtime Python and launcher`
8. `test(windows): qualify x86/x64 foobar2000 component and Stop semantics`

Keep issues 4–8 gated on concrete parity evidence and a verified reduction in packaged runtime Python, not merely the presence of new C++ files.
