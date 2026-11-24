# Workstream and extraction ledger — v0.0.201

**Authoritative intent:** make every *partial* extraction auditable. A stale
stacked PR does not become safe to merge simply because one of its component
files was cherry-picked. Preserve the original PR head/source SHA and residual
scope; the new PR must independently qualify on current main.

## Completed, independently qualified extractions

| Source PR | Landed in current main | Historic source still not landed / disposition |
| --- | --- | --- |
| #19 BinSkim (draft) | #40 v0.0.198 — current pinned Windows Spectre/6-PE BinSkim, rules, SARIF evidence | **Unported** native-quality action, offline report/viewer and coverage tools in #42. Never revive old unmitigated build workflow. |
| #21 offline fixture matrix (draft) | Synthetic media manifest/generator, 206/416/429/503 mocks and media CI already on main (identical source files) | No remaining #21-specific code; any future decoder/host enhancement is #18/#22 |
| #23 Robot/ETW/cache/streaming (draft) | Existing host Robot, cache and decoder plans, ETW exporter, QPC contracts; #41 v0.0.200 builds isolated native ETW test component | Three long technical docs restored separately in v0.0.201: CACHE_MATRIX, STREAMING_IO_MATRIX and ARCHIVE_TOUCH. Still unqualified: actual Windows host E2E, output PCM and cross-action correlation in #33/#22 |
| #29 screenshots/catalog/host smoke (draft) | Main already has Robot screenshot/corpus/manifest generator and tests; #41 adds a **separate** x64 test component CI build | The old workflow rebuilt x64 in the production output tree; **do not merge** it. Actual screenshots/PCM/ETL remain #17/#22/#33 |
| #40 explicit Spectre + BinSkim | Merged; six PE scans /Qspectre passed | Maintained independently of test-only builds |
| #31 keyless CI attestations | Merged on main at v0.0.199, real post-merge standard+security predicates signed | Independent production release promotion remains #32 |
| #41 isolated ETW artifact | Merged on main at v0.0.200; test-only x64 component build succeeded | Real foobar2000 interactive Windows qualification still pending |

**Never label the first four old PRs themselves merged.** They are historic
sources with their own branch heads and potential unported code. Closing a
stale PR as superseded must link the exact corresponding new main PR/issue,
not claim all original content shipped.

## In-progress or genuinely unmerged work

| Source PR / issue | Still valuable | Safe promotion conditions |
| --- | --- | --- |
| #24 — facet clear + natural tag order | Actual user-facing dialog behavior not yet on main; old branch based on #23 | Extract against main with new version, portable comparator test, Win32/x64/ARM64EC compile/BinSkim and real UIA focus/theme/DPI smoke |
| #27 — Search/Charts native cutover | Native foreground coordinator, chart/prefetch, AST exclusion/deprecation, packaged runtime 1,375 -> 1,105 LoC *on historic candidate only* | Extract onto current main, differential native-vs-Python SQLite/IPC contract, offline/Windows build, Search/Charts Robot scenario, no unqualified runtime retirement |
| #28 — Download/Repair native cutover | Native explicit job state machine, cancellation/POST uncertainty, MediaFire/MEGA integration; historic remainder 887 LoC | Depends on current qualified #27 cutover, Download/Repair UIA/host contracts and live-provider controlled opt-in tests; don't inherit older BA2024 failures as waivers |
| #42 — leftover quality reporter from #19 | GitHub composite action, docs/QUALITY.md, tools/quality/coverage.py, report.py, viewer.py and their tests | Port independent of security scanner; preserve useful JUnit+LLVM coverage/denominators and prevent false host coverage |
| #33 — real host E2E | Actual Robot/FlaUI screen, first PCM, seek/cancel, coverage, WinHTTP, benchmark evidence | Provision isolated interactive Windows VM, pin host/component/asset hashes, use same-source ETW package, capture real ETL and screenshots |
| #22 — time-to-first-audio/seek/resource | Stage-specific end-to-end latency, output PCM sink and ownership-aware HTTP/ZIP metrics | See LATENCY_MODEL.md. Real native first-PCM must precede output or WASAPI claims |
| #10 / #26 — field regressions | Wavebar/Minibar analyzer overlap / TCP close and confirmed slow WAV #19732 | Run controlled archive/codec/cache baselines, preserve source structural metadata, no guessing actual ZIP method |
| #18 / #25 — provider/archive capability | Native process/provider stub E2E, 7z/RAR architecture and solid-group caching | ZIP STORE/DEFLATE only baseline; explicitly unsupported remote RAR/7z until proven |
| #20 — encoding integrity | Per-file raw ZIP filename, metadata, contents and Unicode/japanese oracles | Verify per file+tag, reject ambiguous/colliding names and corrupt metadata |

All prior source heads remain available for source-by-source comparison:
- #19: `acb363de5af179f695a0a7a2cf84045ad84f1e69`
- #21: `c2a7c41c82c921e0b573531bd0af5e69d08e39de`
- #23: `64433a30d859abee8ab9b7515c56b8eed309424a`
- #24: `65938a7a87654a843114d24c72a7600e246d29ac`
- #27: `608022178d4feb65590808909179ac4c4e5c900f`
- #28: `4774810bea82cbf818c56a795de5dce7adf12e26`
- #29: `a3a5eebffa311bd54c2276ef2efc884919075047`

## Issue lifecycle automation — v0.0.203 candidate (#45)

The 2026-11-23 issue hygiene audit reconciled all 20 then-existing non-PR
issues to actual work states. #6 (six-PE BinSkim gate) and #35 (historical
ARM64EC missing-directory blocker) were closed as completed/superseded with
actual build evidence and residual host work transferred to #2/#33.
Other unresolved issues remain open with verified progress and next steps.
The new #45 tracks the read-only scheduled lifecycle audit and policy in
[ISSUE_LIFECYCLE.md](ISSUE_LIFECYCLE.md). This issue-status automation
does **not** prove production behavior or change native code.

## Test truth boundaries

- **Portable contracts:** execute C++ state machines/Python oracles; do not
  prove actual WinHTTP/foobar2000 GUI/decoder behavior.
- **Windows build:** compiled PE and package, can establish BinSkim and
  test-component packaging, not successful Windows host playback.
- **Host E2E:** requires independent interactive GUI/PCM/ETW/fixture oracles,
  actual source- and binary-SHA manifest, source coverage denominators where
  measured, and explicit output-null versus PCM/WASAPI distinctions.
- **Release:** requires signed provenanced artifact plus separate approved
  governance and sustained SBOM/SCA; CI attestations are not approval.

Update this ledger and dependent issue scopes whenever extracting another
slice from an old PR; **do not leave working code stranded in an unmerged
ancestor branch or reinterpret source-only status as host-qualified**.
