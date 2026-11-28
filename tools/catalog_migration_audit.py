"""Private, offline qualification of native catalog initialization via worker IPC.

The original closed backup is never opened for writes. The caller explicitly
chooses a disposable destination. No provider requests or production promotion.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

EXPECTED_ADDED={"catalog_lookup_claims","catalog_post_attempts","published_references"}


def require(ok,message):
    if not ok:
        raise ValueError(message)


def digest(path):
    h=sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def closed(path):
    require(path.is_file(),"Expected closed database file")
    require(not any(Path(str(path)+s).exists() for s in ("-wal","-shm","-journal")),
            "Database sidecar exists; export a closed backup first")


def quote(name):
    return '"'+name.replace('"','""')+'"'


def typed(value):
    if isinstance(value,bytes):
        return {"blob_sha256":sha256(value).hexdigest(),"length":len(value)}
    return value


def logical(path):
    closed(path)
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro&immutable=1",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        require(db.execute("PRAGMA integrity_check").fetchone()[0]=="ok","Integrity failure")
        require(db.execute("PRAGMA foreign_key_check").fetchone() is None,"Foreign key failure")
        rows={}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            columns=db.execute("PRAGMA table_info("+quote(name)+")").fetchall()
            h=sha256()
            n=0
            order=",".join(str(i+1)+" COLLATE BINARY" for i in range(len(columns)))
            for row in db.execute("SELECT * FROM "+quote(name)+" ORDER BY "+order):
                h.update(json.dumps([typed(v) for v in row],ensure_ascii=True,separators=(",",":")).encode()+b"\n")
                n+=1
            rows[name]={"rows":n,"sha256":h.hexdigest(),"columns":columns}
        return {"version":db.execute("PRAGMA user_version").fetchone()[0],"tables":rows}


def worker_identity(worker,expected_sha,expected_commit):
    require(digest(worker)==expected_sha,"Worker digest mismatch")
    p=subprocess.run([str(worker.resolve()),"--version"],capture_output=True,timeout=10,check=True)
    lines=p.stdout.decode("ascii").splitlines()
    require(len(lines)==3 and lines[2]=="commit="+expected_commit,"Worker commit mismatch")
    require(lines[1].startswith("source="),"Missing worker source digest")
    return lines


def initialize(worker,path):
    command={"operation":"initialize","db":str(path.resolve()),"wal":False,"legacy":[]}
    p=subprocess.run([str(worker.resolve()),"--serve"],input=(json.dumps(command)+"\n").encode(),
                     capture_output=True,timeout=30)
    require(p.returncode==0,"Native process failed")
    lines=p.stdout.splitlines()
    require(len(lines)==2 and lines[0]==b"DoujinStyle catalog 1","Unexpected native protocol")
    response=json.loads(lines[1])
    require(isinstance(response,dict) and (("result" in response)!=("error" in response)),
            "Unexpected native response")
    return "result" in response


def audit(source,destination,worker,expected_sha,expected_commit):
    source=source.resolve(strict=True)
    destination=destination.resolve()
    closed(source)
    require(source!=destination and not destination.exists(),"New disposable destination required")
    identity=worker_identity(worker,expected_sha,expected_commit)
    original=digest(source)
    before=logical(source)
    require(before["version"]==1,"Expected v1 seed")
    require(destination.parent.is_dir(),"Destination parent must exist")
    # Exclusive create, no overwrite, no raw DB or table contents in report.
    with source.open("rb") as src,destination.open("xb") as dst:
        shutil.copyfileobj(src,dst)
    require(digest(destination)==original,"Copy mismatch")
    try:
        require(initialize(worker,destination),"Native migration rejected")
        after=logical(destination)
        require(after["version"]==2,"Expected v2")
        require(all(after["tables"].get(k)==v for k,v in before["tables"].items()),
                "Original table contents or columns changed")
        added=set(after["tables"])-set(before["tables"])
        require(added<=EXPECTED_ADDED and all(after["tables"][k]["rows"]==0 for k in added),
                "Unexpected new tables or rows")
        require(EXPECTED_ADDED<=set(after["tables"]),"Missing migration tables")
        migrated=digest(destination)
        require(initialize(worker,destination),"Native reopen rejected")
        require(digest(destination)==migrated and logical(destination)==after,"Reopen modified database")
        require(digest(source)==original,"Original changed")
        require(digest(worker)==expected_sha,"Worker changed")
        return {"schema_version":1,"source_sha256":original,"migrated_sha256":migrated,
                "worker_sha256":expected_sha,"worker_identity":identity,
                "before_version":1,"after_version":2,"original_tables_preserved":True,
                "original_counts":{k:v["rows"] for k,v in before["tables"].items()},
                "added_tables":sorted(added),"integrity":"ok","foreign_key_errors":0,
                "reopen_byte_identical":True,"original_unchanged":True}
    finally:
        require(digest(source)==original,"Original changed")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("source","destination","worker","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--worker-sha256",required=True)
    p.add_argument("--commit",required=True)
    a=p.parse_args()
    require(not a.output.exists(),"Refuse to overwrite report")
    result=audit(a.source,a.destination,a.worker,a.worker_sha256,a.commit)
    with a.output.open("x",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,indent=2)
        f.write("\n")


if __name__=="__main__":
    main()
