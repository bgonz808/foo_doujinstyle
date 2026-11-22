# Python → C++ runtime migration ledger — v0.0.64 source candidate

**No Windows artifact is qualified by this source-only candidate.** The production goal is
zero embedded Python, not merely renaming or annotating its active lines.

## Count definitions

`python_lines` is the physical Python line count in the installed zipapp, including
blank lines and imports. `deprecated_active_lines` is the number of **still-executable
Python source lines** in top-level functions marked `@runtime_deprecated(reason)`;
it **does not represent lines retired, nor does it count toward reduced package size**.
`fallback_python_lines` counts retained fallback functions with reasons. Source-only
`@standalone_only` definitions are excluded from the zipapp and kept in the repository
for fixture parity. The packer rejects references to removed direct symbols. The
native-only catalog methods are guarded and stripped of their Python compatibility arm.

Run `python native/pack_search_worker.py --inventory` to get the exact inventory.
Run `test_reference_publisher` with the built native helper and SQLite >=3.54.1
before treating any deleted Python route as qualified.

## New native path

`publish_many` now holds a single C++ `PublicationLock` for the release batch,
invokes native descriptor projection, stable filename selection and SQLite publication,
then returns `(release_id, relative_name, changed)` notifications only after completion.
The installed `native.search_worker.export` adapter sends one IPC request per batch;
the source reference implementation remains in `native/export_library.py` for parity.
`native/export_library.py` is no longer embedded. Its former Python publication lock
is marked `@standalone_only` in the source catalog class.

Regression checks include first publication, untouched repeat, missing releases,
relocated existing `.djt`, invalid IDs, oversized batch, compiled-worker launch,
source-versus-native byte-for-byte reference parity and failed-native fail closed.

## Still-active deprecated paths (NOT retired)

These annotations are a migration inventory and DO NOT strip runtime logic:

- `doujin.inspect_zip`: requires equivalent native MediaFire and MEGA ZIP inspection,
  including encrypted MEGA ranges, ZIP metadata, notes, artwork and all error paths.
- `native.search_worker.run_search`: job dispatch, result delivery and cancellation.
- `native.search_worker.run_popular`: chart result delivery and indexing.
- `native.search_worker.schedule_index`: offer discovery, worker-pool concurrency,
  archive probes, cancellation and commit-before-notify.
- `native.search_worker.run_idle_layers`: details, links, header/audio probes and browse.
- `native.search_worker.download_archive`: MediaFire and MEGA archive saving.
- `native.search_worker.repair_release`: established playlist paths and refreshed offers.

Do not mark these source-only until the installed replacement passes all their tests.
In particular, dropping `native/mega_download.py` or `native/archive_zip.py`
would currently regress installed workflows.

## Verification status

Portable compiler: Clang C++20 `-Wall -Wextra -Werror`.
Full Python-driven worker suite: 121 tests with 2 Windows-only WinHTTP fixture skips.
ASan/UBSan: same suite; leak detection disabled in the runner.
Clang Static Analyzer on full worker: timed out (NOT a pass).
Windows x86/x64 build, SDK clang-tidy and foobar2000 host: NOT RUN in this runner.

Do not claim an installable `.fb2k-component` until those gates are satisfied.

## v0.0.56 source candidate — native cross-provider ZIP inventory projection

`zip_inventory` is now a C++ IPC operation in `publish_references.cpp` using
`zip_inventory.hpp`. It classifies and bounds up to 32,768 ZIP members,
returns ordered audio and artwork records, sidecar candidates, root names,
unknown suffix counts, Unicode-name warnings, and positional indices into
exactly the directory snapshot used by playback. The same projection works
for native MediaFire, native ZIP-directory reads, and MEGA's existing ranged
ZIP compatibility reader. It performs no remote requests or disk writes.

