"""Research-only seekable Zstd layout sweep; no cache publication or tool execution.

Uses hosted runner Zstd tooling with measured version and binary identity.
Derived frames are ephemeral; timings are synthetic local read traces, not
production analyzer or audio latency.
"""
import argparse
import bisect
from collections import OrderedDict
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import shutil
import statistics
import subprocess
import tarfile
import time

from tools.llvm_archive_selection import materialize, verify
from tools.zstd_frame_probe import probe, need

SOURCE_SHA = "7623f582089983c1cb51030cf1996514b19464e5669099571b399e8543770155"
MIB = 1024 * 1024
LEVEL = 3
CACHE = 64 * MIB
FRAMES = (64 * 1024, 256 * 1024, MIB, 4 * MIB, 16 * MIB, 64 * MIB)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Zstd:
    def __init__(self, library):
        self.lib = C.CDLL(str(library))
        for name, args, result in (
            ("ZSTD_versionString", [], C.c_char_p),
            ("ZSTD_compressBound", [C.c_size_t], C.c_size_t),
            ("ZSTD_compress", [C.c_void_p, C.c_size_t, C.c_char_p, C.c_size_t, C.c_int], C.c_size_t),
            ("ZSTD_decompress", [C.c_void_p, C.c_size_t, C.c_char_p, C.c_size_t], C.c_size_t),
            ("ZSTD_isError", [C.c_size_t], C.c_uint),
            ("ZSTD_getErrorName", [C.c_size_t], C.c_char_p),
        ):
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.version = self.lib.ZSTD_versionString().decode()

    def checked(self, n):
        need(not self.lib.ZSTD_isError(n), self.lib.ZSTD_getErrorName(n).decode())
        return n

    def compress(self, data):
        out = C.create_string_buffer(self.lib.ZSTD_compressBound(len(data)))
        n = self.checked(self.lib.ZSTD_compress(out, len(out), data, len(data), LEVEL))
        return out.raw[:n]

    def decompress(self, data, size):
        out = C.create_string_buffer(size)
        n = self.checked(self.lib.ZSTD_decompress(out, size, data, len(data)))
        need(n == size, "Frame decoded length mismatch")
        return out.raw


def selected_tar(source, output, decoder):
    root = output.parent / "selected"
    root.mkdir()
    with subprocess.Popen([str(decoder), "-dc", "--", str(source)], stdout=subprocess.PIPE) as proc:
        try:
            chosen = materialize(proc.stdout, root)
            while proc.stdout.read(MIB):
                pass
            need(proc.wait() == 0, "Decoder failed")
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    verify(root, chosen)
    members = []
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for name in chosen:
            p = root / name
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = p.stat().st_size, 0o644, 0
            with p.open("rb") as f:
                archive.addfile(info, f)
    with tarfile.open(output) as archive:
        for m in archive:
            members.append((m.name, m.offset_data, m.size))
    shutil.rmtree(root)
    return members


def spans(length, frame, boundaries, hybrid):
    start = 0
    while start < length:
        stop = min(length, start + frame)
        if hybrid and stop < length:
            i = bisect.bisect_right(boundaries, stop) - 1
            if i >= 0 and boundaries[i] >= start + frame // 4:
                stop = boundaries[i]
        yield start, stop
        start = stop


def traces(members, raw):
    rng = random.Random(77)
    small = [(o, n) for name, o, n in members if "/include/" in name and n]
    large = [(o, n) for _, o, n in members if n >= MIB]
    need(small and large, "Missing benchmark populations")
    reads = {"header": [], "random_page": [], "sequential": []}
    for _ in range(512):
        o, n = rng.choice(small)
        reads["header"].append((o, min(n, 64 * 1024)))
        o, n = rng.choice(large)
        reads["random_page"].append((o + rng.randrange(n - 4096 + 1), 4096))
    for o, n in large:
        for delta in range(0, min(n, 16 * MIB), MIB):
            reads["sequential"].append((o + delta, min(MIB, n - delta)))
    with raw.open("rb") as f:
        for key, requests in reads.items():
            verified = []
            for offset, size in requests:
                f.seek(offset)
                data = f.read(size)
                need(len(data) == size, "Trace outside TAR")
                verified.append((offset, size, hashlib.sha256(data).digest()))
            reads[key] = verified
    return reads


