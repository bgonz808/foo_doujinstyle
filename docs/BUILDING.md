# Build and verification guide

[`VERSION`](../VERSION) is the semantic base, [`RELEASE_STAGE`](../RELEASE_STAGE) selects the release stage, and [`REVISION`](../REVISION) is the monotonically increasing source revision. Use `python -m tools.bump_revision --base origin/main` for **every source candidate**, including documentation-only and unqualified changes. `tools/build_identity.py` derives the Git/source-bound identity; qualification is separate.

## Actual CI and shipped architectures

- [Portable C++ and contract tests](../.github/workflows/portable.yml) run offline native/legacy Python parity, packaged-runtime Python AST inventory and strict compiler checks. A separate required `native-static-analysis` job invokes pinned **LLVM/clang-tidy 23.1.6** with `--strict` for its portable first-party compilation database. This does **not** establish complete Windows-only translation-unit analyzer coverage (see [#5](https://github.com/bgonz808/foo_doujinstyle/issues/5)).
- [Windows Build and BinSkim](../.github/workflows/windows-build.yml) runs on PRs to `main`, pushes to `main` and manual dispatch. Its production build currently selects **VS2022 MSVC v143 version 14.44.35207 for Win32 (x86), x64 and ARM64EC**, with Windows SDK **10.0.26100.0** and the official SHA-256-pinned foobar2000 SDK **2026-10-01**. The verified LLVM/ClangCL **23.1.6** distribution is also acquired for toolchain checking and static tooling, but the CI component invocation consumes the canonical MSVC default from `native/build.ps1`. Do not describe these production PEs as ClangCL-built.
- Each production build verifies the Windows final image architecture, including the **ARM64X** header required to distinguish linked ARM64EC from an ordinary x64 PE. It scans all six files (DLL and embedded `publish_references.exe` worker for each target) with pinned **BinSkim 4.4.9.14**. BA2024/Spectre is blocking and no exception is approved; BA6006/LTCG is advisory. Scanner execution, SARIF and package-to-built-DLL hash matching are fail-closed under the required `component` check.
- [CI workflow security audit](../.github/workflows/security-audit.yml) enforces pinned actions, narrowly scoped token permissions and relevant workflow controls. The exact available checks and enforced GitHub ruleset can be inspected under [#15](https://github.com/bgonz808/foo_doujinstyle/issues/15).
- [Windows x64 ETW test component](../.github/workflows/host-etw-build.yml) independently builds and uploads an **explicitly unqualified**, opt-in TraceLogging x64 component on PRs, `main` pushes and manual dispatch. It runs on its **own** hosted runner; it cannot replace any production BinSkim-scanned PE. The main-push test package is tied to that merged source SHA. This is a Windows **compile/package** result, not interactive foobar2000 GUI/decoder/PCM qualification (see [#33](https://github.com/bgonz808/foo_doujinstyle/issues/33)).

**Supported component build targets:** foobar2000 Win32/x86, x64, and ARM64EC (hybrid Windows-on-ARM). ARM32 and pure ARM64 are not qualified component targets. The existence of a valid ARM64EC binary does not prove native Windows-on-ARM playback.

## Local Windows canonical build recipe

Provision a fresh, trusted, SHA-256-verified foobar2000 SDK 2026-10-01 archive, Visual Studio 2022 MSBuild, and the MSVC/Windows SDK versions in [the candidate toolchain lock](../tools/windows-toolchain-lock.json), with matching /MD Spectre import libraries. Apply the [SDK mitigation helper](../tools/configure_foobar_sdk.ps1) exactly once to the fresh extracted SDK; the production CI also runs the [Spectre component installer](../tools/install_msvc_spectre.ps1) and [CRT verification](../tools/verify_msvc_runtime.ps1).

```powershell
./native/build.ps1 -SdkRoot 'C:\SDKs\foobar2000-SDK'
```

This is the same default compiler selection and release configuration used by the Windows production workflow. Use `-Platform x64` for a single architecture. The canonical entrypoint reads version pins from the repository lock, disables automatic MSBuild response files, requires clean Git source before/after, and enables commit-bound SourceLink for clean Git builds on both local and CI hosts. CI additionally binds the source to its event SHA. There is no implicit fallback to a different compiler. For local dirty/snapshot experiments explicitly use `-AllowDirtySource`; those remain unqualified and omit SourceLink. Explicit alternate MSVC/SDK pins require `-UnqualifiedDevelopment`; specifying LLVM is an explicit alternative compiler experiment.

This is recipe parity, not yet a proof of identical binaries. The lock is a candidate and the normal build does not currently verify its full content inventory, the SDK archive, or host closure by itself. Provisioning, independent local-style rebuilds, canonical source-path mapping and exact PE/PDB/package qualification remain tracked in #127/#114.

Run [PE architecture verification](../tools/verify_pe_targets.py), the pinned [BinSkim gate](../tools/run_binskim.ps1), and [manifest/evidence checks](../tools/binskim_evidence.py) against the final build before calling it equivalent to the required CI build. Local build success alone is not a passing security gate.

**Optional developer ClangCL experiment:** `native/build.ps1` also supports `-LLVMInstallDir` / `-LLVMToolsVersion 23.1.6` for supported targets. This is *not* the default qualified Windows CI recipe and must not inherit CI certification without its own tests.

The opt-in `-E2ETrace` switch requires x64 alone and creates a distinct `*-e2e-trace.fb2k-component`. Use the independent workflow rather than mixing test and production builds in one output tree.

## Portable development and static analysis

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'lxml>=5,<7'
PYTHON="$PWD/.venv/bin/python" bash tools/test_portable.sh
```

Portable strict analysis (with pinned matching Clang/clang-tidy installed):

```sh
python3 native/analyze_native.py --portable build-analysis \
    --clangxx clang++-23 --clang-tidy clang-tidy-23 --strict
```

Portable contracts exercise actual selected native C++ entry points and offline Python parity, but do not prove foobar2000 host UI, WinHTTP, real PCM playback or full DLL/worker source coverage. The Windows-host test contract and current unexecuted matrix live under [HOST_E2E.md](HOST_E2E.md) and [LATENCY_MODEL.md](LATENCY_MODEL.md).

## Artifacts, CI provenance and release qualification

A passing `component` job uploads the unsigned, **WIP** `foo_doujinstyle-windows-wip` package and sanitized `foo_doujinstyle-binskim-evidence` for the exact workflow run. No automatic Git tag, GitHub Release, or independently approved production signing is implied.

On trusted `main` runs only, an isolated `ci-provenance` job depends on the six-PE security build, verifies the input evidence and signs two GitHub keyless attestations for the **same package SHA-256**: standard build provenance and the project-specific six-PE security predicate. Those attestations have been produced successfully; see [BUILD_ATTESTATION.md](BUILD_ATTESTATION.md) for subject verification commands and qualification limits.

The build also produces a deliberately **partial four-entry SPDX source-input inventory**. This is **not** a complete shipped-artifact CycloneDX SBOM, independently updated SCA result, vulnerability-applicability/VEX determination, verified VSA, reproducibility proof or approved signed release. Track these separately under [#8](https://github.com/bgonz808/foo_doujinstyle/issues/8) and [#32](https://github.com/bgonz808/foo_doujinstyle/issues/32). Treat missing fields and host execution as unqualified, never as a pass.

For historical experiment evidence, see the [validation checkpoint index](history/validation/README.md). Historical notes never override current CI job results, exact artifact hashes or merged source identity.


## Shared archive and CI research reference

See [ARCHIVE_RESEARCH.md](ARCHIVE_RESEARCH.md) for the durable format/transport,
seek, decoder/cache, RAM backing and CI toolchain findings, including exact
run/commit-linked [machine-readable evidence](archive-research/2026-11-24.json).
Research qualification does not establish actual host audio or production
derived-toolchain qualification. #77 and #79 track remaining work.
