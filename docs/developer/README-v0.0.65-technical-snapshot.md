# foo_doujinstyle — DoujinStyle integration for foobar2000

**v0.0.65 development baseline (WIP, not a qualified release).** The source
contains a Windows foobar2000 component, native C++ catalog/idle workers,
and a **still-active bundled Python foreground worker**. The next migration
stage will remove that foreground worker only after native parity tests.

Source version is maintained in `VERSION`. Build identity is generated from
Git and the source tree, with a deterministic Git-free fallback. The reported
component version stays numeric until foobar2000 prerelease comparison is
qualified; the full `-dev.N+gSHA[.dirty]` identity is embedded in its metadata.
The `native/publish_references` executable also supports `--version`.

**No Windows `.fb2k-component` has been qualified from this source candidate.**
Portable native tests are not substitutes for foobar2000 host integration.

For local Windows use of the remaining Python foreground worker, Python 3.10+
and `py -3 -m pip install --group runtime` are currently required. Runtime
Python is a documented migration target, not a permanent project dependency.
See `docs/DEVELOPMENT_PLAN.md` for concrete retirement gates and
`docs/THIRD_PARTY.md` for dependency licensing.

## Native component

Development component packages use `foo_doujinstyle-v<build-version>.fb2k-component`. `VERSION` supplies the numeric base; `tools/build_identity.py` generates the full build identity. Source commit and snapshot hashes are separate from the final DLL and component ZIP hashes.

The component under `native/` registers `.djt` references as a playable input. Export references from indexed ZIP copies, then add the output folder under **Preferences → Media Library → Music folders**. Indexing reads each local descriptor. Playback opens the corresponding URL or ZIP member and hands the bytes to foobar2000's installed decoder. This gives the descriptors ordinary Media Library entries and playlist identities. Technical fields in the watched library come from the observations available when the reference was exported.

Install the package under **Preferences → Components → Install**, restart, then open **Library → Search DoujinStyle…**. The foreground search dialog still runs a bundled Python worker through the Windows Python launcher. The idle-backfill path is already native C++ and no longer launches the Python dispatcher. Install its runtime dependencies once from the extracted source folder with `py -3 -m pip install --group runtime`. Set the SQLite catalog and published-track folder under **Preferences → Tools → DoujinStyle**. Defaults are the catalog in the foobar2000 profile and the DoujinStyle subfolder of Windows Music. Set the output to a folder watched by the Media Library.

The resizable search window uses foobar2000 dark-mode hooks and reads Default UI fonts, background, text, and selection colors. **Search**, **This Month**, and **All Time** open the corresponding results. **Back** and **Forward**, Alt+Left/Right, and mouse side buttons restore visited results and selection. **List** uses rows with thumbnails and **Album Art** uses a grid of covers. Ctrl+wheel, Ctrl+plus/minus and Windows zoom gestures adjust the cover size for the active view. Each view remembers its own size.

The query dropdown offers recent searches and prefix matches from cached DoujinStyle tags. Use `tag:` to see the popular cached tags or submit `tag: 東方` for a tag search. The component reads suggestions from committed catalog data using its bundled SQLite engine. Browse pages and search results save tags seen on listing cards. Release details refresh them from the release page.

A `.djt` is a small UTF-8 track descriptor holding the DoujinStyle listing URL, remote share URL, exact ZIP member path, decoder filename, and available tags and technical data. foobar2000 can index the descriptor and read its metadata without SQLite. Right-click a track and choose **Open DoujinStyle release**, or use `%doujinstyle url%` in title formatting. Older exported tracks can resolve the listing from their numeric release ID. Playback opens the remote bytes through the component and submits native decoder metadata to foobar2000 for the `.djt`. **File size** reports the descriptor size, and `$info(source_bytes)` reports the encoded audio member size.

Track artwork queries first ask foobar2000's audio-format extractor for embedded artwork in the exact audio member. Folder artwork is scoped to that member's directory, then its ancestors in the same ZIP. The archive matcher uses conventional names including `front`, `cover`, the filename stem, the album title and `folder`. It recognizes back, disc and artist images too. A foobar2000 front-cover fallback tries a near-square release thumbnail, then a near-square page cover within 768 KiB, then the site favicon. Cached site images work on the main thread. Native HTTPS requests run off the main thread. An ICO favicon is converted to PNG in memory. The archive matcher still applies its built-in embedded-first order. Custom patterns in **Preferences → Display → Album art** are handled by foobar2000 for ordinary local sidecars.

