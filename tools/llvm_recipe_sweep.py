"""Reproduce the upstream LLVM compression recipe, then bound independent frames.

Research only. No candidate artifact is published or executed. The exact
upstream recipe is piped with unknown input size; independent frames declare
size and use one worker and a matching job/window cap to bound memory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import struct
import subprocess
import time

from tools.zstd_compression_sweep import LOGICAL_SHA, LOGICAL_BYTES, decoded_identity
from tools.zstd_frame_bench import SOURCE_SHA, MIB, digest, upstream_decode_args
from tools.zstd_frame_probe import probe, need

RECIPE_COMMIT = "5b10d2d74929eae78912771222c9855af97496f4"


def memory_available():
    with open("/proc/meminfo") as f:
        return {line.split(":")[0]: int(line.split()[1])*1024 for line in f
                if line.startswith(("MemTotal:", "MemAvailable:"))}


def compression_args(decoder, frame_mib, size=None):
    if frame_mib == 0:
        return [str(decoder), "--ultra", "-21", "--long=30", "-T4", "-B1024M", "-c"]
    need(frame_mib in (128,256,512,1024) and 0 < size <= frame_mib*MIB, "Invalid frame policy")
    return [str(decoder),"--ultra","-21",f"--long={20+frame_mib.bit_length()-1}",
            "-T1",f"-B{frame_mib}M",f"--stream-size={size}","-c"]


def encode(raw, candidate, decoder, frame_mib, limit):
    def cap():
        resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
    start=time.monotonic()
    rows=[]
    with raw.open("rb") as f, candidate.open("xb") as out:
        if not frame_mib:
            # stdin is deliberately used, as in upstream xz -dc | zstd.
            # Pipe rather than redirect a regular fd (CLI can inspect fd size).
            sizes=[LOGICAL_BYTES]
        else:
            sizes=[min(frame_mib*MIB,LOGICAL_BYTES-i)
                   for i in range(0,LOGICAL_BYTES,frame_mib*MIB)]
        for size in sizes:
            begin=out.tell()
            args=compression_args(decoder,frame_mib,size)
            with subprocess.Popen(args,stdin=subprocess.PIPE,stdout=out,preexec_fn=cap) as proc:
                try:
                    remaining=size
                    while remaining:
                        data=f.read(min(MIB,remaining))
                        need(bool(data),"Truncated logical input")
                        proc.stdin.write(data)
                        remaining-=len(data)
                    proc.stdin.close()
                    need(proc.wait()==0,"Compression failed under resource policy")
                except BaseException:
                    proc.kill()
                    proc.wait()
                    raise
            rows.append((out.tell()-begin,size))
        need(not f.read(1),"Trailing logical input")
        if frame_mib:
            table=b"".join(struct.pack("<II",c,u) for c,u in rows)
            table+=struct.pack("<IBI",len(rows),0,0x8F92EAB1)
            out.write(struct.pack("<II",0x184D2A5E,len(table)))
            out.write(table)
    return rows,time.monotonic()-start


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("archive","decoder","work","output"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--frame-mib",type=int,choices=(0,128,256,512,1024),required=True)
    a=p.parse_args()
    need(digest(a.archive)==SOURCE_SHA,"Upstream digest mismatch")
    a.work.mkdir()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    raw=a.work/"full.tar"
    with raw.open("xb") as out:
        decoded_identity(upstream_decode_args(a.archive,a.decoder,probe(a.archive)),out)
    mem=memory_available()
    # Reserve 4GiB for OS, Python, page cache and other runner tasks.
    limit=min(12*1024*MIB,mem["MemAvailable"]-4096*MIB)
    need(limit>=4*1024*MIB,"Insufficient available RAM for bounded experiment")
    candidate=a.work/"candidate.zst"
    report={"qualification":"research_only","upstream_recipe_commit":RECIPE_COMMIT,
            "source_sha256":SOURCE_SHA,"logical_sha256":LOGICAL_SHA,"frame_mib":a.frame_mib,
            "decoder_sha256":digest(a.decoder),
            "decoder_version":subprocess.check_output([str(a.decoder),"--version"],text=True).strip(),
            "encoder_args":compression_args(a.decoder,a.frame_mib,min(LOGICAL_BYTES,a.frame_mib*MIB) if a.frame_mib else None),
            "memory_before":mem,"encoder_address_space_limit_bytes":limit}
    try:
        rows,elapsed=encode(raw,candidate,a.decoder,a.frame_mib,limit)
        usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        layout=probe(candidate)
        need(len(rows)==layout["frame_count"],"Unexpected frame count")
        need(all(c==row["compressed_bytes"] and row["window_bytes"]<=1024*MIB
                 and row["dictionary_id"]==0 and
                 (not a.frame_mib or row["content_bytes"]==u)
                 for (c,u),row in zip(rows,layout["frames"])),"Frame identity mismatch")
        need(bool(layout["seek_table"])==bool(a.frame_mib),"Seek table mismatch")
        start=time.monotonic()
        decoded_identity([str(a.decoder),"-dc","--long=30","--",str(candidate)])
        report.update({"status":"verified","physical_sha256":digest(candidate),
                       "physical_matches_upstream":digest(candidate)==SOURCE_SHA,
                       "compressed_bytes":candidate.stat().st_size,"logical_bytes":LOGICAL_BYTES,
                       "encode_seconds":elapsed,"decode_verify_seconds":time.monotonic()-start,
                       "child_peak_rss_kib_cumulative":usage.ru_maxrss,
                       "child_cpu_seconds_cumulative":usage.ru_utime+usage.ru_stime,
                       "frame_count":layout["frame_count"],"max_window_bytes":max(x["window_bytes"] for x in layout["frames"]),
                       "seek_table":layout["seek_table"]})
    except BaseException as exc:
        report.update({"status":"failed","error":str(exc)})
        raise
    finally:
        print("UPSTREAM-RECIPE "+json.dumps(report,sort_keys=True),flush=True)
        a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
        candidate.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)


if __name__=="__main__":
    main()
