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

    def test_no_workflows_fails(self):
        self.path.unlink(missing_ok=True)
        self.assertTrue(audit(self.root))


if __name__ == '__main__':
    unittest.main()
