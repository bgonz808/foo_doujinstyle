"""Run clang-tidy and Clang Static Analyzer on first-party C++."""
import argparse
from concurrent.futures import ThreadPoolExecutor
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


def run_analysis(database, executable, *, strict=False, jobs=1):
    binary = shutil.which(str(executable))
    if binary is None:
        raise RuntimeError(f"clang-tidy was not found at {executable}. Use the matching LLVM toolchain.")
    files = sorted({record["file"] for record in json.loads(database.read_text(encoding="utf-8"))
                    if Path(record["file"]).parent == NATIVE and Path(record["file"]).suffix == ".cpp"})
    if not files:
        raise RuntimeError(f"No first-party C++ units in {database}")
    # clang-tidy supplies its own frontend action. Keep /c in the build database.
    command = [binary, "-p", str(database.parent), f"--config-file={CONFIG}", "--removed-arg=/c"]
    if strict:
        command.append("--warnings-as-errors=*")
    if jobs < 1:
        raise ValueError("Analyzer jobs must be positive")
    # Long translation units first, to avoid a large straggler at the end.
    # Source bytes are a deterministic cheap scheduling heuristic, not a
    # coverage decision; every compilation context still executes.
    files.sort(key=lambda name: (-Path(name).stat().st_size, name))
    workers = min(jobs, len(files), os.cpu_count() or 1)
    print(f"Analyzing {len(files)} C++ units with {workers} worker(s) from {database.parent}", flush=True)

    def analyze(source):
        start = time.monotonic()
        result = subprocess.run([*command, source], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", check=False)
        return source, result.returncode, result.stdout, time.monotonic() - start

    # Each invocation still uses the identical complete compilation database,
    # configuration and strict checks. Never share output files between TUs.
    # Emit diagnostics for every TU, even when another TU fails.
    failures = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for source, code, output, duration in pool.map(analyze, files):
            print(f"ANALYSIS-METRIC unit={Path(source).name} seconds={duration:.3f} exit={code}", flush=True)
            if output:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
            if code:
                failures.append((source, code))
    print(f"ANALYSIS-METRIC total_seconds={time.monotonic()-start:.3f} units={len(files)} workers={workers} failures={len(failures)}", flush=True)
    if failures:
        raise RuntimeError("Strict analysis failed: " + ", ".join(f"{Path(s).name} ({c})" for s, c in failures))


def portable_database(output, compiler):
    generated = output / "generated"
    prepare(generated)
    sys.path.insert(0, str(NATIVE.parent))
    from tools.build_identity import header, identify
    (generated / "doujinstyle_build_version.hpp").write_text(
        header(identify(NATIVE.parent)), encoding="utf-8")
    units = [NATIVE / "descriptor.cpp", NATIVE / "publish_references.cpp", NATIVE / "worker_build_identity.cpp", *sorted(NATIVE.glob("test_*.cpp"))]
    flags = [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(generated), "-pthread"]
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
    args = parser.parse_args()
    database = (portable_database(args.portable.resolve(), args.clangxx) if args.portable
                else args.build_dir.resolve() / "compile_commands.json")
    run_analysis(database, args.clang_tidy, strict=args.strict, jobs=args.jobs)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
