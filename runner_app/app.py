"""Tkinter entry point for the in-DevBox test runner GUI.

Launch via `run_gui.ps1` (repo root), which runs:
    uv run python -m runner_app.app

Three tabs:
  * Run      -- pick test case(s), run them (sequential queue), see live
                status + a streaming log of the case currently running.
  * Results  -- history of past runs (results/*.json), with log tail and
                clickable links to that run's screenshots (opened in the
                OS default image viewer).
  * Settings -- housekeeping (clear results/screenshots) and a link to
                the repository.
"""
import datetime
import os
import queue
import re
import shutil
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import results_store, test_catalog
from .run_worker import RunEvent, RunWorker
from .suite_worker import SuiteRunEvent, SuiteRunWorker

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
SCREENSHOTS_DIR = os.path.join(REPO_ROOT, "screenshots")
RESULT_DIR = os.path.join(REPO_ROOT, "result")
# Results are recorded (and stored on disk) in UTC, but testers are in
# Malaysia/Singapore (UTC+8) -- convert for display in the Results tab only.
DISPLAY_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _format_local(iso_ts):
    """Convert a stored UTC ISO timestamp to "YYYY-MM-DD HH:MM:SS" in UTC+8."""
    if not iso_ts:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts[:19].replace("T", " ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _natural_sort_key(filename):
    """Sort key that treats runs of digits as numbers, e.g. ss_2 before
    ss_10, matching the run_test.py's `ss_1, ss_2, ..., ss_10` capture
    order instead of plain lexicographic ("ss_1, ss_10, ss_11, ss_2...").
    """
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", filename)]
# Visual Studio's default project location -- most test cases create/build
# throwaway projects here, so this is the main place leftover clutter piles up.
REPOS_DIR = os.path.join(os.path.expanduser("~"), "source", "repos")
# The team's fork (origin) can't reach a now-private upstream, so this repo
# link points at upstream/main directly, which testers have direct access to.
REPO_URL = "https://github.com/william051200/UI-automation"

STATUS_COLOR = {
    "queued": "#808080",
    "running": "#1a73e8",
    "pass": "#1e8e3e",
    "fail": "#d93025",
    "error": "#e37400",
    "cancelled": "#808080",
    # Final per-case outcomes a Run Suite (Auto-Retry) invocation can
    # assign to a row (see SuiteRunWorker / RunTab.handle_suite_event) --
    # all treated visually like a failure/warning, consistent with the
    # existing fail/error colors above.
    "stuck": "#d93025",
    "retries_exhausted": "#d93025",
    "timeout": "#e37400",
    "not_run_timeout": "#e37400",
    # Live, in-flight Run Suite (Auto-Retry) states -- a case has failed its
    # current attempt but hasn't been given a terminal status yet (another
    # round will retry it), distinct from the settled "fail"/"error" colors
    # above. See RunTab.handle_suite_event's CASE_ATTEMPT_DONE handling.
    "retrying": "#e37400",
}

# Default suite-run options shown in the "Suite Options..." dialog --
# mirrors run_suite.py's own argparse defaults.
DEFAULT_SUITE_MAX_RETRIES = 2
DEFAULT_SUITE_CASE_TIMEOUT_MIN = 35
DEFAULT_SUITE_SUITE_TIMEOUT_MIN = 0  # disabled

# Max width/height (px) for the Results tab's screenshot thumbnails.
THUMBNAIL_SIZE = 96


class RunRow:
    """One row in the Run tab for a single test case."""

    # Truncate long descriptions so a verbose CSV description can't push the
    # status column off the visible (horizontally non-scrolling) canvas.
    DESC_MAX_CHARS = 90

    def __init__(self, parent, test_case, on_toggle):
        self.test_case = test_case
        self.var = tk.BooleanVar(value=False)
        self.frame = ttk.Frame(parent)
        label_text = test_case.display_name
        if test_case.error:
            label_text += "  [parse error]"
        # The case name lives on the checkbox itself (not a separate label)
        # so keyboard/screen-reader users get an accessible name for the
        # control they're toggling, instead of an unlabeled checkbox next
        # to an unrelated label.
        self.check = ttk.Checkbutton(
            self.frame, text=label_text, variable=self.var, width=42,
            command=lambda: on_toggle(),
        )
        self.check.grid(row=0, column=0, sticky="w")
        # Status sits right after the checkbox/name (not after the
        # description) so it stays visible even when a description is very
        # long.
        self.status_label = ttk.Label(self.frame, text="", width=10, anchor="w")
        self.status_label.grid(row=0, column=1, sticky="w", padx=(4, 4))
        desc = test_case.description or test_case.error or ""
        # If the CSV's internal `# CONFIG name` differs from the filename
        # (common in this repo), keep it visible as a prefix so the two are
        # still easy to cross-reference.
        if test_case.name and test_case.name != test_case.file_stem:
            desc = f"[{test_case.name}] {desc}"
        if len(desc) > self.DESC_MAX_CHARS:
            desc = desc[:self.DESC_MAX_CHARS - 1].rstrip() + "\u2026"
        self.desc_label = ttk.Label(
            self.frame, text=desc, foreground="#555555", width=self.DESC_MAX_CHARS,
        )
        self.desc_label.grid(row=0, column=2, sticky="w", padx=(8, 0))
        if test_case.error:
            self.check.state(["disabled"])

    def set_status(self, status):
        self.status_label.configure(text=status, foreground=STATUS_COLOR.get(status, "black"))

    def matches_filter(self, text):
        if not text:
            return True
        text = text.lower()
        return (text in self.test_case.display_name.lower()
                or text in (self.test_case.name or "").lower()
                or text in (self.test_case.description or "").lower())


class RunTab(ttk.Frame):
    def __init__(self, parent, worker, on_run_started, suite_worker=None, on_suite_done=None):
        super().__init__(parent)
        self.worker = worker
        self.on_run_started = on_run_started
        self.suite_worker = suite_worker
        self.on_suite_done = on_suite_done
        self.rows = {}  # spec_path -> RunRow
        # Specs currently queued or running via the regular RunWorker --
        # used to keep "Run Suite (Auto-Retry)" disabled while a regular
        # queue is active (see §12 of RUN-SUITE-UI-INTEGRATION-SPEC.md:
        # the two run modes are mutually exclusive since both ultimately
        # drive run.ps1 processes that would otherwise fight for
        # mouse/keyboard focus).
        self._active_regular_specs = set()
        self._suite_running = False
        # Session-only memory of the last-used suite options (not
        # persisted to disk) so repeated suite runs don't need re-entry.
        self._suite_options = {
            "max_retries": DEFAULT_SUITE_MAX_RETRIES,
            "case_timeout_min": DEFAULT_SUITE_CASE_TIMEOUT_MIN,
            "suite_timeout_min": DEFAULT_SUITE_SUITE_TIMEOUT_MIN,
        }
        self._last_report_path = None

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(top, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(4, 8))
        ttk.Button(top, text="Select All", command=self._select_all).pack(side="left", padx=2)
        ttk.Button(top, text="Select None", command=self._select_none).pack(side="left", padx=2)

        # Scrollable list of test-case rows -- mouse-wheel driven (like the
        # log panel / Results tree already are), no visible scrollbar to
        # drag. Vertical wheel scrolls up/down; Shift+wheel scrolls
        # sideways for rows wider than the window.
        list_container = ttk.Frame(self)
        list_container.pack(fill="both", expand=True, padx=8)
        canvas = tk.Canvas(list_container, highlightthickness=0)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)

        def _on_vertical_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_horizontal_wheel(event):
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

        # Only active while the cursor is over the list, so wheel scrolling
        # elsewhere (e.g. the log panel, other tabs) isn't hijacked.
        canvas.bind("<Enter>", lambda e: (
            canvas.bind_all("<MouseWheel>", _on_vertical_wheel),
            canvas.bind_all("<Shift-MouseWheel>", _on_horizontal_wheel),
        ))
        canvas.bind("<Leave>", lambda e: (
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Shift-MouseWheel>"),
        ))

        # rel_path -> TestCase, so a suite run's summary.json (which only
        # knows the relative spec paths it was launched with) can be
        # mapped back to the row keyed by absolute path below.
        self._rel_to_tc = {}
        for tc in test_catalog.discover():
            row = RunRow(self.list_frame, tc, self._noop)
            row.frame.pack(fill="x", anchor="w", pady=1)
            self.rows[tc.path] = row
            self._rel_to_tc[tc.rel_path] = tc

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=4)
        self.btn_run_selected = ttk.Button(btns, text="Run Selected", command=self._run_selected)
        self.btn_run_selected.pack(side="left", padx=2)
        self.btn_run_all = ttk.Button(btns, text="Run All", command=self._run_all)
        self.btn_run_all.pack(side="left", padx=2)
        self.btn_run_failed = ttk.Button(btns, text="Run Failed", command=self._run_failed)
        self.btn_run_failed.pack(side="left", padx=2)
        ttk.Button(btns, text="Stop Current Run", command=self._stop_current).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop Queue", command=self._stop_queue).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop All", command=self._stop_all).pack(side="left", padx=2)

        suite_btns = ttk.Frame(self)
        suite_btns.pack(fill="x", padx=8, pady=(0, 4))
        self.btn_run_suite = ttk.Button(
            suite_btns, text="Run Suite (Auto-Retry)", command=self._run_suite)
        self.btn_run_suite.pack(side="left", padx=2)
        ttk.Button(suite_btns, text="Suite Options...", command=self._open_suite_options).pack(
            side="left", padx=2)
        self.btn_stop_suite = ttk.Button(
            suite_btns, text="Stop Suite Run", command=self._stop_suite, state="disabled")
        self.btn_stop_suite.pack(side="left", padx=2)
        self.btn_open_suite_report = ttk.Button(
            suite_btns, text="Open Suite Report", command=self._open_suite_report, state="disabled")
        self.btn_open_suite_report.pack(side="left", padx=2)

        ttk.Label(self, text="Log (currently running case / suite):").pack(anchor="w", padx=8)
        self.log_text = tk.Text(self, height=10, state="disabled", wrap="none")
        self.log_text.pack(fill="both", expand=False, padx=8, pady=(0, 8))

    def _noop(self):
        pass

    def _apply_filter(self):
        text = self.filter_var.get()
        for row in self.rows.values():
            visible = row.matches_filter(text)
            if visible:
                row.frame.pack(fill="x", anchor="w", pady=1)
            else:
                row.frame.pack_forget()

    def _select_all(self):
        # Selects every currently *visible* row and explicitly clears every
        # hidden one -- otherwise a row selected before a filter change
        # would stay selected (and get queued by Run All) even though it's
        # no longer shown.
        for row in self.rows.values():
            visible = row.frame.winfo_ismapped()
            row.var.set(visible and not row.test_case.error)

    def _select_none(self):
        for row in self.rows.values():
            row.var.set(False)

    def _selected_cases(self):
        return [row.test_case for row in self.rows.values() if row.var.get()]

    def _run_selected(self):
        if self._suite_running:
            return
        cases = self._selected_cases()
        if not cases:
            return
        for tc in cases:
            self.rows[tc.path].set_status(RunEvent.QUEUED)
            # Mark these specs active *before* handing them to the worker:
            # enqueue() only posts a QUEUED event asynchronously (consumed
            # later by handle_event() via the GUI's poll loop), so without
            # this, a suite run could slip in and start concurrently during
            # that window, defeating mutual exclusion -- see
            # _refresh_button_states().
            self._active_regular_specs.add(tc.path)
        self._refresh_button_states()
        self.worker.enqueue(cases)
        self.on_run_started()

    def _run_all(self):
        self._select_all()
        self._run_selected()

    def _failed_case_paths(self):
        """Absolute paths of every test case whose most recent recorded run
        was a real failure (`fail`/`error` -- not `cancelled`, which means
        the tester stopped it, not that the case broke).
        """
        latest_status = {}
        for run in results_store.list_runs():  # newest first
            spec_path = run.get("spec_path")
            if spec_path and spec_path not in latest_status:
                latest_status[spec_path] = run.get("status")
        return {
            tc.path for tc in (row.test_case for row in self.rows.values())
            if latest_status.get(tc.rel_path) in ("fail", "error")
        }

    def _run_failed(self):
        failed_paths = self._failed_case_paths()
        if not failed_paths:
            return
        for path, row in self.rows.items():
            row.var.set(path in failed_paths)
        self._run_selected()

    def _stop_queue(self):
        self.worker.stop_queue()

    def _stop_current(self):
        self.worker.stop_current()

    def _stop_all(self):
        self.worker.stop_all()

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def reset_status(self):
        """Clear every row's live status pill and the log panel.

        Called after "Clear Results" so the Run tab doesn't keep showing
        pass/fail/log output for runs whose history was just wiped.
        """
        for row in self.rows.values():
            row.set_status("")
        self._clear_log()

    def handle_event(self, event):
        row = self.rows.get(event.spec_path)
        if event.kind == RunEvent.QUEUED and row:
            row.set_status("queued")
            self._active_regular_specs.add(event.spec_path)
            self._refresh_button_states()
        elif event.kind == RunEvent.RUNNING and row:
            row.set_status("running")
            self._clear_log()
            self._active_regular_specs.add(event.spec_path)
            self._refresh_button_states()
        elif event.kind == RunEvent.OUTPUT:
            self._append_log(event.data.get("line", ""))
        elif event.kind == RunEvent.DONE and row:
            row.set_status(event.data.get("status", "error"))
            self._active_regular_specs.discard(event.spec_path)
            self._refresh_button_states()
        elif event.kind == RunEvent.CANCELLED and row:
            row.set_status("cancelled")
            self._active_regular_specs.discard(event.spec_path)
            self._refresh_button_states()

    # -- Run Suite (Auto-Retry) --------------------------------------

    def _refresh_button_states(self):
        """Keep the regular queue buttons and the suite-run buttons
        mutually exclusive: both ultimately drive `run.ps1` processes
        that would otherwise fight over mouse/keyboard focus on one
        desktop session (see RUN-SUITE-UI-INTEGRATION-SPEC.md §12,
        resolved as "mutually exclusive" for this implementation).
        """
        regular_busy = bool(self._active_regular_specs)
        for btn in (self.btn_run_selected, self.btn_run_all, self.btn_run_failed):
            btn.configure(state="disabled" if self._suite_running else "normal")
        self.btn_run_suite.configure(
            state="disabled" if (regular_busy or self._suite_running) else "normal")
        self.btn_stop_suite.configure(state="normal" if self._suite_running else "disabled")
        self.btn_open_suite_report.configure(
            state="normal" if self._last_report_path else "disabled")

    def _open_suite_options(self):
        """Modal dialog to edit --max-retries / --case-timeout-min /
        --suite-timeout-min before launching a suite run. Values are kept
        in `self._suite_options` for the lifetime of this GUI session
        only (not persisted to disk) -- see RUN-SUITE-UI-INTEGRATION-SPEC.md
        §12, Q2.
        """
        dialog = tk.Toplevel(self)
        dialog.title("Suite Options")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        dialog.grab_set()

        fields = (
            ("max_retries", "Max retries (additional attempts per case):"),
            ("case_timeout_min", "Per-case timeout (minutes):"),
            ("suite_timeout_min", "Overall suite timeout (minutes, 0 = disabled):"),
        )
        vars_ = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(dialog, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=6)
            var = tk.StringVar(value=str(self._suite_options[key]))
            vars_[key] = var
            ttk.Entry(dialog, textvariable=var, width=10).grid(
                row=i, column=1, sticky="w", padx=(0, 8), pady=6)

        def _save():
            try:
                max_retries = int(vars_["max_retries"].get())
                case_timeout_min = float(vars_["case_timeout_min"].get())
                suite_timeout_min = float(vars_["suite_timeout_min"].get())
                if max_retries < 0 or case_timeout_min <= 0 or suite_timeout_min < 0:
                    raise ValueError("values must be non-negative (timeouts must be > 0)")
            except ValueError as e:
                messagebox.showerror("Suite Options", f"Invalid value: {e}", parent=dialog)
                return
            self._suite_options = {
                "max_retries": max_retries,
                "case_timeout_min": case_timeout_min,
                "suite_timeout_min": suite_timeout_min,
            }
            dialog.destroy()

        btn_row = ttk.Frame(dialog)
        btn_row.grid(row=len(fields), column=0, columnspan=2, pady=(4, 8))
        ttk.Button(btn_row, text="Save", command=_save).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        dialog.bind("<Return>", lambda e: _save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

    def _run_suite(self):
        if self.suite_worker is None or self._suite_running or self._active_regular_specs:
            return
        cases = self._selected_cases()
        if not cases:
            return
        spec_paths = [tc.rel_path for tc in cases]
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        report_dir = os.path.join(RESULT_DIR, f"gui-suite-{ts}")
        for tc in cases:
            self.rows[tc.path].set_status("queued")
        started = self.suite_worker.start(
            spec_paths,
            self._suite_options["max_retries"],
            self._suite_options["case_timeout_min"],
            self._suite_options["suite_timeout_min"],
            report_dir,
        )
        if not started:
            return
        self._suite_running = True
        self._refresh_button_states()

    def _stop_suite(self):
        if self.suite_worker is not None:
            self.suite_worker.stop()

    def _open_suite_report(self):
        if self._last_report_path and os.path.isfile(self._last_report_path):
            webbrowser.open(f"file:///{self._last_report_path}")
        else:
            messagebox.showinfo("Open Suite Report", "No suite report is available yet.")

    def handle_suite_event(self, event):
        if event.kind == SuiteRunEvent.STARTED:
            self._clear_log()
            self._append_log(f"=== Suite run started -- report: {event.data.get('report_dir')} ===\n")
            return
        if event.kind == SuiteRunEvent.OUTPUT:
            self._append_log(event.data.get("line", ""))
            return
        if event.kind == SuiteRunEvent.ROUND_STARTED:
            # Only the cases still eligible for another attempt go back to
            # "queued" -- cases that already settled (pass/stuck/retries
            # exhausted) in an earlier round keep their final status/color
            # instead of being reset, so the pill always shows "still being
            # retried" vs. "already done".
            for rel in event.data.get("pending", []):
                tc = self._rel_to_tc.get(rel)
                if tc is not None and tc.path in self.rows:
                    self.rows[tc.path].set_status("queued")
            return
        if event.kind == SuiteRunEvent.CASE_RUNNING:
            tc = self._rel_to_tc.get(event.data.get("spec"))
            if tc is not None and tc.path in self.rows:
                self.rows[tc.path].set_status("running")
            return
        if event.kind == SuiteRunEvent.CASE_ATTEMPT_DONE:
            # A non-pass attempt only means "retrying" here -- the
            # subsequent CASE_FINAL event (if any) overrides this with the
            # real terminal status (stuck/retries_exhausted/etc.); if the
            # case is going to be retried instead, it stays "retrying"
            # until its next CASE_RUNNING flips it back to "running".
            status = event.data.get("status")
            if status != "pass":
                tc = self._rel_to_tc.get(event.data.get("spec"))
                if tc is not None and tc.path in self.rows:
                    self.rows[tc.path].set_status("retrying")
            return
        if event.kind == SuiteRunEvent.CASE_FINAL:
            tc = self._rel_to_tc.get(event.data.get("spec"))
            if tc is not None and tc.path in self.rows:
                self.rows[tc.path].set_status(event.data.get("final_status", "error"))
            return
        # DONE / CANCELLED: apply each case's final status from
        # summary.json (if it was written) and release the mutual-
        # exclusion lock against the regular run queue.
        self._suite_running = False
        summary = event.data.get("summary")
        if summary:
            for case in summary.get("cases", []):
                tc = self._rel_to_tc.get(case.get("spec"))
                if tc is not None and tc.path in self.rows:
                    self.rows[tc.path].set_status(case.get("final_status", "error"))
            counts = summary.get("counts", {})
            counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            self._append_log(f"\n=== Suite finished ({counts_str}) ===\n")
        else:
            self._append_log("\n=== Suite run stopped -- no report was generated ===\n")
        report_path = event.data.get("report_path")
        if report_path and os.path.isfile(report_path):
            self._last_report_path = report_path
            if self.on_suite_done:
                self.on_suite_done(report_path)
        self._refresh_button_states()


class ResultsTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(top, text="Open Screenshots Folder", command=self._open_screenshots).pack(side="left", padx=4)
        ttk.Button(top, text="Download Failed Report", command=self._download_failed_report).pack(side="left", padx=4)

        # "Fail" covers fail/error outcomes only -- "Cancelled" (a stopped
        # run, not a real failure) gets its own option instead of being
        # lumped in.
        ttk.Label(top, text="Filter:").pack(side="left", padx=(12, 2))
        self.filter_var = tk.StringVar(value="All")
        self.filter_combo = ttk.Combobox(
            top, textvariable=self.filter_var,
            values=("All", "Pass", "Fail", "Cancelled"),
            state="readonly", width=10,
        )
        self.filter_combo.pack(side="left")
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self._render_tree())

        columns = ("name", "started_at", "status", "duration")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for col, label, width in (
            ("name", "Test case", 260), ("started_at", "Started (UTC+8)", 170),
            ("status", "Status", 80), ("duration", "Duration (s)", 100),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        # Color status text the same way the Run tab does (green pass, red
        # fail, etc.) so triage is just as quick from the history view.
        for status, color in STATUS_COLOR.items():
            self.tree.tag_configure(status, foreground=color)
        self.tree.pack(fill="x", padx=8)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected())

        detail = ttk.PanedWindow(self, orient="vertical")
        detail.pack(fill="both", expand=True, padx=8, pady=8)
        self.detail_text = tk.Text(detail, height=8, wrap="none")
        detail.add(self.detail_text, weight=1)

        # Screenshots for the selected run are listed as clickable links
        # (filename only) rather than rendered inline -- click one to open
        # it in the OS default image viewer. The list is mouse-wheel
        # scrollable (no visible scrollbar, matching the Run tab's list)
        # since a run can have more screenshots than fit in this pane --
        # without scrolling, the extra links were simply clipped off.
        shots_container = ttk.Frame(detail)
        detail.add(shots_container, weight=1)
        ttk.Label(shots_container, text="Screenshots:").pack(anchor="w", padx=4, pady=(4, 0))
        shots_canvas = tk.Canvas(shots_container, highlightthickness=0)
        self.shots_frame = ttk.Frame(shots_canvas)
        self.shots_frame.bind(
            "<Configure>",
            lambda e: shots_canvas.configure(scrollregion=shots_canvas.bbox("all")),
        )
        shots_canvas.create_window((0, 0), window=self.shots_frame, anchor="nw")
        shots_canvas.pack(anchor="w", fill="both", expand=True, padx=4, pady=4)

        def _on_shots_wheel(event):
            shots_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # Only active while hovering the screenshot list, so it doesn't
        # steal wheel scrolling from the rest of the Results tab.
        shots_canvas.bind("<Enter>", lambda e: shots_canvas.bind_all("<MouseWheel>", _on_shots_wheel))
        shots_canvas.bind("<Leave>", lambda e: shots_canvas.unbind_all("<MouseWheel>"))

        self._runs = []
        self._thumbnails = []
        self.refresh()

    def refresh(self):
        self._runs = results_store.list_runs()
        self._render_tree()

    def _render_tree(self):
        self.tree.delete(*self.tree.get_children())
        # The previously-selected row's detail pane and screenshot links
        # would otherwise keep pointing at a run/screenshots that may no
        # longer exist (e.g. after Clear Results/Clear Screenshots, or now
        # after switching the status filter to something that excludes it).
        self.detail_text.delete("1.0", "end")
        for child in self.shots_frame.winfo_children():
            child.destroy()
        self._thumbnails = []
        filt = self.filter_var.get()
        for i, run in enumerate(self._runs):
            status = run.get("status")
            if filt == "Pass" and status != "pass":
                continue
            if filt == "Fail" and status not in ("fail", "error"):
                continue
            if filt == "Cancelled" and status != "cancelled":
                continue
            self.tree.insert("", "end", iid=str(i), values=(
                run.get("name"), _format_local(run.get("started_at", "")),
                status, f"{run.get('duration_seconds', 0):.1f}",
            ), tags=(status,) if status in STATUS_COLOR else ())

    def _selected_run(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._runs[int(sel[0])]

    def _show_selected(self):
        run = self._selected_run()
        if not run:
            return
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("end", f"spec: {run.get('spec_path')}\n")
        self.detail_text.insert("end", f"screenshot_dir: {run.get('screenshot_dir')}\n\n")
        self.detail_text.insert("end", "--- stdout tail ---\n")
        self.detail_text.insert("end", run.get("stdout_tail", ""))
        if run.get("stderr_tail"):
            self.detail_text.insert("end", "\n--- stderr tail ---\n")
            self.detail_text.insert("end", run.get("stderr_tail", ""))

        for child in self.shots_frame.winfo_children():
            child.destroy()
        # Keep references alive -- Tkinter drops a PhotoImage as soon as
        # nothing in Python still holds it, even while a Label displays it.
        self._thumbnails = []
        shot_dir = run.get("screenshot_dir")
        if shot_dir and os.path.isdir(shot_dir):
            pngs = sorted(
                (f for f in os.listdir(shot_dir) if f.lower().endswith(".png")),
                key=_natural_sort_key,
            )
            cols = 4
            for i, name in enumerate(pngs):
                full_path = os.path.join(shot_dir, name)
                cell = ttk.Frame(self.shots_frame)
                cell.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="n")
                thumb = self._make_thumbnail(full_path)
                if thumb is not None:
                    self._thumbnails.append(thumb)
                    widget = tk.Label(cell, image=thumb, cursor="hand2", relief="solid", borderwidth=1)
                else:
                    # Fall back to a plain link if the PNG can't be decoded.
                    widget = ttk.Label(cell, text="(preview unavailable)",
                                        foreground="#1a73e8", cursor="hand2")
                widget.pack()
                widget.bind("<Button-1>", lambda e, p=full_path: self._open_image(p))
                caption = ttk.Label(cell, text=name, wraplength=THUMBNAIL_SIZE)
                caption.pack()
                caption.bind("<Button-1>", lambda e, p=full_path: self._open_image(p))
        else:
            ttk.Label(self.shots_frame, text="(no screenshots for this run)",
                      foreground="#808080").pack(anchor="w")

    @staticmethod
    def _make_thumbnail(path):
        """Downscale a PNG to fit THUMBNAIL_SIZE, preserving aspect ratio.

        Uses Tk's built-in PhotoImage (no Pillow dependency) -- subsample()
        only supports integer factors, which is a close-enough thumbnail
        for quick triage, not pixel-perfect scaling.
        """
        try:
            img = tk.PhotoImage(file=path)
        except tk.TclError:
            return None
        factor = max(1, max(img.width(), img.height()) // THUMBNAIL_SIZE)
        return img.subsample(factor, factor) if factor > 1 else img

    def _open_image(self, path):
        try:
            os.startfile(path)
        except OSError:
            pass

    def _open_screenshots(self):
        run = self._selected_run()
        shot_dir = run.get("screenshot_dir") if run else None
        if shot_dir and os.path.isdir(shot_dir):
            subprocess.Popen(["explorer", os.path.abspath(shot_dir)])

    def _download_failed_report(self):
        """Save a shareable zip: summary.txt (one "Failed at <case>,
        failed message is <msg>." line per run) plus each run's failure
        screenshots. An explicit selection in the tree is exported as-is
        only when it actually is a fail/error run; otherwise (nothing
        selected, or the selected row is a pass/cancelled run -- which
        did not fail and has no failure screenshots) every recorded
        fail/error run is bundled instead (cancelled runs are excluded
        from that fallback too -- stopping a run isn't the same as it
        failing), so the button can never produce a summary.txt claiming
        "Failed at ..." for a run that did not fail.
        """
        selected = self._selected_run()
        now_str = datetime.datetime.now(DISPLAY_TZ).strftime("%Y%m%d_%H%M%S")
        if selected is not None and selected.get("status") in ("fail", "error"):
            runs = [selected]
            case_part = results_store._safe_filename_part(str(selected.get("name") or "case"))
            default_name = f"failed-report-{case_part}-{now_str}.zip"
        else:
            runs = [r for r in self._runs if r.get("status") in ("fail", "error")]
            default_name = f"failed-report-{now_str}.zip"

        if not runs:
            messagebox.showinfo("Download Failed Report", "No failed runs to export.")
            return

        zip_path = filedialog.asksaveasfilename(
            title="Save Failed Report", defaultextension=".zip",
            filetypes=[("Zip archive", "*.zip")], initialfile=default_name,
        )
        if not zip_path:
            return  # user cancelled the dialog

        try:
            results_store.write_failure_report_zip(runs, zip_path)
        except OSError as e:
            messagebox.showerror("Download Failed Report", f"Failed to write report: {e}")
            return
        messagebox.showinfo("Download Failed Report", f"Saved to:\n{zip_path}")


class SettingsTab(ttk.Frame):
    """Housekeeping: wipe old run history / screenshots / repos, link to the repo."""

    _BTN_WIDTH = 18  # same width for all three cleanup buttons

    def __init__(self, parent, on_results_cleared=None, on_screenshots_cleared=None,
                 get_last_suite_report=None):
        super().__init__(parent)
        self.on_results_cleared = on_results_cleared
        self.on_screenshots_cleared = on_screenshots_cleared
        self.get_last_suite_report = get_last_suite_report

        style = ttk.Style(self)
        # Default ttk button look, just with red text, per user request.
        style.configure("Danger.TButton", foreground="#d93025")

        cleanup = ttk.LabelFrame(self, text="Cleanup (irreversible -- use with care)")
        cleanup.pack(fill="x", padx=8, pady=8)

        row1 = ttk.Frame(cleanup)
        row1.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(row1, text=f"deletes all files under {RESULTS_DIR}",
                  foreground="#808080").pack(side="left")
        ttk.Button(row1, text="Clear Results", command=self._clear_results,
                   style="Danger.TButton", width=self._BTN_WIDTH).pack(side="right")

        row2 = ttk.Frame(cleanup)
        row2.pack(fill="x", padx=8, pady=4)
        ttk.Label(row2, text=f"deletes all files under {SCREENSHOTS_DIR}",
                  foreground="#808080").pack(side="left")
        ttk.Button(row2, text="Clear Screenshots", command=self._clear_screenshots,
                   style="Danger.TButton", width=self._BTN_WIDTH).pack(side="right")

        row3 = ttk.Frame(cleanup)
        row3.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Label(row3, text=f"deletes all leftover test-case projects under {REPOS_DIR}",
                  foreground="#808080").pack(side="left")
        ttk.Button(row3, text="Clear All Repos", command=self._clear_all_repos,
                   style="Danger.TButton", width=self._BTN_WIDTH).pack(side="right")

        about = ttk.LabelFrame(self, text="About")
        about.pack(fill="x", padx=8, pady=(0, 8))
        row4 = ttk.Frame(about)
        row4.pack(fill="x", padx=8, pady=8)
        ttk.Label(row4, text="Repository:").pack(side="left")
        link = ttk.Label(row4, text=REPO_URL, foreground="#1a73e8", cursor="hand2")
        link.pack(side="left", padx=(4, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))

        row5 = ttk.Frame(about)
        row5.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row5, text="Last Run Suite (Auto-Retry) report:").pack(side="left")
        self.btn_open_last_suite_report = ttk.Button(
            row5, text="Open Last Suite Report", command=self._open_last_suite_report,
            state="disabled")
        self.btn_open_last_suite_report.pack(side="left", padx=(8, 0))
        self.refresh_suite_report_button()

    def refresh_suite_report_button(self):
        """Enable "Open Last Suite Report" only once a suite run has
        actually produced a report on disk -- called once at startup and
        again by App whenever a suite run finishes, since this tab has no
        other way to learn that `get_last_suite_report()`'s answer changed.
        """
        report_path = self.get_last_suite_report() if self.get_last_suite_report else None
        enabled = bool(report_path and os.path.isfile(report_path))
        self.btn_open_last_suite_report.configure(state="normal" if enabled else "disabled")

    def _open_last_suite_report(self):
        report_path = self.get_last_suite_report() if self.get_last_suite_report else None
        if report_path and os.path.isfile(report_path):
            webbrowser.open(f"file:///{report_path}")
        else:
            messagebox.showinfo(
                "Open Last Suite Report",
                "No suite report is available yet -- run \"Run Suite (Auto-Retry)\" "
                "from the Run tab first.",
            )

    def _confirm_irreversible(self, title, message):
        return messagebox.askyesno(
            title, f"{message}\n\nThis is IRREVERSIBLE and cannot be undone.",
            icon="warning",
        )

    def _clear_directory(self, title, directory, confirm_message, on_cleared=None):
        """Shared implementation for the three "Clear ..." buttons.

        Confirms (with a warning dialog), wipes every file/subfolder under
        `directory` (recreating the now-empty directory afterwards so the
        app keeps working without a restart), then reports success -- or,
        if any item couldn't be deleted (e.g. locked/in-use file), reports
        that instead of falsely claiming everything was cleared.
        """
        if not os.path.isdir(directory):
            messagebox.showinfo(title, "No such directory -- nothing to clear.")
            return
        if not self._confirm_irreversible(title, confirm_message):
            return
        errors = []

        def _on_error(_func, path, exc_info):
            errors.append(f"{path}: {exc_info[1]}")

        shutil.rmtree(directory, onerror=_on_error)
        os.makedirs(directory, exist_ok=True)
        if on_cleared:
            on_cleared()
        if errors:
            preview = "\n".join(errors[:10])
            if len(errors) > 10:
                preview += f"\n...and {len(errors) - 10} more"
            messagebox.showwarning(
                title,
                "Some items could not be deleted (in use or permission "
                f"denied) and may still remain:\n{preview}",
            )
        else:
            messagebox.showinfo(title, "Cleared.")

    def _clear_results(self):
        self._clear_directory(
            "Clear Results", RESULTS_DIR,
            f"Delete all run history under:\n{RESULTS_DIR}",
            on_cleared=self.on_results_cleared,
        )

    def _clear_screenshots(self):
        self._clear_directory(
            "Clear Screenshots", SCREENSHOTS_DIR,
            f"Delete all screenshots under:\n{SCREENSHOTS_DIR}",
            on_cleared=self.on_screenshots_cleared,
        )

    def _clear_all_repos(self):
        self._clear_directory(
            "Clear All Repos", REPOS_DIR,
            f"Delete ALL projects under:\n{REPOS_DIR}\n\n"
            "This removes every leftover project created by test cases "
            "(Visual Studio solutions, console apps, etc.) in that folder.",
        )


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root)
        self.root = root
        self.worker = RunWorker(repo_root=REPO_ROOT)
        self.suite_worker = SuiteRunWorker(repo_root=REPO_ROOT)
        self._last_suite_report_path = None
        self.pack(fill="both", expand=True)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.results_tab = ResultsTab(notebook)
        self.run_tab = RunTab(
            notebook, self.worker, on_run_started=self._noop,
            suite_worker=self.suite_worker, on_suite_done=self._on_suite_done,
        )
        self.settings_tab = SettingsTab(
            notebook, on_results_cleared=self._on_results_cleared,
            on_screenshots_cleared=self.results_tab.refresh,
            get_last_suite_report=lambda: self._last_suite_report_path,
        )
        notebook.add(self.run_tab, text="Run")
        notebook.add(self.results_tab, text="Results")
        notebook.add(self.settings_tab, text="Settings")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_events()

    def _on_results_cleared(self):
        self.results_tab.refresh()
        self.run_tab.reset_status()

    def _on_suite_done(self, report_path):
        self._last_suite_report_path = report_path
        self.settings_tab.refresh_suite_report_button()

    def _noop(self):
        pass

    def on_close(self):
        """Make sure closing the window can't leave an orphaned run.ps1 /
        run_suite.py process tree behind: cancel anything still queued and
        forcibly kill the in-flight process(es) (if any) before the app
        exits. The worker threads are daemons, so they never get to run
        cleanup code on interpreter exit -- this has to happen here
        instead.
        """
        self.worker.stop_all()
        self.suite_worker.stop()
        self.root.destroy()

    def _poll_events(self):
        try:
            while True:
                event = self.worker.events.get_nowait()
                self.run_tab.handle_event(event)
                if event.kind == RunEvent.DONE:
                    self.results_tab.refresh()
        except queue.Empty:
            pass
        try:
            while True:
                event = self.suite_worker.events.get_nowait()
                self.run_tab.handle_suite_event(event)
                if event.kind in (SuiteRunEvent.DONE, SuiteRunEvent.CANCELLED):
                    self.results_tab.refresh()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)


def main():
    root = tk.Tk()
    root.title("UI-automation Runner")

    # 900x700 is the preferred default (this repo's earlier GUI size), but
    # cap it to whatever actually fits on a smaller DevBox display -- with
    # a usable floor -- instead of opening a window bigger than the screen.
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    width = max(700, min(900, screen_w - 100))
    height = max(500, min(700, screen_h - 120))
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(700, 500)

    App(root)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
