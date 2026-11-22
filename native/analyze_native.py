"""Run clang-tidy and Clang Static Analyzer on first-party C++."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from sqlite_source import prepare


NATIVE = Path(__file__).resolve().parent
CONFIG = NATIVE.parent / ".clang-tidy"


def write_database(output, entries):
    """Record the same argument vectors that the build invokes."""
    output.mkdir(parents=True, exist_ok=True)
    database = output / "compile_commands.json"
    records = [
        {"directory": str(Path.cwd()), "file": str(source), "arguments": list(map(str, argv))}
        for source, argv in entries
    ]
    database.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return database


def run_analysis(database, executable, *, strict=False):
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
    print(f"Analyzing {len(files)} C++ units from {database.parent}", flush=True)
    subprocess.run([*command, *files], check=True)


def portable_database(output, compiler):
    generated = output / "generated"
    prepare(generated)
    units = [NATIVE / "descriptor.cpp", NATIVE / "publish_references.cpp", *sorted(NATIVE.glob("test_*.cpp"))]
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
    args = parser.parse_args()
    database = (portable_database(args.portable.resolve(), args.clangxx) if args.portable
                else args.build_dir.resolve() / "compile_commands.json")
    run_analysis(database, args.clang_tidy, strict=args.strict)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
