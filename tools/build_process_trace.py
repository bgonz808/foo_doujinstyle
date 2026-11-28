"""Private ETW process/image evidence for mapped SQLite build research.

No full command-line, child environment, file-read closure or release claim.
The manifest provider records process lifetimes and image loads. MSBuild's
diagnostic command observations are separate, not proof of child argv.
"""
import argparse
from collections import Counter
import ctypes
from datetime import datetime
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

from tools.reproducibility import digest, source_receipt
from tools.reproducibility_detail import coff_detail
from tools.sqlite_repro import command_flags
from tools.toolchain_inventory import tree_digest

PROVIDER = "Microsoft-Windows-Kernel-Process"
GUID = "{22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716}"
KNOWN = {"msbuild.exe", "cl.exe", "lib.exe", "link.exe", "mspdbsrv.exe",
         "mspdbcore.dll", "mspdb140.dll", "c1.dll", "c1xx.dll", "c2.dll",
         "rc.exe", "cvtres.exe", "mt.exe", "cmd.exe", "python.exe", "py.exe",
         "tracker.exe", "filetracker.dll", "vctip.exe", "conhost.exe"}
COUNTERS = ("EventsLost", "LogBuffersLost", "RealTimeBuffersLost", "BuffersWritten")


def require(ok, msg):
    if not ok:
        raise ValueError(msg)


def hashed(text):
    return sha256(text.encode("utf-8")).hexdigest()


def label(path):
    name=ntpath.basename(path).lower()
    return name if name in KNOWN else "other"


def number(value):
    return int(value,16) if value.lower().startswith("0x") else int(value)


def local_name(node):
    return node.tag.rsplit("}",1)[-1]


def child(node, name):
    return next((x for x in node if local_name(x)==name),None)


def parse_events(path):
    """Decode only outer event envelopes; accept actual Windows XML namespace.

    Get-WinEvent uses .../events/event, not .../events. Never mistake a nested
    payload element named Event for an event envelope. Do not silently skip a
    malformed target-provider envelope.
    """
    rows=[]
    depth=0
    for kind,node in ET.iterparse(path,events=("start","end")):
        if kind=="start":
            depth+=1
            if depth==1:
                require(local_name(node)=="Events","Unexpected trace XML root")
            continue
        if depth==2:
            require(local_name(node)=="Event","Unexpected trace XML envelope")
            system=child(node,"System")
            require(system is not None,"Missing event System envelope")
            provider=child(system,"Provider")
            require(provider is not None,"Missing event Provider")
            if provider.get("Name","").lower()==PROVIDER.lower() or provider.get("Guid","").lower()==GUID:
                event_node=child(system,"EventID")
                require(event_node is not None and event_node.text,"Missing event ID")
                event=int(event_node.text)
                if event in (1,2,5):
                    data=child(node,"EventData")
                    require(data is not None,"Missing kernel EventData")
                    fields={x.get("Name"):x.text or "" for x in data if local_name(x)=="Data"}
                    require("ProcessID" in fields,"ETW process payload missing PID")
                    clock=child(system,"TimeCreated")
                    require(clock is not None and clock.get("SystemTime"),"Missing event timestamp")
                    ts=clock.get("SystemTime")
                    timestamp=datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp()
                    rows.append({"id":event,"time":timestamp,"pid":number(fields["ProcessID"]),
                                 "parent":number(fields.get("ParentProcessID","0")),
                                 "sequence":fields.get("ProcessSequenceNumber",""),
                                 "parent_sequence":fields.get("ParentProcessSequenceNumber",""),
                                 "created":fields.get("CreateTime",""),
                                 "image":fields.get("ImageName",""),
                                 "command":fields.get("CommandLine",""),
                                 "exit":fields.get("ExitCode","")})
            node.clear()
        depth-=1
    require(rows,"No target kernel process/image events decoded")
    return rows


