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