New tracks published by the component go to `YYYY/MM/<track>.djt` based on the descriptor's complete date, or to `undated/<track>.djt`. Existing paths are reused on refresh to preserve playlist identity even if the date changes. Existing flat descriptors keep their current paths.

**Preferences → Advanced → Tools → DoujinStyle → Catalog Number source order** accepts a comma-separated list of `track`, `release`, `lookup`, and `album`, in that default order. Omit a source to skip it, or enter `none` to suppress the mapped field. Track uses explicit `CATALOGNUMBER` or `CATALOG NUMBER`. Album uses agreeing explicit catalog numbers from the same archive folder. The selected source appears in `CATALOG NUMBER SOURCE`. Re-export older descriptors through the component to record these candidates. Comments keep their original value. A standalone event code such as `M3-44` also appears under `RELEASE EVENT`.

For playback failures, open **View → Console** before opening the track. Watch the share page, negotiated HTTP version, archive response timing, ZIP member mode, transfer totals, and `first decoded audio`. Archive totals count response-body bytes across the shared archive session, including duplicate reads. ZIP STORE members use direct byte slices. Ordinary DEFLATE members inflate on demand into shared temporary files using foobar2000's bundled zlib runtime. Backward seeks within inflated data read from disk. When the host runtime is unavailable, the host archive reader opens a virtual ZIP containing the selected member. Other ZIP layouts continue through the host reader.

Playback starts with one 65,557-byte ZIP suffix request, covering the ordinary footer and any central directory that fits before it. The reader fetches missing directory bytes when needed. Directory-only command-line inventory uses the same starting window when its budget is at least twice that size. Jobs that also probe member tags or covers start with 8 KiB to preserve room for those probes.

Under **Preferences → Advanced → Tools → DoujinStyle**, **Reuse recent MediaFire download links (experimental)** keeps the resolved URL, referring page and referrer policy in memory for five minutes after a successful opening. With the setting enabled, reopen a track after its previous archive session has closed. The next opening tries the recent URL in a fresh session and uses the normal suffix range to validate the response and parse a fresh directory. A rejected or changed response falls back to the share page. The console reports each outcome without printing the URL. Restarting foobar2000 clears the in-memory links.

The share request and first four archive ranges show per-hop timings, including time in send and receive-headers calls, time from headers to the first readable body, and total request time. On supported Windows releases, WinHTTP also reports proxy, DNS, connection, and TLS phases and connection reuse. Phase timings can overlap. Under **Preferences → Advanced → Tools → DoujinStyle**, enable **Trace all archive HTTP requests** for a longer capture. **Disable HTTP/3 for native requests** enables an A/B comparison on new WinHTTP sessions while keeping HTTP/2 and HTTP/1.x available. Restart foobar2000 between runs when comparing cold sessions. Keep the trace from `Opening track for playback` through `decode start to first audio`.

When the host zlib exports checkpoint support, ordinary inflation learns restart points at DEFLATE block boundaries, with a nominal gap of 2 MiB for typical tracks. If the archive supplies a strong ETag, checkpoint records are saved under the foobar2000 profile's `DoujinStyle/Seek` folder, keyed by archive revision and ZIP member identity. A seek looks up the closest usable point at or before the decoder's requested byte. It resumes into a separate scratch file and leaves the sequential prefix available to other readers. Without a strong ETag, checkpoints live in the active session. A seek beyond all learned points can still require substantial forward inflation. `restored DEFLATE checkpoint` identifies a resumed inflation in the console.

MediaFire share resolution and ZIP ranges use one WinHTTP session. The session permits HTTP/2 and HTTP/3 where supported. Archive range requests to MediaFire hosts permit HTTP/2 and HTTP/1.x to avoid a delay observed with HTTP/3 enabled. Each redirect hop gets the policy for its destination. The console shows the allowed and negotiated protocols separately. Native HTTP follows static links and applies document, link and redirect referrer policies where available, with `strict-origin-when-cross-origin` as the default. It uses its own client User-Agent. **Open release** hands off to the Windows default browser for full browser behavior. The console omits share URLs and tokens. Use `py .\native\smoke_server.py .\doujin-smoke` from the source folder for local direct, ZIP STORE, and ZIP DEFLATE fixtures. Leave the terminal running and open the generated descriptors in foobar2000.

