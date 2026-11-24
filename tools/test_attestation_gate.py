"""Attestation evidence is fail-closed against tampering and source substitution."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
from tools.attestation_gate import validate, checksum
from tools.binskim_evidence import collect
from tools.binskim_policy import ARCHES, BINARIES

class TestAttestationReadiness(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "repo"
        (self.source / "security").mkdir(parents=True)
        (self.source / "tools").mkdir()
        self.version = "0.0.160"
        (self.source / "VERSION").write_text(self.version)
        self.baseline = self.source / "tools/binskim-baseline.json"
        self.baseline.write_text('{"schema_version":1,"accepted_findings":[]}')
        self.policy = self.source / "security/bin-policy.json"
        self.policy.write_text('{"schema_version":1,"default_enforcement":"blocking","rules":{"BA2024":{"enforcement":"blocking"}}}')
        self.commit = "a" * 40
        self.run_id = "42"
        self.identity = {"dirty": False, "git_commit": self.commit,
                         "base_version": self.version,
                         "version": "0.0.160-dev.1+g" + self.commit[:12],
                         "source_sha256": "b" * 64}
        self.build = self.root / "build"
        self.build.mkdir()
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.evidence = self.root / "evidence"
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.name = "foo_doujinstyle-v" + self.identity["version"] + ".fb2k-component"
        with ZipFile(self.build / self.name, "w") as z:
            for arch in ARCHES:
                folder = self.build / arch
                folder.mkdir()
                worker = b"MZ" + bytes(range(256)) * 2
                dll = b"MZ" + worker + b"code" * 100
                (folder / BINARIES[0]).write_bytes(dll)
                (folder / BINARIES[1]).write_bytes(worker)
                member = ("" if arch == "Win32" else
                          ("x64/" if arch == "x64" else "arm64ec/")) + BINARIES[0]
                z.writestr(member, dll)
                for binary in BINARIES:
                    target = f"{arch}/{binary}"
                    sarif = {"version":"2.1.0","runs":[{"tool":{"driver":{"name":"BinSkim"}},
                       "invocations":[{"executionSuccessful":True}],
                       "artifacts":[{"location":{"uri":"file:///C:/work/build/" + target}}],
                       "results":[{"ruleId":"BA2024","kind":"pass","level":"none"}]}]}
                    (self.reports / f"{arch}-{binary}.sarif").write_text(json.dumps(sarif))
        verdict = collect(self.build, self.reports, self.baseline, self.policy,
                          self.evidence, version_file=self.source / "VERSION",
                          source_commit=self.commit, run_id=self.run_id)
        self.assertEqual(verdict["status"], "pass", verdict)
        (self.evidence / "source-inputs.spdx.json").write_text(json.dumps({
            "spdxVersion": "SPDX-2.3",
            "documentNamespace": "https://github.com/bgonz808/foo_doujinstyle/spdx/source/" + self.commit,
            "documentComment": "PARTIAL source-input inventory only",
            "packages": [{"name": name} for name in ("json", "utf8proc", "zlib", "sqlite")],
        }))
        (self.dist / self.name).write_bytes((self.build / self.name).read_bytes())

    def check(self):
        return validate(self.source, self.dist, self.evidence,
                        self.commit, self.run_id, identity=self.identity)

    def test_qualified(self):
        item = self.check()
        self.assertEqual(item["scans"], 6)
        self.assertEqual(item["package_sha256"], checksum(self.dist / self.name))
        self.assertIn("not-host-qualified", item["qualification"])

    def test_partial_source_inventory_hash_bound(self):
        item = self.check()
        self.assertEqual(item["partial_source_inventory_sha256"],
                         checksum(self.evidence / "source-inputs.spdx.json"))
        self.assertEqual(item["inventory_scope"], "partial-vendored-source-inputs-only")

    def test_missing_source_inventory_fails(self):
        (self.evidence / "source-inputs.spdx.json").unlink()
        with self.assertRaises(OSError):
            self.check()

    def test_wrong_source_inventory_fails(self):
        path = self.evidence / "source-inputs.spdx.json"
        doc = json.loads(path.read_text())
        doc["documentNamespace"] = "https://github.com/bgonz808/foo_doujinstyle/spdx/source/" + "c" * 40
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "Partial source inventory"):
            self.check()

    def test_tampered_binary_rejected(self):
        (self.dist / self.name).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Package hash"):
            self.check()

    def test_wrong_source_rejected(self):
        with self.assertRaisesRegex(ValueError, "Source identity"):
            validate(self.source, self.dist, self.evidence,
                     "c"*40, self.run_id, identity=self.identity)

    def test_missing_scan_rejected(self):
        file = self.evidence / "binary-manifest.json"
        doc = json.loads(file.read_text())
        del doc["targets"]["ARM64EC/publish_references.exe"]
        file.write_text(json.dumps(doc))
        verdict_file = self.evidence / "binskim-verdict.json"
        verdict = json.loads(verdict_file.read_text())
        verdict["manifest_sha256"] = checksum(file)
        verdict_file.write_text(json.dumps(verdict))
        with self.assertRaisesRegex(ValueError, "Six-target"):
            self.check()

    def test_failed_policy_rejected(self):
        file = self.evidence / "binskim-verdict.json"
        result = json.loads(file.read_text())
        result["status"] = "fail"
        file.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "Six-PE security"):
            self.check()

    def test_exceptions_rejected(self):
        self.baseline.write_text('{"schema_version":1,"accepted_findings":[{"rule":"BA2024"}]}')
        with self.assertRaisesRegex(ValueError, "no-exceptions"):
            self.check()

if __name__ == "__main__":
    unittest.main()
