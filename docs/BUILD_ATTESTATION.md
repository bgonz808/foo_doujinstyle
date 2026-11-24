# CI provenance and six-PE security claims (v0.0.199)

## Scope

Build security and release authorization are different trust decisions.
PR #40 established mandatory Win32, x64, and ARM64EC /Qspectre + BinSkim gates
for the component DLL and embedded native worker (six PE files).

PR #31 now adds **CI build provenance only**. The existing read-only Windows
`component` job is the required merge check. A separate Linux
`ci-provenance` job signs evidence with GitHub keyless `actions/attest`
**only for trusted main pushes or a manual dispatch on main**, after the
component job succeeds. The provenance job is skipped on pull requests.
It has job-scoped read access plus `attestations: write` and
`id-token: write`, with no repository contents/releases write access.

The signed subject is the exact `.fb2k-component` SHA-256 from that same
workflow run. Two signed attestations are produced:

1. Standard GitHub Actions build provenance for the package.
2. A custom security predicate using type
   `https://github.com/bgonz808/foo_doujinstyle/security/attestation-v1`.

The predicate is fail-closed: source Git SHA and source-content digest,
full component version, build run ID, package checksum, 6/6 completed
sanitized SARIF scans, scanned package contents, embedded worker bytes,
BA2024 blocking policy and exception-free baseline hashes all match.
It also binds the SHA256 of the **partial four-component SPDX 2.3 source
input inventory**. These checks run before signatures are minted.

## Independently verify an actual build

For the same **push-to-main Windows workflow run** which produced the
package, retrieve `foo_doujinstyle-windows-wip` and
`foo_doujinstyle-binskim-evidence`. Verify the package against the
evidence manifest and GitHub's signatures:

```sh
gh attestation verify ./foo_doujinstyle-v*.fb2k-component -R bgonz808/foo_doujinstyle
gh attestation verify ./foo_doujinstyle-v*.fb2k-component -R bgonz808/foo_doujinstyle --predicate-type https://github.com/bgonz808/foo_doujinstyle/security/attestation-v1
```

Verify the signer repository/workflow identity and match the attested
commit, package digest and policy/evidence digests to the artifacts.
Do not treat a successful unit test or PR check as evidence that the
production attestation job has executed; observe a successful
`ci-provenance` job and actual signature verification first.

## Explicit non-claims and future promotion

These signatures **do not** prove Windows host/foobar2000 playback,
ARM64EC runtime correctness, complete dependency/SBOM coverage, CVE
freedom, reproducibility, independent code review, or publication approval.
They are not Authenticode signatures or a claim of SLSA Build Level 3.

A protected `signed-releases` environment, independent reviewer,
published durable artifacts and release retention, full SCA and build-linked
SBOM, and per-architecture host qualification are separate upgrade steps
(issue #32 and issue #33). The `main` branch presently requires CI but has
zero independent approving reviewers; do not present this as two-person
approved release provenance.

**E2E and Python-to-C++ migration are unblocked independently of the signer.**
Continue tests and parity validation against SHA/version-bound WIP builds,
then qualify actual foobar2000 GUI and PCM behavior in issue #33. The
signed build record is useful supplementary evidence, not a migration
prerequisite. Ordinary PRs stay read-only and require BinSkim/quality checks.
