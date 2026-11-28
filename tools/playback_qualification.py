"""Sparse interaction inventory and exact-context playback evidence summary.

No traffic, decoder execution, release authorization or inferred support.
The core plan intentionally includes not-run and unsupported combinations.
"""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import product
import json
import math
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
HEX=re.compile(r"^[0-9a-f]{64}$")
TOKEN=re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
CONTEXT=("package_sha256","host_sha256","decoder_sha256","network_profile",
         "cache_state","architecture","work_class")
ORACLES=("member_bytes_equal","full_pcm_equal","metadata_equal","eof_verified")
OUTCOMES={"pass","fail","timeout","unsupported","unavailable","not-run","cancelled"}


def require(ok,message):
    if not ok:
        raise ValueError(message)


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def load(path):
    require(path.stat().st_size<=64*1024**2,"Input exceeds limit")
    return json.loads(path.read_text(encoding="utf-8"))


def plan(root=ROOT):
    cfg=load(root/"quality"/"playback-qualification.json")
    decoder=load(root/"quality"/"decoder-io-profiles.json")
    media=load(root/"quality"/"media-support.json")
    providers=cfg["providers"]
    archives=[{"id":r["id"],"state":"candidate"} for r in decoder["archive_modes"]]+cfg["additional_archive_classes"]
    audio=sorted(set(r["id"] for r in decoder["audio_profiles"])|
                 set(r["id"] for r in media["codec_container_fixtures"] if r["tier"]!="gap")|
                 set(cfg["additional_audio_profiles"]))
    require(len({p["id"] for p in providers})==len(providers),"Duplicate provider")
    require(len({a["id"] for a in archives})==len(archives),"Duplicate archive class")
    cells=[]
    for provider,archive,codec in product(providers,archives,audio):
        key=(provider["id"],archive["id"],codec)
        cells.append({"id":"/".join(key),"provider":key[0],"archive":key[1],"audio":key[2],
                      "lane":provider["lane"],"implementation":archive["state"],
                      "eligibility":"unverified","disposition":"not-run"})
    return {"schema_version":1,"scope":"planning-not-execution",
            "input_sha256":sha256(canonical([cfg,decoder,media])).hexdigest(),
            "minimums":cfg["minimums"],"cells":cells}


def valid_digest(value):
    return isinstance(value,str) and bool(HEX.fullmatch(value))


def valid_token(value):
    return isinstance(value,str) and bool(TOKEN.fullmatch(value))


def validate_attempt(row, cells):
    require(isinstance(row,dict),"Invalid attempt")
    require(row.get("cell") in cells,"Unknown interaction cell")
    cell=cells[row["cell"]]
    require(row.get("lane")==cell["lane"],"Evidence lane mismatch")
    require(valid_token(row.get("attempt_id")),"Invalid attempt identity")
    require(row.get("outcome") in OUTCOMES,"Unknown attempt outcome")
    require(row.get("action") in ("startup","seek","full-track","negative"),"Unknown action")
    for k in CONTEXT[:3]:
        require(valid_digest(row.get(k)),"Missing build/host/decoder digest")
    for k in CONTEXT[3:]:
        require(valid_token(row.get(k)),"Missing execution context")
    if row["outcome"] in ("pass","fail","timeout"):
        require(valid_digest(row.get("fixture_sha256")) and valid_digest(row.get("track_sha256"))
                and valid_digest(row.get("evidence_sha256")),"Missing immutable fixture or evidence identity")
        if cell["archive"]!="direct":
            require(valid_digest(row.get("archive_sha256")),"Missing archive identity")
    if row["outcome"]=="pass":
        require(cell["implementation"]!="negative-only" or row["action"]=="negative",
                "Negative-only cell cannot pass playback")
        if row["action"]=="full-track":
            require(all(row.get(k) is True for k in ORACLES),"Incomplete full-track oracle")
        elif row["action"]=="negative":
            require(row.get("safe_rejection_verified") is True,"Missing negative oracle")
        else:
            require(row.get("pcm_verified") is True,"Missing PCM oracle")
            ms=row.get("latency_ms")
            require(isinstance(ms,(int,float)) and not isinstance(ms,bool) and math.isfinite(ms) and ms>=0,
                    "Missing valid latency")


def summarize(matrix, attempts):
    cells={c["id"]:c for c in matrix["cells"]}
    groups=defaultdict(list)
    seen=set()
    full_tracks=set()
    archives=set()
    seeks=0
    for row in attempts:
        validate_attempt(row,cells)
        require(row["attempt_id"] not in seen,"Duplicate attempt")
        seen.add(row["attempt_id"])
        key=(row["cell"],row["action"],*(row[k] for k in CONTEXT))
        groups[key].append(row)
        if row["outcome"]=="pass" and row["action"]=="full-track":
            full_tracks.add(row["track_sha256"])
            if row.get("archive_sha256"):
                archives.add(row["archive_sha256"])
        if row["action"]=="seek" and row["outcome"]!="not-run":
            seeks+=1
    results=[]
    for key,rows in sorted(groups.items()):
        counts=Counter(r["outcome"] for r in rows)
        good=sorted(r["latency_ms"] for r in rows if r["outcome"]=="pass" and key[1] in ("startup","seek"))
        # Failure/timeout/unavailable are not dropped when deciding if the
        # percentile is measurable. Success-only p95 is never the release SLO.
        n=len(rows)-counts["not-run"]
        rank=math.ceil(.95*n)
        enough=n>=matrix["minimums"]["latency_cell_attempts"]
        p95=good[rank-1] if enough and rank and rank<=len(good) else None
        results.append({"cell":key[0],"action":key[1],"context":dict(zip(CONTEXT,key[2:])),
                        "outcomes":dict(counts),"attempted":n,"p95_ms":p95,
                        "latency_sample_minimum_met":enough,
                        "p95_censored_or_unavailable":p95 is None})
    exercised={r["cell"] for r in attempts if r["outcome"]!="not-run"}
    return {"schema_version":1,"scope":"evidence-accounting-not-release-verdict",
            "plan_sha256":sha256(canonical(matrix)).hexdigest(),
            "planned_cells":len(cells),"attempted_cells":len(exercised),
            "not_attempted_cells":sorted(set(cells)-exercised),
            "distinct_full_track_pass_hashes":len(full_tracks),
            "distinct_full_track_archive_hashes":len(archives),
            "seek_attempts":seeks,"groups":results,
            "release_qualified":False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--attempts",type=Path,help="sanitized JSON array of attempt receipts")
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    matrix=plan()
    if args.attempts:
        attempts=load(args.attempts)
        require(isinstance(attempts,list),"Attempts must be an array")
        result=summarize(matrix,attempts)
    else:
        result=matrix
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
