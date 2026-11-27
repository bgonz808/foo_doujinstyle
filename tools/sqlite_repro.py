"""Opt-in standalone SQLite deterministic-option and path relocation experiment."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from statistics import median

from tools.reproducibility import digest, source_receipt
from tools.reproducibility_detail import archive_detail, coff_detail
from tools.toolchain_inventory import tree_digest

PROFILES = ("baseline", "experimental", "mapped")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sequence(replica):
    # Each profile appears twice at each root; rotate runner B order and swap roots.
    first = [(profile, location) for profile in PROFILES for location in ("a", "b")]
    rows = first + list(reversed(first))
    return rows if replica == "a" else [(p, "b" if r == "a" else "a") for p, r in rows[4:] + rows[:4]]


def command_flags(command, profile):
    upper = command.upper()
    return (all(flag in upper for flag in ("/BREPRO", "/Z7", "/QSPECTRE"))
            and ("/EXPERIMENTAL:DETERMINISTIC" in upper) == (profile != "baseline")
            and ("/PATHMAP:" in upper) == (profile == "mapped")
            and "/CGTHREADS" not in upper)


def summaries(rows):
    result = {}
    for profile in PROFILES:
        group = [r for r in rows if r["profile"] == profile and r["status"] == "ok"]
        result[profile] = {"samples": len(group), "complete": len(group) == 4}
        if group:
            result[profile].update({
                "seconds": [r["seconds"] for r in group],
                "median_seconds": median(r["seconds"] for r in group),
                "object_equal": len(group) == 4 and len({r["object"]["sha256"] for r in group}) == 1,
                "library_equal": len(group) == 4 and len({r["library"]["sha256"] for r in group}) == 1,
                "same_root_object_equal": {k: len([r for r in group if r["location"] == k]) == 2
                    and len({r["object"]["sha256"] for r in group if r["location"] == k}) == 1
                    for k in ("a", "b")}
            })
    return result


def librarian_args(obj, absolute):
    return ["/NOLOGO", "/MACHINE:X64", "/Brepro", "/OUT:replay.lib",
            str(obj.resolve()) if absolute else obj.name]


def replay_library(tool, obj, original_lib, replica):
    """Replay the real mapped object without changing compiler or linker inputs."""
    before, library_before = digest(obj), digest(original_lib)
    out = obj.parent / "replay.lib"
    require(not out.exists(), "Replay output already exists")
    order = ("absolute", "relative", "relative", "absolute")
    if replica == "b":
        order = ("relative", "absolute", "absolute", "relative")
    rows = []
    try:
        for profile in order:
            out.unlink(missing_ok=True)
            argv = librarian_args(obj, profile == "absolute")
            start = time.monotonic()
            proc = subprocess.run([str(tool), *argv], cwd=obj.parent, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=60)
            require(proc.returncode == 0 and not re.search(rb"(?i)\bLNK4\d{3}\b", proc.stdout),
                    "Native librarian replay rejected")
            members = archive_detail(out.read_bytes())
            payloads = [m for m in members if m["kind"] == "object"]
            require(len(payloads) == 1 and payloads[0]["payload_sha256"] == before["sha256"],
                    "Librarian did not retain exact frozen object")
            rows.append({"profile":profile, "seconds":round(time.monotonic()-start, 4),
                         "library":digest(out), "members":members})
            require(digest(obj) == before and digest(original_lib) == library_before,
                    "Replay modified original inputs")
    finally:
        out.unlink(missing_ok=True)
    return {"tool":digest(tool), "frozen_object":before, "original_library":library_before,
            "runs":rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--msbuild", type=Path, required=True)
    p.add_argument("--replica", choices=("a", "b"), required=True)
    p.add_argument("--lib", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Disposable Windows CI only")
    root = Path(__file__).resolve().parents[1]
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    require(args.output.resolve().is_relative_to(temp), "Report must stay in runner temp")
    require(not os.environ.get("CL") and not os.environ.get("_CL_"), "Unexpected implicit compiler flags")
    tool = args.lib.resolve()
    require(tool.parts[-5:] == ("14.44.35207", "bin", "Hostx64", "x64", "lib.exe") and tool.is_file(), "Wrong native librarian")
    require(not os.environ.get("LIB") and not os.environ.get("LINK") and not os.environ.get("_LINK_"), "Unexpected implicit librarian environment")
    src = source_receipt(root)
    roots = {k: temp / ("determinism-root-" + k) for k in ("a", "b")}
    for path in roots.values():
        require(not path.exists(), "Fresh relocated checkout required")
        subprocess.run(["git", "worktree", "add", "--detach", str(path), "HEAD"],
                       cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        require(source_receipt(path) == src, "Relocated tree mismatch")
    report = {"schema_version": 3, "source": src, "replica": args.replica,
              "scope": "x64 MSVC standalone SQLite; two paths, baseline /Brepro, experimental, experimental plus pathmap; no full closure or final PE claim",
              "msbuild": digest(args.msbuild), "image_version": os.environ.get("ImageVersion", ""),
              "sequence": sequence(args.replica), "runs": []}
    for profile, location in report["sequence"]:
        work = roots[location]
        native = work / "native"
        build = native / "build"
        if build.exists():
            shutil.rmtree(build)
        cmd = [str(args.msbuild), str(native / "sqlite_shared.vcxproj"), "/t:Rebuild",
               "/m:1", "/nr:false", "/noAutoResponse", "/v:minimal",
               "/p:Configuration=Release", "/p:Platform=x64", "/p:PlatformToolset=v143",
               "/p:VCToolsVersion=14.44.35207", "/p:WindowsTargetPlatformVersion=10.0.26100.0",
               "/p:DoujinReproDeterministic=" + profile]
        start = time.monotonic()
        proc = subprocess.run(cmd, cwd=native, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        row = {"profile": profile, "location": location, "seconds": round(time.monotonic()-start, 3),
               "status": "ok", "exit": proc.returncode,
               "diagnostic_codes": sorted({m.decode("ascii") for m in re.findall(rb"\b(?:D9\d{3}|C\d{4}|MSB\d{4})\b", proc.stdout)})}
        report["runs"].append(row)
        if proc.returncode or any(code in row["diagnostic_codes"] for code in ("D9002", "D9007")):
            row["status"] = "rejected"
        else:
            objroot = build / "shared-obj" / "sqlite" / "x64"
            objects = list(objroot.rglob("sqlite_amalgamation.obj"))
            logs = list(objroot.rglob("CL.command.1.tlog"))
            require(len(objects) == 1 and len(logs) == 1, "Missing standalone object or tracked command")
            lib = build / "shared-lib" / "x64" / "doujinstyle_sqlite.lib"
            actual = logs[0].read_text(encoding="utf-16")
            require(command_flags(actual, profile), "Effective tracked compiler flags differ from profile")
            if profile == "mapped":
                require("X:\\DOUJIN-REPRO\\NATIVE" in actual.upper(), "Canonical mapping target absent")
            row.update({"object": {**digest(objects[0]), "coff": coff_detail(objects[0].read_bytes())},
                        "library": {**digest(lib), "members": archive_detail(lib.read_bytes())},
                        "command": digest(logs[0]), "generated": tree_digest(build / "generated" / "x64" / "sqlite")})
            if profile == "mapped":
                row["librarian_replay"] = replay_library(tool, objects[0], lib, args.replica)
        require(source_receipt(work) == src and source_receipt(root) == src,
                "Source changed during experiment")
        report["profiles"] = summaries(report["runs"])
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    require(report["profiles"]["baseline"]["complete"], "Baseline incomplete")
    require(report["profiles"]["mapped"]["complete"], "Mapped control incomplete")
    for profile in ("absolute", "relative"):
        hashes = [v["library"]["sha256"] for r in report["runs"] if "librarian_replay" in r
                  for v in r["librarian_replay"]["runs"] if v["profile"] == profile]
        require(len(hashes) == 8, "Librarian replay incomplete")
        report.setdefault("librarian_profiles", {})[profile] = {"samples":len(hashes), "unique_hashes":len(set(hashes))}
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("DETERMINISTIC-PROFILES " + json.dumps(report["profiles"], sort_keys=True))


if __name__ == "__main__":
    main()
