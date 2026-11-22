# Windows / foobar2000 build environment recovery

This archive does **not** include the external foobar2000 SDK or Windows sysroot. No foobar2000 DLL was built or exercised in the current Linux sandbox. The existing sources support two explicit reproducible build routes. Do not substitute a Linux publisher process for Windows WinHTTP/CNG and foobar2000 validation.

## Native Windows (preferred host-integration gate)

Prerequisites, supplied by the operator and pinned in CI:

- A matching official foobar2000 SDK extract (`foobar2000/SDK/foobar2000.h`, the PFC, SDK and component-client Visual Studio projects); the architecture-specific `foobar2000/shared/shared-Win32.lib` and `shared-x64.lib` used by the project must be built/present.
- Visual Studio 2022 Build Tools or compatible, MSBuild, C++ v143 toolchain and a supported Windows 10/11 SDK; run from a Developer PowerShell.
- LLVM/Clang installation that supplies **clang-cl**, **clang-tidy**, and **lld-link**, pinned to one major release. Python 3 required by the SQLite/zipapp generation steps.

Inventory first (adjust to actual paths; no magic SDK auto-download):

```powershell
py -3 native/toolchain_doctor.py --mode windows --sdk 'C:\SDKs\foobar2000' --windows-kit 'C:\Program Files (x86)\Windows Kits\10' --llvm-bin 'C:\Program Files\LLVM\bin' --output native/toolchain-audit-windows.json
```

Then explicitly select the ClangCL toolset, rather than silently falling back to MSVC:

```powershell
./native/build.ps1 -SdkRoot 'C:\SDKs\foobar2000' -Platform Win32,x64 -LLVMInstallDir 'C:\Program Files\LLVM' -WindowsSdkVersion '10.0.YOUR_VERSION.HERE'
```

The example Windows SDK version is intentionally a placeholder: discover the installed kit before choosing. `build.ps1` already packages the architecture-specific DLLs into `.fb2k-component`. Verify the installed component's displayed version against `native/component.cpp`; do not publish a candidate under a version not compiled into the DLL.

**Static analysis:** the native MSBuild entrypoint does not itself claim a strict clang-tidy pass. The cross toolchain's `--analyze strict` path does; otherwise generate and audit an equivalent compile database on the Windows host. Review all first-party warning/analysis failures before qualification.

## Cross build with xwin (compile qualification, not runtime qualification)

A matching foobar2000 SDK, an xwin Windows CRT/SDK sysroot, and one LLVM toolchain are required. Discover them using:

```sh
python3 native/toolchain_doctor.py --mode cross --sdk /path/to/foobar-sdk --xwin /path/to/xwin --llvm-bin /path/to/llvm/bin --output build-cross/toolchain-audit.json
python3 native/build_cross.py --sdk /path/to/foobar-sdk --xwin /path/to/xwin --llvm-bin /path/to/llvm/bin --arch x86 x64 --analyze strict
```

`native/build_cross.py` compiles the bundled worker and foobar2000 SDK sources, performs strict Clang analysis, and produces x86/x64 components, but cannot prove working WinHTTP or host callbacks here.

## Release qualification gate

After successful **both-architecture** source builds, test installation and startup in appropriate foobar2000 versions, playlist paths, search/graphical result events, Stop during GET/POST/SQLite commit, listener thread affinity, MediaFire and MEGA live network operations, cancellation of the *entire* Windows Job Object child tree, and metadata/seek/playback parity. Preserve logs, SDK hashes, build compiler banners, sanitizer outputs, and the `.fb2k-component` SHA-256.

**Security residual:** process-tree termination can interrupt an external POST after it reached the server but before the local SQLite commit. It is not valid to claim cross-session exactly-once effects without a durable attempt journal and remote idempotency semantics. Treat a retry as a new attempt whose earlier outcome may be unknown.

## Build identity (from v0.0.65)

The canonical numeric base version lives in `VERSION`. Git identifies committed
source, and `tools/build_identity.py` generates an ignored C++ header with a
SemVer prerelease string and a deterministic dirty/snapshot digest. The same
script supports Git-free source archives; it never fabricates a Git SHA.
The foobar2000 component advertises numeric base version for update comparator
compatibility; its metadata and the worker `--version` expose full identity.

`native/build.ps1` and `native/build_cross.py` consume this generated identity,
and `tools/package_component.py` packages members with stable ordering and
metadata. No `SOURCE-ATTRIBUTION.json` is committed or packaged. Inspect
`VERSION`, full Git commit, compiled binaries and final archive hash when
qualifying a release. Verify the vendor SDK SHA-256 before building.

For uncertain POST recovery, query the `catalog_post_attempts` audit endpoint
or the SQLite table with a read-only connection. Cross-reference the release
with provider-side state and the locally committed offer. If and only if a
responsible operator confirms a new POST is acceptable, perform a deliberate
SQLite backup and explicit transactional marker resolution while all workers
are stopped. Never auto-clear by age, restart, or network timeout; a marker
may represent a POST that succeeded remotely but was not committed locally.


## Known-good historical build recipe, checked again 2026-11-21

Earlier **full x86 and x64 foobar2000 builds** used the following family of
build dependencies, per the earlier development checkpoint. Preserve it as a
recovery target; this table does **not** assert those binaries or SDK contents
exist in the current execution workspace.

| Dependency | Historically used / validated | Recovery route |
|---|---|---|
| foobar2000 SDK | **2026-10-01** | https://www.foobar2000.org/downloads/SDK-2026-10-01.7z (official SDK download) |
| Windows SDK | **10.0.26100** family | Visual Studio Installer / Windows 11 SDK, record exact full version |
| MSVC CRT | **14.44** family | Visual Studio 2022 v143 toolchain; retain actual CRT manifest and file hashes |
| LLVM clang-cl/lld-link/tidy | **23.1.6** | https://github.com/llvm/llvm-project/releases/tag/llvmorg-23.1.6 ; official binaries may lag the source release; verify provenance |
| foobar SDK shared libs | built for **Win32 + x64** | rebuild from matching foobar SDK, no mixing SDK versions |

Before changing to a newer SDK/LLVM, reproduce the historical recipe and run
strict x86/x64 builds, static analysis and host loading tests. This source
candidate's portable Clang is v17, **not** the earlier qualified LLVM v23;
portable compilation and sanitizer passes do not replace the Windows build.

In this workspace, `native/toolchain_doctor.py --mode cross` finds the bundled
SQLite amalgamation, JSON and utf8proc source, but it cannot locate the
external foobar2000 SDK, xwin CRT/Windows SDK headers/libraries, or clang-tidy.
The official SDK host also failed to download from the current container.
Do not synthesize, vendor, or misattribute missing Microsoft SDK/CRT artifacts.
On a Windows host with the earlier toolchain installed, run the doctor in
`--mode windows`, then:

```powershell
./native/build.ps1 -SdkRoot 'C:\SDKs\foobar2000' -Platform Win32,x64 `
  -LLVMInstallDir 'C:\Program Files\LLVM' -LLVMToolsVersion '23.1.6' `
  -WindowsSdkVersion '10.0.26100.0'
```

Replace paths and the Windows Kit's fourth version component with actual
installed values. Only after compiling and launching in foobar2000 should a
`.fb2k-component` be marked installable. Keep the Git revision and deterministic source-snapshot digest
from the source zip, compiler versions, linker map and binary hashes.
