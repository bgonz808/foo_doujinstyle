"""Offline SearchDialog source integration checks; these are not host UIA tests."""
from pathlib import Path
import re
import unittest

SEARCH = Path(__file__).resolve().parents[1] / "native" / "search.cpp"


class FacetClearSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = SEARCH.read_text(encoding="utf-8")

    def test_facet_clear_ids_are_distinct(self):
        m = re.search(r"id_sort_label\s*=\s*144,\s*id_tag_clear\s*=\s*145,\s*id_format_clear\s*=\s*146", self.code)
        self.assertIsNotNone(m)

    def test_new_controls_are_real_buttons_and_dark_mode_registered(self):
        for name in ("id_tag_clear", "id_format_clear"):
            self.assertRegex(self.code, r'control\(L"BUTTON",\s*L"×",\s*WS_TABSTOP\s*\|\s*BS_PUSHBUTTON,\s*' + name)
        self.assertIn("id_tag_clear, id_format_clear, id_ready_filter", self.code)

    def test_clear_uses_passive_projection_without_new_search(self):
        code = self.code
        self.assertIn("HIWORD(wp) == BN_CLICKED", code)
        body = re.search(r"if\s*\(\(LOWORD\(wp\)\s*==\s*id_tag_clear.*?return TRUE;\s*\}", code, re.S)
        self.assertIsNotNone(body)
        self.assertIn("CB_SETCURSEL", body.group(0))
        self.assertIn("apply_filters()", body.group(0))
        self.assertNotIn("launch_worker", body.group(0))
        self.assertNotIn("start_search", body.group(0))
        apply = re.search(r"void apply_filters\(\)\s*\{(.*?)\n\s*ResultPosition result_position\(", code, re.S)
        self.assertIsNotNone(apply)
        self.assertIn("projection_dirty_ = true;", apply.group(1))

    def test_human_numeric_sort_and_selected_facet_visibility(self):
        self.assertIn("doujin::facet_sort::natural_compare(a, b, compare_text)", self.code)
        self.assertIn("std::stable_sort(values.begin(), values.end()", self.code)
        self.assertIn("!result_filter_.tag.empty() ? SW_SHOW : SW_HIDE", self.code)
        self.assertIn("!result_filter_.format.empty() ? SW_SHOW : SW_HIDE", self.code)
        self.assertGreaterEqual(self.code.count("update_filter_clear_buttons();"), 3)


if __name__ == "__main__":
    unittest.main()
