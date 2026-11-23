"""Deterministic streaming I/O *plan*; single-host batches, never fabricated E2E evidence.

Formats remain decoded by foobar2000's input services. The component's
file_readonly cursor is the test boundary for remote member access.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

class ProfileError(ValueError):
    pass

REQUIRED_METRICS = {
    "first_nonempty_pcm_qpc", "cursor_read_count", "cursor_seek_count",
    "cursor_reopen_count", "compressed_http_body_bytes", "http_range_request_count",
    "checkpoint_restore_count", "post_seek_sample_alignment",
    "first_party_code_coverage",
}
REQUIRED_LIFECYCLES = [
    "cold-asset-in-running-host", "warm-asset-in-running-host",
    "reopened-track", "host-restarted-persistent-state",
]
REQUIRED_ACTIONS = [
    "open-for-playback", "play-to-first-pcm", "seek-near-start",
    "seek-middle", "seek-near-end", "stop-with-queued-read",
]

def generate(doc: dict) -> dict:
    if type(doc) is not dict or doc.get("schema_version") != 1:
        raise ProfileError("Invalid streaming matrix schema")
    modes, profiles = doc.get("archive_modes"), doc.get("audio_profiles")
    if not isinstance(modes, list) or not isinstance(profiles, list):
        raise ProfileError("Missing archive mode or audio profiles")
    if len(modes) < 3 or len(profiles) < 8:
        raise ProfileError("Missing required archive/technical format profiles")
    for values in (modes, profiles):
        ids = [item.get("id") for item in values if isinstance(item, dict)]
        if len(ids) != len(values) or len(set(ids)) != len(values) or not all(
                isinstance(x, str) and x for x in ids):
            raise ProfileError("Duplicate or invalid profile identifiers")
    if {x["id"] for x in modes} != {"direct", "zip-store", "zip-deflate"}:
        raise ProfileError("Unsupported archive cursor taxonomy")
    if doc.get("lifecycle_phases") != REQUIRED_LIFECYCLES:
        raise ProfileError("Missing or unordered session lifecycle phases")
    if doc.get("actions") != REQUIRED_ACTIONS:
        raise ProfileError("Missing or unordered decoder I/O actions")
    if not REQUIRED_METRICS <= set(doc.get("required_observables", [])):
        raise ProfileError("Required latency, cursor or correctness evidence omitted")
    for item in profiles:
        if item.get("codec") not in {"wav", "flac", "mp3", "opus"} or not item.get("technical"):
            raise ProfileError("Unqualified format specification")
    asset_cases = []
    for mode in modes:
        for profile in profiles:
            ident = f"{mode['id']}--{profile['id']}"
            steps = []
            for phase in REQUIRED_LIFECYCLES:
                for action in REQUIRED_ACTIONS:
                    steps.append({
                        "phase": phase, "action": action, "verdict": "not_run",
                        "duration_ns": None, "observed_cursor_reads": None,
                        "actual_decoder": None, "validated_pcm": None,
                    })
            asset_cases.append({
                "id": ident, "archive": mode["id"], "cursor": mode["cursor"],
                "format": profile["codec"], "technical": profile["technical"],
                "focus": profile["focus"], "source_sha256": None,
                "host_capability": "not_qualified",
                "steps": steps,
            })
    return {
        "schema_version": 1, "kind": "decoder-cursor-matrix",
        "status": "not_run", "asset_combinations": len(asset_cases),
        "planned_step_count": sum(len(case["steps"]) for case in asset_cases),
        "host_process_schedule": [
            {
                "id": "session-one",
                "start": "fresh_disposable_host",
                "phase_groups": REQUIRED_LIFECYCLES[:-1],
                "restart_count": 0,
                "note": "Only the first action occurs in a fresh process. Other assets are cold by key, not by OS or connection.",
            },
            {
                "id": "session-two",
                "start": "restart_same_profile_preserving_disk_state",
                "phase_groups": REQUIRED_LIFECYCLES[-1:],
                "restart_count": 1,
                "note": "Verify per-asset disk snapshots and no volatile range-cache survivors.",
            },
        ],
        "trace_blocker": (
            "The current host ETW provider snapshots one attempt ID at initialization. "
            "Multiple test attempts in one process cannot be correlated until mutable, "
            "per-asset/per-action trace contexts are implemented and qualified."
        ),
        "required_observables": doc["required_observables"],
        "unsupported_archive_cases": doc["unsupported_archive_cases"],
        "named_regressions": doc["named_regressions"],
        "source_manifest_sha256": hashlib.sha256(
            json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "first_party_dll_coverage": None,
        "first_party_worker_coverage": None,
        "host_e2e_cases_executed": 0,
        "cases": asset_cases,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ProfileError("Refusing to overwrite plan/evidence")
        result = generate(json.loads(args.manifest.read_text(encoding="utf-8")))
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"{result['asset_combinations']} cursor/decoder combinations, "
              f"{result['planned_step_count']} unexecuted observations, 2 planned sessions")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Streaming scenario plan rejected: {exc}", file=__import__("sys").stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
