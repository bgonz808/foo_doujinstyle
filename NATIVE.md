# Native foobar2000 integration

`foo_doujinstyle` is a C++ input component with Win32, x64, and ARM64EC build targets. It registers `.djt` files as playable track references. **Library → Search DoujinStyle…** starts an embedded Python worker for the user's query. The worker saves each page, then schedules up to three offer lookups and ZIP probes at a time. A resolved offer can begin probing as soon as a slot opens. SQLite commits and track publication run on the coordinator. [Official foobar2000 SDK](https://www.foobar2000.org/SDK)

**Index status** has its own view in the search window. It shows saved coverage and controls which layers are enabled for the next idle run. Browse pages is enabled by default. Once it reaches the newest page, select Release details, Download links, ZIP directories, and Track metadata for the next audio enrichment pass. Each selected enrichment layer works on a small batch of eligible catalog entries. Deep layers can reuse completed earlier layers from a previous run. A ZIP header pass attempts every readable audio member using bounded reads per member.

Index status and tag suggestions use read-only connections to the bundled SQLite 3.54.1 engine on separate native threads. Each status report sees committed data from one snapshot and displays the SQLite journal mode. Since v0.0.41, the idle worker sends a refresh signal at the start and end of its layer pass, every ten saved browse pages, and after browsing completes. The dialog reads the report with the native status reader while the status view is visible.

**Preferences → Tools → DoujinStyle** has a shortcut to **Index status**, path pickers, folder shortcuts, and **Look up release metadata in Bangumi**. The external lookup is off by default. When enabled it can be queued by search, charts, archive indexing, existing and new playlist items, and the first decoded audio. Playback proceeds while the background worker looks up release data. Repair and idle backfill with **Track metadata** selected also use the same catalog cache and per-release/provider claim. A unique music match needs matching title and artist plus a compatible event, catalog number, or release date. New evidence can make an earlier miss eligible again. Turning the option off stops new lookups. Accepted evidence already in the catalog stays available.

Search results offer **List** with thumbnail rows and **Album Art** cover tiles. Sort by site order, title, artist, date, or track readiness and filter the loaded releases by exact site tag, advertised format, or published track availability. Rows awaiting metadata appear below confirmed matches while indexing runs. Selection and navigation use release IDs. Album Art fits five columns and two rows where the viewport allows at default zoom. Ctrl+0 returns to automatic tile sizing or the default List size.

**This Month** and **All Time** show saved rankings immediately. The dialog prefetches both while idle with one archive indexing slot. The monthly ranking is fresh for six hours and the all-time ranking for 24 hours. The site supplies both in one response, so a successful refresh renews their timestamps together. The snapshot and newly discovered releases are committed in one transaction. Both charts use the regular offer discovery, archive indexing, and reference publication pipeline. A failed refresh leaves the previous rankings available. The dialog caches the site favicon and release thumbnails locally.

Right-click a release and choose **Download archive to Downloads** to save its archive to the current Windows user's Downloads folder. MediaFire and public single-file MEGA links supply byte ranges to the same ZIP inspection and native playback code. The MEGA adapter translates offsets to storage requests and decrypts locally with Windows CNG. Its complete downloads also verify the file MAC before saving. Sparse reads cannot verify the whole-file MAC. Outside Windows the Python worker can use the `cryptography` package. **Open release** is available for unsupported hosts and public MEGA folders.

Search and idle track indexing retry old ZIP headers whose saved member list is incomplete and reload changed references in place. Right-click a release and choose **Repair track metadata** to repeat its header pass on demand. On a MediaFire share-page 403, repair refreshes the DoujinStyle offers and tries a current alternative if available. Re-export uses agreeing embedded album artists and catalog numbers as fallbacks for missing track tags.

The playback input saves learned decoder tags and audio properties in the profile, keyed by the track reference contents. It submits a forced media database update after audio starts. Playback delegates the member's buffering to foobar2000's SDK pre-decoder helper. The archive transport requests stored member bytes on demand. The DEFLATE input window has a 64 KiB minimum and grows only when the current decoder request requires more forward inflation. ZIP directory and local-header reads are archive lookups. A cold open reads a ZIP suffix of up to 65,557 bytes. The temporary inflation cache stores traversed audio bytes for seeks. An uncached distant DEFLATE read logs the destination and current inflate position. For a native DEFLATE MP3 member larger than 5 MiB, playback startup limits decoder reads to the first 4 MiB of member output, 4 MiB of aggregate logical reads and 5 MiB of compressed input per attempt. A seekable attempt that exceeds its budget retries once with a sequential, unknown-length source and a fresh budget. The sequential decoder exposes no seek controls. The budget is lifted at first audio for the active playback decoder. The host's buffering helper wraps the source after first audio. The guarded MP3 path uses catalog metadata for display and avoids a second decoder metadata call just after startup. Other decode opens, such as a waveform scan, follow the regular path.

The wrapper forwards native decoder live-info events. For a legacy decoder, the foobar2000 2.25 poll converts the decoder's dynamic technical changes into a live-info event using the decoder's original timestamp. The wrapper samples the child legacy callback once per decoded chunk when the host calls both interfaces. A codec that publishes no dynamic bitrate keeps its static file average.

Embedded genres take precedence. Recognized listing genres fill missing `GENRE` and are also published under `RELEASE GENRE`. Event IDs, site tags, and the listing's date added go in `RELEASE EVENT`, `DOUJINSTYLE TAG`, and `DOUJINSTYLE DATE ADDED`. `DATE` uses a complete `YYYY-MM-DD` release date first, then a verified single-day event date, then the site date added. `DATE SOURCE` records the choice. A bare embedded year can appear as `RELEASE YEAR` and blocks fallbacks with a conflicting year. Publisher aliases fill `LABEL`. The configurable Catalog Number order selects from explicit track tags, release details, lookup metadata and agreeing explicit catalog tags in the track's archive folder. Comments are preserved and standalone event codes are added to `RELEASE EVENT`.

## Runtime dependencies

