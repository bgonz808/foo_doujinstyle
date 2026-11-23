"""Versioned, source-attributed host traces and measured (never inferred) latencies.

Only events from the same declared clock domain can be compared. Windows QPC
ticks are directly comparable across processes on the same guest; other OSes
use process-local monotonic domains. No URL, filename, or track metadata fields.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import sys
import time

SCHEMA = 1
RUN_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
EVENTS = frozenset({
    "clock.sync",
    "robot.host.launch.intent", "robot.host.window.visible",
    "robot.play.intent", "robot.play.dispatch.return",
    "robot.seek.intent", "robot.stop.intent",
    "host.play.accepted", "host.seek.accepted", "host.stop.accepted",
    "component.track.open.begin", "component.track.open.end",
    "component.archive.member.ready", "component.http.first_byte",
    "component.decoder.initialize.begin", "component.decoder.initialize.end",
    "component.decoder.first_pcm", "component.decoder.seek.first_pcm",
    "component.decoder.eof", "component.cancel.observed",
    "worker.request.begin", "worker.request.end", "worker.cancel.observed",
    "output.null.progress", "output.pcm.first", "output.file.finalized",
    "output.wasapi.rendered",
})
SOURCES = frozenset({"robot", "host", "component", "worker", "network", "output"})
METRICS = frozenset({
    "byte_count", "request_count", "sample_count", "sample_rate",
    "channels", "target_sample", "actual_sample", "frames", "seek_index_restores",
    "cancellation_pending", "clock_uncertainty_ns", "utc_unix_ns",
})
PAIRS = (
    ("robot.play.intent", "host.play.accepted", "play.dispatch_to_accept"),
    ("robot.play.intent", "component.decoder.first_pcm", "play.intent_to_first_pcm"),
    ("host.play.accepted", "component.decoder.first_pcm", "play.accepted_to_first_pcm"),
    ("robot.play.intent", "output.pcm.first", "play.intent_to_output_pcm"),
    ("robot.seek.intent", "component.decoder.seek.first_pcm", "seek.intent_to_first_pcm"),
    ("robot.stop.intent", "component.cancel.observed", "stop.intent_to_component_cancel"),
    ("component.decoder.first_pcm", "output.pcm.first", "decoder_to_output_pcm"),
)


class TraceError(ValueError):
    pass


def clock_sample(run_id: str) -> tuple[str, int, int]:
    if sys.platform == "win32":
        tick = ctypes.c_longlong()
        frequency = ctypes.c_longlong()
        kernel = ctypes.windll.kernel32
        if not kernel.QueryPerformanceFrequency(ctypes.byref(frequency)) or frequency.value <= 0:
            raise TraceError("QPC frequency unavailable")
        if not kernel.QueryPerformanceCounter(ctypes.byref(tick)):
            raise TraceError("QPC unavailable")
        return f"win-qpc:{run_id}", tick.value, frequency.value
    return f"process-monotonic:{run_id}:{os.getpid()}", time.monotonic_ns(), 1_000_000_000


def _validate_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise TraceError(f"Invalid {field}: use 8-64 ASCII identifier characters")


def _validate_event(event: dict) -> None:
    if event.get("schema_version") != SCHEMA:
        raise TraceError("Unsupported trace schema")
    for key in ("run_id", "scenario_id", "attempt_id"):
        _validate_id(event.get(key), key)
    if event.get("source") not in SOURCES or event.get("event") not in EVENTS:
        raise TraceError("Unrecognized source or event")
    if not isinstance(event.get("pid"), int) or event["pid"] < 0:
        raise TraceError("Invalid process id")
    if not isinstance(event.get("seq"), int) or event["seq"] < 0:
        raise TraceError("Invalid sequence")
    clock = event.get("clock", {})
    if (not isinstance(clock, dict) or
            not isinstance(clock.get("domain"), str) or
            not isinstance(clock.get("tick"), int) or clock["tick"] < 0 or
            not isinstance(clock.get("frequency_hz"), int) or clock["frequency_hz"] <= 0):
        raise TraceError("Invalid clock")
    if not clock["domain"].endswith(event["run_id"]) and not clock["domain"].startswith(
            f"process-monotonic:{event['run_id']}:"):
        raise TraceError("Clock domain does not belong to this run")
    metrics = event.get("metrics", {})
    if not isinstance(metrics, dict) or any(
            k not in METRICS or type(v) is not int or v < 0 for k, v in metrics.items()):
        raise TraceError("Metrics must be nonnegative allowlisted integers")
    if set(event) != {"schema_version", "run_id", "scenario_id", "attempt_id",
                      "source", "pid", "seq", "event", "clock", "metrics"}:
        raise TraceError("Unexpected event fields")


class TraceWriter:
    def __init__(self, path: Path, *, run_id: str, scenario_id: str,
                 attempt_id: str, source: str):
        for key, value in (("run_id", run_id), ("scenario_id", scenario_id),
                           ("attempt_id", attempt_id)):
            _validate_id(value, key)
        if source not in SOURCES:
            raise TraceError("Invalid trace source")
        self.path = Path(path)
        self.run_id, self.scenario_id, self.attempt_id, self.source = (
            run_id, scenario_id, attempt_id, source)
        self.seq = 0
        # Existence is exclusive to prevent mixing test runs or evidence replacement.
        self.file = self.path.open("x", encoding="utf-8", newline="\n")
        self.pid = os.getpid()
        wall_before = time.time_ns()
        domain, tick, freq = clock_sample(self.run_id)
        wall_after = time.time_ns()
        self.domain, self.frequency = domain, freq
        self._emit("clock.sync", tick, {"utc_unix_ns": (wall_before + wall_after) // 2,
                                       "clock_uncertainty_ns": wall_after - wall_before})

    def _emit(self, name: str, tick: int, metrics: dict) -> dict:
        event = {
            "schema_version": SCHEMA, "run_id": self.run_id,
            "scenario_id": self.scenario_id, "attempt_id": self.attempt_id,
            "source": self.source, "pid": self.pid, "seq": self.seq, "event": name,
            "clock": {"domain": self.domain, "tick": tick, "frequency_hz": self.frequency},
            "metrics": metrics,
        }
        _validate_event(event)
        self.file.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
        self.file.flush()
        self.seq += 1
        return event

    def mark_at(self, name: str, sample: tuple[str, int, int], **metrics: int) -> dict:
        # Automation triggers capture before action dispatch, and write AFTER
        # the operation to avoid contaminating the measured intent latency.
        domain, tick, frequency = sample
        if (domain, frequency) != (self.domain, self.frequency):
            raise TraceError("Clock changed during trace")
        return self._emit(name, tick, metrics)

    def mark(self, name: str, **metrics: int) -> dict:
        return self.mark_at(name, clock_sample(self.run_id), **metrics)

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def load_events(folder: Path) -> list[dict]:
    records = []
    for path in sorted(Path(folder).glob("*.jsonl")):
        prior = -1
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                _validate_event(item)
                if item["seq"] != prior + 1:
                    raise TraceError(f"Non-contiguous event sequence: {path.name}")
                prior = item["seq"]
                records.append(item)
    if records and len({item["run_id"] for item in records}) != 1:
        raise TraceError("Mixed run IDs in trace folder")
    return records


def compare_pair(records: list[dict], begin: str, end: str, label: str) -> dict:
    # Require a unique logical attempt to avoid pairing unrelated playback,
    # analyzer and next-track reads.
    attempts = sorted({(r["scenario_id"], r["attempt_id"]) for r in records
                       if r["event"] == begin})
    observations = []
    for scenario, attempt in attempts:
        selected = [r for r in records
                    if (r["scenario_id"], r["attempt_id"]) == (scenario, attempt)]
        starts = [r for r in selected if r["event"] == begin]
        ends = [r for r in selected if r["event"] == end]
        outcome = {"scenario_id": scenario, "attempt_id": attempt,
                   "status": "not_measured", "duration_ns": None}
        if len(starts) != 1 or not ends:
            outcome["reason"] = "missing_or_ambiguous_milestone"
        elif any(e["clock"]["domain"] != starts[0]["clock"]["domain"] or
                 e["clock"]["frequency_hz"] != starts[0]["clock"]["frequency_hz"]
                 for e in ends):
            outcome["reason"] = "incomparable_clock_domain"
        else:
            future = [e for e in ends if e["clock"]["tick"] >= starts[0]["clock"]["tick"]]
            if not future:
                outcome["status"], outcome["reason"] = "invalid", "end_precedes_start"
            else:
                nearest = min(future, key=lambda e: e["clock"]["tick"])
                delta = nearest["clock"]["tick"] - starts[0]["clock"]["tick"]
                outcome["duration_ns"] = delta * 1_000_000_000 // starts[0]["clock"]["frequency_hz"]
                outcome["status"] = "measured"
        observations.append(outcome)
    return {"metric": label, "start": begin, "end": end, "observations": observations,
            "status": ("not_measured" if not attempts or all(x["status"] == "not_measured" for x in observations) else
                       ("measured" if all(x["status"] == "measured" for x in observations) else "partial"))}


def summarize(folder: Path) -> dict:
    events = load_events(folder)
    return {"schema_version": SCHEMA, "kind": "host-trace-analysis",
            "status": "not_run" if not events else "recorded",
            "event_count": len(events),
            "event_names": sorted({x["event"] for x in events}),
            "latencies": [compare_pair(events, *pair) for pair in PAIRS],
            "dll_line_coverage": None,
            "worker_line_coverage": None,
            "audio_output_verified": None}
