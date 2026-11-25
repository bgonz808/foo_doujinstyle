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
        method=item.get("compress_type")
        if type(method) is int:
            methods[str(method)]=methods.get(str(method),0)+1
    http=answer.get("archive_http",{})
    return {"format":fmt,"archive_bytes":answer.get("archive_bytes"),
            "inventory_count":len(inventory),"inventory_sha256":stable_digest(inventory),
            "compression_methods":methods,
            "http":{k:http.get(k) for k in ("requests","body_bytes")},
            "audio_members":len(answer["audio"]) if isinstance(answer.get("audio"),list) else
                answer.get("audio_members") if type(answer.get("audio_members")) is int else None}

def execute(a):
    if not (os.environ.get("GITHUB_ACTIONS")=="true" and
            os.environ.get("RUNNER_ENVIRONMENT")=="github-hosted" and
            os.environ.get("GITHUB_EVENT_NAME")=="workflow_dispatch" and
            os.environ.get("DOUJIN_LIVE_PROVIDER_SMOKE")=="1" and sys.platform=="win32"):
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
            except (ValueError,OSError,KeyError,TypeError):
                row["failure"]="provider_or_policy_or_archive_inspection_failed"
            row["elapsed_seconds"]=time.monotonic()-start
            out["archives"].append(row)
            save()
        out["status"]="observed" if any(x["status"]=="observed" for x in out["archives"]) else "no_archive_observation"
        save()
        return 0 if out["status"]=="observed" else 1
    except (OSError,ValueError,KeyError,TypeError):
        out["status"]="failed"
        out["failure"]="native_provider_operation_failed"
        save()
        return 2
    finally:
        worker.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("worker","zlib","scratch","output"):
        p.add_argument("--"+k,required=True,type=Path)
    p.add_argument("--worker-sha256",required=True)
    a=p.parse_args()
    try:
        return execute(a)
    except (OSError,ValueError,subprocess.SubprocessError):
        print("Live provider smoke refused or preflight failed",file=sys.stderr)
        return 2

if __name__=="__main__": raise SystemExit(main())
