"""Emit bounded, sanitized BinSkim evidence even when scanning or policy checks fail.

The evidence bundle is NOT an attestation. Raw scanner reports are hashed but
not uploaded: they may contain private paths, symbol names, and command lines.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from zipfile import BadZipFile

from tools.binskim_policy import (ARCHES, BINARIES, check, check_package,
                                  findings_from_report, load_enforcement)

SCANNER_VERSION = "4.4.9.14"
SCANNER_SHA512 = ("f627d17dfa7927a8352a2cf329c370a74aecfbd759141d89d0d0b20541ff220ae"
                  "903951aab9edcd444a24980cfae20c939d7b9c69d380482a439341aa35a7838")
RULE_ID = re.compile(r"BA[0-9]{4}\Z")
MESSAGE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
VALID_KIND = {"fail", "pass", "notApplicable", "informational", "open", "review"}
VALID_LEVEL = {"error", "warning", "note", "none"}
MAX_REPORT_SIZE = 32 * 1024 * 1024


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def file_record(path: Path) -> dict:
    if not path.is_file():
        return {"present": False, "sha256": None, "size": None}
    return {"present": True, "sha256": digest(path), "size": path.stat().st_size}


def safe_sarif(original: dict, target: str) -> dict:
    """Allowlist-only SARIF projection; no raw messages, paths, source or commands."""
    run = original["runs"][0]
    results = []
    rules = set()
    for item in run["results"]:
        rule = item.get("ruleId")
        kind = item.get("kind", "fail")
        level = item.get("level", "warning")
        if not isinstance(rule, str) or not RULE_ID.fullmatch(rule):
            raise ValueError("Unrecognized rule ID in SARIF")
        if kind not in VALID_KIND or level not in VALID_LEVEL:
            raise ValueError("Unrecognized SARIF result classification")
        rules.add(rule)
        message = {"text": f"BinSkim {rule}: {kind}"}
        mid = item.get("message", {}).get("id", "")
        if isinstance(mid, str) and MESSAGE_ID.fullmatch(mid):
            message["id"] = mid
        results.append({
            "ruleId": rule, "level": level, "kind": kind, "message": message,
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": target}}}],
        })
    completed = all(inv.get("executionSuccessful") is True
                    for inv in run["invocations"])
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{
                "tool": {"driver": {"name": "BinSkim", "version": SCANNER_VERSION,
                                    "rules": [{"id": rule} for rule in sorted(rules)]}},
                "invocations": [{"executionSuccessful": completed}],
                "artifacts": [{"location": {"uri": target}}],
                "results": results,
            }]}


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def baseline_keys(path: Path) -> set[tuple]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("accepted_findings"), list):
        raise ValueError("Invalid baseline")
    keys = set()
    for row in data["accepted_findings"]:
        key = tuple(row[name] for name in ("target", "rule", "kind", "level", "message_id"))
        if key in keys or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("Invalid baseline entry")
        keys.add(key)
    return keys


def collect(build: Path, reports: Path, baseline: Path, policy: Path,
            output: Path, *, version_file: Path, source_commit: str = "",
            run_id: str = "") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    evidence_sarif = output / "sarif"
    evidence_sarif.mkdir(exist_ok=True)

    reasons = set()
    policy_record = file_record(policy)
    baseline_record = file_record(baseline)
    try:
        enforcement = load_enforcement(policy)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
        enforcement = {}
        reasons.add("invalid_policy")
    try:
        accepted = baseline_keys(baseline)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
        accepted = set()
        reasons.add("invalid_baseline")

    targets = {}
    observed = []
    rule_counts = Counter()
    for arch in ARCHES:
        for name in BINARIES:
            target = f"{arch}/{name}"
            image = build / arch / name
            entry = file_record(image)
            entry["status"] = "present" if entry["present"] else "missing_binary"
            if not entry["present"]:
                reasons.add("missing_binary")
            source = reports / f"{arch}-{name}.sarif"
            sarif = {"status": "missing", "raw_sha256": None, "sanitized_sha256": None}
            if source.is_file():
                sarif["raw_sha256"] = digest(source)
                try:
                    if source.stat().st_size > MAX_REPORT_SIZE:
                        raise ValueError("Oversized SARIF")
                    original = json.loads(source.read_text(encoding="utf-8"))
                    findings = findings_from_report(original, target)
                    curated = safe_sarif(original, target)
                    dest = evidence_sarif / f"{arch}-{name}.sarif"
                    write_json(dest, curated)
                    sarif.update(status="complete",
                                 sanitized_sha256=digest(dest),
                                 output=f"sarif/{dest.name}",
                                 results=len(curated["runs"][0]["results"]))
                    observed.extend(findings)
                    for item in findings:
                        rule_counts[item[1]] += 1
                except (ValueError, OSError, TypeError, KeyError, IndexError,
                        AttributeError, json.JSONDecodeError):
                    sarif["status"] = "invalid"
                    reasons.add("invalid_sarif")
            else:
                reasons.add("missing_sarif")
            entry["sarif"] = sarif
            targets[target] = entry

    packages = sorted(build.glob("*.fb2k-component"))
    package = file_record(packages[0]) if len(packages) == 1 else {
        "present": False, "sha256": None, "size": None}
    if len(packages) == 1:
        try:
            check_package(build)
            package["content_verified"] = True
        except (ValueError, OSError, BadZipFile, KeyError):
            package["content_verified"] = False
            reasons.add("package_mismatch")
    else:
        package["content_verified"] = False
        reasons.add("missing_or_ambiguous_package")

    for arch in ARCHES:
        dll = build / arch / BINARIES[0]
        worker = build / arch / BINARIES[1]
        if dll.is_file() and worker.is_file():
            try:
                match = worker.stat().st_size > 256 and worker.read_bytes() in dll.read_bytes()
                targets[f"{arch}/{BINARIES[0]}"]["embedded_worker_match"] = match
                if not match:
                    reasons.add("embedded_worker_mismatch")
            except OSError:
                targets[f"{arch}/{BINARIES[0]}"]["embedded_worker_match"] = None
                reasons.add("embedded_worker_unverified")
        else:
            targets[f"{arch}/{BINARIES[0]}"]["embedded_worker_match"] = None

    accepted_found = 0
    advisory = 0
    blocking = 0
    for finding in observed:
        if finding in accepted:
            accepted_found += 1
        elif enforcement.get(finding[1], "blocking") == "advisory":
            advisory += 1
        else:
            blocking += 1
    if blocking:
        reasons.add("blocking_findings")
    if accepted - set(observed):
        reasons.add("stale_exception")
    if not reasons:
        try:
            check(build, reports, baseline, policy)
        except (ValueError, OSError, TypeError, KeyError, BadZipFile, json.JSONDecodeError):
            reasons.add("policy_validation_failed")

    version = None
    try:
        candidate = version_file.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", candidate):
            version = candidate
    except OSError:
        pass
    if version is None:
        reasons.add("missing_version")

    manifest = {
        "schema_version": 1,
        "qualification": "wip-unqualified",
        "source_commit": source_commit if re.fullmatch(r"[a-fA-F0-9]{40}", source_commit) else None,
        "ci_run_id": run_id if re.fullmatch(r"[0-9]+", run_id) else None,
        "version": version,
        "scanner": {"name": "BinSkim", "version": SCANNER_VERSION,
                    "package_sha512": SCANNER_SHA512},
        "policy_sha256": policy_record["sha256"],
        "baseline_sha256": baseline_record["sha256"],
        "package": package,
        "targets": targets,
    }
    write_json(output / "binary-manifest.json", manifest)

    verdict = {
        "schema_version": 1,
        "qualification": "wip-unqualified",
        "status": "fail" if reasons else "pass",
        "policy_sha256": policy_record["sha256"],
        "manifest_sha256": digest(output / "binary-manifest.json"),
        "complete_scans": sum(1 for x in targets.values() if x["sarif"]["status"] == "complete"),
        "expected_scans": len(ARCHES) * len(BINARIES),
        "findings": {"blocking": blocking, "advisory": advisory,
                     "accepted_exceptions": accepted_found, "by_rule": dict(sorted(rule_counts.items()))},
        "reasons": sorted(reasons),
    }
    write_json(output / "binskim-verdict.json", verdict)
    summary = [
        "# BinSkim evidence (WIP, not an attestation)",
        f"Status: {verdict['status']}",
        f"Validated scans: {verdict['complete_scans']}/{verdict['expected_scans']}",
        f"Findings: blocking={blocking}, advisory={advisory}, approved={accepted_found}",
        "Failure codes: " + (", ".join(sorted(reasons)) if reasons else "none"),
        "By rule: " + (", ".join(f"{key}={value}" for key, value in sorted(rule_counts.items()))
                       if rule_counts else "none"),
        "Original scanner text, unredacted SARIF and PDBs are excluded.",
    ]
    (output / "failure-summary.md").write_text("\n".join(summary) + "\n",
                                               encoding="utf-8")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--version-file", type=Path, required=True)
    args = ap.parse_args()
    verdict = collect(args.build, args.reports, args.baseline, args.policy,
                      args.output, version_file=args.version_file,
                      source_commit=os.environ.get("GITHUB_SHA", ""),
                      run_id=os.environ.get("GITHUB_RUN_ID", ""))
    print(f"BinSkim evidence: {verdict['status']}, "
          f"{verdict['complete_scans']}/{verdict['expected_scans']} scans, "
          f"{verdict['findings']['blocking']} blocking findings")
    return 0 if verdict["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
