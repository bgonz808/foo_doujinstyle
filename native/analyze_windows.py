"""Strict clang-tidy/CSA over actual Windows MSVC first-party compiler commands.

No additional SDK, compiler download, cross-compilation, or relinking. Run after
a clean x64 production build. MSVC-only output/mitigation options are stripped
for frontend analysis, never from the production build. The analysis is not
a claim of compiler mitigation or runtime coverage.
"""
from __future__ import annotations

import argparse
import ctypes
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from analyze_native import NATIVE, run_analysis_group, write_database

PROJECTS = {
    "dll": ("foo_doujinstyle.vcxproj", "obj"),
    "worker": ("publish_references.vcxproj", "publisher-obj"),
}
TARGETS = {"x64": "x86_64-pc-windows-msvc", "Win32": "i686-pc-windows-msvc",
           "ARM64EC": "arm64ec-pc-windows-msvc"}


def project_units(project: Path) -> set[Path]:
    """Only production first-party C++; reject unevaluated new conditions."""
    out = set()
    for item in ET.parse(project).findall(".//{*}ClCompile"):
        name = item.get("Include")
        if not name:
            continue
        source = project.parent / name.replace("\\", "/")
        if source.suffix.lower() != ".cpp" or source.parent != NATIVE:
            continue
        condition = item.get("Condition")
        if condition == "'$(DoujinE2ETrace)' == 'true'" and name == "host_trace_etw.cpp":
            continue  # separately tracked ETW build configuration
        if condition or item.findall("{*}ExcludedFromBuild"):
            raise ValueError(f"Unqualified conditional source: {name}")
        if not source.is_file():
            raise ValueError(f"Missing production unit: {name}")
        out.add(source.resolve())
    if not out:
        raise ValueError(f"No production units in {project.name}")
    return out


def tracking_records(text: str):
    """Decode MSVC tracking log root records, not command-line stdout."""
    roots = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("^"):
            if roots is not None:
                raise ValueError("Compiler tracking root missing command")
            roots = line[1:].split("|")
            if not all(roots):
                raise ValueError("Empty compiler tracking root")
        else:
            if roots is None:
                raise ValueError("Compiler tracking command missing root")
            yield roots, line
            roots = None
    if roots is not None:
        raise ValueError("Compiler tracking root missing command")


def split_windows_command(line: str) -> list[str]:
    """Use Windows' own quote/backslash parser (placeholder argv[0])."""
    if os.name != "nt":
        raise RuntimeError("Windows compiler tracking commands require Windows")
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    split = shell.CommandLineToArgvW
    split.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel.LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    argc = ctypes.c_int()
    argv = split("placeholder " + line, ctypes.byref(argc))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[i] for i in range(1, argc.value)]
    finally:
        free(ctypes.cast(argv, ctypes.c_void_p))


def normalize_command(argv, roots, source, compiler, target, include_dirs, directory=None):
    """Preserve frontend-affecting switches; remove only known MSVC output flags."""
    result = [str(compiler), "--target=" + target]
    removed = []
    def source_key(path):
        return ntpath.normcase(ntpath.normpath(ntpath.join(str(directory or ""), str(path))))
    source_names = {source_key(p) for p in roots}
    drop_exact = {"/Qspectre", "/Qspectre-", "/sdl", "/sdl-", "/FS", "/FC",
                  "/Zi", "/ZI", "/Z7", "/GL", "/GL-", "/Gy", "/Gy-", "/Gw",
                  "/Gw-", "/Gm", "/Gm-", "/Zc:inline", "/Zc:inline-",
                  "/external:W0", "/external:W1", "/external:W2",
                  "/external:W3", "/external:W4", "/external:anglebrackets"}
    drop_prefix = ("/Fo", "/Fd", "/Fa", "/Fe", "/Fi", "/ZH:",
                   "/errorReport:", "/diagnostics:", "/MP", "/analyze:")
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if i == 1 and re.search(r"(?:^|[\\/])cl(?:\.exe)?$", arg, re.I):
            continue
        if not arg.startswith(("/", "-")) and source_key(arg) in source_names:
            continue
        if arg in ("/Tp", "/Tc"):
            if i == len(argv) or source_key(argv[i]) not in source_names:
                raise ValueError("Unexpected per-file source argument")
            i += 1
            continue
        if arg.startswith(("/Tp", "/Tc")) and source_key(arg[3:]) in source_names:
            continue
        if arg.startswith("@"):
            raise ValueError("Unexpanded response file in compiler tracking command")
        if arg in ("/Fo", "/Fd", "/Fa", "/Fe", "/Fi"):
            if i == len(argv):
                raise ValueError("Missing output path")
            removed.append(arg)
            i += 1
            continue
        if arg in drop_exact or arg.startswith(drop_prefix):
            removed.append(arg)
            continue
        if arg == "/external:I":
            if i == len(argv):
                raise ValueError("Missing external include path")
            result.extend(("/imsvc", argv[i]))
            i += 1
        elif arg.startswith("/external:I"):
            result.append("/imsvc" + arg[len("/external:I"):])
        else:
            # Unknown flags are deliberately not ignored: clang-tidy must reject
            # incompatible frontend options rather than silently losing coverage.
            result.append(arg)
    if not any(a.startswith("/std:c++") for a in result):
        raise ValueError("Missing C++ language standard in tracked command")
    if not any(a.startswith("/D") for a in result):
        raise ValueError("Missing preprocessor definitions in tracked command")
    if "/c" not in result:
        result.append("/c")
    for directory in include_dirs:
        result.extend(("/imsvc", str(directory)))
    result.append(str(source))
    return result, removed


