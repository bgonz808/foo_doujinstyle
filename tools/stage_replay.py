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

from tools.reproducibility import digest, pe_structure, source_receipt
from tools.reproducibility_detail import archive_detail, pdb_detail
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


def response_candidate(bundle):
    candidates = []
    for path in bundle.rglob("*.rsp"):
        data = path.read_bytes()
        require(len(data) <= 4*1024*1024, "Oversized native response")
        text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig", errors="strict")
        # Native tool generated; only replay its one full worker-link recipe.
        if re.search(r"(?i)(?:^|\s)/OUT:", text) and re.search(r"(?i)\.obj(?:[\s\x22]|$)",text) and re.search(r"(?i)(?:^|\s)/DEBUG(?::|\s|$)", text):
            candidates.append((path, text))
    require(len(candidates)==1, "Native full-link response missing or ambiguous")
    path,text=candidates[0]
    require(not re.search(r"(?i)(?:^|\s)/LINKREPRO",text), "Recursive capture option in response")
    require(not re.search(r"(?m)(?:^|\s)@",text), "Nested response not yet supported")
    return path


def run_tool(tool, args, cwd):
    start = time.monotonic()
    # Capture raw paths/diagnostics privately; do not upload or print them.
    proc = subprocess.run([str(tool), *args], cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=180, check=False)
    require(proc.returncode == 0, f"Native replay {tool.name} exited {proc.returncode}")
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


def measure_link(tool, bundle, out):
    rsp=response_candidate(bundle)
    original=tree_digest(bundle)
    exe=out/"publish_references.exe"
    pdb=out/"publish_references.pdb"
    rows=[]
    for _ in range(2):
        for p in out.iterdir():
            require(p.is_file(), "Unexpected replay output directory")
            p.unlink()
        elapsed=run_tool(tool, ["@"+str(rsp), "/OUT:"+str(exe), "/PDB:"+str(pdb),
                                "/IMPLIB:"+str(out/"publish_references.lib"),
                                "/PDBALTPATH:publish_references.pdb", "/INCREMENTAL:NO"], rsp.parent)
        require(tree_digest(bundle)==original, "Link replay changed native bundle")
        pe,syms=exe.read_bytes(),pdb.read_bytes()
        rows.append({"seconds":elapsed, "pe":{**digest(exe),"structure":pe_structure(pe)},
                     "pdb":{**digest(pdb),"msf":pdb_detail(syms)},
                     "binding":verify_pair(pe,syms,pdb.name)})
    return {"tool":digest(tool),"bundle":original,"response_sha256":digest(rsp)["sha256"],
            "runs":rows,"pe_equal":rows[0]["pe"]["sha256"]==rows[1]["pe"]["sha256"],
            "pdb_equal":rows[0]["pdb"]["sha256"]==rows[1]["pdb"]["sha256"]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tool-dir",type=Path,required=True)
    p.add_argument("--sdk",type=Path,required=True)
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
    report={"schema_version":1,"source":src,"original_inputs":before,
            "scope":"same-job frozen-input stage replay; not original-path or independent qualification",
            "environment_sha256":sha256(json.dumps({k:os.environ.get(k,"") for k in ("LINK","_LINK_","LIB","PATH","INCLUDE","CL","_CL_")},sort_keys=True).encode()).hexdigest()}
    # Persist useful librarian result even if native link capture format differs.
    try:
        report["librarian"]=measure_library(tools/"lib.exe",obj[0],libdir/"doujinstyle_sqlite.lib")
        report["linker"]=measure_link(tools/"link.exe",bundle,linkdir)
    except (ValueError,OSError,subprocess.SubprocessError,UnicodeError) as error:
        # Raw exception text can contain private paths. Emit a fixed status only.
        report["status"]="incomplete"
        report["failed_stage"]="linker" if "librarian" in report else "librarian"
        if isinstance(error,ValueError):
            report["diagnostic"]=str(error) if str(error) in {
                "Native full-link response missing or ambiguous","Recursive capture option in response",
                "Nested response not yet supported","Link replay changed native bundle",
                "Native replay emitted linker warning or ignored option"} else "stage validation failed"
    else:
        report["status"]="completed"
    report["original_inputs_unchanged"]=fingerprint_inputs(roots)==before
    require(report["original_inputs_unchanged"] and source_receipt(root)==src,
            "Original inputs changed during replay")
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("STAGE-REPLAY status="+report["status"]+" librarian="+str(report.get("librarian",{}).get("equal","unavailable")))
    if report["status"]!="completed":
        raise SystemExit(1)


if __name__=="__main__":
    main()
