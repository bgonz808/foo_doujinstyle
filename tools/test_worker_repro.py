from collections import Counter
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tools.worker_repro import sequence, command_valid, native_response_safe, summarize, flag_evidence, tracked_evidence, scheduling_evidence, optional_views, diagnostic_error

class WorkerReproTests(unittest.TestCase):
    def test_counterbalanced_profiles(self):
        for replica in ("a","b"):
            self.assertEqual(len(sequence(replica)),12)
            self.assertEqual(set(Counter(sequence(replica)).values()),{2})
        self.assertNotEqual(sequence("a"),sequence("b"))

    def test_profile_flags(self):
        base="/Brepro /Qspectre /MP2 /MD /guard:cf /GS /sdl"
        self.assertTrue(command_valid(base+" /Zi","baseline"))
        mapped=base+r" /experimental:deterministic /pathmap:A=X:\doujin-repro\native"
        self.assertTrue(command_valid(mapped+" /Zi","mapped-zi"))
        self.assertTrue(command_valid(mapped+" /Z7","mapped-z7"))
        self.assertFalse(command_valid(mapped+" /Zi /Z7","mapped-z7"))
        self.assertFalse(command_valid(base+" /Z7","mapped-z7"))

    def test_missing_flag_names_and_case(self):
        flags="/Brepro /Qspectre /MP2 /MD /guard:cf /GS /sdl /Zi"
        self.assertEqual(flag_evidence(flags.replace("/MP2",""),"baseline")["mismatches"],[])
        self.assertFalse(flag_evidence(flags.replace("/MP2",""),"baseline")["observed"]["mp2"])
        self.assertIn("gs",flag_evidence(flags.replace("/GS","/GS-"),"baseline")["mismatches"])
        self.assertIn("edit_continue",flag_evidence(flags.replace("/Zi","/ZI"),"baseline")["mismatches"])
        self.assertIn("cfg",flag_evidence(flags.replace("/guard:cf","/guard:cf-"),"baseline")["mismatches"])

    def test_reject_unsafe_link_response(self):
        self.assertTrue(native_response_safe('/DEBUG:FULL worker.obj library.lib'))
        for flag in ('/LTCG','/LTCG:INCREMENTAL','/MANIFESTFILE:foo','/ILK:foo','/PDBSTRIPPED:foo'):
            self.assertFalse(native_response_safe('/DEBUG:FULL worker.obj '+flag))

    def test_real_tracking_parser_import_and_records(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "CL.command.1.tlog"
            path.write_text("^C:\\private\\one.cpp|C:\\private\\two.cpp\n/Brepro /Qspectre /MP2 /MD /guard:cf /GS /sdl /Zi\n",
                            encoding="utf-16")
            rows = tracked_evidence(path, "baseline")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_count"], 2)
            self.assertEqual(rows[0]["mismatches"], [])
            self.assertNotIn("private", str(rows))

    def test_scheduling_log_is_not_process_proof(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"msbuild.log"
            path.write_text('  "C:\\private\\cl.exe" /MP2 /FS publish_references.cpp\n'
                            '  Task Parameter:AdditionalOptions=/MP4\n'
                            '  cl.exe /MP4 sqlite_amalgamation.c\n',encoding="utf-8")
            result=scheduling_evidence(path)
            self.assertTrue(result["mp2_observed_in_command_log"])
            self.assertFalse(result["process_concurrency_verified"])
            self.assertEqual(len(result["command_observations"]),1)
            self.assertNotIn("private",str(result))

    def test_optional_inspection_does_not_expose_paths(self):
        def too_large(*args):
            raise ValueError("Forensic output exceeds diagnostic limit")
        result=optional_views(too_large)
        self.assertEqual(result["status"],"unavailable")
        self.assertEqual(result["reason"],"Forensic output exceeds diagnostic limit")
        self.assertNotIn("secret",str(diagnostic_error(ValueError("C:/secret/private"))))

    def test_incomplete_not_qualified(self):
        self.assertFalse(summarize([])["mapped-z7"]["complete"])
