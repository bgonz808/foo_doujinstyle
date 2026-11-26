"""Isolated same-job MSVC librarian/linker replay. Sanitized diagnostics only.

Replays the native tool's own locally captured response, never a downloaded
or untrusted cached recipe. Output paths differ from the original build but
are fixed between repetitions. No package promotion or whole-closure claim.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import time
import sys
import ntpath

from tools.reproducibility import digest, pe_structure, source_receipt
from tools.reproducibility_detail import archive_detail, pdb_detail, coff_detail
from tools.symbol_identity import verify_pair
from tools.toolchain_inventory import tree_digest


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint_inputs(roots):
    """Hash original linkable inputs and compiler PDBs, not replay outputs."""
    out = {}
    for label, root in roots.items():
        require(root.is_dir(), "Missing original input root")
        rows = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".obj", ".lib", ".pdb", ".res", ".exp", ".json", ".pch"):
                rows[p.relative_to(root).as_posix()] = digest(p)
        require(rows, "Empty original input root")
        # Don't publish names; only aggregate identity and count.
        data = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        out[label] = {"sha256": sha256(data).hexdigest(), "files": len(rows)}
    return out


def read_response(path):
    data = path.read_bytes()
    require(len(data) <= 4*1024*1024, "Oversized native response")
    return data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig", errors="strict")


def response_features(text):
    return {"out": bool(re.search(r'(?i)(?:^|[\s"])/OUT:', text)),
            "object": bool(re.search(r'(?i)\.obj(?:[\s"]|$)', text)),
            "debug": bool(re.search(r'(?i)(?:^|[\s"])/DEBUG(?::|[\s"]|$)', text))}


def capture_inventory(bundle):
    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    extensions = {}
    responses = []
    for p in files:
        ext = p.suffix.lower()
        ext = ext if ext in (".rsp",".obj",".lib",".pdb",".res",".json",".txt",".exe",".dll") else "other"
        extensions[ext] = extensions.get(ext,0)+1
        if ext == ".rsp":
            row = digest(p)
            try:
                row.update(response_features(read_response(p)))
            except (ValueError, UnicodeError):
                row["unreadable"] = True
            responses.append(row)
    return {"files":len(files),"extensions":extensions,"responses":responses}


def response_candidate(bundle):
    candidates = []
    for path in bundle.rglob("*.rsp"):
        text = read_response(path)
        features = response_features(text)
        if features["object"] and features["debug"]:
            candidates.append((path, text))
    require(len(candidates)==1, "Native full-link response missing or ambiguous")
    path,text=candidates[0]
    require(not re.search(r'(?i)(?:^|[\s"])/LINKREPRO',text), "Recursive capture option in response")
    require(not re.search(r'(?m)(?:^|[\s"])@',text), "Nested response not yet supported")
    return path


def run_tool(tool, args, cwd, env=None):
    start = time.monotonic()
    # Capture raw paths/diagnostics privately; do not upload or print them.
    proc = subprocess.run([str(tool), *args], cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=180, check=False, env=env)
    if proc.returncode:
        # Do not emit arbitrary tool output or paths. Report only an allowlisted
        # helper basename if the native diagnostic names it.
        helper = ""
        if proc.returncode == 1158:
            match = re.search(rb"(?i)LNK1158[^\r\n]*?cannot run ['\x22]?([^'\x22\r\n]+)", proc.stdout)
            if match:
                name = ntpath.basename(match.group(1).decode("ascii",errors="ignore").strip()).lower()
                if name in ("rc.exe","mt.exe","cvtres.exe","mspdbsrv.exe","rc","mt","cvtres","mspdbsrv"):
                    helper = " helper=" + name
        raise ValueError(f"Native replay {tool.name} exited {proc.returncode}" + helper)
    require(not re.search(rb"(?i)\b(?:LNK4\d{3}|D9002)\b", proc.stdout),
            "Native replay emitted linker warning or ignored option")
    return round(time.monotonic()-start, 3)


def measure_library(tool, obj, out):
    original = digest(obj)
    rows=[]
    for _ in range(2):
        out.unlink(missing_ok=True)
        elapsed=run_tool(tool, ["/NOLOGO", "/MACHINE:X64", "/Brepro", "/OUT:"+str(out), str(obj)], out.parent)
        require(digest(obj)==original, "Librarian changed frozen object")
        rows.append({"seconds":elapsed, **digest(out), "members":archive_detail(out.read_bytes())})
    return {"object":original, "tool":digest(tool), "runs":rows,
            "equal":rows[0]["sha256"]==rows[1]["sha256"]}


def windows_system_root(env):
    # os.environ uppercases Windows keys; a plain dict loses its
    # case-insensitive lookup behavior.
    values = {v for k,v in env.items() if k.upper()=="SYSTEMROOT" and v}
    require(len(values)==1, "Missing or ambiguous Windows system root")
    return Path(values.pop())


def measure_link(tool, bundle, out, sdk_bin):
    rsp=response_candidate(bundle)
    original=tree_digest(bundle)
    helpers = {}
    for name, directory in (("rc.exe",sdk_bin),("mt.exe",sdk_bin),("cvtres.exe",tool.parent)):
        path = directory/name
        require(path.is_file(), "Pinned linker helper missing")
        helpers[name] = digest(path)
    env = dict(os.environ)
    require(not env.get("LINK") and not env.get("_LINK_"), "Unexpected implicit linker arguments")
    # Restrict child executable lookup to verified toolchain roots and core OS.
    system = windows_system_root(env)
    require(system.is_absolute() and (system/"System32").is_dir(), "Invalid Windows system root")
    env["PATH"] = os.pathsep.join(map(str,(tool.parent,sdk_bin,system/"System32",system)))
    exe=out/"publish_references.exe"
    pdb=out/"publish_references.pdb"
    rows=[]
    for _ in range(2):
        for p in out.iterdir():
            require(p.is_file(), "Unexpected replay output directory")
            p.unlink()
        elapsed=run_tool(tool, ["@"+str(rsp), "/OUT:"+str(exe), "/PDB:"+str(pdb),
                                "/IMPLIB:"+str(out/"publish_references.lib"),
                                "/PDBALTPATH:publish_references.pdb", "/INCREMENTAL:NO"], rsp.parent, env)
        require(tree_digest(bundle)==original, "Link replay changed native bundle")
        pe,syms=exe.read_bytes(),pdb.read_bytes()
        rows.append({"seconds":elapsed, "pe":{**digest(exe),"structure":pe_structure(pe)},
                     "pdb":{**digest(pdb),"msf":pdb_detail(syms)},
                     "binding":verify_pair(pe,syms,pdb.name)})
    return {"tool":digest(tool),"helpers":helpers,"bundle":original,"response_sha256":digest(rsp)["sha256"],
            "runs":rows,"pe_equal":rows[0]["pe"]["sha256"]==rows[1]["pe"]["sha256"],
            "pdb_equal":rows[0]["pdb"]["sha256"]==rows[1]["pdb"]["sha256"]}


def compiler_arguments(argv, source, cwd):
    """Redirect output options only; preserve actual frontend/codegen flags."""
    out = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if i == 1 and re.search(r"(?:^|[\\/])cl(?:\.exe)?$",arg,re.I):
            continue
        require(not arg.startswith("@"), "Unexpanded compiler response")
        if arg in ("/Fo","/Fd","/Fi"):
            require(i<len(argv),"Missing compiler output operand")
            i+=1
            continue
        if arg.startswith(("/Fo","/Fd","/Fi")):
            continue
        out.append(arg)
    require(all(flag in out for flag in ("/c","/Z7","/Qspectre","/Brepro")),
            "Tracked compiler mitigation or debug flag missing")
    require(not any(a.startswith(("/Yu","/Yc","/Fp","/Fa","/Fe","/FR","/Fr")) or a in ("/P","/E","/EP") for a in out),
            "Unsupported compiler side output or precompiled input")
    def key(p):
        return ntpath.normcase(ntpath.normpath(ntpath.join(str(cwd),str(p))))
    names = [key(a[3:] if a.startswith(("/Tc","/Tp")) and len(a)>3 else a) for a in out]
    if key(source) not in names:
        out.append(str(source))
    return out


def measure_compiler(tool, msbuild, root, sdk, out):
    sys.path.insert(0,str(root/"native"))
    from analyze_windows import tracking_records, split_windows_command, evaluate_includes
    native=root/"native"
    source=native/"sqlite_amalgamation.c"
    logs=list((native/"build"/"shared-obj"/"sqlite"/"x64").rglob("CL.command.1.tlog"))
    require(len(logs)==1,"Missing or ambiguous SQLite compiler tracking log")
    matches=[line for roots,line in tracking_records(logs[0].read_text(encoding="utf-16"))
             if len(roots)==1 and ntpath.normcase(ntpath.normpath(roots[0]))==ntpath.normcase(str(source))]
    require(len(matches)==1,"Missing or ambiguous SQLite compile command")
    args=compiler_arguments(split_windows_command(matches[0]),source,native)
    includes=evaluate_includes(msbuild,native/"sqlite_shared.vcxproj",sdk,"x64")
    env=dict(os.environ)
    require(not env.get("CL") and not env.get("_CL_"),"Unexpected implicit compiler arguments")
    env["INCLUDE"]=";".join(map(str,includes))
    generated=native/"build"/"generated"/"x64"/"sqlite"
    before=tree_digest(generated)
    result={"tool":digest(tool),"tracked_command_sha256":sha256(matches[0].encode()).hexdigest(),
            "include_order_sha256":sha256(env["INCLUDE"].encode()).hexdigest(),
            "generated_source":before,"source":digest(source),"runs":[]}
    obj=out/"sqlite_amalgamation.obj"
    pre=out/"sqlite_amalgamation.i"
    for _ in range(2):
        for f in (obj,pre):
            f.unlink(missing_ok=True)
        pretime=run_tool(tool,[*args,"/P","/Fi"+str(pre)],native,env)
        prehash=digest(pre)
        elapsed=run_tool(tool,[*args,"/Fo"+str(obj),"/Fd"+str(out/"compiler.pdb")],native,env)
        result["runs"].append({"preprocess_seconds":pretime,"preprocessed":prehash,
                               "compile_seconds":elapsed,"object":{**digest(obj),"coff":coff_detail(obj.read_bytes())}})
    require(tree_digest(generated)==before and digest(source)==result["source"],"Compiler source changed")
    a,b=result["runs"]
    result["preprocessed_equal"]=a["preprocessed"]==b["preprocessed"]
    result["object_equal"]=a["object"]["sha256"]==b["object"]["sha256"]
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tool-dir",type=Path,required=True)
    p.add_argument("--sdk",type=Path,required=True)
    p.add_argument("--msbuild",type=Path,required=True)
    p.add_argument("--sdk-bin",type=Path,required=True)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true", "Disposable Windows CI only")
    root=Path(__file__).resolve().parents[1]
    temp=Path(os.environ["RUNNER_TEMP"]).resolve()
    bundle=args.bundle.resolve()
    output=args.output.resolve()
    require(bundle.is_relative_to(temp) and output.is_relative_to(temp)
            and not output.is_relative_to(bundle), "Replay paths must remain private")
    tools=args.tool_dir.resolve()
    require(tools.parts[-4:]==("14.44.35207","bin","Hostx64","x64"), "Wrong MSVC tool directory")
    sdk_bin=args.sdk_bin.resolve()
    require(sdk_bin.parts[-3:]==("bin","10.0.26100.0","x64"), "Wrong SDK tool directory")
    src=source_receipt(root)
    build=root/"native"/"build"
    roots={"build":build,"sdk":args.sdk.resolve()}
    before=fingerprint_inputs(roots)
    obj=list((build/"shared-obj"/"sqlite"/"x64").rglob("sqlite_amalgamation.obj"))
    require(len(obj)==1, "Missing or ambiguous SQLite object")
    stage=output.parent/"stage-replay-output"
    stage.mkdir(exist_ok=False)
    libdir=stage/"lib"; libdir.mkdir()
    linkdir=stage/"link"; linkdir.mkdir()
    cldir=stage/"compile"; cldir.mkdir()
    report={"schema_version":1,"source":src,"original_inputs":before,
            "scope":"same-job frozen-input stage replay; not original-path or independent qualification",
            "environment_sha256":sha256(json.dumps({k:os.environ.get(k,"") for k in ("LINK","_LINK_","LIB","PATH","INCLUDE","CL","_CL_")},sort_keys=True).encode()).hexdigest()}
    report["capture_inventory"]=capture_inventory(bundle)
    report["failures"]={}
    stages=(
        ("librarian", lambda: measure_library(tools/"lib.exe",obj[0],libdir/"doujinstyle_sqlite.lib")),
        ("compiler", lambda: measure_compiler(tools/"cl.exe",args.msbuild,root,args.sdk.resolve(),cldir)),
        ("linker", lambda: measure_link(tools/"link.exe",bundle,linkdir,sdk_bin)),
    )
    for label, operation in stages:
        try:
            report[label]=operation()
        except (ValueError,OSError,subprocess.SubprocessError,UnicodeError,RuntimeError,KeyError) as error:
            detail=str(error)
            safe = detail if re.fullmatch(r"Native replay (?:cl|link|lib)\.exe exited [0-9]+(?: helper=(?:rc|mt|cvtres|mspdbsrv)(?:\.exe)?)?",detail) or detail in {
                "Native full-link response missing or ambiguous","Recursive capture option in response",
                "Nested response not yet supported","Link replay changed native bundle",
                "Native replay emitted linker warning or ignored option",
                "Missing or ambiguous SQLite compiler tracking log",
                "Missing or ambiguous SQLite compile command",
                "Tracked compiler mitigation or debug flag missing",
                "Unsupported compiler side output or precompiled input",
                "Unexpected implicit compiler arguments",
                "Unexpanded compiler response","Pinned linker helper missing","Unexpected implicit linker arguments",
                "Missing or ambiguous Windows system root","Invalid Windows system root"
            } else type(error).__name__
            report["failures"][label]=safe
    report["status"]="incomplete" if report["failures"] else "completed"
    if "compiler" in report:
        report["compiler"]["original_object"]={**digest(obj[0]),"coff":coff_detail(obj[0].read_bytes())}
        report["compiler"]["matches_original"] = [r["object"]["sha256"]==report["compiler"]["original_object"]["sha256"] for r in report["compiler"]["runs"]]
    report["original_inputs_unchanged"]=fingerprint_inputs(roots)==before
    require(report["original_inputs_unchanged"] and source_receipt(root)==src,
            "Original inputs changed during replay")
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("STAGE-REPLAY status="+report["status"]+" librarian="+str(report.get("librarian",{}).get("equal","unavailable")))
    if report["status"]!="completed":
        raise SystemExit(1)


if __name__=="__main__":
    main()