def evaluate_includes(msbuild, project, sdk, platform):
    cmd = [str(msbuild), str(project), "/nologo", "/p:Configuration=Release",
           "/p:Platform=" + platform, "/p:PlatformToolset=v143",
           "/p:VCToolsVersion=14.44.35207", "/p:WindowsTargetPlatformVersion=10.0.26100.0",
           "/p:FoobarSdkRoot=" + str(sdk), "-getProperty:IncludePath,ExternalIncludePath"]
    values = json.loads(subprocess.check_output(cmd, text=True, encoding="utf-8-sig"))["Properties"]
    includes = []
    for key in ("IncludePath", "ExternalIncludePath"):
        for raw in values.get(key, "").split(";"):
            raw = raw.strip()
            if not raw:
                continue
            directory = Path(raw)
            if "$(" in raw or not directory.is_absolute() or not directory.is_dir():
                raise ValueError(f"Unresolved {key} entry: {raw}")
            if directory not in includes:
                includes.append(directory)
    if not includes or not any((p / "windows.h").is_file() for p in includes):
        raise ValueError("Evaluated MSBuild includes omit Windows SDK")
    if not any((p / "vector").is_file() for p in includes):
        raise ValueError("Evaluated MSBuild includes omit MSVC STL")
    return includes


def prepare_project(args, label, project_name, object_dir):
    project = NATIVE / project_name
    expected = project_units(project)
    log_root = NATIVE / "build" / object_dir / args.platform
    logs = sorted(p for p in log_root.rglob("*.tlog")
                  if p.name.casefold() == "cl.command.1.tlog")
    if not logs:
        raise ValueError(f"No MSVC compiler tracking log for {label}/{args.platform}")
    includes = evaluate_includes(args.msbuild, project, args.sdk, args.platform)
    commands = {}
    for log in logs:
        for roots, line in tracking_records(log.read_text(encoding="utf-16")):
            paths = [(project.parent / r).resolve() for r in roots]
            for source in set(paths) & expected:
                if source in commands:
                    raise ValueError(f"Duplicate compiler command for {label}/{source.name}")
                argv, removed = normalize_command(split_windows_command(line), paths, source,
                                                    args.llvm_bin / "clang-cl.exe",
                                                    TARGETS[args.platform], includes, directory=project.parent)
                commands[source] = (argv, removed)
    if set(commands) != expected:
        missing = sorted(p.name for p in expected - set(commands))
        raise ValueError(f"Missing actual compiler commands for {label}: {missing}")
    database = write_database(args.output / label, ((s, commands[s][0]) for s in sorted(expected)),
                              directory=project.parent)
    return database, {"project": project_name, "platform": args.platform, "target": TARGETS[args.platform],
            "units": sorted(p.name for p in expected), "count": len(expected),
            "tracking_log_sha256": [sha256(p.read_bytes()).hexdigest() for p in logs],
            "status": "prepared"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--msbuild", type=Path, required=True)
    p.add_argument("--sdk", type=Path, required=True)
    p.add_argument("--llvm-bin", type=Path, required=True)
    p.add_argument("--platform", choices=TARGETS, default="x64")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--jobs", type=int, default=1, help="bounded parallel translation-unit analysis")
    args = p.parse_args()
    for field in ("msbuild", "sdk", "llvm_bin", "output", "evidence"):
        setattr(args, field, getattr(args, field).resolve())
    # Only qualified LLVM inputs from the existing Windows acquisition step.
    tools = {}
    for name in ("clang-cl.exe", "clang-tidy.exe"):
        binary = args.llvm_bin / name
        banner = subprocess.check_output([str(binary), "--version"], text=True)
        if not re.search(r"\bversion 23\.1\.6\b", banner):
            raise ValueError(f"Unexpected analyzer version: {name}")
        tools[name] = sha256(binary.read_bytes()).hexdigest()
    evidence = {"schema_version": 1, "kind": "windows-frontend-static-analysis",
                "source_commit": os.environ.get("GITHUB_SHA"), "tool_sha256": tools,
                "platform": args.platform, "requested_jobs": args.jobs, "status": "failed", "projects": []}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    try:
        prepared = {}
        start = time.monotonic()
        for label, (project, obj) in PROJECTS.items():
            database, row = prepare_project(args, label, project, obj)
            prepared[label] = (database, row)
            evidence["projects"].append(row)
        evidence["preparation_seconds"] = round(time.monotonic() - start, 3)
        result = run_analysis_group([(label, pair[0]) for label, pair in prepared.items()],
                                    args.llvm_bin / "clang-tidy.exe", strict=True, jobs=args.jobs)
        evidence["scheduler"] = {"wall_seconds": result["wall_seconds"], "workers": result["workers"]}
        for label, (_, row) in prepared.items():
            metrics = result["contexts"][label]
            if {m["unit"] for m in metrics} != set(row["units"]) or len(metrics) != row["count"]:
                raise RuntimeError("Analysis coverage mismatch: " + label)
            row["unit_metrics"] = metrics
            row["duration_seconds"] = round(max(m["end_offset_seconds"] for m in metrics)
                                             - min(m["start_offset_seconds"] for m in metrics), 3)
            row["status"] = "passed"
        evidence["status"] = "passed"
    finally:
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
