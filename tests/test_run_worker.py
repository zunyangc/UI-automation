"""Tests for runner_app.run_worker: sequential ordering, exit-code -> status
mapping, and Stop Queue cancelling only not-yet-started items.

`subprocess.Popen` is mocked throughout -- these tests never invoke a real
`run.ps1`/PowerShell process.
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from runner_app.run_worker import RunEvent, RunWorker  # noqa: E402


class FakeTestCase:
    def __init__(self, path, name):
        self.path = path
        self.display_name = name


def make_fake_popen(exit_code=0, stdout_lines=("hello\n",), block_event=None):
    """Build a fake subprocess.Popen replacement.

    If `block_event` is given, stdout iteration waits for it to be set
    before yielding -- used to hold the worker thread "mid-run" so a test
    can exercise Stop Queue against still-pending items.
    """
    def _factory(cmd, **kwargs):
        if block_event is not None:
            block_event.wait(timeout=5)

        class FakeProc:
            def __init__(self):
                self.stdout = iter(stdout_lines)
                self.stderr = io.StringIO("")
                self.returncode = exit_code
                self.pid = 4242

            def wait(self):
                return self.returncode

            def poll(self):
                return self.returncode

        return FakeProc()
    return _factory


def make_blocking_stdout_popen(exit_code=0, block_event=None):
    """Fake Popen whose stdout iteration blocks (simulating a genuinely
    in-flight process) until `block_event` is set -- used to exercise
    `stop_current()` against a process that hasn't naturally exited yet.
    """
    class FakeProc:
        def __init__(self):
            self.pid = 4242
            self.returncode = None
            self.stderr = io.StringIO("")

        @property
        def stdout(self):
            if block_event is not None:
                block_event.wait(timeout=5)
            return iter(())

        def poll(self):
            return self.returncode

        def wait(self):
            self.returncode = exit_code
            return self.returncode

    def _factory(cmd, **kwargs):
        return FakeProc()
    return _factory


class RunWorkerSequentialTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _drain_events(self, worker, expected_done_count, timeout=5):
        done = []
        deadline = time.time() + timeout
        while len(done) < expected_done_count and time.time() < deadline:
            try:
                event = worker.events.get(timeout=0.2)
            except Exception:
                continue
            if event.kind == RunEvent.DONE:
                done.append(event)
        return done

    def test_runs_sequentially_in_order_with_exit_code_mapping(self):
        cases = [
            FakeTestCase(os.path.join(self.tmpdir, f"c{i}.csv"), f"case{i}")
            for i in range(3)
        ]
        exit_codes = {cases[0].path: 0, cases[1].path: 1, cases[2].path: 2}

        def fake_popen(cmd, **kwargs):
            # cmd ends with [..., rel_spec]; rel_spec matches the basename
            # of one of our fake case paths.
            rel_spec = cmd[-1]
            code = None
            for tc in cases:
                if os.path.basename(tc.path) == os.path.basename(rel_spec):
                    code = exit_codes[tc.path]
            return make_fake_popen(exit_code=code, stdout_lines=("ok\n",))(cmd, **kwargs)

        with patch("runner_app.run_worker.subprocess.Popen", side_effect=fake_popen):
            worker = RunWorker(repo_root=self.tmpdir, results_dir=os.path.join(self.tmpdir, "results"))
            worker.enqueue(cases)
            done_events = self._drain_events(worker, expected_done_count=3)

        self.assertEqual(len(done_events), 3)
        self.assertEqual([e.data["name"] for e in done_events], ["case0", "case1", "case2"])
        self.assertEqual([e.data["status"] for e in done_events], ["pass", "fail", "error"])

    def test_stop_queue_cancels_only_pending_items(self):
        cases = [
            FakeTestCase(os.path.join(self.tmpdir, f"c{i}.csv"), f"case{i}")
            for i in range(3)
        ]
        block_event = threading.Event()
        fake_popen = make_fake_popen(exit_code=0, stdout_lines=("ok\n",), block_event=block_event)

        with patch("runner_app.run_worker.subprocess.Popen", side_effect=fake_popen):
            worker = RunWorker(repo_root=self.tmpdir, results_dir=os.path.join(self.tmpdir, "results"))
            worker.enqueue(cases)

            # Wait until the worker has picked up case0 (RUNNING event) so
            # it is genuinely "in flight" and no longer cancellable, then
            # cancel the rest while case0's fake process is still blocked.
            deadline = time.time() + 5
            saw_running = False
            while time.time() < deadline:
                event = worker.events.get(timeout=0.2)
                if event.kind == RunEvent.RUNNING:
                    saw_running = True
                    break
            self.assertTrue(saw_running, "expected case0 to start running")

            worker.stop_queue()
            block_event.set()  # let case0 finish

            cancelled = []
            done = []
            deadline = time.time() + 5
            while time.time() < deadline and len(done) < 1:
                event = worker.events.get(timeout=0.2)
                if event.kind == RunEvent.CANCELLED:
                    cancelled.append(event)
                elif event.kind == RunEvent.DONE:
                    done.append(event)

        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].data["name"], "case0")
        self.assertEqual({e.data["name"] for e in cancelled}, {"case1", "case2"})

    def test_stop_current_kills_in_flight_process_and_marks_cancelled(self):
        case = FakeTestCase(os.path.join(self.tmpdir, "c0.csv"), "case0")
        block_event = threading.Event()
        fake_popen = make_blocking_stdout_popen(exit_code=0, block_event=block_event)

        with patch("runner_app.run_worker.subprocess.Popen", side_effect=fake_popen), \
                patch("runner_app.run_worker.subprocess.run") as mock_run:
            # Simulate taskkill actually terminating the process: unblock
            # its stdout iteration so the worker thread can proceed.
            mock_run.side_effect = lambda *a, **k: block_event.set()

            worker = RunWorker(repo_root=self.tmpdir, results_dir=os.path.join(self.tmpdir, "results"))
            worker.enqueue([case])

            deadline = time.time() + 5
            while time.time() < deadline:
                event = worker.events.get(timeout=0.2)
                if event.kind == RunEvent.RUNNING:
                    break

            worker.stop_current()

            done_event = None
            deadline = time.time() + 5
            while time.time() < deadline:
                event = worker.events.get(timeout=0.2)
                if event.kind == RunEvent.DONE:
                    done_event = event
                    break

        self.assertIsNotNone(done_event)
        self.assertEqual(done_event.data["status"], "cancelled")
        mock_run.assert_called_once()
        killed_cmd = mock_run.call_args[0][0]
        self.assertIn("taskkill", killed_cmd)
        self.assertIn("4242", [str(a) for a in killed_cmd])

    def test_stop_current_is_noop_when_nothing_running(self):
        worker = RunWorker(repo_root=self.tmpdir, results_dir=os.path.join(self.tmpdir, "results"))
        with patch("runner_app.run_worker.subprocess.run") as mock_run:
            worker.stop_current()  # should not raise or call taskkill
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
