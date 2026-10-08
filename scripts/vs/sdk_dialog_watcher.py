"""Background watcher that clicks the Android SDK License / UAC dialogs
by title match whenever they pop up, for as long as it is left running.

    scripts/vs/sdk_dialog_watcher.py start --stop-flag <path> [--log-file <path>]
    ...                                   (rest of the test steps)
    scripts/vs/sdk_dialog_watcher.py stop  --stop-flag <path>

`start` launches a detached loop process and returns immediately; the loop
reuses handle_android_sdk_dialogs.py's handle_dialogs_once() every
`--poll-ms` until `stop` writes the `--stop-flag` file. No timer, no state,
no correlation -- if the title doesn't match, nothing happens.
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
    """Marker lets run_test.py find and stop this watcher on any run outcome."""
    return stop_flag + ".active"


def _loop(stop_flag, poll_ms, log_file):
    from handle_android_sdk_dialogs import handle_dialogs_once

    _log(log_file, f"watcher loop started (stop_flag={stop_flag}, poll_ms={poll_ms})")
    interval = max(poll_ms, 0) / 1000.0

    while not os.path.exists(stop_flag):
        try:
            for h in handle_dialogs_once():
                _log(log_file, f"handled: {h}")
        except Exception as e:
            _log(log_file, f"ERROR during poll (ignored, continuing): {e}")
        time.sleep(interval)

    _log(log_file, "stop-flag seen; exiting")
    for path in (stop_flag, _marker_path(stop_flag)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _cmd_start(a):
    # Clear any stale stop-flag/marker left by a previous (crashed) run.
    for path in (a.stop_flag, _marker_path(a.stop_flag)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    if a.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(a.log_file)), exist_ok=True)

    cmd = [sys.executable, os.path.abspath(__file__), "_loop",
           "--stop-flag", a.stop_flag, "--poll-ms", str(a.poll_ms)]
    if a.log_file:
        cmd += ["--log-file", a.log_file]

    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: survive this (short-lived)
        # `start` process exiting, and don't inherit its console.
        creationflags = 0x00000008 | 0x00000200

    proc = subprocess.Popen(cmd, creationflags=creationflags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    # Marker written after launch, so a reader never sees "active" with no process.
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
    # Best-effort: wait briefly for the loop to notice and remove the flag.
    deadline = time.time() + a.wait_ms / 1000.0
    while time.time() < deadline:
        if not os.path.exists(a.stop_flag):
            print("watcher stopped")
            return
        time.sleep(0.2)
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
    p_start.add_argument("--log-file", dest="log_file", default=None)

    p_stop = sub.add_parser("stop", help="signal the background watcher to exit")
    p_stop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_stop.add_argument("--wait-ms", dest="wait_ms", type=int, default=5000)

    p_loop = sub.add_parser("_loop", help=argparse.SUPPRESS)  # internal use only
    p_loop.add_argument("--stop-flag", dest="stop_flag", required=True)
    p_loop.add_argument("--poll-ms", dest="poll_ms", type=int, default=1000)
    p_loop.add_argument("--log-file", dest="log_file", default=None)

    a = p.parse_args()

    if a.cmd == "start":
        _cmd_start(a)
    elif a.cmd == "stop":
        _cmd_stop(a)
    elif a.cmd == "_loop":
        _loop(a.stop_flag, a.poll_ms, a.log_file)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(0 if (len(sys.argv) > 1 and sys.argv[1] == "start") else 1)
