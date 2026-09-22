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
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

try:
    from pywinauto import Desktop
except Exception as e:
    print(f"ERROR: pywinauto import failed: {e}", file=sys.stderr)
    sys.exit(0)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_TERMINATE = 0x0001
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
        buttons = dlg.descendants(control_type="Button")
        always_btn = next(b for b in buttons if b.automation_id() == "OpenWith_AlwaysButton")
        always_btn.click_input()
    except Exception as e:
        print(f"    failed to click Always: {e}")
        return False

    return True


def get_devenv_pids():
    """Return the set of currently running devenv.exe process ids."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq devenv.exe", "/FO", "CSV", "/NH"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("INFO:"):
            continue
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2:
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def close_stray_devenv(pre_existing_pids):
    """Best-effort: force-close only the devenv.exe process(es) newly spawned
    by our disposable probe .sln -- never a devenv.exe that was already
    running before we opened the probe, so we don't risk killing an unrelated
    Visual Studio session with unsaved work."""
    time.sleep(2.0)
    new_pids = get_devenv_pids() - pre_existing_pids
    for pid in new_pids:
        try:
            h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if h:
                kernel32.TerminateProcess(h, 1)
                kernel32.CloseHandle(h)
                print(f"    closed stray devenv.exe opened by probe .sln (pid {pid})")
        except Exception:
            continue


def main():
    tmpdir = tempfile.mkdtemp(prefix="sln_assoc_")
    tmp_sln = os.path.join(tmpdir, "assoc_probe.sln")
    with open(tmp_sln, "w", encoding="utf-8") as f:
        f.write("")

    pre_existing_pids = get_devenv_pids()

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

    close_stray_devenv(pre_existing_pids)

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
