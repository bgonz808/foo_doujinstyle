# DoujinStyle v0.0.62 — native archive dispatch / POST uncertainty checkpoint

**Candidate date:** 2026-11-21. **Status:** source-only; NO Windows DLL or foobar2000 `.fb2k-component` built. **Canonical component version:** `0.0.62` in `native/component.cpp` (corrects the prior v0.0.61 source candidate's `0.0.60` embedded-version mismatch). Next changed source candidate must bump the embedded version again. Do not publish this as a qualified installable release.

## Deliverables and migration

- `native/catalog_archive_dispatch.hpp`: C++ concurrent archive pool, SQLite link and URL revalidation, shared-copy deduplication, completion/commit-before-event, strict bounded archive results, HTTP 429 dispatch halt, Stop-during-SQLite-commit reporting, native idle pacing and no Python network fallback.
- `native/publish_references.cpp`: foreground/idle archive native operation; each probe receives its own native Publisher/WinHTTP/ZIP context. Portable build rejects live WinHTTP archive work. Native POSTs are gated by a **durable pre-POST SQLite marker**; the legacy unjournaled `site_release_offers` native RPC is rejected on every platform.
- `native/post_attempt_ledger.hpp` + schema/offer-ingest changes: an attempt record is committed *before* outbound POST; another process refuses replay while an unresolved marker exists. Multiple forms share a private owner. Successful offer ingestion atomically clears its marker; timeout/error/crash retains it. Read-only `catalog_post_attempts` audit returns metadata without the private owner. This protects against blind automatic replay, **NOT true end-to-end exactly-once POST**. A crash before sending may still leave a stale marker; remote execution may occur without a local commit.
- `native/search_worker.py`: the installed zipapp no longer includes Python archive probes, archive persistence and per-copy archive loop. Native archive completion events drive progress/artwork/reference refresh; idle archive execution uses the native coordinator; repair keeps copy identity through a 403-triggered fresh-offer replan. Legacy implementations remain *source-only* for parity tests.
- `native/source_attribution.py`, Windows/cross build scripts: manifest of canonical version, deterministic SHA-256 digest of the versioned source tree, and file count is embedded in every future built `.fb2k-component` (not confused with SDK/toolchain or final binary hashes). A separate source archive also includes `SOURCE-ATTRIBUTION.json`.

## Validation matrix

| Gate | Evidence | Result |
|---|---|---|
| Strict C++20 portable worker compile | GCC `-Wall -Wextra -Werror` with pinned SQLite 3.54.1 | PASS |
| Native archive dispatcher fixture | Bounded parallelism; SQLite commit before events; shared links; reject stale/mismatched jobs; 429 halt; cancel after/during commit | PASS |
| Durable POST ledger C++ fixture | Cross-connection restart, owner exclusivity, multi-form count, atomic successful resolution, timeout remains pending | PASS |
| ASan + UBSan native fixtures | Clang 17 `-fsanitize=address,undefined -fno-omit-frame-pointer`, ASan leak checks enabled, both fixtures rebuilt and executed | PASS |
| Root Python integration suite | `python3 -u -m unittest discover -v` with portable worker and bundled SQLite preloaded | **183 tests; 5 skipped; 0 failed** |
| Native Python suite | `python3 -u -m unittest discover -s native -p 'test*.py' -v` | **69 tests; 2 skipped; 0 failed** |
| Installed-worker boundary | Fails closed without Windows WinHTTP; no Python archive writer/prober; malformed terminal; native archive events; 403 repair path | PASS within mocks/portable IPC |
| Unguarded legacy POST operation | Native IPC test verifies unjournaled `site_release_offers` is rejected | PASS |
| Windows MSBuild/ClangCL, foobar2000 SDK, xwin/sysroot | `native/toolchain_doctor.py --mode cross` | **BLOCKED: required SDK, Windows library/sysroot and matching clang-tidy unavailable** |
| Windows x86/x64 `.fb2k-component` | No build attempted without dependencies | **NOT BUILT** |
| Live provider, foobar2000 callbacks and Job Object Stop | Requires Windows/foobar2000 environment | **NOT TESTED** |

**Skipped tests** are mostly platform/input/host-dependent; do not count them as passing Windows qualification. Portable ASan tests cover core queue/transaction logic with injected fetchers, not WinHTTP, CNG, foobar2000 callbacks or remote provider state. The `test_site_offers.py` source-only oracle is retained for parity but the production unjournaled RPC is disabled.

## Runtime Python inventory

| Measurement | v0.0.61 source candidate | v0.0.62 source candidate |
|---|---:|---:|
| AST-pruned packaged Python physical lines | 1,513 | **1,500** |
| Packaged still-active deprecated lines | 391 | **386** |
| Packaged Python fallback lines | 0 | **0** |
| Python modules in zipapp | 6 | **6** |

The modest **13-line** net reduction reflects replacement of large legacy responsibilities *plus* new IPC protocol validation/adapters. Source-only Python remains in the repository as a test oracle and is omitted from production zipapp; it is not the same thing as zero repository Python. Continued candidates should target Python idle detail/offer projections, chart orchestration, UI/result projection and launcher with contract-tested C++ alternatives.

## Failure model and manual recovery

1. A POST marker is written in SQLite before any network bytes leave. A crash/timeout after the marker may mean POST succeeded, failed or never left the host. The marker is deliberately conservative; no automatic age-based clearing or restart replay.
2. Inspect unresolved attempts via the read-only `catalog_post_attempts` operation. Compare each release against last locally committed offer data and provider-side state. Back up the catalog and stop all workers before an explicitly reviewed/manual marker resolution. The production API does not offer a silent automatic `ack` endpoint.
3. An archive commit may survive a process kill while its progress event is lost. Reconcile the catalog rather than assuming event absence means the job did not commit. Native Stop and Job Object behavior still require Windows tests.
4. The installed `ReferencePublisher` API disallows reentrant/concurrent requests on a pipe. Archive cover events use bounded hex and run after commit. Offer POST and GET are performed without SQLite write locks held during network calls.

## Reproduction and provenance

Run from this source tree with the pinned `native/sqlite-amalgamation-3540100.zip` unpacked through `native/sqlite_source.py`, compiled portable `build-linux/publish_references`, bundled SQLite preload and `DOUJINSTYLE_TEST_PUBLISHER` pointing to the compiled worker:

```sh
LD_PRELOAD="$PWD/build-linux/libsqlite3.so" DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references" python3 -m unittest discover -v
LD_PRELOAD="$PWD/build-linux/libsqlite3.so" DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references" python3 -m unittest discover -s native -p 'test*.py' -v
python3 native/pack_search_worker.py --inventory
python3 native/source_attribution.py
python3 native/toolchain_doctor.py --mode cross --output build-linux/toolchain-audit-v0.0.62.json
```

For fully reproducible compiler commands inspect the evidence `commands.txt`; binary and SDK fingerprints must be captured on Windows for an eventual installable build. The `SOURCE-ATTRIBUTION.json` digest is source-only; not a cryptographic signature, build attestation, or Windows executable SHA.
