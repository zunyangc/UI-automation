"""Opportunistically set Visual Studio as the default app for .sln files.

Several e2e scenarios open a solution by double-clicking a .sln file in File
Explorer. On a fresh Windows profile .sln has no default app association, so
that double-click pops the "Open With" chooser instead of launching Visual
Studio, and the test's subsequent "wait for the VS window" step times out.

This script triggers that chooser once (via a disposable temp .sln), picks a
"Visual Studio Insiders"/"Visual Studio" entry from the suggested-apps list,
and clicks "Always" so Windows remembers the association -- after this, every
later double-click of a real .sln in the test opens Visual Studio directly,
with no dialog in the way. If .sln is already associated, the chooser never
appears and this script is a no-op (it always exits 0; it is a best-effort
environment fixup, not a test assertion).
"""
import ctypes
import os
import re
import sys
import tempfile
import time
from ctypes import wintypes

try:
    from pywinauto import Desktop
except Exception as e:
    print(f"ERROR: pywinauto import failed: {e}", file=sys.stderr)
    sys.exit(0)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_TERMINATE = 0x0001
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP_NAME_PATTERNS = ("^Visual Studio Insiders", "^Visual Studio$", "^Microsoft Visual Studio")


def find_open_with_dialog(timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            for w in Desktop(backend="uia").windows(class_name="Open With"):
                return w
        except Exception:
            pass
        time.sleep(0.25)
    return None


def pick_vs_and_confirm(dlg):
    try:
        items = dlg.descendants(control_type="ListItem")
    except Exception as e:
        print(f"    could not enumerate app list: {e}")
        return False

    target = None
    for pattern in APP_NAME_PATTERNS:
        rx = re.compile(pattern, re.IGNORECASE)
        for it in items:
            try:
                name = it.window_text() or ""
            except Exception:
                continue
            if rx.search(name):
                target = it
                break
        if target:
            break

    if target is None:
        print("    no Visual Studio entry found in the Open With list")
        return False

    try:
        target.click_input()
    except Exception as e:
        print(f"    failed to select Visual Studio entry: {e}")
        return False

    time.sleep(0.3)
    try:
        always_btn = dlg.child_window(auto_id="OpenWith_AlwaysButton", control_type="Button")
        always_btn.click_input()
    except Exception as e:
        print(f"    failed to click Always: {e}")
        return False

    return True


def close_stray_devenv():
    """Best-effort: force-close whatever Visual Studio window opened for our
    disposable probe .sln (it has nothing worth keeping/saving)."""
    time.sleep(2.0)
    try:
        wins = Desktop(backend="uia").windows()
    except Exception:
        return
    for w in wins:
        try:
            title = w.window_text() or ""
        except Exception:
            continue
        if "Visual Studio" not in title:
            continue
        try:
            hwnd = w.handle
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid.value)
            if h:
                kernel32.TerminateProcess(h, 1)
                kernel32.CloseHandle(h)
                print(f"    closed stray '{title}' window (pid {pid.value})")
        except Exception:
            continue


def main():
    tmpdir = tempfile.mkdtemp(prefix="sln_assoc_")
    tmp_sln = os.path.join(tmpdir, "assoc_probe.sln")
    with open(tmp_sln, "w", encoding="utf-8") as f:
        f.write("")

    try:
        os.startfile(tmp_sln)  # noqa: S606 -- same as double-clicking in Explorer
    except Exception as e:
        print(f"ERROR: could not open probe .sln: {e}", file=sys.stderr)
        sys.exit(0)

    dlg = find_open_with_dialog(timeout_s=5)
    if dlg is None:
        print("no Open With dialog appeared; .sln already has a default app (no-op)")
    else:
        ok = pick_vs_and_confirm(dlg)
        if ok:
            print("selected Visual Studio and set it as the default app for .sln")
        else:
            print("Open With dialog appeared but could not be driven to completion")

    close_stray_devenv()

    # The just-closed VS process may briefly hold the probe file open; retry
    # a few times before giving up (leftover temp files are harmless either
    # way, but worth cleaning up when possible).
    for _ in range(5):
        try:
            os.remove(tmp_sln)
            os.rmdir(tmpdir)
            break
        except Exception:
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(0)
