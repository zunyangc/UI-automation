"""Unit tests for scripts/files/assert_file_exists.py, especially the
--delete flag's directory-tree support added for post-test cleanup of
extracted-zip folders (in addition to its original single-file behavior)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "files", "assert_file_exists.py")


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                           capture_output=True, text=True, encoding="utf-8")


class AssertFileExistsTests(unittest.TestCase):
    def test_exists_and_negate(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txt")
            with open(p, "w") as f:
                f.write("hi")
            self.assertEqual(run(p).returncode, 0)
            self.assertEqual(run(p, "--negate").returncode, 1)

    def test_delete_single_file_is_noop_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "missing.txt")
            cp = run(p, "--delete")
            self.assertEqual(cp.returncode, 0)
            self.assertIn("absent", cp.stdout)

    def test_delete_single_file_removes_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txt")
            with open(p, "w") as f:
                f.write("hi")
            cp = run(p, "--delete")
            self.assertEqual(cp.returncode, 0)
            self.assertFalse(os.path.exists(p))

    def test_delete_directory_tree_removes_it_recursively(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "extracted")
            nested = os.path.join(folder, "sub")
            os.makedirs(nested)
            with open(os.path.join(nested, "file.txt"), "w") as f:
                f.write("content")
            cp = run(folder, "--delete")
            self.assertEqual(cp.returncode, 0)
            self.assertIn("directory", cp.stdout)
            self.assertFalse(os.path.exists(folder))

    def test_delete_directory_is_noop_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "does-not-exist")
            cp = run(folder, "--delete")
            self.assertEqual(cp.returncode, 0)
            self.assertIn("absent", cp.stdout)

    def test_delete_directory_retries_then_succeeds_when_transiently_locked(self):
        # Simulate a file inside the tree still held open by a just-closed
        # application (e.g. VS ServiceHub) for one retry cycle, then released.
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "extracted")
            os.makedirs(folder)
            locked_path = os.path.join(folder, "locked.txt")
            with open(locked_path, "w") as f:
                f.write("x")
            handle = open(locked_path, "r+")
            try:
                import threading
                def release():
                    time.sleep(0.5)
                    handle.close()
                threading.Thread(target=release).start()
                cp = run(folder, "--delete", "--retries", "10", "--retry-ms", "200")
            finally:
                if not handle.closed:
                    handle.close()
            # On Windows an open handle blocks deletion of that file; retrying
            # after it's released should eventually succeed.
            if sys.platform.startswith("win"):
                self.assertEqual(cp.returncode, 0)
                self.assertFalse(os.path.exists(folder))


    def test_delete_directory_removes_deny_acl_left_by_test_explorer(self):
        # Reproduces a real observed failure: Visual Studio's Test Explorer
        # leaves a reparse-point-flagged `.vs\...\TestStore\<n>` folder (with
        # an inherited Deny ACE on it, observed live) that survives VS
        # closing. shutil.rmtree doesn't recognize this reparse tag as a
        # symlink, so it recurses into the folder and hits Access Denied on
        # the inherited-deny children -- this is why the script now deletes
        # directories via PowerShell's Remove-Item (which treats a reparse
        # point as a single deletable unit) instead, with an ACL reset as an
        # extra safety net for plain (non-reparse-point) Deny ACEs.
        if not sys.platform.startswith("win"):
            self.skipTest("reparse-point/icacls Deny ACE repro is Windows-only")
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "extracted")
            store = os.path.join(folder, "TestStore")
            os.makedirs(store)
            target_dir = os.path.join(d, "junction_target")
            os.makedirs(target_dir)
            with open(os.path.join(target_dir, "000.testlog"), "w") as f:
                f.write("log")
            reparse_path = os.path.join(store, "0")

            mk = subprocess.run(["cmd", "/c", "mklink", "/J", reparse_path, target_dir],
                                 capture_output=True, text=True)
            self.assertEqual(mk.returncode, 0, mk.stdout + mk.stderr)
            deny = subprocess.run(
                ["icacls", reparse_path, "/deny", "Everyone:(OI)(CI)(DC)"],
                capture_output=True, text=True)
            self.assertEqual(deny.returncode, 0, deny.stderr)

            cp = run(folder, "--delete")

            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertFalse(os.path.exists(folder))


if __name__ == "__main__":
    unittest.main()
