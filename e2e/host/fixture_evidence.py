"""Fail-closed source evidence gate for the first real foobar2000 ZIP test."""
from __future__ import annotations


def qualify(trace_report: dict, http_events: list[dict], *, require_range: bool = True, require_window: bool = True) -> dict:
    required = {"robot.host.launch.intent",
                "robot.host.window.visible" if require_window else "component.observer.config_ready",
                "robot.play.intent", "component.track.open.begin",
                "component.decoder.first_pcm"}
    seen = set(trace_report.get("event_names", []))
    missing = sorted(required - seen)
    latency = next((x for x in trace_report.get("latencies", [])
                    if x.get("metric") == "play.intent_to_first_pcm"), None)
    observations = latency.get("observations", []) if latency else []
    measured = (
        len(observations) == 1
        and observations[0].get("status") == "measured"
        and type(observations[0].get("duration_ns")) is int
        and observations[0]["duration_ns"] >= 0
    )
    if any(type(row.get("bytes_sent")) is not int or row["bytes_sent"] < 0
           for row in http_events):
        return {"qualified": False, "reason": "invalid_http_counts",
                "first_pcm_duration_ns": None, "http_response_bytes": None}
    received = sum(row["bytes_sent"] for row in http_events if row.get("status") in (200, 206))
    served_range = any(row.get("status") == 206 and row["bytes_sent"] > 0 for row in http_events)
    ok = (not missing and measured and (served_range or not require_range) and received > 0
          and trace_report.get("status") == "recorded")
    return {
        "qualified": bool(ok),
        "reason": (("native_pcm_and_range_observed" if require_range else "native_pcm_and_http_observed") if ok else
                   "missing_native_or_http_evidence"),
        "missing_events": missing,
        "first_pcm_duration_ns": observations[0]["duration_ns"] if measured else None,
        "http_response_bytes": received,
        "http_range_required": require_range,
        "http_range_requests": sum(row.get("status") == 206 for row in http_events),
        "output_pcm_validated": None,
        "null_output_verified": None,
    }
