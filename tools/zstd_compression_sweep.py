"""Controlled whole-upstream-TAR compression sweep, research only.

No derived payload is published. Each representation is decoded again and
checked against the independently recorded full logical SHA256 and length.
The single-frame control uses CLI; chunk cases use the same runner's libzstd.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time

from tools.zstd_frame_bench import SOURCE_SHA, MIB, digest, upstream_decode_args
from tools.zstd_frame_probe import probe, need

LOGICAL_SHA = "8766b3093a94beabfa50fd14858917263773ecfa029b2ecd02ae9fb1c2ff4d2c"
LOGICAL_BYTES = 4283111424


def decoded_identity(args, output=None):
    h = hashlib.sha256()
    total = 0
    with subprocess.Popen(args, stdout=subprocess.PIPE) as proc:
        try:
            while data := proc.stdout.read(MIB):
                total += len(data)
                need(total <= LOGICAL_BYTES, "Decoded output bound")
                h.update(data)
                if output is not None:
                    output.write(data)
            need(proc.wait() == 0, "Decoder failed")
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    need((h.hexdigest(),total) == (LOGICAL_SHA,LOGICAL_BYTES), "Full TAR identity mismatch")


class Encoder:
    def __init__(self, library, level, long_log):
        self.lib = C.CDLL(str(library))
        for name, args, result in (
            ("ZSTD_versionString", [], C.c_char_p),
            ("ZSTD_createCCtx", [], C.c_void_p),
            ("ZSTD_freeCCtx", [C.c_void_p], C.c_size_t),
            ("ZSTD_CCtx_setParameter", [C.c_void_p,C.c_int,C.c_int], C.c_size_t),
            ("ZSTD_compressBound", [C.c_size_t], C.c_size_t),
            ("ZSTD_compress2", [C.c_void_p,C.c_void_p,C.c_size_t,C.c_char_p,C.c_size_t], C.c_size_t),
            ("ZSTD_isError", [C.c_size_t], C.c_uint),
            ("ZSTD_getErrorName", [C.c_size_t], C.c_char_p),
        ):
            fn=getattr(self.lib,name)
            fn.argtypes,fn.restype=args,result
        self.ctx=self.lib.ZSTD_createCCtx()
        need(bool(self.ctx),"Could not allocate compression context")
        # Stable zstd.h enum values: compressionLevel, windowLog,
        # enableLongDistanceMatching, nbWorkers.
        self.set(100,level)
        self.set(400,0)
        self.set(160,1 if long_log else 2)  # ZSTD_ps_enable / ZSTD_ps_disable
        if long_log:
            self.set(101,long_log)

    def checked(self, n):
        need(not self.lib.ZSTD_isError(n), self.lib.ZSTD_getErrorName(n).decode())
        return n

    def set(self,key,value):
        self.checked(self.lib.ZSTD_CCtx_setParameter(self.ctx,key,value))

    def compress(self,data):
        out=C.create_string_buffer(self.lib.ZSTD_compressBound(len(data)))
        n=self.checked(self.lib.ZSTD_compress2(self.ctx,out,len(out),data,len(data)))
        return out.raw[:n]

    def close(self):
        if self.ctx:
            self.lib.ZSTD_freeCCtx(self.ctx)
            self.ctx=None


def case(raw, packed, decoder, library, level, frame_mib, long_log):
    wall=time.monotonic()
    cpu=time.process_time()
    child_before=resource.getrusage(resource.RUSAGE_CHILDREN)
    if frame_mib == 0:
        args=[str(decoder),"-q","--single-thread",f"-{level}","-c"]
        if long_log:
            args.append(f"--long={long_log}")
        # Levels 3/9 use default CLI window and LDM policy for controls.
        with packed.open("xb") as out:
            subprocess.run(args+["--",str(raw)],stdout=out,check=True)
    else:
        enc=Encoder(library,level,long_log)
        try:
            with raw.open("rb") as f, packed.open("xb") as out:
                while data:=f.read(frame_mib*MIB):
                    out.write(enc.compress(data))
        finally:
            enc.close()
    elapsed=time.monotonic()-wall
    child_after=resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds=time.process_time()-cpu+child_after.ru_utime+child_after.ru_stime-child_before.ru_utime-child_before.ru_stime
    layout=probe(packed)
    frames=layout["frames"]
    need(len(frames)==(1 if frame_mib==0 else (LOGICAL_BYTES+frame_mib*MIB-1)//(frame_mib*MIB)),
         "Unexpected independent frame count")
    need(not layout["skippable_frames"] and all(row["dictionary_id"]==0 for row in frames),
         "Unexpected frame structure")
    start=time.monotonic()
    decoded_identity([str(decoder),"-dc","--long=30","--",str(packed)])
    row={"level":level,"frame_mib":frame_mib,"long_window_log":long_log,
         "frames":len(frames),"compressed_bytes":packed.stat().st_size,
         "modeled_seek_table_bytes":17+8*len(frames) if frame_mib else 0,
         "ratio":packed.stat().st_size/LOGICAL_BYTES,
         "upstream_size_multiple":packed.stat().st_size/276226344,
         "max_declared_window_bytes":max(x["window_bytes"] for x in frames),
         "encode_seconds":elapsed,"encode_cpu_seconds":cpu_seconds,
         "decode_verify_seconds":time.monotonic()-start,
         "process_peak_rss_kib_cumulative":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
         "child_peak_rss_kib_cumulative":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
         "physical_sha256":digest(packed),"logical_sha256":LOGICAL_SHA,
         "encoder":"cli" if frame_mib==0 else "libzstd"}
    print("ZSTD-FULL "+json.dumps(row,sort_keys=True),flush=True)
    packed.unlink()
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("archive","decoder","library","work","output"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--lane",choices=("3","9","15","long"),required=True)
    a=p.parse_args()
    need(digest(a.archive)==SOURCE_SHA,"Upstream digest mismatch")
    layout=probe(a.archive)
    a.work.mkdir()
    raw=a.work/"full.tar"
    start=time.monotonic()
    with raw.open("xb") as out:
        decoded_identity(upstream_decode_args(a.archive,a.decoder,layout),out)
    prepare=time.monotonic()-start
    if a.lane=="3":
        cases=[(3,x,0) for x in (0,4,16,64,256)]
    elif a.lane=="9":
        cases=[(9,x,0) for x in (0,4,64,256)]
    elif a.lane=="15":
        cases=[(15,x,0) for x in (64,256)]
    else:
        cases=[(9,256,27),(9,0,27)]
    rows=[case(raw,a.work/"candidate.zst",a.decoder,a.library,*params) for params in cases]
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({"qualification":"research_only","source_sha256":SOURCE_SHA,
        "logical_sha256":LOGICAL_SHA,"logical_bytes":LOGICAL_BYTES,"preparation_seconds":prepare,
        "decoder_sha256":digest(a.decoder),"decoder_version":subprocess.check_output([str(a.decoder),"--version"],text=True).strip(),
        "library_sha256":digest(a.library),"source_commit":os.environ.get("GITHUB_SHA"),
        "lane":a.lane,"results":rows,"limitations":["No payload published; whole TAR on local disk for controlled comparison",
        "Single-frame CLI vs chunked libzstd; same codec family, not proof of identical internal auto settings",
        "RSS cumulative within lane, not per-case peak; levels/window auto settings recorded, no production trust",
        "No serialized seek table for this sweep, overhead modeled"]},sort_keys=True,indent=2)+"\n")
    raw.unlink()


if __name__=="__main__":
    main()
