"""Offline contract tests for fixture-observed archive access footprints.

These tests deliberately do not claim any real foobar2000 or RAR/7z playback.
"""
from __future__ import annotations

from copy import deepcopy
import time
import unittest

from e2e.host.archive_touch import ArchiveTouchError, _union_size, analyze
from tools.media_matrix import fixture_server, http_get


RUN = "1234567890abcdef1234567890abcdef"
ATTEMPT = "rar-store-firstpcm-0001"
CLOCK = f"guest-perf-ns:{RUN}"


def ledger(size: int, requests: list[dict], milestones: list[dict]) -> dict:
    return {"schema_version": 1, "run_id": RUN, "attempt_id": ATTEMPT,
            "archive_size_bytes": size, "clock_domain": CLOCK,
            "requests": requests, "milestones": milestones}


def milestone(name: str, stamp: int, uncertainty: int = 0) -> dict:
    return {"name": name, "tick_ns": stamp, "uncertainty_ns": uncertainty}


def request(i: int, first: int, count: int, start: int, end: int,
            *, status: int = 206, complete: bool = True) -> dict:
    return {"request_id": i, "start_ns": start, "end_ns": end,
            "status": status, "response_offset": first,
            "bytes_written": count, "response_complete": complete}


class ArchiveTouchContracts(unittest.TestCase):
    def test_union_deduplicates_overlapping_ranges(self):
        self.assertEqual(_union_size([(0, 100), (50, 140), (200, 225)]), 165)
        self.assertEqual(_union_size([(10, 20), (10, 20)]), 10)

    def test_sparse_ranges_vs_redundant_and_full_transfer(self):
        doc = ledger(10_000, [
            request(0, 9000, 500, 100, 200),
            request(1, 9200, 500, 300, 400),
            request(2, 0, 10_000, 600, 800, status=200)
        ], [milestone("index.ready", 250),
            milestone("decoder.first_pcm", 500),
            milestone("decoder.seek.first_pcm", 900)])
        stages = analyze(doc)["milestones"]
        self.assertEqual(stages[0]["completed_body_bytes"], 500)
        self.assertEqual(stages[0]["completed_unique_archive_bytes"], 500)
        self.assertFalse(stages[0]["full_archive_touched_by_completed_responses"])
        self.assertEqual(stages[1]["completed_body_bytes"], 1000)
        self.assertEqual(stages[1]["completed_unique_archive_bytes"], 700)
        self.assertEqual(stages[1]["redundant_completed_bytes"], 300)
        self.assertEqual(stages[2]["completed_body_bytes"], 11_000)
        self.assertEqual(stages[2]["completed_unique_archive_bytes"], 10_000)
        self.assertTrue(stages[2]["full_response_detected"])
        self.assertEqual(analyze(doc)["qualification"], "fixture-http-ledger-only-not-host-playback")
        self.assertIsNone(analyze(doc)["host_playback_verified"])

    def test_pending_request_never_becomes_precise_touch(self):
        doc = ledger(4096, [request(0, 0, 4096, 100, 400, status=200)],
                     [milestone("decoder.first_pcm", 200, 10)])
        stage = analyze(doc)["milestones"][0]
        self.assertEqual(stage["status"], "partial")
        self.assertEqual(stage["completed_body_bytes"], 0)
        self.assertEqual(stage["inflight_or_ambiguous_requests"], 1)
        self.assertFalse(stage["exact_stage_bytes_verified"])

    def test_error_or_partial_response_does_not_claim_host_success(self):
        doc = ledger(4096, [request(0, 0, 1024, 100, 200, status=206, complete=False)],
                     [milestone("index.ready", 300)])
        stage = analyze(doc)["milestones"][0]
        self.assertEqual(stage["status"], "partial")
        self.assertEqual(stage["failed_or_incomplete_requests"], 1)
        self.assertFalse(stage["full_archive_touched_by_completed_responses"])

    def test_reject_invalid_clock_bounds_and_response(self):
        valid = ledger(4096, [request(0, 0, 1024, 10, 20)],
                       [milestone("index.ready", 30)])
        cases = []
        doc = deepcopy(valid); doc["clock_domain"] = "win-qpc:" + RUN; cases.append(doc)
        doc = deepcopy(valid); doc["requests"][0]["bytes_written"] = 9999; cases.append(doc)
        doc = deepcopy(valid); doc["requests"][0]["end_ns"] = 9; cases.append(doc)
        doc = deepcopy(valid); doc["requests"][0]["status"] = 404; cases.append(doc)
        doc = deepcopy(valid); doc["requests"].append(doc["requests"][0]); cases.append(doc)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ArchiveTouchError):
                analyze(document)

    def test_real_loopback_206_and_200_records_but_no_host(self):
        asset = bytes(range(256)) * 16
        with fixture_server({"rar-test": asset}) as (base, events):
            got_status, _, payload = http_get(base, "rar-test", headers={"Range": "bytes=4000-4095"})
            self.assertEqual((got_status, payload), (206, asset[4000:4096]))
            self.assertTrue(events.wait_count(1))
            boundary1 = time.perf_counter_ns()
            got_status, _, payload = http_get(base, "rar-test", headers={"Range": "bytes=0-127"})
            self.assertEqual((got_status, payload), (206, asset[:128]))
            self.assertTrue(events.wait_count(2))
            boundary2 = time.perf_counter_ns()
            got_status, _, payload = http_get(base, "rar-test")
            self.assertEqual((got_status, payload), (200, asset))
            self.assertTrue(events.wait_count(3))
            boundary3 = time.perf_counter_ns()
            requests = [{
                **{k: row[k] for k in ("request_id", "start_ns", "end_ns",
                                      "status", "response_offset", "response_complete")},
                "bytes_written": row["bytes_sent"],
            } for row in events]
            doc = ledger(len(asset), requests, [
                milestone("index.ready", boundary1),
                milestone("decoder.first_pcm", boundary2),
                milestone("decoder.seek.first_pcm", boundary3)
            ])
            observations = analyze(doc)["milestones"]
            self.assertEqual([x["completed_body_bytes"] for x in observations],
                             [96, 224, 4320])
            self.assertEqual([x["completed_unique_archive_bytes"] for x in observations],
                             [96, 224, 4096])
            self.assertEqual([x["status"] for x in observations], ["bounded"] * 3)
            self.assertFalse(observations[1]["full_response_detected"])
            self.assertTrue(observations[2]["full_response_detected"])


if __name__ == "__main__":
    unittest.main()
