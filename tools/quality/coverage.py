"""Collect real LLVM source coverage for a declared binary/scope.

The measured denominator is ONLY the instrumented, observed first-party files.
Do not present it as whole-repository or foobar2000 host coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

METRICS = ("lines", "functions", "branches", "regions")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def file_in_scope(filename: str, root: Path) -> bool:
    try:
        Path(filename).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def totals(files: list[dict]) -> dict:
    summary = {}
    for metric in METRICS:
        count = sum(item["summary"][metric]["count"] for item in files)
        covered = sum(item["summary"][metric]["covered"] for item in files)
        if covered > count:
            raise ValueError(f"Invalid LLVM {metric} coverage counts")
        summary[metric] = {
            "count": count, "covered": covered,
            "percent": round(100 * covered / count, 4) if count else None
        }
    return summary


def reduce_export(data: dict, *, root: Path, binaries: list[Path], scope: str) -> dict:
    if data.get("type") != "llvm.coverage.json.export" or not isinstance(data.get("data"), list):
        raise ValueError("Not an LLVM source-based coverage export")
    selected = {}
    for unit in data["data"]:
        for item in unit.get("files", []):
            path = item.get("filename")
            if not isinstance(path, str) or not file_in_scope(path, root):
                continue
            if not isinstance(item.get("summary"), dict):
                raise ValueError(f"Missing per-file coverage: {path}")
            for key in METRICS:
                if key not in item["summary"] or not all(
                    isinstance(item["summary"][key].get(k), int) and item["summary"][key][k] >= 0
                    for k in ("count", "covered")
                ):
                    raise ValueError(f"Missing coverage metric {key}: {path}")
            short = Path(path).resolve().relative_to(root.resolve()).as_posix()
            if short in selected:
                raise ValueError(f"Duplicate coverage file {short}; merge instrumented objects in one export")
            selected[short] = item
    if not selected:
        raise ValueError("No instrumented first-party source files in LLVM coverage")
    files = [{"path": name, "coverage": totals([item])} for name, item in sorted(selected.items())]
    aggregate = totals(list(selected.values()))
    if aggregate["lines"]["count"] < 1:
        raise ValueError("No coverable first-party lines")
    return {
        "schema_version": 1,
        "tool": "llvm-cov",
        "scope": scope,
        "measured": True,
        "whole_repository_coverage": None,
        "native_host_e2e_coverage": None,
        "binaries": [{"name": b.name, "sha256": sha256(b)} for b in binaries],
        "summary": aggregate,
        "files": files,
        "source_file_count": len(files),
    }


def collect(*, binaries: list[Path], profiles: Path, source_root: Path,
            output: Path, scope: str, llvm_cov: str, llvm_profdata: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = sorted(p for p in profiles.rglob("*.profraw") if p.is_file() and p.stat().st_size)
    if not raw:
        raise ValueError("No nonempty .profraw profiles; coverage cannot be claimed")
    if not binaries or any(not p.is_file() for p in binaries):
        raise ValueError("Missing instrumented binary")
    combined = output / "merged.profdata"
    run([llvm_profdata, "merge", "-sparse", *raw, "-o", combined])
    opts = [llvm_cov, "export", binaries[0], f"-instr-profile={combined}"]
    for binary in binaries[1:]:
        opts.extend(("-object", binary))
    proc = run(opts, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    result = reduce_export(data, root=source_root, binaries=binaries, scope=scope)
    result["profile_count"] = len(raw)
    (output / "coverage-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (output / "coverage.lcov").open("w", encoding="utf-8") as destination:
        run([*opts[:2], "-format=lcov", *opts[2:]], stdout=destination)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, action="append", required=True)
    ap.add_argument("--profiles", type=Path, required=True)
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--llvm-cov", default="llvm-cov")
    ap.add_argument("--llvm-profdata", default="llvm-profdata")
    args = ap.parse_args()
    try:
        result = collect(binaries=args.binary, profiles=args.profiles,
                         source_root=args.source_root, output=args.output,
                         scope=args.scope, llvm_cov=args.llvm_cov, llvm_profdata=args.llvm_profdata)
        print("Measured C++ coverage:", result["scope"], result["summary"]["lines"],
              f"({result['source_file_count']} source files, {result['profile_count']} profiles)")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: native coverage unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
