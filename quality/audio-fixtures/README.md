# Audio fixture corpus — data, not a toolchain cache

This directory contains 12 synthetic, audio-only encoded samples (307,257 bytes total) named by the SHA-256 of their actual bytes. The 13th case, PCM WAV, is regenerated from Python. No video stream, video encoder, codec executable, dynamic library, or untrusted Actions cache is needed for the ordinary media workflow. Audio-only MP4, Matroska and the explicitly unclassified Opus/WebM case are container coverage, not video scope.

## Source and trust boundary

The bytes were extracted from the exact previously qualified v0.0.207 [workflow run](https://github.com/bgonz808/foo_doujinstyle/actions/runs/45329962157), artifact 14891917147, after independently checking the archive SHA-256 and its manifest. The lock records the original PR head, merge-test source commit, tool binary hashes, tool versions, original manifest digest and every sample's size, SHA-256 and recorded codec probe. This is reviewed, Git-tracked test data with recorded CI origin, **not** a signed claim that the earlier FFmpeg installation was reproducible or a release attestation. The historical encoder is not executed by the current job.

The ordinary matrix verifies the lock, exact case inventory, encoding recipe, PCM seed, and every blob hash; regenerates direct/ZIP STORE/ZIP DEFLATE and negative archives; then reruns loopback transport, byte-hash, CRC and fault oracles. No missing or changed sample silently falls back to a download. The lock and blob changes require the same review as test-code changes. An attacker able to modify both the repository's tests and their lock is outside the cache integrity boundary.

## Refresh contract

Only refresh when a codec/metadata/seed/encoding recipe or fixture requirement changes. Use explicitly pinned and independently qualified **audio-only** tooling, or a minimal audio-only source build with verifiable dependency inputs and provenance. Do not reinstall broad distro FFmpeg on every PR, and do not use a floating binary release. Preserve the encoder and probe binary hashes, exact source/workflow identity, and signed dependency/build provenance where available. The explicit regeneration entry point is `python3 -m tools.media_matrix --output build-media-refresh --include-ffmpeg --repeats 3`; the separate live-encoder smoke requires `MEDIA_MATRIX_REGENERATE=1`. These are developer refresh operations, not the regular CI path. Review all newly generated samples against the previous lock, update their SHA-256 names and provenance, and rerun the normal frozen matrix. Never claim a fresh codec probe on a frozen sample; the recorded probe is historical.

The current corpus removes encoder installation entirely. A fully pinned audio-only refresh toolchain and signed refresh provenance remain follow-up acceptance under #50, rather than blocking ordinary source iteration on acquiring an encoder.
