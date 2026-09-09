"""Click a MAUI ``Click me`` button ``N`` times and verify the label after each click.

The MAUI default counter button starts labeled ``Click me`` and updates to
``Clicked 1 time`` after the first click, then ``Clicked 2 times``,
``Clicked 3 times``, ... for subsequent clicks. This script:

  1. Finds a Button whose current name matches either ``Click me`` or a
     ``Clicked N time(s)`` label inside the MauiApp window at ``<hwnd>``.
  2. Clicks it via the UIA Invoke pattern (falls back to mouse click).
  3. Re-reads the button name and asserts it equals the expected label.
  4. Repeats until ``--times`` clicks have been performed.

Exit codes:
  0 all N clicks verified
  1 a label mismatch, or the button was not found
  2 usage error / unexpected UIA failure
"""
import argparse, re, sys, time

from pywinauto import Application

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LABEL_RE = re.compile(r"^(Click me|Clicked \d+ time[s]?)$")


def expected_label(n):
    return f"Clicked {n} time" + ("" if n == 1 else "s")


def find_button(win, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000.0
    last_err = None
    while time.time() < deadline:
        try:
            for c in win.descendants(control_type="Button"):
                name = (c.element_info.name or "").strip()
                if LABEL_RE.match(name):
                    return c, name
        except Exception as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"Click me button not found within {timeout_ms}ms; last err: {last_err}")


def read_name(btn, tries=20, interval=0.15):
    """Poll the button name; MAUI can take a beat to update the accessible name."""
    for _ in range(tries):
        try:
            n = (btn.element_info.name or "").strip()
            if n:
                return n
        except Exception:
            pass
        time.sleep(interval)
    return ""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hwnd", type=lambda s: int(s, 0))
    p.add_argument("--times", type=int, default=5)
    p.add_argument("--find-timeout-ms", dest="find_timeout_ms", type=int, default=8000)
    p.add_argument("--label-poll-ms", dest="label_poll_ms", type=int, default=3000)
    a = p.parse_args()

    app = Application(backend="uia").connect(handle=a.hwnd)
    win = app.window(handle=a.hwnd)
    btn, initial = find_button(win, timeout_ms=a.find_timeout_ms)
    print(f"initial button label: {initial!r}")

    for i in range(1, a.times + 1):
        try:
            btn.invoke()
        except Exception:
            # fall back to a mouse click at the control center
            try:
                btn.click_input()
            except Exception as e:
                print(f"ERROR: click #{i} failed: {e}", file=sys.stderr); sys.exit(2)
        want = expected_label(i)
        deadline = time.time() + a.label_poll_ms / 1000.0
        seen = ""
        while time.time() < deadline:
            seen = read_name(btn, tries=1)
            if seen == want:
                break
            time.sleep(0.2)
        if seen != want:
            print(f"ERROR: after click #{i}, expected {want!r} but got {seen!r}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"click #{i}: label={seen!r}")

    print(f"OK: verified {a.times} clicks")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
