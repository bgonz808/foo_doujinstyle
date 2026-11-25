# Windows foobar2000 E2E — phase-zero host harness

This is a manual, offline Windows host smoke, **not** a green CI host qualification. Robot JUnit can be consumed by the existing native-quality reporter, but host code coverage remains unmeasured.

## Guest boundary and prerequisites

Run `e2e/host/runner.py` inside an isolated disposable Windows VM with a logged-in interactive desktop, not Session 0 or ordinary hosted Actions. The guest must have all network adapters down; loopback remains usable. Provision a publisher-sourced foobar2000 portable installation and its component, plus hash-verified Python test dependencies, using a trusted offline mechanism before starting the tests. Do not redistribute proprietary foobar2000 installers or execute arbitrary PRs on a privileged self-hosted runner.

The Python launcher checks adapter status and rejects any adapter reporting Up, but this is **not itself an isolation boundary**. Enforce deny-by-default egress independently in the hypervisor/vSwitch, blocking bridges, virtual adapters and newly activated interfaces. Review the boot-time process set; a clean guest is the canonical profile reset.

Ensure the portable installation contains `portable_mode_enabled` and its actual installed component file. Match both binary hashes, an inventory digest of the entire template, exact Python package versions and the clean source commit against a schema-1 JSON manifest, structured as:

```json
{
  "schema_version": 1,
  "project": "foo_doujinstyle",
  "source_commit": "<40-hex checked-out commit>",
  "foobar_version": "<exact publisher version>",
  "template_sha256": "<64-hex canonical directory-inventory digest>",
  "host_path": "foobar2000.exe",
  "host_sha256": "<64-hex installed host digest>",
  "component_path": "<verified relative path to installed component DLL>",
  "component_sha256": "<64-hex component digest>",
  "robotframework_version": "<exact installed version>",
  "flaui_version": "<exact installed version>"
}
```

A digest can be generated with `e2e.host.runner.template_digest(Path("C:/sandbox/foobar-portable"))`, which hashes sorted paths and file digests. It refuses symlinks/junctions. The manifest is test-run input (not a checked-in fake pin); its values must be obtained from the real inspected guest and binary artifacts. Supply it under an exclusive, non-writable template root.

```powershell
py -3 -m e2e.host.runner --manifest C:\sandbox\manifest.json `
  --template C:\sandbox\foobar-portable --output C:\sandbox\runs\smoke-001
```

Use a new output directory every time. The launcher checks source/host/dependency identities and desktop state; copies the portable installation; then runs a Robot Framework UIA3 smoke with actual window visibility assertion and screenshot. It records Robot XML, JUnit and a JSON checkpoint containing source and asset digests; **DLL, worker and E2E line coverage, audible output, native seek and timing remain null**.

## Planned but not yet qualified

1. Prove the initial selector /Window on a real Windows guest; replace with stable UIA selectors from inspected application state.
2. Inspect Preferences / Advanced and component dialogs to add distinct native Robot scenarios and deterministic catalog fixture states (#17).
3. Integrate fixture transport from #18 and measure real playback and WASAPI output (#22).
4. Run a Windows instrumented DLL and child worker under the host; report scoped native line/branch coverage independently of portable publisher tests.
5. Reuse the manifest-driven harness for foo_qobuz and foo_opushd, leaving each plugin's fixture and selector adapters in its own repository.

No real host run, FlaUI package audit, or verified Windows screenshot is claimed by this source-only change.

## Trace and backend qualification

See [TRACE.md](TRACE.md) for QPC-aligned Robot/ETW events, a strict normalized JSONL format, native test build instructions, and the Null Output → PCM capture → WASAPI hierarchy. The phase-zero host window smoke records launch intent and visibility only. It **does not trigger Play**, verify that Null Output is selected, or provide a measured first-audio or output timestamp. A manifest's `output_backend` value is requested configuration, never verified state.


## Real synthetic ZIP playback qualification — first executable scenario

The runner now accepts `--profile window` (the default), `--profile
fixture-zip-store`, or `--profile fixture-zip-deflate`. Both playback modes
exercise **actual foobar2000** in a disconnected Windows VM rather than a
Python-only mocked host. They create a deterministic 2-second WAV, place it
after 512 KiB of unrelated data in a ZIP, publish an isolated `.djt`
reference, and serve the archive only on `127.0.0.1` with HTTP range
support. Synthetic fixture content/hashes and a request ledger are retained.

Prerequisites: approved portable foobar2000 installation with the
`-e2e-trace.fb2k-component` test DLL installed and hashed into the immutable
template; `Null Output` must be selected in the template **and independently
verified on the host** (requested configuration alone is not proof).
Install and pin `robotframework`, `robotframework-flaui`, Python/.NET
dependencies from a digest-verified offline wheelhouse. The guest must have
an interactive desktop, no enabled network adapters, and an independent
hypervisor/vSwitch egress deny policy.

On the offline, interactive Windows guest, run from the checked-out commit
named by the fixture manifest (no unstaged source edits):

```powershell
py -3 -m e2e.host.runner --manifest C:\sandbox\manifest.json `
  --template C:\sandbox\foobar-portable `
  --output C:\sandbox\runs\store-001 --profile fixture-zip-store

