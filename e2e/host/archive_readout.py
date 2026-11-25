"""Read-only per-copy archive inventory from the committed catalog.

Directory observations are not member verification or decoder support. Use a
quiesced disposable catalog or SQLite backup; no network or extraction here.
Only intended for synthetic/authorized local evidence; filenames may be private.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

MAX_REPORT=16*1024*1024
MAX_ROWS=32768
MAX_COPIES=128

def inspect_archive(raw):
    if raw is None:
        return {"status":"not_inspected","format":None,"members":None}
    if not isinstance(raw,str) or len(raw.encode("utf-8"))>MAX_REPORT:
        raise ValueError("Archive report exceeds limit")
    doc=json.loads(raw)
    if not isinstance(doc,dict):
        raise ValueError("Invalid archive report")
    fmt=doc.get("format")
    if fmt!="zip":
        return {"status":"non_zip_or_unclassified","format":fmt if isinstance(fmt,str) else None,"members":None}
    inventory=doc.get("inventory")
    if not isinstance(inventory,list) or len(inventory)>MAX_ROWS:
        return {"status":"inventory_unavailable","format":"zip","members":None}
    rows=[]
    seen=set()
    paths=Counter()
    for row in inventory:
        if not isinstance(row,dict):
            raise ValueError("Invalid member")
        name=row.get("path")
        offset=row.get("zip_header_offset")
        if (not isinstance(name,str) or not name or len(name)>4096 or "\x00" in name or
            type(offset) is not int or offset<0 or offset in seen):
            raise ValueError("Invalid or duplicate member identity")
        seen.add(offset)
        paths[name]+=1
        record={k:row.get(k) for k in ("path","suffix","kind","compressed_bytes","uncompressed_bytes",
                                      "zip_method","zip_header_offset","crc32","encrypted","zip_name")}
        for key in ("compressed_bytes","uncompressed_bytes","zip_method"):
            if type(record[key]) is not int or record[key]<0:
                raise ValueError("Invalid member numeric field")
        if type(record["encrypted"]) is not bool:
            raise ValueError("Invalid encryption flag")
        if not isinstance(record["kind"],str) or not isinstance(record["crc32"],str):
            raise ValueError("Invalid member classification")
        record["codec_verified"]=None
        record["member_sha256"]=None
        record["remote_playback_candidate"]=(record["kind"]=="audio_candidate" and
            record["zip_method"] in (0,8) and not record["encrypted"])
        rows.append(record)
    if doc.get("members")!=len(rows):
        raise ValueError("Member count mismatch")
    return {"status":"directory_inventory_observed","format":"zip","members":rows,
        "member_count":len(rows),"member_types":dict(Counter(x["kind"] for x in rows)),
        "compression_methods":dict(Counter(str(x["zip_method"]) for x in rows)),
        "duplicate_paths":sorted(k for k,v in paths.items() if v>1),
        "member_bytes_verified":False,"decoder_support_verified":False}

def readout(path,release):
    if type(release) is not int or release<=0:
        raise ValueError("Positive release ID required")
    path=path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Catalog file required")
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        found=db.execute("SELECT id FROM releases WHERE id=?",(release,)).fetchone()
        if not found:
            raise ValueError("Release not found")
        copies=db.execute("""SELECT c.id,c.host,c.archive,c.archive_checked,
            MAX(rc.current) FROM copies c JOIN release_copies rc ON c.id=rc.copy_id
            WHERE rc.release_id=? GROUP BY c.id ORDER BY c.id LIMIT ?""",(release,MAX_COPIES+1)).fetchall()
        if len(copies)>MAX_COPIES:
            raise ValueError("Copy limit exceeded")
        result=[]
        for ident,host,archive,checked,current in copies:
            result.append({"copy_id":ident,"host":host,"archive_checked":checked,
                           "current":bool(current),"archive":inspect_archive(archive)})
        return {"schema_version":1,"kind":"catalog-archive-readout","release_id":release,
            "copies":result,"source":"committed_catalog_observations","live_verified":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog",type=Path,required=True)
    p.add_argument("--release",type=int,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        report=readout(a.catalog,a.release)
        data=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)
        if len(data.encode("utf-8"))>MAX_REPORT:
            raise ValueError("Output exceeds limit")
        with a.output.open("x",encoding="utf-8") as out:
            out.write(data+"\n")
        return 0
    except (OSError,ValueError,sqlite3.Error) as exc:
        print("Archive readout failed:",type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