The component package contains the architecture-specific DLLs and the third-party JSON and utf8proc licenses. Each DLL includes the foobar2000 SDK glue, SQLite, and the embedded Python worker with its matching native catalog and reference writer. The Python worker installs the native executable in the foobar2000 profile under a content-keyed filename. The executable uses the same Microsoft C++ runtime family as the component. Its Windows startup handshake goes directly to the output pipe before request handling. The component uses the host shared library, Microsoft's C++ runtime and UCRT, Windows UI, shell, WinHTTP, CNG, and Windows Imaging Component. The DEFLATE path uses the host zlib DLL and can fall back to the host archive reader. Audio is decoded by the installed foobar2000 inputs.

The installed coordinator still launches through Windows Python 3 and imports lxml for the compatibility paths. With the native worker available, interactive search and popularity refresh use native WinHTTP, native HTML parsing and direct SQLite page commits. Search honors the ten-minute page TTL, and chart refresh still uses the cross-worker lock and the existing six-hour and 24-hour freshness rules. The native worker keeps raw search and chart HTML inside the process. In v0.0.40, `site_release_offers` opens a release-scoped WinHTTP session, fetches the page, selects the forms, and builds the offer records. Each POST shares the GET cookies. The worker validates the form action and records the returned Location without following it. Indexing tries the primary and processes the remaining forms if the primary fails or cannot be inspected. Explicit download discovery requests all forms. The worker closes the session before returning the results. MediaFire range I/O uses separate native sessions.

The coordinator normally reads a committed snapshot through a read-only SQLite connection. The native writer creates and migrates the schema and commits page snapshots, checkpoints, observations, offers, archive observations, and lookup leases. The recovery writer can take over when the native helper fails at startup and the installed runtime safely supports the catalog journal mode. Saved chart rankings remain available even when the recovery writer is unavailable. MEGA uses Windows CNG. A non-Windows worker can use the optional cryptography package for MEGA.

Move the coordinator entry point to the native worker. The cutover includes foreground search and charts, idle scheduling, archive repair and downloads, optional release lookup, and the catalog side of MEGA. Verify search, charts, indexing, repair, download and publication on a Windows profile without Python or lxml. The standalone CLI and fixture tooling can stay in source.

## Python worker migration

`@standalone_only` keeps a source function available to the standalone CLI or tests and excludes it from the installed ZIP app. The packer checks direct references to excluded functions and methods. `@worker_fallback("reason")` keeps a fallback in the ZIP app and records why it is present. Both annotations are stripped from packaged Python.

```sh
python3 native/pack_search_worker.py --inventory
python3 native/trace_worker.py --report calls.json --line-report lines.json \
  worker.pyz --cache catalog.sqlite --output tracks --query 'A cached query'
```

The inventory measures physical lines in the ZIP app. `fallback_python_lines` counts the retained spans of functions annotated with `@worker_fallback`. `unmarked_python_lines` counts the rest, including common code and branches in shared dispatchers. The optional line report measures distinct executed lines by ZIP member for one fixture. Line numbers refer to the packaged copy. Run matching fixtures with the native helper and with startup recovery forced, then compare outputs. A path with no observed calls can still serve idle indexing, a different host, or a startup failure. Remove a superseded worker route once its callers and compatibility cases are covered. Use a clean Windows profile for the final Python-free runtime check.

In the same change as a migration, delete Python whose runtime callers are gone, mark code still reached by recovery with `@worker_fallback`, and mark standalone code with `@standalone_only`. For a fallback inside a shared dispatcher, identify the branch and its remaining caller in the migration notes. Compare `--inventory` before and after the change and update the packaged-worker test to check the affected functions. Report the shipped line count alongside the paths retired or still reachable.

v0.0.40 removes `release_offer_session` and the `site_offer_post` and `site_offer_end` requests. `fetch_python_offers` and `resolve_archive_offers` carry `@worker_fallback` for startup and pre-POST failures. `doujin.parse_detail`, `resolve_offer`, `resolve_offers`, `describe_offer` and `offer_source` retain the Python recovery and standalone paths. The fallback GET also serves details-only lookups through `release_details`. A worker reply lost after dispatch has an unknown POST outcome and is surfaced as an error. A later request can start a fresh helper. If a POST fails inside native discovery, the first error and any successful mirrors are returned together.

Python still runs the shared Unicode-normalized `catalog_hint` matcher on the filename and hands the result to `CatalogCache.put_offers`. The existing native SQLite writer saves the observation, links and error in one transaction. The coordinator keeps database commits outside transport jobs so ZIP probes can be scheduled from saved rows. Run the Windows loopback fixture and the foobar2000 smoke test before dropping Python offer recovery from the package.

v0.0.41 marks the old Python index status formatting, depth and storage queries, and tag suggestions as `@standalone_only`. The installed dialog now owns rendering of every status snapshot. SQLite compatibility helpers reachable only through the recovery writer carry `@worker_fallback`. Legacy ZIP member names can still require `reference_location` during native publication. The fallback marker belongs on the Python reference writer.

| Python measurement | v0.0.40 | v0.0.41 |
| --- | ---: | ---: |
| Physical lines shipped | 4,242 | 4,060 |
| Lines in marked fallback functions | 203 | 304 |
| Physical lines outside marked fallback functions | 4,039 | 3,756 |
| Executed lines in the offline idle fixture with the native writer | 769 | 626 |
| Executed lines in the same fixture with startup recovery | 775 | 632 |

The idle fixture runs the packaged details layer against an already dated release, so it reaches both status refreshes without a network request. The trace counts lines executed in that fixture.

v0.0.42 reads the release, selected archive and ordered tracklists for publication in one native SQLite snapshot. The Python snapshot reader carries `@worker_fallback`. The installed worker uses the component's existing background Bangumi queue. The superseded inline `--join-bangumi` route has been removed. The ZIP app ships 4,049 physical Python lines, with 323 in marked fallback functions.

