"""Tests for the Run Suite (Auto-Retry) integration in runner_app.app's
RunTab: launching via the (mocked) SuiteRunWorker, button mutual-exclusion
with the regular run queue, and applying a finished suite's per-case
final status back onto the row list.

Runs a real (but hidden) Tk root -- Tkinter works headlessly on Windows
without a virtual display -- so these exercise the actual RunTab
widget/state code, not a reimplementation of it.
"""
import os
import sys
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from runner_app import app as app_module  # noqa: E402
from runner_app.run_worker import RunEvent  # noqa: E402
from runner_app.suite_worker import SuiteRunEvent  # noqa: E402


class FakeTestCase:
    def __init__(self, path, rel_path):
        self.path = path
        self._rel_path = rel_path
        self.display_name = os.path.splitext(os.path.basename(path))[0]
        self.file_stem = self.display_name
        self.name = self.display_name
        self.description = ""
        self.error = None

    @property
    def rel_path(self):
        return self._rel_path


class RunTabSuiteIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk not available in this environment: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _make_tab(self, cases, suite_worker=None, on_suite_done=None):
        with patch.object(app_module.test_catalog, "discover", return_value=cases):
            tab = app_module.RunTab(
                self.root, worker=MagicMock(), on_run_started=lambda: None,
                suite_worker=suite_worker, on_suite_done=on_suite_done,
            )
        self.addCleanup(tab.destroy)
        return tab

    def test_run_suite_noop_when_nothing_selected(self):
        suite_worker = MagicMock()
        tab = self._make_tab([FakeTestCase("/a.csv", "test_cases/a.csv")], suite_worker)
        tab._run_suite()
        suite_worker.start.assert_not_called()
        self.assertFalse(tab._suite_running)

    def test_run_suite_launches_with_selected_specs_and_options(self):
        suite_worker = MagicMock()
        suite_worker.start.return_value = True
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker)
        tab.rows[tc.path].var.set(True)

        tab._run_suite()

        suite_worker.start.assert_called_once()
        args, _ = suite_worker.start.call_args
        spec_paths, max_retries, case_timeout, suite_timeout, report_dir = args
        self.assertEqual(list(spec_paths), ["test_cases/a.csv"])
        self.assertEqual(max_retries, app_module.DEFAULT_SUITE_MAX_RETRIES)
        self.assertEqual(case_timeout, app_module.DEFAULT_SUITE_CASE_TIMEOUT_MIN)
        self.assertEqual(suite_timeout, app_module.DEFAULT_SUITE_SUITE_TIMEOUT_MIN)
        self.assertTrue(os.path.basename(report_dir).startswith("gui-suite-"))
        self.assertEqual(os.path.dirname(report_dir), app_module.RESULT_DIR)
        self.assertTrue(tab._suite_running)
        self.assertEqual(tab.rows[tc.path].status_label.cget("text"), "queued")

    def test_run_suite_disabled_while_regular_queue_active(self):
        suite_worker = MagicMock()
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker)
        tab.rows[tc.path].var.set(True)

        # Simulate a regular RunWorker event marking this spec as queued.
        tab.handle_event(RunEvent(RunEvent.QUEUED, tc.path))
        self.assertEqual(str(tab.btn_run_suite["state"]), "disabled")

        tab._run_suite()
        suite_worker.start.assert_not_called()

        # Once the regular run finishes, the suite button is re-enabled.
        tab.handle_event(RunEvent(RunEvent.DONE, tc.path, status="pass"))
        self.assertEqual(str(tab.btn_run_suite["state"]), "normal")

    def test_run_selected_marks_specs_active_synchronously(self):
        # RunWorker.enqueue() only posts a QUEUED event asynchronously
        # (consumed later via the GUI's poll loop) -- _run_selected()
        # itself must mark the spec active and disable Run Suite
        # immediately, in the same call, so a suite run can't slip in
        # and start concurrently during that polling window.
        worker = MagicMock()
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker=MagicMock())
        tab.worker = worker
        tab.rows[tc.path].var.set(True)

        tab._run_selected()

        worker.enqueue.assert_called_once()
        self.assertIn(tc.path, tab._active_regular_specs)
        self.assertEqual(str(tab.btn_run_suite["state"]), "disabled")

    def test_regular_run_buttons_disabled_while_suite_running(self):
        suite_worker = MagicMock()
        suite_worker.start.return_value = True
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker)
        tab.rows[tc.path].var.set(True)

        tab._run_suite()
        self.assertEqual(str(tab.btn_run_selected["state"]), "disabled")
        self.assertEqual(str(tab.btn_run_all["state"]), "disabled")
        self.assertEqual(str(tab.btn_run_failed["state"]), "disabled")
        self.assertEqual(str(tab.btn_stop_suite["state"]), "normal")

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.CANCELLED, report_dir="x", report_path=None, summary=None,
        ))
        self.assertEqual(str(tab.btn_run_selected["state"]), "normal")
        self.assertFalse(tab._suite_running)

    def test_handle_suite_event_round_started_sets_pending_to_queued_without_resetting_settled(self):
        tc_a = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tc_b = FakeTestCase("/abs/b.csv", "test_cases/b.csv")
        tab = self._make_tab([tc_a, tc_b], suite_worker=MagicMock())
        tab.rows[tc_a.path].set_status("pass")  # already settled in an earlier round

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.ROUND_STARTED, round=2, pending=["test_cases/b.csv"],
        ))

        self.assertEqual(tab.rows[tc_a.path].status_label.cget("text"), "pass")
        self.assertEqual(tab.rows[tc_b.path].status_label.cget("text"), "queued")

    def test_handle_suite_event_case_running_sets_row_running(self):
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker=MagicMock())

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.CASE_RUNNING, spec="test_cases/a.csv", attempt_no=1,
        ))

        self.assertEqual(tab.rows[tc.path].status_label.cget("text"), "running")

    def test_handle_suite_event_case_attempt_done_non_pass_sets_retrying(self):
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker=MagicMock())
        tab.rows[tc.path].set_status("running")

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.CASE_ATTEMPT_DONE, spec="test_cases/a.csv", attempt_no=1, status="fail",
        ))

        self.assertEqual(tab.rows[tc.path].status_label.cget("text"), "retrying")

    def test_handle_suite_event_case_attempt_done_pass_does_not_set_retrying(self):
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker=MagicMock())
        tab.rows[tc.path].set_status("running")

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.CASE_ATTEMPT_DONE, spec="test_cases/a.csv", attempt_no=1, status="pass",
        ))

        self.assertEqual(tab.rows[tc.path].status_label.cget("text"), "running")

    def test_handle_suite_event_case_final_sets_terminal_status(self):
        tc = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tab = self._make_tab([tc], suite_worker=MagicMock())
        tab.rows[tc.path].set_status("retrying")

        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.CASE_FINAL, spec="test_cases/a.csv", final_status="stuck",
        ))

        self.assertEqual(tab.rows[tc.path].status_label.cget("text"), "stuck")

    def test_live_round_events_end_to_end_then_final_sweep_matches(self):
        # Simulate a full 2-round suite run purely through handle_suite_event
        # calls (as SuiteRunWorker would post them), then confirm the final
        # DONE summary sweep doesn't contradict what live events already set.
        tc_a = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tc_b = FakeTestCase("/abs/b.csv", "test_cases/b.csv")
        tab = self._make_tab([tc_a, tc_b], suite_worker=MagicMock())
        tab._suite_running = True

        events = [
            SuiteRunEvent(SuiteRunEvent.ROUND_STARTED, round=1,
                          pending=["test_cases/a.csv", "test_cases/b.csv"]),
            SuiteRunEvent(SuiteRunEvent.CASE_RUNNING, spec="test_cases/a.csv", attempt_no=1),
            SuiteRunEvent(SuiteRunEvent.CASE_ATTEMPT_DONE, spec="test_cases/a.csv",
                          attempt_no=1, status="pass"),
            SuiteRunEvent(SuiteRunEvent.CASE_FINAL, spec="test_cases/a.csv", final_status="pass"),
            SuiteRunEvent(SuiteRunEvent.CASE_RUNNING, spec="test_cases/b.csv", attempt_no=1),
            SuiteRunEvent(SuiteRunEvent.CASE_ATTEMPT_DONE, spec="test_cases/b.csv",
                          attempt_no=1, status="fail"),
            SuiteRunEvent(SuiteRunEvent.ROUND_STARTED, round=2, pending=["test_cases/b.csv"]),
            SuiteRunEvent(SuiteRunEvent.CASE_RUNNING, spec="test_cases/b.csv", attempt_no=2),
            SuiteRunEvent(SuiteRunEvent.CASE_ATTEMPT_DONE, spec="test_cases/b.csv",
                          attempt_no=2, status="fail"),
            SuiteRunEvent(SuiteRunEvent.CASE_FINAL, spec="test_cases/b.csv", final_status="stuck"),
        ]
        for event in events:
            tab.handle_suite_event(event)
        # "a" settled pass in round 1 -- round 2's ROUND_STARTED (pending
        # only lists "b") must not have reset it back to queued.
        self.assertEqual(tab.rows[tc_a.path].status_label.cget("text"), "pass")
        self.assertEqual(tab.rows[tc_b.path].status_label.cget("text"), "stuck")

        summary = {
            "counts": {"pass": 1, "stuck": 1},
            "cases": [
                {"spec": "test_cases/a.csv", "final_status": "pass"},
                {"spec": "test_cases/b.csv", "final_status": "stuck"},
            ],
        }
        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.DONE, report_dir="result/x",
            report_path="result/x/report.html", summary=summary,
        ))
        self.assertEqual(tab.rows[tc_a.path].status_label.cget("text"), "pass")
        self.assertEqual(tab.rows[tc_b.path].status_label.cget("text"), "stuck")

    def test_handle_suite_event_applies_final_status_to_rows(self):
        tc_a = FakeTestCase("/abs/a.csv", "test_cases/a.csv")
        tc_b = FakeTestCase("/abs/b.csv", "test_cases/b.csv")
        tab = self._make_tab([tc_a, tc_b], suite_worker=MagicMock())
        tab._suite_running = True

        summary = {
            "counts": {"pass": 1, "stuck": 1},
            "cases": [
                {"spec": "test_cases/a.csv", "final_status": "pass"},
                {"spec": "test_cases/b.csv", "final_status": "stuck"},
            ],
        }
        tab.handle_suite_event(SuiteRunEvent(
            SuiteRunEvent.DONE, report_dir="result/x",
            report_path="result/x/report.html", summary=summary,
        ))

        self.assertEqual(tab.rows[tc_a.path].status_label.cget("text"), "pass")
        self.assertEqual(tab.rows[tc_b.path].status_label.cget("text"), "stuck")
        self.assertFalse(tab._suite_running)
        # report_path doesn't actually exist on disk -> report button stays disabled.
        self.assertEqual(str(tab.btn_open_suite_report["state"]), "disabled")

    def test_handle_suite_event_notifies_on_suite_done_when_report_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report.html")
            with open(report_path, "w") as f:
                f.write("<html></html>")
            called = {}
            tab = self._make_tab(
                [], suite_worker=MagicMock(),
                on_suite_done=lambda p: called.setdefault("path", p),
            )
            tab.handle_suite_event(SuiteRunEvent(
                SuiteRunEvent.DONE, report_dir=tmp, report_path=report_path,
                summary={"counts": {}, "cases": []},
            ))
            self.assertEqual(called.get("path"), report_path)
            self.assertEqual(str(tab.btn_open_suite_report["state"]), "normal")

    def test_suite_options_dialog_updates_options_on_save(self):
        tab = self._make_tab([], suite_worker=MagicMock())
        tab._open_suite_options()
        toplevels = [w for w in tab.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(toplevels), 1)
        dialog = toplevels[0]
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        entries = [w for w in dialog.winfo_children() if isinstance(w, ttk.Entry)]
        self.assertEqual(len(entries), 3)
        for entry, value in zip(entries, ("5", "20", "120")):
            entry.delete(0, "end")
            entry.insert(0, value)
        btn_row = [w for w in dialog.winfo_children() if isinstance(w, ttk.Frame)][0]
        save_btn = btn_row.winfo_children()[0]  # "Save" packed before "Cancel"
        save_btn.invoke()
        self.assertEqual(tab._suite_options, {
            "max_retries": 5, "case_timeout_min": 20.0, "suite_timeout_min": 120.0,
        })

    def test_suite_options_dialog_rejects_invalid_values(self):
        tab = self._make_tab([], suite_worker=MagicMock())
        original = dict(tab._suite_options)
        tab._open_suite_options()
        toplevels = [w for w in tab.winfo_children() if isinstance(w, tk.Toplevel)]
        dialog = toplevels[0]
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        entries = [w for w in dialog.winfo_children() if isinstance(w, ttk.Entry)]
        entries[0].delete(0, "end")
        entries[0].insert(0, "not-a-number")
        btn_row = [w for w in dialog.winfo_children() if isinstance(w, ttk.Frame)][0]
        save_btn = btn_row.winfo_children()[0]
        with patch.object(app_module.messagebox, "showerror"):
            save_btn.invoke()
        # Invalid input leaves options unchanged and the dialog open.
        self.assertEqual(tab._suite_options, original)
        self.assertTrue(dialog.winfo_exists())


