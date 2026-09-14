"""Persist one JSON file per test run under `results/`.

Human-readable, no new dependency (stdlib `json`) -- mirrors the repo's
existing "plain files, no database" style (e.g. `screenshots/`).
"""
import datetime
import glob
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")

# Keep JSON files small -- full console output already streams to the GUI
# log panel during the run and isn't persisted anywhere else today either.
TAIL_CHARS = 4000

_STATUS_BY_EXIT_CODE = {0: "pass", 1: "fail", 2: "error"}


def status_for_exit_code(exit_code):
    return _STATUS_BY_EXIT_CODE.get(exit_code, "error")


def _safe_filename_part(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "test"


def save_run(spec_path, name, started_at, ended_at, exit_code,
             stdout_text="", stderr_text="", screenshot_dir=None,
             results_dir=RESULTS_DIR, status_override=None):
    """Write one results/<timestamp>_<name>.json file and return its path.

    `status_override` lets the caller record a status other than what the
    raw exit code implies -- e.g. "cancelled" when the tester used
    "Stop Current Run" to kill an in-flight test case.
    """
    os.makedirs(results_dir, exist_ok=True)
    ts = started_at.strftime("%Y%m%d_%H%M%SZ")
    filename = f"{ts}_{_safe_filename_part(name)}.json"
    record = {
        "spec_path": spec_path,
        "name": name,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "exit_code": exit_code,
        "status": status_override or status_for_exit_code(exit_code),
        "stdout_tail": (stdout_text or "")[-TAIL_CHARS:],
        "stderr_tail": (stderr_text or "")[-TAIL_CHARS:],
        "screenshot_dir": screenshot_dir,
    }
    out_path = os.path.join(results_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return out_path


def list_runs(results_dir=RESULTS_DIR):
    """Return all recorded runs, newest first (by started_at)."""
    runs = []
    for path in glob.glob(os.path.join(results_dir, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            continue
        record["_file"] = path
        runs.append(record)
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return runs


if __name__ == "__main__":
    for r in list_runs():
        print(f"{r.get('started_at')}  {r.get('name')}  {r.get('status')}")
