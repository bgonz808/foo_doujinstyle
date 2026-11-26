# Release engineering policy

Issue #114; protected publication governance #32 and supply-chain evidence #8.

VERSION contains numeric major.minor.patch for existing machine-readable contracts.
RELEASE_STAGE contains dev, rc.N or stable; REVISION is the monotonically increasing
source candidate counter, migrated from 0.0.302 to 303. The displayed diagnostic
version composes these coordinates with Git/source identity. The About text exposes
the project revision. Host registration continues to use numeric VERSION until actual
foobar2000 ordering is qualified; do not publish an RC from this change.

Before a new candidate, fetch the integration branch and run
`python -m tools.bump_revision --base origin/main`. Explicitly commit REVISION.
The read-only PR gate compares exact event base and head plus each new first-parent
commit; a later bump does not excuse an earlier missing increment. A merge must
use a current integration baseline. Independent branches can allocate the same
number locally; Git SHA and source digest are authoritative. No global allocator,
no compile-time writes, no wall clock, no CI run number in the binary. The policy
does not enforce remote uniqueness across unmerged branches. Build retries keep
the same source coordinates; provenance records execution identity separately.

Full and shallow Git checkouts of identical clean bytes must produce identical
identity headers. Git-free snapshots are explicitly distinct, not release evidence.
Dirty source identities include the source digest. Release qualification is not
implied by a version string.

## Remaining acceptance

Independent clean runner builds for Win32, x64, ARM64EC: compare each PE, PDB,
and final package bytes; qualify cached vs uncached. Explicitly report any differing
artifact and cause. Keep full separate matching PDBs for DLL and embedded worker,
verify CodeView GUID/age and cryptographic hashes; inspect path/privacy exposure.
Publish a small normal component plus separate symbols, checksums, source/toolchain
manifest, SBOM with declared coverage and qualification/provenance. Promote exact
qualified artifacts on protected main after approval, immutable RC tags, no asset
replacement or fresh unqualified publication build. Actual native GUI migration
parity and existing security gates remain necessary. First proposed RC 0.2.0-rc.1.

## Baseline experiment (revision 304)

Opt-in x64 pilot: two independent hosted Windows jobs with different checkout paths, same pinned SDK/MSVC configuration and clean source identity, no restored build outputs. Compare exact PE, PDB and one-architecture package digests; record PE structural fingerprints and known tool/runtime hashes. Capture hashes only, not PDB contents/paths. Input inventory is partial; report mismatches as observations, not a release gate or proof of complete hermetic closure. Extend to Win32/ARM64EC and final combined package, cache equivalence, GUID/age matching and durable symbols after the pilot. No new release privilege or publication.

## Deterministic build experiment (revision 307)

First remediation: MSVC compiler and linker /Brepro, explicit full debug and
nonincremental link, basename-only PE PDB reference. Preserve optimization,
LTCG and binary mitigations; include the configured SDK and SQLite in compiler
settings. Compare independent x64 runners with fixed checkout layout first,
not a path-independence claim. Record checkout path digest, SQLite archive hash,
known tool/runtime/image identity and before/after clean Git commit/tree/source
receipt. Verify both binaries' embedded identity independently of the embedded
worker. No post-link byte patching, raw symbol upload, or claim of hermeticity.
SDK/CRT/tool closure and ignored generated input coverage remain partial;
CodeView/PDB GUID/age and distinct-path experiments remain necessary.

## Toolchain closure and symbol matching (revision 309)

Inventory selected MSVC Hostx64 tools, headers and libraries, versioned Windows
SDK include/lib/bin, selected MSBuild binary directory and VC targets as sorted
relative path/content digests. Observational only: do not auto-approve a runner
or cache on this basis. OS-loaded dependencies, effective imported target and
actual read/exec closure still need verification. Separate derived SQLite
archive fingerprints from external build inputs. Verify PE CodeView basename
and GUID/age against the PDB information stream without transforming binaries
or publishing paths or raw symbols. This is a read-only x64 pilot, not a
complete PDB privacy or all-architecture release gate. Evaluate immutable
vendor offline layout or disposable VM snapshot only if measured drift makes
it necessary. Native-tool options preferred, not an absolute prohibition on
qualified deterministic post-processing.

## Hosted toolchain lock (revision 310)

Two independent revision 309 x64 receipts on distinct Windows runner images
matched all eight inventoried roots (11,605 files, 8,287,796,152 bytes).
The installed content is a candidate reference, not an approved release
closure. Pilot enforces an exact root/digest/count/size match. Production
release-stage source requires an explicitly approved lock; ordinary dev uses
existing version pins and normal CI, with full hashing only in opt-in pilot.
No automatic lock refresh, no automatic install or cache publication on drift.
A mismatch must enter explicit qualification or verified vendor provisioning.
Measured cold full inventory approximately 172 seconds in one hosted lane;
prefer runner reuse and eventually a measured narrower actual dependency
closure over unconditional multi-GB rehash in every development lane.
Current scope is selected roots, not complete OS-loaded dependencies. Cache
and offline installer acquisition remain future work if actual drift warrants.