v0.0.45 uses one native publication request per release for snapshot selection, descriptor bytes, stable filenames, and file publication. It preserves the SHA-256 filename scheme and searches for existing CP437, Unicode-extra-field, and normalized-slash spellings before selecting a filename. The Python snapshot, projection, and placement functions remain marked recovery paths. The original prebuilt-descriptor client is excluded from the installed ZIP. The archive carries 4,047 Python lines, with 508 in marked fallback functions. The matched two-track publication fixture executes 679 distinct Python lines, down from 839 in v0.0.44, with identical descriptor bytes and paths.

The helper uses statically linked utf8proc 2.9.0 for NFKC and case folding and Windows CNG for SHA-256. The vendored files are from the v2.9.0 GitHub source archive (SHA-256 `18c1626e9fc5a2e192311e36b3010bfc698078f692888940f1fa150547abb0c1`). The vendored C source backports upstream's decomposition pointer and length guards. It forms a destination pointer only when the buffer has room, including in the size-only pass tested under UBSan. The portable fixture build uses libcrypto for SHA-256. It compares descriptor bytes and paths for Unicode tag normalization, legacy names, moved references, release dates, metadata precedence, and STORED and DEFLATE ZIP members. Verify watched-library indexing, refresh in place, and playback from an existing playlist in Windows foobar2000 before removing publication recovery.

v0.0.46 moves foreground search page URL construction, release deduplication, removal of internal form fields, and the pagination stop condition into the helper. The worker commits each page before returning results. Python still dispatches one request per page so the dialog can show results before later pages complete, then hands the releases to the shared archive indexer. The Python page reader is marked recovery code and can take over during a failed page. The package ships 4,056 Python lines, with 521 in marked recovery functions. The portable parity fixture exercises Unicode query encoding, duplicate releases, commit before display, a failed later page and a warm-cache repeat.

v0.0.47 launches the native catalog executable directly from the foobar2000 background Bangumi queue. The embedded publisher is stored verbatim so both launchers can use the same bytes. The native job owns eligibility, bounded HTTPS and JSON parsing, match decisions, result and miss expiry, and the SQLite lease. Publication uses the existing catalog lock and native descriptor writer to refresh existing references in place. The idle tag layer requests eligible IDs from the native helper. The Python reference implementation stays in source for parity tests. The package drops `catalog_join.py`, the Python enrichment command, and their exclusively used helpers. It ships 3,841 Python lines, down from 4,056. The function inventory still marks 521 lines for recovery in the remaining workflows.

v0.0.48 uses a validated embedded `CATALOGID` as the Track catalog number when the canonical catalog fields are absent. The playback metadata cache, native descriptor writer, and archive evidence for Bangumi recognize the same code. A numeric identifier or event code in this field is ignored. The original decoder tag remains available alongside the selected `CATALOGNUMBER`. The Python publication recovery path uses the same precedence.

The playback trace formats elapsed time with up to three significant digits and chooses the unit from the measured duration. Archive session age and transfer counts start when the shared archive opens. Decoder setup and first-audio durations start with the current decode. Each open archive retains its validated ZIP tail, up to 65,557 bytes, separately from the evictable audio ranges. A fresh archive session still validates its own suffix range. Archives offered for the same release can have different bytes. Safe mirror grouping would attach URLs to a verified whole-archive digest and retain separate representations for different encodes or repacks.

Move the foreground search and chart dispatch and cancellation onto the native job runner next. Port idle scheduling, archive repair and downloads, and remaining MEGA catalog work through their existing entry points. Keep network requests and archive reads off the UI thread. Bound parallel work per host and cap in-memory results. Use one SQLite commit path, with independent committed-snapshot reads for the dialog. Propagate cancellation to network handles, archive reads and SQLite statements. Post asynchronous progress and reference updates back to the UI thread. A clean-profile run of every installed mode with Python absent verifies the final launcher and ZIP-app removal.

## Catalog storage

The archive catalog stores parsed ZIP member paths, methods, sizes, CRCs, and local-header offsets. The exported `.djt` carries the share URL, member name, sizes, and known track metadata. Playback verifies the member against the remote ZIP and shares the fetched spans while the archive session is active. Playing a track can signal optional Bangumi enrichment after first audio.

