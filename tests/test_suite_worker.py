"""Tests for runner_app.suite_worker: launching run_suite.py, streaming its
output, reading back summary.json, and Stop killing the process tree.

`subprocess.Popen`/`subprocess.run` are mocked throughout -- these tests
never invoke a real `run_suite.py`/PowerShell process.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from runner_app.suite_worker import SuiteRunEvent, SuiteRunWorker  # noqa: E402


def make_fake_popen(exit_code=0, stdout_lines=("ok\n",)):
    def _factory(cmd, **kwargs):
        class FakeProc:
            def __init__(self):
                self.stdout = iter(stdout_lines)
                self.stderr = io.StringIO("")
                self.returncode = exit_code
                self.pid = 9191

            def wait(self):
                return self.returncode

            def poll(self):
                return self.returncode

        return FakeProc()
    return _factory


class SuiteRunWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _drain(self, worker, kinds, timeout=5):
        collected = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = worker.events.get(timeout=0.2)
            except Exception:
                continue
            collected.append(event)
            if event.kind in kinds:
                break
        return collected

    def test_launches_expected_command_and_streams_output(self):
        fake_popen = make_fake_popen(exit_code=0, stdout_lines=("line1\n", "line2\n"))
        report_dir = os.path.join(self.tmpdir, "report")
        os.makedirs(report_dir)
        with open(os.path.join(report_dir, "summary.json"), "w") as f:
            json.dump({"counts": {"pass": 1}, "cases": []}, f)

        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=fake_popen) as mock_popen:
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            started = worker.start(
                ["test_cases/a.csv", "test_cases/b.csv"], max_retries=3,
                case_timeout_min=10, suite_timeout_min=0, report_dir=report_dir,
            )
            self.assertTrue(started)
            events = self._drain(worker, {SuiteRunEvent.DONE})

        kinds = [e.kind for e in events]
        self.assertEqual(kinds[0], SuiteRunEvent.STARTED)
        self.assertIn(SuiteRunEvent.OUTPUT, kinds)
        self.assertEqual(kinds[-1], SuiteRunEvent.DONE)
        done = events[-1]
        self.assertEqual(done.data["exit_code"], 0)
        self.assertEqual(done.data["summary"]["counts"], {"pass": 1})
        self.assertEqual(done.data["report_path"], os.path.join(report_dir, "report.html"))

        cmd = mock_popen.call_args[0][0]
        self.assertIn("run_suite.ps1", cmd[cmd.index("-File") + 1])
        self.assertIn("test_cases/a.csv", cmd)
        self.assertIn("test_cases/b.csv", cmd)
        self.assertIn("--max-retries", cmd)
        self.assertIn("3", cmd)
        self.assertIn("--case-timeout-min", cmd)
        self.assertIn("--report-dir", cmd)
        self.assertIn(report_dir, cmd)
        # suite_timeout_min=0 means "disabled" -- must not be passed through.
        self.assertNotIn("--suite-timeout-min", cmd)

    def test_suite_timeout_is_passed_through_when_nonzero(self):
        fake_popen = make_fake_popen(exit_code=0)
        report_dir = os.path.join(self.tmpdir, "report2")
        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=fake_popen) as mock_popen:
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["test_cases/a.csv"], 1, 5, 60, report_dir)
            self._drain(worker, {SuiteRunEvent.DONE})
        cmd = mock_popen.call_args[0][0]
        self.assertIn("--suite-timeout-min", cmd)
        self.assertIn("60", cmd)

    def test_missing_summary_json_is_reported_as_none(self):
        fake_popen = make_fake_popen(exit_code=1)
        report_dir = os.path.join(self.tmpdir, "no-report")
        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=fake_popen):
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["test_cases/a.csv"], 0, 5, 0, report_dir)
            events = self._drain(worker, {SuiteRunEvent.DONE})
        done = events[-1]
        self.assertIsNone(done.data["summary"])

    def test_second_start_while_running_is_rejected(self):
        block = {"ready": False}

        def _factory(cmd, **kwargs):
            class FakeProc:
                def __init__(self):
                    self.pid = 1234
                    self.returncode = 0

                @property
                def stdout(self):
                    while not block["ready"]:
                        time.sleep(0.05)
                    return iter(())

                def wait(self):
                    return self.returncode

                def poll(self):
                    return None

            return FakeProc()

        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=_factory):
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["test_cases/a.csv"], 0, 5, 0, os.path.join(self.tmpdir, "r1"))
            # Give the worker thread a moment to mark itself "running".
            deadline = time.time() + 2
            while time.time() < deadline and not worker.is_running():
                time.sleep(0.02)
            self.assertTrue(worker.is_running())
            second = worker.start(["test_cases/b.csv"], 0, 5, 0, os.path.join(self.tmpdir, "r2"))
            self.assertFalse(second)
            block["ready"] = True  # let the first run finish so the thread exits cleanly
            self._drain(worker, {SuiteRunEvent.DONE})

    def test_stop_kills_process_tree_via_taskkill(self):
        block = {"ready": False}

        def _factory(cmd, **kwargs):
            class FakeProc:
                def __init__(self):
                    self.pid = 5678
                    self.returncode = None

                @property
                def stdout(self):
                    while not block["ready"]:
                        time.sleep(0.05)
                    return iter(())

                def wait(self):
                    self.returncode = 1
                    return self.returncode

                def poll(self):
                    return self.returncode

            return FakeProc()

        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=_factory), \
                patch("runner_app.suite_worker.subprocess.run") as mock_run:
            mock_run.side_effect = lambda *a, **k: block.update(ready=True)
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["test_cases/a.csv"], 0, 5, 0, os.path.join(self.tmpdir, "r3"))
            deadline = time.time() + 2
            while time.time() < deadline and not worker.is_running():
                time.sleep(0.02)
            worker.stop()
            events = self._drain(worker, {SuiteRunEvent.CANCELLED, SuiteRunEvent.DONE})

        self.assertEqual(events[-1].kind, SuiteRunEvent.CANCELLED)
        killed_cmd = mock_run.call_args[0][0]
        self.assertIn("taskkill", killed_cmd)
        self.assertIn("5678", [str(a) for a in killed_cmd])

    def test_stop_is_noop_when_nothing_running(self):
        worker = SuiteRunWorker(repo_root=self.tmpdir)
        with patch("runner_app.suite_worker.subprocess.run") as mock_run:
            worker.stop()
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
