# Spectre and six-PE BinSkim merge gate — v0.0.198

This is a **CI security-policy and build change**, not Windows-on-ARM host execution qualification or signed release approval.

- Build Win32, x64 and ARM64EC component DLLs and embedded native worker executables using pinned VS2022 v143/MSVC 14.44.35207 and Windows SDK 10.0.26100.0; LLVM 23.1.6 is independently pinned for static analysis. MSVC is an explicit exception for all three Windows release architectures because LLVM speculative-load hardening backend attempts failed on Win32/x64 and ARM64EC ClangCL was unavailable.
- Enforce MSVC /Qspectre on native sources and SHA256-pinned foobar2000 SDK static projects, plus /MD Spectre runtime import-library preflight. For ARM64EC, MSVC-compatible `lib/spectre/arm64` runtime imports were linked and six PE scans passed on the v0.0.197 source candidate. This link compatibility does NOT establish ARM64 runtime behavior.
- Require `tools/run_binskim.ps1` and `tools/binskim_evidence.py` in the existing `component` job. Both must pass before the job reports success. Exact package and all six PE scans must be complete. BinSkim 4.4.9.14 is SHA512 pinned; report results are sanitized before upload.
- `security/bin-policy.json` defaults unlisted rules to blocking. BA2024 is blocking; BA6006 remains advisory. `tools/binskim-baseline.json` accepts no exceptions. Missing SARIF/binaries, a partial scan, scanner errors or evidence mismatch fail closed.
- The active `main` GitHub branch ruleset must require the `component` check, as well as existing portable/static/workflow-security checks. Ruleset settings and CI code should be audited together.
- VS2022/v143 is retained for older Windows x86/x64 compatibility; do not silently upgrade those build targets to VS2026/v145. Host compatibility remains unverified, not guaranteed.

This separate merge candidate deliberately **does not** add release-attestation workflows, OIDC write permissions, or a signature. Governance and interactive Windows host E2E are tracked separately in issues #32 and #33. The exact merge candidate v0.0.198 requires new CI qualification; the previous v0.0.197 pass does not automatically qualify different bytes.
