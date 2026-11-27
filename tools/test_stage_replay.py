"""Portable tests for the local native-response selection contract."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.stage_replay import response_candidate, fingerprint_inputs, response_features, compiler_arguments, windows_system_root, profile_summary, validate_component_response


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

    def test_windows_system_root_case_and_ambiguity(self):
        self.assertEqual(windows_system_root({"SYSTEMROOT":"C:/Windows"}),Path("C:/Windows"))
        self.assertEqual(windows_system_root({"SystemRoot":"C:/Windows"}),Path("C:/Windows"))
        for env in ({},{"SYSTEMROOT":"C:/Windows","SystemRoot":"D:/Windows"}):
            with self.assertRaises(ValueError):
                windows_system_root(env)

    def test_profile_summary_uses_median_and_exact_hashes(self):
        rows=[{"seconds":x,"pe":{"sha256":"a"},"pdb":{"sha256":v}}
              for x,v in [(3.0,"b"),(1.0,"b"),(2.0,"c")]]
        self.assertEqual(profile_summary(rows,("pe","pdb")),
                         {"samples":3,"median_seconds":2.0,"pe_equal":True,"pdb_equal":False})
        with self.assertRaises(ValueError):
            profile_summary([],("pe",))

    def test_component_native_ltcg_response(self):
        validate_component_response('"/DLL" /LTCG /DEBUG:FULL input.obj')
        for response in ('/DLL /DEBUG input.obj', '/LTCG /DEBUG input.obj',
                         '/DLL /LTCG /LTCGOUT:original.iobj input.obj',
                         '/DLL /LTCG /PGD:original.pgd input.obj',
                         '/DLL /LTCG /ILK:original.ilk input.obj'):
            with self.assertRaises(ValueError):
                validate_component_response(response)
