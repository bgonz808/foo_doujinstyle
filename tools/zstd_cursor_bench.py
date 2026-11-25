"""Research-only bounded streaming cursor and decoded-page reuse.

Interleaved requests are serial, not a thread-safety/concurrency qualification.
Compressed data and indexes are generated from the verified local corpus.
No executable or derived artifact is published or consumed.
"""
import argparse
import bisect
from collections import OrderedDict
import ctypes as C
import hashlib
import itertools
import json
from pathlib import Path
import resource
import time

from tools.zstd_frame_bench import (Zstd, SOURCE_SHA, MIB, digest, selected_tar,
                                    traces, replay)
from tools.zstd_frame_probe import probe, need

PAGE=64*1024
CACHE=64*MIB
WINDOW_LOG=23  # level-3 frames, bounded at 8MiB history
MAX_FRAME=64*MIB
MAX_COMPRESSED=MAX_FRAME+MIB


class In(C.Structure):
    _fields_=[("src",C.c_void_p),("size",C.c_size_t),("pos",C.c_size_t)]


class Out(C.Structure):
    _fields_=[("dst",C.c_void_p),("size",C.c_size_t),("pos",C.c_size_t)]


class Codec:
    def __init__(self,library):
        self.lib=C.CDLL(str(library))
        for name,args,result in (
            ("ZSTD_createDCtx",[],C.c_void_p),
            ("ZSTD_freeDCtx",[C.c_void_p],C.c_size_t),
            ("ZSTD_DCtx_setParameter",[C.c_void_p,C.c_int,C.c_int],C.c_size_t),
            ("ZSTD_decompressStream",[C.c_void_p,C.POINTER(Out),C.POINTER(In)],C.c_size_t),
            ("ZSTD_isError",[C.c_size_t],C.c_uint),
            ("ZSTD_getErrorName",[C.c_size_t],C.c_char_p),
            ("ZSTD_sizeof_DCtx",[C.c_void_p],C.c_size_t),
        ):
            fn=getattr(self.lib,name)
            fn.argtypes,fn.restype=args,result

    def check(self,n):
        need(not self.lib.ZSTD_isError(n),self.lib.ZSTD_getErrorName(n).decode())
        return n


class Cursor:
    def __init__(self,codec,frame,blob,length):
        need(0<len(blob)<=MAX_COMPRESSED and 0<length<=MAX_FRAME,"Frame bound")
        need(blob[:4]==bytes.fromhex("28b52ffd"),"Not a Zstd data frame")
        self.codec,self.frame,self.blob,self.length=codec,frame,blob,length
        self.ptr=C.c_char_p(blob)
        self.src=In(C.cast(self.ptr,C.c_void_p),len(blob),0)
        self.pos=0
        self.ctx=codec.lib.ZSTD_createDCtx()
        need(bool(self.ctx),"Decoder allocation failed")
        try:
            codec.check(codec.lib.ZSTD_DCtx_setParameter(self.ctx,100,WINDOW_LOG))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.ctx:
            self.codec.lib.ZSTD_freeDCtx(self.ctx)
            self.ctx=None

    def page(self):
        size=min(PAGE,self.length-self.pos)
        need(size>0,"Cursor exhausted")
        buf=C.create_string_buffer(size)
        out=Out(C.cast(buf,C.c_void_p),size,0)
        while out.pos<size:
            before=(self.src.pos,out.pos)
            ret=self.codec.check(self.codec.lib.ZSTD_decompressStream(self.ctx,C.byref(out),C.byref(self.src)))
            need((self.src.pos,out.pos)!=before,"Decoder stalled")
            if ret==0:
                need(self.pos+out.pos==self.length and self.src.pos==self.src.size,"Unexpected frame end")
                break
        need(out.pos==size,"Short page")
        self.pos+=size
        if self.pos==self.length and ret!=0:
            # Consume any final frame checksum/epilogue without accepting extra output.
            last=C.create_string_buffer(1)
            tail=Out(C.cast(last,C.c_void_p),1,0)
            ret=self.codec.check(self.codec.lib.ZSTD_decompressStream(self.ctx,C.byref(tail),C.byref(self.src)))
            need(ret==0 and tail.pos==0 and self.src.pos==self.src.size,"Frame termination mismatch")
        return buf.raw


