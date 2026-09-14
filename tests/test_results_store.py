"""Tests for runner_app.results_store (JSON-per-run persistence)."""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from runner_app import results_store  # noqa: E402


class ResultsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def test_save_and_list_round_trip(self):
        started = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ended = started + datetime.timedelta(seconds=5)
        path = results_store.save_run(
            spec_path="test_cases/sample.csv", name="sample_case",
            started_at=started, ended_at=ended, exit_code=0,
            stdout_text="ok\n", stderr_text="", screenshot_dir="screenshots/sample-x",
            results_dir=self.tmpdir,
        )
        self.assertTrue(os.path.isfile(path))

        runs = results_store.list_runs(self.tmpdir)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["name"], "sample_case")
        self.assertEqual(run["status"], "pass")
        self.assertEqual(run["exit_code"], 0)
        self.assertAlmostEqual(run["duration_seconds"], 5.0)
        self.assertEqual(run["screenshot_dir"], "screenshots/sample-x")

    def test_status_mapping_for_exit_codes(self):
        self.assertEqual(results_store.status_for_exit_code(0), "pass")
        self.assertEqual(results_store.status_for_exit_code(1), "fail")
        self.assertEqual(results_store.status_for_exit_code(2), "error")
        self.assertEqual(results_store.status_for_exit_code(99), "error")

    def test_status_override_records_cancelled_regardless_of_exit_code(self):
        started = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ended = started + datetime.timedelta(seconds=2)
        results_store.save_run(
            spec_path="test_cases/sample.csv", name="sample_case",
            started_at=started, ended_at=ended, exit_code=0,
            results_dir=self.tmpdir, status_override="cancelled",
        )
        run = results_store.list_runs(self.tmpdir)[0]
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(run["exit_code"], 0)  # raw exit code still recorded

    def test_list_runs_sorted_newest_first(self):
        older = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        newer = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
        results_store.save_run("a.csv", "a", older, older, 0, results_dir=self.tmpdir)
        results_store.save_run("b.csv", "b", newer, newer, 0, results_dir=self.tmpdir)
        runs = results_store.list_runs(self.tmpdir)
        self.assertEqual([r["name"] for r in runs], ["b", "a"])

    def test_stdout_tail_is_truncated(self):
        long_text = "x" * (results_store.TAIL_CHARS + 500)
        started = datetime.datetime.now(datetime.timezone.utc)
        results_store.save_run(
            "a.csv", "a", started, started, 0, stdout_text=long_text,
            results_dir=self.tmpdir,
        )
        run = results_store.list_runs(self.tmpdir)[0]
        self.assertEqual(len(run["stdout_tail"]), results_store.TAIL_CHARS)


if __name__ == "__main__":
    unittest.main()
