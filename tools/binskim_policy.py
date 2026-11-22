"""Validate BinSkim coverage and findings for shipped Windows PE outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

ARCHES = ("Win32", "x64", "ARM64EC")
BINARIES = ("foo_doujinstyle.dll", "publish_references.exe")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_package(build: Path) -> None:
    packages = list(build.glob("*.fb2k-component"))
    if len(packages) != 1:
        raise ValueError(f"Expected one component package; found {len(packages)}")
    expected = {"foo_doujinstyle.dll": "Win32", "x64/foo_doujinstyle.dll": "x64",
                "arm64ec/foo_doujinstyle.dll": "ARM64EC"}
    with ZipFile(packages[0]) as package:
        dlls = {name for name in package.namelist() if name.lower().endswith(".dll")}
        if dlls != set(expected):
            raise ValueError("Component package PE layout differs from expected architectures")
        for name, arch in expected.items():
            if sha256(package.read(name)) != sha256((build / arch / "foo_doujinstyle.dll").read_bytes()):
                raise ValueError(f"Packaged DLL differs from scanned build output: {arch}")


def normalize(uri: str) -> str:
    parts = urlsplit(uri)
    value = unquote(parts.path if parts.scheme == "file" else uri)
    return value.replace("\\", "/").lower()


def covers(run: dict, target: str) -> bool:
    suffix = "/" + target.lower()
    seen = []
    artifacts = run.get("artifacts", [])
    for artifact in artifacts:
        loc = artifact.get("location", {})
        if isinstance(loc.get("uri"), str):
            seen.append(loc["uri"])
    for result in run.get("results", []):
        for location in result.get("locations", []):
            loc = location.get("physicalLocation", {}).get("artifactLocation", {})
            if isinstance(loc.get("uri"), str):
                seen.append(loc["uri"])
            index = loc.get("index")
            if isinstance(index, int) and 0 <= index < len(artifacts):
                value = artifacts[index].get("location", {}).get("uri")
                if isinstance(value, str):
                    seen.append(value)
    return any(normalize(uri).endswith(suffix) for uri in seen)


def findings_from_report(data: dict, target: str) -> list[tuple]:
    if data.get("version") != "2.1.0" or not isinstance(data.get("runs"), list) or len(data["runs"]) != 1:
        raise ValueError(f"Invalid BinSkim SARIF: {target}")
    run = data["runs"][0]
    if run.get("tool", {}).get("driver", {}).get("name", "").lower() != "binskim":
        raise ValueError(f"Unexpected SARIF scanner: {target}")
    invocations = run.get("invocations")
    if not isinstance(invocations, list) or not invocations:
        raise ValueError(f"Missing scanner execution record: {target}")
    for invocation in invocations:
        if invocation.get("executionSuccessful") is not True:
            raise ValueError(f"BinSkim did not complete successfully: {target}")
        for entry in invocation.get("toolExecutionNotifications", []) + invocation.get("configurationNotifications", []):
            if entry.get("level", "error") in {"error", "warning"}:
                raise ValueError(f"BinSkim issued a scanner notification: {target}")
    if not covers(run, target):
        raise ValueError(f"BinSkim did not document target coverage: {target}")
    results = run.get("results", [])
    if not isinstance(results, list) or not results:
        raise ValueError(f"BinSkim reported no rules for target: {target}")
    if not any(r.get("kind", "fail") in ("pass", "fail", "open", "review") for r in results):
        raise ValueError(f"BinSkim provided only not-applicable/informational results: {target}")
    findings = []
    for r in results:
        if r.get("kind", "fail") in {"pass", "notApplicable", "informational"}:
            continue
        level = r.get("level", "warning")
        if level not in {"error", "warning"}:
            continue
        rule = r.get("ruleId", "")
        if not rule.startswith("BA"):
            raise ValueError(f"Missing/invalid BinSkim rule identity: {target}")
        findings.append((target, rule, r.get("kind", "fail"), level, r.get("message", {}).get("id", "")))
    return findings


def check(build: Path, reports: Path, baseline: Path) -> list[tuple]:
    check_package(build)
    policy = json.loads(baseline.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1 or not isinstance(policy.get("accepted_findings"), list):
        raise ValueError("Invalid BinSkim baseline")
    accepted = set()
    for row in policy["accepted_findings"]:
        key = tuple(row[k] for k in ("target", "rule", "kind", "level", "message_id"))
        if key in accepted or not row.get("reason"):
            raise ValueError("Invalid or duplicate accepted BinSkim finding")
        accepted.add(key)
    all_findings = []
    for arch in ARCHES:
        for name in BINARIES:
            target = f"{arch}/{name}"
            path = build / arch / name
            if not path.is_file() or path.stat().st_size < 256 or path.read_bytes()[:2] != b"MZ":
                raise ValueError(f"Missing/invalid shipped PE: {target}")
            path_report = reports / f"{arch}-{name}.sarif"
            if not path_report.is_file():
                raise ValueError(f"Missing BinSkim SARIF: {target}")
            findings = findings_from_report(json.loads(path_report.read_text(encoding="utf-8")), target)
            all_findings.extend(findings)
    unexpected = set(all_findings) - accepted
    if unexpected:
        summary = ", ".join(f"{x[0]}:{x[1]}:{x[2]}:{x[3]}:{x[4]}" for x in sorted(unexpected))
        raise ValueError(f"Unaccepted BinSkim findings: {summary}")
    if accepted - set(all_findings):
        raise ValueError("BinSkim baseline includes unobserved findings; remove stale exceptions")
    return all_findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    args = ap.parse_args()
    try:
        findings = check(args.build, args.reports, args.baseline)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: BinSkim scanned six PE outputs; accepted baseline findings: {len(findings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
