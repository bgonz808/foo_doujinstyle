"""Portable tests for the local native-response selection contract."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.stage_replay import response_candidate, fingerprint_inputs, response_features, compiler_arguments


class StageReplayTests(unittest.TestCase):
    def test_response_selection_and_ambiguity(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            rsp=root/"link.rsp"
            rsp.write_text('/OUT:worker.exe /DEBUG:FULL "input.obj"\n')
            (root/"other.rsp").write_text('/OUT:worker.lib input.obj\n')
            self.assertEqual(response_candidate(root),rsp)
            (root/"other.rsp").write_text('/OUT:other.exe /DEBUG:FULL input.obj\n')
            with self.assertRaises(ValueError):
                response_candidate(root)

    def test_reject_recursive_capture_and_nested_response(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            for extra in ("/LINKREPRO:other","@second.rsp"):
                (root/"link.rsp").write_text('/OUT:worker.exe /DEBUG:FULL input.obj '+extra)
                with self.assertRaises(ValueError):
                    response_candidate(root)

    def test_frozen_input_change_detected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            obj=root/"worker.obj"
            obj.write_bytes(b"original")
            a=fingerprint_inputs({"build":root})
            obj.write_bytes(b"modified")
            self.assertNotEqual(a,fingerprint_inputs({"build":root}))

    def test_quoted_native_response_switches(self):
        self.assertEqual(response_features('"/DEBUG:FULL" "/OUT:a.exe" "x.obj"'),
                         {"out":True,"object":True,"debug":True})
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"link.rsp").write_text('/DEBUG:FULL "input.obj"')
            self.assertEqual(response_candidate(root),root/"link.rsp")

    def test_compiler_output_redirect_preserves_flags(self):
        flags=["/c","/Z7","/Qspectre","/Brepro","/O2","/Fo","old.obj","/Fdold.pdb","sqlite.c"]
        self.assertEqual(compiler_arguments(flags,"sqlite.c","C:/work"),
                         ["/c","/Z7","/Qspectre","/Brepro","/O2","sqlite.c"])
        with self.assertRaises(ValueError):
            compiler_arguments(flags+["@nested.rsp"],"sqlite.c","C:/work")
