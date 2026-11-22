# DoujinStyle v0.0.64 — native idle frontend source candidate

**Date:** 2026-11-21. **Qualification:** SOURCE-ONLY; portable native build and tests passed, Windows foobar2000 x86/x64 component build and host integration NOT performed in this workspace.

## Actual Python retirement (do not conflate source with packaged runtime)

| Inventory | v0.0.62 previously released source | Start of this checkpoint (unreleased v0.0.63 worktree) | v0.0.64 |
|---|---:|---:|---:|
| Packaged runtime Python (physical lines) | 1,500 | 1,491 | **1,375** |
| Active deprecated packaged Python | 386 | 363 | **287** |
| Runtime Python fallback | 0 | 0 | **0** |
| Packaged Python modules | 6 | 6 | **6** |

The installed idle job runs `publish_references.exe --idle-backfill`, launched **directly** by foobar2000's native UI under its existing Windows Job Object. The Python `run_idle_layers` source-only parity reference, old zipapp idle launcher and idle dispatch branch have been excluded/removed from production. No new production Python adapter was added. Relative to the published v0.0.62 package, there are **125 fewer executable Python physical lines**, or 116 fewer than the current checkpoint's starting tree.

## C++ contracts and differential coverage

- Native idle frontend: manages details, offers, archives, artwork, reference publication, Bangumi signals and browse continuation. No nested pipe operation and no Python callback ownership.
- Detail jobs: one native selection, no separate preflight selection race. Parsed observations are validated, SQLite committed, then announced; the terminal must match exactly the event sequence. A real SQLite two-connection test proves other readers see a committed row before announcement, and a failed second fetch is recovered without refetching the first committed item.
- Archive jobs: validates selected copy identities and release memberships; native archive writer commits before event. Terminal response must exactly match streamed events after stripping cover payload, including attempted and selected counts, cancellation and rate-limit flags. No fake final-success output on failure or Stop. Durable POST ledger from v0.0.62 remains intact.
- Publication: only after successful archive-commit events, notification cardinality checked; no publication on interrupted dispatch. Archive members, bytes transferred and sanitized errors have user-visible output parity.
- Browse: respects Windows input-idle gate, preserves checkpoint progress, source robots.txt advisory (not an enforcement gate), bounded browse continuation; site resource is origin-restricted and capped at 64 KiB. Simplified native robots parser flags complex wildcard patterns for manual review rather than misreporting an allow/deny verdict.
- Native launcher: C++ executable bytes extracted into a private `.exe` file; Windows Job Object termination still needs host tests to qualify Stop semantics.

## Execution evidence

| Gate | Result |
|---|---|
| Portable C++ worker (Clang 17, C++20, `-Wall -Wextra -Werror`) | **PASS**, freshly rebuilt |
| C++ complete idle frontend fake-commit/interrupt/malformed-terminal fixture | **PASS** |
| C++ real SQLite detail commit/read-after-commit/restart fixture | **PASS** |
| C++ robots allow/disallow/group/ambiguity fixture | **PASS** |
| ASan + UBSan idle frontend fixture | **PASS** |
| ASan + UBSan SQLite detail fixture | **PASS** (bundled sqlite3 object is not sanitized) |
| ASan + UBSan robots fixture | **PASS** |
| Root Python/SQLite/native IPC integration tests | **183 tests, 5 skipped, 0 failures** |
| Native Python/SQLite IPC test suite | **69 tests, 2 skipped, 0 failures** |
| Windows SDK, MSVC CRT, clang-tidy and foobar SDK preflight | **BLOCKED**, 19/26 required checks absent |
| Windows x86/x64 `.fb2k-component` binary | **NOT BUILT** |
| Live WinHTTP, media providers, foobar2000 event dispatch and Job Object Stop | **NOT TESTED** |

Root tests require `LD_PRELOAD` for the bundled SQLite 3.54.1 library; the system Python-linked SQLite version lacks a tested extension feature and fails some existing tests without preload.

## Toolchain recovery

Earlier sessions built complete x86/x64 foobar2000 components with foobar SDK 2026-10-01, Windows SDK 26100, MSVC CRT 14.44 and LLVM 23.1.6. This Linux workspace has Clang 17, LLD and bundled source libraries, **not** the external Windows SDK, foobar SDK, CRT sysroot or matching clang-tidy. The official SDK download URL was confirmed but network retrieval from this container failed. The known-good version matrix and exact Windows/Cross build commands are preserved in `native/TOOLCHAIN_RECOVERY.md`; source and package provenance are separate. The migration does not claim these historical dependencies have been restored here.

## Limitations and next retirement targets

Remaining packaged Python is mostly query/chart/UI result projection, foreground indexing wrappers and archive download/repair control. The next C++ retirement should own the query/chart desktop job entrypoints directly, preserving the foobar2000 UI control-byte protocol and standalone native worker JSONL isolation. Avoid new production Python adapters. Before Windows release, require both-architecture builds, static analyzer pass, live WinHTTP request/POST, interrupted in-flight POST reconciliation, provider 429 handling, UI callbacks and Stop tests; no remote exactly-once semantics are claimed for unresolved POST outcomes.

## Reproduction

```bash
clang++ -std=c++20 -O0 -g0 -Wall -Wextra -Werror -pthread -I native -I build-linux \
 native/publish_references.cpp native/descriptor.cpp build-linux/sqlite3.o build-linux/utf8proc.o -lz -lcrypto \
 -o build-linux/publish_references
LD_PRELOAD="$PWD/build-linux/libsqlite3.so" DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references" \
 python -m unittest discover -q -p 'test*.py'
LD_PRELOAD="$PWD/build-linux/libsqlite3.so" DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references" \
 python -m unittest discover -s native -p 'test*.py' -q
python native/pack_search_worker.py --inventory
python native/source_attribution.py --output SOURCE-ATTRIBUTION.json
python native/toolchain_doctor.py --mode cross --output build-linux/toolchain-audit-v0.0.64.json
```

The test source includes direct C++ fixtures that compile independently for sanitizer runs; do not interpret their success as Windows runtime qualification.