`doujin.member_entry` is `@standalone_only`: omitted from the installed
worker, retained for `native.zip_inventory_reference` parity tests. The
source-only oracle is not in `pack_search_worker.SOURCES`; installed workers
fail closed if no native ZIP inventory is available rather than reaching into
site-packages or silently reporting an indexed archive. The full archive
inspector (`doujin.inspect_zip`) and MEGA range/cryptography code remain
active Python and must not be counted as retired.

The source-only reference provides the same projection for Python-only
unit tests, while `native.test_zip_inventory_job` exercises real IPC and
seeded member lists. The portable native tests include rejected negative
sizes, oversized names and invalid frames. No claim is made that moving
this projection alone improves first-audio latency: the intermediate IPC
transfer has not been benchmarked and full native inspection is next.

**Next deletions:** move provider-independent range/ZIP inspection and its
streaming DEFLATE support into C++, then complete native MEGA public-file
AES-CTR/MAC verification and archive repair/download scheduling. Keep the
Python provider modules installed until full-prefix, out-of-order range,
malformed ZIP, encrypted link, cancellation, and committed-catalog parity
succeed. Windows clang-tidy/x86/x64 SDK builds and host playback remain
release gates before installing this candidate.

## v0.0.57 source candidate — native MEGA archive inspection and authenticated downloads

Native `mega_public.hpp`, `mega_cng.hpp`, `mega_mac.hpp`, `mega_transfer.hpp`,
`mega_filename.hpp` and `mega_download_job.hpp` separate provider-specific
MEGA link/API validation, CNG AES, whole-file MAC, streaming and filename
rules. Both input playback and the catalog worker now reuse the SAME CNG
AES-CTR code. The catalog worker reuses `CatalogZipRange` and its existing
DEFLATE inflater for both MediaFire and MEGA; neither introduces another ZIP
parser. `mega_zip_open`, `mega_zip_probes` and `mega_zip_member` enforce the
provider of the active archive. The job makes bounded reads of the remote
central directory and audio prefixes and classifies non-ZIP signatures.

`mega_download_archive` streams a public MEGA file into an exclusive temporary
file and authenticates the complete plaintext using the MEGA chunk/file MAC
before publishing via no-overwrite rename. It has explicit bounds and rejects
unsafe storage endpoints, malformed API JSON, invalid/unrecognized archive
headers, range truncation and length mismatches. Partial random-access MEGA
inspection CANNOT authenticate the complete file MAC; reports identify this
as `mega_mac=not_verified_for_range_probes`. Never interpret a ZIP CRC32 check
as a cryptographic authenticity guarantee.

`native/mega_download.py` is now @standalone_only reference code and excluded
from the installed zipapp. The installed `archive_providers` module selects
native-only MEGA; source-only tools retain the Python reference for parity.
The installed worker fails closed if the native helper cannot inspect MEGA.
Do not count this as production-qualified until Windows x86/x64 SDK builds,
strict clang-tidy, a real WinHTTP/CNG MEGA fixture, and foobar2000 playback
have passed. The 0.0.57 source package is NOT a component installation package.

The two direct crypto integration fixtures cover irregular-offset AES-CTR
and full-file MAC against a Python cryptography reference vector. The whole-
archive fixture decrypts a real generated DEFLATE ZIP, resolves its central
directory, inflates the member and rejects altered ciphertext. Native routing,
source-only import exclusion, download dispatch, and native reporting are
covered by `native/test_native_mega_path.py`.

Python inventory v0.0.56: 2,843 physical lines in installed worker.
Python inventory v0.0.57: 2,668 physical lines (net -175).
`native/mega_download.py` is no longer included in SOURCES. Net reduction is
smaller because dispatch, fail-closed behavior and audit metadata were added.
The Python `inspect_zip` and foreground/idle job coordinator remain ACTIVE;
do not count them as retired. Keep `native/archive_zip.py` until all supported
archive paths have parity or are explicitly replaced, not merely annotated.

