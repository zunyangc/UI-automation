r"""Run a set of CSV test-case specs, retry only the ones that fail, stop
retrying a case once it looks stuck, and write a local HTML/JSON report.

This is a thin sequencing/retry/report layer on top of the existing
single-spec runner (`run.ps1` / `run_test.py`) and the JSON persistence
already used by the GUI runner (`runner_app/results_store.py`). It does not
change how any single test case runs.

Usage:
    uv run python run_suite.py test_cases\e2e-001-verify_dotnet_info.csv
    uv run python run_suite.py --all
    uv run python run_suite.py --all --max-retries 2 --suite-timeout-min 180

Exit codes:
    0  every case ended "pass"
    1  one or more cases ended "fail"/"stuck"/"error"/"timeout"/"not_run_timeout"
    2  runner-level problem (bad args, no specs resolved, report write failure)
"""
import argparse
import datetime
import glob
import html
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from runner_app import results_store  # noqa: E402

# run_test.py always prints this line (see run_worker.py's identical use).
_SCREENSHOT_DIR_RE = re.compile(r"^screenshot_dir:\s*(.+)$", re.MULTILINE)

_DIGITS_RE = re.compile(r"\d+")

DEFAULT_MAX_RETRIES = 2
DEFAULT_CASE_TIMEOUT_MIN = 35
DEFAULT_SUITE_TIMEOUT_MIN = 0  # disabled


def _case_name(spec):
    return os.path.splitext(os.path.basename(spec))[0]


def resolve_specs(args):
    if args.all:
        if args.specs:
            raise SystemExit("error: --all cannot be combined with explicit spec paths")
        pattern = os.path.join(ROOT, "test_cases", "*.csv")
        specs = sorted(
            os.path.relpath(p, ROOT) for p in glob.glob(pattern)
            if os.path.basename(p) != "_template.csv"
        )
        return specs
    return list(args.specs)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="*", help="CSV spec path(s) to run")
    ap.add_argument("--all", action="store_true",
                     help="run every test_cases/*.csv (excluding _template.csv and "
                          "test_cases/v0/**); mutually exclusive with explicit specs")
    ap.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                     help=f"max additional attempts per case after its first run "
                          f"(default: {DEFAULT_MAX_RETRIES})")
    ap.add_argument("--case-timeout-min", type=float, default=DEFAULT_CASE_TIMEOUT_MIN,
                     help=f"hard per-attempt wall-clock cap in minutes "
                          f"(default: {DEFAULT_CASE_TIMEOUT_MIN})")
    ap.add_argument("--suite-timeout-min", type=float, default=DEFAULT_SUITE_TIMEOUT_MIN,
                     help="hard cap for the whole invocation in minutes; 0 disables it "
                          "(default: 0)")
    ap.add_argument("--no-cleanup", action="store_true",
                     help="pass --no-cleanup through to every run.ps1 invocation")
    ap.add_argument("--report-dir", default=None,
                     help="output folder for the local report "
                          "(default: result/suite-{timestamp})")
    ap.add_argument("-q", "--quiet", action="store_true",
                     help="pass -q through to every run.ps1 invocation")
    a = ap.parse_args(argv)
    if not a.all and not a.specs:
        ap.error("provide at least one spec path, or pass --all")
    return a


def _terminate(proc):
    """Kill the whole process tree -- mirrors runner_app/run_worker.py's
    `_terminate()` so a hung spec can't block the rest of the suite.
    """
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


