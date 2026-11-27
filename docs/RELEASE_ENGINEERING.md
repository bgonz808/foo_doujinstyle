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

Revision 314 actual native librarian replay 31ms/28ms, identical SHA256
cba0f5321853db14a11162495079c3c98571de1d37c8784a87851bee2770c4d8,
6,705,768 bytes, also identical to original library; original input aggregates
unchanged. Worker link response discovery failed; no linker result.
Revision 315 removes redundant target filter in worker-only capture, handles
quoted response flags and absent original OUT (replay supplies it), records
sanitized extension counts and response digests/features. Separate compiler
replay uses actual SQLite CL.command.1.tlog and evaluated include paths; two
preprocessing and compilation passes with fixed private output paths. Original
flags preserved except output redirection. Compare preprocessed and object
bytes; not yet frozen-preprocessed-input or original-output equivalence.
Each stage records independent success/failure. No raw logs/response/input
publication, no cache or release qualification. Actual Windows CI pending.

Revision 315 actual report 15158103162: isolated SQLite preprocess 402/406ms,
8,000,830 bytes exact; direct compile 27.933/27.726s, 6,572,579 bytes exact
(all section-family hashes), original object 6,572,679 bytes differs; librarian
30/27ms exact original library. Native capture 29 files, 21 libs, 4 objects,
1 PDB, response found. Link invocation returned LNK1158, child executable
missing/unlaunchable; actual helper not recorded, do not infer identity.
Revision 316 explicitly supplies pinned SDK bin and MSVC child lookup paths,
records approved helper digests, reports only allowlisted helper name on failure.
Separate standalone x64 SQLite MSBuild /m:1 /nr:false repeat on clean project
outputs, no foobar SDK acquisition or final PE compile/link; no broad lock claim.
Retain independent comparison even if replay fails after uploading both receipts.
Compare original and replay object section families, not merely whole hash.
No change to production flags, no artifact promotion; all actual CI pending.

Revision 316 standalone report 15160026100: 39.618/22.975s, identical
tracked command and generated source tree, different OBJ/LIB. Six of nine
COFF family digests identical; .text, .debug, other differ. Independent full
report 15159649304: no recorded input differences, 0/5 final output matches.
Stage replay failed before link at plain-dict SystemRoot lookup (Windows
os.environ canonicalizes keys to uppercase), no stage report retained.
Revision 317 case-insensitive system-root validation, stage KeyError receipt,
and additional order-independent multiset hash over named raw COFF sections
with synthetic order/content tests. Does not normalize or establish semantic
COFF equivalence; relocations/symbol identities remain separate.
Performance observations (x64 production MSBuild, seconds wall / summed Link
 task seconds): r306 102.021/6.389; r308 142.271/5.087; r311 148.576/4.743;
r313 150.561/4.758; r316 148.753/4.832. Different runner allocations and source
revisions, not a paired causal benchmark. Current /Brepro and /Z7 flags remain;
no global serialization or reduced optimizer parallelism. Compare native flags
and concurrency with paired alternating fixed-VM runs and separate stage CPU,
wall, output size and equality before choosing any slower configuration.

Revision 317 stage replay 15160993520: preprocessing 0.332/0.331s,
compiler 23.060/22.777s exact repeat (not original object); librarian
0.049/0.046s exact; worker native linker 0.232/0.230s with identical EXE,
GUID/age and PDB info stream; 65/113 PDB streams differ. Standalone
SQLite 35.875/23.166s, OBJ/LIB still differ; order-independent raw section
fingerprints differ for .text/.debug/other, so not merely section ordering.
Independent full report 15160933710: same recorded inputs, DLL/EXE/package
identical, both PDBs differ. Revision 318 adds same-runner alternating
SQLite compiler /cgthreads default/1/2/4 (two samples each, tracked actual
option), independently tests undocumented native /PDBTHREADS:1 and
/PDBMODCLOSETHREADS:1 individually and jointly on frozen worker link inputs
(three samples per supported profile, median, whole PE/PDB identity and PE
vs default). Unsupported options are recorded, never promoted. No global
serialization or production flag change, no full closure claim. Microsoft
compiler /cgthreads documentation: https://learn.microsoft.com/en-us/cpp/build/reference/cgthreads-code-generation-threads
Undocumented linker leads only: https://github.com/assarbad/msvc-undoc/blob/main/LINK.exe.md

