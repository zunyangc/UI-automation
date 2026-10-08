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
stop-flag file never appears.
"""
import argparse, os, subprocess, sys, time

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


def _loop(stop_flag, poll_ms, max_lifetime_ms, log_file):
    """The detached, long-running half: poll until stopped or capped."""
    from handle_android_sdk_dialogs import handle_dialogs_once

    _log(log_file, f"watcher loop started (stop_flag={stop_flag}, poll_ms={poll_ms}, "
                    f"max_lifetime_ms={max_lifetime_ms})")
    start = time.time()
    deadline = start + max_lifetime_ms / 1000.0
    interval = max(poll_ms, 0) / 1000.0

    while True:
        if os.path.exists(stop_flag):
            _log(log_file, "stop-flag seen; exiting")
            break
        if time.time() >= deadline:
            _log(log_file, f"max lifetime ({max_lifetime_ms}ms) reached; exiting "
                            "as a safety net (stop() was never called)")
            break
        try:
            hits = handle_dialogs_once()
            for h in hits:
                _log(log_file, f"handled: {h}")
        except Exception as e:
            _log(log_file, f"ERROR during poll (ignored, continuing): {e}")
        time.sleep(interval)

    # Best-effort cleanup of our own stop-flag so a stale one never confuses
    # a later test run that reuses the same screenshot-dir naming scheme.
    try:
        if os.path.exists(stop_flag):
            os.remove(stop_flag)
    except Exception:
        pass


def _cmd_start(a):
    # Make sure no stale stop-flag from a previous (crashed) run short-circuits
    # this brand-new loop the instant it starts.
    try:
        if os.path.exists(a.stop_flag):
            os.remove(a.stop_flag)
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

    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: survive this (short-lived)
        # `start` process exiting, and don't inherit its console.
        creationflags = 0x00000008 | 0x00000200

    subprocess.Popen(cmd, creationflags=creationflags, close_fds=True,
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
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
                         help="safety cap so the loop can't outlive a crashed test (default 2h). "
                              "Callers whose scenario can legitimately run longer than that "
                              "(e.g. a multi-iteration loop with long build timeouts) should pass "
                              "an explicit, larger value here -- this is a crash safety net, not "
                              "meant to be hit during a normal run.")
    p_start.add_argument("--log-file", dest="log_file", default=None)

    p_stop = sub.add_parser("stop", help="signal the background watcher to exit")
    p_stop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_stop.add_argument("--wait-ms", dest="wait_ms", type=int, default=5000)

    p_loop = sub.add_parser("_loop", help=argparse.SUPPRESS)  # internal use only
    p_loop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_loop.add_argument("--poll-ms", dest="poll_ms", type=int, default=1000)
    p_loop.add_argument("--max-lifetime-ms", dest="max_lifetime_ms", type=int, default=2 * 60 * 60 * 1000)
    p_loop.add_argument("--log-file", dest="log_file", default=None)

    a = p.parse_args()

    if a.cmd == "start":
        _cmd_start(a)
    elif a.cmd == "stop":
        _cmd_stop(a)
    elif a.cmd == "_loop":
        _loop(a.stop_flag, a.poll_ms, a.max_lifetime_ms, a.log_file)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(0 if (len(sys.argv) > 1 and sys.argv[1] == "start") else 1)