def run_one_attempt(spec, attempt_no, case_timeout_min, quiet, no_cleanup, results_dir):
    """Run one attempt of `spec` via `run.ps1`, enforcing a hard per-attempt
    timeout, and return an attempt-record dict (also persisted as a
    `results/*.json` file via `results_store.save_run`).
    """
    name = _case_name(spec)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", os.path.join(ROOT, "run.ps1"), spec]
    if quiet:
        cmd.append("-q")
    if no_cleanup:
        cmd.append("--no-cleanup")
    env = dict(os.environ, PYTHONUNBUFFERED="1")

    print(f"\n--- [{name}] attempt {attempt_no} ---")
    started_at = datetime.datetime.now(datetime.timezone.utc)
    out_lines = []
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )

    def _reader():
        for line in proc.stdout:
            out_lines.append(line)
            print(f"    {line}", end="" if line.endswith("\n") else "\n")

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    timed_out = False
    try:
        proc.wait(timeout=case_timeout_min * 60)
    except subprocess.TimeoutExpired:
        timed_out = True
        print(f"    ! attempt exceeded {case_timeout_min} min -- terminating process tree")
        _terminate(proc)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    reader_thread.join(timeout=10)

    ended_at = datetime.datetime.now(datetime.timezone.utc)
    stdout_text = "".join(out_lines)
    exit_code = proc.returncode

    status_override = "timeout" if timed_out else None
    status = status_override or results_store.status_for_exit_code(exit_code)

    screenshot_dir = None
    m = _SCREENSHOT_DIR_RE.search(stdout_text)
    if m:
        screenshot_dir = m.group(1).strip()

    failure_message = None
    failed_step = None
    screenshots = []
    if status != "pass":
        failure_message = results_store.extract_failure_message(stdout_text)
        failed_step = results_store.extract_failure_step(stdout_text)
        screenshots = results_store.failure_screenshot_paths(screenshot_dir)

    result_path = results_store.save_run(
        spec_path=spec, name=name, started_at=started_at, ended_at=ended_at,
        exit_code=exit_code, stdout_text=stdout_text, screenshot_dir=screenshot_dir,
        results_dir=results_dir, status_override=status_override,
    )

    print(f"    [{name}] attempt {attempt_no}: {status} "
          f"({(ended_at - started_at).total_seconds():.1f}s)")

    return {
        "spec": spec,
        "name": name,
        "attempt_no": attempt_no,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "status": status,
        "exit_code": exit_code,
        "failed_step": failed_step,
        "failure_message": failure_message,
        "screenshot_dir": screenshot_dir,
        "screenshots": screenshots,
        "stdout_tail": stdout_text[-results_store.TAIL_CHARS:],
        "result_json": result_path,
    }


def _normalize_message(message):
    return _DIGITS_RE.sub("#", message or "")


def is_stuck(history):
    """A case is "stuck" (retrying further is pointless) when:

    - its last attempt is a runner `error` (exit code 2) -- a tooling
      problem, not flakiness, so it's never auto-retried; or
    - its last two attempts both `timeout`ed -- a hang, not a flake; or
    - its last two attempts both `fail`ed at the same step with the same
      (digit-normalized) failure message -- repeating the identical
      failure means another attempt is very unlikely to help.

    Anything else (a first failure, or a failure whose step/message
    differs from the previous attempt) is treated as possibly transient
    and is left to `--max-retries` as the hard backstop.
    """
    if not history:
        return False
    last = history[-1]
    if last["status"] == "error":
        return True
    if len(history) < 2:
        return False
    prev = history[-2]
    if last["status"] == "timeout" and prev["status"] == "timeout":
        return True
    if last["status"] == "fail" and prev["status"] == "fail":
        if last.get("failed_step") == prev.get("failed_step") and _normalize_message(
            last.get("failure_message")
        ) == _normalize_message(prev.get("failure_message")):
            return True
    return False


_STATUS_BADGE = {
    "pass": ("#1a7f37", "PASS"),
    "fail": ("#cf222e", "FAIL"),
    "error": ("#cf222e", "ERROR"),
    "timeout": ("#9a6700", "TIMEOUT"),
    "stuck": ("#cf222e", "STUCK"),
    "retries_exhausted": ("#cf222e", "RETRIES EXHAUSTED"),
    "not_run_timeout": ("#9a6700", "NOT RUN (suite timeout)"),
}


def _badge(status):
    color, label = _STATUS_BADGE.get(status, ("#57606a", status.upper()))
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background:{color};color:#fff;font-size:12px;font-weight:600;">'
            f'{html.escape(label)}</span>')


