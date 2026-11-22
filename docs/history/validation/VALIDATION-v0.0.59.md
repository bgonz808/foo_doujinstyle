# DoujinStyle v0.0.59 — source-only migration candidate

Date: 2026-11-21

## Burndown (installed zipapp, AST-verified)

- Baseline v0.0.58: **2,612 physical Python lines**.
- Candidate v0.0.59: **2,531 physical Python lines**.
- **81 physical lines removed**. `@standalone_only` selectors and old
  coordinators remain in source for parity and standalone CLI use.
- **512 marked deprecated but still active**. These are not retired.
- 16 retained fallback lines.

## Native changes

- `catalog_index_plan`: current offer/archive selection and replan after
  committed discovery, with bound ID cardinality, strict provider URL
  validation and malformed JSON resistance.
- `catalog_idle_plan`: native phase eligibility for Details, Links and ZIP/
  header/cover probes. Each phase is requested after previous stage commits.
- Compiled worker is an independent C++ process, not a Python extension.
- Production-facing Python execution and cancellation is NOT yet retired.

## Portable verification (this runner)

- Clang C++20 compile of the **full portable native worker**: `-Wall -Wextra
  -Werror`, passed.
- `native/test_catalog_index_plan.cpp`: passed; ASan and UBSan passed with
  `ASAN_OPTIONS=detect_leaks=0`, `UBSAN_OPTIONS=halt_on_error=1`.
- Targeted static analyzer (`clang++ --analyze`): passed with no diagnostics.
- Native-backed Python integration: 142 tests run, 139 passed, 3 Windows-only
  skipped, zero failures. Uses bundled SQLite 3.54.1 via LD_PRELOAD.
- New real-IPC tests for stale offers, malicious URLs, current copies,
  finished ZIP probes, interrupted native requests, and code stripped from
  the installed worker. Idle phase order is tested after committed mutation.

## NOT verified

- Windows SDK/foobar2000 SDK builds, strict Windows clang-tidy, live WinHTTP
  and CNG operations, live cancellation and foobar2000 E2E playback.
- The native job runner is not yet the owner of network execution, user-visible
  progress or Stop signals. Python remains in the installed worker.

**Do not distribute as an installable `.fb2k-component`.**
