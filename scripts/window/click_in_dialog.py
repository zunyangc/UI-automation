"""Find a dialog window by title regex and click a button inside it, if present.

Tolerant by default: if the dialog (or button) is not found within --timeout, exit 0
without doing anything (a no-op) unless --required is given. This lets a test step
handle dialogs that only appear sometimes (e.g. a NuGet 'License Acceptance' that some
packages show and others do not) in a single fast step, instead of a brittle
find_window + find_control + click sequence that fails when the dialog is absent.

Exit 0 on click OR tolerant no-op; exit 1 if --required and not found; exit 2 on error.
"""
import argparse, re, sys, time
from pywinauto import Desktop, Application

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_dialog(title_rx, backend, deadline):
    rx = re.compile(title_rx)
    backends = ["uia", "win32"] if backend == "any" else [backend]
    while True:
        for b in backends:
            try:
                windows = Desktop(backend=b).windows()
            except Exception:
                # A window can close mid-enumeration (e.g. a transient dialog from a
                # prior step), which raises rather than just skipping that window.
                # Tolerant by design: retry on the next poll instead of crashing.
                windows = []
            for w in windows:
                try:
                    if rx.search(w.window_text() or ""):
                        return w
                except Exception:
                    continue
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("title_regex")
    p.add_argument("--button", required=True, help="button name to click")
    p.add_argument("--auto-id", dest="auto_id", default=None)
    p.add_argument("--match", choices=["exact", "contains", "regex"], default="contains")
    p.add_argument("--find-backend", dest="find_backend", choices=["uia", "win32", "any"], default="win32",
                   help="backend used to DISCOVER the dialog (win32 reliably sees NuGet dialogs; "
                        "'any' also checks uia, needed for modern flyouts like Explorer's "
                        "'N Interrupted Actions' panel)")
    p.add_argument("--timeout", type=float, default=4.0,
                   help="seconds to wait for the dialog to appear (default 4)")
    p.add_argument("--required", action="store_true",
                   help="exit 1 if the dialog/button is not found (default: tolerant no-op)")
    a = p.parse_args()

    deadline = time.time() + max(0.0, a.timeout)
    dlg = find_dialog(a.title_regex, a.find_backend, deadline)
    if dlg is None:
        if a.required:
            print(f"no dialog matching {a.title_regex!r}", file=sys.stderr); sys.exit(1)
        print(f"no dialog matching {a.title_regex!r}; skipping")
        return

    # Discover via win32 but interact via UIA so auto_id matching + invoke() work.
    title = dlg.window_text()
    app = Application(backend="uia").connect(handle=dlg.handle)
    win = app.window(handle=dlg.handle)

    kwargs = {"control_type": "Button"}
    if a.auto_id is not None:
        kwargs["auto_id"] = a.auto_id
    if a.match == "exact":
        kwargs["title"] = a.button
    else:
        kwargs["title_re"] = a.button if a.match == "regex" else f".*{re.escape(a.button)}.*"

    btn = win.child_window(**kwargs)
    try:
        if not btn.exists(timeout=1.0):
            raise RuntimeError("not found")
    except Exception:
        if a.required:
            print(f"button {a.button!r} not found in dialog", file=sys.stderr); sys.exit(1)
        print(f"button {a.button!r} not found in dialog; skipping")
        return

    for action in ("invoke", "click_input"):
        try:
            getattr(btn, action)()
            print(f"clicked {a.button!r} in {title!r} via {action}")
            return
        except Exception:
            continue
    print(f"ERROR: found button {a.button!r} but all click methods failed", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
