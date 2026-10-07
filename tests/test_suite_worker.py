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
from runner_app.suite_worker import (  # noqa: E402
    SuiteRunEvent, SuiteRunWorker, _EVENT_PREFIX, _parse_event_line,
)


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

    def test_stop_called_before_popen_registers_still_kills_process(self):
        # Simulates clicking Stop in the window between start() returning
        # and the worker thread's subprocess.Popen() call completing --
        # stop() must remember the request (via _stop_before_launch) and
        # honor it the instant the process is registered, instead of
        # silently doing nothing because self._proc was still None.
        popen_called = {"flag": False}
        allow_popen = {"flag": False}

        def _factory(cmd, **kwargs):
            popen_called["flag"] = True
            while not allow_popen["flag"]:
                time.sleep(0.02)

            class FakeProc:
                def __init__(self):
                    self.pid = 4242
                    self.returncode = None
                    self.stdout = iter(())

                def wait(self):
                    self.returncode = 1
                    return self.returncode

                def poll(self):
                    return self.returncode

            return FakeProc()

        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=_factory), \
                patch("runner_app.suite_worker.subprocess.run") as mock_run:
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["test_cases/a.csv"], 0, 5, 0, os.path.join(self.tmpdir, "r4"))
            # Wait until the worker thread is inside Popen() (so is_running()
            # is true via _starting) but hasn't returned a process yet.
            deadline = time.time() + 2
            while time.time() < deadline and not popen_called["flag"]:
                time.sleep(0.02)
            self.assertTrue(worker.is_running())
            worker.stop()  # proc is still None here -- must not be a no-op
            allow_popen["flag"] = True  # let Popen "return" the FakeProc now
            events = self._drain(worker, {SuiteRunEvent.CANCELLED, SuiteRunEvent.DONE})

        self.assertEqual(events[-1].kind, SuiteRunEvent.CANCELLED)
        mock_run.assert_called_once()
        killed_cmd = mock_run.call_args[0][0]
        self.assertIn("taskkill", killed_cmd)
        self.assertIn("4242", [str(a) for a in killed_cmd])


class ParseEventLineTests(unittest.TestCase):
    """`_parse_event_line` turns one of run_suite.py's
    `##SUITE-EVENT## {json}` lines into a `SuiteRunEvent`, or `None` if the
    line isn't one (plain log output, malformed JSON, unknown type) -- the
    caller then falls back to forwarding it as a plain OUTPUT line instead
    of crashing or dropping it silently.
    """

    def test_round_started_line_is_parsed(self):
        line = '##SUITE-EVENT## {"type": "round_started", "round": 2, "pending": ["a.csv", "b.csv"]}\n'
        event = _parse_event_line(line)
        self.assertEqual(event.kind, SuiteRunEvent.ROUND_STARTED)
        self.assertEqual(event.data, {"round": 2, "pending": ["a.csv", "b.csv"]})

    def test_attempt_started_line_is_parsed(self):
        line = '##SUITE-EVENT## {"type": "attempt_started", "spec": "a.csv", "attempt_no": 3}'
        event = _parse_event_line(line)
        self.assertEqual(event.kind, SuiteRunEvent.CASE_RUNNING)
        self.assertEqual(event.data, {"spec": "a.csv", "attempt_no": 3})

    def test_attempt_done_line_is_parsed(self):
        line = '##SUITE-EVENT## {"type": "attempt_done", "spec": "a.csv", "attempt_no": 1, "status": "fail"}'
        event = _parse_event_line(line)
        self.assertEqual(event.kind, SuiteRunEvent.CASE_ATTEMPT_DONE)
        self.assertEqual(event.data, {"spec": "a.csv", "attempt_no": 1, "status": "fail"})

    def test_case_final_line_is_parsed(self):
        line = '##SUITE-EVENT## {"type": "case_final", "spec": "a.csv", "final_status": "stuck"}'
        event = _parse_event_line(line)
        self.assertEqual(event.kind, SuiteRunEvent.CASE_FINAL)
        self.assertEqual(event.data, {"spec": "a.csv", "final_status": "stuck"})

    def test_plain_log_line_is_not_an_event(self):
        self.assertIsNone(_parse_event_line("--- [a] attempt 1 ---\n"))

    def test_malformed_json_after_prefix_is_not_an_event(self):
        self.assertIsNone(_parse_event_line("##SUITE-EVENT## {not valid json"))

    def test_unknown_event_type_is_not_an_event(self):
        self.assertIsNone(_parse_event_line('##SUITE-EVENT## {"type": "something_new", "x": 1}'))

    def test_valid_json_scalar_non_object_payload_is_not_an_event(self):
        # `null`/a bare number/a list are all valid JSON but aren't dicts,
        # so there's no "type" to look up -- must not raise AttributeError
        # from calling .pop() on a non-dict.
        for body in ("null", "42", "[1, 2, 3]", '"a string"'):
            with self.subTest(body=body):
                self.assertIsNone(_parse_event_line(f"{_EVENT_PREFIX}{body}"))

    def test_payload_with_top_level_kind_key_is_not_an_event(self):
        # A payload whose keys collide with SuiteRunEvent.__init__'s own
        # `kind` positional argument (via **payload) must not raise
        # TypeError out of _parse_event_line -- that construction happens
        # inside the guarded try block.
        line = '##SUITE-EVENT## {"type": "case_final", "kind": "oops", "spec": "a.csv"}'
        self.assertIsNone(_parse_event_line(line))


