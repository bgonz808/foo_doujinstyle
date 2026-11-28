# Public playback qualification — #135

Revision 336 begins a planning and evidence-accounting layer using existing
media-support and decoder-io manifests. No provider traffic, playback or
emulator-fidelity claim is made. The complete core product is enumerated,
including unknown and unsupported combinations, not silently excluded.

Generate the deterministic plan:

    python -m tools.playback_qualification --output build-linux/quality/playback-plan.json

Summarize sanitized JSON attempt receipts:

    python -m tools.playback_qualification --attempts receipts.json --output report.json

Each receipt binds exact fixture/archive/track, package, host and decoder
identities; network, cache, architecture and required-work class; action,
outcome and independent evidence digest. Full-track successes require byte,
PCM, metadata and EOF oracles, seek/startup require PCM validation. A negative
case requires verified safe rejection. The same content hash counts once even
if repackaged. Logical subsong identity is not yet represented separately.

Per-context distributions cannot pool provider lanes, cache, architecture or
builds. Timeouts/failures count in the attempted denominator; a p95 whose rank
exceeds the observed successful samples remains null. Minimum 100 attempts is
an accounting floor, not a confidence claim or proof of independent samples.
No automatic release verdict. Evidence hashes are identifiers, not verified
oracle contents; connecting the actual host runner and independently verifying
receipts is the next implementation step. Census #106 remains separate.
