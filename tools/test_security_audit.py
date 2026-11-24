"""Negative tests prove our local workflow policy rejects regressions."""
import tempfile
from pathlib import Path
import unittest

from tools.security_audit import APPROVED_ACTIONS, audit


class WorkflowSecurityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        directory = self.root / '.github/workflows'
        directory.mkdir(parents=True)
        self.path = directory / 'test.yml'
        self.good = ("name: Test\non:\n  pull_request:\npermissions:\n  contents: read\n"
                     "jobs:\n  test:\n    runs-on: ubuntu-24.04\n    steps:\n"
                     f"      - uses: actions/checkout@{APPROVED_ACTIONS['actions/checkout']}\n"
                     "        with:\n          persist-credentials: false\n")

    def check(self, old, new, expect_error):
        self.path.write_text(self.good.replace(old, new), encoding='utf-8')
        errors = audit(self.root)
        self.assertEqual(bool(errors), expect_error, errors)

    def test_approved(self):
        self.check('name: Test', 'name: Test', False)

    def test_unpinned(self):
        self.check('actions/checkout@' + APPROVED_ACTIONS['actions/checkout'], 'actions/checkout@v7', True)

    def test_unknown_action(self):
        self.check('actions/checkout@', 'surprise/action@', True)

    def test_dangerous_trigger(self):
        self.check('  pull_request:', '  pull_request_target:', True)

    def test_write_token(self):
        self.check('  contents: read', '  contents: write', True)

    def test_checkout_credentials(self):
        self.check('persist-credentials: false', 'persist-credentials: true', True)

    def signer_fixture(self):
        self.path.unlink(missing_ok=True)
        self.path = self.root / ".github/workflows/windows-build.yml"
        content = """name: Windows Build and BinSkim
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  component:
    runs-on: windows-2022
  provenance:
    name: ci-provenance
    if: (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && github.ref == 'refs/heads/main'
    needs: [component]
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      actions: read
      attestations: write
      id-token: write
    steps:
      - name: Download built component
        uses: actions/download-artifact@{download}
        with:
          name: foo_doujinstyle-windows-wip
      - name: Download security evidence
        uses: actions/download-artifact@{download}
        with:
          name: foo_doujinstyle-binskim-evidence
      - name: Verify predicate
        run: python3 -m tools.attestation_gate --source-commit "$SOURCE_COMMIT" --run-id "$CI_RUN_ID"
      - name: Standard provenance
        uses: actions/attest@{attest}
        with:
          subject-path: dist/package/*.fb2k-component
      - name: Security predicate
        uses: actions/attest@{attest}
        with:
          subject-path: dist/package/*.fb2k-component
          predicate-path: dist/security-predicate.json
""".format(download=APPROVED_ACTIONS["actions/download-artifact"],
           attest=APPROVED_ACTIONS["actions/attest"])
        self.path.write_text(content, encoding="utf-8")

    def test_signer_valid_main_only(self):
        self.signer_fixture()
        self.assertEqual(audit(self.root), [])

    def test_signer_must_be_main_only(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().replace(
            "github.ref == 'refs/heads/main'", "github.ref != 'refs/heads/main'"))
        self.assertTrue(any("main-only provenance" in e for e in audit(self.root)))

    def test_signer_needs_component(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().replace(
            "needs: [component]", "needs: []"))
        self.assertTrue(audit(self.root))

    def test_signer_cannot_elevate_build(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().replace(
            "  provenance:\n", "    permissions:\n      id-token: write\n  provenance:\n"))
        self.assertTrue(any("outside provenance" in e for e in audit(self.root)))

    def test_signer_cannot_add_write_scope(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().replace(
            "      actions: read\n", "      actions: read\n      contents: write\n"))
        self.assertTrue(audit(self.root))

    def test_signer_cannot_drop_security_attestation(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().replace(
            "      - name: Security predicate\n", "      - name: Security predicate\n        # removed\n").replace(
            "        uses: actions/attest@" + APPROVED_ACTIONS["actions/attest"] + "\n        with:\n          subject-path: dist/package/*.fb2k-component\n          predicate-path: dist/security-predicate.json",
            "        run: echo no-signature"))
        self.assertTrue(audit(self.root))

    def test_signer_missing_job_rejected(self):
        self.signer_fixture()
        self.path.write_text(self.path.read_text().split("\n  provenance:\n")[0] + "\n")
        self.assertTrue(audit(self.root))

    def test_no_workflows_fails(self):
        self.path.unlink(missing_ok=True)
        self.assertTrue(audit(self.root))


if __name__ == '__main__':
    unittest.main()