def process_tree(events, root_pid):
    """Bound PID reuse by lifetime and prefer sequence identifiers where present."""
    starts=[]
    active={}
    for e in sorted(events,key=lambda x:(x["time"],{2:0,1:1,5:2}[x["id"]])):
        if e["id"]==1:
            row={**e,"stop":None,"exit_status":None,"parent_index":None,"images":[]}
            parent=active.get(e["parent"])
            if parent is not None:
                p=starts[parent]
                if not e["parent_sequence"] or not p["sequence"] or e["parent_sequence"]==p["sequence"]:
                    row["parent_index"]=parent
            if e["pid"] in active:
                # Missing stop: never attribute across an observed PID reuse.
                starts[active[e["pid"]]]["stop"]=e["time"]
                starts[active[e["pid"]]]["stop_inferred"]=True
            active[e["pid"]]=len(starts)
            starts.append(row)
        elif e["id"]==2:
            i=active.get(e["pid"])
            if i is not None:
                row=starts[i]
                if e["sequence"] and row["sequence"] and e["sequence"]!=row["sequence"]:
                    continue
                if e["created"] and row["created"] and e["created"]!=row["created"]:
                    continue
                row["stop"]=e["time"]
                row["exit_status"]=e["exit"]
                active.pop(e["pid"])
        else:
            i=active.get(e["pid"])
            if i is not None:
                starts[i]["images"].append(e["image"])
    roots=[i for i,r in enumerate(starts) if r["pid"]==root_pid and label(r["image"])=="msbuild.exe"]
    require(len(roots)==1,"Missing or ambiguous MSBuild process start")
    root=roots[0]
    selected={root}
    for i,r in enumerate(starts):
        if r["parent_index"] in selected:
            selected.add(i)
    # An image load can precede its process start. Count it but don't invent
    # ownership or include it in the verified lifetime-linked module set.
    pids={starts[i]["pid"] for i in selected}
    unassigned=sum(e["id"]==5 and e["pid"] in pids for e in events)-sum(len(starts[i]["images"]) for i in selected)
    return [starts[i] for i in sorted(selected)],max(0,unassigned)


def device_paths():
    """Translate NT volume paths using the actual local fixed-drive mappings."""
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    query=kernel.QueryDosDeviceW
    query.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32]
    query.restype=ctypes.c_uint32
    drive_type=kernel.GetDriveTypeW
    drive_type.argtypes=[ctypes.c_wchar_p]
    drive_type.restype=ctypes.c_uint32
    out={}
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive=char+":"
        if drive_type(drive+"\\")!=3:
            continue
        buf=ctypes.create_unicode_buffer(32768)
        if query(drive,buf,len(buf)):
            out[buf.value.casefold()]=drive
    return out


def local_path(raw, devices):
    s=raw
    if s.startswith("\\??\\"):
        s=s[4:]
    if s.startswith("\\\\?\\"):
        s=s[4:]
    for prefix,drive in sorted(devices.items(),key=lambda x:-len(x[0])):
        if s.casefold().startswith(prefix+"\\"):
            s=drive+s[len(prefix):]
            break
    if not re.match(r"^[A-Za-z]:\\",s) or s[:2].upper() not in devices.values():
        return None
    return s


def module_identity(raw, devices, cache):
    key=raw.casefold()
    if key not in cache:
        row={"label":label(raw),"path_sha256":hashed(key),"content_status":"unresolved"}
        s=local_path(raw,devices)
        if s:
            try:
                path=Path(s).resolve(strict=True)
                # Do not follow remote paths or reparse targets outside local fixed volumes.
                if local_path(str(path),devices) and path.is_file() and path.stat().st_size<=256*1024**2:
                    row["content"]=digest(path)
                    row["content_status"]="post-trace-file"
            except (OSError,RuntimeError):
                pass
        cache[key]=row
    return cache[key]