Revision 318 (#124, run 45913664409) report ZIP digests verified: SQLite default
and /cgthreads1 identical in two standalone repeats; 2 and 4 differ. Median
23.154s for 1 vs 23.014s for 4. Worker /PDBTHREADS:1 reduced differing logical
PDB streams from 65/113 to 3/113 (3, 7, 15; directory equal), median 259ms vs
252ms default, no PE difference; no complete PDB equality. Independent clean
replicas 0/5 identical despite equal recorded known inputs. Revision 319 adds
private separate native DLL /LINKREPRO capture, frozen component LTCG replay
(default vs PDBTHREADS:1, 3 each), and two standalone SQLite runner replicas.
No production flag changes, no raw bundle upload, no release qualification.

Revision 320: replace standalone codegen-thread sweep with native /Brepro baseline,
/experimental:deterministic, and combined /experimental:deterministic + /pathmap
(Triton Windows reported wheel recipe). Two independent runners, two detached
worktree roots per runner and two repetitions per profile/root. Preserve /Z7,
/Qspectre and optimization; explicit /noAutoResponse, verify tracked flags, report
rejected diagnostics without paths. Standalone only, no production setting change;
full PE/PDB and SourceLink mapping remain to qualify. Reference:
https://github.com/triton-lang/triton-windows/blob/readme/BUILD.md

## Canonical local and CI build recipe (revision 322)

#127: default native/build.ps1 resolves MSVC and Windows SDK version pins from
 the checked-in candidate lock, disables automatic MSBuild response files,
requires clean Git identity before/after unless explicitly opted out locally,
and enables SourceLink from clean Git identity rather than CI environment.
Windows production/ETW workflows use the same entrypoint defaults. Explicit
LLVM or unqualified developer profiles remain separate; no automatic fallback.
This is recipe parity only: content lock enforcement, shared SDK provisioning,
full reproducibility and canonical SourceLink path qualification remain open.
No experimental compiler flag promoted in this change.

## Compiler/librarian/linker comparison (revision 325)

#129: opt-in Windows matrix with pinned LLVM 23.1.6 from existing verified
archive, additional bounded forensic tools extracted only for research.
Two runners, two relocated roots, two repeats per compiler/root; ordinary
x64 COFF C fixture (no CRT/SDK/LTO/runtime execution), frozen entry and payload
objects, native relative/absolute lib.exe, llvm-lib and llvm-ar COFF,
Microsoft LINK and LLD. 16 profiles, 64 attempted links per runner. Full
PE/PDB binding and raw artifact equality; unsupported profiles recorded,
baseline completion required. Read-only LLVM views of actual frozen MSVC
worker/component linker PDBs and PE in replay; sanitized per-view hashes,
stream roles only when recognized, no raw symbol or path upload. Preserve
production mitigation gates and optimizer concurrency. Not a production
compiler choice; no claim of SDK /GL compatibility or all-arch qualification.

Revision 326: repeat r321 mapped SQLite source experiment and replay actual
frozen mapped object with native lib.exe /Brepro using absolute versus relative
operand, same cwd/output basename, two repeats per form for each mapped build.
Verify exact object payload in archive and unchanged original files. Two runners,
16 observations per librarian form across both roots and repetitions. No production
librarian substitution, no artifact mutation; native path repair qualification only.

## Frozen SQLite inputs (revision 331)

Extract the successful r330 diagnostic lane independently of incomplete worker
flag instrumentation. Both r330 Windows replicas completed: four mapped MSBuild
controls, eight preprocessing passes and 32 direct replays; 3/4 MSBuild OBJ
predominant hash, one repeats r327 alternate hash. Per root both modes 8/8 across
hosts identical; two unique hashes across roots. Preprocessing also two hashes
across roots. Distinct modes not expected to produce identical debug records.
Exact artifact references and caveats in #114/#129. No production flag changes.

## Process/image observations (revision 333)

#129: opt-in Windows native ETW Kernel-Process provider (process/image keywords),
logman session, ControlTrace STOP loss/buffer counters, private Get-WinEvent
manifest XML and MSBuild diagnostic command log. Two independent runners,
two workspaces, two traced and two untraced builds per workspace, balanced
order. Compare exact OBJ/LIB, generated source and tlog fingerprints and build
time; process lifetime, observed compiler concurrency, allowlisted tool names,
post-trace loaded image file hashes; reject missing process or lost events.
No raw ETL/XML/log/paths uploaded. Command-line field may be absent in this
manifest; diagnostic MSBuild text is not proof of actual child argv, cwd,
environment or response bytes. Image hashing after the trace is not atomic
at-load identity. Pre-start image events reported separately; no full process,
file-read, already-running server or loaded dependency closure assertion.
No production build settings changed. Separate from #132/#133 until reconciled.

Revision 334: both r333 ETW sessions started/stopped and decoded to XML with
zero reported event/buffer losses, but parser expected the wrong XML namespace
and failed System lookup. Parse outer envelopes with namespace-independent
local names, reject malformed target records, retain sanitized counts/errors
and continue samples. Exercise actual Windows event namespace, namespace-free
fixture and nested Event payload. Reconcile on merged r331; no production change.
