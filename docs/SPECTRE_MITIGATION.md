# Spectre mitigation candidate — v0.0.150

Security qualification is the **six binary** BinSkim BA2024 gate; compiler command-line declarations alone are not release evidence.

- **x64 ClangCL 23.1.6**: `-mspeculative-load-hardening` (LLVM speculative load hardening). Do **not** substitute `/Qspectre` for ClangCL: it is accepted but ignored by the driver. Place Spectre-mitigated MSVC runtime libraries first on the linker library search path.
- **Win32 MSVC v143**: An explicit CI exception uses MSVC `/Qspectre` after LLVM 23.1.6 aborted several SDK compilands at `Cannot emit physreg copy instruction` when x86 speculative-load hardening was enabled (v0.0.151). The checked-in Clang option remains fail-closed for local Win32 Clang experiments, but is **not qualified** for Win32 production builds.
- **ARM64EC MSVC v143**: `<SpectreMitigation>Spectre</SpectreMitigation>` enables `/Qspectre` and the matched Spectre-mitigated runtime libraries.
- **SDK static projects**: the hash-verified SDK's pfc, component client and foobar SDK project files are configured with the same architecture-appropriate options; no checked-in copy of the SDK is needed.
- **Acceptance**: normal Windows compilation + all six SARIF results must qualify; no BA2024 exception, no lowered warning severity, and no weakening of existing fail-closed policy.

Clang SLH can be expensive on hot loops and is **not** semantically identical to MSVC `/Qspectre`. Once binary qualification passes, measure actual WAV/ZIP seek and first-PCM startup on Windows before a public release. BinSkim may report unmitigated linked static libraries even when first-party source is correctly instrumented; investigate those PDB compilands rather than suppressing BA2024.

Sources: [Microsoft /Qspectre](https://learn.microsoft.com/en-us/cpp/build/reference/qspectre?view=msvc-170), [LLVM speculative load hardening](https://llvm.org/docs/SpeculativeLoadHardening.html), [BinSkim BA2024](https://github.com/microsoft/binskim/blob/main/docs/BinSkimRules.md).
