"""Private census planning and observed gap accounting on closed SQLite backups.

Uses the existing catalog as the source of truth; no network, no media fetch,
no new shipped coordinator. Never publish the private manifest or raw DB.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from hashlib import sha256
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3

from tools.catalog_corpus import REQUIRED

HOST=re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
KNOWN_HOSTS={"mediafire.com","mega.nz"}
FORMATS={"zip","zip64","rar","rar4","rar5","7z","gzip","tar","tar.gz","tar.zst","nested-zip","direct"}
AUDIO={".wav",".flac",".mp3",".aac",".m4a",".ogg",".oga",".opus",".alac",".ape",".tta",".tak",".wv",".wma",".aif",".aiff",".dff",".dsf",".mp2",".mp4",".mka",".mod",".nsf",".spc",".vgm",".vgz",".xma",".ac3",".adx",".mpc"}
MAX_RECORD=16*1024*1024


def require(ok,msg):
    if not ok:
        raise ValueError(msg)


def file_digest(path):
    h=sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def snapshot(path):
    p=path.resolve(strict=True)
    require(p.is_file(),"Expected closed catalog backup")
    require(not any(Path(str(p)+s).exists() for s in ("-wal","-shm","-journal")),
            "Create a closed SQLite backup before census")
    before=file_digest(p)
    db=sqlite3.connect(p.as_uri()+"?mode=ro&immutable=1",uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        require(db.execute("PRAGMA quick_check").fetchone()[0]=="ok","SQLite integrity failure")
        require(db.execute("PRAGMA foreign_key_check").fetchone() is None,"SQLite foreign key failure")
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(REQUIRED<=tables,"Missing catalog tables")
        version=db.execute("PRAGMA user_version").fetchone()[0]
        require(version in (1,2),"Unqualified catalog schema")
        yield db,before,version
    finally:
        db.close()
        require(file_digest(p)==before,"Snapshot changed during census")


def population(db):
    ids=[r[0] for r in db.execute("SELECT id FROM releases ORDER BY id")]
    require(all(type(i) is int and i>0 for i in ids) and len(set(ids))==len(ids),
            "Invalid release population")
    return ids


def sample_order(ids,seed):
    require(isinstance(seed,str) and 0<len(seed)<=256 and "\0" not in seed,"Invalid seed")
    require(all(type(i) is int and i>0 for i in ids) and len(set(ids))==len(ids),"Invalid sample population")
    return sorted(ids,key=lambda i:(sha256((seed+"\0"+str(i)).encode()).digest(),i))


def freeze(path,seed,pilot=100,extension=1000):
    with snapshot(path) as (db,digest,version):
        ids=population(db)
        require(0<pilot<=extension<=len(ids) and extension<=100000,"Invalid cohort sizes")
        return {"schema_version":1,"kind":"private-catalog-sample-manifest",
                "snapshot_sha256":digest,"snapshot_schema_version":version,
                "population_count":len(ids),
                "population_id_sha256":sha256("".join(f"{i}\n" for i in ids).encode()).hexdigest(),
                "seed":seed,"algorithm":"ascending SHA-256 of UTF-8 seed + NUL + decimal release ID, tie break ascending ID; without replacement",
                "pilot_size":pilot,"extension_size":extension,
                "ordered_release_ids":sample_order(ids,seed)[:extension]}


def cohort(manifest,size):
    require(manifest.get("kind")=="private-catalog-sample-manifest" and manifest.get("schema_version")==1,
            "Invalid census manifest")
    ids=manifest["ordered_release_ids"]
    require(isinstance(ids,list) and all(type(i) is int and i>0 for i in ids)
            and len(ids)==len(set(ids))==manifest["extension_size"],"Invalid manifest IDs")
    require(sample_order(ids,manifest["seed"])==ids,"Manifest order changed")
    require(type(size) is int and 0<size<=len(ids),"Invalid cohort size")
    return ids[:size]


def host_label(value):
    # Private host-family names only; never output share URLs or arbitrary strings.
    v=value.lower().strip() if isinstance(value,str) else ""
    return v if HOST.fullmatch(v) else "unknown"


def classify(raw):
    if raw is None:
        return "not-inspected",None,Counter(),set(),False
    require(isinstance(raw,str) and len(raw.encode())<=MAX_RECORD,"Archive record exceeds limit")
    d=json.loads(raw)
    require(isinstance(d,dict),"Invalid archive observation")
    fmt=d.get("format")
    fmt=fmt.lower() if isinstance(fmt,str) and fmt.lower() in FORMATS else "unknown"
    if fmt not in ("zip","zip64"):
        return ("signature-only" if fmt!="unknown" else "unclassified"),fmt,Counter(),set(),False
    rows=d.get("inventory")
    grade="directory-observed"
    if not isinstance(rows,list):
        rows=d.get("audio")
        grade="legacy-audio-observed"
    if not isinstance(rows,list):
        return "inventory-unavailable",fmt,Counter(),set(),False
    require(len(rows)<=32768,"Archive member limit")
    counts=Counter()
    gaps=set()
    candidate=False
    offsets=set()
    for r in rows:
        require(isinstance(r,dict) and isinstance(r.get("path"),str),"Invalid member observation")
        suffix=PurePosixPath(r["path"]).suffix.lower()
        if grade=="directory-observed":
            offset=r.get("zip_header_offset")
            require(type(offset) is int and offset>=0 and offset not in offsets,"Duplicate or invalid member locator")
            offsets.add(offset)
            if r.get("kind")!="audio_candidate":
                continue
        elif suffix not in AUDIO:
            continue
        suffix=suffix if suffix in AUDIO else "unknown"
        method=r.get("zip_method")
        method=str(method) if type(method) is int and 0<=method<=65535 else "unknown"
        encrypted=r.get("encrypted")
        enc="encrypted" if encrypted is True else "clear" if encrypted is False else "unknown"
        counts[(method,suffix,enc)]+=1
        if method not in ("0","8","unknown"):
            gaps.add("zip-method:"+method)
        if encrypted is True:
            gaps.add("encrypted")
        if method in ("0","8") and encrypted is False:
            candidate=True
    if grade=="directory-observed":
        require(d.get("members")==len(rows),"Directory member count mismatch")
    return grade,fmt,counts,gaps,candidate


def report(path,manifest=None,size=None,batch=10):
    require(type(batch) is int and 1<=batch<=100,"Invalid batch limit")
    with snapshot(path) as (db,digest,version):
        all_ids=population(db)
        ids=all_ids if manifest is None else cohort(manifest,size)
        # SQLite parameter count independent of catalog size. No joins of
        # duplicate source associations into member/copy cardinality.
        selected=set(ids)
        releases={r[0]:(r[1],r[2]) for r in db.execute("SELECT id,offer_checked,offer_error FROM releases") if r[0] in selected}
        links=defaultdict(set)
        history=set()
        for rel,copy,current in db.execute("SELECT DISTINCT release_id,copy_id,current FROM release_copies"):
            if rel in selected:
                history.add((rel,copy))
                if current:
                    links[copy].add(rel)
        current_rel=set().union(*links.values()) if links else set()
        host_counts=Counter()
        host_releases=defaultdict(set)
        formats=Counter()
        grades=Counter()
        member_cells=Counter()
        copy_cells=Counter()
        blocked=defaultdict(set)
        gap_copies=defaultdict(set)
        candidate_rel=set()
        unknown_rel=set()
        copy_status=Counter()
        planned_copies=[]
        for copy,host,raw,checked,error in db.execute("SELECT id,host,archive,archive_checked,archive_error FROM copies ORDER BY id"):
            if copy not in links:
                continue
            h=host_label(host)
            rs=links[copy]
            host_counts[h]+=1
            host_releases[h].update(rs)
            try:
                grade,fmt,counts,gaps,candidate=classify(raw)
            except (ValueError,TypeError,OverflowError):
                grade,fmt,counts,gaps,candidate="invalid-record",None,Counter(),set(),False
            grades[grade]+=1
            formats[fmt or "unknown"]+=1
            copy_cells[(h,fmt or "unknown",grade)]+=1
            copy_status["never-checked" if checked is None else "error" if error is not None else "checked"]+=1
            for (method,suffix,enc),n in counts.items():
                member_cells[(h,fmt,method,suffix,enc)]+=n
            reasons=set(gaps)
            if h not in KNOWN_HOSTS and h!="unknown":
                reasons.add("host:"+h)
            if fmt and fmt not in ("zip","zip64","unknown"):
                reasons.add("archive:"+fmt)
            if h=="unknown" or grade in ("not-inspected","unclassified","invalid-record","inventory-unavailable","legacy-audio-observed") or error is not None:
                unknown_rel.update(rs)
            # Candidate only, not playback evidence or complete release.
            if h in KNOWN_HOSTS and candidate and error is None:
                candidate_rel.update(rs)
            for reason in reasons:
                blocked[reason].update(rs)
                gap_copies[reason].add(copy)
            if h in KNOWN_HOSTS and checked is None and raw is None:
                planned_copies.append((copy,min(rs)))
        missing=[i for i in ids if i not in releases]
        unobserved=[i for i in ids if i in releases and releases[i][0] is None]
        checked_error=sum(x[0] is not None and x[1] is not None for x in releases.values())
        offer_ok=sum(x[0] is not None and x[1] is None for x in releases.values())
        unknown_rel.update(unobserved)
        unknown_rel.update(missing)
        # A checked release with an error, or no recorded current copy, is
        # not proof no alternative exists. Preserve unresolved status.
        unknown_rel.update(i for i,(checked,error) in releases.items() if error is not None or i not in current_rel)
        rankings=[{"gap":k,"affected_current_copies":len(gap_copies[k]),
                   "affected_releases":len(rs),
                   "releases_without_known_candidate":len(rs-candidate_rel),
                   "of_those_unresolved_alternatives":len((rs-candidate_rel)&unknown_rel)}
                  for k,rs in blocked.items()]
        rankings.sort(key=lambda x:(-x["releases_without_known_candidate"],-x["affected_releases"],x["gap"]))
        order={i:n for n,i in enumerate(ids)}
        planned_copies.sort(key=lambda x:(order[x[1]],x[0]))
        return {"schema_version":1,"kind":"private-census-readout","snapshot_sha256":digest,
                "sqlite_user_version":version,"cohort_size":len(ids),"population_count":len(all_ids),
                "original_seed_snapshot_sha256":manifest.get("snapshot_sha256") if manifest else digest,
                "release_status":{"missing":len(missing),"offer_unchecked":len(unobserved),
                    "offer_checked_no_error":offer_ok,"offer_checked_error":checked_error,
                    "with_current_copy":len(current_rel),"with_structural_candidate":len(candidate_rel),
                    "unresolved":len(unknown_rel)},
                "current_distinct_copies":len(links),"historical_release_copy_pairs":len(history),
                "current_release_copy_pairs":sum(map(len,links.values())),
                "hosts":[{"host":h,"copies":n,"releases":len(host_releases[h])} for h,n in sorted(host_counts.items(),key=lambda x:(-x[1],x[0]))],
                "archive_formats":dict(formats),"archive_evidence_grades":dict(grades),"copy_check_status":dict(copy_status),
                "copy_interactions":[{"host":h,"archive":f,"grade":g,"copies":n} for (h,f,g),n in sorted(copy_cells.items())],
                "member_interactions":[{"host":h,"archive":f,"method":m,"audio_suffix":s,"encryption":e,"members":n} for (h,f,m,s,e),n in sorted(member_cells.items())],
                "observed_gap_rankings":rankings,
                "private_next_batch":{"offer_release_ids":unobserved[:batch],
                    "archive_copy_jobs":[{"id":c,"release_id":r} for c,r in planned_copies[:batch]]},
                "limitations":["Private offline inventory, not representative prevalence unless frozen uniform cohort.",
                    "Candidate is structural only, not playable or all tracks qualified.",
                    "Gap counts overlap; unresolved alternatives prevent causal incremental coverage claims.",
                    "No automatic retries of errors or live requests. Native policy must revalidate each job.",
                    "Audio suffix is not verified codec; historic or stale observations are not live confirmation."]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--freeze-seed")
    p.add_argument("--manifest",type=Path)
    p.add_argument("--size",type=int,default=100)
    p.add_argument("--batch",type=int,default=10)
    a=p.parse_args()
    require(not(a.freeze_seed and a.manifest),"Choose freeze or readout")
    require(not a.output.exists(),"Refusing to overwrite evidence")
    result=freeze(a.db,a.freeze_seed) if a.freeze_seed else report(a.db,json.loads(a.manifest.read_text()) if a.manifest else None,a.size,a.batch)
    with a.output.open("x",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,indent=2)
        f.write("\n")


if __name__=="__main__":
    main()
