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
