"""Independent full-TAR equivalence gate. Candidate is data, never executable.

No transformer code or manifest is imported. The expected logical digest is
computed from a separately acquired, pinned upstream archive. This module
produces unsigned research evidence only, not a trust or promotion assertion.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess

UPSTREAM = "7623f582089983c1cb51030cf1996514b19464e5669099571b399e8543770155"
FRAME = 4 * 1024**2
LIMIT = 5 * 1024**3
COMPRESSED_LIMIT = FRAME + 1024**2
MAX_FRAMES = LIMIT // FRAME + 1


def require(ok, why):
    if not ok:
        raise ValueError(why)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def exact(f, n):
    b = f.read(n)
    require(len(b) == n, "Truncated candidate")
    return b


def read_table(f, length):
    require(17 <= length <= LIMIT, "Candidate size bound")
    f.seek(length-9)
    count, flags, magic = struct.unpack("<IBI", exact(f, 9))
    require(magic == 0x8F92EAB1 and flags == 0 and 0 < count <= MAX_FRAMES,
            "Invalid seek footer or unsupported flags")
    table_bytes = count*8+9
    start = length-table_bytes-8
    require(start > 0, "Seek table overlaps frames")
    f.seek(start)
    require(struct.unpack("<II", exact(f, 8)) == (0x184D2A5E, table_bytes),
            "Invalid seek table header")
    rows = [struct.unpack("<II", exact(f, 8)) for _ in range(count)]
    require(all(0 < c <= COMPRESSED_LIMIT and 0 < u <= FRAME for c,u in rows),
            "Frame size outside policy")
    require(all(u == FRAME for _,u in rows[:-1]), "Unexpected non-final frame length")
    require(sum(c for c,_ in rows) == start and sum(u for _,u in rows) <= LIMIT,
            "Seek table coverage mismatch")
    return rows, start


class Decoder:
    def __init__(self, library):
        self.lib = C.CDLL(str(library))
        for name, args, result in (
            ("ZSTD_versionString", [], C.c_char_p),
            ("ZSTD_findFrameCompressedSize", [C.c_char_p, C.c_size_t], C.c_size_t),
            ("ZSTD_getFrameContentSize", [C.c_char_p, C.c_size_t], C.c_ulonglong),
            ("ZSTD_getDictID_fromFrame", [C.c_char_p, C.c_size_t], C.c_uint),
            ("ZSTD_decompress", [C.c_void_p,C.c_size_t,C.c_char_p,C.c_size_t], C.c_size_t),
            ("ZSTD_isError", [C.c_size_t], C.c_uint),
        ):
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result

    def decode(self, compressed, size):
        require(compressed[:4] == bytes.fromhex("28b52ffd"), "Non-data frame")
        require(self.lib.ZSTD_findFrameCompressedSize(compressed,len(compressed)) == len(compressed),
                "Frame boundary mismatch")
        require(self.lib.ZSTD_getFrameContentSize(compressed,len(compressed)) == size,
                "Declared frame size mismatch")
        require(self.lib.ZSTD_getDictID_fromFrame(compressed,len(compressed)) == 0,
                "External dictionary forbidden")
        # Single-shot bounded output, no externally provided dictionary.
        # Reject windows beyond fixed frame policy before decoder invocation.
        desc = compressed[4]
        require(not desc & 8, "Reserved frame bit")
        if not desc & 32:
            wd = compressed[5]
            base = 1 << (10 + (wd >> 3))
            require(base + (base >> 3)*(wd & 7) <= FRAME, "Frame window exceeds policy")
        out = C.create_string_buffer(size)
        n = self.lib.ZSTD_decompress(out,size,compressed,len(compressed))
        require(not self.lib.ZSTD_isError(n) and n == size, "Frame decode/length failure")
        return out.raw


def source_identity(source, decoder):
    require(digest(source) == UPSTREAM, "Upstream digest mismatch")
    h, size = hashlib.sha256(), 0
    with subprocess.Popen([str(decoder), "-dc", "--long=30", "--", str(source)],
                          stdout=subprocess.PIPE) as proc:
        try:
            while data := proc.stdout.read(1024**2):
                size += len(data)
                require(size <= LIMIT, "Upstream decoded size bound")
                h.update(data)
            require(proc.wait() == 0 and size > 0, "Upstream decode failure")
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    return h.hexdigest(), size


def verify_candidate(candidate, codec, expected):
    logical, physical = hashlib.sha256(), hashlib.sha256()
    entries, uoff = [], 0
    with candidate.open("rb") as f:
        length = candidate.stat().st_size
        rows, table_start = read_table(f, length)
        f.seek(0)
        for c,u in rows:
            offset = f.tell()
            data = exact(f,c)
            physical.update(data)
            decoded = codec.decode(data,u)
            logical.update(decoded)
            entries.append({"compressed_offset":offset, "compressed_bytes":c,
                            "logical_offset":uoff,"logical_bytes":u,
                            "compressed_sha256":hashlib.sha256(data).hexdigest(),
                            "logical_sha256":hashlib.sha256(decoded).hexdigest()})
            uoff += u
        require(f.tell() == table_start, "Unexpected frame boundary")
        table = exact(f,length-table_start)
        physical.update(table)
        require(not f.read(1), "Trailing bytes")
    require((logical.hexdigest(),uoff) == expected, "Logical upstream equivalence failure")
    return {"schema":1,"qualification":"unsigned_research_equivalence",
            "upstream_sha256":UPSTREAM,"logical_sha256":logical.hexdigest(),
            "logical_bytes":uoff,"candidate_sha256":physical.hexdigest(),
            "candidate_bytes":length,"seek_table_sha256":hashlib.sha256(table).hexdigest(),
            "frames":entries}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source","candidate","decoder","library","output"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    expected=source_identity(a.source,a.decoder)
    codec=Decoder(a.library)
    result=verify_candidate(a.candidate,codec,expected)
    result["verifier_library_sha256"]=digest(a.library)
    result["verifier_library_version"]=codec.lib.ZSTD_versionString().decode()
    result["verifier_decoder_sha256"]=digest(a.decoder)
    result["verification_policy"]="full_tar_exact_bytes_fixed_4MiB_v1"
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open("x",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,indent=2)
    print("CUSTODY-VERIFY " + json.dumps({k:v for k,v in result.items() if k!="frames"},sort_keys=True))


if __name__=="__main__":
    main()
