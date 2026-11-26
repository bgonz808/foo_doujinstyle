"""Real Git history and shallow checkout regression contracts."""
from pathlib import Path
import tempfile
import unittest

from tools.build_identity import identify, header
from tools.bump_revision import bump
from tools.verify_revision import verify, git
from tools.version_policy import read_policy, parse_revision


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "VERSION").write_text("0.2.0\n")
        (self.root / "RELEASE_STAGE").write_text("dev\n")
        self.base = self.commit(303)

    def commit(self, revision):
        if revision is not None:
            (self.root / "REVISION").write_text(f"{revision}\n")
        else:
            (self.root / "REVISION").unlink()
        (self.root / "change").write_text(str(revision) + str(len(git(self.root, "status", "--short"))))
        git(self.root, "add", ".")
        git(self.root, "commit", "--allow-empty", "-qm", "candidate")
        return git(self.root, "rev-parse", "HEAD")

    def test_full_and_shallow_have_identical_header(self):
        self.commit(304)
        shallow = Path(self.tmp.name) / "shallow"
        git(self.root, "clone", "--depth", "1", self.root.as_uri(), str(shallow))
        self.assertEqual(git(shallow, "rev-parse", "--is-shallow-repository"), "true")
        self.assertEqual(identify(self.root), identify(shallow))
        self.assertEqual(header(identify(self.root)), header(identify(shallow)))

    def test_bump_is_source_mutation_only(self):
        self.assertEqual(bump(self.root, self.base), 304)
        self.assertEqual(read_policy(self.root).revision, 304)
        self.assertTrue(identify(self.root)["dirty"])
        self.assertEqual(verify(self.root, self.base, self.commit(304)), 304)

    def test_unchanged_decreasing_and_missing_revisions_reject(self):
        for bad in (303, 302, None):
            with self.subTest(bad=bad):
                git(self.root, "reset", "--hard", self.base)
                with self.assertRaises(ValueError):
                    verify(self.root, self.base, self.commit(bad))

    def test_later_bump_does_not_hide_forgotten_candidate(self):
        self.commit(303)
        head = self.commit(304)
        with self.assertRaises(ValueError):
            verify(self.root, self.base, head)

    def test_parallel_stale_revision_and_reallocation(self):
        main = self.commit(304)
        git(self.root, "checkout", "-qb", "parallel", self.base)
        head = self.commit(304)
        with self.assertRaises(ValueError):
            verify(self.root, main, head)
        self.assertEqual(bump(self.root, main), 305)
        self.assertEqual(verify(self.root, main, self.commit(305)), 305)

    def test_stage_and_numeric_validation(self):
        for value in ("0", "0303", "-1", "2147483648", "1.2", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_revision(value)
        for stage, expected in (("dev", "0.2.0-dev.303"), ("rc.1", "0.2.0-rc.1"), ("stable", "0.2.0")):
            (self.root / "RELEASE_STAGE").write_text(stage)
            self.assertEqual(read_policy(self.root).product_version, expected)
        (self.root / "RELEASE_STAGE").write_text("rc.01")
        with self.assertRaises(ValueError):
            read_policy(self.root)
