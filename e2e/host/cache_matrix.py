"""Constrained pairwise cache/lifecycle E2E test *planner*, not an E2E runner.

Produces a deterministic set-cover of all *feasible* factor pairs plus reviewed
mandatory lifecycle/fault cases. Every verdict remains not_run until the actual
host/worker fixtures execute it. No external Python dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
from itertools import combinations, product
import json
from pathlib import Path
import re
import sys

EXPECTED = {
    "search": "catalog_rows_committed",
    "select": "no_new_selection_caused_network_or_index_job",
    "drag": "published_track_drag_payload",
    "play": "first_decoded_pcm",
    "seek": "correct_post_seek_pcm",
    "idle-backfill": "committed_archive_index_before_visible_notification",
}
FACTOR_NAMES = (
    "trigger", "archive", "codec", "catalog", "zip_directory",
    "checkpoints", "runtime", "connection", "range_cache", "provider",
)
ID = re.compile(r"[a-z][a-z0-9-]*\Z")
NEGATIVE_FAULTS = frozenset({
    "etag-changed", "truncated-response", "http-429", "stop-before-first-byte",
    "interrupt-between-commit-and-notify", "archive-identity-changed",
    "inflight-index",
})


class MatrixError(ValueError):
    pass


def validate_manifest(doc: dict) -> None:
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise MatrixError("Unsupported matrix schema")
    dims = doc.get("dimensions")
    if not isinstance(dims, dict) or tuple(dims) != FACTOR_NAMES:
        raise MatrixError("Factor names/order changed; update schema and constraints")
    for key, options in dims.items():
        if (not isinstance(options, list) or len(options) < 2 or
                len(set(options)) != len(options) or
                any(not isinstance(value, str) or not ID.fullmatch(value)
                    for value in options)):
            raise MatrixError(f"Invalid or duplicate {key} factor values")
    if set(dims["trigger"]) != set(EXPECTED):
        raise MatrixError("Trigger taxonomy diverged from semantic expectations")
    required = {
        "archive": {"direct", "zip-stored", "zip-deflate"},
        "catalog": {"empty", "metadata", "references"},
        "zip_directory": {"not-applicable", "missing", "persisted"},
        "checkpoints": {"not-applicable", "missing", "partial", "complete"},
        "runtime": {"fresh", "warm-process", "restarted"},
        "connection": {"fresh", "reused", "server-closed"},
        "range_cache": {"cold", "memory"},
        "provider": {"loopback", "mediafire-stub", "mega-stub"},
    }
    for name, expected in required.items():
        if set(dims[name]) != expected:
            raise MatrixError(f"Unsupported factor vocabulary in {name}")
    if type(doc.get("pairwise_case_limit")) is not int or not 1 <= doc["pairwise_case_limit"] <= 500:
        raise MatrixError("Invalid pairwise case budget")
    if not isinstance(doc.get("anchors"), list) or not doc["anchors"]:
        raise MatrixError("Missing lifecycle anchors")
    anchors = set()
    for anchor in doc["anchors"]:
        if not isinstance(anchor, dict) or set(anchor) != set(FACTOR_NAMES) | {"id", "expected"}:
            raise MatrixError("Anchor must specify every factor, identity and expectation")
        if anchor["id"] in anchors or not ID.fullmatch(anchor["id"]):
            raise MatrixError("Duplicate or invalid anchor identity")
        anchors.add(anchor["id"])
        row = {k: anchor[k] for k in FACTOR_NAMES}
        if not valid(row) or any(row[k] not in dims[k] for k in FACTOR_NAMES):
            raise MatrixError(f"Impossible lifecycle anchor: {anchor['id']}")
        if anchor["expected"] != EXPECTED[row["trigger"]]:
            raise MatrixError(f"Incorrect oracle for {anchor['id']}")
    faults = doc.get("directed_faults", [])
    if not isinstance(faults, list):
        raise MatrixError("Fault scenarios must be a list")
    seen_faults = set()
    for fault in faults:
        if (not isinstance(fault, dict) or set(fault) != {"id", "base", "fault", "expected"} or
                not isinstance(fault["id"], str) or not ID.fullmatch(fault["id"]) or
                fault["id"] in anchors or fault["id"] in seen_faults or
                fault["base"] not in anchors or fault["fault"] not in NEGATIVE_FAULTS or
                not isinstance(fault["expected"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", fault["expected"])):
            raise MatrixError("Invalid fault case definition")
        seen_faults.add(fault["id"])


def valid(r: dict) -> bool:
    if r["archive"] == "direct":
        if r["zip_directory"] != "not-applicable" or r["checkpoints"] != "not-applicable":
            return False
    elif r["archive"] == "zip-stored":
        if r["zip_directory"] == "not-applicable" or r["checkpoints"] != "not-applicable":
            return False
    elif r["archive"] == "zip-deflate":
        if r["zip_directory"] == "not-applicable" or r["checkpoints"] == "not-applicable":
            return False
        if r["checkpoints"] in {"partial", "complete"} and r["zip_directory"] != "persisted":
            return False
    else:
        return False
    # Only volatile in-process range bytes exist today. Persisted ZIP seek
    # checkpoints do not constitute a disk-backed HTTP response cache.
    if r["runtime"] == "fresh":
        if (r["range_cache"] != "cold" or r["connection"] != "fresh" or
                r["zip_directory"] == "persisted" or
                r["checkpoints"] in {"partial", "complete"}):
            return False
    elif r["runtime"] == "restarted":
        if r["connection"] == "reused" or r["range_cache"] == "memory":
            return False
    elif r["runtime"] == "warm-process":
        pass
    else:
        return False
    if r["range_cache"] not in {"cold", "memory"}:
        return False
    if r["range_cache"] == "memory" and r["runtime"] != "warm-process":
        return False
    if r["connection"] == "reused" and r["runtime"] != "warm-process":
        return False
    if r["trigger"] in {"drag", "play", "seek"} and r["catalog"] != "references":
        return False
    if r["trigger"] == "select" and r["catalog"] == "empty":
        return False
    if r["trigger"] not in {"search", "idle-backfill"} and r["catalog"] == "empty":
        return False
    if r["provider"] != "loopback" and r["archive"] == "direct":
        return False
    # Current MEGA storage response does not expose the strong HTTP ETag
    # required by RangeObject.checkpoint_directory() for persisted seeks.
    if r["provider"] == "mega-stub" and r["checkpoints"] in {"partial", "complete"}:
        return False
    return True


def pairs(row: dict) -> tuple[tuple[str, str, str, str], ...]:
    return tuple((a, row[a], b, row[b]) for a, b in combinations(FACTOR_NAMES, 2))


def reset_contract(row: dict) -> dict:
    runtime = {
        "fresh": "clean-vm-and-profile-plus-seeded-synthetic-reference-if-needed",
        "warm-process": "prime-deterministic-state-and-prove-cache-and-pool-identity",
        "restarted": "prime-close-host-restart-with-verified-persisted-state",
    }
    preconditions = [
        runtime[row["runtime"]],
        "prove-profile-catalog-directory-checkpoint-content-and-digests",
        "prove-connection-identity-and-HTTP-request-counter-baseline",
        "prove-in-process-range-cache-hit-miss-state-not-just-nominal-temperature",
        "record-OS-page-cache-state-as-unknown-unless-independently-controlled",
    ]
    if row["trigger"] == "select":
        preconditions.append("quiesce-existing-workers-before-counting-selection-attributed-work")
    if row["trigger"] in {"drag", "play", "seek"}:
        preconditions.append("verify-published-djt-exists-before-action")
    if row["trigger"] == "seek":
        preconditions.append("already-playing-and-decoding-fixture-before-seek")
    return {"setup": preconditions, "verdict": "not_run",
            "host_qualification": "not_qualified", "native_coverage": None,
            "worker_coverage": None, "output_pcm_verified": None}


def make_case(case_id: str, row: dict, *, origin: str,
              expected: str, fault: str | None = None) -> dict:
    qualification = reset_contract(row)
    if fault == "inflight-index":
        preconditions = qualification["setup"]
        preconditions.remove("quiesce-existing-workers-before-counting-selection-attributed-work")
        preconditions.append("prove-independent-index-worker-already-running-before-selection")
        preconditions.append("attribute-worker-completion-to-its-preexisting-parent-action")
    return {
        "id": case_id, "origin": origin, "factors": dict(row),
        "fault": fault, "expected": expected,
        "qualification": qualification,
        "note": "A test plan is not evidence that the action, format, provider or decoder works.",
    }


def build(doc: dict) -> dict:
    validate_manifest(doc)
    dims = doc["dimensions"]
    candidates = [
        dict(zip(FACTOR_NAMES, values))
        for values in product(*(dims[k] for k in FACTOR_NAMES))
        if valid(dict(zip(FACTOR_NAMES, values)))
    ]
    if not candidates:
        raise MatrixError("No feasible combinations")
    anchors = doc["anchors"]
    anchor_rows = [{k: row[k] for k in FACTOR_NAMES} for row in anchors]
    pair_universe = sorted({pair for row in candidates for pair in pairs(row)})
    pair_index = {pair: i for i, pair in enumerate(pair_universe)}
    full_mask = (1 << len(pair_universe)) - 1

    def mask(row: dict) -> int:
        bits = 0
        for pair in pairs(row):
            bits |= 1 << pair_index[pair]
        return bits

    indexed = [(row, mask(row)) for row in candidates]
    selected = list(anchor_rows)
    identities = {tuple(row[k] for k in FACTOR_NAMES) for row in selected}
    covered = 0
    for row in selected:
        covered |= mask(row)
    pairwise = []
    limit = doc["pairwise_case_limit"]
    while covered != full_mask:
        if len(selected) >= limit:
            raise MatrixError(f"Pairwise budget exhausted: {covered.bit_count()}/{len(pair_universe)} pairs")
        best = None
        best_gain = 0
        for index, (row, bits) in enumerate(indexed):
            gain = (bits & ~covered).bit_count()
            if gain > best_gain:
                best = index
                best_gain = gain
        if best is None or best_gain <= 0:
            raise MatrixError("No feasible candidate covers the remaining pairs")
        row, bits = indexed[best]
        key = tuple(row[k] for k in FACTOR_NAMES)
        if key in identities:
            raise MatrixError("Duplicate candidate selected by greedy coverage")
        identities.add(key)
        selected.append(row)
        pairwise.append(row)
        covered |= bits

    cases = [make_case(a["id"], row, origin="mandatory-anchor",
                       expected=a["expected"])
             for a, row in zip(anchors, anchor_rows)]
    for row in pairwise:
        digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]
        cases.append(make_case("pair-" + digest, row, origin="pairwise",
                               expected=EXPECTED[row["trigger"]]))
    by_anchor = {a["id"]: row for a, row in zip(anchors, anchor_rows)}
    for fault in doc["directed_faults"]:
        cases.append(make_case(fault["id"], by_anchor[fault["base"]],
                               origin="directed-fault",
                               expected=fault["expected"], fault=fault["fault"]))
    if len({c["id"] for c in cases}) != len(cases):
        raise MatrixError("Test case ID collision")
    return {
        "schema_version": 1,
        "kind": "planned-cache-e2e-matrix",
        "status": "not_run",
        "qualification": "scenario-generator-not-foobar2000-qualification",
        "canonical_manifest_sha256": hashlib.sha256(
            (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest(),
        "feasible_combination_count": len(candidates),
        "feasible_pair_count": len(pair_universe),
        "planned_pair_coverage_count": covered.bit_count(),
        "planned_pair_coverage_fraction": covered.bit_count() / len(pair_universe),
        "mandatory_anchor_count": len(anchors),
        "pairwise_added_count": len(pairwise),
        "directed_fault_count": len(doc["directed_faults"]),
        "executed_case_count": 0,
        "cases": cases,
        "exclusions": [
            "Physical audio output, file-backed PCM evidence and Windows GUI execution not run",
            "Real provider HTTP and native decoder support not established by synthetic plan",
            "Cold profile is not proof of cold OS page cache, DNS or TLS session state",
            "Connection reuse requires observed socket/request identity, not just a warm label",
            "Selection is currently passive; no speculative selection-prefetch feature claimed",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        doc = json.loads(args.manifest.read_text(encoding="utf-8"))
        plan = build(doc)
        if args.output.exists():
            raise MatrixError("Refusing to overwrite existing quality evidence")
        args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"Planned {len(plan['cases'])} cases, "
              f"{plan['feasible_pair_count']} feasible pairs; "
              "0 host tests executed")
        return 0
    except (OSError, MatrixError, ValueError) as exc:
        print(f"Scenario plan rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
