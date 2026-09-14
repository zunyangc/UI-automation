"""Sequential background worker that runs queued test-case specs.

One worker thread owns a `queue.Queue` of specs and runs them strictly one
at a time via `powershell -File run.ps1 <spec> -q`, since the UIA-driven
steps fight over mouse/keyboard/focus on a single desktop session -- true
concurrent runs are not safe on one DevBox.

The GUI (Tkinter) main thread is not thread-safe, so this module never
touches widgets directly. Instead it pushes `RunEvent` objects onto a
thread-safe `queue.Queue` that the GUI drains on a `root.after(...)` poll.
"""
import datetime
import os
import queue
import re
import subprocess
import threading

from . import results_store

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# run_test.py always prints this line (not gated by -q), so we can recover
# the resolved screenshot directory for a run without re-deriving the
# {name}-{timestamp} convention ourselves.
_SCREENSHOT_DIR_RE = re.compile(r"^screenshot_dir:\s*(.+)$", re.MULTILINE)


class RunEvent:
    """One status update posted from the worker thread to the GUI thread."""

    QUEUED = "queued"
    RUNNING = "running"
    OUTPUT = "output"
    DONE = "done"
    CANCELLED = "cancelled"

    def __init__(self, kind, spec_path, **data):
        self.kind = kind
        self.spec_path = spec_path
        self.data = data


class RunWorker:
    """Owns the run queue + background thread. One instance per app."""

    def __init__(self, repo_root=REPO_ROOT, results_dir=None):
        self.repo_root = repo_root
        self.results_dir = results_dir or os.path.join(repo_root, "results")
        self.events = queue.Queue()
        self._work_queue = queue.Queue()
        self._pending = set()  # spec_paths still queued (not yet started)
        self._lock = threading.Lock()
        self._current_proc = None  # subprocess.Popen of the in-flight run, if any
        self._current_spec = None
        self._stop_requested_spec = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def enqueue(self, test_cases):
        """Queue a list of TestCase objects for sequential execution."""
        with self._lock:
            for tc in test_cases:
                self._pending.add(tc.path)
                self._work_queue.put(tc)
                self.events.put(RunEvent(RunEvent.QUEUED, tc.path, name=tc.display_name))

    def stop_queue(self):
        """Cancel every spec that hasn't started running yet.

        The currently in-flight subprocess (if any) is left to finish on
        its own -- use `stop_current()` if you also want to kill it.
        """
        with self._lock:
            drained = []
            try:
                while True:
                    drained.append(self._work_queue.get_nowait())
            except queue.Empty:
                pass
            for tc in drained:
                if tc.path in self._pending:
                    self._pending.discard(tc.path)
                    self.events.put(RunEvent(RunEvent.CANCELLED, tc.path, name=tc.display_name))

    def stop_current(self):
        """Forcibly terminate the currently-running test case, if any.

        Kills the whole process tree (not just the top `powershell.exe`)
        via `taskkill /T /F`, since `run.ps1` spawns `uv`/`python`/possibly
        Visual Studio child processes that would otherwise be orphaned.
        The run is recorded as "cancelled" rather than pass/fail/error.
        """
        with self._lock:
            proc = self._current_proc
            spec_path = self._current_spec
            if proc is None or proc.poll() is not None:
                return
            self._stop_requested_spec = spec_path
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, text=True,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run_loop(self):
        while True:
            tc = self._work_queue.get()
            with self._lock:
                still_pending = tc.path in self._pending
                self._pending.discard(tc.path)
            if not still_pending:
                continue  # was cancelled via stop_queue while queued
            self._run_one(tc)

    def _run_one(self, tc):
        spec_path = tc.path
        rel_spec = os.path.relpath(spec_path, self.repo_root)
        self.events.put(RunEvent(RunEvent.RUNNING, spec_path, name=tc.display_name))

        started_at = datetime.datetime.now(datetime.timezone.utc)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", os.path.join(self.repo_root, "run.ps1"), rel_spec, "-q"]
        stdout_text, stderr_text, exit_code = "", "", 2
        was_stopped = False
        try:
            proc = subprocess.Popen(
                cmd, cwd=self.repo_root, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            with self._lock:
                self._current_proc = proc
                self._current_spec = spec_path
            out_lines, err_lines = [], []
            for line in proc.stdout:
                out_lines.append(line)
                self.events.put(RunEvent(RunEvent.OUTPUT, spec_path, line=line))
            proc.wait()
            stderr_text = proc.stderr.read() if proc.stderr else ""
            stdout_text = "".join(out_lines)
            exit_code = proc.returncode
        except Exception as e:
            stderr_text = f"{stderr_text}\nrunner_app failed to launch run.ps1: {e}"
            exit_code = 2
        finally:
            with self._lock:
                was_stopped = self._stop_requested_spec == spec_path
                self._stop_requested_spec = None
                self._current_proc = None
                self._current_spec = None

        ended_at = datetime.datetime.now(datetime.timezone.utc)
        screenshot_dir = None
        m = _SCREENSHOT_DIR_RE.search(stdout_text)
        if m:
            screenshot_dir = m.group(1).strip()

        status_override = "cancelled" if was_stopped else None
        result_path = results_store.save_run(
            spec_path=rel_spec, name=tc.display_name, started_at=started_at,
            ended_at=ended_at, exit_code=exit_code, stdout_text=stdout_text,
            stderr_text=stderr_text, screenshot_dir=screenshot_dir,
            results_dir=self.results_dir, status_override=status_override,
        )
        self.events.put(RunEvent(
            RunEvent.DONE, spec_path, name=tc.display_name, exit_code=exit_code,
            status=status_override or results_store.status_for_exit_code(exit_code),
            result_path=result_path, screenshot_dir=screenshot_dir,
        ))
