"""Close a floating/docked tool-window pane inside another window, by title, if present.

Tolerant by default (like click_in_dialog.py): if no matching pane is found within
--timeout-ms, exits 0 as a no-op. Intended for Visual Studio tool windows that
sometimes auto-open on solution load (e.g. "Live Unit Testing", which VS was observed
live to auto-open and dock over the editor on first load for some solutions) --
call this once right after Visual Studio's window is found, so every test case gets
the same handling instead of each CSV working around the popup individually.

Unlike click_in_dialog.py (which matches a *top-level* window by title), this searches
the DESCENDANTS of an already-known parent window (e.g. the VS main window) for a
nested pane/tool-window control, since VS tool windows are not separate top-level
windows discoverable via Desktop().windows(). It then looks for a plain "Close"
button scoped to *that* pane only (VS panes expose several buttons named similarly,
e.g. "Close (Shift+Esc)" for a "hide" action vs. the plain "Close" title-bar button
that actually dismisses the floating window -- only the latter is clicked by default).

Exit 0 on close OR tolerant no-op; exit 1 if --required and the pane/button is not
found; exit 2 on error.
"""
import argparse, re, sys, time
from pywinauto import Application

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_pane(win, name_rx, control_types, deadline, poll_s):
    while True:
        try:
            for c in win.descendants():
                try:
                    if c.element_info.control_type not in control_types:
                        continue
                    if name_rx.search(c.window_text() or ""):
                        return c
                except Exception:
                    continue
        except Exception:
            pass
        if time.time() >= deadline:
            return None
        time.sleep(poll_s)


def find_button(pane, name, match):
    if match == "exact":
        matcher = lambda t: t == name
    elif match == "regex":
        rx = re.compile(name)
        matcher = lambda t: bool(rx.search(t or ""))
    else:
        matcher = lambda t: name.lower() in (t or "").lower()
    try:
        for c in pane.descendants():
            try:
                if c.element_info.control_type != "Button":
                    continue
                if matcher(c.window_text()):
                    return c
            except Exception:
                continue
    except Exception:
        pass
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hwnd", type=lambda s: int(s, 0), help="parent window hwnd (e.g. the VS main window)")
    p.add_argument("name_regex", help="title of the pane/tool window to close, e.g. '^Live Unit Testing$'")
    p.add_argument("--control-type", dest="control_types", action="append",
                   choices=["Window", "Pane", "Custom"],
                   help="control_type(s) the pane itself may match (default: Window, Pane)")
    p.add_argument("--button", default="Close", help="button name to click (default 'Close')")
    p.add_argument("--match", choices=["exact", "contains", "regex"], default="exact",
                   help="how --button is matched against a control's name (default exact -- "
                        "VS panes often expose similarly-named buttons, e.g. 'Close (Shift+Esc)', "
                        "that must NOT be matched instead of the plain 'Close' title-bar button)")
    p.add_argument("--timeout-ms", dest="timeout_ms", type=int, default=3000,
                   help="how long to wait for the pane to appear (default 3000)")
    p.add_argument("--poll-ms", dest="poll_ms", type=int, default=300)
    p.add_argument("--required", action="store_true",
                   help="exit 1 if the pane/button is not found (default: tolerant no-op)")
    a = p.parse_args()

    control_types = a.control_types or ["Window", "Pane"]
    rx = re.compile(a.name_regex)
    deadline = time.time() + max(0, a.timeout_ms) / 1000.0
    poll_s = max(a.poll_ms, 0) / 1000.0

    app = Application(backend="uia").connect(handle=a.hwnd)
    win = app.window(handle=a.hwnd)

    pane = find_pane(win, rx, control_types, deadline, poll_s)
    if pane is None:
        if a.required:
            print(f"no pane matching {a.name_regex!r}", file=sys.stderr); sys.exit(1)
        print(f"no pane matching {a.name_regex!r}; skipping")
        return

    title = pane.window_text()
    btn = find_button(pane, a.button, a.match)
    if btn is None:
        if a.required:
            print(f"button {a.button!r} not found in pane {title!r}", file=sys.stderr); sys.exit(1)
        print(f"pane {title!r} found but button {a.button!r} not found; skipping")
        return

    for action in ("invoke", "click_input"):
        try:
            getattr(btn, action)()
            print(f"closed pane {title!r} via {a.button!r} ({action})")
            return
        except Exception:
            continue
    print(f"ERROR: found button {a.button!r} in pane {title!r} but all click methods failed",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