def sanitize_tree(tree, devices):
    rows=[]
    cache={}
    origin=tree[0]["time"]
    for i,r in enumerate(tree):
        images=[module_identity(p,devices,cache) for p in sorted(set(r["images"]),key=str.casefold) if p]
        rows.append({"process":i,"image":module_identity(r["image"],devices,cache),
                     "start_ms":round((r["time"]-origin)*1000,3),
                     "duration_ms":round((r["stop"]-r["time"])*1000,3) if r["stop"] is not None else None,
                     "stop_inferred":r.get("stop_inferred",False),
                     "exit":r["exit_status"],
                     "command_sha256":hashed(r["command"]) if r["command"] else None,
                     "images":images})
    counts=Counter(r["image"]["label"] for r in rows)
    compiler=sorted((r["start_ms"],r["start_ms"]+r["duration_ms"]) for r in rows
                    if r["image"]["label"]=="cl.exe" and r["duration_ms"] is not None and not r["stop_inferred"])
    peak=0
    for t,_ in compiler:
        peak=max(peak,sum(a<=t<b for a,b in compiler))
    return {"processes":rows,"process_counts":dict(counts),"peak_observed_cl_processes":peak,
            "all_observed_compilers_have_stops":counts["cl.exe"]==len(compiler),
            "unresolved_module_identities":sum(r["content_status"]!="post-trace-file" for r in cache.values()),
            "command_line_events":sum(r["command_sha256"] is not None for r in rows)}


def command_evidence(path):
    """Extract only allowlisted flags, source names and whole-line hashes."""
    rows=[]
    text=path.read_text(encoding="utf-8-sig",errors="replace")
    for line in text.splitlines():
        if not re.search(r'(?i)(?:^|[\\/"\s])cl\.exe(?=["\s]|$)',line):
            continue
        if not re.search(r'(?i)(?:^|[\\/"\s])sqlite_amalgamation\.c(?=["\s]|$)',line):
            continue
        rows.append({"sha256":hashed(line),
                     "flags":sorted({v.upper() for v in re.findall(r'(?i)(?:^|[\s"])(/MP\d*|/FS|/Brepro|/Qspectre|/Z7|/experimental:deterministic)(?=$|[\s"])',line)}),
                     "response_reference_observed":bool(re.search(r'(?:^|\s)@',line)),
                     "pathmap_observed":"/PATHMAP:" in line.upper()})
    return {"log":digest(path),"compiler_command_observations":rows,
            "source":"MSBuild diagnostic text; not ETW child argv"}


class Wnode(ctypes.Structure):
    _fields_=[("BufferSize",ctypes.c_uint32),("ProviderId",ctypes.c_uint32),
              ("HistoricalContext",ctypes.c_uint64),("TimeStamp",ctypes.c_int64),
              ("Guid",ctypes.c_byte*16),("ClientContext",ctypes.c_uint32),("Flags",ctypes.c_uint32)]


class TraceProperties(ctypes.Structure):
    _fields_=[("Wnode",Wnode)]+[(n,ctypes.c_uint32) for n in
      ("BufferSize","MinimumBuffers","MaximumBuffers","MaximumFileSize","LogFileMode",
       "FlushTimer","EnableFlags","AgeLimit","NumberOfBuffers","FreeBuffers",
       "EventsLost","BuffersWritten","LogBuffersLost","RealTimeBuffersLost")]+[
       ("LoggerThreadId",ctypes.c_void_p),("LogFileNameOffset",ctypes.c_uint32),
       ("LoggerNameOffset",ctypes.c_uint32)]


def stop_trace(name):
    # ControlTrace STOP returns the final session loss/buffer counters.
    size=ctypes.sizeof(TraceProperties)
    buffer=ctypes.create_string_buffer(size+8192)
    props=TraceProperties.from_buffer(buffer)
    props.Wnode.BufferSize=len(buffer)
    props.LoggerNameOffset=size
    props.LogFileNameOffset=size+4096
    fn=ctypes.WinDLL("advapi32",use_last_error=True).ControlTraceW
    fn.argtypes=[ctypes.c_uint64,ctypes.c_wchar_p,ctypes.c_void_p,ctypes.c_uint32]
    fn.restype=ctypes.c_uint32
    rc=fn(0,name,ctypes.byref(buffer),1)
    require(rc==0,"ETW stop/counters unavailable")
    return {n:getattr(props,n) for n in COUNTERS}


