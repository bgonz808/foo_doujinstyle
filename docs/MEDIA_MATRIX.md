# Offline media compatibility and performance fixtures

This is a deterministic generator and loopback HTTP test, **not a foobar2000
playback benchmark**. The native catalog classifies 30 audio file extensions
but delegates decoding to installed foobar2000 decoders. Our remote archive
reader implements ZIP STORED and ZIP DEFLATE only. Recognition of RAR, 7z,
gzip, or a codec suffix does not imply remote playback support.

## Running locally

Run Python from the repository root:
- python3 -m tools.quality.unittest_runner --start tools --pattern test_media_matrix.py --output build-media/tests/media-matrix.xml --suite-name media-matrix
- python3 -m tools.media_matrix --output build-media --include-ffmpeg --repeats 3

Python 3.10+ is required. FFmpeg and ffprobe are optional for the core WAV
fixtures; CI explicitly requires both when generating the codec matrix.
The manifest records FFmpeg version and binary SHA-256. Different versions
may produce different encoded bytes; hashes are per-build provenance, not
a claim of byte-identical cross-version builds.

All content is synthetic. WAV contains four deterministic 0.5-second audio
markers. The FFmpeg matrix generates FLAC, MP3 ID3v2.3 and v2.4, Vorbis/Ogg,
Opus/Ogg, AAC ADTS, AAC/M4A, ALAC/M4A, WavPack, FLAC/Matroska,
PCM/AIFF, and WebM/Opus (an explicit classification gap). Each *classified*
candidate is stored as identical direct bytes and ZIP STORED/DEFLATE member
bytes, with UTF-8 Japanese names and a text metadata sidecar.
Negative fixtures include incorrect ZIP CRC and unsupported ZIP BZIP2.

Loopback mocks implement finite/open/suffix ranges, conditional If-Range and
ETag, 416, loopback-only redirects, 429, 503, delayed and throttled responses,
range-ignoring servers, forced connection closes and truncated bodies.
No production provider, media, cookies, signed URLs or credentials are used.

## Generated evidence

- build-media/manifest.json: generated/not-generated per codec/container,
  tool and source identity, and exact SHA-256/size of each asset.
- build-media/transport-results.json: request bytes/count and timing for
  each direct-vs-archive pair, with three counterbalanced repetitions and
  medians; p95 remains null below 20 samples.
- build-media/tests/media-matrix.xml: standard JUnit outcomes for negative
  and positive fixture/HTTP tests.
- build-media/summary.md: brief CI summary.

The transport measurement uses Python's HTTP client/server and ZIP extractor,
not native .djt playback. It does not measure first PCM, audible output,
seek-to-audio, native memory/CPU, or real provider latency. Such values are
explicitly null. Paired same-run direct-vs-archive tests reduce *some* runner
variance; they do not eliminate scheduling, cache, CPU or network effects.
Absolute latency is never a PR gate based on this lab.

## Real capability gaps and future additions

The checked-in support-matrix file is quality/media-support.json. Its list of
30 classified audio extensions is checked against native/zip_inventory.hpp.
Host-only review gaps include .webm, .w64, .rf64, .au, .snd, .ts, .spx,
.dts, .mp1 and .aifc. More codecs need specialized fixture generation:
TAK, Monkey's Audio, Musepack, TTA, DSF/DFF, AC3, tracking/chiptune formats,
WMA, XMA, and exotic depths/sample rates. Their suffix classification does
not establish installed decoder support.

Metadata needs separate actual decoding/round-trip assertions across ID3v1,
ID3v2.3/2.4, Vorbis comments, OpusTags, MP4 atoms, RIFF INFO, FLAC PICTURE,
APEv2, ASF, CUE and Japanese ZIP CP932/Unicode Path 0x7075 (#20).
A generated tag is a fixture candidate, not yet verified in foobar2000.

Remote format expansion requires deliberate design and security acceptance:
RAR4/RAR5, 7z and solid archives, gzip, tar, split/multipart, encrypted ZIP,
ZIP64 >4 GiB, non-DEFLATE ZIP methods, nested archives, collision/traversal,
and decompression bombs. foobar2000's built-in optional archive reader may
support an archive that our remote archive transport cannot stream.

## Host-performance follow-up (#10, #17, #18)

A pinned Windows Robot/FlaUI runner will consume the same manifest and assets,
using injected provider mocks and an isolated foobar2000 profile. Then add
instrumented native DLL/worker event telemetry, WASAPI loopback first-audio
and seek markers, range ownership, process resource counters, Wavebar/Minibar
coexistence, cold/warm caches and cancellation. Host results have a distinct
schema and are joined to Robot/JUnit scenario IDs and artifact hashes.
No Python-loopback timing may be relabeled as foobar2000 performance.
