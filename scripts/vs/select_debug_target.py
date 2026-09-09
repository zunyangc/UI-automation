r"""Open the Visual Studio Debug Target SplitButton (the small down-arrow next to `Windows Machine` on the toolbar) and locate the `Framework (netX.Y-windowsX.Y.Z)` entry that corresponds to `--net-version`.

The Debug Target control is exposed to UIA as a ``SplitButton`` whose accessibility name is literally ``Debug Target``. Its legacy IAccessible ``Help`` field holds the currently-selected target label (e.g. ``Windows Machine`` for a MAUI project), which is our verification hook that the default target is a Windows-machine one. The button rectangle covers both the label and a small drop-down chevron on its right edge — clicking near the right-inside edge of the rectangle expands the drop-down. There is no separate UIA control for the chevron; only the rectangle exists.

Once the drop-down is expanded, its items appear on a top-level popup and expose ``MenuItem`` nodes named like ``Framework (net10.0-windows10.0.19041.0)``. WPF does not expose per-item selection state through UIA for these items, so we verify "default is a Windows target" from the SplitButton's Help field before we open the drop-down, then read the drop-down purely to extract the exact TFM string.

Workflow:
  1. Find the ``Debug Target`` SplitButton and read its legacy Help field. Assert Help contains ``Windows Machine`` (the spec requirement: default target is the netX.Y-windows one).
  2. Click ~8px inside the SplitButton's right edge to expand the drop-down.
  3. Scan the desktop for a ``MenuItem`` whose name matches ``^Framework \(net<major>\.<minor>-windows[\d.]+\)$``.
  4. Extract the Windows TFM from the parentheses (e.g. ``net10.0-windows10.0.19041.0``).
  5. Click the menu item so the selection is explicit (matches spec step 20).
  6. Print the TFM on stdout so the caller can capture it via ``$.cols[0]``.

Exit codes:
  0 OK — TFM printed to stdout
  1 default target is not Windows Machine, or no matching drop-down entry
  2 usage / UIA errors
"""
import argparse, re, sys, time

from pywinauto import Application, Desktop
from pywinauto.keyboard import send_keys
from pywinauto.mouse import click as mouse_click

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


NET_RE = re.compile(r"\.NET\s+(\d+)\.(\d+)")


def find_split_button(win, timeout_ms):
    """Return the ``Debug Target`` SplitButton element, or None on timeout."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            for c in win.descendants(control_type="SplitButton"):
                if (c.element_info.name or "").strip() == "Debug Target":
                    return c
        except Exception:
            pass
        time.sleep(0.3)
    return None


def scan_menu(prefix_re, timeout_ms):
    """Scan the desktop for the first MenuItem whose name matches ``prefix_re``."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    for elem in w.descendants(control_type="MenuItem"):
                        name = (elem.element_info.name or "").strip()
                        m = prefix_re.match(name)
                        if m:
                            return elem, name
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.3)
    return None, None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hwnd", type=lambda s: int(s, 0),
                   help="Visual Studio IDE window handle")
    p.add_argument("--net-version", required=True,
                   help='the ".NET X.Y" combo label from the wizard, e.g. ".NET 10.0 (Long Term Support)"')
    p.add_argument("--timeout-ms", dest="timeout_ms", type=int, default=20000)
    p.add_argument("--expected-target", default="Windows Machine",
                   help="expected value of the SplitButton's Help field (default: `Windows Machine`)")
    a = p.parse_args()

    m = NET_RE.search(a.net_version)
    if not m:
        print(f"ERROR: could not parse major.minor from --net-version {a.net_version!r}",
              file=sys.stderr); sys.exit(2)
    major, minor = m.group(1), m.group(2)
    entry_re = re.compile(
        r"^Framework\s+\(net" + re.escape(f"{major}.{minor}") + r"-windows[\d.]+\)$"
    )
    tfm_extract_re = re.compile(r"\(([^)]+)\)")

    app = Application(backend="uia").connect(handle=a.hwnd)
    win = app.window(handle=a.hwnd)

    split = find_split_button(win, a.timeout_ms)
    if split is None:
        print("ERROR: could not locate the `Debug Target` SplitButton on the VS toolbar",
              file=sys.stderr)
        sys.exit(1)

    try:
        legacy = split.legacy_properties() or {}
    except Exception as e:
        print(f"ERROR: failed to read SplitButton legacy properties: {e}", file=sys.stderr)
        sys.exit(2)
    default_target = (legacy.get("Help") or "").strip()
    if a.expected_target not in default_target:
        print(f"ERROR: default debug target Help={default_target!r} does not contain "
              f"{a.expected_target!r}; the Windows target is not the default",
              file=sys.stderr)
        sys.exit(1)

    r = split.rectangle()
    click_x = r.right - 8
    click_y = (r.top + r.bottom) // 2

    try:
        win.set_focus()
    except Exception:
        pass
    try:
        mouse_click(coords=(click_x, click_y))
    except Exception as e:
        print(f"ERROR: failed to click drop-down chevron at ({click_x},{click_y}): {e}",
              file=sys.stderr); sys.exit(2)
    time.sleep(0.6)

    elem, name = scan_menu(entry_re, a.timeout_ms)
    if elem is None:
        try:
            send_keys("{ESC}")
        except Exception:
            pass
        print(f"ERROR: no drop-down MenuItem matching {entry_re.pattern!r}", file=sys.stderr)
        sys.exit(1)

    tfm_m = tfm_extract_re.search(name)
    if not tfm_m:
        try:
            send_keys("{ESC}")
        except Exception:
            pass
        print(f"ERROR: could not extract TFM from menu item name {name!r}", file=sys.stderr)
        sys.exit(1)
    tfm = tfm_m.group(1)

    try:
        elem.invoke()
    except Exception:
        try:
            elem.click_input()
        except Exception as e:
            print(f"ERROR: failed to click {name!r}: {e}", file=sys.stderr); sys.exit(2)
    time.sleep(0.4)

    print(tfm)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)

