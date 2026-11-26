import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from e2e.host.etw_session import EtwSession


class EtwLifecycle(unittest.TestCase):
    def session(self, replies):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        invoke = Mock(side_effect=replies)
        return EtwSession("DoujinE2E-012345abcdef", root/"trace.etl", root/"session.json", invoke), invoke

    def reply(self, rc=0, out=b"", err=b""):
        return subprocess.CompletedProcess([], rc, out, err)

    def test_normal(self):
        s, invoke = self.session([self.reply(), self.reply()])
        self.assertTrue(s.start())
        self.assertTrue(s.stop())
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual([x["outcome"] for x in s.history], ["success", "success"])

    def test_stop_failure_cleanup_never_qualifies(self):
        s, invoke = self.session([self.reply(), self.reply(1, err=b"Access is denied. 0x80070005 private-canary"),
                                  self.reply(), self.reply()])
        self.assertTrue(s.start())
        self.assertFalse(s.stop())
        doc = json.loads(s.evidence.read_text())
        self.assertEqual([x["action"] for x in doc["commands"]],
                         ["start", "stop", "query_after_stop_failure", "cleanup_after_stop_failure"])
        self.assertEqual(doc["commands"][1]["stderr"]["hex_codes"], ["0x80070005"])
        self.assertEqual(doc["commands"][1]["stderr"]["known_messages"], ["access is denied"])
        self.assertNotIn("private-canary", s.evidence.read_text())

    def test_start_timeout_and_query_failure(self):
        s, invoke = self.session([subprocess.TimeoutExpired(["logman"], 25, output=b"private-path"),
                                  self.reply(1, err=b"The data collector set was not found.")])
        self.assertFalse(s.start())
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(s.history[0]["outcome"], "timeout")
        self.assertNotIn("private-path", s.evidence.read_text())

    def test_stop_timeout_keeps_failure(self):
        s, _ = self.session([self.reply(), subprocess.TimeoutExpired(["logman"], 25), self.reply(1)])
        self.assertTrue(s.start())
        self.assertFalse(s.stop())
        self.assertEqual(s.history[1]["outcome"], "timeout")
        self.assertEqual(len(s.history), 3)
