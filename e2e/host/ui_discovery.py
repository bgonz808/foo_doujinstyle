"""Bounded read-only UIA snapshots and candidate selector inventory.

No test result is inferred from a tree alone. Names may contain synthetic
fixture text; evidence is local and must be reviewed before sharing.
"""
from __future__ import annotations
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from e2e.host.runner import QualificationError, sha256
from e2e.host.screenshot_evidence import collect

STATE=re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")
MAX_BYTES=8*1024**2
MAX_NODES=2048
FIELDS={"index","parent","depth","pid","name","automation_id","control_type","class_name",
        "framework_id","enabled","offscreen","password","keyboard_focusable","bounds","patterns"}


def validate(document,pid):
    if (not isinstance(document,dict) or document.get("schema_version")!=1 or
        document.get("kind")!="uia-control-view" or document.get("pid")!=pid or
        type(document.get("truncated")) is not bool or
        type(document.get("excluded_foreign_subtrees")) is not int or document["excluded_foreign_subtrees"]<0):
        raise QualificationError("Invalid UIA snapshot identity")
    nodes=document.get("nodes")
    if not isinstance(nodes,list) or not 1<=len(nodes)<=MAX_NODES:
        raise QualificationError("Invalid UIA node count")
    for i,node in enumerate(nodes):
        if not isinstance(node,dict) or set(node)!=FIELDS:
            raise QualificationError("Unexpected UIA fields")
        if (type(node["index"]) is not int or node["index"]!=i or node["pid"]!=pid or
            type(node["parent"]) is not int or not -1<=node["parent"]<i or
            type(node["depth"]) is not int or not 0<=node["depth"]<=16):
            raise QualificationError("Invalid UIA hierarchy or process")
        if node["depth"]!=(0 if node["parent"]==-1 else nodes[node["parent"]]["depth"]+1):
            raise QualificationError("Invalid UIA depth")
        for k in ("name","automation_id","control_type","class_name","framework_id"):
            if not isinstance(node[k],str) or len(node[k])>1024:
                raise QualificationError("Invalid UIA text property")
        for k in ("enabled","offscreen","password","keyboard_focusable"):
            if type(node[k]) is not bool:
                raise QualificationError("Invalid UIA boolean property")
        if node["name"] and (node["password"] or node["control_type"] not in {
            "ControlType.Window","ControlType.Button","ControlType.MenuItem","ControlType.TabItem",
            "ControlType.ListItem","ControlType.TreeItem","ControlType.CheckBox","ControlType.RadioButton",
            "ControlType.HeaderItem"}):
            raise QualificationError("Ambiguous or password label must be suppressed")
        bounds=node["bounds"]
        if bounds is not None and (not isinstance(bounds,list) or len(bounds)!=4 or
            any(type(x) not in (int,float) or not math.isfinite(x) for x in bounds) or min(bounds[2:])<0):
            raise QualificationError("Invalid UIA bounds")
        patterns=node["patterns"]
        if not isinstance(patterns,list) or len(patterns)>128 or any(not isinstance(x,str) or len(x)>128 for x in patterns):
            raise QualificationError("Invalid UIA patterns")
    return nodes


def candidates(document,pid):
    nodes=validate(document,pid)
    root=[]
    for node in nodes:
        root.append(node["index"] if node["parent"]==-1 else root[node["parent"]])
    counts=Counter((root[i],n["automation_id"],n["control_type"]) for i,n in enumerate(nodes) if n["automation_id"])
    rows=[]
    for i,n in enumerate(nodes):
        key=(root[i],n["automation_id"],n["control_type"])
        if n["automation_id"] and counts[key]==1 and not n["password"]:
            rows.append({"window_index":root[i],"node_index":i,"automation_id":n["automation_id"],
                         "control_type":n["control_type"],"patterns":n["patterns"],
                         "visible":not n["offscreen"],"enabled":n["enabled"]})
    return {"status":"candidate_only","complete_snapshot":not document["truncated"],
            "selector_candidates":rows if not document["truncated"] else [],
            "runtime_uniqueness_verified":False}


def inspect(pid,exe):
    pid=int(pid)
    if pid<=0 or not exe.is_file():
        raise QualificationError("Expected process and executable required")
    proc=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-STA","-File",
        str(Path(__file__).with_name("discover_uia.ps1")),"-TargetPid",str(pid),
        "-ExpectedExe",str(exe.resolve())],capture_output=True,timeout=30,check=False)
    if proc.returncode or len(proc.stdout)>MAX_BYTES:
        raise QualificationError("UIA inspection failed: "+proc.stderr.decode("utf-8",errors="replace")[:1000])
    document=json.loads(proc.stdout.decode("utf-8-sig"))
    validate(document,pid)
    return document


def write_json(path,data):
    with path.open("x",encoding="utf-8") as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)
        stream.write("\n")


class UiDiscovery:
    ROBOT_LIBRARY_SCOPE="SUITE"

    def capture_ui_state(self,state,pid,selector="/Window"):
        from robot.libraries.BuiltIn import BuiltIn
        if not STATE.fullmatch(state):
            raise QualificationError("Unsafe state identifier")
        robot=BuiltIn()
        root=Path(robot.get_variable_value("${OUTPUT DIR}"))
        dest=root/"ui"/state
        dest.mkdir(parents=True,exist_ok=False)
        exe=Path(os.environ["DOUJIN_E2E_HOST_EXE"])
        document=inspect(int(pid),exe)
        document["state"]=state
        document["run_id"]=os.environ["DOUJIN_E2E_RUN_ID"]
        write_json(dest/"tree.json",document)
        write_json(dest/"selectors.json",candidates(document,int(pid)))
        # FlaUI captures the selected window, never deliberately the desktop.
        robot.run_keyword("Set Screenshot Directory",str(dest))
        try:
            robot.run_keyword("Take Screenshot",selector)
        finally:
            robot.run_keyword("Set Screenshot Directory",str(root))
        screenshots=collect(dest)
        if screenshots["status"]!="present":
            raise QualificationError("State screenshot missing")
        write_json(dest/"state.json",{"schema_version":1,"state":state,"pid":int(pid),
            "run_id":document["run_id"],"tree_sha256":sha256(dest/"tree.json"),
            "selectors_sha256":sha256(dest/"selectors.json"),"screenshots":screenshots,
            "visual_content_verified":None,"atomic_snapshot":False})
