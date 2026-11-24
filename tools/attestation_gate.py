"""Fail-closed handoff from six-PE BinSkim inspection to artifact attestation.

This validates binary-security provenance only. It does NOT assert Windows host
playback, runtime safety, software vulnerability freedom or reproducibility.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from tools.binskim_policy import ARCHES, BINARIES
from tools.build_identity import identify

def checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def required(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)

def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    required(isinstance(value, dict), "Invalid evidence document")
    return value

def validate(root: Path, package_dir: Path, evidence_dir: Path,
             commit: str, run_id: str, identity: dict | None = None) -> dict:
    required(re.fullmatch("[a-f0-9]{40}", commit) is not None, "Malformed commit identity")
    required(re.fullmatch("[0-9]+", run_id) is not None, "Malformed run identity")
    identity = identity if identity is not None else identify(root)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    required(identity.get("dirty") is False and identity.get("git_commit") == commit
             and identity.get("base_version") == version
             and re.fullmatch("[a-f0-9]{64}", identity.get("source_sha256", "")),
             "Source identity mismatch")
    policy = root / "security/bin-policy.json"
    baseline = root / "tools/binskim-baseline.json"
    policy_doc, baseline_doc = load(policy), load(baseline)
    required(policy_doc.get("default_enforcement") == "blocking" and
             policy_doc.get("rules", {}).get("BA2024", {}).get("enforcement") == "blocking"
             and baseline_doc.get("accepted_findings") == [],
             "Blocking policy or no-exceptions invariant violated")
    manifest_file = evidence_dir / "binary-manifest.json"
    manifest = load(manifest_file)
    verdict = load(evidence_dir / "binskim-verdict.json")
    required(manifest.get("schema_version") == 1 and verdict.get("schema_version") == 1
             and manifest.get("source_commit") == commit
             and manifest.get("ci_run_id") == run_id
             and manifest.get("version") == version, "Evidence source/run/version mismatch")
    required(manifest.get("policy_sha256") == checksum(policy)
             and verdict.get("policy_sha256") == checksum(policy)
             and manifest.get("baseline_sha256") == checksum(baseline)
             and verdict.get("manifest_sha256") == checksum(manifest_file),
             "Evidence/manifest/policy digest mismatch")
    # Only the declared four-component source-input inventory is present.
    # Bind its bytes without implying an exhaustive binary dependency SBOM.
    inventory_file = evidence_dir / "source-inputs.spdx.json"
    inventory = load(inventory_file)
    required(inventory.get("spdxVersion") == "SPDX-2.3"
             and inventory.get("documentNamespace") ==
                 "https://github.com/bgonz808/foo_doujinstyle/spdx/source/" + commit
             and isinstance(inventory.get("documentComment"), str)
             and "PARTIAL" in inventory["documentComment"]
             and isinstance(inventory.get("packages"), list)
             and len(inventory["packages"]) == 4,
             "Partial source inventory missing, incomplete or source mismatch")
    findings = verdict.get("findings") or {}
    required(verdict.get("status") == "pass" and verdict.get("reasons") == []
             and verdict.get("complete_scans") == 6 and verdict.get("expected_scans") == 6
             and findings.get("blocking") == 0
             and findings.get("accepted_exceptions") == 0,
             "Six-PE security scan did not pass without exceptions")
    packages = list(package_dir.glob("*.fb2k-component"))
    required(len(packages) == 1, "Expected one exact package")
    pkg = packages[0]
    required(pkg.name == "foo_doujinstyle-v" + identity["version"] + ".fb2k-component",
             "Package source version mismatch")
    package_info = manifest.get("package") or {}
    required(package_info.get("content_verified") is True
             and package_info.get("sha256") == checksum(pkg)
             and package_info.get("size") == pkg.stat().st_size,
             "Package hash or scanned-content match failed")
    rows = manifest.get("targets")
    expected = {f"{arch}/{name}" for arch in ARCHES for name in BINARIES}
    required(isinstance(rows, dict) and set(rows) == expected,
             "Six-target binary manifest incomplete")
    for arch in ARCHES:
        for name in BINARIES:
            item = rows[f"{arch}/{name}"]
            scan = item.get("sarif") or {}
            file = f"{arch}-{name}.sarif"
            safe_file = evidence_dir / "sarif" / file
            required(item.get("present") is True
                     and re.fullmatch("[a-f0-9]{64}", item.get("sha256", ""))
                     and scan.get("status") == "complete"
                     and re.fullmatch("[a-f0-9]{64}", scan.get("raw_sha256", ""))
                     and scan.get("output") == "sarif/" + file
                     and safe_file.is_file()
                     and checksum(safe_file) == scan.get("sanitized_sha256"),
                     "Unverified PE/SARIF record " + arch + "/" + name)
        required(rows[f"{arch}/foo_doujinstyle.dll"].get("embedded_worker_match") is True,
                 "Embedded worker does not match scanned helper " + arch)
    return {
        "schema_version": 1, "qualification": "six-pe-ci-bin-skim-only-not-host-qualified",
        "source_commit": commit, "source_sha256": identity["source_sha256"],
        "component_version": identity["version"], "github_run_id": run_id,
        "package_sha256": checksum(pkg),
        "binary_manifest_sha256": checksum(manifest_file),
        "partial_source_inventory_sha256": checksum(inventory_file),
        "inventory_scope": "partial-vendored-source-inputs-only",
        "policy_sha256": checksum(policy), "baseline_sha256": checksum(baseline),
        "scanner": manifest.get("scanner"),
        "scans": 6, "blocking_findings": 0, "accepted_exceptions": 0,
        "advisory_findings": findings.get("advisory", 0)
    }

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--package-dir", type=Path, required=True)
    p.add_argument("--evidence-dir", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        result = validate(Path.cwd(), args.package_dir, args.evidence_dir,
                          args.source_commit, args.run_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("FAIL: cannot attest: " + str(exc), file=sys.stderr)
        return 1
    print("PASS: exact package SHA-256 bound to six scans and source provenance")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
