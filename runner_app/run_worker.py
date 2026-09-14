"""Sequential background worker that runs queued test-case specs.

One worker thread owns a `queue.Queue` of specs and runs them strictly one
at a time via `powershell -File run.ps1 <spec>` (verbose, not `-q`, so the
Run tab's log panel gets real step-by-step output), since the UIA-driven
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
        # spec_path -> TestCase for items queued but not yet started. This
        # dict (mutated only while holding `_lock`) is the single source of
        # truth for "still cancellable" -- stop_queue() clears it directly
        # instead of racing to drain `_work_queue`, so an item the worker
        # thread already popped off the queue (but hasn't started running)
        # is still reliably recognized as cancelled. See _run_loop().
        self._pending = {}
        self._lock = threading.Lock()
        self._current_proc = None  # subprocess.Popen of the in-flight run, if any
        self._current_spec = None
        self._stop_requested_spec = None
        # Set when stop_current() is called for a spec that's already
        # "running" (per the RUNNING event) but whose Popen() call hasn't
        # returned yet -- honored the moment `_current_proc` is registered,
        # instead of silently no-op'ing because there's nothing to kill yet.
        self._stop_before_launch_spec = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def enqueue(self, test_cases):
        """Queue a list of TestCase objects for sequential execution."""
        with self._lock:
            for tc in test_cases:
                self._pending[tc.path] = tc
                self._work_queue.put(tc)
                self.events.put(RunEvent(RunEvent.QUEUED, tc.path, name=tc.display_name))

    def stop_queue(self):
        """Cancel every spec that hasn't started running yet.

        The currently in-flight subprocess (if any) is left to finish on
        its own -- use `stop_current()` if you also want to kill it.
        """
        with self._lock:
            cancelled = list(self._pending.items())
            self._pending.clear()
        for path, tc in cancelled:
            self.events.put(RunEvent(RunEvent.CANCELLED, path, name=tc.display_name))

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
            if spec_path is None:
                return  # nothing running or about to run
            if proc is None:
                # RUNNING has been published for `spec_path` but its
                # subprocess.Popen() call hasn't returned yet -- remember
                # the request so _run_one() kills it the instant it's
                # registered, instead of silently doing nothing.
                self._stop_before_launch_spec = spec_path
                return
            if proc.poll() is not None:
                return
            self._stop_requested_spec = spec_path
        self._terminate(proc)

    def _terminate(self, proc):
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
                # `_pending.pop` and stop_queue()'s `_pending.clear()` both
                # only ever run while holding `_lock`, so this check is
                # race-free regardless of whether stop_queue() ran before or
                # after this item was physically dequeued above.
                still_pending = self._pending.pop(tc.path, None) is not None
            if not still_pending:
                continue  # was cancelled via stop_queue while queued
            self._run_one(tc)

    def _run_one(self, tc):
        spec_path = tc.path
        rel_spec = os.path.relpath(spec_path, self.repo_root)

        # Publish "running" (and register `_current_spec`, proc still None)
        # before Popen() so stop_current()'s window is well-defined -- see
        # stop_current()/`_stop_before_launch_spec` for how a stop request
        # arriving before Popen() returns is still honored.
        with self._lock:
            self._current_spec = spec_path
        self.events.put(RunEvent(RunEvent.RUNNING, spec_path, name=tc.display_name))

        started_at = datetime.datetime.now(datetime.timezone.utc)
        # No "-q": quiet mode also suppresses run_test.py's per-step/command
        # output on a passing run, leaving the Run tab's "live" log panel
        # essentially empty until the process exits. PYTHONUNBUFFERED keeps
        # the child's stdout from sitting in a block buffer (the default
        # when stdout isn't a real console) so lines actually stream as
        # they're printed, not just at exit/buffer-full.
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", os.path.join(self.repo_root, "run.ps1"), rel_spec]
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        stdout_text, exit_code = "", 2
        was_stopped = False
        try:
            # stderr is merged into stdout (rather than a second PIPE) so a
            # single blocking readline loop can't deadlock against a full,
            # undrained stderr buffer, and so stderr lines stream to the
            # Run tab's live log too.
            proc = subprocess.Popen(
                cmd, cwd=self.repo_root, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=env,
            )
            pending_stop = False
            with self._lock:
                self._current_proc = proc
                if self._stop_before_launch_spec == spec_path:
                    self._stop_before_launch_spec = None
                    self._stop_requested_spec = spec_path
                    pending_stop = True
            if pending_stop:
                self._terminate(proc)
            out_lines = []
            for line in proc.stdout:
                out_lines.append(line)
                self.events.put(RunEvent(RunEvent.OUTPUT, spec_path, line=line))
            proc.wait()
            stdout_text = "".join(out_lines)
            exit_code = proc.returncode
        except Exception as e:
            stdout_text = f"{stdout_text}\nrunner_app failed to launch run.ps1: {e}"
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
            stderr_text="", screenshot_dir=screenshot_dir,
            results_dir=self.results_dir, status_override=status_override,
        )
        self.events.put(RunEvent(
            RunEvent.DONE, spec_path, name=tc.display_name, exit_code=exit_code,
            status=status_override or results_store.status_for_exit_code(exit_code),
            result_path=result_path, screenshot_dir=screenshot_dir,
        ))