class SettingsTabSuiteReportButtonTests(unittest.TestCase):
    """The Settings tab's "Open Last Suite Report" button must reflect
    whether a suite report actually exists on disk, not just be
    permanently clickable (see SettingsTab.refresh_suite_report_button()).
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk not available in this environment: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_button_starts_disabled_when_no_report_exists(self):
        tab = app_module.SettingsTab(self.root, get_last_suite_report=lambda: None)
        self.addCleanup(tab.destroy)
        self.assertEqual(str(tab.btn_open_last_suite_report["state"]), "disabled")

    def test_button_enabled_after_refresh_once_report_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report.html")
            with open(report_path, "w") as f:
                f.write("<html></html>")
            tab = app_module.SettingsTab(self.root, get_last_suite_report=lambda: report_path)
            self.addCleanup(tab.destroy)
            self.assertEqual(str(tab.btn_open_last_suite_report["state"]), "normal")

    def test_button_disabled_when_reported_path_does_not_exist_on_disk(self):
        missing_path = os.path.join(REPO_ROOT, "__no_such_dir__", "report.html")
        tab = app_module.SettingsTab(self.root, get_last_suite_report=lambda: missing_path)
        self.addCleanup(tab.destroy)
        self.assertEqual(str(tab.btn_open_last_suite_report["state"]), "disabled")

    def test_refresh_suite_report_button_reflects_later_change(self):
        state = {"path": None}
        tab = app_module.SettingsTab(self.root, get_last_suite_report=lambda: state["path"])
        self.addCleanup(tab.destroy)
        self.assertEqual(str(tab.btn_open_last_suite_report["state"]), "disabled")

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report.html")
            with open(report_path, "w") as f:
                f.write("<html></html>")
            state["path"] = report_path
            tab.refresh_suite_report_button()
            self.assertEqual(str(tab.btn_open_last_suite_report["state"]), "normal")


if __name__ == "__main__":
    unittest.main()
