"""Static contracts for the isolated x64 ETW host E2E test component.

These tests assert source and workflow separation, not actual Windows-host playback.
"""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}


class HostTraceBuildContracts(unittest.TestCase):
    def test_telemetry_flag_is_explicitly_opt_in(self):
        root = ET.parse(ROOT / "native/foo_doujinstyle.vcxproj").getroot()
        props = root.findall(".//m:PropertyGroup/m:DoujinE2ETraceDefines", NS)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].text, "DOUJIN_E2E_TRACE=1;")
        self.assertIn("'$(DoujinE2ETrace)' == 'true'", props[0].attrib["Condition"])
        compile_items = root.findall(".//m:ClCompile", NS)
        for name in ("host_trace_etw.cpp", "host_pcm_observer.cpp"):
            source = [item for item in compile_items if item.attrib.get("Include") == name]
            self.assertEqual(len(source), 1)
            self.assertEqual(source[0].attrib.get("Condition"), "'$(DoujinE2ETrace)' == 'true'")
            self.assertFalse(source[0].findall("m:ExcludedFromBuild", NS))
        opts = root.findall(".//m:ItemDefinitionGroup/m:ClCompile/m:PreprocessorDefinitions", NS)
        self.assertTrue(any("$(DoujinE2ETraceDefines)" in (item.text or "") for item in opts))

    def test_test_only_package_requires_explicit_x64_trace_switch(self):
        build = (ROOT / "native/build.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$E2ETrace", build)
        self.assertIn("$Platform.Count -ne 1", build)
        self.assertIn("$Platform[0] -ne 'x64'", build)
        self.assertIn("'/p:DoujinE2ETrace=true'", build)
        self.assertIn("'-e2e-trace.fb2k-component'", build)
        self.assertIn("if ($E2ETrace)", build)

    def test_production_binary_gate_has_no_test_trace_build(self):
        release = (ROOT / ".github/workflows/windows-build.yml").read_text(encoding="utf-8")
        self.assertNotIn("-E2ETrace", release)
        self.assertNotIn("/p:DoujinE2ETrace=true", release)
        self.assertIn("run: ./tools/run_binskim.ps1", release)
        self.assertIn("name: ci-provenance", release)

    def test_test_workflow_uses_dedicated_read_only_windows_runner(self):
        script = (ROOT / ".github/workflows/host-etw-build.yml").read_text(encoding="utf-8")
        for token in ("host-trace-build:", "runs-on: windows-2022",
                      "contents: read", "persist-credentials: false",
                      "d4c55077336fae81bf8df0259b5b2748fa45ea84132c656ead93eb123cbcdc26",
                      "tools/install_msvc_spectre.ps1", "14.44.35207",
                      "-Platform x64", "-E2ETrace",
                      "foo_doujinstyle-x64-e2e-unqualified",
                      "tools/verify_pe_targets.py --build-dir native/build --require x64"):
            with self.subTest(token=token):
                self.assertIn(token, script)
        self.assertIn("  push:\n    branches: [main]", script)
        self.assertIn("  workflow_dispatch:", script)
        self.assertIn("  pull_request:", script)
        self.assertNotIn("id-token: write", script)
        self.assertNotIn("attestations: write", script)
        self.assertNotIn("pull_request_target:", script)
        self.assertNotIn("workflow_run:", script)
        self.assertNotIn("release:", script)

    def test_native_provider_event_contract(self):
        source = (ROOT / "native/host_trace_etw.hpp").read_text(encoding="utf-8")
        impl = (ROOT / "native/host_trace_etw.cpp").read_text(encoding="utf-8")
        self.assertIn("#if defined(DOUJIN_E2E_TRACE)", source)
        self.assertIn("#else", source)
        self.assertIn("namespace doujin::host_trace", source)
        self.assertIn("TraceLoggingWrite(", source)
        self.assertIn("TraceLoggingRegister(", source)
        self.assertIn("QueryPerformanceCounter(", source)
        self.assertIn("DOUJIN_E2E_ATTEMPT_ID", source)
        self.assertIn("TRACELOGGING_DEFINE_PROVIDER", impl)
        component = (ROOT / "native/component.cpp").read_text(encoding="utf-8")
        search = (ROOT / "native/search.cpp").read_text(encoding="utf-8")
        for event in ("component.track.open.begin", "component.track.open.end",
                      "component.decoder.first_pcm", "component.decoder.seek.first_pcm",
                      "component.decoder.seek.begin", "component.archive.member.ready"):
            self.assertIn('host_trace::emit("' + event + '"', component)
        for event in ("component.ui.search.selection.changed", "component.ui.search.worker.started",
                      "component.ui.search.track.projected", "component.ui.search.drag.drop_accepted"):
            self.assertIn(event, search)
        self.assertEqual(component.count('#include "host_trace_etw.hpp"'), 1)
        self.assertEqual(search.count('#include "host_trace_etw.hpp"'), 1)


if __name__ == "__main__":
    unittest.main()
