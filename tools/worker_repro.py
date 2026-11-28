"""Real x64 worker build/debug format and frozen native-vs-LLD link research."""
import argparse
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from statistics import median

from tools.reproducibility import digest, source_receipt, pe_structure
from tools.reproducibility_detail import coff_detail, pdb_detail
from tools.symbol_identity import verify_pair
from tools.stage_replay import response_candidate, read_response, fingerprint_inputs, windows_system_root, capture_inventory
from tools.toolchain_inventory import tree_digest
from tools.artifact_forensics import object_views, pdb_views, pe_views

PROFILES = ("baseline", "mapped-zi", "mapped-z7")
EXPECTED_OBJECTS = {"publish_references.obj", "worker_build_identity.obj", "descriptor.obj", "utf8proc.obj"}
EXPECTED_SOURCES = {"publish_references.cpp", "worker_build_identity.cpp", "descriptor.cpp", "utf8proc.c"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sequence(replica):
    rows = [(p, r) for p in PROFILES for r in ("a", "b")]
    rows += list(reversed(rows))
    return rows if replica == "a" else rows[4:] + rows[:4]


def flag_evidence(text, profile):
    # Do not confuse a path substring with an option. Preserve exact /Zi vs /ZI.
    def present(flag, insensitive=True, operand=False):
        suffix = r"[^ \t\r\n\"]+" if operand else ""
        return bool(re.search(r'(?:^|[\s"])' + re.escape(flag) + suffix + r'(?=$|[\s"])',
                              text, re.I if insensitive else 0))
    # /pathmap may have a quoted operand. The specific logical target is
    # separately verified, without publishing the physical source path.
    mapped = bool(re.search(r'(?:^|[\s"])/pathmap:', text, re.I))
    observed = {
        "brepro":present("/Brepro"), "spectre":present("/Qspectre"),
        "mp2":present("/MP2"), "md":present("/MD"),
        "cfg":present("/guard:cf"),
        "experimental":present("/experimental:deterministic"),
        "pathmap":mapped,
        "canonical_target":mapped and "X:\\DOUJIN-REPRO\\NATIVE" in text.upper(),
        "zi":present("/Zi", False), "edit_continue":present("/ZI", False),
        "z7":present("/Z7"), "fs":present("/FS"),
        "gl":present("/GL"), "gs":present("/GS"), "sdl":present("/sdl"),
    }
    # /MP and /FS are driver/task scheduling options, not reliable per-TU
    # tlog evidence. Report them separately, do not infer actual concurrency.
    expected = {k:True for k in ("brepro","spectre","md","cfg","gs","sdl")}
    expected.update({"experimental":profile!="baseline","pathmap":profile!="baseline",
                     "canonical_target":profile!="baseline","zi":profile!="mapped-z7",
                     "z7":profile=="mapped-z7","edit_continue":False,"gl":False})
    # Tlog may represent /Zi with canonicalized case; report, do not guess.
    return {"observed":observed,"mismatches":[k for k,v in expected.items() if observed[k]!=v]}


def command_valid(text, profile):
    return not flag_evidence(text, profile)["mismatches"]


def tracked_evidence(path, profile):
    # analyze_windows is a script module with sibling imports; use the same
    # explicit native-module root as stage_replay.measure_compiler.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native"))
    try:
        from analyze_windows import tracking_records
    finally:
        sys.path.pop(0)
    records=[]
    for roots, command in tracking_records(path.read_text(encoding="utf-16")):
        names=[ntpath.basename(root).lower() for root in roots]
        records.append({"source_count":len(roots), "known_sources":[n for n in names if n in EXPECTED_SOURCES],
                        "command_sha256":sha256(command.encode()).hexdigest(),
                        **flag_evidence(command,profile)})
    require(records,"Empty tracking log")
    return records


def scheduling_evidence(path):
    """Private diagnostic logger; no raw commands, paths or binlogs published.

    Command-line records are distinct from task parameters and from actual
    process creation. A missing /MP2 observation is not a measured serial build.
    """
    text=path.read_text(encoding="utf-8-sig",errors="replace")
    commands=[]
    for line in text.splitlines():
        if not re.search(r'(?i)(?:^|[\\/"\s])cl\.exe(?=["\s]|$)',line):
            continue
        sources=[n for n in sorted(EXPECTED_SOURCES) if re.search(r'(?i)(?:^|[\\/"\s])'+re.escape(n)+r'(?=["\s]|$)',line)]
        if not sources:
            continue
        options=sorted(set(re.findall(r'(?i)(?:^|[\s"])(/MP\d*|/FS)(?=$|[\s"])',line)))
        commands.append({"known_sources":sources,"scheduling_options":[v.upper() for v in options],
                         "command_sha256":sha256(line.encode()).hexdigest()})
    return {"log":digest(path),"requested_compiler_jobs":2,
            "command_observations":commands,
            "mp2_observed_in_command_log":any("/MP2" in c["scheduling_options"] for c in commands),
            "process_concurrency_verified":False}


def checkpoint(path, report):
    report["profiles"]=summarize(report["runs"])
    path.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")


def native_response_safe(text):
    # Never replay LTCG, nested responses, or explicitly redirected side outputs.
    return not bool(re.search(r"(?i)(?:^|[\s\"])/(?:LTCG(?:[:\s\"]|$)|GL(?:[\s\"]|$)|LTCGOUT:|PGD:|ILK:|MANIFESTFILE:|MAP:|PDBSTRIPPED:)", text))


def run(tool, args, cwd, env, timeout):
    start = time.monotonic()
    proc = subprocess.run([str(tool), *args], cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout, check=False)
    output = proc.stdout
    # Sanitize rather than emitting any path or compiler output.
    diagnostic = sorted({m.decode("ascii") for m in re.findall(rb"\b(?:D9\d{3}|LNK\d{4}|C\d{4}|MSB\d{4})\b", output)})
    ignored = bool(re.search(rb"(?i)(D9002|D9007|LNK4\d{3}|unknown argument|unused argument|ignoring unknown|unsupported option|lld-link:\s+warning:)", output))
    return {"ok":proc.returncode == 0 and not ignored, "exit":proc.returncode,
            "diagnostic_codes":diagnostic, "diagnostic_sha256":sha256(output).hexdigest(),
            "argv_sha256":sha256(json.dumps(args).encode()).hexdigest(),
            "seconds":round(time.monotonic()-start, 3)}


def pe_record(pe, pdb):
    return {"pe":{**digest(pe), "structure":pe_structure(pe.read_bytes())},
            "pdb":{**digest(pdb), "streams":pdb_detail(pdb.read_bytes())},
            "binding":verify_pair(pe.read_bytes(), pdb.read_bytes(), pdb.name)}


def diagnostic_error(error):
    allowed={"Forensic output exceeds diagnostic limit","Read-only inspector changed input",
             "Native full-link response missing or ambiguous","Recursive capture option in response",
             "Nested response not yet supported","Oversized native response",
             "Missing original input root","Empty original input root","Missing or ambiguous Windows system root"}
    result={"error_type":type(error).__name__}
    if str(error) in allowed:
        result["reason"]=str(error)
    return result


def optional_views(fn, *args):
    try:
        return {"status":"observed","views":fn(*args)}
    except (ValueError,OSError,subprocess.SubprocessError,UnicodeError,RuntimeError) as error:
        return {"status":"unavailable",**diagnostic_error(error)}


def replay(bundle, out, native_link, lld, sdk_bin, llvm, build, evidence, save):
    evidence["capture"]=capture_inventory(bundle)
    evidence["stage"]="response-selection"
    save()
    rsp = response_candidate(bundle)
    text = read_response(rsp)
    evidence["response"]=digest(rsp)
    evidence["stage"]="response-policy"
    save()
    if not native_response_safe(text):
        return {"status":"unsafe-or-ltcg-response", "response":digest(rsp), "runs":[]}
    # Do not remove unsupported native flags. If LLD rejects them, record it.
    original, inputs = tree_digest(bundle), fingerprint_inputs({"build":build})
    env = dict(os.environ)
    system = windows_system_root(env)
    env["PATH"] = os.pathsep.join(map(str,(native_link.parent, sdk_bin, llvm, system/"System32", system)))
    require(not env.get("LINK") and not env.get("_LINK_"), "Implicit linker arguments")
    out.mkdir(exist_ok=False)
    pe,pdb = out/"publish_references.exe",out/"publish_references.pdb"
    rows=evidence.setdefault("runs",[])
    for name,tool in (("native",native_link),("lld",lld),("lld",lld),("native",native_link)):
        evidence["stage"]="invoke-"+name
        save()
        for f in out.iterdir():
            require(f.is_file(), "Unexpected replay directory")
            f.unlink()
        argv=["@"+str(rsp),"/OUT:"+str(pe),"/PDB:"+str(pdb),"/IMPLIB:"+str(out/"worker.lib"),
              "/PDBALTPATH:"+pdb.name,"/INCREMENTAL:NO"]
        if name=="lld":
            argv += ["/lldignoreenv","/pdbsourcepath:X:\\doujin-repro"]
        row={"linker":name,**run(tool,argv,rsp.parent,env,180)}
        rows.append(row)
        evidence["stage"]="verify-"+name
        save()
        require(tree_digest(bundle)==original and fingerprint_inputs({"build":build})==inputs,
                "Replay changed original inputs")
        if row["ok"]:
            row.update(pe_record(pe,pdb))
            save()
            row["forensics"]={"pe":optional_views(pe_views,llvm,pe),"pdb":optional_views(pdb_views,llvm,pdb)}
        require(tree_digest(bundle)==original and fingerprint_inputs({"build":build})==inputs,
                "Replay changed original inputs")
        save()
    return {"status":"completed" if sum(r["ok"] for r in rows if r["linker"]=="native")==2 else "native-control-rejected",
            "response":digest(rsp), "runs":rows}


def summarize(rows):
    out={}
    for p in PROFILES:
        group=[r for r in rows if r["profile"]==p and r["status"]=="ok"]
        result={"samples":len(group),"complete":len(group)==4}
        if group:
            result.update({"median_seconds":median(r["build"]["seconds"] for r in group),
                           "unique_pe":len({r["pe"]["sha256"] for r in group}),
                           "unique_pdb":len({r["pdb"]["sha256"] for r in group}),
                           "unique_sqlite":len({r["sqlite"]["sha256"] for r in group}),
                           "unique_object_hashes":{n:len({r["objects"][n]["sha256"] for r in group})
                                                   for n in sorted(EXPECTED_OBJECTS)}})
        out[p]=result
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("msbuild","msvc-bin","sdk-bin","llvm-bin","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--replica",choices=("a","b"),required=True)
    args=p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true", "Disposable Windows CI only")
    repo=Path(__file__).resolve().parents[1]
    temp=Path(os.environ["RUNNER_TEMP"]).resolve()
    require(args.output.resolve().is_relative_to(temp), "Private report required")
    src=source_receipt(repo)
    tools=args.msvc_bin.resolve()
    sdk=args.sdk_bin.resolve()
    llvm=args.llvm_bin.resolve()
    require(tools.parts[-4:]==("14.44.35207","bin","Hostx64","x64") and sdk.parts[-3:]==("bin","10.0.26100.0","x64"),"Wrong native pins")
    require(llvm.is_relative_to(temp),"Unverified LLVM location")
    env=dict(os.environ)
    require(not any(env.get(k) for k in ("CL","_CL_","LINK","_LINK_")), "Unexpected implicit flags")
    env["DOUJIN_WORKER_REPRO"]="1"
    roots={k:temp/name for k,name in (("a","worker-root-a"),("b","worker-root-relocated-b"))}
    for work in roots.values():
        require(not work.exists(),"Fresh checkout required")
        subprocess.run(["git","worktree","add","--detach",str(work),"HEAD"],cwd=repo,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
        require(source_receipt(work)==src,"Worktree source mismatch")
    report={"schema_version":1,"source":src,"replica":args.replica,
            "scope":"real worker x64, mapped Zi/Z7; native-vs-LLD frozen replay; no component, BinSkim or runtime qualification",
            "image_version":os.environ.get("ImageVersion",""),
            "tools":{n:digest(f) for n,f in (("cl",tools/"cl.exe"),("link",tools/"link.exe"),("lib",tools/"lib.exe"),
                                           ("lld",llvm/"lld-link.exe"),("msbuild",args.msbuild))},
            "sequence":sequence(args.replica),"runs":[]}
    checkpoint(args.output,report)
    for profile, loc in report["sequence"]:
        work=roots[loc]
        native=work/"native"
        build=native/"build"
        capture=temp/("worker-capture-"+loc)
        out=temp/("worker-replay-"+loc)
        for path in (build,capture,out):
            if path.exists():
                shutil.rmtree(path)
        capture.mkdir()
        sample_env=dict(env)
        sample_env["DoujinStageReplayDir"]=str(capture)
        log=temp/("worker-msbuild-"+loc+".log")
        log.unlink(missing_ok=True)
        argv=[str(native/"publish_references.vcxproj"),"/t:Rebuild","/m:1","/nr:false","/noAutoResponse","/v:minimal",
              "/fl","/flp:logfile="+str(log)+";verbosity=diagnostic;encoding=UTF-8",
              "/p:Configuration=Release","/p:Platform=x64","/p:PlatformToolset=v143",
              "/p:VCToolsVersion=14.44.35207","/p:WindowsTargetPlatformVersion=10.0.26100.0",
              "/p:DoujinCompilerJobs=2","/p:DoujinSourceLink=true","/p:DoujinWorkerRepro="+profile,
              "/p:DoujinReproDeterministic="+("baseline" if profile=="baseline" else "mapped")]
        row={"profile":profile,"location":loc,"status":"rejected",
             "build":run(args.msbuild,argv,native,sample_env,360)}
        report["runs"].append(row)
        row["scheduling"]=scheduling_evidence(log) if log.is_file() else {"requested_compiler_jobs":2,"log_missing":True,"process_concurrency_verified":False}
        log.unlink(missing_ok=True)
        checkpoint(args.output,report)
        if row["build"]["ok"]:
            objroot=build/"publisher-obj"/"x64"
            objs={f.name:f for f in objroot.rglob("*.obj")}
            require(set(objs)==EXPECTED_OBJECTS,"Unexpected worker object set")
            logs=list(objroot.rglob("CL.command.1.tlog"))
            if len(logs)!=1:
                row["status"]="invalid-tracking-log"
                row["tracking_log_count"]=len(logs)
                checkpoint(args.output,report)
                continue
            row["tracked_command"]=digest(logs[0])
            row["flag_records"]=tracked_evidence(logs[0],profile)
            row["flag_mismatches"]=sorted({k for rec in row["flag_records"] for k in rec["mismatches"]})
            tracked_sources=[n for rec in row["flag_records"] for n in rec["known_sources"]]
            if set(tracked_sources)!=EXPECTED_SOURCES or len(tracked_sources)!=4 or sum(rec["source_count"] for rec in row["flag_records"])!=4:
                row["flag_mismatches"].append("source_coverage")
            checkpoint(args.output,report)
            if row["flag_mismatches"]:
                row["status"]="invalid-flags"
                checkpoint(args.output,report)
                require(source_receipt(work)==src and source_receipt(repo)==src,"Source changed")
                continue
            row["objects"]={n:{**digest(f),"coff":coff_detail(f.read_bytes())} for n,f in objs.items()}
            sqlite=list((build/"shared-obj"/"sqlite"/"x64").rglob("sqlite_amalgamation.obj"))
            require(len(sqlite)==1,"Missing SQLite object")
            row["sqlite"]={**digest(sqlite[0]),"coff":coff_detail(sqlite[0].read_bytes())}
            row["sqlite_library"]=digest(build/"shared-lib"/"x64"/"doujinstyle_sqlite.lib")
            row["compiler_pdbs"]=[digest(f) for f in sorted(objroot.rglob("*.pdb"))]
            row["sourcelink"]=digest(build/"generated"/"x64"/"sourcelink-publish_references.json")
            pe,pdb=build/"x64"/"publish_references.exe",build/"x64"/"publish_references.pdb"
            row.update(pe_record(pe,pdb))
            row["status"]="ok"
            checkpoint(args.output,report)
            if profile=="mapped-z7":
                # Preserve frozen linker evidence even if a verbose object dump
                # exceeds its private diagnostic cap. Check original inputs
                # outside exception handling so mutation cannot become optional.
                original_bundle,original_inputs=tree_digest(capture),fingerprint_inputs({"build":build})
                evidence=row["replay"]={"status":"started","stage":"inventory","runs":[]}
                save=lambda: checkpoint(args.output,report)
                try:
                    evidence.update(replay(capture,out,tools/"link.exe",llvm/"lld-link.exe",sdk,llvm,build,evidence,save))
                except (ValueError,OSError,subprocess.SubprocessError,UnicodeError,RuntimeError) as error:
                    evidence.update({"status":"diagnostic-error",**diagnostic_error(error)})
                require(tree_digest(capture)==original_bundle and fingerprint_inputs({"build":build})==original_inputs,
                        "Replay changed original inputs")
                save()
                row["object_forensics"]={}
                for n,f in sorted(objs.items()):
                    row["object_forensics"][n]=optional_views(object_views,llvm,f)
                    require(digest(f)["sha256"]==row["objects"][n]["sha256"],"Object inspection changed input")
                    save()
        require(source_receipt(work)==src and source_receipt(repo)==src,"Source changed")
        checkpoint(args.output,report)
    require(all(report["profiles"][p]["complete"] for p in PROFILES),"Worker experiment incomplete; inspect retained flag records")
    require(all(r.get("replay",{}).get("status")=="completed" for r in report["runs"] if r["profile"]=="mapped-z7"),
            "Mapped worker replay incomplete")
    print("WORKER-PROFILES "+json.dumps(report["profiles"],sort_keys=True))


if __name__=="__main__":
    main()
