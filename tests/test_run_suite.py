"""Tests for run_suite.py: stuck detection, the round/retry loop, and spec
resolution. `run_one_attempt` is monkeypatched throughout -- these tests
never invoke a real `run.ps1`/PowerShell process.
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import run_suite  # noqa: E402


def make_attempt(spec, attempt_no, status, failed_step=None, failure_message=None):
    return {
        "spec": spec, "name": run_suite._case_name(spec), "attempt_no": attempt_no,
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "duration_seconds": 1.0, "status": status,
        "exit_code": {"pass": 0, "fail": 1, "error": 2, "timeout": None}.get(status),
        "failed_step": failed_step, "failure_message": failure_message,
        "screenshot_dir": None, "screenshots": [], "stdout_tail": "", "result_json": None,
    }


class IsStuckTests(unittest.TestCase):
    def test_empty_history_is_not_stuck(self):
        self.assertFalse(run_suite.is_stuck([]))

    def test_single_failure_is_not_stuck(self):
        history = [make_attempt("a.csv", 1, "fail", "step_1", "boom")]
        self.assertFalse(run_suite.is_stuck(history))

    def test_runner_error_is_stuck_immediately(self):
        history = [make_attempt("a.csv", 1, "error")]
        self.assertTrue(run_suite.is_stuck(history))

    def test_two_consecutive_timeouts_is_stuck(self):
        history = [
            make_attempt("a.csv", 1, "timeout"),
            make_attempt("a.csv", 2, "timeout"),
        ]
        self.assertTrue(run_suite.is_stuck(history))

    def test_timeout_then_fail_is_not_stuck(self):
        history = [
            make_attempt("a.csv", 1, "timeout"),
            make_attempt("a.csv", 2, "fail", "step_1", "boom"),
        ]
        self.assertFalse(run_suite.is_stuck(history))

    def test_same_step_same_message_twice_is_stuck(self):
        history = [
            make_attempt("a.csv", 1, "fail", "step_4", "window not found after 300000ms"),
            make_attempt("a.csv", 2, "fail", "step_4", "window not found after 300000ms"),
        ]
        self.assertTrue(run_suite.is_stuck(history))

    def test_same_step_different_volatile_numbers_still_stuck(self):
        # Digit-normalized comparison: elapsed ms/attempt counts differ but
        # the message shape is identical -- still treated as a repeat.
        history = [
            make_attempt("a.csv", 1, "fail", "step_4", "window not found after 300000ms (290 attempts)"),
            make_attempt("a.csv", 2, "fail", "step_4", "window not found after 301500ms (291 attempts)"),
        ]
        self.assertTrue(run_suite.is_stuck(history))

    def test_different_step_is_not_stuck(self):
        history = [
            make_attempt("a.csv", 1, "fail", "step_4", "boom"),
            make_attempt("a.csv", 2, "fail", "step_9", "boom"),
        ]
        self.assertFalse(run_suite.is_stuck(history))

    def test_different_message_is_not_stuck(self):
        history = [
            make_attempt("a.csv", 1, "fail", "step_4", "console did not contain 'ok'"),
            make_attempt("a.csv", 2, "fail", "step_4", "window not found"),
        ]
        self.assertFalse(run_suite.is_stuck(history))

    def test_pass_after_fail_is_not_stuck(self):
        history = [
            make_attempt("a.csv", 1, "fail", "step_4", "boom"),
            make_attempt("a.csv", 2, "pass"),
        ]
        self.assertFalse(run_suite.is_stuck(history))


class ResolveSpecsTests(unittest.TestCase):
    def test_explicit_specs_returned_as_is(self):
        args = run_suite.parse_args(["test_cases/a.csv", "test_cases/b.csv"])
        self.assertEqual(run_suite.resolve_specs(args), ["test_cases/a.csv", "test_cases/b.csv"])

    def test_all_excludes_template(self):
        args = run_suite.parse_args(["--all"])
        specs = run_suite.resolve_specs(args)
        self.assertTrue(len(specs) > 0)
        self.assertNotIn("test_cases\\_template.csv", specs)
        self.assertFalse(any(os.path.basename(s) == "_template.csv" for s in specs))

    def test_all_combined_with_explicit_specs_errors(self):
        args = run_suite.parse_args(["--all", "test_cases/a.csv"])
        with self.assertRaises(SystemExit):
            run_suite.resolve_specs(args)

    def test_no_specs_and_no_all_errors(self):
        with self.assertRaises(SystemExit):
            run_suite.parse_args([])


class RunLoopTests(unittest.TestCase):
    """Exercise main()'s round/retry loop with a fake run_one_attempt so no
    real UIA execution happens.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.report_dir = os.path.join(self.tmpdir, "report")

    def _run(self, specs, attempt_sequences, **extra_args):
        """`attempt_sequences` maps spec -> list of statuses to return on
        successive calls (one per attempt)."""
        calls = {s: 0 for s in specs}

        def fake_run_one_attempt(spec, attempt_no, case_timeout_min, quiet, no_cleanup, results_dir):
            idx = calls[spec]
            calls[spec] += 1
            status = attempt_sequences[spec][idx]
            # Distinct (non-numeric-only) message per attempt index by
            # default so repeated "fail" entries aren't mistaken for a
            # stuck-detection repeat -- is_stuck() digit-normalizes
            # messages, so a purely numeric suffix (e.g. "...-0" vs
            # "...-1") would still compare equal. Unless a test explicitly
            # wants a true repeat (see
            # test_stuck_case_stops_retrying_before_max_retries, which
            # passes an identical message on purpose), use a distinct
            # letter per attempt.
            variant = chr(ord("A") + idx)
            return make_attempt(spec, attempt_no, status, "step_1", f"boom-{status}-{variant}")

        argv = list(specs) + ["--report-dir", self.report_dir]
        for k, v in extra_args.items():
            argv += [f"--{k.replace('_', '-')}", str(v)]
        with patch.object(run_suite, "run_one_attempt", side_effect=fake_run_one_attempt):
            rc = run_suite.main(argv)
        return rc, calls

    def test_passes_on_first_try_no_retry(self):
        rc, calls = self._run(["test_cases/a.csv"], {"test_cases/a.csv": ["pass"]})
        self.assertEqual(rc, 0)
        self.assertEqual(calls["test_cases/a.csv"], 1)

    def test_retries_until_pass_within_max_retries(self):
        rc, calls = self._run(
            ["test_cases/a.csv"],
            {"test_cases/a.csv": ["fail", "fail", "pass"]},
            max_retries=2,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls["test_cases/a.csv"], 3)

    def test_retries_exhausted_stops_at_max_retries(self):
        # Each failure must differ (different message) so is_stuck() never
        # fires first -- this isolates the --max-retries cutoff.
        def fake_run_one_attempt(spec, attempt_no, case_timeout_min, quiet, no_cleanup, results_dir):
            # Digit-only suffixes are normalized away by is_stuck()'s
            # comparison -- use a letter so each attempt's message is
            # genuinely distinct.
            variant = chr(ord("A") + attempt_no)
            return make_attempt(spec, attempt_no, "fail", "step_1", f"boom-{variant}")

        argv = ["test_cases/a.csv", "--report-dir", self.report_dir, "--max-retries", "1"]
        with patch.object(run_suite, "run_one_attempt", side_effect=fake_run_one_attempt):
            rc = run_suite.main(argv)
        self.assertEqual(rc, 1)
        import json
        with open(os.path.join(self.report_dir, "summary.json")) as f:
            summary = json.load(f)
        case = summary["cases"][0]
        self.assertEqual(case["final_status"], "retries_exhausted")
        self.assertEqual(case["attempt_count"], 2)  # 1 initial + 1 retry

    def test_stuck_case_stops_retrying_before_max_retries(self):
        calls = {"test_cases/a.csv": 0}

        def fake_run_one_attempt(spec, attempt_no, case_timeout_min, quiet, no_cleanup, results_dir):
            calls[spec] += 1
            # Identical step/message every attempt -- a true repeat.
            return make_attempt(spec, attempt_no, "fail", "step_1", "boom")

        argv = ["test_cases/a.csv", "--report-dir", self.report_dir, "--max-retries", "5"]
        with patch.object(run_suite, "run_one_attempt", side_effect=fake_run_one_attempt):
            rc = run_suite.main(argv)
        self.assertEqual(rc, 1)
        # Stuck detection should fire after the 2nd identical failure,
        # well before the generous max_retries=5 ceiling.
        self.assertEqual(calls["test_cases/a.csv"], 2)

    def test_mixed_pass_and_fail_cases_report_independent_outcomes(self):
        rc, calls = self._run(
            ["test_cases/a.csv", "test_cases/b.csv"],
            {"test_cases/a.csv": ["pass"], "test_cases/b.csv": ["fail", "fail"]},
            max_retries=1,
        )
        self.assertEqual(rc, 1)
        self.assertEqual(calls["test_cases/a.csv"], 1)
        self.assertEqual(calls["test_cases/b.csv"], 2)

    def test_suite_timeout_marks_unreached_cases_not_run(self):
        # --max-retries 0 so case "a"'s single failure is immediately
        # terminal (retries_exhausted) rather than requeued -- isolating
        # the suite-timeout cutoff to a single round. monotonic() is
        # called exactly 4 times in that round: once to record the suite
        # start, once for the while-loop deadline check, then once per
        # spec in the for-loop -- the last of which (case "b") is made to
        # look like the deadline has already passed.
        def fake_run_one_attempt(spec, attempt_no, case_timeout_min, quiet, no_cleanup, results_dir):
            return make_attempt(spec, attempt_no, "fail", "step_1", "boom")

        argv = [
            "test_cases/a.csv", "test_cases/b.csv",
            "--report-dir", self.report_dir, "--suite-timeout-min", "1", "--max-retries", "0",
        ]
        with patch.object(run_suite, "run_one_attempt", side_effect=fake_run_one_attempt), \
                patch.object(run_suite.time, "monotonic", side_effect=[0, 0, 0, 1000]):
            rc = run_suite.main(argv)
        self.assertEqual(rc, 1)
        import json
        with open(os.path.join(self.report_dir, "summary.json")) as f:
            summary = json.load(f)
        statuses = {c["name"]: c["final_status"] for c in summary["cases"]}
        self.assertEqual(statuses["a"], "retries_exhausted")
        self.assertEqual(statuses["b"], "not_run_timeout")


if __name__ == "__main__":
    unittest.main()
