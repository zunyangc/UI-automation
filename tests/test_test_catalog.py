"""Tests for runner_app.test_catalog (test-case discovery for the GUI)."""
import os
import shutil
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import sys  # noqa: E402
sys.path.insert(0, REPO_ROOT)
from runner_app import test_catalog  # noqa: E402

VALID_CSV = """# CONFIG
Section,Key,Value
name,,sample_case
description,,"A one-line description."

# STEPS
No,step no,Main step,Trigger,script,args,wait_ms,capture,expect_exit,expected_contains,poll_total_ms,poll_interval_ms,screenshot_pass,screenshot_fail,max_iter,Expected
1,1,Do a thing,Press a key.,scripts/input/key.py,,100,,,,,,,,
"""

MALFORMED_CSV = """# CONFIG
Section,Key,Value
name,,broken_case
description,,"Broken on purpose."

# STEPS
No,step no,Main step,Trigger,script,args,wait_ms,capture,expect_exit,expected_contains,poll_total_ms,poll_interval_ms,screenshot_pass,screenshot_fail,max_iter,Expected
1,1,Do a thing,Press a key.,scripts/input/key.py,[not valid json,100,,,,,,,,
"""


class TestCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _write(self, filename, content):
        path = os.path.join(self.tmpdir, filename)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return path

    def test_discovers_valid_case_with_name_and_description(self):
        self._write("sample.csv", VALID_CSV)
        cases = test_catalog.discover(self.tmpdir)
        self.assertEqual(len(cases), 1)
        tc = cases[0]
        self.assertEqual(tc.name, "sample_case")
        self.assertEqual(tc.description, "A one-line description.")
        self.assertIsNone(tc.error)

    def test_display_name_matches_filename_not_config_name(self):
        # display_name should reflect the actual file under test_cases/ (so
        # it matches what a tester sees when browsing the folder), even
        # though the CSV's internal CONFIG name is something else.
        self._write("prod-002-hot_reload.csv", VALID_CSV)
        cases = test_catalog.discover(self.tmpdir)
        self.assertEqual(len(cases), 1)
        tc = cases[0]
        self.assertEqual(tc.name, "sample_case")
        self.assertEqual(tc.file_stem, "prod-002-hot_reload")
        self.assertEqual(tc.display_name, "prod-002-hot_reload")

    def test_skips_template_file(self):
        self._write("_template.csv", VALID_CSV)
        cases = test_catalog.discover(self.tmpdir)
        self.assertEqual(cases, [])

    def test_malformed_csv_surfaced_as_error_not_raised(self):
        self._write("broken.csv", MALFORMED_CSV)
        cases = test_catalog.discover(self.tmpdir)
        self.assertEqual(len(cases), 1)
        self.assertIsNotNone(cases[0].error)

    def test_sorted_by_filename(self):
        self._write("b_case.csv", VALID_CSV)
        self._write("a_case.csv", VALID_CSV.replace("sample_case", "a_case_name"))
        cases = test_catalog.discover(self.tmpdir)
        self.assertEqual([os.path.basename(c.path) for c in cases],
                         ["a_case.csv", "b_case.csv"])


if __name__ == "__main__":
    unittest.main()
