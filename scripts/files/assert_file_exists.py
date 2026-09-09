r"""Assert a file exists (exit 0) or not (with --negate); supports env-var paths.

Optional --delete removes the target first (used for pre/post-test cleanup)
and always exits 0 regardless of whether it existed. A single file uses
os.remove; a directory tree (auto-detected) is removed via Windows
PowerShell's `Remove-Item -Recurse -Force` rather than shutil.rmtree.
This matters for e.g. Visual Studio's Test Explorer, which leaves a
reparse-point-flagged `.vs\...\TestStore\<n>` folder (observed with an
inherited Deny ACE on it) after VS has fully closed: shutil.rmtree doesn't
recognize that reparse tag as a symlink, so it recurses into the folder and
hits Access Denied on the inherited-deny children; Remove-Item correctly
deletes the reparse point as a single unit without touching its contents
(verified live -- this is the only approach of the two that succeeds here).
On PermissionError, a directory delete also tries an ACL reset (`icacls
<path> /reset /T /C /Q`, best-effort) as an extra safety net. Both file and
directory deletes retry (--retries, --retry-ms) as a fallback for genuine
transient locks (e.g. a background process or OneDrive sync briefly holding
a file open).
"""
import argparse, os, shutil, subprocess, sys, time

def _reset_acl(path):
    """Best-effort: clear inherited Deny ACEs that block deletion but aren't
    a process handle. No-op / ignored on failure."""
    if os.name != "nt":
        return
    try:
        subprocess.run(["icacls", path, "/reset", "/T", "/C", "/Q"],
                       capture_output=True, timeout=30)
    except Exception:
        pass

def _remove_directory_tree(path):
    """Remove a directory tree. On Windows, shell out to PowerShell's
    Remove-Item instead of shutil.rmtree -- see module docstring for why
    (reparse-point folders like VS's TestStore need this)."""
    if os.name != "nt":
        shutil.rmtree(path)
        return
    escaped = path.replace("'", "''")
    cp = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"Remove-Item -LiteralPath '{escaped}' -Recurse -Force -ErrorAction Stop"],
        capture_output=True, text=True, timeout=120)
    if cp.returncode == 0:
        return
    stderr = cp.stderr or ""
    if "cannot find path" in stderr.lower():
        raise FileNotFoundError(path)
    raise PermissionError(stderr.strip() or f"Remove-Item failed (exit {cp.returncode})")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path")
    p.add_argument("--delete", action="store_true",
                   help="Remove the file or directory tree if present, then exit 0.")
    p.add_argument("--negate", action="store_true",
                   help="Exit 0 only if the file does NOT exist.")
    p.add_argument("--contains", default=None,
                   help="If set, also assert the file contents include this substring.")
    p.add_argument("--starts-with", dest="starts_with", default=None,
                   help="If set, also assert the file contents begin with this exact substring "
                        "(e.g. to prove a directive is on the literal first line).")
    p.add_argument("--retries", type=int, default=5,
                   help="Delete only: retries on PermissionError/OSError (default 5).")
    p.add_argument("--retry-ms", dest="retry_ms", type=int, default=1000,
                   help="Delete only: delay between retries in ms (default 1000).")
    a = p.parse_args()
    resolved = os.path.expandvars(os.path.expanduser(a.path))

    if a.delete:
        is_dir = os.path.isdir(resolved) and not os.path.islink(resolved)
        remove = (lambda: _remove_directory_tree(resolved)) if is_dir else (lambda: os.remove(resolved))
        kind = "directory " if is_dir else ""
        attempts = max(1, a.retries)
        acl_reset_done = False
        for attempt in range(1, attempts + 1):
            try:
                remove()
                print(f"deleted {kind}{resolved}")
                return
            except FileNotFoundError:
                print(f"absent (nothing to delete) {resolved}")
                return
            except PermissionError:
                if is_dir and not acl_reset_done:
                    print(f"permission denied; resetting ACLs on {resolved}", file=sys.stderr)
                    _reset_acl(resolved)
                    acl_reset_done = True
                    continue  # retry immediately, doesn't count against the wait budget
                if attempt >= attempts:
                    raise
                print(f"retry {attempt}/{attempts} after permission denied", file=sys.stderr)
                time.sleep(a.retry_ms / 1000.0)
            except OSError as e:
                if attempt >= attempts:
                    raise
                print(f"retry {attempt}/{attempts} after {e}", file=sys.stderr)
                time.sleep(a.retry_ms / 1000.0)
        return

    exists = os.path.isfile(resolved)
    size = os.path.getsize(resolved) if exists else 0
    print(f"path\texists\tsize_bytes\n{resolved}\t{exists}\t{size}")
    want_exists = not a.negate
    if exists != want_exists:
        sys.exit(1)
    if (a.contains is not None or a.starts_with is not None) and exists:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        if a.contains is not None and a.contains not in data:
            print(f"ERROR: file does not contain {a.contains!r}; got {data!r}", file=sys.stderr)
            sys.exit(1)
        if a.contains is not None:
            print(f"contains\t{a.contains!r}\tOK")
        if a.starts_with is not None and not data.startswith(a.starts_with):
            first_line = data.splitlines()[0] if data else ""
            print(f"ERROR: file does not start with {a.starts_with!r}; first line={first_line!r}", file=sys.stderr)
            sys.exit(1)
        if a.starts_with is not None:
            print(f"starts_with\t{a.starts_with!r}\tOK")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
