# DoujinStyle v0.0.60 — native coordinator source candidate

Date: 2026-11-21

## Migration scorecard

| Audit | v0.0.59 | v0.0.60 | Delta |
|---|---:|---:|---:|
| Installed Python lines | 2531 | 2475 | -56 |
| Deprecated but still active | 512 | 603 | +91 |
| Retained fallback lines | 16 | 16 | 0 |

The physical retirement is 56 lines across the installed Python package, not
500+ and not a full coordinator removal. The remaining 603 marked deprecated
lines are **still active** and are not counted as retired.  The **native offer worker pool**
and **native browse checkpoint state machine** are substantive workflow migrations,
not merely decorator changes. The remaining Python result delivery, archive probe
pool, job launcher, and Windows idle/robots advisory have NOT been removed.

## Native and boundary test evidence

- Portable first-party worker: Clang C++20 `-Wall -Wextra -Werror`, **passed**.
- Root integration: **148 tests, 144 passed, 4 skipped**, zero failures.
- Native-package/boundary suite: **34 tests, 33 passed, 1 skipped**, zero failures.
- Compiled `test_catalog_offer_executor.cpp`: **passed**, including bounded
  concurrency, commit-before-notify, cancellation, 429 queue halt, no replay,
  input validation and filename evidence.
- Compiled `test_catalog_browse_step.cpp`: **passed**, including oldest-first
  progress, atomic page and checkpoint writes, restart and cancellation.
- Both changed C++ fixtures: **passed with ASan/UBSan** (`detect_leaks=0`)
  and **targeted Clang Static Analyzer** (zero diagnostics).
- Real native-worker IPC/SQLite fixtures: oldest-first traversal, relocation,
  failed navigation, and bad URLs passed.
- `@standalone_only` stripping and installed bundle failure boundaries tested.

### Important limitations

1. **No Windows x86/x64 binaries produced.** Pinned Windows SDK, foobar2000 SDK,
   MSVC CRT and Windows clang-tidy are not available in this runner.
2. **No live WinHTTP GET/POST validation or foobar2000 E2E.** Portable tests use
   synthetic replies. An HTTP 429 halts new native offer work but already-issued
   concurrent requests may finish.
3. **UI progress for a native offer batch is returned after the batch completes**,
   not streamed as each native task commits. Batches are limited to six.
4. **Browse wake pacing still uses Python on Windows**, pending the native UI/job
   launcher replacement. The old Python browsing algorithm is source-only.
5. Full combined sanitizer build and strict Windows clang-tidy remain unverified.

## Native test commands

```sh
# Build the portable native worker first; see NATIVE.md for generated SQLite / utf8proc prerequisites.
DOUJINSTYLE_TEST_PUBLISHER=/path/to/publish_references \
LD_PRELOAD=/path/to/libsqlite3.so \
python -m unittest discover -v

DOUJINSTYLE_TEST_PUBLISHER=/path/to/publish_references \
LD_PRELOAD=/path/to/libsqlite3.so \
python -m unittest discover -s native -p 'test*.py' -v

# New C++ fixtures build as normal standalone native tests.
# Enable ASan/UBSan, strict warnings, and static analysis for the two changed headers.
```

This is a **source candidate**, not an installable `.fb2k-component`.
