# Build and verification guide

The current source version is defined solely in [`VERSION`](../VERSION). `tools/build_identity.py` derives a unique Git-based development version and source fingerprint. Every new change candidate updates `VERSION`; release qualification is tracked separately.

## Current CI baseline

- [Portable C++ and contract tests](../.github/workflows/portable.yml): offline native and Python parity tests, compile warnings treated as errors, packaged-runtime Python inventory.
- [Windows component compile](../.github/workflows/windows-build.yml): **pull requests and manual dispatch** on a hosted Windows runner, verified foobar2000 SDK 2026-10-01 archive, MSBuild + **verified official LLVM/ClangCL 23.1.6** for **Win32 and x64**, with an **explicit MSVC v143 ARM64EC compiler exception** because the Windows runner does not support ARM64EC via the ClangCL MSBuild toolset. The runner verifies the official archive SHA-256 and compiler/tidy version and rejects unexpected toolchains.
- The Windows workflow attempts **ARM64EC** compilation using an explicit v143 toolset selection in addition to Win32/x64 ClangCL; host/runtime qualification on Windows 11 ARM and the CI result remain distinct. ARM32 is not a supported foobar2000 component target.
- A matching LLVM 23.1.6 strict static-analysis job is included in portable CI; workflow-security checks (actionlint, zizmor and repository-specific negative tests) run in a dedicated CI job; BinSkim is still an outstanding shipped-artifact gate.

Neither a green native compile nor a successful portable test suite proves actual foobar2000 host integration. A one-song manual smoke was reported for the 2026-11-21 experimental Windows build; further decoder, seek, concurrency, provider and architecture testing remains necessary.

## Local Windows ClangCL build

Install Visual Studio 2022 MSBuild + C++ tools, foobar2000 SDK, compatible Windows SDK and clang-cl. Verify their versions and record the exact build identity. For the *current* working installation, use:

```powershell
./native/build.ps1 -SdkRoot C:\SDKs\foobar2000-SDK -Platform Win32,x64 `
    -WindowsSdkVersion 10.0.26100.0 -LLVMInstallDir 'C:\Program Files\LLVM'
```

To enforce an exact supported LLVM version once installed:

```powershell
./native/build.ps1 -SdkRoot C:\SDKs\foobar2000-SDK -Platform Win32,x64 `
    -WindowsSdkVersion 10.0.26100.0 -LLVMInstallDir 'C:\Program Files\LLVM' `
    -LLVMToolsVersion 23.1.6
```

The second command intentionally fails unless the installed compiler reports the expected version. A release candidate cannot silently fall back to an older compiler. ARM64EC may be added to the `-Platform` list after toolchain support has been demonstrated; building ARM64EC is **not** the same as building Windows ARM32 or pure ARM64.

## Portable development and tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'lxml>=5,<7'
PYTHON="$PWD/.venv/bin/python" bash tools/test_portable.sh
```

For native standalone strict Clang checks on supported LLVM versions:

```sh
python3 native/analyze_native.py --portable build-analysis \
    --clangxx clang++ --clang-tidy clang-tidy --strict
```

Portable tests do not use live provider credentials. They include Python compatibility contracts until the installed Python foreground worker is fully retired.

## CI test artifacts, security and provenance

The Windows workflow uploads a `foo_doujinstyle-windows-wip` Actions artifact containing an unsigned `.fb2k-component` (7-day retention). It does **not** create a Git tag or GitHub Release. Verify the source SHA and package checksum and test in a separate foobar2000 profile.

BinSkim, complete Windows clang-tidy, CodeQL, full shipped-binary SBOM/SCA, SLSA build provenance, VSA and release signing are **not** yet certified or assumed by this document. Track their actual status using [GitHub issues](https://github.com/bgonz808/foo_doujinstyle/issues).

For historical release evidence, see [validation checkpoint index](history/validation/README.md). Those historical documents are not a substitute for current CI logs and artifact-specific hashes.
