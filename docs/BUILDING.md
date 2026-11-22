# Build and release qualification

`VERSION` defines numeric base version (currently `0.0.69`).
`tools/build_identity.py` generates a source-dependent header for C++ with
`0.0.69-dev.N+gSHA[.dirty]` for Git checkouts and `0.0.69-dev.0+src.HASH`
for source exports with no Git metadata. A dirty-tree SHA-256 is embedded in
metadata; the display string alone does not distinguish two dirty worktrees.

No runtime Python has been added for build tooling. Python remains necessary
for the *existing* packaged foreground worker until it is retired.

## Local portable test development (Linux)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'lxml>=5,<7'
PYTHON="$PWD/.venv/bin/python" bash tools/test_portable.sh
```

The portable worker and all Python unit/contract tests run against bundled
SQLite 3.54.1 with a local SQLite preload. No remote provider requests are
made by the standard regression suite. An actual Windows component is not
produced by this command.

## Windows developer / Actions compile gate

The supported Windows native compile path is:

```powershell
./native/build.ps1 -SdkRoot C:\SDKs\foobar2000-SDK -Platform Win32,x64 `
  -WindowsSdkVersion 10.0.26100.0
```

GitHub's **Windows component compile** workflow is initially **manual only**.
It downloads the official 2026-10-01 foobar2000 SDK, validates the pinned
archive SHA-256 and invokes the existing Visual Studio project references,
which build the SDK dependencies along with the component. It does not need
SDK files committed to Git. The Windows runner's MSVC compiler is currently
image-provided, not yet reproducibly pinned: passing that job is a *compile*
gate, not deterministic-binary or foobar2000 runtime qualification.

The separate ClangCL + xwin cross-build recipe and strict analyzer gates are
in `native/TOOLCHAIN_RECOVERY.md`. LLVM 23.1.6, Windows SDK 26100 and MSVC
CRT 14.44 are the historically known-good *qualification* targets, not
verified components of the initial CI runner image. Do not publish the
Windows CI artifact as a qualified release until the exact toolchain is
pinned and host integration is checked.

## Package reproducibility

Both build scripts call `tools/package_component.py`; it sorts paths, writes
stable ZIP timestamps and file permissions, and omits generated attribution
manifests. They embed a build-identity header, not an Actions run ID.
Reproducibility is achieved only after independent same-input builds produce
matching DLL and `.fb2k-component` SHA-256. Timestamp and SDK/toolchain drift
in the MSBuild path still require controlled two-run qualification.

## Untrusted PR security policy

GitHub Actions jobs have read-only repository tokens, no provider credentials,
no `pull_request_target`, and no live provider tests. Dependencies are pinned
where they are GitHub Actions; the OS image, apt packages and Python test
dependency are temporarily image/channel-based for development convenience,
not part of a qualified reproducible release toolchain. Rotate to fully pinned
inputs for release builds. Sensitive capture fixtures should never be committed
without explicit sanitization and review.
