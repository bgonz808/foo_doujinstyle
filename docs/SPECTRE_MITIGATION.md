# Spectre mitigation candidate — v0.0.157

Security qualification is the **six binary** BinSkim BA2024 gate; compiler command-line declarations alone are not release evidence.

- **Win32/x64 ClangCL 23.1.6**: LLVM speculative-load hardening remains an *experimental, non-release* configuration; the exact compiler crashed on Win32 (`Cannot emit physreg copy instruction`, v0.0.151) and x64 (`Unable to unfold load!`, v0.0.153). Never pass ignored ClangCL `/Qspectre` as a substitute. LLVM remains pinned for strict portable analysis.
- **Win32 MSVC v143**: An explicit CI exception uses MSVC `/Qspectre` for the qualified release; ClangCL is retained only for experimental build work.
- **x64 MSVC v143**: Another explicit exception enables `/Qspectre` and mitigated CRT libraries after LLVM 23.1.6 SLH failed in `publish_references.cpp` (v0.0.153).
- **ARM64EC MSVC v143**: `<SpectreMitigation>Spectre</SpectreMitigation>` enables `/Qspectre` and the matched Spectre-mitigated runtime libraries.
- **SDK static projects**: the hash-verified SDK's pfc, component client and foobar SDK project files are configured with the same architecture-appropriate options; no checked-in copy of the SDK is needed.
- **SDL and PDB provenance**: enable `<SDLCheck>true</SDLCheck>` for first-party and imported SDK projects (BA2026), and generate linker `/SOURCELINK:` + commit-bound mapping for all architectures (BA2027).
- **MSVC flag observability**: provide explicit `/Qspectre` in compile options, not merely project property; print categorically redacted linked-object diagnostic summaries; treat a missing SARIF `kind` as `fail`, not as absent, never full SARIF messages or PDB paths.
- **Acceptance**: normal Windows compilation + all six SARIF results must qualify; no BA2024 exception, no lowered warning severity, and no weakening of existing fail-closed policy.

Clang SLH can be expensive on hot loops and is **not** semantically identical to MSVC `/Qspectre`. Once binary qualification passes, measure actual WAV/ZIP seek and first-PCM startup on Windows before a public release. BinSkim may report unmitigated linked static libraries even when first-party source is correctly instrumented; investigate those PDB compilands rather than suppressing BA2024.

Sources: [Microsoft /Qspectre](https://learn.microsoft.com/en-us/cpp/build/reference/qspectre?view=msvc-170), [LLVM speculative load hardening](https://llvm.org/docs/SpeculativeLoadHardening.html), [BinSkim BA2024](https://github.com/microsoft/binskim/blob/main/docs/BinSkimRules.md).
