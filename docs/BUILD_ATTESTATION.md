# Provenance and attestation readiness — v0.0.161

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

## Trusted-build signing

The Windows workflow additionally runs on main pushes. On pull_request builds
the component job is read-only and the signing job is skipped. Only a successful
main build can start the isolated signing job; it receives narrowly scoped
OIDC and attestations write permissions. A full-history checkout ensures the
version/source fingerprint agrees with the original Windows compilation.

The signing job uses SHA-pinned checkout, download-artifact and actions/attest
and reads the built package and sanitized six-PE evidence from the same run.
The attestation gate must pass before issuing both a standard SLSA provenance
attestation and a custom predicate with type URI:

    https://github.com/bgonz808/foo_doujinstyle/security/attestation-v1

Verification (for a successful attested main build only):

    gh attestation verify ./foo_doujinstyle-v*.fb2k-component -R bgonz808/foo_doujinstyle

To inspect the separate security claim, add:

    --predicate-type https://github.com/bgonz808/foo_doujinstyle/security/attestation-v1

These signatures attest only the scoped statements and exact package digest.
They do not certify a release or host E2E behavior. Do not call this
SLSA Build Level 3 without reusable-builder isolation and related controls.
