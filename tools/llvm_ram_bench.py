"""Compare pinned upstream extraction and verified reads on disk and tmpfs.

Data only; no executable is launched. No mount or kernel driver installation.
The OS cache is not flushed. Independent per-case output trees are removed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import resource
import shutil
import subprocess
import tarfile
import tempfile
import time

from tools.llvm_archive_selection import safe_name, materialize, verify
from tools.zstd_frame_bench import SOURCE_SHA, MIB, digest, upstream_decode_args
from tools.zstd_frame_probe import probe, need


def meminfo():
    with open("/proc/meminfo") as f:
        return {line.split(":")[0]:int(line.split()[1])*1024 for line in f
                if line.startswith(("MemAvailable:","Cached:","Shmem:","Dirty:","Writeback:","MemFree:"))}


def io():
    with open("/proc/self/io") as f:
        return {line.split(":")[0]:int(line.split()[1]) for line in f}


def full_extract(stream, root):
    names,seen,total=[],set(),0
    with tarfile.open(fileobj=stream,mode="r|",bufsize=MIB) as a:
        for m in a:
            p=safe_name(m.name)
            name=p.as_posix()
            need(name.casefold() not in seen,"Duplicate archive path")
            seen.add(name.casefold())
            need(len(seen)<=10000 and 0<=m.size<=5*1024**3,"Archive bounds")
            target=root.joinpath(*p.parts)
            if m.isdir():
                target.mkdir(parents=True,exist_ok=True)
                continue
            need(m.isreg() and not m.issparse(),"Full research extraction refuses links/special files")
            total+=m.size
            need(total<=5*1024**3,"Payload bound")
            target.parent.mkdir(parents=True,exist_ok=True)
            with a.extractfile(m) as f,target.open("xb") as out:
                shutil.copyfileobj(f,out,length=MIB)
            need(target.stat().st_size==m.size,"Truncated file")
            names.append(name)
    return sorted(names)


def extract(source,decoder,layout,root,mode):
    with subprocess.Popen(upstream_decode_args(source,decoder,layout),stdout=subprocess.PIPE) as proc:
        try:
            names=(materialize if mode=="selected" else full_extract)(proc.stdout,root)
            while proc.stdout.read(MIB):
                pass
            need(proc.wait()==0,"Upstream decoder failed")
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    verify(root,names)
    return names


def read_trace(root,names):
    rng=random.Random(77)
    headers=[n for n in names if "/include/" in n and (root/n).stat().st_size>0]
    large=[n for n in names if (root/n).stat().st_size>=MIB]
    need(headers and large,"Trace population absent")
    requests=[]
    for _ in range(512):
        n=rng.choice(headers)
        requests.append((n,0,min(64*1024,(root/n).stat().st_size)))
        n=rng.choice(large)
        requests.append((n,rng.randrange((root/n).stat().st_size-4096+1),4096))
    return requests


def replay(root,requests,cache=None):
    h=hashlib.sha256()
    start=time.monotonic()
    for n,offset,size in requests:
        if cache is not None and n in cache:
            data=cache[n][offset:offset+size]
        else:
            with (root/n).open("rb") as f:
                f.seek(offset)
                data=f.read(size)
        need(len(data)==size,"Short trace read")
        h.update(data)
    return {"seconds":time.monotonic()-start,"sha256":h.hexdigest(),"requests":len(requests),
            "useful_bytes":sum(s for _,_,s in requests)}


def run(source,decoder,layout,base,backend,mode,trial):
    required=(550 if mode=="selected" else 4600)*MIB
    need(shutil.disk_usage(base).free>=required,"Insufficient filesystem free capacity")
    if backend=="tmpfs":
        need(meminfo()["MemAvailable"]>=required+2048*MIB,"Insufficient RAM headroom")
    root=Path(tempfile.mkdtemp(prefix="llvm-ram-",dir=base))
    before,io_before=meminfo(),io()
    try:
        start=time.monotonic()
        names=extract(source,decoder,layout,root,mode)
        extract_seconds=time.monotonic()-start
        after=meminfo()
        files=[root/n for n in names]
        size=sum(p.stat().st_size for p in files)
        dirs=sum(p.is_dir() for p in root.rglob("*"))
        # Content manifest, verified between storage backends and trials.
        h=hashlib.sha256()
        for n,p in zip(names,files):
            h.update(n.encode()+b"\0"+bytes.fromhex(digest(p)))
        manifest=h.hexdigest()
        requests=read_trace(root,names)
        cold=replay(root,requests)
        repeat=replay(root,requests)
        need(cold["sha256"]==repeat["sha256"],"Repeated reads differ")
        # Application cache of selected header files only; bound at 64MiB.
        cache={}
        cached=0
        start=time.monotonic()
        for n in sorted({n for n,_,_ in requests if "/include/" in n}):
            size_n=(root/n).stat().st_size
            if cached+size_n<=64*MIB:
                cache[n]=(root/n).read_bytes()
                cached+=size_n
        cache_seconds=time.monotonic()-start
        direct=replay(root,requests,cache)
        need(direct["sha256"]==cold["sha256"],"Application cache read mismatch")
        io_after=io()
        row={"backend":backend,"mode":mode,"trial":trial,"files":len(files),"directories":dirs,
             "logical_bytes":size,"extract_seconds":extract_seconds,"manifest_sha256":manifest,
             "cold_application_trace":cold,"repeat_trace":repeat,"header_memory_cache_trace":direct,
             "header_cache_bytes":cached,"header_cache_prepare_seconds":cache_seconds,
             "mem_before":before,"mem_after_extract":after,"mem_after_trace":meminfo(),
             "self_io_delta":{k:io_after[k]-io_before[k] for k in io_before},
             "self_peak_rss_kib_cumulative":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             "child_peak_rss_kib_cumulative":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}
    finally:
        start=time.monotonic()
        shutil.rmtree(root)
        cleanup=time.monotonic()-start
    row["cleanup_seconds"]=cleanup
    print("RAM-BENCH "+json.dumps(row,sort_keys=True),flush=True)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("archive","decoder","disk","tmpfs","output"):
        p.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    need(digest(a.archive)==SOURCE_SHA,"Upstream digest mismatch")
    layout=probe(a.archive)
    mount=subprocess.check_output(["findmnt","-n","-o","FSTYPE,OPTIONS","-T",str(a.tmpfs)],text=True).strip()
    need(mount.split()[0]=="tmpfs","RAM path is not tmpfs")
    rows=[]
    for mode in ("selected","full"):
        for trial in (1,2):
            order=("disk","tmpfs") if trial==1 else ("tmpfs","disk")
            for backend in order:
                rows.append(run(a.archive,a.decoder,layout,a.disk if backend=="disk" else a.tmpfs,backend,mode,trial))
        relevant=[x for x in rows if x["mode"]==mode]
        need(len({x["manifest_sha256"] for x in relevant})==1,"Backend content differs")
        need(len({x["cold_application_trace"]["sha256"] for x in relevant})==1,"Backend read trace differs")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({"qualification":"research_only","mount":mount,
        "source_sha256":SOURCE_SHA,"decoder_sha256":digest(a.decoder),"results":rows,
        "limitations":["Linux tmpfs may swap; no physical disk or Windows minifilter instrumentation",
        "OS cache not flushed; cold refers to application trace only; extraction/manifest warms cache",
        "Full stream tree no intermediate TAR; self I/O counters exclude decoder child; not executed PE tools"]},indent=2,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
