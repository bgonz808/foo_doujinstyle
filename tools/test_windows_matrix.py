"""Synthetic exact-run assembly contracts, not a Windows runtime claim."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import os
from unittest.mock import patch
from zipfile import ZipFile
from tools.windows_matrix import (ARCHES, BINARIES, MEMBERS, LICENSES, ANALYZER_HASHES,
                                  ANALYSIS_UNITS, context, seal, aggregate)
from tools.binskim_evidence import write_json, digest
from tools.attestation_gate import validate
from tools.verify_pe_targets import EXPECTED


class WindowsMatrixTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.identity = {"dirty": False, "git_commit": "a"*40, "source_sha256": "b"*64,
                         "base_version": "0.0.239", "version": "0.0.239-dev.1+g" + "a"*12}
        (self.source / "VERSION").write_text("0.0.239")
        write_json(self.source / "security/bin-policy.json", {"schema_version": 1, "default_enforcement": "blocking",
                   "rules": {"BA2024": {"enforcement": "blocking"}}})
        write_json(self.source / "tools/binskim-baseline.json", {"schema_version": 1, "accepted_findings": []})
        for path in LICENSES.values():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test license")
        self.ctx = context(self.source, "a"*40, "42", "1", self.identity)
        self.lanes = self.root / "lanes"
        self.lanes.mkdir()
        runtime = self.root / "runtime.json"
        write_json(runtime, {"schema_version": 1, "tools_version": "14.44.35207", "architectures": {
            arch: {"spectre_dir_present": True, "selected_spectre_lib_arch": "arm64" if arch == "arm64ec" else arch,
                   "cl_sha256": "c"*64, "link_sha256": "d"*64,
                   "runtime_library_sha256": {n: "e"*64 for n in ("msvcrt.lib", "vcruntime.lib", "msvcprt.lib")}}
            for arch in ("x86", "x64", "arm64ec")}})
        analysis = self.root / "analysis.json"
        write_json(analysis, {"schema_version": 1, "kind": "windows-frontend-static-analysis", "source_commit": "a"*40,
                   "platform": "x64", "status": "passed", "tool_sha256": ANALYZER_HASHES,
                   "projects": [{"project": p, "platform": "x64", "target": "x86_64-pc-windows-msvc", "status": "passed",
                                 "count": len(u), "units": sorted(u), "tracking_log_sha256": ["f"*64]}
                                for p,u in ANALYSIS_UNITS.items()]})
        for arch in ARCHES:
            build = self.root / ("build-" + arch)
            dest = build / arch
            dest.mkdir(parents=True)
            pe = bytearray(512)
            pe[:2] = b"MZ"
            pe[60:64] = (64).to_bytes(4, "little")
            pe[64:68] = b"PE" + bytes(2)
            pe[68:70] = EXPECTED[arch].to_bytes(2, "little")
            worker = bytes(pe) + arch.encode()
            dll = bytes(pe) + worker
            (dest / BINARIES[0]).write_bytes(dll)
            (dest / BINARIES[1]).write_bytes(worker)
            with ZipFile(build / "lane.fb2k-component", "w") as z:
                z.writestr(MEMBERS[arch], dll)
            reports = self.root / ("reports-" + arch)
            for name in BINARIES:
                write_json(reports / f"{arch}-{name}.sarif", {"version": "2.1.0", "runs": [{
                    "tool": {"driver": {"name": "BinSkim"}}, "invocations": [{"executionSuccessful": True}],
                    "artifacts": [{"location": {"uri": "file:///C:/private/" + arch + "/" + name}}],
                    "results": [{"ruleId": "BA2024", "kind": "pass", "level": "none"}]}]})
            seal(self.source, build, reports, runtime, analysis, self.lanes / arch, arch, self.ctx)

    def assemble(self):
        return aggregate(self.source, self.lanes, self.root / "build", self.root / "evidence", self.ctx)

    def test_aggregate_and_attestation(self):
        package = self.assemble()
        ev = self.root / "evidence"
        write_json(ev / "source-inputs.spdx.json", {"spdxVersion": "SPDX-2.3",
                   "documentNamespace": "https://github.com/bgonz808/foo_doujinstyle/spdx/source/" + "a"*40,
                   "documentComment": "PARTIAL", "packages": [{}, {}, {}, {}]})
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "1"}):
            result = validate(self.source, package.parent, ev, "a"*40, "42", identity=self.identity)
        self.assertEqual(result["scans"], 6)
        self.assertEqual(result["package_sha256"], digest(package))
        manifest = json.loads((ev / "binary-manifest.json").read_text())
        for arch in ARCHES:
            local = json.loads((self.lanes / arch / "evidence/binary-manifest.json").read_text())
            for name in BINARIES:
                key = arch + "/" + name
                self.assertEqual(manifest["targets"][key]["sarif"]["raw_sha256"], local["targets"][key]["sarif"]["raw_sha256"])

    def test_canonical_json_bytes(self):
        path = self.root / "canonical.json"
        write_json(path, {"b": 1, "a": 2})
        self.assertEqual(path.read_bytes(), b'{\n  "a": 2,\n  "b": 1\n}\n')
        self.assertNotIn(b"\r", path.read_bytes())

    def test_wrong_attempt(self):
        path = self.lanes / "x64/lane.json"
        data = json.loads(path.read_text())
        data["run_attempt"] = "2"
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "source/run/attempt"):
            self.assemble()

    def test_tampered_pe(self):
        (self.lanes / "x64/foo_doujinstyle.dll").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.assemble()

    def test_missing_lane(self):
        (self.lanes / "x64").rename(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "Missing or foreign"):
            self.assemble()

    def test_unexpected_file(self):
        (self.lanes / "x64/private.pdb").write_bytes(b"private")
        with self.assertRaisesRegex(ValueError, "file set mismatch"):
            self.assemble()

    def test_wrong_source(self):
        path = self.lanes / "Win32/lane.json"
        data = json.loads(path.read_text())
        data["source_sha256"] = "c"*64
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "source/run/attempt"):
            self.assemble()

    def test_tampered_scan(self):
        path = self.lanes / "ARM64EC/evidence/sarif/ARM64EC-foo_doujinstyle.dll.sarif"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.assemble()


if __name__ == "__main__":
    unittest.main()