class Reader:
    def __init__(self,codec,path,index,slots,budget):
        need(1<=slots<=4 and 0<=budget<=CACHE,"Resource policy")
        self.codec,self.file,self.index=codec,path.open("rb"),index
        self.starts=[x[0] for x in index]
        self.slots,self.budget=slots,budget
        self.cursors=OrderedDict()
        self.cache=OrderedDict()
        self.cached=0
        self.opened=set()
        self.serial=0
        self.stats={k:0 for k in ("session_opens","restarts","session_evictions","cursor_reuses",
                    "page_hits","page_misses","decoded_bytes","compressed_read_bytes",
                    "useful_bytes","peak_cache_bytes","peak_decoder_context_bytes",
                    "peak_retained_compressed_bytes","peak_sessions")}

    def close(self):
        for c in self.cursors.values():
            c.close()
        self.cursors.clear()
        self.file.close()

    def observe(self):
        self.stats["peak_sessions"]=max(self.stats["peak_sessions"],len(self.cursors))
        self.stats["peak_decoder_context_bytes"]=max(self.stats["peak_decoder_context_bytes"],
            sum(self.codec.lib.ZSTD_sizeof_DCtx(c.ctx) for c in self.cursors.values()))
        self.stats["peak_retained_compressed_bytes"]=max(self.stats["peak_retained_compressed_bytes"],
            sum(len(b) for b in {id(c.blob):c.blob for c in self.cursors.values()}.values()))

    def remember(self,key,data):
        if len(data)>self.budget:
            return
        if key in self.cache:
            self.cached-=len(self.cache.pop(key))
        while self.cache and self.cached+len(data)>self.budget:
            _,old=self.cache.popitem(last=False)
            self.cached-=len(old)
        self.cache[key]=data
        self.cached+=len(data)
        self.stats["peak_cache_bytes"]=max(self.stats["peak_cache_bytes"],self.cached)

    def get_page(self,i,start):
        key=(i,start)
        if key in self.cache:
            self.stats["page_hits"]+=1
            data=self.cache.pop(key)
            self.cache[key]=data
            return data
        self.stats["page_misses"]+=1
        viable=[(k,c) for k,c in self.cursors.items() if c.frame==i and c.pos<=start]
        if viable:
            k,c=max(viable,key=lambda item:item[1].pos)
            self.cursors.move_to_end(k)
            self.stats["cursor_reuses"]+=1
        else:
            u,n,off,z=self.index[i]
            need(0<z<=MAX_COMPRESSED and 0<n<=MAX_FRAME,"Frame bound")
            blob=next((c.blob for c in self.cursors.values() if c.frame==i),None)
            if blob is None:
                self.file.seek(off)
                blob=self.file.read(z)
                need(len(blob)==z,"Truncated frame")
                self.stats["compressed_read_bytes"]+=z
            if i in self.opened:
                self.stats["restarts"]+=1
            self.opened.add(i)
            if len(self.cursors)>=self.slots:
                _,old=self.cursors.popitem(last=False)
                old.close()
                self.stats["session_evictions"]+=1
            c=Cursor(self.codec,i,blob,n)
            self.serial+=1
            self.cursors[self.serial]=c
            self.stats["session_opens"]+=1
        self.observe()
        while c.pos<=start:
            offset=c.pos
            data=c.page()
            self.stats["decoded_bytes"]+=len(data)
            self.remember((i,offset),data)
            self.observe()
        return data

    def read(self,offset,size):
        pieces=[]
        self.stats["useful_bytes"]+=size
        while size:
            i=bisect.bisect_right(self.starts,offset)-1
            need(0<=i<len(self.index),"Read outside archive")
            u,n,_,_=self.index[i]
            rel=offset-u
            need(0<=rel<n,"Read outside frame")
            start=rel//PAGE*PAGE
            data=self.get_page(i,start)
            take=min(size,len(data)-(rel-start))
            need(take>0,"Invalid page span")
            pieces.append(data[rel-start:rel-start+take])
            offset+=take
            size-=take
        return b"".join(pieces)


def measure(codec,path,index,trace,slots,budget):
    reader=Reader(codec,path,index,slots,budget)
    wall=time.monotonic()
    cpu=time.process_time()
    lat=[]
    try:
        for offset,size,expected in trace:
            start=time.monotonic()
            need(hashlib.sha256(reader.read(offset,size)).digest()==expected,"Read parity failed")
            lat.append((time.monotonic()-start)*1000)
        row=dict(reader.stats)
        row.update({"sum_ms":sum(lat),"p95_ms":sorted(lat)[min(len(lat)-1,int(.95*len(lat)))],
                    "wall_seconds":time.monotonic()-wall,"cpu_seconds":time.process_time()-cpu,
                    "decode_amplification":row["decoded_bytes"]/row["useful_bytes"]})
        return row
    finally:
        reader.close()


def pack(codec,raw,path,frame):
    index=[]
    h=hashlib.sha256()
    with raw.open("rb") as f,path.open("xb") as out:
        offset=0
        while data:=f.read(frame):
            blob=codec.compress(data)
            h.update(codec.decompress(blob,len(data)))
            index.append((offset,len(data),out.tell(),len(blob)))
            out.write(blob)
            offset+=len(data)
    need(h.hexdigest()==digest(raw),"Full frame roundtrip parity")
    return index


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("archive","decoder","library","work","output"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--frame-mib",type=int,choices=(4,16,64),required=True)
    a=p.parse_args()
    need(digest(a.archive)==SOURCE_SHA,"Upstream digest mismatch")
    a.work.mkdir()
    raw=a.work/"selected.tar"
    members=selected_tar(a.archive,raw,a.decoder,probe(a.archive))
    reads=traces(members,raw)
    # Deterministic interleaving of logically independent consumer streams.
    reads["interleaved"]=[req for batch in itertools.zip_longest(*reads.values())
                          for req in batch if req is not None]
    one=Zstd(a.library)
    codec=Codec(a.library)
    path=a.work/"frames.bin"
    index=pack(one,raw,path,a.frame_mib*MIB)
    rows=[]
    for name,trace in reads.items():
        base=replay(one,path,index,trace,CACHE)
        for slots in (1,2,4):
            result=measure(codec,path,index,trace,slots,CACHE)
            row={"frame_mib":a.frame_mib,"trace":name,"slots":slots,"page_bytes":PAGE,
                 "cache_budget_bytes":CACHE,"whole_frame_baseline":base,"streaming":result}
            rows.append(row)
            print("CURSOR-BENCH "+json.dumps(row,sort_keys=True),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({"qualification":"research_only","source_sha256":SOURCE_SHA,
        "selected_tar_sha256":digest(raw),"library_sha256":digest(a.library),"library_version":one.version,
        "peak_process_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "results":rows,"limitations":["Serial interleaving, not thread concurrency or real host trace",
        "OS file cache not flushed; level3 selected corpus; no production trust",
        "Bounded cache/session count/window, not hard aggregate RSS cap; per-category peaks not simultaneous",
        "Decoder contexts retained within session but not pooled after eviction"]},indent=2,sort_keys=True)+"\n")
    path.unlink()
    raw.unlink()


if __name__=="__main__":
    main()
