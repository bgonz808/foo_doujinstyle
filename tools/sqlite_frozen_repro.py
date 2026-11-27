"""Isolate MSVC frontend vs backend variation using real SQLite frozen input.

Diagnostic only: compare repeats within each input/mode, not assume preprocessed
and original-source debug records are identical. Never rewrite output objects.
"""
import argparse
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from statistics import median

from tools.reproducibility import digest, source_receipt
from tools.reproducibility_detail import coff_detail
from tools.stage_replay import compiler_arguments
from tools.toolchain_inventory import tree_digest
from tools.artifact_forensics import object_views

MODES = ("source", "frozen")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def input_args(argv, source, frozen, cwd):
    """Replace exactly one C source operand; retain all other compiler flags."""
    def key(path):
        return ntpath.normcase(ntpath.normpath(ntpath.join(str(cwd), str(path))))
    out, count, i = [], 0, 0
    while i < len(argv):
        a = argv[i]
        i += 1
        if a == "/Tc":
            require(i < len(argv) and key(argv[i]) == key(source), "Unexpected C source operand")
            i += 1
            out.extend(("/Tc", str(frozen)))
            count += 1
        elif a.startswith("/Tc") and len(a) > 3:
            require(key(a[3:]) == key(source), "Unexpected C source operand")
            out.append("/Tc" + str(frozen))
            count += 1
        elif a == "/Tp" or a.startswith("/Tp"):
            raise ValueError("Unexpected C++ source operand")
        elif not a.startswith(("/", "-")) and key(a) == key(source):
            out.append("/Tc" + str(frozen))
            count += 1
        else:
            out.append(a)
    require(count == 1, "Missing or ambiguous tracked C source")
    return out


def sequence(replica):
    order = ["source", "frozen", "frozen", "source"] * 2
    return order if replica == "a" else ["frozen" if m == "source" else "source" for m in order]


def run(tool, argv, cwd, env, timeout=180):
    start = time.monotonic()
    proc = subprocess.run([str(tool), *argv], cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout, check=False)
    codes = sorted({m.decode("ascii") for m in re.findall(rb"\b(?:D9\d{3}|C\d{4}|MSB\d{4})\b", proc.stdout)})
    return {"ok":proc.returncode == 0 and not any(c in codes for c in ("D9002","D9007")),
            "exit":proc.returncode,"diagnostic_codes":codes,
            "diagnostic_sha256":sha256(proc.stdout).hexdigest(),
            "argv_sha256":sha256(json.dumps(argv).encode()).hexdigest(),
            "seconds":round(time.monotonic()-start,3)}


