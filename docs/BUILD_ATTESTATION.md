# Provenance and attestation readiness — v0.0.160

The signed release chain must distinguish the security scanner's PDB compiler
command-line evidence from GitHub's cryptographic build provenance.

The gate validates the exact source commit and source-content digest, the
versioned package SHA-256, six sanitized BinSkim reports, scanner identity,
policy/baseline hashes, and the bytes of the embedded helper. Every failure
blocks an attestation. The output predicate is scoped to six-PE binary CI
security qualification only; it is not host playback or reproducibility proof.

The signing job must be restricted to successful trusted main builds, with
OIDC and attestations write permissions unavailable to pull_request jobs.
The independent signature must be bound to the exact .fb2k-component bytes.

Future gaps: effective compiler binary pinning and library lookup proof,
ARM64EC BA2024 zero, host E2E, dependency/SBOM inventory, reproducible
double builds, protected branch approval, release promotion and retention.
