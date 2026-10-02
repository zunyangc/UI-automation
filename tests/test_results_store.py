"""Tests for runner_app.results_store (JSON-per-run persistence)."""
import datetime
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

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


class ExtractFailureMessageTests(unittest.TestCase):
    def test_step_failed_line(self):
        stdout = "some output\n*** STEP FAILED: step_4: exit 1, expected 0\nmore text\n"
        self.assertEqual(
            results_store.extract_failure_message(stdout),
            "step_4: exit 1, expected 0",
        )

    def test_step_raised_unexpected_error_line(self):
        stdout = "*** STEP RAISED UNEXPECTED ERROR: step_2: bad capture selector\n"
        self.assertEqual(
            results_store.extract_failure_message(stdout),
            "step_2: bad capture selector",
        )

    def test_prefers_last_step_failed_line_when_multiple(self):
        stdout = (
            "*** STEP FAILED: step_1: first\n"
            "some cleanup text\n"
            "*** STEP FAILED: step_5: second\n"
        )
        self.assertEqual(results_store.extract_failure_message(stdout), "step_5: second")

    def test_runner_error_line(self):
        stdout = "some earlier lines\nRUNNER ERROR: script not found: scripts/x.py\n"
        self.assertEqual(
            results_store.extract_failure_message(stdout),
            "script not found: scripts/x.py",
        )

    def test_falls_back_to_last_non_blank_line(self):
        stdout = "first line\nsecond line\n\n   \n"
        self.assertEqual(results_store.extract_failure_message(stdout), "second line")

    def test_empty_stdout_returns_placeholder(self):
        self.assertEqual(
            results_store.extract_failure_message(""),
            "(no failure detail captured)",
        )
        self.assertEqual(
            results_store.extract_failure_message(None),
            "(no failure detail captured)",
        )


class ExtractFailureStepTests(unittest.TestCase):
    def test_step_failed_line(self):
        stdout = "*** STEP FAILED: step_21: exit 1, expected 0\n"
        self.assertEqual(results_store.extract_failure_step(stdout), "step_21")

    def test_step_raised_unexpected_error_line(self):
        stdout = "*** STEP RAISED UNEXPECTED ERROR: step_2: bad capture selector\n"
        self.assertEqual(results_store.extract_failure_step(stdout), "step_2")

    def test_prefers_last_step_failed_line_when_multiple(self):
        stdout = (
            "*** STEP FAILED: step_1: first\n"
            "*** STEP FAILED: step_5: second\n"
        )
        self.assertEqual(results_store.extract_failure_step(stdout), "step_5")

    def test_runner_error_has_no_step(self):
        stdout = "RUNNER ERROR: script not found: scripts/x.py\n"
        self.assertIsNone(results_store.extract_failure_step(stdout))

    def test_empty_stdout_returns_none(self):
        self.assertIsNone(results_store.extract_failure_step(""))
        self.assertIsNone(results_store.extract_failure_step(None))


class FailureScreenshotPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _touch(self, *names):
        for name in names:
            with open(os.path.join(self.tmpdir, name), "w") as f:
                f.write("x")

    def test_filters_and_sorts_failure_screenshots(self):
        self._touch(
            "ss_2_FAILURE_console_log.png", "ss_1_FAILURE_ui_state.png",
            "ss_3_pass.png", "notes.txt",
        )
        paths = results_store.failure_screenshot_paths(self.tmpdir)
        names = [os.path.basename(p) for p in paths]
        self.assertEqual(names, ["ss_1_FAILURE_ui_state.png", "ss_2_FAILURE_console_log.png"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(
            results_store.failure_screenshot_paths(os.path.join(self.tmpdir, "nope")), [],
        )
        self.assertEqual(results_store.failure_screenshot_paths(None), [])
        self.assertEqual(results_store.failure_screenshot_paths(""), [])


class FormatFailureSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.generated_at = datetime.datetime(2026, 1, 1, 3, 0, 0, tzinfo=datetime.timezone.utc)

    def test_one_line_per_run_with_expected_wording(self):
        shot_dir = os.path.join(self.tmpdir, "shots")
        os.makedirs(shot_dir)
        with open(os.path.join(shot_dir, "ss_1_FAILURE_ui_state.png"), "w") as f:
            f.write("x")
        runs = [
            {
                "name": "prod-003-dotnet_core_cs",
                "stdout_tail": "*** STEP FAILED: step_12: exit 1, expected 0\n",
                "screenshot_dir": shot_dir,
            },
            {
                "name": "e2e-017-retarget_version",
                "stdout_tail": "*** STEP FAILED: step_3: console did not contain 'ok'\n",
                "screenshot_dir": None,
            },
        ]
        text = results_store.format_failure_summary(runs, generated_at=self.generated_at)
        self.assertIn("Generated: 2026-01-01 03:00:00 UTC", text)
        self.assertIn(
            "Failed at prod-003-dotnet_core_cs test case, failed message is "
            "step_12: exit 1, expected 0.",
            text,
        )
        line2 = (
            "Failed at e2e-017-retarget_version test case, failed message is "
            "step_3: console did not contain 'ok'. (no failure screenshot captured)"
        )
        self.assertIn(line2, text)
        # The run WITH a screenshot must not get the "no screenshot" note.
        self.assertNotIn(
            "prod-003-dotnet_core_cs test case, failed message is "
            "step_12: exit 1, expected 0. (no failure screenshot captured)",
            text,
        )


class WriteFailureReportZipTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _make_shot_dir(self, *names):
        shot_dir = tempfile.mkdtemp(dir=self.tmpdir)
        for name in names:
            with open(os.path.join(shot_dir, name), "w") as f:
                f.write("fake png bytes")
        return shot_dir

    def test_zip_contains_summary_and_failure_screenshots_only(self):
        shot_dir = self._make_shot_dir(
            "ss_1_FAILURE_ui_state.png", "ss_2_FAILURE_console_log.png", "ss_3_pass.png",
        )
        runs = [{
            "name": "sample_case",
            "stdout_tail": "*** STEP FAILED: step_1: boom\n",
            "screenshot_dir": shot_dir,
        }]
        zip_path = os.path.join(self.tmpdir, "out", "report.zip")
        result = results_store.write_failure_report_zip(runs, zip_path)
        self.assertEqual(result, zip_path)
        self.assertTrue(os.path.isfile(zip_path))

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            self.assertIn("summary.txt", names)
            self.assertIn("screenshots/sample_case_ss_1_FAILURE_ui_state.png", names)
            self.assertIn("screenshots/sample_case_ss_2_FAILURE_console_log.png", names)
            # The non-failure screenshot must be excluded from the zip.
            self.assertFalse(any("ss_3_pass" in n for n in names))
            summary = zf.read("summary.txt").decode("utf-8")
            self.assertIn("Failed at sample_case test case, failed message is step_1: boom.", summary)

    def test_missing_screenshot_dir_still_produces_summary_only_zip(self):
        runs = [{"name": "no_shots_case", "stdout_tail": "RUNNER ERROR: script not found\n",
                 "screenshot_dir": None}]
        zip_path = os.path.join(self.tmpdir, "report2.zip")
        results_store.write_failure_report_zip(runs, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            self.assertEqual(names, ["summary.txt"])
            summary = zf.read("summary.txt").decode("utf-8")
            self.assertIn("no failure screenshot captured", summary)


if __name__ == "__main__":
    unittest.main()