py -3 -m e2e.host.runner --manifest C:\sandbox\manifest.json `
  --template C:\sandbox\foobar-portable `
  --output C:\sandbox\runs\deflate-001 --profile fixture-zip-deflate
```

Each invocation creates its own portable-profile copy. The **test executable**
is launched by Robot/FlaUI, and the fixture is added using foobar2000's own
command-line IPC (`/add`), followed by `/play`. Therefore this exercises
the host playback pipeline but does **not** yet exercise the search-list
selection, drag-and-drop, output Preferences, or UIA Play button. Those are
separate future Robot scenarios.

The runner starts a Windows ETW session *before* launching the application,
stops it after Robot, exports native TraceLogging milestones to the shared
QPC event schema, and checks:

- Actual Robot process/window visibility and Play intent in its JSONL trace.
- Native `component.track.open.begin` and first nonempty
  `component.decoder.first_pcm` events from the instrumented component.
- Exactly one comparable measured intent-to-first-PCM attempt (no heuristic
  mixing with background indexing, multiple tracks or other attempts).
- At least one actual HTTP **206** response with payload from the loopback
  fixture server; rejected ETW, missing trace, wrong clock domain, unsuccessful
  Robot test or zero requests are **not a pass**.

Outputs include Robot output/JUnit/screenshots, `component.etl`,
`component.jsonl`, normalized trace summary, HTTP request ledger, fixture
hash manifest and `checkpoint.json`. All are local to the offline guest.
The qualification scope is **native decoder invocation and HTTP-range
transport**, not correct PCM sample alignment, complete playback, NULL sink
consumption, Windows audio endpoint or acoustic output. Source-based DLL and
worker coverage remain null until they are genuinely collected.

**Source-only status:** there is no verified Windows ETW trace/GUI/playback
result in the repository yet. The built DLL, TraceLogging export format and
foobar2000 CLI/portable interaction need a real Windows run; never label
this success on the basis of a green portable GitHub Actions job alone.
The pinned Windows build workflow now includes a distinct ClangCL
`-E2ETrace` x64 component build and PowerShell syntax parsing.

### Batching: what is deliberately not automatic yet

The native ETW provider currently binds one scenario/attempt ID per process.
This first gate intentionally runs **one playback attempt per host**, despite
the desire to minimize restarts. The follow-up change will propagate a
per-track/per-action identity from Robot to the native input instance and
worker, allowing multiple ZIP/audio cases in one host without causal
misattribution. Do not simply change the environment variable or infer
attempts by nearest timestamps: the trace provider and background jobs won't
follow a mutable environment variable correctly.

After the first real Windows first-PCM pass, expand to Search → Select → Drag
and per-member format matrices in one host session; retain only dedicated
cold-process and restarted-persistent-cache control runs.

## Evidence screenshots (source candidate v0.0.144)

FlaUILibrary saves screenshots into the Robot output directory by default.
Both suites capture a screenshot after successful window interaction, and
attempt one more screenshot in Test Teardown even on test failure. The playback
suite also captures a screenshot after adding the synthetic reference.

The runner inventories screenshots as **files**, checks basic image headers,
hashes their bytes and includes their relative paths under
`checkpoint.json → screenshots`. A Robot green result **without any screenshot**
is now marked unqualified. A valid header is not proof of a correct visual UI.
Review the images locally for playlist state, selected output and errors.

Screenshots are retained **only in the offline VM's local run folder**;
this workflow never publishes them publicly. Review/redact before attaching
to a GitHub issue or PR, since screenshots may contain filenames and metadata.
The fixture/Window runs still do not assert complete SearchDialog behavior.

## Provisioning the instrumented DLL without bypassing BinSkim

The pinned Windows workflow now builds the **test-only x64 TraceLogging
component** and uploads `foo_doujinstyle-x64-e2e-unqualified` before
the release BinSkim scan. A successful upload is **not a security pass**:
BinSkim remains enforced, and release-oriented WIP packages still cannot
be published if that gate fails. The test component must be used only in the
isolated guest and never installed in a personal foobar2000 profile.

Hosted GitHub Actions is a **cross-build/protocol contract runner** here,
not a proven interactive GUI environment. There is no foobar2000 executable,
portable configuration, verified Null Output, or visual screenshot in CI.
The real host screenshot must be generated from the isolated Windows guest.

## Read-only real-catalog coverage audit

A user-supplied, closed SQLite v1 catalog can be profiled without copying raw
offer URLs, released filenames, display metadata, or user credentials into
source control. The database is **not committed** and no network operations
are performed:

```powershell
py -3 -m tools.catalog_corpus --db C:\sandbox\doujin.sqlite `
  --output C:\sandbox\runs\catalog-coverage.json
```

