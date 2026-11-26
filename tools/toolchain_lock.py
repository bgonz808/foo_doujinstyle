"""Compare an observed installation to a reviewed, content-addressed candidate.

A cache or hosted runner is only an acquisition source. No trust-on-first-use,
automatic approval, fallback download, or modification of the host installation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOTS = frozenset(("msbuild-bin", "msbuild-vc-targets", "msvc-hostx64",
                   "msvc-include", "msvc-lib", "sdk-bin", "sdk-include", "sdk-lib"))
HEX = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_roots(rows):
    require(isinstance(rows, dict) and set(rows) == ROOTS, "Unexpected inventory roots")
    for row in rows.values():
        require(isinstance(row, dict) and set(row) == {"sha256", "files", "bytes"}
                and isinstance(row["sha256"], str) and HEX.fullmatch(row["sha256"])
                and type(row["files"]) is int and row["files"] > 0
                and type(row["bytes"]) is int and row["bytes"] > 0,
                "Invalid inventory record")
    return rows


def evaluate(lock, inventory, release=False):
    require(isinstance(lock, dict) and lock.get("schema_version") == 1
            and lock.get("algorithm") == "DS-TOOLCHAIN-TREE-V1"
            and lock.get("msvc_version") == "14.44.35207"
            and lock.get("windows_sdk_version") == "10.0.26100.0"
            and lock.get("qualification") in ("candidate", "approved"),
            "Invalid toolchain lock")
    expected = validate_roots(lock.get("roots"))
    actual = validate_roots(inventory)
    differences = sorted(k for k in ROOTS if actual[k] != expected[k])
    approved = lock["qualification"] == "approved"
    return {"match": not differences, "differences": differences,
            "qualification": lock["qualification"],
            "allowed": not differences and (approved or not release)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lock", type=Path, required=True)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--release", action="store_true")
    args = p.parse_args()
    try:
        lock_bytes = args.lock.read_bytes()
        inventory_bytes = args.inventory.read_bytes()
        result = evaluate(json.loads(lock_bytes), json.loads(inventory_bytes), args.release)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SystemExit("Invalid or incomplete toolchain evidence") from None
    result["lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
    result["inventory_sha256"] = hashlib.sha256(inventory_bytes).hexdigest()
    result["source"] = "preinstalled-hosted-runner"
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("TOOLCHAIN-LOCK match=" + str(result["match"]).lower()
          + " qualification=" + result["qualification"]
          + " drift_roots=" + str(len(result["differences"])))
    if not result["allowed"]:
        raise SystemExit("Unqualified toolchain: explicit review or verified provisioning required")


if __name__ == "__main__":
    main()
