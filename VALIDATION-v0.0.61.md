# DoujinStyle v0.0.61 recovery validation — source candidate

The interrupted v0.0.61 source workspace was recovered and revalidated on Linux on 2026-11-21. This release is **source-only**; no Windows DLL or `.fb2k-component` is qualified.

## Installed Python footprint

| Metric | v0.0.60 | v0.0.61 | Delta |
|---|---:|---:|---:|
| Shipped Python physical lines | 2475 | 1445 | -1030 |
| Shipped Python modules | 9 | 6 | -3 |
| Shipped Python AST statement lines | 1760 | 1026 | -734 |
| Deprecated but active packaged lines | 603 | 370 | -233 |

The Python reference paths in `doujin.py`, `native/archive_zip.py`, `native/archive_providers.py`, and `native/idle_backfill.py` remain **in source**, but are absent from the installed `worker_sources()` bundle. The shipped Python still launches native operations and handles result delivery / remaining scheduling. `@runtime_deprecated` code remains active; only `@standalone_only` and native-worker-only exclusions remove runtime code.

## Native and end-to-end boundaries

- One C++ archive inspector reuses the bounded ZIP reader, filename projection, header probes, and artwork selection. It serves the cross-provider inspection path rather than introducing separate provider-specific parsers.
- The installed package has no Python HTTP, ZIP, provider, or lxml implementation. Native bridge failure is fail-closed where catalog writes and probes require the helper.
- UI date display uses native date projection; saved chart rankings remain readable after helper startup failure, omitting a live date field rather than launching retired Python.
- Native provider capability, bounded artwork/robots transport, idle-gate rules, archive filename evidence, and source-only Python parity all have fixtures.

## Portable tests, independently rerun in the recovered worktree

- Root integration: **164 tests**, **4 skipped**, **160 passed**, **0 failed**.
- Native-package integration: **50 tests**, **1 skipped**, **49 passed**, **0 failed**.
- Native archive inspection: Clang C++20 `-Wall -Wextra -Werror` rebuilt; exact report parity with existing compiled fixture.
- Native archive sanitizer fixture: ASan+UBSan passed with `detect_leaks=0` (sandbox limitation) and reproduced identical archive output.
- Native idle gate executable completed without error.
- Archive inspection coverage from the recovered LLVM profile: **88.17% line**, **63.46% branch** for `archive_inspection.hpp`; includes instrumented fixture coverage, not an exhaustive host run.
- Tests used bundled SQLite **3.54.1** via `LD_PRELOAD`, because this Python environment embeds SQLite 3.46.1.

## Qualification still open

- Windows x86/x64 SDK and foobar2000 SDK builds, strict Windows clang-tidy and Clang Static Analyzer of the full component.
- Windows WinHTTP/CNG and live MediaFire/MEGA tests, foobar2000 playback, real Stop/cancellation and input/main-thread callback checks.
- Remaining Python job launcher and UI-side progress callbacks still require native migration before the zero-runtime-Python goal.

The change is **not installable**. Use `native/pack_search_worker.py --inventory` to reproduce physical line counts and `test_reference_publisher.py` with the compiled native helper to test the installed ZIPapp in isolation.