This is **coverage discovery**, not source-media or executable host E2E.
The project uses the observed provider, archive and codec distributions to
choose independently generated fixtures, including missing WAV/Opus/OGG and
RAR/7z methods. A catalog entry with `advertised_format=FLAC` does not
prove that the actual file is FLAC. Never publish an unsanitized live DB as an
artifact; user-specific source metadata and signed URLs can be present.

## Guest one-time manifest creation

Once an isolated portable foobar2000 template contains the **exact candidate**
component DLL and intentionally configured Null Output, from the clean source
checkout and preinstalled/pinned Robot/FlaUI packages:

```powershell
py -3 -m e2e.host.manifest_generator `
  --template C:\sandbox\foobar-portable `
  --component-path user-components/foo_doujinstyle/foo_doujinstyle.dll `
  --foobar-version 2.25 --output-backend null `
  --output C:\sandbox\manifest.json
```

The component relative path and foobar2000 version are **examples**: use
the actual values on the guest. The tool derives the template, host, component
SHA-256 values and source commit; it refuses dirty checkout / unsafe paths.
It does not verify Null Output was selected, nor does it configure networking.
Then execute the two runner profile commands from the previous section.


## v0.0.180 test-only promotion

This portable harness is intentionally independent of security PR #31, the
Windows binary release blocker and native runtime cutovers. All fixture and
ETW/screenshot outputs are source-only contracts until an externally isolated,
interactive foobar2000 guest produces real, attributable evidence.

## Isolated ETW instrumented test component — v0.0.200

The ordinary Windows component + six-PE BinSkim release gate and main-only
CI attestations are unchanged. A **separate ephemeral GitHub-hosted Windows
runner** executes `.github/workflows/host-etw-build.yml` and compiles the
opt-in x64 ETW component with the same pinned VS2022 MSVC and foobar2000 SDK,
but in its **own job workspace**. The test job never reuses the production
runner's `native/build/x64` output or changes any of the six release-scanned
binaries. Its output is `foo_doujinstyle-x64-e2e-unqualified`, containing
one `*-e2e-trace.fb2k-component`. There is no signer, release publishing
permission, or claim of Windows-host test qualification.

The test component includes native playback/seek/SearchDialog TraceLogging
milestones stamped using Windows QPC. The normal production binary contains
only a compile-time no-op for these trace calls; the TraceLogging provider
is compiled and linked only with `DoujinE2ETrace=true`. The ETW provider
reads a **single run/attempt/scenario from environment at its first
emission**, so **each qualified trace attempt requires a new foobar2000
process**. Do not batch multiple attempt IDs in a persistent process until
this limitation is resolved with mutable per-action correlation.

