"""Project-independent quality checkpoint from JUnit, LLVM coverage and optional migration inventory.

Evidence is tied to input SHA-256 and Git source identity. Missing measurements
are null / not_run, never zero or pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path | None):
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def parse_junit(folder: Path) -> tuple[dict, list[dict], list[dict]]:
    files = sorted(folder.glob("*.xml")) if folder.is_dir() else []
    cases = []
    for path in files:
        root = ET.parse(path).getroot()
        if root.tag not in ("testsuite", "testsuites"):
            raise ValueError(f"Not JUnit XML: {path.name}")
        for case in root.iter("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            if not name:
                raise ValueError("JUnit testcase missing name")
            failure = next((x for x in case if x.tag in ("failure", "error")), None)
            skipped = next((x for x in case if x.tag == "skipped"), None)
            status = "failed" if failure is not None else ("skipped" if skipped is not None else "passed")
            cases.append({"id": (classname + "." if classname else "") + name,
                          "status": status, "suite": path.stem})
    count = Counter(c["status"] for c in cases)
    # An empty or missing suite is not a successful regression run.
    identities = [item["id"] for item in cases]
    unique = len(set(identities))
    summary = {"status": "not_run" if not cases else
               ("failed" if count["failed"] else "passed"),
               "passed": count["passed"], "failed": count["failed"],
               "skipped": count["skipped"], "total": len(cases),
               "unique_test_ids": unique, "duplicate_executions": len(cases) - unique,
               "suite_count": len(files)}
    manifest = [{"path": p.name, "sha256": file_hash(p)} for p in files]
    return summary, cases, manifest


def assess_patterns(patterns: list[str], cases: list[dict]) -> dict:
    matched = []
    missing = []
    for pattern in patterns:
        found = [c for c in cases if fnmatch.fnmatchcase(c["id"], pattern)]
        if not found:
            missing.append(pattern)
        else:
            matched.extend(found)
    if not patterns:
        status = "not_defined"
    elif not matched:
        status = "not_run"
    elif any(item["status"] == "failed" for item in matched):
        status = "failed"
    elif missing or any(item["status"] == "skipped" for item in matched):
        status = "partial"
    else:
        status = "passed"
    return {"status": status, "required_patterns": len(patterns),
            "matched_cases": len(matched), "missing_patterns": missing}


def summarize_features(document: dict | None, cases: list[dict]) -> list[dict]:
    if document is None:
        return []
    if document.get("schema_version") != 1 or not isinstance(document.get("features"), list):
        raise ValueError("Invalid feature manifest")
    seen = set()
    answer = []
    for feature in document["features"]:
        name = feature.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in seen:
            raise ValueError("Missing or duplicate feature ID")
        seen.add(name)
        contracts = feature.get("contract_tests", [])
        host = feature.get("host_e2e_tests", [])
        if not isinstance(contracts, list) or not all(isinstance(x, str) for x in contracts):
            raise ValueError(f"Invalid contract tests: {name}")
        if not isinstance(host, list) or not all(isinstance(x, str) for x in host):
            raise ValueError(f"Invalid host tests: {name}")
        answer.append({"id": name, "issue": feature.get("issue"),
                       "migration_state": feature.get("migration_state", "not-tracked"),
                       "contracts": assess_patterns(contracts, cases),
                       "host_e2e": assess_patterns(host, cases)})
    return answer


def create(*, coverage: Path | None, junit_dir: Path, inventory: Path | None,
           features: Path | None, version_file: Path | None, output: Path,
           require_coverage: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    junit, cases, junit_hashes = parse_junit(junit_dir)
    cov = load_json(coverage)
    if cov is not None and (cov.get("schema_version") != 1 or cov.get("measured") is not True or
                            cov.get("tool") != "llvm-cov"):
        raise ValueError("Invalid native coverage evidence")
    inv = load_json(inventory)
    if inv is not None and (not isinstance(inv.get("python_lines"), int) or
                             not isinstance(inv.get("fallback_python_lines"), int)):
        raise ValueError("Invalid packaged Python inventory")
    manifest = load_json(features)
    groups = summarize_features(manifest, cases)
    if version_file and version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip()
    else:
        version = None
    source_sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_sha):
        source_sha = None
    measured = cov is not None
    test_status = junit["status"]
    status = "failed" if (test_status == "failed" or
                          (require_coverage and not measured)) else (
                          "incomplete" if test_status == "not_run" else "passed")
    baseline = (manifest or {}).get("migration_baseline")
    if baseline is not None and (not isinstance(baseline, dict) or
                                 not isinstance(baseline.get("packaged_python_lines"), int) or
                                 baseline["packaged_python_lines"] < 0):
        raise ValueError("Invalid migration baseline")
    migration = None
    if inv is not None:
        migration = {
            "packaged_python_lines": inv["python_lines"],
            "fallback_python_lines": inv["fallback_python_lines"],
            "deprecated_active_lines": inv.get("deprecated_active_lines"),
            "baseline_python_lines": baseline["packaged_python_lines"] if baseline else None,
            "delta_python_lines": inv["python_lines"] - baseline["packaged_python_lines"]
                                  if baseline else None,
        }
    checkpoint = {
        "schema_version": 1,
        "kind": "native-quality-checkpoint",
        "qualification": "source-tested-not-host-qualified",
        "status": status,
        "version": version,
        "source_commit": source_sha,
        "run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "evidence": {
            "junit": junit_hashes,
            "coverage_summary_sha256": file_hash(coverage) if measured else None,
            "packaged_inventory_sha256": file_hash(inventory) if inv is not None else None,
            "features_manifest_sha256": file_hash(features) if manifest is not None else None,
        },
        "tests": junit,
        "native_coverage": ({
            "scope": cov["scope"], "summary": cov["summary"],
            "instrumented_source_files": cov["source_file_count"],
            "profile_count": cov.get("profile_count"),
            "whole_repository_coverage": None, "host_e2e_coverage": None
        } if cov else None),
        "migration": migration,
        "features": groups,
    }
    (output / "checkpoint.json").write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Native quality checkpoint", "",
             f"Result: **{status}** · version: \`{version or 'unknown'}\`",
             f"Tests: {junit['passed']} passed / {junit['failed']} failed / "
             f"{junit['skipped']} skipped; {junit['unique_test_ids']} unique IDs, "
             f"{junit['total']} executions ({junit['duplicate_executions']} repeats)"]
    if cov:
        line = cov["summary"]["lines"]
        branch = cov["summary"]["branches"]
        lines.append(f"Instrumented **{cov['scope']}**: lines "
                     f"{line['covered']}/{line['count']} ({line['percent']}%); "
                     f"branches {branch['covered']}/{branch['count']} "
                     f"({branch['percent']}%)")
    else:
        lines.append("Native C++ coverage: **not measured**")
    if inv is not None:
        delta = migration["delta_python_lines"]
        lines.append(f"Packaged Python: {inv['python_lines']} lines; "
                     f"{inv['fallback_python_lines']} fallback lines; "
                     f"delta from baseline: {delta if delta is not None else 'not measured'}")
    else:
        lines.append("Packaged Python: not measured / not applicable")
    lines.extend(["", "Whole-plugin and foobar2000 E2E code coverage: **not measured**.",
                  "This evidence does not assert release or host qualification.", ""])
    for group in groups:
        lines.append(f"- {group['id']}: contract={group['contracts']['status']}, "
                     f"host={group['host_e2e']['status']}")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checkpoint


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage", type=Path)
    ap.add_argument("--junit-dir", type=Path, required=True)
    ap.add_argument("--inventory", type=Path)
    ap.add_argument("--features", type=Path)
    ap.add_argument("--version-file", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--require-coverage", action="store_true")
    args = ap.parse_args()
    try:
        result = create(coverage=args.coverage, junit_dir=args.junit_dir,
                        inventory=args.inventory, features=args.features,
                        version_file=args.version_file, output=args.output,
                        require_coverage=args.require_coverage)
        print(f"Quality checkpoint: {result['status']}; {result['tests']['total']} tests; "
              f"coverage={'measured' if result['native_coverage'] else 'not measured'}")
        return 0 if result["status"] == "passed" else 1
    except (ValueError, TypeError, OSError, ET.ParseError, json.JSONDecodeError, KeyError) as exc:
        print(f"FAIL: quality evidence invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