def write_report(specs, history, terminal, report_dir, suite_started_at, suite_ended_at):
    os.makedirs(report_dir, exist_ok=True)

    cases = []
    for spec in specs:
        attempts = history.get(spec, [])
        final_status = terminal.get(spec, "not_run_timeout" if not attempts else attempts[-1]["status"])
        cases.append({
            "spec": spec,
            "name": _case_name(spec),
            "final_status": final_status,
            "attempt_count": len(attempts),
            "attempts": attempts,
        })

    counts = {}
    for c in cases:
        counts[c["final_status"]] = counts.get(c["final_status"], 0) + 1

    summary = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "suite_started_at": suite_started_at.isoformat(),
        "suite_ended_at": suite_ended_at.isoformat(),
        "suite_duration_seconds": (suite_ended_at - suite_started_at).total_seconds(),
        "counts": counts,
        "cases": cases,
    }

    import json
    summary_path = os.path.join(report_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    rows = []
    for c in cases:
        attempt_summ = f"{c['attempt_count']}" if c["attempt_count"] else "0"
        detail_parts = []
        for a in c["attempts"]:
            msg = html.escape(a.get("failure_message") or "")
            step = html.escape(a.get("failed_step") or "")
            shots = "".join(
                f'<br><a href="file:///{html.escape(s)}">{html.escape(os.path.basename(s))}</a>'
                for s in a.get("screenshots", [])
            )
            detail_parts.append(
                f'<li>attempt {a["attempt_no"]}: <b>{html.escape(a["status"])}</b>'
                f' ({a["duration_seconds"]:.1f}s)'
                + (f' -- {step}: {msg}' if msg else "")
                + shots + "</li>"
            )
        details = (
            f'<details><summary>{len(c["attempts"])} attempt(s)</summary><ul>'
            + "".join(detail_parts) + "</ul></details>"
        ) if c["attempts"] else "(not run)"
        rows.append(
            "<tr>"
            f'<td>{html.escape(c["name"])}</td>'
            f'<td>{_badge(c["final_status"])}</td>'
            f'<td>{attempt_summ}</td>'
            f'<td>{details}</td>'
            "</tr>"
        )

    counts_line = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    report_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Suite report</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1f2328; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #d0d7de; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background: #f6f8fa; }}
summary {{ cursor: pointer; }}
</style></head>
<body>
<h1>Suite report</h1>
<p>Suite started: {html.escape(suite_started_at.isoformat())}<br>
Suite ended: {html.escape(suite_ended_at.isoformat())}<br>
Duration: {(suite_ended_at - suite_started_at).total_seconds():.1f}s<br>
Counts: {html.escape(counts_line)}</p>
<table>
<tr><th>Case</th><th>Final status</th><th>Attempts</th><th>Details</th></tr>
{''.join(rows)}
</table>
</body></html>
"""
    report_path = os.path.join(report_dir, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_html)

    non_pass_runs = [
        {"name": c["name"], "stdout_tail": c["attempts"][-1]["stdout_tail"],
         "screenshot_dir": c["attempts"][-1]["screenshot_dir"]}
        for c in cases if c["attempts"] and c["final_status"] != "pass"
    ]
    zip_path = None
    if non_pass_runs:
        zip_path = results_store.write_failure_report_zip(
            non_pass_runs, os.path.join(report_dir, "failures.zip"),
        )

    return summary_path, report_path, zip_path


def main(argv=None):
    args = parse_args(argv)
    specs = resolve_specs(args)
    if not specs:
        print("error: no specs resolved", file=sys.stderr)
        return 2

    report_dir = args.report_dir
    suite_started_at = datetime.datetime.now(datetime.timezone.utc)
    if report_dir is None:
        ts = suite_started_at.strftime("%Y%m%d_%H%M%SZ")
        report_dir = os.path.join(ROOT, "result", f"suite-{ts}")
    results_dir = os.path.join(ROOT, "results")

    suite_started_mono = time.monotonic()
    suite_deadline = (
        suite_started_mono + args.suite_timeout_min * 60
        if args.suite_timeout_min > 0 else None
    )

    history = {s: [] for s in specs}
    terminal = {}
    pending = list(specs)
    round_no = 1

    while pending:
        if suite_deadline and time.monotonic() >= suite_deadline:
            for s in pending:
                terminal[s] = "not_run_timeout"
            pending = []
            break
        print(f"\n=== Round {round_no}: {len(pending)} case(s) ===")
        still_pending = []
        for spec in pending:
            if suite_deadline and time.monotonic() >= suite_deadline:
                terminal[spec] = "not_run_timeout"
                continue
            attempt_no = len(history[spec]) + 1
            attempt = run_one_attempt(
                spec, attempt_no, args.case_timeout_min, args.quiet,
                args.no_cleanup, results_dir,
            )
            history[spec].append(attempt)
            if attempt["status"] == "pass":
                terminal[spec] = "pass"
                continue
            if is_stuck(history[spec]):
                terminal[spec] = "stuck"
                print(f"    [{_case_name(spec)}] stuck -- will not retry further")
                continue
            if len(history[spec]) >= args.max_retries + 1:
                terminal[spec] = "retries_exhausted"
                continue
            still_pending.append(spec)
        pending = still_pending
        round_no += 1

    suite_ended_at = datetime.datetime.now(datetime.timezone.utc)
    summary_path, report_path, zip_path = write_report(
        specs, history, terminal, report_dir, suite_started_at, suite_ended_at,
    )

    print(f"\n=== Suite finished: {suite_ended_at - suite_started_at} ===")
    for spec in specs:
        print(f"  {_case_name(spec):50s} {terminal.get(spec)}")
    print(f"\nReport: {report_path}")
    print(f"Summary: {summary_path}")
    if zip_path:
        print(f"Failures zip: {zip_path}")

    return 0 if all(terminal.get(s) == "pass" for s in specs) else 1


if __name__ == "__main__":
    sys.exit(main())
