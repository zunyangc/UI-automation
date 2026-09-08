"""Close every currently open top-level window whose title matches a regex.

Unlike close_window.py (which closes one specific, already-known hwnd), this
sweeps all live top-level windows at call time and closes each match. Intended
as a final cleanup catch-all -- e.g. closing every leftover File Explorer
window a run may have opened, when the exact hwnd(s) still alive at the end
aren't reliably known (some Explorer navigations spawn additional windows
that never get captured into a tracked variable).

Sends WM_CLOSE to each match (same mechanism as close_window.py) and waits up
to --grace-ms for it to disappear. With --force, force-terminates the owning
process for any window still alive after the grace period. No matches at all
is treated as success (nothing to clean up).

Exit codes:
  0  no matches, or every match closed within its grace period
  2  at least one match was still alive after --grace-ms and --force not set
  3  bad usage / unexpected error
"""
import argparse, ctypes, re, sys, time
from ctypes import wintypes
from pywinauto import Desktop

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_CLOSE = 0x0010
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

user32.IsWindow.argtypes = [wintypes.HWND]; user32.IsWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def owning_pid(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def force_kill(pid, exit_code=1):
    h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        raise OSError(f"OpenProcess(TERMINATE, pid={pid}) failed (error {ctypes.get_last_error()})")
    try:
        if not kernel32.TerminateProcess(h, exit_code):
            raise OSError(f"TerminateProcess(pid={pid}) failed (error {ctypes.get_last_error()})")
    finally:
        kernel32.CloseHandle(h)


def find_matches(rx, backends):
    matches = []
    seen = set()
    for backend in backends:
        try:
            windows = Desktop(backend=backend).windows()
        except Exception:
            # A window can close mid-enumeration (e.g. a transient dialog or
            # Explorer window from a prior step), which raises rather than
            # just skipping that window. This script is a best-effort cleanup
            # catch-all, so it must never itself abort the run -- skip this
            # backend's enumeration instead of propagating the exception.
            windows = []
        for w in windows:
            try:
                if w.handle in seen:
                    continue
                title = w.window_text() or ""
                if not rx.search(title):
                    continue
                matches.append((w.handle, title))
                seen.add(w.handle)
            except Exception:
                continue
    return matches


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("title_regex")
    p.add_argument("--backend", choices=["uia", "win32", "any"], default="any")
    p.add_argument("--grace-ms", dest="grace_ms", type=int, default=2000,
                   help="how long to wait per window for WM_CLOSE to take effect (default 2000)")
    p.add_argument("--poll-ms", dest="poll_ms", type=int, default=100)
    p.add_argument("--force", action="store_true",
                   help="TerminateProcess the owning pid of any window still alive after --grace-ms")
    a = p.parse_args()

    rx = re.compile(a.title_regex)
    backends = ["uia", "win32"] if a.backend == "any" else [a.backend]

    matches = find_matches(rx, backends)
    if not matches:
        print("no matching windows (nothing to close)")
        return

    interval = max(a.poll_ms, 0) / 1000.0
    any_failed = False
    for hwnd, title in matches:
        if not user32.IsWindow(hwnd):
            continue
        pid = owning_pid(hwnd)
        if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            print(f"ERROR: PostMessage(WM_CLOSE) failed for hwnd={hwnd} ({title!r}) "
                  f"(error {ctypes.get_last_error()})", file=sys.stderr)
            any_failed = True
            continue

        deadline = time.time() + a.grace_ms / 1000.0
        closed = False
        while time.time() < deadline:
            if not user32.IsWindow(hwnd):
                closed = True
                break
            time.sleep(interval)

        if closed:
            print(f"closed hwnd={hwnd} ({title!r})")
            continue

        if not a.force:
            print(f"window hwnd={hwnd} ({title!r}) still alive after {a.grace_ms}ms "
                  f"and --force not set", file=sys.stderr)
            any_failed = True
            continue

        try:
            force_kill(pid)
            print(f"force-killed hwnd={hwnd} ({title!r}) pid={pid} after WM_CLOSE timeout")
        except OSError as e:
            print(f"ERROR: force kill failed for hwnd={hwnd} ({title!r}): {e}", file=sys.stderr)
            any_failed = True

    if any_failed:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(3)
