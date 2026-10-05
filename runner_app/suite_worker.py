"""Launches `run_suite.py` as a child process and streams its progress.

Mirrors `runner_app/run_worker.py`'s shape (`Popen`, merged stderr,
`taskkill /T /F` for Stop) but drives exactly one `run_suite.py`
invocation per `start()` call instead of a queue of individual `run.ps1`
specs -- a suite run is one unit of work ("run this set with retries"),
not N independent queue items. See RUN-SUITE-UI-INTEGRATION-SPEC.md for
the full design rationale (why this is a separate worker instead of
flowing through `RunWorker`/`RunEvent`).
"""
import json
import os
import queue
import subprocess
import threading

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SuiteRunEvent:
    """One status update posted from the worker thread to the GUI thread."""

    STARTED = "started"
    OUTPUT = "output"
    DONE = "done"
    CANCELLED = "cancelled"

    def __init__(self, kind, **data):
        self.kind = kind
        self.data = data


class SuiteRunWorker:
    """Owns at most one in-flight `run_suite.py` process at a time.

    Unlike `RunWorker`, there is no internal queue -- the GUI is expected
    to keep its "Run Suite" control disabled while `is_running()` is
    true, so overlapping `start()` calls are a defensive no-op rather than
    a normal code path.
    """

    def __init__(self, repo_root=REPO_ROOT):
        self.repo_root = repo_root
        self.events = queue.Queue()
        self._lock = threading.Lock()
        self._proc = None
        self._starting = False
        self._stop_requested = False
        self._stop_before_launch = False

    def is_running(self):
        with self._lock:
            return self._proc is not None or self._starting

    def start(self, spec_paths, max_retries, case_timeout_min, suite_timeout_min, report_dir):
        """Launch `run_suite.py` for `spec_paths` (paths relative to the
        repo root). Returns `False` without doing anything if a suite run
        is already in flight.
        """
        with self._lock:
            if self._proc is not None or self._starting:
                return False
            self._starting = True
            self._stop_requested = False
            self._stop_before_launch = False
        thread = threading.Thread(
            target=self._run,
            args=(list(spec_paths), max_retries, case_timeout_min, suite_timeout_min, report_dir),
            daemon=True,
        )
        thread.start()
        return True

    def stop(self):
        """Kill the in-flight `run_suite.py` process tree, if any.

        If `start()` has returned but the child `Popen()` call inside
        `_run()` hasn't registered `self._proc` yet, remember the request
        via `_stop_before_launch` so `_run()` kills it the instant it's
        registered, instead of silently doing nothing (mirrors
        `RunWorker.stop_current()`'s `_stop_before_launch_spec` handling
        of the same race).
        """
        with self._lock:
            proc = self._proc
            if proc is None:
                if self._starting:
                    self._stop_before_launch = True
                return
            if proc.poll() is not None:
                return
            self._stop_requested = True
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

    def _run(self, spec_paths, max_retries, case_timeout_min, suite_timeout_min, report_dir):
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", os.path.join(self.repo_root, "run_suite.ps1"),
            *spec_paths,
            "--max-retries", str(max_retries),
            "--case-timeout-min", str(case_timeout_min),
            "--report-dir", report_dir,
        ]
        if suite_timeout_min and suite_timeout_min > 0:
            cmd += ["--suite-timeout-min", str(suite_timeout_min)]
        env = dict(os.environ, PYTHONUNBUFFERED="1")

        self.events.put(SuiteRunEvent(SuiteRunEvent.STARTED, report_dir=report_dir))

        exit_code = 2
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=self.repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
            )
            pending_stop = False
            with self._lock:
                self._proc = proc
                self._starting = False
                if self._stop_before_launch:
                    self._stop_before_launch = False
                    self._stop_requested = True
                    pending_stop = True
            if pending_stop:
                self._terminate(proc)
            for line in proc.stdout:
                self.events.put(SuiteRunEvent(SuiteRunEvent.OUTPUT, line=line))
            proc.wait()
            exit_code = proc.returncode
        except Exception as e:
            self.events.put(SuiteRunEvent(
                SuiteRunEvent.OUTPUT, line=f"\nrunner_app failed to launch run_suite.py: {e}\n",
            ))
        finally:
            with self._lock:
                was_stopped = self._stop_requested
                self._stop_requested = False
                self._stop_before_launch = False
                self._proc = None
                self._starting = False

        summary = self._read_summary(report_dir)
        kind = SuiteRunEvent.CANCELLED if was_stopped else SuiteRunEvent.DONE
        self.events.put(SuiteRunEvent(
            kind, exit_code=exit_code, report_dir=report_dir,
            report_path=os.path.join(report_dir, "report.html"),
            summary=summary,
        ))

    @staticmethod
    def _read_summary(report_dir):
        """Best-effort read of `report_dir/summary.json`; `None` if it
        doesn't exist yet (e.g. the process was killed before writing a
        report, or crashed before getting that far).
        """
        path = os.path.join(report_dir, "summary.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