Select rows with the mouse or keyboard, using Ctrl and Shift for multiple releases. Drag their published tracks into a foobar2000 playlist. Double-click a row or use **Open release** to view the listing in the browser. **Indexing activity** shows archive progress and **Result pages** controls the result cap. Live queries accept artist, title, convention tags such as `M3-58` and `C108`, and other tags such as `Artcore`. A publisher name can match as an artist or other displayed credit. The offline `cached` command can search the publisher field where the catalog has one.

Search shows each result page after writing it to SQLite. It processes the returned listings automatically in batches of up to six offer discoveries and six eligible MediaFire archive inspections. The initial ZIP directory read has a 2 MiB range-response allowance and up to 12 requests. A header pass attempts every readable audio member with a separate bounded read allowance for each member. The worker publishes available tracks as it goes and reports how many releases produced references. **Stop** leaves committed work in place. Run the same search to continue, reusing fresh result pages, offers and archive inventories. **Refresh cached pages** re-fetches search pages while respecting the archive and offer refresh schedules. Normal Media Library searches only inspect local entries.

ZIP directories supply exact compressed and uncompressed member sizes, filenames, CRCs, and artwork candidates. Bounded audio prefixes cover FLAC, PCM WAV, Ogg Opus/Vorbis and MP3. They can supply codec and encoding, sample rate, channels, bit depth, duration, tags and embedded artwork hints, depending on the format and header layout. Average file bitrate is derived only when a duration is available. In foobar2000, `$info(source_bytes)` gives the encoded audio member length and `$info(zip_member_compressed_bytes)` gives its ZIP-compressed length. Archive artwork paths, sizes and observed embedded artwork remain in SQLite. No separate artwork payload is requested. Precise values outside the probe budget and formats such as M4A can remain unknown.

```powershell
py .\native\export_library.py C:\Music\DoujinStyle --cache doujin.sqlite --ids 32584
py .\native\smoke_server.py C:\Music\DoujinSmoke
.\native\build.ps1 -SdkRoot C:\SDKs\foobar2000-SDK
```

The last command targets Win32, x64, and ARM64EC with Visual Studio 2022 Build Tools and the official SDK (the 2026-10-01 SDK is the cross-build baseline). Install the **MSVC ARM64 build tools** for the ARM64EC target. The package places x86 at the archive root, x64 under `x64/`, and ARM64EC under `arm64ec/`. Install it with **Preferences → Components → Install**.

For the Clang 23.1.5 qualification build, install MSBuild support for LLVM (clang-cl), the MSVC C++ build tools, and the serviced Windows SDK 26100 series. Pass the SDK folder version displayed by Visual Studio or under `Windows Kits\10\Include`. The SDK installer may show a separate servicing revision.

```powershell
.\native\build.ps1 -SdkRoot C:\SDKs\foobar2000-SDK -Platform Win32,x64 `
    -WindowsSdkVersion 10.0.26100.0 -LLVMInstallDir 'C:\Program Files\LLVM' -LLVMToolsVersion 23.1.5
```

Explicit toolkit selection rebuilds the component and its SDK projects. The package continues to contain only the component DLL for each selected architecture. The inflater uses the foobar2000 installation's loaded zlib when available.

For an x86 and x64 cross-build on Linux, extract the [official foobar2000 SDK](https://www.foobar2000.org/SDK), provision x86 and x64 MSVC and Windows SDK inputs with [xwin](https://github.com/Jake-Shadle/xwin), and pass the directories explicitly:

```sh
python native/build_cross.py --sdk /path/to/foobar2000-sdk --xwin /path/to/xwin-splat \
    --llvm-bin /path/to/llvm/bin --output /path/to/build
