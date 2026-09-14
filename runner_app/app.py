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
import os
import queue
import shutil
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import results_store, test_catalog
from .run_worker import RunEvent, RunWorker

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
SCREENSHOTS_DIR = os.path.join(REPO_ROOT, "screenshots")
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
}


class RunRow:
    """One row in the Run tab for a single test case."""

    # Truncate long descriptions so a verbose CSV description can't push the
    # status column off the visible (horizontally non-scrolling) canvas.
    DESC_MAX_CHARS = 90

    def __init__(self, parent, test_case, on_toggle):
        self.test_case = test_case
        self.var = tk.BooleanVar(value=False)
        self.frame = ttk.Frame(parent)
        self.check = ttk.Checkbutton(
            self.frame, variable=self.var,
            command=lambda: on_toggle(),
        )
        self.check.grid(row=0, column=0, sticky="w")
        # Status sits right after the checkbox (not after the description)
        # so it stays visible even when a description is very long.
        self.status_label = ttk.Label(self.frame, text="", width=10, anchor="w")
        self.status_label.grid(row=0, column=1, sticky="w", padx=(4, 4))
        label_text = test_case.display_name
        if test_case.error:
            label_text += "  [parse error]"
        self.name_label = ttk.Label(self.frame, text=label_text, width=42, anchor="w")
        self.name_label.grid(row=0, column=2, sticky="w")
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
        self.desc_label.grid(row=0, column=3, sticky="w", padx=(8, 0))
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
    def __init__(self, parent, worker, on_run_started):
        super().__init__(parent)
        self.worker = worker
        self.on_run_started = on_run_started
        self.rows = {}  # spec_path -> RunRow

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(top, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(4, 8))
        ttk.Button(top, text="Select All", command=self._select_all).pack(side="left", padx=2)
        ttk.Button(top, text="Select None", command=self._select_none).pack(side="left", padx=2)

        # Scrollable list of test-case rows.
        list_container = ttk.Frame(self)
        list_container.pack(fill="both", expand=True, padx=8)
        canvas = tk.Canvas(list_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for tc in test_catalog.discover():
            row = RunRow(self.list_frame, tc, self._noop)
            row.frame.pack(fill="x", anchor="w", pady=1)
            self.rows[tc.path] = row

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=4)
        ttk.Button(btns, text="Run Selected", command=self._run_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="Run All", command=self._run_all).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop Current Run", command=self._stop_current).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop Queue", command=self._stop_queue).pack(side="left", padx=2)

        ttk.Label(self, text="Log (currently running case):").pack(anchor="w", padx=8)
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
        for row in self.rows.values():
            if row.frame.winfo_ismapped() and not row.test_case.error:
                row.var.set(True)

    def _select_none(self):
        for row in self.rows.values():
            row.var.set(False)

    def _selected_cases(self):
        return [row.test_case for row in self.rows.values() if row.var.get()]

    def _run_selected(self):
        cases = self._selected_cases()
        if not cases:
            return
        for tc in cases:
            self.rows[tc.path].set_status(RunEvent.QUEUED)
        self.worker.enqueue(cases)
        self.on_run_started()

    def _run_all(self):
        self._select_all()
        self._run_selected()

    def _stop_queue(self):
        self.worker.stop_queue()

    def _stop_current(self):
        self.worker.stop_current()

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
        elif event.kind == RunEvent.RUNNING and row:
            row.set_status("running")
            self._clear_log()
        elif event.kind == RunEvent.OUTPUT:
            self._append_log(event.data.get("line", ""))
        elif event.kind == RunEvent.DONE and row:
            row.set_status(event.data.get("status", "error"))
        elif event.kind == RunEvent.CANCELLED and row:
            row.set_status("cancelled")


class ResultsTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(top, text="Open Screenshots Folder", command=self._open_screenshots).pack(side="left", padx=4)

        columns = ("name", "started_at", "status", "duration")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for col, label, width in (
            ("name", "Test case", 260), ("started_at", "Started (UTC)", 170),
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
        # it in the OS default image viewer.
        shots_container = ttk.Frame(detail)
        detail.add(shots_container, weight=1)
        ttk.Label(shots_container, text="Screenshots:").pack(anchor="w", padx=4, pady=(4, 0))
        self.shots_frame = ttk.Frame(shots_container)
        self.shots_frame.pack(anchor="w", fill="both", expand=True, padx=4, pady=4)

        self._runs = []
        self.refresh()

    def refresh(self):
        self._runs = results_store.list_runs()
        self.tree.delete(*self.tree.get_children())
        for i, run in enumerate(self._runs):
            status = run.get("status")
            self.tree.insert("", "end", iid=str(i), values=(
                run.get("name"), run.get("started_at", "")[:19].replace("T", " "),
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
        shot_dir = run.get("screenshot_dir")
        if shot_dir and os.path.isdir(shot_dir):
            pngs = sorted(f for f in os.listdir(shot_dir) if f.lower().endswith(".png"))
            for name in pngs:
                full_path = os.path.join(shot_dir, name)
                link = ttk.Label(
                    self.shots_frame, text=name, foreground="#1a73e8", cursor="hand2",
                )
                link.pack(anchor="w")
                link.bind("<Button-1>", lambda e, p=full_path: self._open_image(p))
        else:
            ttk.Label(self.shots_frame, text="(no screenshots for this run)",
                      foreground="#808080").pack(anchor="w")

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


class SettingsTab(ttk.Frame):
    """Housekeeping: wipe old run history / screenshots / repos, link to the repo."""

    _BTN_WIDTH = 18  # same width for all three cleanup buttons

    def __init__(self, parent, on_results_cleared=None):
        super().__init__(parent)
        self.on_results_cleared = on_results_cleared

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

    def _confirm_irreversible(self, title, message):
        return messagebox.askyesno(
            title, f"{message}\n\nThis is IRREVERSIBLE and cannot be undone.",
            icon="warning",
        )

    def _clear_directory(self, title, directory, confirm_message, on_cleared=None):
        """Shared implementation for the three "Clear ..." buttons.

        Confirms (with a warning dialog), wipes every file/subfolder under
        `directory` (recreating the now-empty directory afterwards so the
        app keeps working without a restart), then reports success.
        """
        if not os.path.isdir(directory):
            messagebox.showinfo(title, "No such directory -- nothing to clear.")
            return
        if not self._confirm_irreversible(title, confirm_message):
            return
        shutil.rmtree(directory, ignore_errors=True)
        os.makedirs(directory, exist_ok=True)
        if on_cleared:
            on_cleared()
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
        self.pack(fill="both", expand=True)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.results_tab = ResultsTab(notebook)
        self.run_tab = RunTab(notebook, self.worker, on_run_started=self._noop)
        self.settings_tab = SettingsTab(notebook, on_results_cleared=self._on_results_cleared)
        notebook.add(self.run_tab, text="Run")
        notebook.add(self.results_tab, text="Results")
        notebook.add(self.settings_tab, text="Settings")

        self._poll_events()

    def _on_results_cleared(self):
        self.results_tab.refresh()
        self.run_tab.reset_status()

    def _noop(self):
        pass

    def _poll_events(self):
        try:
            while True:
                event = self.worker.events.get_nowait()
                self.run_tab.handle_event(event)
                if event.kind == RunEvent.DONE:
                    self.results_tab.refresh()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)


def main():
    root = tk.Tk()
    root.title("UI-automation Runner")
    root.geometry("900x700")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
