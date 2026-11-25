# Snapshot-backed host E2E execution

Status: operational design, no VM provisioned or snapshot restored by CI yet.
Python remains a development/test dependency, not a shipped runtime dependency.
Do not introduce a per-run PowerShell/Python installer. See #33 #22 #17 #18.

## Immutable invariant base

Use a versioned offline Windows x64 guest snapshot, preferably a stopped VM
disk snapshot or immutable image plus disposable differencing disks. Include
patched Windows build and required runtimes, publisher-authenticated portable
foobar2000 base (no candidate DLL), pinned Python, offline Robot/FlaUI wheels
and their installed dependency closure, Git, and verified test-only audio
endpoint configuration where relevant. No credentials, source candidate,
user library, real release data or long-lived secrets. Desktop/session readiness
and ETW permissions must be established in the guest. Keep virtual NIC absent
or disconnected and enforce independent hypervisor egress deny. Do not attach
a public PR self-hosted runner to a privileged network/hypervisor.

Snapshot identity: hypervisor/image ID, parent disk digest or trusted immutable
storage reference, OS build/patch epoch, host executable and portable tree
digests, Python executable/version and complete dependency inventory/digests,
VM resource configuration, virtual audio and graphics settings, and creation
policy/approval. Never use a mutable snapshot name as the sole trust anchor.
Refresh the baseline for security updates and dependency changes; revoke
obsolete bases, don't silently fall back. Record candidate receipt and exact
checkout as separate per-run mutable inputs. Initial pilot can use an
independently provisioned local Windows guest without automating hypervisor.

## Execution cohorts

1. Fresh trial group: restore/clone the verified base, inject reviewed
   exact-source package/exporter/receipt and source tree through an input
   read-only channel. Use guest_execute on a new local output directory.
   Copy/verify only candidate-specific state; no Python/Robot/FlaUI reinstall.
   Keep initial four local/direct/STORE/DEFLATE anchors as fresh processes
   with same template; do not restore VM per playback if measuring warm
   behavior.
2. Warm trial group: keep the same guest and host process for related
   stop/play/seek/reopen/A-B-A attempts with mutable attempt correlation.
   Do not reset transport/cache state between attempts intended to measure
   reuse. Restart process only for explicit process-cold/persistent-cache
   tests. Snapshot restore is not proof of cold OS page cache, DNS, TLS,
   remote/CDN cache or host backing-store cache.
3. UI regression group: same pinned base, deterministic named fixture
   states, stable display resolution/DPI/theme, independent screenshots and
   tree diffs. Capture outside timing window.
4. Live provider group: separate authorized connected guest, strict egress
   policy, no reuse of live credentials/content as offline base; no automatic
   live miss fallback. Private approved captures into immutable replay corpus.

Prefer disk-only snapshot/clone initially. RAM snapshot restoration is a
separate experiment: stale process handles, ETW sessions, synchronization
objects, clock domains, connection state and random state can invalidate a
run; never resume a live benchmark or compare QPC across restores. Start
new trace/run IDs after resume; independently check current clock behavior,
desktop and no prior foobar2000 process.

## Turnaround measurement

Measure base provision once (amortize), restore/clone, boot/resume, interactive
readiness, input transfer/verification, candidate template copy, configuration,
anchor run, evidence export, cleanup separately. Compare disk clone vs
snapshot restore vs existing repeated copy with matched resource limits and
cold/warm storage controls. Report median and raw observations; no asserted
savings before experiment. Prefer incremental immutable overlays and
persistent verified fixture store only if justified. Keep evidence on separate
bounded output volume, collect/hash outside guest before rollback; preserve
failed run diagnostics. Never make the test guest a trusted artifact signer.

First milestone: real foobar2000 timing using #95 guest_execute. Next:
hypervisor-independent snapshot contract, then adapter for user's available
hypervisor (e.g. Proxmox/Hyper-V), explicit trusted orchestration outside
guest, and measured restoration overhead. No automatic VM actions authorized
or implemented by this document.