## v0.0.58 source candidate — shared native MediaFire/MEGA download job

A single `archive_download_job` IPC operation now reads a current archive offer
from SQLite (without holding a transaction during network I/O), dispatches to
native MediaFire or MEGA, and returns a completed file with explicit integrity
semantics. The same C++ `archive_job_plan.hpp` is used for metadata-repair
candidate selection, including excluded/failed URLs. Provider decisions reject
unsupported hosts, stale links, malformed offer records, and invalid release
IDs. Missing offers are still discovered by the Python coordinator using the
existing native release POST; a lost/uncertain POST result is NOT replayed.

MediaFire uses the existing WinHTTP and validated HTTPS share resolution;
full files are streamed in bounded chunks through the SAME exclusive temporary
archive writer as MEGA. `mediafire_download_policy.hpp` validates the file
signature, advertised length, and 16 GiB transfer bound. A completed file is
renamed without overwriting a user's existing download. Partial files are
removed on error. The source's old Python `compatibility_download_archive`,
`save_archive`, and filename helpers are `@standalone_only` and excluded from
the installed worker. No Python streaming fallback remains in the package.

Both provider downloaders compute a whole-file SHA-256 for future verified
mirror joining. The digest is informational until it has been independently
matched with another copy; **MediaFire has no whole-file authentication**.
MEGA retains its separate mandatory whole-file MAC validation before publish.

Portable native tests: `test_archive_job_plan.cpp`,
`test_mediafire_download_policy.cpp`, and `test_archive_digest.cpp`; all pass
warnings-as-errors, ASan/UBSan and targeted Clang Static Analyzer. The real
compiled-worker/SQLite IPC fixture checks selection, exclusions, invalid
links and missing releases. Python mocks verify no fallback HTTP streaming,
missing-offer discovery exactly once and no POST replay on uncertain failure.

The **general Search/Popular/Idle scheduling, cancellation, progress delivery,
archive inspection and repair execution are still active Python**; do not
claim those jobs retired. This source candidate is NOT installable until
Windows SDK/foobar2000 builds, clang-tidy, live WinHTTP/CNG and host integration
pass. A portable Linux worker build does not qualify the Windows-only branch.

## v0.0.59 source candidate — native foreground/idle eligibility snapshots

This is a **source-only candidate**, NOT a Windows-qualified component.

- The installed foreground `schedule_index` requests `catalog_index_plan` from
  the compiled C++ worker. C++ reads current releases, retry-due offers,
  incomplete ZIP probes, current copy membership, and provider URL eligibility
  in a bounded SQLite selection. It validates release IDs, filters unsupported
  or malformed destinations, and does no HTTP work while reading the database.
- Offers are saved before the next native archive plan is requested. Replanning
  after each committed offer prevents newly discovered copies from disappearing
  behind a stale job list. No uncertain POST is replayed by the planner.
- Idle Details, Links, and ZIP/Covers selection now use `catalog_idle_plan`.
  Each stage is planned AFTER earlier mutations commit. The native worker
  checks probe completeness and cover eligibility before returning work.
- Python `CatalogCache.offer_jobs`, `CatalogCache.index_jobs`,
  `compatibility_schedule_index`, and `compatibility_run_idle_layers` are
  `@standalone_only` references, omitted from the zipapp. The installed worker
  fails closed if native planning is missing; no Python SQL selection remains.
- The active `schedule_index` and `run_idle_layers` execution loops are still
  `@runtime_deprecated`: Python still owns network job execution, foreground
  result/progress callbacks, Windows idle gating, browse checkpoints and
  cancellation. **Do not report these as retired.**

Regression tests: `native/test_catalog_index_plan.cpp` (SQLite selection,
malformed URLs/JSON, retry order and ID bounds),
`native/test_catalog_index_plan_ipc.py` (real worker IPC/SQLite, package
exclusion, committed replanning, idle layer correctness). The complete
portable native-backed integration suite also covers earlier playback,
ZIP, search, publisher, MEGA and MediaFire regressions.

