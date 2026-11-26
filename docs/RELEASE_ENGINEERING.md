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

## PDB and SQLite diagnostic pilot (revision 311)

Keep production flags unchanged. Fingerprint archive member headers, timestamps,
payloads and SQLite object/compiler PDB files; hash logical PDB streams and
physical directory separately. No raw PDBs, paths or binary transformations.
Second clean build on replica A restores original SDK from the verified archive,
cleans disposable checkout including ignored outputs and rebuilds at fixed paths.
Compare same-runner and independent-runner evidence; same-runner reuses original
verified toolchain inventory, not a second complete toolchain verification.
Select native librarian /Brepro, /Z7 vs /Zi, serial debug merging or path
normalization experiments only after evidence localizes divergence. Preserve
full debug, SourceLink, security mitigations and cache qualification.

## SQLite native-option remediation (revision 312)

Revision 311 reports establish same-VM same-path nondeterminism. Both SQLite
archive header timestamps and object payload differ; other archive member
payloads match. PDB logical streams differ, not merely physical layout.
Test native MSVC SQLite /Z7 (OldStyle) and librarian /Brepro, keeping full final
PDBs, /Qspectre, existing optimizations and BinSkim. Distinguish object raw code
and debug section families in evidence, with bounds and synthetic test. No
assumption that these options guarantee reproducibility: verify archive and
whole binary hashes, symbol binding and all standard checks. If librarian flag
is unsupported or ineffective, evaluate a pinned alternative separately.

## Reproducibility controls and isolation order (revision 313)

Revision 312 native /Z7 and librarian /Brepro compiled successfully in the
production x64 lane; reproducibility capture rejected a nonunsigned archive
timestamp. Accept only the recognized -1 sentinel in addition to prior formats;
retain raw header/payload digests and reject other negative values. Actual
sentinel and exact output equality remain to be confirmed in the rerun.

Research references and staged controls:
- Microsoft /Z7 vs /Zi, /Fd, /FS, /Zf: compiler debug/type-server and RPC
  scheduling. Test /Z7 and isolated PDB paths, then /MP1 vs existing /MP2.
- MSBuild /m:1 vs /m, /nr:false and /noAutoResponse: orchestration and hidden
  inputs. Collect effective ordered commands and response files, not just XML.
- Microsoft /CGTHREADS:1: LTCG codegen threading independently of /MP or /m.
  SDK /GL inputs require compatible LTCG; do not casually substitute lld-link.
- /LINKREPROFULLPATHRSP and /LINKREPRO: freeze exact ordered link inputs and
  repeat link independently; hash objects, archives, compiler PDBs, manifest
  inputs and resolved paths. Raw repro bundles/binlogs stay private, emit
  sanitized digests only. Repeat librarian with identical frozen object.
- /PDBALTPATH changes PE reference only. Native /d1trimfile and
  /experimental:deterministic are undocumented/experimental candidates, not
  release requirements; verify actual pinned tool behavior. Do not assume C#
  /pathmap or lld /PDBSourcePath is a supported native MSVC option.
- LLVM/Chromium deterministic build methodology: compare transitive inputs,
  same path clean, different path, incremental/cold and independent hosts.
  Evaluate deterministic llvm-lib on non-/GL COFF only if native controls fail;
  lld-link/clang path requires independent ABI, mitigation and ARM64EC gates.

References: https://learn.microsoft.com/en-us/cpp/build/reference/z7-zi-zi-debug-information-format
https://learn.microsoft.com/en-us/cpp/build/reference/fs-force-synchronous-pdb-writes
https://learn.microsoft.com/en-us/cpp/build/reference/cgthreads-compiler-threads
https://learn.microsoft.com/en-us/cpp/build/reference/link-repro-full-path-rsp
https://learn.microsoft.com/en-us/visualstudio/msbuild/msbuild-response-files
https://blog.llvm.org/2019/11/deterministic-builds-with-clang-and-lld.html
https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/deterministic_builds.md

## Stage-addressed replay (revision 314)

Revision 313: /Brepro librarian headers and non-object archive payloads equal
(-1 timestamps); /Z7 removes separate SQLite compiler PDB in measured obj root,
but object code/debug-family hashes differ. Same-VM PDB streams 101/191 and
68/113 differ. Preserve full clean builds as qualification. Both opt-in replicas capture native worker /LINKREPRO privately. Replica A
replays lib.exe on one
frozen SQLite OBJ and link.exe on the native worker response twice at fixed
replay output paths. Original build/SDK input aggregates and clean source
checked before/after, bundle hash checked per link, PE/PDB binding and stream
fingerprints measured. Replayed bytes are experimental, never packaged or
published; original vs replay output paths differ and full external read/exec
closure is not claimed. No raw bundles, response files, proprietary SDK or
stdout uploaded. Both replicas use the same diagnostic capture option; independent
comparison is of that diagnostic recipe, not production qualification. Follow with compiler-only SQLite replay using tracked command,
evaluated includes and preprocessed input identity; component LTCG link replay
and /CGTHREADS sweeps after native response capture works. Measure stage costs,
not assumed savings. See Microsoft /LINKREPRO and /LINKREPROFULLPATHRSP docs.
