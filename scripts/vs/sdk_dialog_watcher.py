"""Background watcher that auto-accepts the Android SDK License / UAC
dialogs whenever they pop up, for as long as it is left running.

The MAUI "install Android SDK" flow does not fire at one predictable step --
it can appear at essentially any point after the project-creation wizard
starts driving MSBuild/VS in the background. Rather than inlining a bounded
poll window at every step that *might* trigger it, a test case starts this
watcher once (as a detached background process) and stops it once, near the
end, bracketing every step in between:

    scripts/vs/sdk_dialog_watcher.py start --stop-flag <path> [--log-file <path>]
    ...                                   (rest of the test steps)
    scripts/vs/sdk_dialog_watcher.py stop  --stop-flag <path>

`start` returns almost immediately (it only launches the detached loop and
exits); the detached process does the actual polling. It reuses the exact
same dialog-detection/click logic as handle_android_sdk_dialogs.py
(`handle_dialogs_once()`) so there is a single place that knows about these
two dialog titles.

Deliberately narrow in scope: it ONLY ever clicks the Android SDK - License
Agreement (`Accept`) and User Account Control (`Yes`) dialogs. It never
touches, dismisses, or otherwise interacts with any other window. Any other
unexpected dialog is left exactly as-is -- it will keep blocking VS, so the
very next real UI step (find_control/wait_for/click) naturally times out and
fails the test, which already triggers run_test.py's on_failure_capture
(full-screen screenshot + captured-window cleanup). This is intentional: we
do not want a second, duplicate "unknown dialog -> fail" path here.

`start` always exits 0. The detached loop also always exits 0 and never
raises past its own top-level guard (it must never crash VS/test automation
just because a transient UIA lookup failed). As a safety net against a
crashed/killed test case that never reaches its own `stop` call, the loop
self-terminates after `--max-lifetime-ms` (default 2 hours) even if the
stop-flag file never appears -- but that cap is a LAST-RESORT fallback, not
the primary shutdown path: `run_test.py`'s own `finally` block (which always
runs, pass/fail/crash) looks for this watcher's `.active` marker next to any
spec's `--stop-flag` path and calls `stop` on it automatically, so a failed
run's watcher normally stops within moments of the failure instead of
lingering for the rest of its `--max-lifetime-ms`. Scenarios whose own
runtime can legitimately exceed the 2-hour default should still raise
`--max-lifetime-ms` explicitly -- that generous value only has to protect
against the test process itself being killed outright (e.g. SIGKILL/Task
Manager), which `run_test.py`'s `finally` can't observe.
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _log(log_file, line):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    msg = f"[{ts}] {line}"
    print(msg)
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def _marker_path(stop_flag):
    """Path of the "a watcher is active for this stop-flag" marker.

    Lets a generic, spec-agnostic caller (run_test.py's own cleanup) notice
    "a watcher was started under this run's screenshot_dir" and stop it on
    any run outcome -- pass, fail, or crash -- without needing to know
    anything CSV-specific about which spec happens to use this watcher.
    Named alongside (not instead of) the stop-flag so both live under the
    same run-scoped `artifacts.screenshot_dir` and vanish together with it.
    """
    return stop_flag + ".active"


def _loop(stop_flag, poll_ms, max_lifetime_ms, log_file, license_state_file):
    """The detached, long-running half: poll until stopped or capped."""
    from handle_android_sdk_dialogs import handle_dialogs_once, list_uac_hwnds

    _log(log_file, f"watcher loop started (stop_flag={stop_flag}, poll_ms={poll_ms}, "
                    f"max_lifetime_ms={max_lifetime_ms})")
    start = time.time()
    deadline = start + max_lifetime_ms / 1000.0
    interval = max(poll_ms, 0) / 1000.0

    # Anything already on-screen before we start polling cannot possibly be a
    # UAC prompt belonging to THIS run's SDK-install flow (that flow hasn't
    # even started yet) -- never approve one of these, no matter how soon a
    # License Accept happens to follow. See handle_android_sdk_dialogs.py.
    baseline_uac_hwnds = list_uac_hwnds()

    while True:
        if os.path.exists(stop_flag):
            _log(log_file, "stop-flag seen; exiting")
            break
        if time.time() >= deadline:
            _log(log_file, f"max lifetime ({max_lifetime_ms}ms) reached; exiting "
                            "as a safety net (stop() was never called)")
            break
        try:
            hits = handle_dialogs_once(license_state_file, baseline_uac_hwnds)
            for h in hits:
                _log(log_file, f"handled: {h}")
        except Exception as e:
            _log(log_file, f"ERROR during poll (ignored, continuing): {e}")
        time.sleep(interval)

    # Best-effort cleanup of our own stop-flag and active-marker so neither
    # ever confuses a later test run that reuses the same screenshot-dir
    # naming scheme.
    for path in (stop_flag, _marker_path(stop_flag)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _cmd_start(a):
    # Make sure no stale stop-flag/marker from a previous (crashed) run
    # short-circuits this brand-new loop the instant it starts.
    for path in (a.stop_flag, _marker_path(a.stop_flag)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    if a.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(a.log_file)), exist_ok=True)

    cmd = [sys.executable, os.path.abspath(__file__), "_loop",
           "--stop-flag", a.stop_flag,
           "--poll-ms", str(a.poll_ms),
           "--max-lifetime-ms", str(a.max_lifetime_ms)]
    if a.log_file:
        cmd += ["--log-file", a.log_file]
    if a.license_state_file:
        cmd += ["--license-state-file", a.license_state_file]

    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: survive this (short-lived)
        # `start` process exiting, and don't inherit its console.
        creationflags = 0x00000008 | 0x00000200

    proc = subprocess.Popen(cmd, creationflags=creationflags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    # Write the marker AFTER the child is launched, so run_test.py's cleanup
    # never sees "active" without a process actually having been started.
    # Best-effort: if this fails, the watcher still runs fine -- the only
    # loss is run_test.py's automatic failure-path stop for this one run.
    try:
        os.makedirs(os.path.dirname(os.path.abspath(a.stop_flag)) or ".", exist_ok=True)
        with open(_marker_path(a.stop_flag), "w", encoding="utf-8") as f:
            json.dump({"pid": proc.pid, "startedAt": time.time()}, f)
    except Exception:
        pass
    print(f"watcher started (stop-flag {a.stop_flag})")


def _cmd_stop(a):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(a.stop_flag)) or ".", exist_ok=True)
        with open(a.stop_flag, "w", encoding="utf-8") as f:
            f.write("stop\n")
    except Exception as e:
        print(f"WARNING: could not write stop-flag {a.stop_flag}: {e}")
        return
    # Give the loop a brief moment to notice and exit/remove the flag; this is
    # best-effort only -- we never fail the test over the watcher's shutdown.
    deadline = time.time() + a.wait_ms / 1000.0
    while time.time() < deadline:
        if not os.path.exists(a.stop_flag):
            print("watcher stopped")
            return
        time.sleep(0.2)
    # Didn't confirm in time -- clean up the marker anyway so a later cleanup
    # pass doesn't keep retrying a watcher that may already be gone (e.g. hit
    # its own max-lifetime cap and exited without seeing this stop-flag write
    # land first).
    try:
        os.remove(_marker_path(a.stop_flag))
    except Exception:
        pass
    print("watcher stop requested (did not confirm exit within wait window; harmless)")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="launch the detached background watcher")
    p_start.add_argument("--stop-flag", dest="stop_flag", required=True,
                         help="path to a file; its existence tells the loop to exit")
    p_start.add_argument("--poll-ms", dest="poll_ms", type=int, default=1000)
    p_start.add_argument("--max-lifetime-ms", dest="max_lifetime_ms", type=int,
                         default=2 * 60 * 60 * 1000,
                         help="LAST-RESORT safety cap, only reached if the test process is "
                              "killed outright (run_test.py's own finally-block cleanup "
                              "normally stops the watcher within moments of any ordinary "
                              "pass/fail/crash -- see module docstring). Default 2h. "
                              "Callers whose scenario can legitimately run longer than that "
                              "should still pass an explicit, larger value here.")
    p_start.add_argument("--log-file", dest="log_file", default=None)
    p_start.add_argument("--license-state-file", dest="license_state_file", default=None,
                         help="passed through to handle_android_sdk_dialogs.py's "
                              "handle_dialogs_once(); should be a path under this run's own "
                              "artifacts.screenshot_dir so the License-Accept/UAC-grace gate "
                              "can never be confused by a previous run's state.")

    p_stop = sub.add_parser("stop", help="signal the background watcher to exit")
    p_stop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_stop.add_argument("--wait-ms", dest="wait_ms", type=int, default=5000)

    p_loop = sub.add_parser("_loop", help=argparse.SUPPRESS)  # internal use only
    p_loop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_loop.add_argument("--poll-ms", dest="poll_ms", type=int, default=1000)
    p_loop.add_argument("--max-lifetime-ms", dest="max_lifetime_ms", type=int, default=2 * 60 * 60 * 1000)
    p_loop.add_argument("--log-file", dest="log_file", default=None)
    p_loop.add_argument("--license-state-file", dest="license_state_file", default=None)

    a = p.parse_args()

    if a.cmd == "start":
        _cmd_start(a)
    elif a.cmd == "stop":
        _cmd_stop(a)
    elif a.cmd == "_loop":
        _loop(a.stop_flag, a.poll_ms, a.max_lifetime_ms, a.log_file, a.license_state_file)



if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(0 if (len(sys.argv) > 1 and sys.argv[1] == "start") else 1)
