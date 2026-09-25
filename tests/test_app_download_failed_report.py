"""Tests for ResultsTab._download_failed_report's run-selection logic in
runner_app.app: an explicit tree selection must only be exported as-is when
it is actually a fail/error run -- selecting a pass/cancelled run must not
produce a "Failed at ..." zip for a run that did not fail.

Runs a real (but hidden) Tk root -- Tkinter works headlessly on Windows
without a virtual display, unlike X11 -- so these exercise the actual
ResultsTab widget/selection code, not a reimplementation of it.
"""
import os
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from runner_app import app as app_module  # noqa: E402


def _make_run(name, status):
    return {
        "name": name, "status": status, "spec_path": f"test_cases/{name}.csv",
        "started_at": "2026-01-01T00:00:00+00:00", "duration_seconds": 1.0,
        "screenshot_dir": None, "stdout_tail": "",
    }


class DownloadFailedReportSelectionTests(unittest.TestCase):
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

    def _make_tab(self, runs):
        with patch.object(app_module.results_store, "list_runs", return_value=[]):
            tab = app_module.ResultsTab(self.root)
        tab._runs = runs
        tab._render_tree()
        return tab

    def _select_row(self, tab, index):
        tab.tree.selection_set(str(index))

    def test_selecting_a_passing_run_falls_back_to_other_failed_runs(self):
        runs = [_make_run("case_pass", "pass"), _make_run("case_fail", "fail")]
        tab = self._make_tab(runs)
        self._select_row(tab, 0)  # select the passing run explicitly

        with patch.object(app_module, "filedialog") as mock_dialog, \
                patch.object(app_module, "messagebox") as mock_box, \
                patch.object(app_module.results_store, "write_failure_report_zip") as mock_write:
            mock_dialog.asksaveasfilename.return_value = os.path.join(REPO_ROOT, "unused.zip")
            tab._download_failed_report()

        mock_write.assert_called_once()
        exported_runs = mock_write.call_args[0][0]
        self.assertEqual([r["name"] for r in exported_runs], ["case_fail"])
        mock_box.showerror.assert_not_called()

    def test_selecting_a_cancelled_run_with_no_failures_shows_message_not_zip(self):
        runs = [_make_run("case_pass", "pass"), _make_run("case_cancelled", "cancelled")]
        tab = self._make_tab(runs)
        self._select_row(tab, 1)  # select the cancelled run explicitly

        with patch.object(app_module, "filedialog") as mock_dialog, \
                patch.object(app_module, "messagebox") as mock_box, \
                patch.object(app_module.results_store, "write_failure_report_zip") as mock_write:
            tab._download_failed_report()

        mock_write.assert_not_called()
        mock_dialog.asksaveasfilename.assert_not_called()
        mock_box.showinfo.assert_called_once()
        self.assertIn("No failed runs", mock_box.showinfo.call_args[0][1])

    def test_selecting_a_failed_run_is_exported_as_is(self):
        runs = [_make_run("case_fail", "fail"), _make_run("case_error", "error")]
        tab = self._make_tab(runs)
        self._select_row(tab, 0)  # select the fail run explicitly

        with patch.object(app_module, "filedialog") as mock_dialog, \
                patch.object(app_module, "messagebox") as mock_box, \
                patch.object(app_module.results_store, "write_failure_report_zip") as mock_write:
            mock_dialog.asksaveasfilename.return_value = os.path.join(REPO_ROOT, "unused.zip")
            tab._download_failed_report()

        mock_write.assert_called_once()
        exported_runs = mock_write.call_args[0][0]
        self.assertEqual([r["name"] for r in exported_runs], ["case_fail"])
        mock_box.showinfo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
