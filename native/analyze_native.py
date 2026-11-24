"""Run clang-tidy and Clang Static Analyzer on first-party C++."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import time
import json
from pathlib import Path
import shutil
import subprocess
import sys

from sqlite_source import prepare


NATIVE = Path(__file__).resolve().parent
CONFIG = NATIVE.parent / ".clang-tidy"


def write_database(output, entries, *, directory=None):
    """Record the same argument vectors that the build invokes."""
    output.mkdir(parents=True, exist_ok=True)
    database = output / "compile_commands.json"
    records = [
        {"directory": str(Path(directory).resolve() if directory else Path.cwd()), "file": str(source), "arguments": list(map(str, argv))}
        for source, argv in entries
    ]
    database.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return database


def run_analysis_group(databases, executable, *, strict=False, jobs=1):
    """Schedule independent (compilation database, source) contexts globally.

    A source in two projects is two tasks. Do not combine databases: clang-tidy
    must see the exact original context for each invocation.
    """
    binary = shutil.which(str(executable))
    if binary is None:
        raise RuntimeError(f"clang-tidy was not found at {executable}. Use the matching LLVM toolchain.")
    if jobs < 1:
        raise ValueError("Analyzer jobs must be positive")
    tasks = []
    labels = set()
    for label, database in databases:
        if label in labels:
            raise ValueError("Duplicate analysis context: " + label)
        labels.add(label)
        files = {record["file"] for record in json.loads(database.read_text(encoding="utf-8"))
                 if Path(record["file"]).parent == NATIVE and Path(record["file"]).suffix == ".cpp"}
        if not files:
            raise RuntimeError(f"No first-party C++ units in {database}")
        command = [binary, "-p", str(database.parent), f"--config-file={CONFIG}", "--removed-arg=/c"]
        if strict:
            command.append("--warnings-as-errors=*")
        tasks.extend((label, source, command) for source in files)
    if not tasks:
        raise RuntimeError("No first-party C++ units")
    tasks.sort(key=lambda task: (-Path(task[1]).stat().st_size, task[0], task[1]))
    workers = min(jobs, len(tasks), os.cpu_count() or 1)
    print(f"Analyzing {len(tasks)} C++ contexts with {workers} worker(s) across {len(labels)} database(s)", flush=True)

    start = time.monotonic()
    def analyze(task):
        label, source, command = task
        began = time.monotonic()
        result = subprocess.run([*command, source], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", check=False)
        finished = time.monotonic()
        return label, source, result.returncode, result.stdout, began, finished

    failures = []
    metrics = {label: [] for label in sorted(labels)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze, task): task for task in tasks}
        for future in as_completed(futures):
            label, source, _ = futures[future]
            try:
                label, source, code, output, began, finished = future.result()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                code, output, began, finished = -1, str(error), start, time.monotonic()
            duration = finished - began
            print(f"ANALYSIS-METRIC context={label} unit={Path(source).name} seconds={duration:.3f} exit={code}", flush=True)
            if output:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
            metrics[label].append({"unit": Path(source).name, "exit": code,
                                   "seconds": round(duration, 3),
                                   "start_offset_seconds": round(began-start, 3),
                                   "end_offset_seconds": round(finished-start, 3)})
            if code:
                failures.append((label, source, code))
    elapsed = time.monotonic() - start
    print(f"ANALYSIS-METRIC total_seconds={elapsed:.3f} units={len(tasks)} workers={workers} failures={len(failures)}", flush=True)
    if failures:
        raise RuntimeError("Strict analysis failed: " + ", ".join(
            f"{label}/{Path(source).name} ({code})" for label, source, code in failures))
    return {"wall_seconds": round(elapsed, 3), "workers": workers,
            "contexts": {label: sorted(rows, key=lambda row: row["unit"]) for label, rows in metrics.items()}}


def run_analysis(database, executable, *, strict=False, jobs=1):
    return run_analysis_group([("default", database)], executable, strict=strict, jobs=jobs)


def portable_database(output, compiler, optimization=None):
    generated = output / "generated"
    prepare(generated)
    sys.path.insert(0, str(NATIVE.parent))
    from tools.build_identity import header, identify
    (generated / "doujinstyle_build_version.hpp").write_text(
        header(identify(NATIVE.parent)), encoding="utf-8")
    units = [NATIVE / "descriptor.cpp", NATIVE / "publish_references.cpp", NATIVE / "worker_build_identity.cpp", *sorted(NATIVE.glob("test_*.cpp"))]
    flags = [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(generated), "-pthread"]
    if optimization is not None:
        flags.append(f"-O{optimization}")
    return write_database(output, ((source, [*flags, "-c", source]) for source in units))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--portable", metavar="OUTPUT", type=Path,
                      help="generate and analyze a Linux compilation database")
    mode.add_argument("--build-dir", type=Path, help="directory of an existing compile_commands.json")
    parser.add_argument("--clang-tidy", default="clang-tidy", type=Path)
    parser.add_argument("--clangxx", default="clang++", help="compiler name for the portable command database")
    parser.add_argument("--strict", action="store_true", help="fail if an enabled check reports a warning")
    parser.add_argument("--jobs", type=int, default=1, help="bounded number of independent TU analyses")
    parser.add_argument("--portable-optimization", type=int, choices=(0, 1),
                        help="explicit portable analysis optimization for fortified compilers")
    args = parser.parse_args()
    if args.build_dir and args.portable_optimization is not None:
        parser.error("--portable-optimization requires --portable")
    database = (portable_database(args.portable.resolve(), args.clangxx, args.portable_optimization) if args.portable
                else args.build_dir.resolve() / "compile_commands.json")
    run_analysis(database, args.clang_tidy, strict=args.strict, jobs=args.jobs)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