```

The script regenerates the remaining embedded foreground Python worker, compiles both architectures with Clang's MSVC ABI, and packages the linked DLLs. Add `--arch x64` for an x64-only package. Use Wine or Windows for a separate host test. Inspect the DLL machine type, imports, exports, and load configuration with `llvm-readobj`.

The v0.0.16 and v0.0.17 Linux release builds used Clang and LLD 23.1.6, xwin 0.10.0, the Windows SDK 10.0.26100 series, MSVC CRT headers 14.44.35220, and the official foobar2000 SDK dated 2026-10-01. The official SDK archive SHA-256 is `d4c55077336fae81bf8df0259b5b2748fa45ea84132c656ead93eb123cbcdc26`.

The component uses a range-backed file service and the bundled ZIP reader. Enable **Archive Reader** when installing foobar2000. The loopback fixture offers direct WAV, stored ZIP and deflated ZIP tracks, and logs transferred ranges. Adding its descriptor folder should require zero HTTP requests. Then play each track and seek to check remote access. The exporter currently picks one indexed usable ZIP copy per release and leaves unrelated files alone.

```powershell
py -3 -m pip install --group runtime
py doujin.py front --limit 8
py doujin.py search "dat file records" --by artist --limit 5
py doujin.py search C108 --by tags
py doujin.py search M3-58 --by tags --page 2
py doujin.py release 32584 --resolve
py doujin.py release 32584 --dojin-info https://www.dojin-music.info/cd/36991 --creator https://umsoundz.net/releases/UMSZ-002/
py doujin.py release 32584 --inspect --directory-only
py doujin.py zip "https://example.invalid/your-archive" --sample-flac
py -m unittest -v
```

The earlier `sample-results.json` upstream capture is intentionally **not** part of the public repository baseline. Recorded upstream fixtures require a separate sanitization and redistribution review before promotion. Output from standalone commands can include response-body bytes, HTTP status, and elapsed milliseconds. A header-only download redirect is an explicit action because DoujinStyle may count it as a download.

## Host census and incremental indexing

```powershell
py doujin.py --cache doujin.sqlite front
py doujin.py --cache doujin.sqlite search C108 --by tags
py doujin.py --cache doujin.sqlite census --limit 12
py doujin.py --cache doujin.sqlite release 31050 --resolve
py doujin.py --cache doujin.sqlite census --details --refresh --ids 31050
py doujin.py --cache doujin.sqlite add-copy 31050 "https://example.invalid/alternate.zip"
py doujin.py --cache doujin.sqlite index --host mediafire.com --limit 3
py doujin.py --cache doujin.sqlite cached "夜桜電光"
py doujin.py --cache doujin.sqlite status
py doujin.py --cache doujin.sqlite status --depth
```

`status --depth` opens an existing cache read-only and reports release counts, copy counts, and archive member counts separately. Coverage uses current copy links. Site dates are reported only where a release has a stored DoujinStyle Date Added. Recorded covers indicate that an archive report names a cached cover. SQLite allocation by table and index is available when the runtime includes `dbstat`.

Listing requests retain the full page even if the command displays fewer rows. Pages stay fresh for ten minutes, and release IDs deduplicate across pages. The census resolves the offers exposed by saved listing forms. With `--details`, it fetches the detail page to discover additional mirror buttons. `release --resolve` resolves the offers from one detail page, and `--offer-slot 1` selects Mirror #1 for inspection. Offer resolution stops at the DoujinStyle redirects and keeps share URLs for later. Successful offers and archive indexes are eligible for refresh after 30 days, and failures can be retried after one day. The `index` batch probes MediaFire and eligible direct archive links. `cached` searches saved release fields and archive members across all linked copies offline.

With `--cache`, a live DoujinStyle search publishes its parsed page and listing rows in one SQLite transaction. Existing detail fields, technical evidence and already discovered mirror slots survive a shallow search response. A fresh cached page is served without HTTP. Use `--refresh` to refresh it on demand.

A release can have multiple hosted copies. Plain MediaFire file-share aliases for the same file key share one destination. Other URLs match exactly, including opaque query parameters and fragments. A new URL for a DoujinStyle download slot makes its earlier association historical. The previous copy stays in the cache with its archive evidence. `add-copy` attaches a known alternate share directly. Destinations may be shared across multiple listing IDs, and each destination keeps its own member inventory, probe state, and available audio evidence. A later successful probe of the same destination updates its current index.

Existing SQLite databases migrate in place on first open. In `status`, `host_resolved` counts listings with a current DoujinStyle offer. `copy_links` counts unique listing-to-copy associations. `copies`, the host breakdown, and archive coverage count distinct destinations. The same destination referenced by multiple listings is indexed once per refresh cycle in a single indexing run.

The live host census selected 12 distinct listings from the front page, two event searches, and an older listing page. It found 11 MediaFire redirects and one Jottacloud redirect. The 12 resolved offers were drawn from 79 unique saved listings. DoujinStyle also recommends MEGA in its submission form.

## Full catalog import and recovery

The observed music browse view on 2026-11-16 linked to page 1501. The sampled regular pages had 20 music cards and the last page had three, which implies 30,003 public music listings. This is a pagination-derived count of listings. Music and game views are separate. DoujinStyle's [robots.txt](https://doujinstyle.com/robots.txt) disallows general automated crawlers, and this prototype provides an offline bulk import.

**Idle backfill** in the search window reports the site's robots policy as an advisory. After you start it, it waits for 30 seconds of Windows input inactivity and spaces listing requests at least 10 seconds apart. The worker uses the site's observed pagination links and sends an identifiable User-Agent and the applicable referrer. An HTTP rejection, rate limit, or unexpected page redirect stops the run. The worker commits an ordered-ID checkpoint with each page and uses ordered overlap to relocate that page on resume. Numbered pages can still drift during a long pass. **Stop**, closing the window, or starting a foreground search cancels the worker.

```powershell
py doujin.py --cache doujin.sqlite import-catalog .\saved-pages --html --baseline
py doujin.py --cache doujin.sqlite import-catalog .\new-pages --html
py doujin.py --cache doujin.sqlite import-catalog .\export.jsonl.gz --baseline
py doujin.py --cache doujin.sqlite backup .\doujin-2026-11-16.sqlite.gz
py doujin.py --cache restored.sqlite restore .\doujin-2026-11-16.sqlite.gz
py doujin.py --cache restored.sqlite status
```

An import accepts one saved music-listing HTML page or a directory of `.html` pages with `--html`. Without that flag, it accepts a UTF-8 JSON Lines export or gzip-compressed JSON Lines. Every record needs the numeric DoujinStyle music ID and album title. Optional fields include artist, album_artist, publisher, catalog_id, release_date, listed_at, updated_at, tags, technical, share_urls, tracks, and track_source_url. The import reads local files and upserts transactionally, with zero HTTP requests.

```json
{"id":32584,"album":"ORIENTAL RADIANCE Vol.2","artist":"Ultramarine Soundz","tags":["M3-58"],"tracks":[{"number":2,"title":"Yozakura Denko ~夜桜電光~","artist":"Altermis"}],"track_source_url":"https://umsoundz.net/releases/UMSZ-002/"}
```

`--baseline` adds historical IDs without creating activity. Incremental imports count only newly inserted listing IDs. Existing IDs, metadata edits, mirrors, and repeated observations don't increment the listing-activity counter. Use `--baseline` for historical gaps discovered after the first load. The backup preserves listing IDs, activity, mirrors, tracklists, and archive indexes through its snapshot point. Keep later imports or newer backups to recover work after that point. Restore checks SQLite consistency and writes to a new filename.

## Song lookup and metadata

```powershell
py doujin.py --cache doujin.sqlite release 32584 --creator https://umsoundz.net/releases/UMSZ-002/
py doujin.py --cache doujin.sqlite songs "夜桜電光"
```

DoujinStyle documents album, circle, and tag search. Tracklists can be imported with a source URL or cached from a matching creator page. The `songs` command searches stored tracklists, embedded FLAC title tags already sampled, and audio filename hints from ZIP manifests. Each result carries its source. The prototype's native component design is in [NATIVE.md](NATIVE.md).

## Catalog fields

The front page and search each fetch one HTML document and parse up to 20 music cards per page. Cards supply the release ID, album, displayed artist credit, tags, event IDs, and advertised format. DoujinStyle sends UTF-8 bytes with a conflicting HTML meta tag, so the parser uses UTF-8. The release detail can add the site's listing and update dates and any explicit fields. A five-edition seed supplies sourced dates for C108, C109, and M3-56 through M3-58. Other recognized event IDs keep their original tag and blank dates. A multi-day event window does not imply a specific release date.

The schema exposes album, artist, album artist, publisher, and catalog ID. Album artist and publisher remain null if the evidence supplies only a circle or site display credit. The release, event, and archive can supply separate dates and credits. Archive filenames, bracketed top-level directory names and explicit sidecar fields can supply catalog ID candidates. Individual ZIP member filenames are excluded from this inference. A single candidate is surfaced alongside source evidence, and conflicting candidates remain visible. The dedicated Ultramarine Soundz adapter promotes a catalog ID and release date when both title and circle match. It exposes the publishing circle, physical medium, and track-level artist credits from the creator page. The 同人音楽info adapter accepts a known CD page and extracts the circle, date, event, booth and linked creator page. It does not copy the embedded artist dictionary or session token into output.

With `--musicbrainz`, the prototype makes one release search for the catalog ID or the album and displayed artist and outputs candidates. Use `--catno` to set the search key yourself. A matching title or catalog number alone can represent multiple editions. Compare labels, event or release date, medium, track count, and tracklist before promoting a candidate. Discogs and VGMdb are follow-on sources. This prototype required no API keys. The MusicBrainz endpoint returned a non-JSON page during the live run.

## Archive inspection

`release --resolve` stops at the host redirect. When the URL includes an archive filename, it extracts the apparent filename and any catalog ID candidate. `release --inspect` requests additional evidence through the MediaFire adapter or a direct archive URL. `zip` accepts a direct HTTP(S) archive URL or a MediaFire share URL.

The indexer uses a budgeted suffix request and validates HTTP 206, Content-Range, payload length and object size. It uses a strong ETag or Last-Modified for subsequent conditional reads when available. ZIP parsing records each file's path, suffix, size, header offset, CRC, and compression and encryption flags. It counts unknown suffixes and flags nested archives without opening them. Audio candidates are classified by filename. If ZIP parsing fails, a bounded signature check can identify 7z, RAR or gzip, or flag a ZIP signature with an unavailable directory. Segmented archives, RAR/7z member listings and password entry are outside this prototype.

By default, inspection reads up to three small unencrypted NFO, CUE or explicitly named text sidecars. Individual notes are limited to 16 KiB. `--directory-only` skips sidecar reads. `--sample-flac` additionally samples one FLAC STREAMINFO header for sample rate, channels, bit depth and duration, with up to 32 KiB of following metadata for Vorbis comments. It records the technical header even if a subsequent metadata block exceeds the budget. Prefer archive tags and sidecar fields as evidence for the copy you inspected. A cue file's album artist can differ from the NFO's album artist.

The default archive-body budget is 256 KiB and 24 range requests. `--byte-budget` can reduce or extend the byte cap. A server that answers with HTTP 200 after a range request is reported and its body is skipped. Requests and bytes in the archive subsection cover the ZIP reader, while the top-level log also covers host resolution and other sources.

Independent cursors share cached spans. The Python prototype serializes range-cache access with a lock. A production Go service could share a validated `io.ReaderAt` across ZIP parsing and multiple `io.SectionReader` instances, while coalescing concurrent misses for the same span. Stored members can be read at arbitrary offsets. Deflated members need a separate inflation stream per reader or a shared temporary decompressed member when random access is required. This prototype only inspects archives and does not serve playable URLs.

## Observed results

- The live front page returned 20 cards in one 72,580-byte response. The artist search reported 91 results and the C108 tag search reported 45 results. Each made one request for its first page.
- ORIENTAL RADIANCE Vol.2 was listed on DoujinStyle on 2026-11-16. The creator's dedicated release page gives 2026-10-25 at M3-58, catalog UMSZ-002, 14 track credits and a pressed CD in a jewel case. The MediaFire redirect filename also contained UMSZ-002.
- A user-run trace reached the MediaFire file endpoint and received HTTP 206 for an 8 KiB suffix of the 490,887,533-byte ZIP, sufficient to list 27 files and 14 FLAC candidates. Its MediaFire share page consumed another 335,012 body bytes. The execution environment used for development returned a share-page error, and the user run establishes partial reads for that file.
- The deterministic local 709,417-byte ZIP fixture took 8 KiB in one range request for the directory. The directory, two sidecars and a 96 kHz stereo 24-bit FLAC header took 16 KiB across two range requests. The same test server confirmed that the client skips the body of a range request answered with HTTP 200.

Sources for the sample release are the [DoujinStyle listing](https://doujinstyle.com/?p=page&type=1&id=32584), [同人音楽info](https://www.dojin-music.info/cd/36991), and [Ultramarine Soundz](https://umsoundz.net/releases/UMSZ-002/).


### Migration status (v0.0.52 source candidate)

The packaged worker no longer includes `audio_probe.py`; its native C++
replacement is exercised by the integration fixtures. On native startup
failure, the installed worker records missing header probes as incomplete so
that a later pass can retry. The Python audio parser remains in the source
archive solely as a differential-test oracle. Idle browse pagination parsing
now uses a C++ projection when the native worker is available; the lxml path
is explicitly marked as a recovery fallback. See `NATIVE.md` for exact counts,
verification, and Windows qualification requirements.


### v0.0.54 source candidate (not installable)

The native C++ catalog helper now resolves all ZIP filename encodings, and
performs cross-process chart refresh/snapshot coordination. The packaged worker
no longer contains Python `zip_names.py` or the old chart refresh coordinator.
Portable native-backed and sanitizer regression tests passed, but the pinned
Windows SDK/foobar2000 build gates and WinHTTP/host smoke checks were unavailable
in this runner. Do not attempt to install the source ZIP as a component.
See [NATIVE.md](NATIVE.md) for migration details and test commands.
