"""Research-only full-TAR lossless transformation. No signing or publication."""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
from tools.zstd_frame_bench import Zstd, SOURCE_SHA, digest

FRAME = 4 * 1024**2
LIMIT = 5 * 1024**3


def transform(source, candidate, decoder, library):
    if digest(source) != SOURCE_SHA:
        raise ValueError("Upstream digest mismatch")
    codec = Zstd(library)
    entries, total = [], 0
    logical = hashlib.sha256()
    with subprocess.Popen([str(decoder), "-dc", "--long=30", "--", str(source)],
                          stdout=subprocess.PIPE) as proc:
        try:
            with candidate.open("xb") as out:
                while data := proc.stdout.read(FRAME):
                    total += len(data)
                    if total > LIMIT:
                        raise ValueError("Upstream decoded size bound")
                    logical.update(data)
                    compressed = codec.compress(data)
                    out.write(compressed)
                    entries.append((len(compressed), len(data)))
                if proc.wait() or not entries:
                    raise ValueError("Upstream decoding failed")
                # Official seekable format, no optional XXH64 checksums.
                # Independent verifier constructs cryptographic frame hashes.
                payload = b"".join(struct.pack("<II", *row) for row in entries)
                payload += struct.pack("<IBI", len(entries), 0, 0x8F92EAB1)
                out.write(struct.pack("<II", 0x184D2A5E, len(payload)))
                out.write(payload)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    print(f"CUSTODY-TRANSFORM frames={len(entries)} logical_bytes={total} "
          f"logical_sha256={logical.hexdigest()} physical_sha256={digest(candidate)} "
          f"encoder_version={codec.version} library_sha256={digest(library)}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "candidate", "decoder", "library"):
        p.add_argument("--"+name, type=Path, required=True)
    a = p.parse_args()
    transform(a.source, a.candidate, a.decoder, a.library)


if __name__ == "__main__":
    main()
