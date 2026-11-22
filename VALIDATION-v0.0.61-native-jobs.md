# Native coordinator continuation — v0.0.61 source candidate

**Status:** source-only; **not** a foobar2000 installable binary and **not** a Windows-qualified release. Revalidated 2026-11-21. The uploaded candidate was used as the baseline; the component's compiled version string still reads `0.0.60` in `native/component.cpp` and has deliberately not been relabeled.

## Changes in this checkpoint

1. `native/catalog_index_dispatch.hpp`: C++ foreground coordinator uses the native retry/eligibility planner, bounded parallel offer executor, single SQLite committing thread, archive re-planning after each offer batch, shared-copy de-duplication and HTTP 429 shutdown. Portable compilation fails closed when WinHTTP offer discovery is required.
2. `native/catalog_offer_executor.hpp`: commit results now include native provider capability/status projections. Stop that races an in-progress SQLite transaction still emits the committed event **exactly once**; no invisible committed state is mistaken for a retryable task. A stop between commits prevents future commits; WinHTTP requests in flight are not magically rolled back.
3. `native/site_catalog.hpp`: native multi-page search emits strictly ordered, post-commit page events with date projections from committed SQLite listings. The C++ job owns tag parsing, deduplication, pagination, late-page failure and cache/fetch selection.
4. `native/search_worker.py`: foreground search no longer drives page-by-page native IPC; the old parser/loop is explicitly source-only. Foreground indexing uses the native offer coordinator; Python **still** owns archive probe scheduling, UI projection and reference publication. Cover/icon threads now use independent native transport peers.
5. `native/reference_publisher.py` and `native/publish_references.cpp`: opt-in JSONL progress events, a strict one-terminal-frame contract, 64 MiB maximum frame, single-request-per-pipe interlock, fail-closed malformed-frame rejection, immediate kill on protocol/callback failure and graceful reuse after a valid application error. Event handlers cannot synchronously reenter the same pipe.
6. `native/toolchain_doctor.py`: reproducible source/LLVM/SDK fingerprint audit distinguishes Windows native MSBuild from Linux/macOS cross-compilation; rejects incomplete environments instead of claiming a qualified DLL.

## Contract and failure matrix

| Boundary | Guarantee tested | Residual risk |
|---|---|---|
| Offer network tasks | Bounded concurrency; worker threads never write SQLite; stop/429 halt pending dispatch | Concurrent in-flight requests may already have started before 429 |
| SQLite commit → progress event | Only the committing thread emits; event is after commit; Stop during write still reports committed row | OS/process termination between commit and event can leave a durable but unreported update; recovery must reconcile SQLite |
| Search page → UI | C++ commits/deduplicates before emitting page; no nested native UI-date request | Windows WinHTTP and host/UI dispatch remain untested |
| Worker request/response | Opt-in events, strict terminal result/error, bounded frames, no concurrent/reentrant request | JSONL is not a version-negotiated, durable event log; a crashed process can truncate output |
| Native cancellation from foobar | Existing foobar2000 Stop closes `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` job and terminates descendants | Host-integrated Stop and race tests have not been run on Windows; no successful transaction can be rolled back |
| One-time POST | No retry/replay inside one native offer job after uncertain response | A crash **before recording** POST outcome permits retry on a subsequent session. A durable pre-POST attempt ledger / idempotency policy is still needed |

## Tests performed in this container

- Python root suite (bundled SQLite 3.54.1): **176 run, 5 skipped, 0 failed**.
- Native Python suite: **62 run, 2 skipped, 0 failed**.
- Compiled `test_catalog_index_dispatch.cpp` fixture: passed concurrent completion, commit-before-event, HTTP 429, shared-copy replan/dedup, duplicate/missing IDs, Stop-after-first-commit, and **Stop inside the SQLite update transaction**.
- Compiled `test_catalog_offer_executor.cpp` fixture: passed; source rebuilt with C++20 `-Wall -Wextra -Werror`.
- Recompiled portable `publish_references` helper with C++20 `-Wall -Wextra -Werror`; real-process tests confirm search cache/late failure/commit-event boundaries.
- The updated sanitizer rebuild **timed out in this sandbox**. A previous candidate's sanitizer binary exists but is **not counted** as validation of the current changes. Current-revision ASan/UBSan is an OPEN gate.
- `native/toolchain_doctor.py --mode cross --output build-linux/toolchain-audit.json` returned **not ready** (19 missing checks including foobar SDK, Windows sysroot and clang-tidy).

## Honest packaged-Python burndown

| Metric | Uploaded v0.0.61 | This candidate | Net |
|---|---:|---:|---:|
| Packaged Python physical lines | 1,445 | 1,513 | **+68** |
| Deprecated but still active lines | 370 | 391 | **+21** |
| Packaged module count | 6 | 6 | 0 |

This checkpoint migrates responsibilities, but **is not yet a Python line-count reduction**. New IPC validation, event adapters, and concurrency guards exceed the Python code retired from the installed search path. The `@runtime_deprecated` functions remain **active** and are never counted as removed. Source-only functions omitted by `pack_search_worker.py` are actually excluded.

Next substantive removals should target the remaining Python archive-probe dispatcher, idle orchestration, chart-result assembly, and the Python process launcher. Those must move behind contract-tested C++ APIs before their active Python is deleted. Refuse cosmetic line-count gains that merely move production behavior to a fallback path.

## Reproduction

With `native/sqlite_source.py`'s generated SQLite files and a portable `publish_references` build available, set:

```sh
export DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references"
export LD_PRELOAD="$PWD/build-linux/libsqlite3.so"
python3 -m unittest discover -q
python3 -m unittest discover -s native -p 'test*.py' -q
./build-linux/index_dispatch
./build-linux/offer_executor
python3 native/pack_search_worker.py --inventory
python3 native/toolchain_doctor.py --mode cross --output build-linux/toolchain-audit.json
```

The compilation environment and SDK/host qualification recipe are in `native/TOOLCHAIN_RECOVERY.md`.