### Next runnable isolated Windows guest check

1. Obtain the exact-source test artifact from the dedicated ETW build
   workflow for the candidate SHA. Check its package SHA-256 and extract
   the x64 DLL into a disposable, portable foobar2000 installation.
2. Pin the component DLL hash, foobar2000 executable hash, exact source
   commit, Robot/FlaUI versions and template directory digest using
   `py -3 -m e2e.host.manifest_generator`. Never install into a personal
   foobar2000 profile. The fixture test process requires genuine desktop
   interaction and hypervisor-enforced egress isolation.
3. From the **same checked-out candidate SHA**, run
   `python -m e2e.host.runner --profile window` first; only then run
   `--profile fixture-zip-store` and `--profile fixture-zip-deflate`
   as separately launched processes, each with a unique output directory.
4. Validate actual Robot selectors/window and screenshot hashes, then
   ETW/HTTP 206/first-PCM evidence. A produced DLL or green Python fixture
   test is **not** a passing GUI or decoded-audio E2E test.

The unmodified production build remains the six-PE CI scanner's sole
subject. This E2E package is test-only even if its own build is green.

## Automatic exact-main-SHA E2E test package (v0.0.202)

The read-only `Windows x64 ETW test component (unqualified)` GitHub Actions
workflow builds on **each push to protected main**, in addition to PR checks
and optional manual dispatch. This ensures a **test-only** native ETW component
exists for the actual merged main commit, not just a synthetic PR merge SHA.
It runs on a separate ephemeral Windows runner and never overwrites the
production six-PE BinSkim build or signs/releases the trace package.

For the host VM, select the successful workflow run with
`head_sha == git rev-parse HEAD` in the checked-out clean `main` source
and retrieve `foo_doujinstyle-x64-e2e-unqualified`. Verify the
component ZIP/package checksum and installed DLL checksum, exact source
commit, and foobar2000 and Robot/FlaUI binaries with the manifest generator.
A test-only package and an Actions green build do not qualify ETW collection,
GUI selectors, Null Output, decoder PCM or output timing: these require the
real isolated interactive Windows host steps documented above.

## First host readiness anchors (v0.0.258)

From the isolated guest, after obtaining the exact successful main-source ETW
package and checking its independently recorded SHA256, install the x64 test
DLL into the pinned portable template and generate the manifest as above.
The new direct profile uses the identical generated WAV through a .djt with
no MEMBER; direct HTTP 200/206 is accepted, ZIP profiles still require 206.
Fixed ZIP timestamps remove a wall-clock dependency in the fixture identity.
Run all initial anchors with one command, one fresh host per profile:

```powershell
py -3 -m e2e.host.run_readiness --manifest C:\sandbox\manifest.json `
  --template C:\sandbox\foobar-portable --output C:\sandbox\runs\anchors-001 `
  --package C:\sandbox\test-e2e-trace.fb2k-component `
  --package-sha256 <independently-recorded-exact-package-sha256>
```

Order: window, direct WAV, ZIP STORE, ZIP DEFLATE. Check package digest and
embedded DLL against manifest, reject any already running foobar2000 process,
stop on first failed anchor and retain per-case checkpoints/readiness.json.
No driver, network or isolation changes. No output PCM or screenshot visual
correctness claim; actual interactive guest remains necessary. Package digest
must be obtained independently from the exact-source successful build, not
computed from an untrusted download and treated as authorization.

## Actual native ETW transport qualification (v0.0.259 candidate)

The dedicated Windows ETW build compiles a small standalone test-only EXE with
the same native TraceLogging provider and pinned MSVC/SDK. Python starts logman,
emits QPC intent, runs the native probe, stops ETW and invokes the real PowerShell
exporter. Verify exact run/attempt/PID, native sequence, QPC frequency, sample
payload and cross-process clock comparability. The exporter rejects header vs
payload PID mismatch and zero first-PCM samples; uses eventPid, not reserved $PID.
The probe has a synthetic sample count and is never executed in foobar2000.
Its evidence is explicitly native_etw_transport_only, never host audio/GUI.
No security policy or network changes; actual isolated guest still required.
