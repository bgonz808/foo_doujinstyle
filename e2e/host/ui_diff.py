"""Review version-bound UI state changes, not pixels or sibling positions."""
from collections import Counter
import argparse
import json
from pathlib import Path
import re
from e2e.host.ui_discovery import validate,write_json
from e2e.host.runner import sha256,QualificationError

def index(doc):
    nodes=validate(doc,doc["pid"])
    if doc["truncated"] or doc["excluded_foreign_subtrees"]:
        raise QualificationError("Incomplete tree cannot qualify semantic compatibility")
    roots=[]
    keys=[]
    for n in nodes:
        if n["parent"]==-1:
            anchor=n["automation_id"] or n["name"]
            root=(n["control_type"],anchor) if anchor else None
        else:
            root=roots[n["parent"]]
        roots.append(root)
        keys.append((root,n["control_type"],n["automation_id"]) if root and n["automation_id"] else None)
    counts=Counter(k for k in keys if k is not None)
    root_counts=Counter(roots[i] for i,n in enumerate(nodes) if n["parent"]==-1)
    stable={k:n for k,n in zip(keys,nodes) if k is not None and counts[k]==1 and root_counts[k[0]]==1}
    return stable,len(nodes)-len(stable)

def diff(old,new):
    before,unkeyed_before=index(old)
    after,unkeyed_after=index(new)
    added=sorted(after.keys()-before.keys())
    removed=sorted(before.keys()-after.keys())
    semantic=[]
    layout=[]
    for key in sorted(before.keys()&after.keys()):
        a,b=before[key],after[key]
        changes={k:{"before":a[k],"after":b[k]} for k in
                 ("name","class_name","framework_id","enabled","offscreen","keyboard_focusable","password")
                 if a[k]!=b[k]}
        if set(a["patterns"])!=set(b["patterns"]):
            changes["patterns"]={"before":sorted(a["patterns"]),"after":sorted(b["patterns"])}
        if changes: semantic.append({"key":key,"changes":changes})
        if a["bounds"]!=b["bounds"]: layout.append({"key":key,"before":a["bounds"],"after":b["bounds"]})
    return {"schema_version":1,"qualification":"candidate_semantic_diff_only",
            "added":added,"removed":removed,"semantic_changes":semantic,"geometry_changes":layout,
            "unkeyed_before":unkeyed_before,"unkeyed_after":unkeyed_after,
            "live_selectors_verified":False,
            "review_required":bool(added or removed or semantic or unkeyed_before or unkeyed_after)}

def load(folder):
    state=json.loads((folder/"state.json").read_text(encoding="utf-8"))
    if state.get("tree_sha256")!=sha256(folder/"tree.json"):
        raise QualificationError("Tree digest mismatch")
    for k,n in (("source_commit",40),("component_sha256",64)):
        if not re.fullmatch("[0-9a-f]{"+str(n)+"}",state.get(k,"")):
            raise QualificationError("Version-bound evidence required")
    doc=json.loads((folder/"tree.json").read_text(encoding="utf-8"))
    if any(doc.get(k)!=state.get(k) for k in ("pid","state","run_id")):
        raise QualificationError("Tree identity mismatch")
    return state,doc

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before",type=Path,required=True)
    p.add_argument("--after",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    left,old=load(a.before)
    right,new=load(a.after)
    if left["state"]!=right["state"] or left["foobar_version"]!=right["foobar_version"]:
        raise QualificationError("Compare same named state and host version")
    result=diff(old,new)
    result["before"]={k:left[k] for k in ("state","source_commit","component_sha256","component_version","foobar_version","tree_sha256")}
    result["after"]={k:right[k] for k in result["before"]}
    write_json(a.output,result)

if __name__=="__main__":
    main()
