"""Isolated x64 ordinary-COFF compiler/librarian/linker matrix. Not production."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from tools.artifact_forensics import object_views, pdb_views, pe_views
from tools.reproducibility import digest, source_receipt
from tools.reproducibility_detail import archive_detail, coff_detail, pdb_detail
from tools.symbol_identity import verify_pair

COMPILERS = ("msvc", "clang")
LIBRARIANS = ("native-relative", "native-absolute", "llvm-lib", "llvm-ar")
LINKERS = ("msvc", "lld")
FIXTURE = {
    "entry.c": b"extern int payload(int); int entry(void) { return payload(7); }\n",
    "payload.c": b"const char source_name[] = __FILE__; int payload(int n) { return n + source_name[0]; }\n",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(tool, args, cwd, env):
    start = time.monotonic()
    proc = subprocess.run([str(tool), *args], cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=90, check=False)
    # No raw paths or diagnostics in evidence.
    codes = sorted({m.decode("ascii") for m in re.findall(rb"\b(?:D9\d{3}|LNK\d{4}|C\d{4})\b", proc.stdout)})
    warning = bool(re.search(rb"(?i)(unknown argument|unused argument|ignoring unknown|argument unused|warning: ignoring|unsupported option|D9002|D9007|LNK4044)", proc.stdout))
    return {"ok": proc.returncode == 0 and not warning, "exit": proc.returncode,
            "diagnostic_codes": codes, "output_sha256": hashlib.sha256(proc.stdout).hexdigest(),
            "argv_sha256": hashlib.sha256(json.dumps(args).encode()).hexdigest(),
            "seconds": round(time.monotonic()-start, 4)}


def compile_flags(compiler, root, source):
    base = ["/nologo", "/c", "/TC", "/O2", "/GS", "/Z7", "/Zl", "/Brepro",
            "/Fo" + Path(source).with_suffix(".obj").name]
    if compiler == "msvc":
        base += ["/experimental:deterministic", "/pathmap:" + str(root) + "=X:\\canonical"]
    else:
        base += ["/clang:-fdebug-compilation-dir=.", "/clang:-gno-codeview-command-line",
                 "/clang:-ffile-prefix-map=" + str(root) + "=X:\\canonical"]
    return base + [source]


def library_args(kind, root):
    if kind == "llvm-ar":
        return ["--format=coff", "rcsD", "fixture.lib", "payload.obj"]
    if kind == "llvm-lib":
        return ["/nologo", "/out:fixture.lib", "payload.obj"]
    return ["/nologo", "/machine:x64", "/Brepro", "/out:fixture.lib",
            str(root / "payload.obj") if kind == "native-absolute" else "payload.obj"]


def link_args(kind):
    args = ["/nologo", "/nodefaultlib", "/entry:entry", "/subsystem:console", "/machine:x64",
            "/manifest:no", "/debug:full", "/incremental:no", "/Brepro", "/pdb:fixture.pdb",
            "/pdbaltpath:fixture.pdb", "/out:fixture.exe", "/opt:ref", "/opt:icf"]
    if kind == "lld":
        args += ["/lldignoreenv", "/pdbsourcepath:X:\\canonical"]
    return args + ["entry.obj", "fixture.lib"]


def summarize(rows):
    result = {}
    for c in COMPILERS:
        for a in LIBRARIANS:
            for l in LINKERS:
                key = "/".join((c,a,l))
                group = [r for r in rows if r["profile"] == key and r["status"] == "ok"]
                result[key] = {"samples":len(group), "complete":len(group)==4,
                               "unique_hashes":{f:len({r["artifacts"][f]["sha256"] for r in group})
                                                for f in ("entry","payload","library","pe","pdb")}}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--msvc-bin", type=Path, required=True)
    p.add_argument("--llvm-bin", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--replica", choices=("a","b"), required=True)
    args = p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true", "Disposable Windows CI only")
    root = Path(__file__).resolve().parents[1]
    src = source_receipt(root)
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    require(args.output.resolve().is_relative_to(temp), "Private evidence output required")
    msvc, llvm = args.msvc_bin.resolve(), args.llvm_bin.resolve()
    require(msvc.parts[-4:]==("14.44.35207","bin","Hostx64","x64"), "Incorrect native tool pin")
    tools = {"msvc":msvc/"cl.exe", "clang":llvm/"clang-cl.exe",
             "native-relative":msvc/"lib.exe","native-absolute":msvc/"lib.exe",
             "llvm-lib":llvm/"llvm-lib.exe","llvm-ar":llvm/"llvm-ar.exe",
             "link-msvc":msvc/"link.exe","link-lld":llvm/"lld-link.exe",
             "llvm-pdbutil":llvm/"llvm-pdbutil.exe","llvm-readobj":llvm/"llvm-readobj.exe",
             "llvm-objdump":llvm/"llvm-objdump.exe"}
    require(all(t.is_file() for t in tools.values()), "Missing pinned tool")
    env = dict(os.environ)
    require(not any(env.get(k) for k in ("CL","_CL_","LINK","_LINK_")), "Implicit flags present")
    # No CRT, SDK libraries or imports; fixed tool/OS child lookup.
    system = Path(env["SYSTEMROOT"])
    env["PATH"] = os.pathsep.join(map(str,(msvc,llvm,system/"System32",system)))
    report = {"schema_version":1,"source":src,"replica":args.replica,
              "image_version":env.get("ImageVersion",env.get("IMAGEVERSION","")),
              "scope":"freestanding x64 ordinary COFF fixture; no SDK, CRT, LTO, execution or production qualification",
              "tools":{k:digest(v) for k,v in tools.items()}, "runs":[]}
    roots = {k:temp/name for k,name in (("a","matrix-a"),("b","matrix-relocated-longer-b"))}
    for path in roots.values():
        path.mkdir(exist_ok=False)
    order = [(c,r) for c in COMPILERS for r in ("a","b")]
    order += list(reversed(order))
    if args.replica=="b":
        order = order[2:]+order[:2]
    for compiler, loc in order:
        work=roots[loc]
        for path in work.iterdir():
            require(path.is_file(), "Unexpected fixture directory")
            path.unlink()
        for name, content in FIXTURE.items():
            (work/name).write_bytes(content)
        compiled = {s:run(tools[compiler], compile_flags(compiler,work,s),work,env) for s in FIXTURE}
        if all(x["ok"] for x in compiled.values()):
            frozen={f:digest(work/(f+".obj")) for f in ("entry","payload")}
            objects=object_views(llvm,work/"payload.obj")
        else:
            frozen={}
            objects={}
        for librarian in LIBRARIANS:
            (work/"fixture.lib").unlink(missing_ok=True)
            library = run(tools[librarian],library_args(librarian,work),work,env) if frozen else None
            lib=work/"fixture.lib"
            lib_identity=digest(lib) if library and library["ok"] else None
            for linker in LINKERS:
                row={"profile":"/".join((compiler,librarian,linker)), "root":loc,
                     "compile":compiled,"librarian":library,"status":"rejected"}
                report["runs"].append(row)
                pe,pdb=work/"fixture.exe",work/"fixture.pdb"
                for path in (pe,pdb):
                    path.unlink(missing_ok=True)
                if lib_identity:
                    row["link"]=run(tools["link-"+linker],link_args(linker),work,env)
                    if row["link"]["ok"]:
                        row["status"]="ok"
                        row["artifacts"]={**frozen,"library":lib_identity,"pe":digest(pe),"pdb":digest(pdb)}
                        row["binding"]=verify_pair(pe.read_bytes(),pdb.read_bytes(),pdb.name)
                        row["archive"]=archive_detail(lib.read_bytes())
                        row["pdb_streams"]=pdb_detail(pdb.read_bytes())
                        row["forensics"]={"object":objects,"pe":pe_views(llvm,pe),"pdb":pdb_views(llvm,pdb)}
                    require(digest(lib)==lib_identity, "Linker changed frozen library")
                require(all(digest(work/(f+".obj"))==d for f,d in frozen.items()), "Tool changed frozen object")
                report["profiles"]=summarize(report["runs"])
                args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        require(all((work/n).read_bytes()==v for n,v in FIXTURE.items()), "Fixture source changed")
        require(source_receipt(root)==src, "Repository source changed")
    require(len(report["runs"])==64, "Incomplete experiment matrix")
    require(report["profiles"]["msvc/native-relative/msvc"]["complete"], "Native baseline rejected")
    require(report["profiles"]["clang/llvm-ar/lld"]["complete"], "LLVM baseline rejected")
    print("TOOLCHAIN-MATRIX profiles="+str(len(report["profiles"]))+" samples="+str(len(report["runs"])))


if __name__=="__main__":
    main()
