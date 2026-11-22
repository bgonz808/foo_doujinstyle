# DevSecOps / software supply-chain security baseline (not a certification)

Baseline: source `bgonz808/foo_doujinstyle`, audited 2026-11-21; initial trust model: GitHub-hosted ephemeral CI, unsigned WIP artifacts, no release signing. This is a *control mapping and action plan*, **not** NIST certification or a claim of an OpenSSF Scorecard number.

References: [NIST SP 800-204D (final, 2024)](https://csrc.nist.gov/pubs/sp/800/204/d/final), [OpenSSF Scorecard](https://scorecard.dev/), [SLSA v1.2 verification summary](https://slsa.dev/spec/v1.2/verification_summary).

Each check is marked `PASS`, `FAIL` or `NOT_EVALUATED` **only when backed by applicable evidence**. `IN_PROGRESS` below identifies work being implemented and is **not a passing verification result**. Scores and pass thresholds must not silently convert unknown information into a pass.

## Audit at the baseline

| NIST 800-204D supply-chain control area | Current evidence / known gap | State | Next verification |
|---|---|---|---|
| Source repository / SCM controls | Git commits identify sources; reviewed PR workflow available. No rulesets returned by GitHub API; connector cannot inspect classic branch protection (403). | NOT_EVALUATED | Owner: verify ruleset/branch protection, required independent approval, required CI checks, no admin bypass / force push. |
| CI access and isolation | Hosted Linux and Windows runners; workflow-level `contents: read`, checkout `persist-credentials: false`, no privileged PR triggers in source. | PASS for repository-declared configuration; execution infrastructure beyond this is not audited | Enforce with `tools/security_audit.py`, actionlint and zizmor; verify runner image and trust policy. |
| Dependency integrity | Official foobar2000 SDK SHA-256 verified before build; SQLite and JSON fetches checksum-pinned, vendored files retained. LLVM toolchain and OS package versions still image/channel-derived. | IN_PROGRESS | Record exact DLL/EXE/SDK/toolchain digests, source of every binary dependency and provenance of vendored libraries. |
| Static analysis, compilation, tests | ClangCL Win32/x64 and portable tests were CI-green on 2026-11-21. Strict clang-tidy/Clang Static Analyzer, CodeQL and full Windows foobar2000 host-test matrix are not CI-enforced. | IN_PROGRESS | #5, #2: complete first-party translation unit coverage and host tests. |
| Binary security posture | Compiled Windows DLL and worker exist; PE mitigation baseline not yet established by BinSkim. | NOT_EVALUATED | #6: BinSkim each shipped PE and assert the complete target inventory. |
| SBOM, SCA, license audit | SQLite, utf8proc, nlohmann/json and SDK are known inputs; full normalized SBOM and vulnerability database snapshot absent. | NOT_EVALUATED | #8: SPDX/CycloneDX, Syft/SCA augmented for statically linked native code, VEX when justified. |
| Provenance and evidence | Git source fingerprint embedded in built DLL; Actions artifact hash logged. No signed in-toto/DSSE provenance, independent verifier/VSA or reproducible build proof yet. | IN_PROGRESS | #8: protected provenance workflow, subject hash check and independent verifier; no unjustified SLSA level. |
| Promotion and distribution | WIP `.fb2k-component` artifacts expire after 7 days; no Releases or signing. | PASS for intended WIP distribution only | Protected release workflow with explicit approval, Azure Artifact Signing and independently checked attestations later. |
| Monitoring and response | CI logs exist; no scheduled SCA/VEX reevaluation, triage SLA, vulnerability reporting policy or exception ledger proven. | NOT_EVALUATED | Add SECURITY.md, scheduled scanning and evidence retention policy; audit exceptions quarterly. |

## OpenSSF Scorecard audit policy

A scheduled/dispatchable Scorecard v5.7.0 CLI job records detailed JSON for the **exact main commit**. It is intentionally read-only and does not publish a badge or request signing/OIDC permissions. Treat the **individual check results** as evidence; a single composite 0–10 score is not an assurance claim. A fresh checkout's branch protection, code-review, security-policy, SAST, fuzzing, dependency updates and signed-release scores may be limited by repository age and access visibility. Never invent a score from static GitHub file inspection.

The official Scorecard Action v2.4.3 had an unresolved mutable Docker image tag despite a pinned GitHub Action SHA ([issue](https://github.com/ossf/scorecard-action/issues/1676)). We therefore prefer a versioned Scorecard Go CLI install with Go's module checksum validation, operating without publish credentials. This does not by itself guarantee the runner, Go compiler or all network dependencies are pinned; that is tracked in the build provenance workstream. Use Scorecard v5.7.0 or later because that release addressed credential-forwarding and symlink handling vulnerabilities; v5.6.0 had known issues.

## Priorities and release policy

**P0 (migration gate):** first-party Windows x86/x64 ClangCL compilation, portable parity/contract tests, workflow security lint, correct credential scopes, strict first-party analysis and BinSkim PE coverage. Treat each unimplemented P0 gate as **NOT_EVALUATED**, never green-by-default.

**P1 (parallel with migrations):** manual-build CodeQL on Windows where practical, sanitizer/fuzz fixtures for archive/IPC inputs, complete SBOM/SCA plus licence/source provenance; evidence manifests with binary coverage and scanner timestamps.

**P2 (qualified release path):** reproducibility comparison; source/build SLSA attestations verified by a separate trust boundary; VSA at an explicit policy snapshot, with `timeVerified`; VEX for specific CVE applicability; optionally Azure Artifact Signing after promotion. A VSA says a policy was verified at a particular time, **not** that the artifact is vulnerability free.

Policy is risk-based: don't block every Python-to-C++ migration on P2 controls. Do not publish a qualified release or sign a security declaration without verifying all required release checks for the exact digest.

## Evidence longevity

Seven-day WIP binaries are appropriate. Store concise checksums, toolchain manifest, scanner version/database date, signed policy verdicts, and manually attributed host-test evidence outside expiring Actions logs for audit needs. Removing a temporary artifact never revokes downloaded copies. GitHub artifact retention is an engineering control, not a NIST-prescribed interval.