class SuiteRunWorkerStructuredEventStreamingTests(unittest.TestCase):
    """End-to-end (through SuiteRunWorker._run) check that structured
    event lines are posted as their parsed kind -- not as plain OUTPUT --
    while ordinary log lines still flow through as OUTPUT untouched.
    """

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

    def test_structured_lines_become_typed_events_plain_lines_stay_output(self):
        stdout_lines = (
            "\n=== Round 1: 1 case(s) ===\n",
            '##SUITE-EVENT## {"type": "round_started", "round": 1, "pending": ["a.csv"]}\n',
            '##SUITE-EVENT## {"type": "attempt_started", "spec": "a.csv", "attempt_no": 1}\n',
            "    some case output\n",
            '##SUITE-EVENT## {"type": "attempt_done", "spec": "a.csv", "attempt_no": 1, "status": "pass"}\n',
            '##SUITE-EVENT## {"type": "case_final", "spec": "a.csv", "final_status": "pass"}\n',
        )
        fake_popen = make_fake_popen(exit_code=0, stdout_lines=stdout_lines)
        report_dir = os.path.join(self.tmpdir, "report")
        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=fake_popen):
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["a.csv"], 0, 5, 0, report_dir)
            events = self._drain(worker, {SuiteRunEvent.DONE})

        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            SuiteRunEvent.STARTED,
            SuiteRunEvent.OUTPUT,  # "=== Round 1: ... ==="
            SuiteRunEvent.ROUND_STARTED,
            SuiteRunEvent.CASE_RUNNING,
            SuiteRunEvent.OUTPUT,  # "    some case output"
            SuiteRunEvent.CASE_ATTEMPT_DONE,
            SuiteRunEvent.CASE_FINAL,
            SuiteRunEvent.DONE,
        ])
        round_started = events[2]
        self.assertEqual(round_started.data, {"round": 1, "pending": ["a.csv"]})
        case_final = events[6]
        self.assertEqual(case_final.data, {"spec": "a.csv", "final_status": "pass"})

    def test_malformed_event_line_mid_stream_does_not_abort_remaining_output(self):
        # A malformed prefixed line (e.g. a bare JSON `null`, which is
        # valid JSON but not a dict) must not raise out of the read loop --
        # it should be forwarded as plain OUTPUT and every later line must
        # still be processed and reach DONE.
        stdout_lines = (
            '##SUITE-EVENT## {"type": "round_started", "round": 1, "pending": ["a.csv"]}\n',
            "##SUITE-EVENT## null\n",
            '##SUITE-EVENT## {"type": "case_final", "spec": "a.csv", "final_status": "pass"}\n',
            "    trailing output after the malformed line\n",
        )
        fake_popen = make_fake_popen(exit_code=0, stdout_lines=stdout_lines)
        report_dir = os.path.join(self.tmpdir, "report2")
        with patch("runner_app.suite_worker.subprocess.Popen", side_effect=fake_popen):
            worker = SuiteRunWorker(repo_root=self.tmpdir)
            worker.start(["a.csv"], 0, 5, 0, report_dir)
            events = self._drain(worker, {SuiteRunEvent.DONE})

        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            SuiteRunEvent.STARTED,
            SuiteRunEvent.ROUND_STARTED,
            SuiteRunEvent.OUTPUT,  # the malformed "##SUITE-EVENT## null" line
            SuiteRunEvent.CASE_FINAL,
            SuiteRunEvent.OUTPUT,  # "    trailing output after the malformed line"
            SuiteRunEvent.DONE,
        ])


if __name__ == "__main__":
    unittest.main()
