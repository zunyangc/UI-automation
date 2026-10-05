"""Persist one JSON file per test run under `results/`.

Human-readable, no new dependency (stdlib `json`) -- mirrors the repo's
existing "plain files, no database" style (e.g. `screenshots/`).
"""
import datetime
import glob
import json
import os
import re
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")

# Keep JSON files small -- full console output already streams to the GUI
# log panel during the run and isn't persisted anywhere else today either.
TAIL_CHARS = 4000

_STATUS_BY_EXIT_CODE = {0: "pass", 1: "fail", 2: "error"}

# run_test.py prints exactly one of these right before a failing step's
# cleanup (see on_failure_capture()/main() in run_test.py) -- the message
# after the marker is the concise, human-readable failure reason.
_FAILURE_LINE_RE = re.compile(r"^\*\*\* STEP (?:FAILED|RAISED UNEXPECTED ERROR): (.+)$")
# Printed by run_test.py's `if __name__ == "__main__"` wrapper for a
# runner-level error (bad spec, missing script, etc. -- exit code 2) that
# never reaches a per-step failure marker.
_RUNNER_ERROR_RE = re.compile(r"^RUNNER ERROR: (.+)$")


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
    base = f"{ts}_{_safe_filename_part(name)}"
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
    # Two runs of the same case starting within the same second would
    # otherwise collide on `base` and overwrite each other -- create the
    # file exclusively and fall back to a numeric suffix on collision.
    suffix = 0
    while True:
        candidate = f"{base}.json" if suffix == 0 else f"{base}-{suffix}.json"
        out_path = os.path.join(results_dir, candidate)
        try:
            fd = os.open(out_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            suffix += 1
            continue
        break
    with os.fdopen(fd, "w", encoding="utf-8") as f:
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


def extract_failure_message(stdout_tail):
    """Return the concise reason a run failed, parsed from its stored
    stdout tail.

    Priority (matches run_test.py's own printed markers):
      1. The LAST line matching `*** STEP FAILED: <id>: <err>` or
         `*** STEP RAISED UNEXPECTED ERROR: <id>: <err>` -- returns
         `"<id>: <err>"` (everything after the marker).
      2. Else the last line matching `RUNNER ERROR: <err>` -- returns
         `<err>`.
      3. Else the last non-blank line of `stdout_tail` (stripped).
      4. Else (empty/whitespace-only stdout_tail) --
         `"(no failure detail captured)"`.

    Never raises -- worst case returns the placeholder string.
    """
    lines = (stdout_tail or "").splitlines()
    for line in reversed(lines):
        m = _FAILURE_LINE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    for line in reversed(lines):
        m = _RUNNER_ERROR_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return "(no failure detail captured)"


def extract_failure_step(stdout_tail):
    """Return just the `step_N` id from the last `*** STEP FAILED: step_N:
    ...` / `*** STEP RAISED UNEXPECTED ERROR: step_N: ...` marker line in
    `stdout_tail`, or `None` if no such line is present (e.g. a runner-level
    error, or a clean pass). Used by callers that need to compare *which*
    step failed across repeated attempts of the same case without
    re-parsing stdout themselves (see `extract_failure_message` for the
    full detail string).
    """
    lines = (stdout_tail or "").splitlines()
    for line in reversed(lines):
        m = _FAILURE_LINE_RE.match(line.strip())
        if m:
            step_id, _, _ = m.group(1).partition(":")
            return step_id.strip() or None
    return None


def failure_screenshot_paths(screenshot_dir):
    """Return the sorted list of this run's `*_FAILURE_*.png` screenshots
    (the ones `run_test.py`'s `on_failure_capture()` writes), or `[]` if
    `screenshot_dir` is falsy or doesn't exist. Only `_FAILURE_` files are
    returned -- a run's ordinary pass/fail step screenshots aren't
    included in the report.
    """
    if not screenshot_dir or not os.path.isdir(screenshot_dir):
        return []
    return sorted(glob.glob(os.path.join(screenshot_dir, "*_FAILURE_*.png")))


def format_failure_summary(runs, generated_at=None):
    """Build the plain-text `summary.txt` content for a failure report.

    `runs` is a list of run dicts (same shape as `list_runs()` records).
    One line per run, in input order:

        Failed at <name> test case, failed message is <message>.

    where `<message>` is `extract_failure_message(run["stdout_tail"])`. A
    run with no failure screenshots gets a trailing note appended to its
    line so a reader knows why the zip's `screenshots/` folder doesn't
    have anything for that case.
    """
    ts = generated_at or datetime.datetime.now(datetime.timezone.utc)
    lines = [
        "Failed test case report",
        f"Generated: {ts.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
    ]
    for run in runs:
        message = extract_failure_message(run.get("stdout_tail"))
        sentence = f"Failed at {run.get('name')} test case, failed message is {message}."
        if not failure_screenshot_paths(run.get("screenshot_dir")):
            sentence += " (no failure screenshot captured)"
        lines.append(sentence)
    return "\n".join(lines) + "\n"


def write_failure_report_zip(runs, zip_path, generated_at=None):
    """Write a single zip at `zip_path` containing `summary.txt` (see
    `format_failure_summary`) plus every run's failure screenshots under
    `screenshots/<safe case name>_<original filename>`.

    Screenshot entry names are prefixed with the case's own (sanitized)
    name so two different cases' `ss_N_FAILURE_*.png` files never collide;
    if two runs of the SAME case still collide (identical `ss_N` name --
    only possible if both runs happen to fail at the same step count), a
    numeric suffix is appended, mirroring `save_run()`'s own
    collision-handling pattern.

    Creates the parent directory of `zip_path` if needed. Returns
    `zip_path`. A screenshot that can't be read (e.g. deleted after the
    run) is silently skipped rather than failing the whole report; only an
    unwritable `zip_path` itself propagates to the caller.
    """
    out_dir = os.path.dirname(os.path.abspath(zip_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    summary_text = format_failure_summary(runs, generated_at)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", summary_text)
        used_names = set()
        for run in runs:
            prefix = _safe_filename_part(run.get("name") or "case")
            for shot_path in failure_screenshot_paths(run.get("screenshot_dir")):
                if not os.path.isfile(shot_path):
                    continue
                arcname = f"screenshots/{prefix}_{os.path.basename(shot_path)}"
                suffix = 0
                candidate = arcname
                while candidate in used_names:
                    suffix += 1
                    stem, ext = os.path.splitext(arcname)
                    candidate = f"{stem}-{suffix}{ext}"
                used_names.add(candidate)
                try:
                    zf.write(shot_path, candidate)
                except OSError:
                    continue
    return zip_path


if __name__ == "__main__":
    for r in list_runs():
        print(f"{r.get('started_at')}  {r.get('name')}  {r.get('status')}")