def command(tool, args, cwd=None, timeout=120):
    proc=subprocess.run([str(tool),*args],cwd=cwd,stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,timeout=timeout,check=False)
    return {"exit":proc.returncode,"output_sha256":sha256(proc.stdout).hexdigest()}


def sequence(replica):
    rows=[("a",False),("b",True),("a",True),("b",False)]
    rows+=list(reversed(rows))
    return rows if replica=="a" else [(r,not t) for r,t in rows]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--msbuild",type=Path,required=True)
    p.add_argument("--replica",choices=("a","b"),required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true","Disposable Windows CI only")
    root=Path(__file__).resolve().parents[1]
    temp=Path(os.environ["RUNNER_TEMP"]).resolve()
    require(args.output.resolve().is_relative_to(temp),"Private output required")
    require(not any(os.environ.get(k) for k in ("CL","_CL_","LINK","_LINK_","LIB")),"Implicit flags present")
    src=source_receipt(root)
    system=Path(os.environ["SYSTEMROOT"])/"System32"
    logman=system/"logman.exe"
    powershell=system/"WindowsPowerShell"/"v1.0"/"powershell.exe"
    report={"schema_version":1,"source":src,"replica":args.replica,
            "image_version":os.environ.get("ImageVersion",""),
            "scope":"mapped SQLite MSBuild trace vs untraced controls; process/image observation, not full argv or read closure",
            "tools":{f.name:digest(f) for f in (args.msbuild,logman,powershell)},
            "parent_environment_sha256":hashed(json.dumps({k:os.environ.get(k,"") for k in
                ("CL","_CL_","LINK","_LINK_","LIB","INCLUDE","PATH","TEMP","TMP","SYSTEMROOT")},sort_keys=True)),
            "sequence":sequence(args.replica),"runs":[]}
    def save():
        args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    save()
    roots={k:temp/name for k,name in (("a","process-trace-a"),("b","process-trace-relocated-longer-b"))}
    for work in roots.values():
        subprocess.run(["git","worktree","add","--detach",str(work),"HEAD"],cwd=root,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
        require(source_receipt(work)==src,"Relocated source mismatch")
    devices=device_paths()
    for index,(loc,traced) in enumerate(report["sequence"]):
        work=roots[loc]
        native=work/"native"
        build=native/"build"
        if build.exists():
            shutil.rmtree(build)
        row={"location":loc,"traced":traced,"status":"started"}
        report["runs"].append(row)
        save()
        with tempfile.TemporaryDirectory(prefix="build-trace-private-",dir=temp) as tmp:
            private=Path(tmp)
            trace=private/"capture.etl"
            xml=private/"events.xml"
            log=private/"msbuild.log"
            name="DoujinBuildTrace-"+str(os.getpid())+"-"+str(index)
            argv=[str(native/"sqlite_shared.vcxproj"),"/t:Rebuild","/m:1","/nr:false","/noAutoResponse",
                  "/v:minimal","/fl","/flp:logfile="+str(log)+";verbosity=diagnostic;encoding=UTF-8",
                  "/p:Configuration=Release","/p:Platform=x64","/p:PlatformToolset=v143",
                  "/p:VCToolsVersion=14.44.35207","/p:WindowsTargetPlatformVersion=10.0.26100.0",
                  "/p:DoujinReproDeterministic=mapped"]
            row["parent_argv_sha256"]=hashed(json.dumps(argv))
            row["parent_cwd_sha256"]=hashed(str(native))
            active=False
            try:
                if traced:
                    row["trace_start"]=command(logman,["create","trace",name,"-p",PROVIDER,"0x50","4",
                        "-o",str(trace),"-f","bin","-max","256","-bs","64","-nb","32","128","-ets"])
                    save()
                    require(row["trace_start"]["exit"]==0,"ETW start failed")
                    active=True
                start=time.monotonic()
                with subprocess.Popen([str(args.msbuild),*argv],cwd=native,stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT) as proc:
                    root_pid=proc.pid
                    try:
                        output,_=proc.communicate(timeout=240)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                        raise
                    row["build"]={"exit":proc.returncode,"seconds":round(time.monotonic()-start,3),
                                  "output_sha256":sha256(output).hexdigest(),
                                  "diagnostic_codes":sorted({m.decode("ascii") for m in re.findall(rb"\b(?:D9\d{3}|C\d{4}|MSB\d{4})\b",output)})}
            finally:
                if active:
                    try:
                        row["trace_counters"]=stop_trace(name)
                    except (OSError,ValueError):
                        row["trace_stop_fallback"]=command(logman,["stop",name,"-ets"])
                    save()
            require(row["build"]["exit"]==0 and not any(c in row["build"]["diagnostic_codes"] for c in ("D9002","D9007")),"Build rejected")
            row["command_evidence"]=command_evidence(log)
            objroot=build/"shared-obj"/"sqlite"/"x64"
            objs=list(objroot.rglob("sqlite_amalgamation.obj"))
            logs=list(objroot.rglob("CL.command.1.tlog"))
            require(len(objs)==len(logs)==1,"Missing SQLite build artifacts")
            require(command_flags(logs[0].read_text(encoding="utf-16"),"mapped"),"Wrong mapped flags")
            row["object"]={**digest(objs[0]),"coff":coff_detail(objs[0].read_bytes())}
            row["library"]=digest(build/"shared-lib"/"x64"/"doujinstyle_sqlite.lib")
            row["tracked_command"]=digest(logs[0])
            row["generated"]=tree_digest(build/"generated"/"x64"/"sqlite")
            row["status"]="built"
            save()
            if traced:
                etls=list(private.glob("*.etl"))
                require(len(etls)==1 and 0<etls[0].stat().st_size<256*1024**2,"Missing or capped ETW trace")
                row["trace"]=digest(etls[0])
                row["decode"]=command(powershell,["-NoProfile","-NonInteractive","-File",
                    str(root/"tools"/"export_build_trace.ps1"),"-Trace",str(etls[0]),"-Output",str(xml)],timeout=180)
                save()
                require(row["decode"]["exit"]==0 and xml.is_file() and xml.stat().st_size<512*1024**2,"Trace decode failed")
                try:
                    events=parse_events(xml)
                    row["decoded_event_counts"]=dict(Counter(e["id"] for e in events))
                    save()
                    tree,unassigned=process_tree(events,root_pid)
                except (ValueError,ET.ParseError,AttributeError,KeyError) as error:
                    row["status"]="decode-incomplete"
                    # Only static parser errors may be retained; never XML/paths.
                    row["decode_error_type"]=type(error).__name__
                    safe={"Missing event System envelope","Missing event Provider","Missing event ID",
                          "Missing kernel EventData","ETW process payload missing PID","Missing event timestamp",
                          "No target kernel process/image events decoded","Unexpected trace XML root",
                          "Unexpected trace XML envelope","Missing or ambiguous MSBuild process start"}
                    if str(error) in safe:
                        row["decode_error"]=str(error)
                    save()
                    continue
                row["observations"]=sanitize_tree(tree,devices)
                row["observations"]["unassigned_image_events_for_tree_pids"]=unassigned
                row["observations"]["provider_events"]=len(events)
                row["loss_counters_available"]="trace_counters" in row
                row["loss_free"]=row["loss_counters_available"] and all(row["trace_counters"][k]==0 for k in COUNTERS[:3])
                row["status"]="observed" if row["loss_free"] and row["observations"]["process_counts"].get("cl.exe",0)>0 and row["observations"]["all_observed_compilers_have_stops"] else "incomplete-trace"
            else:
                row["status"]="control"
            require(source_receipt(work)==src and source_receipt(root)==src,"Source changed")
            save()
    require(all(r["status"]==("observed" if r["traced"] else "control") for r in report["runs"]),"Incomplete process observation")
    print("BUILD-PROCESS-TRACE samples="+str(len(report["runs"])))


if __name__=="__main__":
    main()