def replay(codec, path, index, trace):
    starts = [row[0] for row in index]
    cache = OrderedDict()
    cached = peak = decoded = transferred = hits = misses = useful = 0
    latencies = []
    cpu = time.process_time()
    with path.open("rb") as f:
        for offset, size, expected in trace:
            start = time.perf_counter()
            remaining = size
            pieces = []
            while remaining:
                i = bisect.bisect_right(starts, offset) - 1
                u, n, c, z = index[i]
                if i in cache:
                    data = cache.pop(i)
                    cache[i] = data
                    hits += 1
                else:
                    misses += 1
                    f.seek(c)
                    compressed = f.read(z)
                    need(len(compressed) == z, "Truncated benchmark frame")
                    data = codec.decompress(compressed, n)
                    decoded += n
                    transferred += z
                    while cache and cached + n > CACHE:
                        _, evicted = cache.popitem(last=False)
                        cached -= len(evicted)
                    if n <= CACHE:
                        cache[i] = data
                        cached += n
                        peak = max(peak, cached)
                take = min(remaining, u + n - offset)
                need(take > 0, "Invalid frame span")
                pieces.append(data[offset-u:offset-u+take])
                offset += take
                remaining -= take
            need(hashlib.sha256(b"".join(pieces)).digest() == expected, "Read parity failure")
            useful += size
            latencies.append((time.perf_counter() - start) * 1000)
    ordered = sorted(latencies)
    pct = lambda p: ordered[min(len(ordered)-1, int(p * len(ordered)))]
    return {"requests": len(trace), "useful_bytes": useful, "compressed_read_bytes": transferred,
            "decoded_bytes": decoded, "decode_amplification": decoded / useful,
            "hits": hits, "misses": misses, "peak_cached_bytes": peak,
            "first_ms": latencies[0], "p50_ms": statistics.median(latencies),
            "p95_ms": pct(.95), "p99_ms": pct(.99), "sum_ms": sum(latencies),
            "cpu_seconds": time.process_time() - cpu}


def run_layout(codec, raw, packed, members, reads, frame, hybrid):
    boundaries = sorted({0, raw.stat().st_size, *(o + ((n + 511)//512)*512 for _, o, n in members)})
    index = []
    offset = 0
    start = time.perf_counter()
    cpu = time.process_time()
    with raw.open("rb") as f, packed.open("xb") as out:
        for lo, hi in spans(raw.stat().st_size, frame, boundaries, hybrid):
            data = f.read(hi-lo)
            need(len(data) == hi-lo, "Truncated benchmark TAR")
            compressed = codec.compress(data)
            out.write(compressed)
            index.append((lo, hi-lo, offset, len(compressed)))
            offset += len(compressed)
    encode_wall = time.perf_counter() - start
    encode_cpu = time.process_time() - cpu
    # Verify every frame in order against the canonical TAR, not only sampled reads.
    start = time.perf_counter()
    with packed.open("rb") as f:
        h = hashlib.sha256()
        for _, n, c, z in index:
            f.seek(c)
            h.update(codec.decompress(f.read(z), n))
    need(h.hexdigest() == digest(raw), "Full reconstructed TAR digest mismatch")
    full_verify = time.perf_counter() - start
    result = {"frame_bytes": frame, "layout": "hybrid" if hybrid else "fixed",
              "frames": len(index), "compressed_bytes": offset,
              "seek_table_bytes_with_checksums": 17+12*len(index),
              "ratio": offset/raw.stat().st_size, "encode_seconds": encode_wall,
              "encode_cpu_seconds": encode_cpu, "full_roundtrip_verify_seconds": full_verify,
              "reads": {key: replay(codec, packed, index, trace) for key, trace in reads.items()}}
    packed.unlink()
    print("ZSTD-BENCH " + json.dumps(result, sort_keys=True), flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", required=True, type=Path)
    p.add_argument("--decoder", required=True, type=Path)
    p.add_argument("--library", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--work", required=True, type=Path)
    a = p.parse_args()
    need(digest(a.archive) == SOURCE_SHA, "Upstream LLVM digest mismatch")
    layout = probe(a.archive)
    print("ZSTD-PROBE " + json.dumps({k:v for k,v in layout.items() if k != "frames"}), flush=True)
    codec = Zstd(a.library)
    identity = {"library_sha256": digest(a.library), "library_version": codec.version,
                "decoder_sha256": digest(a.decoder),
                "decoder_version": subprocess.check_output([str(a.decoder), "--version"], text=True).strip(),
                "source_sha256": SOURCE_SHA, "level": LEVEL, "threads": 1,
                "cache_budget_bytes": CACHE, "source_commit": os.environ.get("GITHUB_SHA")}
    a.work.mkdir()
    raw = a.work / "selected.tar"
    start = time.perf_counter()
    members = selected_tar(a.archive, raw, a.decoder)
    preparation = time.perf_counter() - start
    reads = traces(members, raw)
    results = []
    for frame, hybrid in [(f, False) for f in FRAMES] + [(MIB, True), (4*MIB, True)]:
        results.append(run_layout(codec, raw, a.work/"frames.bin", members, reads, frame, hybrid))
    report = {"schema": 1, "qualification": "research_only", "identity": identity,
              "upstream_layout": layout, "selected_tar_bytes": raw.stat().st_size,
              "selected_tar_sha256": digest(raw), "selected_members": len(members),
              "preparation_seconds": preparation, "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "results": results, "limitations": ["Linux runner; synthetic deterministic traces, not real analyzer/audio access",
              "No network range tests, no FUSE, no production artifact, runner tool identities measured not independently pinned",
              "Independent frames plus in-memory index, not serialized official seek table; table size modeled"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    raw.unlink()


if __name__ == "__main__":
    main()