Build gates NOT run: Windows x86/x64 SDK compile, strict Windows clang-tidy,
WinHTTP/CNG runtime, cancellation under foobar2000, host playback and seek.
The next migration is the actual native event-driven job execution and
cancellation path, not further decorator-only Python removal.

## v0.0.60 source candidate — native offer pool and browse checkpoint state machine

**Migrated installed work:**

- `catalog_offer_batch`: bounded C++ offer GET/one-time POST execution, with at most four native
  network tasks, one SQLite writer, completion-order commit-before-notify, no POST replay,
  and queue stoppage on an observed HTTP 429. Windows foreground Search, Repair, Idle Links,
  and explicit Download use this operation in groups of at most six offers. `all_forms`
  remains explicit for Download. Native identity keys preserve MediaFire file IDs, and
  catalog-number evidence reuses the descriptor's NFKC and catalog validator.
- `catalog_browse_step`: the C++ state machine owns oldest-first page traversal,
  checkpoint relocation and ordered page+checkpoint SQLite commits. The installed
  Python idle scheduler only waits for user idle, emits progress after each response,
  and preserves the existing robots.txt advisory.

**Python retirement:** `fetch_offer`, `save_offer`, `compatibility_native_plan_index`,
`compatibility_discover_offers`, and `compatibility_idle_backfill` are now marked
`@standalone_only` and excluded from the installed zipapp. The standalone source
retains them for differential tests; absence from the installed worker is asserted.
Remaining active Python includes foreground/idle result delivery, rate/idle gating,
ZIP inspection, archive repair, and the search/transport launcher. `@runtime_deprecated`
is not physical retirement.

**Safety and validation:** Source-only until the Windows SDK/foobar2000 SDK build,
strict Windows clang-tidy and WinHTTP/host tests run. Portable Clang uses
`-std=c++20 -Wall -Wextra -Werror`; the C++ boundary fixtures pass ASan/UBSan and
targeted static analysis. The compiled portable worker and actual SQLite IPC tests
cover current archive selection, page checkpoint commits, relocated checkpoints,
first-page navigation, POST timeouts, 429, cancellation before commit, malformed
remote evidence and fail-closed module imports. Live WinHTTP, delayed UI progress
and foobar2000 Stop remain Windows qualification gates.

## v0.0.61 native jobs continuation — source-only, 2026-11-21

`catalog_index_dispatch` moves foreground offer sequencing, bounded fetch,
commit ordering, event publication, 429 halting and archive re-planning into
C++. `site_search_job` now owns the entire multi-page native search and streams
only committed pages. The old Python multi-page reader is `@standalone_only`
and omitted from the installed bundle; `run_search` remains an explicitly
`@runtime_deprecated` **active projection/launch adapter**. The Python
`native_offer_batch`, `schedule_index`, `run_popular`, `repair_release` and
`run_idle_layers` are still active and must not be counted as removed.

The JSONL protocol now has bounded, opt-in events, nonreentrant single-request
transport, strict terminal frames, and post-commit date/status projection.
Stop-race, 429, malformed frames, lost terminal, callback failure and
cross-process SQLite commit visibility have regression tests. Details and
unqualified gates are in `VALIDATION-v0.0.61-native-jobs.md`,
`native/JOB_CONTRACTS.md`, and `native/TOOLCHAIN_RECOVERY.md`.

**No packaged Python LoC reduction this checkpoint:** 1,513 physical lines
(+68 vs. the 1,445-line upload baseline), 391 deprecated-active (+21).
The added IPC correctness scaffolding currently exceeds the eliminated
runtime page loop. Source-only and `@runtime_deprecated` annotations are
explicitly separate. This v0.0.61 checkpoint did not bump the compiled version, leaving an attribution
gap that is corrected in v0.0.62. A source candidate version is not a claim
of Windows binary or foobar2000 qualification.