The native writer uses bundled SQLite 3.54.1 to enable WAL for local catalogs. Network volumes stay in rollback mode. The standalone Python CLI keeps its original SQLite version check when it writes a catalog. A standalone writer using an older SQLite can write a rollback-mode catalog and read a WAL catalog. Use the backup command to export a standalone snapshot with all committed changes. [SQLite WAL](https://sqlite.org/wal.html)

WAL permits readers alongside one active writer. Each ingest operation starts a native transaction after the worker has prepared the request and acknowledges it after commit. Reference publishers serialize file changes per catalog with an operating-system file lock. The native writer builds descriptor metadata from a committed snapshot, selects paths, atomically replaces files, and commits the placements. It recognizes a file already moved into a supported track shard and repairs its saved path. Cached archive bytes can be read while a range fetch is outstanding. Cache misses for the same archive share one transport and recheck the cache before starting another fetch.

## Catalog extension points

| Boundary | Current owner | Contract for a new implementation |
| --- | --- | --- |
| Host transport | `archive_providers.py`, `mediafire_http.hpp`, and the playback host dispatch in `component.cpp` | Resolve a supported URL to a bounded range source with a size and consistent object identity. Keep redirect, authentication, and decryption rules at the host boundary. Give each reader its own position and share validated bytes for one object revision. Exercise resolution, redirects, expiring links, malformed ranges, cancellation, and one live smoke test per host. |
| Archive structure | `archive_zip.py` and `zip_catalog.hpp` for indexing | Given the range source, enumerate raw member names, offsets, sizes, and integrity fields. Read a bounded member prefix or a bounded complete object such as a cover. Exercise truncated input, invalid spans, unsupported flags, and range use for both indexed and unindexed members. ZIP playback has an independent native reader in `descriptor.cpp`; qualify playback separately before advertising a new format there. |
| Remote release evidence | `catalog_join.py` and the per-provider lookup claims in SQLite | Use a provider identifier, a matching evidence fingerprint, and provider-specific refresh policy. Persist the selected match and its provenance. Give policy or provider revisions their own eligibility rules so that updated evidence can be projected into track metadata without reading the archive again. Exercise ambiguous matches, transient errors, cancellation of a lookup, and conflicting provider fields. |

On Windows, the MediaFire ZIP catalog worker keeps the HTTP snapshot, ZIP directory, member reads, DEFLATE inflation, and audio probe in one process. It uses foobar2000's installed zlib DLL from its full path. It sends the directory and batches of at most 32 structured observations to the coordinator. Audio prefixes stay inside the native worker. Prefix probes keep the 4 KiB output limit and existing per-member request and wire-byte budgets. Native probes cover FLAC, WAV, Ogg Opus and Vorbis, and MP3 including bounded ID3v2 text and artwork presence. Complete reads validate size and CRC. The coordinator still resolves UTF-8, CP437 and inferred CP932 archive names. Other providers, unavailable host zlib and unsupported ZIP layouts use the compatibility range and inflation path, with native audio parsing when the helper is available. A supported directory with an invalid local header yields a failed member probe. Archive job progress commits per copy.

Keep these acceptance tests separate as hosts and formats are added:

- A host fixture supplies the same archive bytes as an existing host fixture.
- An archive fixture uses the same range-source contract as ZIP.
- A metadata provider fixture operates from saved release evidence and uses a scripted provider response.

### Legacy ZIP filenames

The native directory reader validates version 1 Info-ZIP Unicode Path fields (`0x7075`) against the CRC of the original filename bytes. This detects stale or conflicting extra fields. It does not identify the encoding of unflagged raw names. The catalog prefers a valid UTF-8 flag, then a validated Unicode Path field. It infers CP932 only with positive Japanese filename evidence across the archive and otherwise uses CP437. Records keep a diagnostic for malformed or conflicting explicit evidence.

When decoded display bytes differ from the original name, eligible ZIP references include `MEMBER_LOCATOR` containing the raw filename bytes as hex, the local header offset, and the ZIP member data CRC. The decoder verifies those fields against the current central directory and validates the local header with the same native code used for indexing. The archive range reader continues to validate the HTTP object revision. The member CRC in this locator is separate from the filename CRC in the Unicode Path field.

Reindex an old release to correct its names, for example via **Repair track metadata**. The exporter checks for an existing descriptor under previous native and compatibility names and updates it in place. The automatic CP932 decision is heuristic. A legacy archive with sparse evidence can still need an explicit encoding choice. Valid UTF-8 that already contains mojibake needs independent evidence of the intended text.

## Compiler diagnostics and static analysis

Windows cross-builds compile the first-party DLL and catalog helper with `/W4 /WX` and link with fatal warnings. The MSBuild projects also use fatal compile and link warnings, at level 4 for ClangCL and level 3 for MSVC. The SDK headers are external and the SQLite amalgamation has its own warning settings. The cross-build retains CFG, DEP, and ASLR, and the MSBuild component enables CFG alongside the catalog helper.

Use clang-tidy from the same LLVM installation as the build compiler. The root `.clang-tidy` runs the Clang Static Analyzer and checks for dangling handles, use after move, suspicious semicolons, bad `sizeof` expressions, integer division in floating-point expressions, ineffective moves, and unnecessary copies. Analysis starts with first-party C++ translation units and reports diagnostics in first-party headers.

The Linux cross-build writes separate compilation databases for the DLL and worker in each architecture. Their argument arrays come from the same commands used to compile them. Strict analysis runs by default. Use `--analyze=report` to leave tidy findings advisory while keeping compiler and linker warnings fatal. The DLL analysis runs after the helper generates its embedded payload header and before the DLL compiles.

```sh
python3 native/build_cross.py --sdk "$sdk" --xwin "$windows" --llvm-bin "$llvm_bin" \
  --output build-cross --analyze=strict
python3 native/analyze_native.py --build-dir build-cross/x64 --clang-tidy "$llvm_bin/clang-tidy" --strict
python3 native/analyze_native.py --portable build-analysis \
  --clang-tidy "$llvm_bin/clang-tidy" --clangxx "$llvm_bin/clang++" --strict
```

The portable pass covers the catalog worker, descriptor and archive parsing, and portable test translation units without the Windows SDK. Use the architecture-specific databases to cover the Windows-only paths. Pair analysis with integration tests and a foobar2000 smoke test before publishing a Windows build.

The v0.0.35 cross-build uses LLVM 23.1.6 (including clang-cl, lld-link, and clang-tidy), Microsoft Windows SDK 10.0.26100 and MSVC CRT 14.44.35220 extracted with xwin 0.10.0, and the foobar2000 SDK dated 2026-10-01. Keep these development tools outside the component package. The same clang-tidy configuration covers Windows x86, Windows x64, and portable C++.

## Upstream input handling

Catalog HTTP responses use a 1 MiB HTML cap, with separate node and depth limits. The native DoujinStyle and MediaFire parsers advance to the next tag opener when markup contains another opening angle bracket inside a tag name. This avoids repeatedly scanning the same suffix of malformed HTML. Host and redirect validation, ZIP range bounds, archive integrity checks, and timeouts remain part of the transport and archive contracts above.

The catalog subprocess accepts newline-framed JSON from the coordinator. It caps each request at 32 MiB, limits JSON nesting to 64 levels, and rejects raw NUL bytes. An overlong frame is drained through its newline before the helper reports the error. The next request starts on a fresh frame. Portable fixtures exercise these limits and malformed upstream markup through the native entry points.

## Native catalog tests

The source includes the official SQLite 3.54.1 amalgamation ZIP and the nlohmann/json 3.12.0 header with its MIT license. Build setup checks the upstream SHA3-256 and SHA-256 respectively. The portable tests exercise the native catalog writer through the search worker and the reference exporter. [SQLite source releases](https://sqlite.org/download.html) · [JSON library release](https://github.com/nlohmann/json/releases/tag/v3.12.0)

```sh
work=$(mktemp -d)
python3 native/sqlite_source.py "$work"
cc -std=c11 -O2 -I"$work" -c native/sqlite_amalgamation.c -o "$work/sqlite.o"
cc -std=c11 -O2 -c native/third_party/utf8proc/utf8proc.c -o "$work/utf8proc.o"
c++ -std=c++20 -O2 -I"$work" native/publish_references.cpp native/descriptor.cpp "$work/sqlite.o" "$work/utf8proc.o" -lcrypto -pthread -ldl -o "$work/publisher"
c++ -std=c++20 -O2 native/test_descriptor.cpp native/descriptor.cpp -o "$work/input_fixture"
"$work/input_fixture"
DOUJINSTYLE_TEST_PUBLISHER="$work/publisher" DOUJINSTYLE_TEST_INPUT_FIXTURE="$work/input_fixture" python3 -m unittest
c++ -std=c++20 -O2 native/test_result_projection.cpp -o "$work/result_projection"
"$work/result_projection"
c++ -std=c++20 -O2 native/test_site_offers.cpp native/descriptor.cpp -o "$work/site_offers"
"$work/site_offers"
```

Build the Windows loopback fixture in Developer PowerShell. The fixture compares the WinHTTP and Python request bodies, cookies, referrer and Origin headers, and UTF-8 redirect handling. It also stalls the primary POST and checks that WinHTTP records the timeout and posts the mirror once.

```powershell
cl /std:c++20 /EHsc /utf-8 /DNOMINMAX native/test_site_offers.cpp native/descriptor.cpp /Fe:site_offers.exe /link winhttp.lib shlwapi.lib
$env:DOUJINSTYLE_TEST_SITE_SESSION = (Resolve-Path .\site_offers.exe).Path
py -m unittest test_site_offers
```

| Test surface | Evidence |
| --- | --- |
| Portable native worker and input fixture | Drive SQLite through the actual helper, publish a `.djt`, parse it with the input's descriptor code, and resolve the original STORED or DEFLATE ZIP member. This checks the catalog-to-input handoff, including a CP437 member with a raw locator. |
| Windows loopback fixture | Run the Windows executable directly on Windows. On a Linux runner with Wine, set `DOUJINSTYLE_TEST_WINE` to the Wine executable and `DOUJINSTYLE_TEST_SITE_SESSION` to the Windows fixture. Keep the loopback server and client on the same runner. |
| foobar2000 host | Install the component into a disposable profile. Start `native/smoke_server.py` and add its output as a watched Music Folder. Check metadata, first audio, forward and backward seek, track change, and Stop for direct WAV, ZIP STORE, and ZIP DEFLATE. Update a descriptor at its existing path and replay it from the same playlist after restarting. Compare the foobar2000 console with the fixture's HTTP trace. |

With album-art fallbacks enabled, also play an ordinary local file without embedded or external artwork. Request front and back art, then open a `.djt` with known artwork. The global DoujinStyle fallback supplies a valid empty extractor for unrelated tracks and unsupported art types. An empty extractor reports art unavailable and performs zero DoujinStyle lookups.

The host run can use Wine for an extra smoke pass. Native Windows covers the release checks for WinHTTP, CNG, library notifications and the foobar2000 UI. Keep fixture generation and trace tools on the developer side of the installed package.

### Remote seek regression

Start the loopback range fixture with a WAV large enough to learn checkpoints:

```sh
python3 native/smoke_server.py /path/to/fixture-tracks --seek-benchmark --range-delay-ms 30
```

Use a disposable foobar2000 profile. Open the console, play `deflated.zip.djt`, and seek to about 3:35 as soon as playback starts. Wait for the `seek to first decoded audio` line. Record its elapsed time, archive response bytes and range count. Stop and exit foobar2000. Start it again with the same profile and server, play the same track and seek to the same position. The second run should log a restored DEFLATE checkpoint near the requested member offset. Repeat with `stored.zip.djt` as a control. Seek backward to verify playback from an earlier byte position as well. Use an otherwise idle profile to keep the archive session counters free from concurrent scans. The timer ends at the first decoded chunk returned to the player. Output-device buffering is outside this interval.

New persisted points are nominally at least 4 MiB apart in member bytes. Active readers still learn the denser points in memory. Existing checkpoint files remain readable. The seek cache uses a 128 MiB profile-wide budget for checkpoint payloads. Loading a usable saved point for a seek refreshes its age for eviction. In-memory checkpoints are still available when disk writes fail.

v0.0.50 shares up to 256 resolved ZIP member locations per open archive. Playback and artwork share those locations, keyed by member name and any supplied raw member locator. Each archive request gets a fresh WinHTTP request handle, and the archive session retains up to eight origin handles keyed by host, port and scheme. In the archive HTTP trace, `origin handle reused` identifies reuse at the WinHTTP API level. Use `connection request index`, `first`, and TLS timing to tell whether the underlying connection actually stayed open. For an HTTP/1.x server that returns `Connection: close`, expect a new connection.

v0.0.51 exposes **Checkpoint spacing** in Advanced Preferences under Tools → DoujinStyle. It defaults to 4 MiB and rounds up to 2, 4, 8, 16, or 32 MiB. The member's stable slot grid can be wider for large members. A new member session applies the policy when it uses or writes the index. Increasing spacing removes intermediate saved slots for that member. Reducing spacing preserves those slots and learns missing points during playback. Existing records use the same on-disk format. With large enough members, a 32,802-byte record every 4 MiB costs about 8 KiB per MiB of inflated member data. Per packed archive MiB, multiply that by the inflated-to-packed ratio for eligible members and account for members with no checkpoints.

To fill gaps manually, select one remote ZIP DEFLATE track and use **Build DoujinStyle seek index**. The cancellable worker uses the same shared archive, inflater and checkpoint writer as playback. It checks the saved slots and starts from available checkpoints. The manual action accepts members up to 128 MiB packed and 256 MiB inflated, under the same strong-validator and disk budget rules as playback. Normal playback learns points only as it reads. Persisted decoder metadata can satisfy later metadata requests directly.

**Index status** reports current catalog copies by observed layout, with a distinct category for unobserved copies and for 7z/RAR with unknown solid status. The playback summary reports completed DEFLATE seeks that consulted and restored from the index, mean seek-to-audio with and without restores, compressed member bytes bypassed, and startup means for new and familiar references. Familiar means previously started this session or saved playback metadata exists for the descriptor. The playback counters are session-local. The console records per-operation times and archive ranges. A shared member's concurrent readers can contribute to its counters.

In a disposable foobar2000 profile, build the fixture member's index at 4 MiB. Restart with 16 MiB, then run the same command and compare the files under the profile's `DoujinStyle/Seek` directory. For the 40 MiB fixture, the existing 16 and 32 MiB slots survive. Restart at 4 MiB and repeat the command to backfill the missing slots. Check cancellation and seek again during regular playback. ZIP STORE is the control for the seek timings. For a catalog layout regression, run the native `test_catalog_tags.cpp` fixture with a new SQLite path and the `layout-fixture` argument.

After first audio, **Preferences → Advanced → Tools → DoujinStyle → Coalesce DEFLATE playback ranges** enables a 256 KiB compressed-input floor for forward sequential reads and a 2 MiB cap for large demands. Small reads after a seek still start at the 64 KiB floor. Compare identical seeks with the option on and off in fresh host runs, recording the seek console line with elapsed time, response bytes and request count. Check ZIP STORE as a control. For a fixed range, use the transport benchmark to verify connection reuse independently of range size:

```sh
python3 native/benchmark_seek_transport.py --delay-ms 30 --runs 3
```

The transport benchmark needs HTTP/2-enabled curl, Node.js and OpenSSL. It makes verified loopback TLS requests and reports the negotiated protocol, range count and actual new connections. It does not use WinHTTP.

The portable fixture exercises byte-exact restored seeks, damaged records, eviction, sparse writes and reuse of older checkpoints. Its loopback mode measures actual HTTP requests and the production inflater. It verifies a 16 KiB window against the original WAV. The measured interval starts after ZIP member lookup and excludes foobar2000 decoding, initial archive discovery and clock time spent learning checkpoints.

```sh
work=$(mktemp -d)
c++ -std=c++20 -O2 -Wall -Wextra -Werror -pthread \
  native/test_checkpoint_index.cpp -lz -o "$work/checkpoints"
"$work/checkpoints" "$work/checkpoint-tests"
"$work/checkpoints" "$work/memory-trials" --benchmark
c++ -std=c++20 -O2 -Wall -Wextra -Werror -pthread -DDOUJIN_SEEK_TEST_HTTP \
  native/test_checkpoint_index.cpp native/descriptor.cpp -lz -lcurl -o "$work/checkpoints-http"
"$work/checkpoints-http" "$work/http-trials" --http-benchmark 8765
```

## Remote library and transport

The worker registers host capabilities in `native/archive_providers.py`. The Windows range transport has a matching adapter table in `native/component.cpp`. Providers that expose an ordinary HTTPS bytestream reuse the HTTP range reader. Providers with a different wire format implement resolution and range translation, then hand ordinary ZIP bytes to the same archive code.

The idle ZIP-directories layer can send its already validated suffix to the native catalog worker. The worker uses the same central-directory walker as playback and returns member names, sizes, CRCs, flags, methods and local header offsets. The activity log identifies native directory results. The existing reader handles directories outside the suffix, ZIP64, legacy names, and jobs that also request audio headers or covers. The catalog coordinator, indexing transport, and descriptor metadata still run in Python.

Add the exported `.djt` folder as a foobar2000 Music Folder. Each track uses an ordinary file path. Updating its metadata or share URL at the same path preserves its playlist identity. The input returns local descriptor metadata for library scans and resolves the host when playback opens. The public SDK's old `library_manager::add_items` methods are obsolete. A `library_viewer` and Default UI element could also expose the entire catalog on demand, including listings that have yet to yield playable tracks. [Music folders](https://wiki.hydrogenaudio.org/index.php?title=Foobar2000:Preferences:Media_Library) · [SDK library manager header](https://raw.githubusercontent.com/reupen/foobar2000-sdk-unmodified/main/foobar2000/SDK/library_manager.h) · [Navidrome component design](https://github.com/santiagorod92/foo_navidrome)

Link a private SQLite build with the feature set the component needs. Use the SDK's media library, metadata, and file services for integration. The inspected public SDK headers provide no supported general SQLite connection to foobar2000's own catalog. The separate `foo_sqlite` component supplies SQL virtual tables and utilities, and is optional. Compressed SQLite snapshots are for transfer and recovery. Restore them to an ordinary live SQLite file for random reads and writes. [SQLite Utilities](https://www.foobar2000.org/components/view/foo_sqlite) · [SQLite Backup API](https://sqlite.org/backup.html)

The SDK HTTP service supports GET and POST, custom request headers, response status and headers, and cancellation with `abort_callback`. The public interface inspected does not expose a redirect policy setting. WinHTTP is suitable for the Windows host adapters where per-hop redirect control, cookie behavior, timeouts, and exact 206 validation matter. Encapsulate the transport. Keep network and SQLite work off the UI thread, and honor cancellation on every open and read path. [SDK HTTP header](https://raw.githubusercontent.com/reupen/foobar2000-sdk-unmodified/main/foobar2000/SDK/http_client.h) · [WinHTTP sessions](https://learn.microsoft.com/en-us/windows/win32/winhttp/winhttp-sessions-overview)

## Archive reader acceptance test

The bundled `foo_unpack` supports ZIP, RAR, 7z, and gzip. The standalone `foo_unpack_7z` is obsolete on current versions and documents the cost of solid archives. Public product documentation does not establish efficient remote ZIP or MediaFire playback. The current SDK offers a concrete test path. Pass a range-backed `file::ptr` into `archive::archive_list_` with member readers disabled. Test opening individual entries using a virtual archive path and `archive_impl::g_make_unpack_path`. [Archive Reader](https://wiki.hydrogenaudio.org/index.php?title=Foobar2000:Archive_Reader_(foo_unpack)) · [Official 7-Zip component notes](https://www.foobar2000.org/components/view/foo_unpack_7z) · [SDK archive header](https://raw.githubusercontent.com/reupen/foobar2000-sdk-unmodified/main/foobar2000/SDK/archive.h)

Use a controlled HTTP fixture and inspect the wire trace for ZIP listing, first audio, forward and backward seeks, two simultaneous track opens, cancellation, and server responses that ignore Range. Test ZIP STORE and DEFLATE separately, then 7z and RAR separately with solid and non-solid archives. If a stock reader exceeds the byte budget, use our own member reader under the same virtual-file contract. A client-supplied byte reader is an SDK capability. Individual archive components still determine how much they read and decompress.

The virtual file should implement `file::read`, position, size, `seek`, `can_seek`, and `reopen` accurately. Each open gets a cursor. Cursors share validated spans for one object revision. A ZIP STORE entry maps decoder offsets directly onto HTTP byte ranges. A DEFLATE entry needs incremental inflation and a growing, seekable temporary file, or a separately justified checkpoint implementation. A codec seek table gives positions in the inner file. Decoder offsets are meaningful only once the member reader can fulfill them. For decoder selection, keep a credible audio filename or content type and pass the reader via the SDK input helpers to the installed decoder. [SDK file interface](https://raw.githubusercontent.com/reupen/foobar2000-sdk-unmodified/main/foobar2000/SDK/file.h) · [SDK input helpers](https://raw.githubusercontent.com/reupen/foobar2000-sdk-unmodified/main/foobar2000/helpers/input_helpers.h)

## Metadata and seek policy

| Inner format | First reads | Deferred reads |
|---|---|---|
| FLAC | Signature and STREAMINFO for sample rate, duration, channels, and bit depth. Read bounded Vorbis comments. | SEEKTABLE if present, any large PICTURE block only for artwork, and decoder range reads as needed. |
| MP3 | ID3v2 if present, the first MPEG frame, and any Xing/Info or VBRI seek data. | Tail tags when relevant, and decoder probes for missing VBR seek data. |
| Ogg Opus/Vorbis | Identification and comment packets with stream serial and page boundaries. | Use relevant tail granule positions and sparse seeks. Handle chained streams and Opus pre-roll in the decoder. |
| M4A/MP4 | Top-level box lengths and the location of `moov`. | Read sample tables and metadata where `moov` appears, which can be at the end. Use the decoder's fragmented MP4 handling as needed. |

Keep archive directory metadata, catalog fields, measured audio headers, and decoded verification distinct. Give artwork an independent budget. Metadata readers should make a bounded number of reads and return a partial result when large art, padding, or a tail index exceeds it. An initial `get_info` call can use the saved catalog row. Explicit probes can update the technical fields and cached SDK metadata later. [FLAC RFC 9639](https://www.rfc-editor.org/rfc/rfc9639.html) · [Ogg Opus RFC 7845](https://www.rfc-editor.org/rfc/rfc7845.html) · [FFmpeg MOV/MP4 notes](https://www.ffmpeg.org/ffmpeg-formats.html)

## v0.0.52 migration candidate: audio probing and idle navigation

The bundled native catalog helper has owned `audio_prefix` parsing for FLAC,
MP3, Ogg, and PCM WAV for several releases. The source-only Python reference
parser (`audio_probe.py`, annotated `@standalone_only`) is now **excluded from
the installed zipapp**. Native metadata probe byte parity is covered by the
`test_reference_publisher.py` compiled-worker fixture, including malformed
prefixes. With the native helper unavailable, the installed worker explicitly
marks affected audio header probes incomplete for retry, rather than storing
fictitious success. Standalone source tests still exercise the Python reference. The installed zipapp also
refuses to import a same-named `audio_probe` module from system paths.

The idle browse navigation projection now calls the native bounded HTML parser
via `site_parse(kind="browse_links")` in normal Windows-worker operation. The
previous lxml implementation is decorated `@worker_fallback` and retained in
the package for transport/helper recovery. The native implementation only emits
observed first-party `p=home` page links inside `pageScroll`; it preserves
referrer-policy precedence. Both the C++ and Python parity fixtures exercise
link filtering, policy, and malicious off-origin links.

The shipped Python AST inventory is **3,637 physical lines in 12 files**, down
from 3,842 lines in 13 files in v0.0.51. Native fallback functions account for
537 lines, up from 522; the unmarked physical count is 3,100, down from 3,320.
The source-only Python prototype is intentionally unaffected. This migration
is not the final foreground Search, Charts, Idle, Repair, and Download cutover.

Portable verification: compile the native publisher against SQLite 3.54.1,
then set `DOUJINSTYLE_TEST_PUBLISHER=/path/to/publisher` and run `python3 -m unittest`.
The 2026-11-20 candidate ran 109 tests, with 3 skips, when
Python's sqlite3 library used the bundled SQLite 3.54.1 rather than the runner's
older SQLite 3.46.1. Eight native fixtures and the modified site-offer fixture
under ASan/UBSan passed; leak detection was disabled under the sandbox.
The Windows foobar2000 host and the WinHTTP-only fixtures still require a
Windows test run. No Windows component binary is claimed from this source-only
candidate, because this runner does not have the Windows SDK, foobar2000 SDK,
and the pinned clang-tidy executable available for the strict DLL build.

A branch-instrumented Python integration run reported **67% combined coverage**
of the full `doujin.py` and `native/idle_backfill.py` modules (not a claim of
100% migrated-path coverage). Native `test_site_offers` also passed Address-
and UndefinedBehaviorSanitizer and Clang Static Analyzer. The offer-parity test
normalizes wall-clock `first_seen`/`last_seen` seconds for independent writes
while asserting their freshness and matching all other catalog fields.

## v0.0.53 source-only migration candidate (not a release)

The shipped Python generator now removes the already-native SQLite **writer** and
`.djt` **descriptor projector** compatibility arms while retaining the Python
reference algorithms in source for differential tests. `@standalone_only` marks
code removed from the zipapp; `@native_worker_only` generates a native-only
method body with an explicit error when the native helper is absent. The AST
packer refuses a native-only method without a terminating return/throw and
still checks source-only name references. Native site parsing also replaces the
Python release/chart/offer and idle-listing parser on the installed path.

The installed worker no longer provides Python SQLite **write recovery** if the
native helper fails. Failed native startup must fail closed (search, downloads,
repair, idle) or display existing saved chart rankings read-only. This behavioral
change has tests. It must be qualified on Windows before distribution.

The native foreground search implementation additionally coalesces **fully warm**
searches into a single IPC request, with native pagination and duplicate
suppression. **Cold** searches still return page-by-page so results can appear
as soon as their database writes commit. Fixtures test late-page errors, cache
reuse, invalid input, and the absence of calls into Python HTML parsing.

**AST inventory:** v0.0.52 ships 3,637 physical Python lines; this candidate
ships 3,028, a **609-line** physical reduction. Of those, **521 lines**
were previously labeled fallback, and **88** were outside marked fallbacks.
This is below the requested 1,000-line
batch target. Do not treat it as a complete burndown or an installable build.
Remaining native migration work includes the MEGA transport, general archive
inspector, foreground/idle coordinator, and ZIP fallback. Their Python is still
installed because removing it now would break supported archive scenarios.

Portable native build and verification use the documented SQLite 3.54.1 source,
`-Wall -Wextra -Werror`, and `DOUJINSTYLE_TEST_PUBLISHER`. On hosts with an
older system SQLite, ensure the Python testing process also loads a patched
SQLite runtime. The pinned Windows SDK, foobar2000 SDK, clang-tidy, Clang
Static Analyzer x86/x64 checks, and Windows host/WinHTTP tests still must run
before producing a component package.

## v0.0.54 source candidate: native charts and archive-scoped ZIP filename resolution

This is a **source candidate, not an installable component** until Windows x86/x64
compiler/linker, strict clang-tidy, foobar2000 SDK, and host smoke tests pass.
It builds on the v0.0.53 source candidate; neither candidate supersedes an
installed, qualified release.

ZIP directory parsing now resolves filename evidence once per archive in C++:
validated UTF-8 flags, CRC-validated Info-ZIP Unicode extra fields, strict
CP932 decoding and archive-wide Japanese evidence, and CP437 fallback. All
members returned by `zip_catalog_members` and `mediafire_zip_open` include
resolved name, encoding, warning, and alternate. No installed Python module
`native/zip_names.py` remains. That module stays in source, with
`@standalone_only` decorators, for differential tests. Untrusted lengths,
flags, and encoding are bounded before decoding. A source-only fake native
ZIP fixture was updated to the 13-field result contract; both real and fake
native consumers now reject truncated result rows rather than guessing.

The C++ chart job now owns the durable snapshot fetch, refresh lease, HTML
parse, and atomic SQLite page/seed commit. New IPC operations:
`site_chart_snapshot` (read-only, no network) and `site_chart_refresh`
(non-blocking cross-process lock, recheck after lock, no SQLite transaction
held while fetching). The installed Python runner displays existing charts
before refresh and passes committed rows to the shared indexer. Source-only
Python implementations of the chart lock, refresh, and prior foreground/
prefetch coordinators are annotated `@standalone_only` and excluded from the
embedded worker. With no native helper, installed Popular retains **read-only
saved rankings**; a cold chart cannot fetch or index. The native worker is
required for all chart writes. No new foreground cancellation claim is made.

The packaging AST audit of the v0.0.53 candidate measured 3,028 shipped
Python lines in 12 modules. This candidate measures 2,965 lines in 11 modules:
**63 physical Python lines net removed**. The decrease is small because the
replacement native-chart presentation/IPC bridge still runs in Python. The
old chart coordinator and ZIP name parser are not shipped. This does not meet
the outstanding 1,000-line active-runtime migration target, nor is the
Python-free component ready.

Portable regression gates (SQLite 3.54.1 loaded into the test host):
- 109 Python integration/unit tests, one Windows-only skip, zero failures;
  repeated using the ASan/UBSan-instrumented native worker with leak detection
  disabled in this sandbox. Four dedicated native chart fixtures cover cache,
  commit-before-display, malformed refresh retention, and cross-process lock.
- Native ZIP-name Python-vs-C++ parity on eight archive sets plus 150 randomized
  sets. A separate C++ fixture covers UTF-8, CP932, CP437, bad Unicode evidence
  and archive-level inference. The deterministic C++ name fixture passed ASan/
  UBSan. Invalid flag and oversized input fixture checks reject bad IPC.
- The ZIP-name fixture reported 92.8% statement-line and 69.1% branch coverage
  within `zip_name_resolution.hpp`. This is **not** whole-component coverage.
- Portable C++ build used `-std=c++20 -Wall -Wextra -Werror` and Clang Static
  Analyzer on the worker and ZIP-name test. The native helper build had a full
  ASan/UBSan pass when compiled with `-O0 -g0`. The `-O1 -g` sanitizer build
  exceeded the sandbox timeout and is not counted as passing.

Windows-specific SDK compilation, clang-tidy, WinHTTP chart fetching, CP932
parity under Windows conversion APIs, and foobar2000 end-to-end playback remain
**unverified** here. Both old production paths have references in source; do
not delete their source implementations until Windows qualification and parity
are recorded.

The remaining major native migration is job orchestration: download, archive
repair, idle backfill/MEGA, plus the Python launcher. Moving those installed
entry points to C++, rather than adding more thin IPC wrappers, is necessary
for zero runtime Python.
