"""Bounded, manually authorized production-native catalog/ZIP inspection.

No live host-playback claim. The worker uses the real catalog and provider RPCs.
Raw catalog and signed URLs remain in disposable scratch, never artifacts/logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import contextlib
import re
import statistics
import struct
import zipfile
from e2e.host.runner import run,sha256
from e2e.host.trace import load_events
from e2e.host.paired_report import stage_offsets
from urllib.parse import urlsplit
from native.reference_publisher import ReferencePublisher

MAX_RELEASES=6
MAX_ARCHIVES=3

def digest(data):
    return hashlib.sha256(data).hexdigest()

def stable_digest(obj):
    return digest(json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())

def chart_ids(stages):
    ids=[]
    for stage in stages:
        for release in stage.get("releases",[]):
            ident=release.get("id")
            if type(ident) is int and ident>0 and ident not in ids:
                ids.append(ident)
                if len(ids)==MAX_RELEASES:
                    return ids
    return ids

def mediafire_url(value):
    try:
        p=urlsplit(value)
        host=p.hostname or ""
        return (p.scheme=="https" and p.port in (None,443) and not p.username and not p.password
                and not p.fragment and (host in ("mediafire.com","www.mediafire.com","m.mediafire.com")
                or host.endswith(".mediafire.com")))
    except (ValueError,TypeError):
        return False

def archive_projection(answer):
    inventory=answer.get("inventory",[])
    fmt=answer.get("format")
    if fmt not in ("zip","rar","7z","gzip","unknown","zip_invalid_or_incomplete"):
        fmt="unknown"
    methods={}
    for item in inventory:
        method=item.get("zip_method",item.get("compress_type"))
        if type(method) is int:
            methods[str(method)]=methods.get(str(method),0)+1
    http=answer.get("archive_http",{})
    return {"format":fmt,"archive_bytes":answer.get("archive_bytes"),
            "inventory_count":len(inventory),"inventory_sha256":stable_digest(inventory),
            "compression_methods":methods,
            "http":{k:http.get(k) for k in ("requests","body_bytes")},
            "audio_members":answer.get("audio_members") if type(answer.get("audio_members")) is int else
                len(answer.get("audio",[]))}

def authorized():
    return (os.environ.get("GITHUB_ACTIONS")=="true" and
            os.environ.get("RUNNER_ENVIRONMENT")=="github-hosted" and
            os.environ.get("DOUJIN_LIVE_PROVIDER_SMOKE")=="1" and sys.platform=="win32" and
            os.environ.get("GITHUB_REPOSITORY")=="bgonz808/foo_doujinstyle" and
            os.environ.get("GITHUB_ACTOR")=="bgonz808" and
            (os.environ.get("GITHUB_EVENT_NAME")=="workflow_dispatch" or
             (os.environ.get("GITHUB_EVENT_NAME")=="pull_request" and
              os.environ.get("GITHUB_HEAD_REF")=="test/live-provider-v0.0.281" and
              os.environ.get("GITHUB_RUN_ATTEMPT")=="1" and
              Path("VERSION").read_text().strip()=="0.0.283")))

@contextlib.contextmanager
def scoped_env(**changes):
    saved={k:os.environ.get(k) for k in changes}
    try:
        os.environ.update(changes)
        yield
    finally:
        for k,v in saved.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v

CAP=128*1024*1024
MEMBER_CAP=64*1024*1024
AUDIO_SUFFIXES={".wav",".flac",".mp3",".ogg",".opus",".m4a",".aac"}

def select_member(answer):
    if answer.get("format")!="zip" or type(answer.get("archive_bytes")) is not int or not 0<answer["archive_bytes"]<=CAP:
        return None
    candidates=[]
    for m in answer.get("inventory",[]):
        name=m.get("path")
        suffix=m.get("suffix")
        if (not isinstance(name,str) or any(c in name for c in "\r\n\x00") or
            suffix not in AUDIO_SUFFIXES or m.get("kind")!="audio_candidate" or
            m.get("encrypted") or m.get("zip_method") not in (0,8) or
            type(m.get("uncompressed_bytes")) is not int or not 65536<=m["uncompressed_bytes"]<=MEMBER_CAP or
            type(m.get("compressed_bytes")) is not int or m["compressed_bytes"]<=0 or
            type(m.get("zip_header_offset")) is not int or not 0<=m["zip_header_offset"]<answer["archive_bytes"] or
            not re.fullmatch("[0-9a-fA-F]{8}",str(m.get("crc32","")))):
            continue
        raw=m.get("zip_name",{}).get("raw") or name.encode("utf-8").hex()
        if not re.fullmatch("(?:[0-9a-fA-F]{2}){1,4096}",raw) or b"\0" in bytes.fromhex(raw):
            continue
        candidates.append({**m,"raw_hex":raw})
    if not candidates: return None
    return min(candidates,key=lambda m:(m["zip_header_offset"]>=answer["archive_bytes"]-65557,
                   m["zip_header_offset"],m["uncompressed_bytes"]))

def read_rows(path):
    if not path.is_file(): return []
    rows=[json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    allowed={"id","host","method","status","headers_us","first_body_us","last_body_us","read_calls","body_bytes","elapsed_us","complete",
             "dns_us","connect_us","tls_us","request_to_headers_us","connection_request","connection_first",
             "protocol","range_first","range_last","resource_size","range_sha256"}
    for row in rows:
        host=row.get("host","")
        if (set(row)!=allowed or not re.fullmatch("[a-z0-9.-]{1,253}",host) or
            not (host in ("doujinstyle.com","www.doujinstyle.com","mediafire.com") or host.endswith(".mediafire.com")) or
            row["method"] not in ("GET","POST") or type(row["complete"]) is not bool or
            not re.fullmatch("(?:[0-9a-f]{64})?",row["range_sha256"]) or
            any(type(row[k]) is not int for k in allowed-{"host","method","complete","range_sha256"})):
            raise ValueError("Invalid sanitized HTTP evidence")
    if len({x["id"] for x in rows})!=len(rows): raise ValueError("Duplicate HTTP request ID")
    return sorted(rows,key=lambda x:x["id"])

def service_role(host,phase):
    if host in ("doujinstyle.com","www.doujinstyle.com"): return "catalog"
    if host in ("mediafire.com","www.mediafire.com","m.mediafire.com"): return "hoster_resolution"
    if host.endswith(".mediafire.com"): return "hoster_storage"
    return "unclassified"

def profiles(rows):
    groups={}
    for row in rows:
        key=(row.get("phase","unknown"),service_role(row["host"],row.get("phase")),row["host"],row["method"],row["protocol"],row["connection_first"],row["status"])
        groups.setdefault(key,[]).append(row)
    result=[]
    for key,items in sorted(groups.items()):
        entry=dict(zip(("phase","service_role","host","method","protocol","connection_first","status"),key))
        entry["samples"]=len(items)
        for metric in ("headers_us","first_body_us","last_body_us","elapsed_us","body_bytes","read_calls","dns_us","connect_us","tls_us","request_to_headers_us"):
            v=[x[metric] for x in items if x[metric]>=0]
            entry[metric]={"values":v,"median":statistics.median(v) if v else None}
        entry["headers_to_first_body_us"]=[x["first_body_us"]-x["headers_us"] for x in items if 0<=x["headers_us"]<=x["first_body_us"]]
        entry["body_read_span_us"]=[x["last_body_us"]-x["first_body_us"] for x in items if x["read_calls"]>1 and 0<=x["first_body_us"]<=x["last_body_us"]]
        result.append(entry)
    return result

def make_reference(path,url,release,member):
    if not mediafire_url(url) or any(c in url for c in "\r\n\x00"):
        raise ValueError("Invalid selected share")
    path.write_text("DOUJIN-TRACK 1\nURL="+url+"\nREFERRER=https://doujinstyle.com/?p=page&type=1&id="+str(release)+
        "\nMEMBER="+member["path"]+"\nMEMBER_LOCATOR="+member["raw_hex"]+":"+str(member["zip_header_offset"])+
        ":"+str(int(member["crc32"],16))+"\nFILENAME=control"+member["suffix"]+
        "\nSOURCE_BYTES="+str(member["uncompressed_bytes"])+"\n",encoding="utf-8",newline="\n")

def extract_exact(archive,member,dest):
    with zipfile.ZipFile(archive) as z,archive.open("rb") as src:
        matches=[x for x in z.infolist() if x.header_offset==member["zip_header_offset"] and
                 x.CRC==int(member["crc32"],16) and x.compress_type==member["zip_method"] and
                 x.file_size==member["uncompressed_bytes"] and x.compress_size==member["compressed_bytes"] and
                 not x.flag_bits&1]
        if len(matches)!=1: raise ValueError("Exact inspected member missing")
        item=matches[0]
        src.seek(item.header_offset)
        header=src.read(30)
        if len(header)!=30 or header[:4]!=b"PK\x03\x04": raise ValueError("Local ZIP header invalid")
        size=struct.unpack_from("<H",header,26)[0]
        if src.read(size).hex()!=member["raw_hex"].lower(): raise ValueError("Raw ZIP locator mismatch")
        # Stream only the one chosen member to a fixed local name; no archive path extraction.
        with z.open(item) as source,dest.open("xb") as output:
            count=0
            while block:=source.read(1024*1024):
                count+=len(block)
                if count>MEMBER_CAP: raise ValueError("Local member limit")
                output.write(block)
        if count!=item.file_size: raise ValueError("Local member length mismatch")
    return sha256(dest)

def verify_ranges(archive,rows):
    size=archive.stat().st_size
    checked=0
    with archive.open("rb") as stream:
        for row in rows:
            if row["status"]!=206 or not row["complete"] or not row["range_sha256"]: continue
            first,last=row["range_first"],row["range_last"]
            if not 0<=first<=last<size or row["resource_size"]!=size or last-first+1!=row["body_bytes"]:
                raise ValueError("Streamed range identity mismatch")
            stream.seek(first)
            if digest(stream.read(last-first+1))!=row["range_sha256"]:
                raise ValueError("Streamed range differs from acquired archive")
            checked+=1
    if not checked: raise ValueError("No validated streamed archive ranges")
    return checked

def play(a,root,reference,remote):
    from e2e.host.run_readiness import no_host
    try:
        code=run(a.manifest,a.template,root,profile="live-remote" if remote else "live-local",
            exporter=a.exporter,exporter_sha256=a.exporter_sha256,require_common_pcm=True,
            cli_only=True,configure_disposable=True,hosted_ci=True,external_reference=reference)
    finally:
        no_host()
    checkpoint=json.loads((root/"checkpoint.json").read_text(encoding="utf-8"))
    rows=read_rows(root/"live-http.jsonl")
    result={"status":"failed","run_id":checkpoint["run_id"],"http":rows}
    if code==0:
        events=load_events(root/"trace")
        pid=json.loads((root/"robot/cli-host.json").read_text(encoding="utf-8"))["pid"]
        stages=stage_offsets(events,pid)
        sdk=stages["component.observer.sdk_play_call"]["offset_ns"]
        pcm=stages["component.observer.first_pcm"]["offset_ns"]
        decoder=stages["component.decoder.first_pcm"]["offset_ns"]
        if sdk is not None and pcm is not None and pcm>=sdk and (
                (remote and decoder is not None and any(x["status"]==206 and x["range_sha256"] for x in rows)) or
                (not remote and not rows and decoder is None)):
            result.update(status="passed",sdk_play_to_pcm_ns=pcm-sdk,intent_to_pcm_ns=pcm,stages=stages)
    return result

def pair(a,root,url,release,answer,member):
    root.mkdir()
    ref=root/"remote.djt"
    make_reference(ref,url,release,member)
    remote=play(a,root/"remote",ref,True)
    result={"remote":remote,"local":None,"status":"incomplete","range_identity_verified":False,
            "member":{"raw_name_sha256":digest(bytes.fromhex(member["raw_hex"])),
            "header_offset":member["zip_header_offset"],"crc32":member["crc32"],
            "compression_method":member["zip_method"],"encoded_size":member["uncompressed_bytes"]}}
    # Do not download or create the local baseline before the first remote timing.
    if remote["status"]!="passed" or any(x["status"]==429 for x in remote["http"]): return result
    download=root/"download"
    download.mkdir()
    log=root/"download-http.jsonl"
    start=time.monotonic()
    try:
        with scoped_env(DOUJIN_LIVE_CAPTURE_DOWNLOAD="1",DOUJIN_LIVE_HTTP_LOG=str(log)):
            worker=ReferencePublisher(executable=a.worker,host_zlib=a.zlib)
            try:
                saved=worker.request(None,"mediafire_download_archive",id=release,url=url,
                    referrer="https://doujinstyle.com/?p=page&type=1&id="+str(release),filename="archive",output=str(download.resolve()))
            finally:
                worker.close()
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        result["download"]={"status":"failed","seconds":time.monotonic()-start,"http":read_rows(log)}
        return result
    result["download"]={"status":"saved","seconds":time.monotonic()-start,"http":read_rows(log)}
    archive=Path(saved["path"]).resolve(strict=True)
    if archive.parent!=download.resolve() or archive.stat().st_size!=answer["archive_bytes"] or sha256(archive)!=saved["sha256"]:
        result["failure"]="acquired_archive_identity_mismatch"
        return result
    try:
        verified=verify_ranges(archive,remote["http"])
        local=root/("local"+member["suffix"])
        media_sha=extract_exact(archive,member,local)
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError):
        result["failure"]="acquired_archive_or_member_does_not_match"
        return result
    result.update(archive_sha256=saved["sha256"],archive_size=archive.stat().st_size,
        audio_sha256=media_sha,verified_streamed_ranges=verified,range_identity_verified=True)
    # Content-addressed scratch only. This is not durable private retention.
    objects=a.scratch/"objects"
    objects.mkdir(exist_ok=True)
    obj=objects/(saved["sha256"]+".zip")
    if obj.exists():
        if sha256(obj)!=saved["sha256"]: raise ValueError("Object hash collision")
        archive.unlink()
    else: archive.replace(obj)
    try:
        result["local"]=play(a,root/"local",local,False)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        result["failure"]="local_playback_incomplete"
        return result
    if result["local"]["status"]=="passed":
        result["status"]="paired"
        result["delta_sdk_ns"]=remote["sdk_play_to_pcm_ns"]-result["local"]["sdk_play_to_pcm_ns"]
    return result

def execute(a):
    if not authorized():
        raise ValueError("Explicit hosted manual live smoke required")
    if digest(a.worker.read_bytes())!=a.worker_sha256:
        raise ValueError("Worker hash mismatch")
    version=subprocess.run([str(a.worker),"--version"],capture_output=True,check=True,timeout=10)
    identity=version.stdout.decode("ascii").splitlines()
    if "commit="+os.environ["GITHUB_SHA"] not in identity:
        raise ValueError("Worker checkout identity mismatch")
    a.scratch.mkdir(parents=True,exist_ok=False)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    db=a.scratch/"catalog.sqlite"
    out={"schema_version":1,"qualification":"production_native_provider_worker_only",
         "host_playback_verified":False,"source_commit":os.environ["GITHUB_SHA"],
         "worker_sha256":a.worker_sha256,"worker_build_identity":identity,
         "independent_egress_isolation_verified":False,"raw_capture_retained":False,
         "release_ids":[],"offer_results":[],"archives":[],"status":"incomplete"}
    def save():
        a.output.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    save()
    discovery_log=a.scratch/"discovery-http.jsonl"
    os.environ["DOUJIN_LIVE_HTTP_LOG"]=str(discovery_log)
    worker=ReferencePublisher(executable=a.worker,host_zlib=a.zlib)
    try:
        worker.request(db,"initialize",wal=False,legacy=[])
        start=time.monotonic()
        chart=worker.request(db,"catalog_chart_job",period="prefetch",refresh=True)
        ids=chart_ids(chart["stages"])
        out.update(release_ids=ids,chart_seconds=time.monotonic()-start,
                   chart_projection_sha256=stable_digest(chart))
        save()
        if not ids:
            raise ValueError("No chart findings")
        start=time.monotonic()
        result=worker.request(db,"catalog_offer_batch",ids=ids,workers=1,all_forms=False)
        out["offer_seconds"]=time.monotonic()-start
        out["offer_results"]=[{"id":x["id"],"offer_count":len(x.get("offers",[])),
            "had_observation":bool(x.get("had_observation")),"failed":bool(x.get("error")),
            "rate_limited":bool(x.get("rate_limited"))} for x in result.get("results",[])]
        save()
        if result.get("rate_limited"):
            out["status"]="rate_limited"
            save()
            return 2
        with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as connection:
            rows=connection.execute("SELECT rc.release_id,c.id,c.share_url FROM release_copies rc JOIN copies c ON c.id=rc.copy_id WHERE rc.current=1 AND c.host='mediafire.com' ORDER BY rc.release_id,c.id").fetchall()
        seen=set()
        for release,copy,url in rows:
            if release not in ids or copy in seen or not mediafire_url(url):
                continue
            if len(seen)>=MAX_ARCHIVES:
                break
            seen.add(copy)
            row={"release_id":release,"copy_id":copy,"share_identity_sha256":digest(url.encode()),"status":"failed"}
            start=time.monotonic()
            try:
                reply=worker.request(db,"archive_inspect_job",url=url,referrer="https://doujinstyle.com/?p=page&type=1&id="+str(release),
                    budget=512*1024,requests=12,host_zlib=str(a.zlib),headers=False,cover=False)
                row.update(archive_projection(reply),status="observed")
                member=select_member(reply)
                if member is None: row["pair"]={"status":"not_eligible","reason":"format_size_or_member_policy"}
                if member is not None and a.manifest is not None:
                    try:
                        row["pair"]=pair(a,a.scratch/("copy-"+str(copy)),url,release,reply,member)
                    except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):
                        from e2e.host.run_readiness import no_host
                        no_host()
                        pair_root=a.scratch/("copy-"+str(copy))
                        row["pair"]={"status":"failed","reason":"host_playback_or_exact_capture_failed",
                            "remote":{"http":read_rows(pair_root/"remote/live-http.jsonl")},
                            "download":{"http":read_rows(pair_root/"download-http.jsonl")}}
            except (ValueError,OSError,KeyError,TypeError):
                row["failure"]="provider_or_policy_or_archive_inspection_failed"
            row["elapsed_seconds"]=time.monotonic()-start
            out["archives"].append(row)
            save()
            pair_result=row.get("pair") or {}
            if any(x["status"]==429 for x in read_rows(discovery_log)) or any(
                    x["status"]==429 for phase in ("remote","download")
                    for x in (pair_result.get(phase) or {}).get("http",[])):
                out["rate_limited"]=True
                break
        out["discovery_http"]=read_rows(discovery_log)
        all_rows=[{**x,"phase":"discovery"} for x in out["discovery_http"]]
        for row in out["archives"]:
            for phase in ("remote","local","download"):
                all_rows.extend({**x,"phase":phase} for x in ((row.get("pair") or {}).get(phase) or {}).get("http",[]))
        out["transport_profiles"]=profiles(all_rows)
        out["paired_count"]=sum((x.get("pair") or {}).get("status")=="paired" for x in out["archives"])
        out["host_playback_verified"]=out["paired_count"]>0
        out["qualification"]="live_host_common_pcm_exact_download_range_bound" if out["host_playback_verified"] else "production_native_provider_worker_only"
        out["status"]="rate_limited" if out.get("rate_limited") else "paired" if out["host_playback_verified"] else ("observed" if any(x["status"]=="observed" for x in out["archives"]) else "no_archive_observation")
        save()
        return 0 if out["status"]=="paired" or (a.manifest is None and out["status"]=="observed") else 1
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        out["status"]="failed"
        out["failure"]="native_provider_operation_failed"
        save()
        return 2
    finally:
        worker.close()
        out["discovery_http"]=read_rows(discovery_log)
        save()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("worker","zlib","scratch","output"):
        p.add_argument("--"+k,required=True,type=Path)
    p.add_argument("--worker-sha256",required=True)
    for k in ("manifest","template","exporter"):
        p.add_argument("--"+k,type=Path)
    p.add_argument("--exporter-sha256")
    a=p.parse_args()
    if any(x is not None for x in (a.manifest,a.template,a.exporter,a.exporter_sha256)) and not all((a.manifest,a.template,a.exporter,a.exporter_sha256)):
        p.error("All host playback inputs required together")
    try:
        return execute(a)
    except (OSError,ValueError,subprocess.SubprocessError):
        print("Live provider smoke refused or preflight failed",file=sys.stderr)
        return 2

if __name__=="__main__": raise SystemExit(main())