## v0.0.62 native archive + uncertain POST ledger — source-only, 2026-11-21

The project/component version is now **0.0.62** in the native DLL source. This
is a candidate identity, **not** an assertion that a Windows DLL was compiled.
Every subsequent source candidate must increment the embedded version even when
Windows build/host qualification is unavailable. Windows and cross build tools
read that same value for the `.fb2k-component` package filename and now embed
`SOURCE-ATTRIBUTION.json`, a deterministic source-tree digest that is distinct
from the final compiled artifact SHA-256 and the external SDK/build environment.

The C++ `catalog_archive_dispatch` now handles copy validation, isolated native
fetch pools, commit-before-event, 429 halt, bounded events and idle rate pacing.
Python legacy archive probe/writer/per-copy loop/repair are marked
`@standalone_only` and excluded from the packaged worker; the compatible
source test oracle remains available. Idle detail/offer page projection, UI,
Python IPC launcher and chart presentation remain active. `post_attempt_ledger`
adds durable conservative pre-POST markers and prevents cross-process blind
retries. The previously available `site_release_offers` unjournaled native POST
path is disabled; batch offer POST is the sole journaled worker path.

Packaged runtime Python: **1,500 physical lines** (down 13 from v0.0.61's
1,513), **386 deprecated-active lines**, **0 installed Python fallbacks**.
These are AST-pruned zipapp measurements, not total repository Python. The
smaller decrease reflects new event protocol adapters replacing substantially
more legacy archive responsibility, not a near-zero Python runtime.

See `native/JOB_CONTRACTS.md`, `native/TOOLCHAIN_RECOVERY.md` and
`VALIDATION-v0.0.62.md` for validation and remaining qualification gates.


## v0.0.64 — native idle job frontend retirement (source-only, 2026-11-21)

**Shipped runtime Python: 1,375 physical lines**, versus 1,491 at the start of
this checkpoint (net -116) and 1,500 in the v0.0.62 release (net -125).
The installed zipapp no longer contains `run_idle_layers` or its Python
bootstrap path; the foobar2000 idle action extracts and executes the native
`publish_references.exe --idle-backfill` directly under the existing Windows
Job Object. The source-only Python function remains as an explicit differential
fixture and is **not** counted as shipped runtime.

The C++ idle frontend owns detail selection/fetch/commit, offer selection,
archive dispatch, artwork, reference publication, Bangumi signals, browse
continuation and Windows input-idle pacing. It reconciles post-commit detail
and archive event streams against terminal responses and rejects incomplete
work without emitting a false successful job completion. Native detail
selection has one owner (no redundant preselection race). The robots.txt
advisory and browse-setup progress messages are restored in C++.

Validation: direct C++ full-job fixture, SQLite cross-connection commit and
restart test, robots directive parser tests, ASan/UBSan on idle fixtures,
compiled portable worker and Python boundary/integration tests. See
`VALIDATION-v0.0.64.md` for the exact status. No Windows component binary is
claimed; SDK, CRT/sysroot and matching Clang tools were unavailable in this
workspace, despite successful historical Windows builds in earlier sessions.

## v0.0.65 Git baseline (unqualified source)

Removed the prior source-attribution manifest and stale analyzer reports from
source distribution. `VERSION` and deterministic `tools/build_identity.py`
provide Git SHA, dirty-tree snapshot and honest Git-free identity. The compiled
component advertises the numeric version while embedding the complete build
string. `publish_references --version` returns it as well. Windows and Linux
build paths use deterministic packaging. No Python runtime has been retired in
this repository-bootstrap-only change: packaged runtime baseline remains 1,375
lines, including 287 active-deprecated lines. Next priority is direct native
foreground search/chart and foobar2000 UI projection, without new Python
adapters; see `docs/DEVELOPMENT_PLAN.md`.
