"""Analyze a remote archive's HTTP touch footprint at verified host milestones.

The HTTP fixture must use the SAME named monotonic clock domain as its
milestones. This measures response bodies written by the fixture server,
NOT TCP-acknowledged bytes, file_readonly consumption, inflation or PCM.

In-flight or clock-ambiguous requests cannot be silently attributed to the
earlier phase. This module generates no host pass verdict on its own.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

SCHEMA = 1
PHASES = ("index.ready", "decoder.first_pcm", "decoder.seek.first_pcm")
_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
_ALLOWED_STATUS = {200, 206, 302, 416, 429, 503}


class ArchiveTouchError(ValueError):
    pass


def _integer(value: object, name: str, *, min_value: int = 0) -> int:
    if type(value) is not int or value < min_value:
        raise ArchiveTouchError(f"{name} must be an integer >= {min_value}")
    return value


def _union_size(spans: list[tuple[int, int]]) -> int:
    """Half-open byte ranges, merged without materializing large archives."""
    count, end = 0, 0
    for lo, hi in sorted(spans):
        if hi <= lo:
            continue
        if lo >= end:
            count += hi - lo
        elif hi > end:
            count += hi - end
        end = max(end, hi)
    return count


def validate(report: dict) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA:
        raise ArchiveTouchError("Invalid archive-touch schema")
    for key in ("run_id", "attempt_id"):
        if not isinstance(report.get(key), str) or not _ID.fullmatch(report[key]):
            raise ArchiveTouchError(f"Invalid {key}")
    if report.get("clock_domain") != f"guest-perf-ns:{report['run_id']}":
        raise ArchiveTouchError("Explicit same-guest perf_counter_ns clock required")
    size = _integer(report.get("archive_size_bytes"), "archive_size_bytes", min_value=1)
    if size > 1 << 48:
        raise ArchiveTouchError("Unreasonable archive size")
    if not isinstance(report.get("requests"), list) or not isinstance(report.get("milestones"), list):
        raise ArchiveTouchError("Missing request and milestone arrays")
    seen = set()
    for row in report["requests"]:
        if not isinstance(row, dict) or set(row) != {
                "request_id", "start_ns", "end_ns", "status", "response_offset",
                "bytes_written", "response_complete"}:
            raise ArchiveTouchError("Unexpected HTTP request shape")
        rid = _integer(row["request_id"], "request_id")
        if rid in seen:
            raise ArchiveTouchError("Duplicate request_id")
        seen.add(rid)
        start = _integer(row["start_ns"], "start_ns")
        end = _integer(row["end_ns"], "end_ns")
        if end < start:
            raise ArchiveTouchError("Response ended before it started")
        status = _integer(row["status"], "status")
        if status not in _ALLOWED_STATUS:
            raise ArchiveTouchError("Unexpected HTTP status")
        offset = _integer(row["response_offset"], "response_offset")
        written = _integer(row["bytes_written"], "bytes_written")
        if type(row["response_complete"]) is not bool:
            raise ArchiveTouchError("Invalid completion flag")
        if status in (200, 206):
            if (offset >= size or offset + written > size or
                    (status == 200 and offset != 0)):
                raise ArchiveTouchError("HTTP response outside archive bounds")
        elif offset or written:
            raise ArchiveTouchError("Non-content status must not claim payload bytes")
    labels = set()
    for milestone in report["milestones"]:
        if not isinstance(milestone, dict) or set(milestone) != {
                "name", "tick_ns", "uncertainty_ns"}:
            raise ArchiveTouchError("Unexpected milestone shape")
        if milestone["name"] not in PHASES or milestone["name"] in labels:
            raise ArchiveTouchError("Unknown or duplicate milestone")
        labels.add(milestone["name"])
        _integer(milestone["tick_ns"], "tick_ns")
        _integer(milestone["uncertainty_ns"], "uncertainty_ns")


def analyze(doc: dict) -> dict:
    validate(doc)
    size = doc["archive_size_bytes"]
    stages = []
    for milestone in doc["milestones"]:
        tick, uncertainty = milestone["tick_ns"], milestone["uncertainty_ns"]
        completed, inflight, failed, spans = [], [], [], []
        for request in doc["requests"]:
            if request["start_ns"] > tick + uncertainty:
                continue  # The request is definitely after the milestone.
            # Entire response must precede the EARLIEST possible milestone.
            if request["end_ns"] <= tick - uncertainty:
                completed.append(request)
                if request["status"] in (200, 206):
                    spans.append((request["response_offset"],
                                  request["response_offset"] + request["bytes_written"]))
                if request["status"] not in (200, 206):
                    failed.append(request)
                elif not request["response_complete"]:
                    failed.append(request)
            else:
                inflight.append(request)
        transferred = sum(r["bytes_written"] for r in completed if r["status"] in (200, 206))
        unique = _union_size(spans)
        full_fetch = any(r["status"] == 200 and r["response_offset"] == 0 and
                         r["bytes_written"] == size and r["response_complete"] for r in completed)
        stages.append({
            "milestone": milestone["name"],
            "status": "bounded" if not inflight and not failed else "partial",
            "exact_stage_bytes_verified": not inflight and not failed and uncertainty == 0,
            "completed_requests": len(completed),
            "inflight_or_ambiguous_requests": len(inflight),
            "failed_or_incomplete_requests": len(failed),
            "completed_body_bytes": transferred,
            "completed_unique_archive_bytes": unique,
            "completed_unique_fraction": round(unique / size, 8),
            "redundant_completed_bytes": transferred - unique,
            "full_response_detected": full_fetch,
            "full_archive_touched_by_completed_responses": unique == size,
            "note": ("Counts server response body writes completed before the milestone; "
                     "in-flight response bytes are unknown. Never infer decoder bytes or TCP ACKs."),
        })
    return {
        "schema_version": SCHEMA,
        "kind": "archive-touch-observation",
        "qualification": "fixture-http-ledger-only-not-host-playback",
        "status": "observed" if stages else "not_measured",
        "run_id": doc["run_id"], "attempt_id": doc["attempt_id"],
        "archive_size_bytes": size,
        "request_count": len(doc["requests"]),
        "milestones": stages,
        "host_playback_verified": None,
        "reader_implementation": None,
        "decoder_first_pcm_verified": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ArchiveTouchError("Refusing to overwrite prior evidence")
        result = analyze(json.loads(args.input.read_text(encoding="utf-8")))
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Archive touch: {len(result['milestones'])} milestones; "
              f"{result['request_count']} requests; no host claim")
        return 0
    except (ArchiveTouchError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Archive-touch evidence rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