def summary(rows):
    out={}
    for mode in MODES:
        group=[r for r in rows if r["mode"]==mode and r.get("object")]
        out[mode]={"samples":len(group),"complete":len(group)==4,
                   "unique_objects":len({r["object"]["sha256"] for r in group}),
                   "median_seconds":median(r["compile"]["seconds"] for r in group) if group else None}
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("msbuild","msvc-bin","llvm-bin","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--replica",choices=("a","b"),required=True)
    args=p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true","Disposable Windows CI only")
    root=Path(__file__).resolve().parents[1]
    temp=Path(os.environ["RUNNER_TEMP"]).resolve()
    require(args.output.resolve().is_relative_to(temp),"Private report required")
    tools=args.msvc_bin.resolve()
    llvm=args.llvm_bin.resolve()
    require(tools.parts[-4:]==("14.44.35207","bin","Hostx64","x64"),"Wrong MSVC pin")
    require(llvm.is_relative_to(temp),"Wrong LLVM root")
    require(not any(os.environ.get(k) for k in ("CL","_CL_")),"Implicit compiler flags")
    src=source_receipt(root)
    # Same sibling-import contract as existing stage replay.
    sys.path.insert(0,str(root/"native"))
    from analyze_windows import tracking_records, split_windows_command, evaluate_includes
    compiler=tools/"cl.exe"
    report={"schema_version":1,"source":src,"replica":args.replica,
            "image_version":os.environ.get("ImageVersion",""),
            "scope":"real mapped SQLite; repeated original C vs one frozen preprocessed C input per physical root; no cross-mode equivalence or full closure claim",
            "tools":{f.name:digest(f) for f in [args.msbuild,compiler,tools/"c1.dll",tools/"c2.dll"]
                     if f.is_file()},"roots":[]}
    def save():
        args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    save()
    locations=("a","b") if args.replica=="a" else ("b","a")
    for loc in locations:
        work=temp/("sqlite-frozen-a" if loc=="a" else "sqlite-frozen-relocated-longer-b")
        require(not work.exists(),"Fresh worktree required")
        subprocess.run(["git","worktree","add","--detach",str(work),"HEAD"],cwd=root,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
        require(source_receipt(work)==src,"Worktree source mismatch")
        native=work/"native"
        build=native/"build"
        project=native/"sqlite_shared.vcxproj"
        row={"location":loc,"status":"started","preprocess":[],"runs":[]}
        report["roots"].append(row)
        save()
        env=dict(os.environ)
        cmd=[str(project),"/t:Rebuild","/m:1","/nr:false","/noAutoResponse","/v:minimal",
             "/p:Configuration=Release","/p:Platform=x64","/p:PlatformToolset=v143",
             "/p:VCToolsVersion=14.44.35207","/p:WindowsTargetPlatformVersion=10.0.26100.0",
             "/p:DoujinReproDeterministic=mapped"]
        row["build"]=run(args.msbuild,cmd,native,env)
        save()
        require(row["build"]["ok"],"Mapped SQLite build rejected")
        objroot=build/"shared-obj"/"sqlite"/"x64"
        logs=list(objroot.rglob("CL.command.1.tlog"))
        objects=list(objroot.rglob("sqlite_amalgamation.obj"))
        require(len(logs)==len(objects)==1,"Missing tracked SQLite object")
        source=native/"sqlite_amalgamation.c"
        matches=[line for roots,line in tracking_records(logs[0].read_text(encoding="utf-16"))
                 if len(roots)==1 and ntpath.normcase(ntpath.normpath(roots[0]))==ntpath.normcase(str(source))]
        require(len(matches)==1,"Missing tracked source command")
        argv=compiler_arguments(split_windows_command(matches[0]),source,native)
        require("/experimental:deterministic" in argv and
                any(a.lower().startswith("/pathmap:") and a.upper().endswith("=X:\\DOUJIN-REPRO\\NATIVE") for a in argv),
                "Mapped compiler options not observed")
        includes=evaluate_includes(args.msbuild,project,work,"x64")
        env["INCLUDE"]=";".join(map(str,includes))
        system=Path(env["SYSTEMROOT"])
        env["PATH"]=os.pathsep.join(map(str,(tools,system/"System32",system)))
        generated=build/"generated"/"x64"/"sqlite"
        generated_id=tree_digest(generated)
        source_id=digest(source)
        row.update({"tracked_command":digest(logs[0]),"source":source_id,"generated":generated_id,
                    "include_order_sha256":sha256(env["INCLUDE"].encode()).hexdigest(),
                    "environment_sha256":sha256(json.dumps({k:env.get(k,"") for k in
                      ("CL","_CL_","INCLUDE","PATH","TEMP","TMP","SYSTEMROOT")},sort_keys=True).encode()).hexdigest(),
                    "msbuild_object":{**digest(objects[0]),"coff":coff_detail(objects[0].read_bytes())}})
        out=temp/("sqlite-frozen-output-"+loc)
        out.mkdir(exist_ok=False)
        frozen=out/"frozen.i"
        check=out/"check.i"
        obj=out/"sqlite_amalgamation.obj"
        for pre in (frozen,check):
            result=run(compiler,[*argv,"/P","/Fi"+str(pre)],native,env)
            row["preprocess"].append(result)
            if result["ok"]:
                result["input"]=digest(pre)
            save()
            require(result["ok"],"SQLite preprocessing rejected")
        frozen_id=digest(frozen)
        row["preprocessing_equal"]=frozen_id==digest(check)
        frozen_argv=input_args(argv,source,frozen,native)
        seen=set()
        for mode in sequence(args.replica):
            obj.unlink(missing_ok=True)
            chosen=argv if mode=="source" else frozen_argv
            result={"mode":mode,"compile":run(compiler,[*chosen,"/Fo"+str(obj),"/Fd"+str(out/"compiler.pdb")],native,env)}
            row["runs"].append(result)
            if result["compile"]["ok"]:
                result["object"]={**digest(obj),"coff":coff_detail(obj.read_bytes())}
            row["profiles"]=summary(row["runs"])
            save()
            require(digest(frozen)==frozen_id and tree_digest(generated)==generated_id
                    and digest(source)==source_id and digest(objects[0])["sha256"]==row["msbuild_object"]["sha256"],
                    "Original or frozen input changed")
            if result.get("object") and result["object"]["sha256"] not in seen:
                seen.add(result["object"]["sha256"])
                result["forensics"]=object_views(llvm,obj)
            save()
        require(source_receipt(work)==src and source_receipt(root)==src,"Source changed")
        row["status"]="complete" if all(v["complete"] for v in row["profiles"].values()) else "incomplete"
        save()
    require(all(r["status"]=="complete" for r in report["roots"]),"Frozen-input experiment incomplete")
    print("SQLITE-FROZEN "+json.dumps([r["profiles"] for r in report["roots"]],sort_keys=True))


if __name__=="__main__":
    main()
