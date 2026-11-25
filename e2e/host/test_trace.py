"""Offline contract tests for QPC/monotonic trace correlation and fail-closed evidence."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from e2e.host.trace import (
    TraceError, TraceWriter, clock_sample, compare_pair, load_events, summarize,
)


class TraceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_id = "0123456789abcdef0123456789abcdef"

    def writer(self, name="robot", source="robot", attempt="playback-0001"):
        return TraceWriter(self.root / (name + ".jsonl"), run_id=self.run_id,
                           scenario_id="wav-direct", attempt_id=attempt, source=source)

    def test_playback_measured_with_shared_clock(self):
        with self.writer() as robot, self.writer("component", "component") as component:
            beginning = clock_sample(self.run_id)
            robot.mark_at("robot.play.intent", beginning)
            component.mark_at("component.decoder.first_pcm",
                              (beginning[0], beginning[1] + beginning[2] // 10, beginning[2]),
                              sample_count=480, sample_rate=48000, channels=2)
        records = load_events(self.root)
        result = compare_pair(records, "robot.play.intent",
                              "component.decoder.first_pcm", "intent_to_pcm")
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["observations"][0]["duration_ns"], 100_000_000)
        report = summarize(self.root)
        self.assertEqual(report["dll_line_coverage"], None)
        self.assertIn("robot.play.intent", report["event_names"])

    def test_missing_milestones_not_zero(self):
        with self.writer() as robot:
            robot.mark("robot.play.intent")
        result = compare_pair(load_events(self.root), "robot.play.intent",
                              "output.pcm.first", "intent_to_output")
        self.assertEqual(result["status"], "not_measured")
        self.assertIsNone(result["observations"][0]["duration_ns"])

    def test_clock_domain_mismatch_not_mixed(self):
        with self.writer() as robot:
            robot.mark("robot.play.intent")
        with self.writer("component", "component") as native:
            native.mark("component.decoder.first_pcm")
        p = self.root / "component.jsonl"
        events = [json.loads(line) for line in p.read_text().splitlines()]
        for event in events:
            event["clock"]["domain"] = f"process-monotonic:{self.run_id}:987654"
        p.write_text("".join(json.dumps(event) + "\n" for event in events))
        result = compare_pair(load_events(self.root), "robot.play.intent",
                              "component.decoder.first_pcm", "intent_to_pcm")
        self.assertEqual(result["observations"][0]["reason"], "incomparable_clock_domain")

    def test_schema_rejects_secrets_and_filenames(self):
        with self.writer() as robot:
            with self.assertRaises(TraceError):
                robot.mark("robot.play.intent", raw_url="https://example.invalid/secret")
            with self.assertRaises(TraceError):
                robot.mark("component.decoder.first_pcm", sample_count=-1)
            with self.assertRaises(TraceError):
                robot.mark("unapproved.event")

    def test_exclusive_file_prevents_merge_or_overwrite(self):
        with self.writer():
            with self.assertRaises(FileExistsError):
                self.writer()

    def test_attempt_isolation(self):
        with self.writer() as first, self.writer("other", attempt="playback-0002") as second:
            first.mark("robot.play.intent")
            second.mark("robot.play.intent")
        result = compare_pair(load_events(self.root), "robot.play.intent",
                              "component.decoder.first_pcm", "intent_to_pcm")
        self.assertEqual(len(result["observations"]), 2)
        self.assertTrue(all(x["status"] == "not_measured" for x in result["observations"]))

    def test_malformed_sequence_fails_closed(self):
        with self.writer():
            pass
        p = self.root / "robot.jsonl"
        document = json.loads(p.read_text().splitlines()[0])
        document["seq"] = 12
        p.write_text(json.dumps(document) + "\n")
        with self.assertRaises(TraceError):
            load_events(self.root)

    def test_preceding_end_is_invalid(self):
        with self.writer() as robot, self.writer("component", "component") as component:
            origin = clock_sample(self.run_id)
            robot.mark_at("robot.play.intent", (origin[0], origin[1] + origin[2], origin[2]))
            component.mark_at("component.decoder.first_pcm", origin)
        result = compare_pair(load_events(self.root), "robot.play.intent",
                              "component.decoder.first_pcm", "intent_to_pcm")
        self.assertEqual(result["observations"][0]["status"], "invalid")
        self.assertIsNone(result["observations"][0]["duration_ns"])


if __name__ == "__main__":
    unittest.main()

class RobotLibraryLifecycle(unittest.TestCase):
    def test_discovery_instances_do_not_open_trace(self):
        from unittest.mock import patch
        from e2e.host.trace_keywords import TraceKeywords
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"robot.jsonl"
            env={"DOUJIN_E2E_RUN_ID":"a"*32,"DOUJIN_E2E_TRACE_PATH":str(p),
                 "DOUJIN_E2E_SCENARIO_ID":"fixtureplay","DOUJIN_E2E_ATTEMPT_ID":"fixtureplay-0001"}
            with patch.dict("os.environ",env):
                discovery=TraceKeywords()
                actual=TraceKeywords()
                discovery.close_trace()
                self.assertFalse(p.exists())
                actual.mark_trace_event("robot.play.dispatch.return")
                other=TraceKeywords()
                with self.assertRaises(FileExistsError):
                    other.mark_trace_event("robot.play.dispatch.return")
                actual.close_trace()
                actual.close_trace()
                self.assertEqual(len(load_events(Path(d))),2)
                with self.assertRaises(TraceError):
                    actual.mark_trace_event("robot.play.dispatch.return")

class PlaybackDiagnosticEvents(unittest.TestCase):
    def test_diagnostics_do_not_qualify_pcm(self):
        from e2e.host.paired_evidence import qualify_common
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"component.jsonl"
            with TraceWriter(path,run_id="a"*32,scenario_id="fixtureplay",attempt_id="fixtureplay-0001",source="component") as writer:
                writer.mark("component.observer.playlist_ready",sample_count=1)
                writer.mark("component.observer.play_starting")
                writer.mark("component.observer.chunk_without_track",sample_count=480)
            self.assertFalse(qualify_common(load_events(Path(d)),local=True)["qualified"])
