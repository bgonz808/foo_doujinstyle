"""Verify installed archive actions no longer depend on Python handlers."""
import unittest
from pathlib import Path
from native.pack_search_worker import worker_sources

ROOT = Path(__file__).resolve().parents[1]


class NativeArchiveCutover(unittest.TestCase):
    def test_native_ui_routing(self):
        plugin = (ROOT / "native/search.cpp").read_text(encoding="utf-8")
        self.assertIn('arguments.find(L" --download-archive ")', plugin)
        self.assertIn('arguments.find(L" --repair-release ")', plugin)
        self.assertIn('native_idle || native_foreground || native_chart || native_archive', plugin)
        self.assertIn('L" --explicit-archive --cache "', plugin)
        publisher = (ROOT / "native/publish_references.cpp").read_text(encoding="utf-8")
        self.assertIn('args[1] == "--explicit-archive"', publisher)
        self.assertIn("doujin::explicit_job::download(options, submit, read_listing, emit)", publisher)
        self.assertIn("doujin::explicit_job::repair(options, submit, read_listing, emit)", publisher)

    def test_python_code_is_pruned_from_real_zipapp(self):
        members, report = worker_sources(ROOT)
        payload = members["native/search_worker.py"].decode("utf-8")
        for name in ("download_archive", "repair_release", "native_offer_batch", "export",
                     "present_archive_event", "dispatch_archives", "size_text"):
            self.assertNotIn("def " + name + "(", payload)
            self.assertIn(name, report["modules"]["native/search_worker.py"]["standalone"])
        self.assertIn("Installed archive download is native-only", payload)
        self.assertIn("Installed metadata repair is native-only", payload)
        self.assertLess(report["python_lines"], 1105)
        self.assertGreater(report["fallback_python_lines"], 0)


if __name__ == "__main__":
    unittest.main()
