"""Unit tests for scripts/window/close_windows_matching.py: a title-regex
sweep that closes every currently open window matching a pattern (unlike
close_window.py, which targets one already-known hwnd). Used as a cleanup
catch-all for windows a run may have opened without ever capturing their
hwnd into a tracked variable."""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "window"))

import close_windows_matching as cwm  # noqa: E402


def _run_main(argv):
    with mock.patch.object(sys, "argv", ["close_windows_matching.py"] + argv):
        try:
            cwm.main()
            return 0
        except SystemExit as e:
            return e.code or 0


class NoMatchesTests(unittest.TestCase):
    def test_no_matches_is_a_no_op_success(self):
        with mock.patch.object(cwm, "find_matches", return_value=[]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = _run_main(["^Desktop - File Explorer$"])
        self.assertEqual(code, 0)
        self.assertIn("no matching windows", out.getvalue())


class ClosesWithinGraceTests(unittest.TestCase):
    def test_single_match_closed_via_wm_close(self):
        hwnd = 12345
        is_window_calls = {"n": 0}

        def fake_is_window(h):
            is_window_calls["n"] += 1
            # Alive on the first check, gone afterward (WM_CLOSE "took effect").
            return is_window_calls["n"] == 1

        with mock.patch.object(cwm, "find_matches", return_value=[(hwnd, "Desktop - File Explorer")]), \
                mock.patch.object(cwm.user32, "IsWindow", side_effect=fake_is_window), \
                mock.patch.object(cwm.user32, "PostMessageW", return_value=True), \
                mock.patch.object(cwm, "owning_pid", return_value=999):
            out = io.StringIO()
            with redirect_stdout(out):
                code = _run_main(["^Desktop - File Explorer$", "--grace-ms", "500", "--poll-ms", "10"])

        self.assertEqual(code, 0)
        self.assertIn(f"closed hwnd={hwnd}", out.getvalue())


class FindMatchesEnumerationRaceTests(unittest.TestCase):
    # pywinauto's Desktop(...).windows() can raise if a transient window
    # (e.g. an Explorer window this cleanup sweep is meant to close) closes
    # mid-enumeration. find_matches() is used by this best-effort cleanup
    # catch-all, so it must skip that backend's enumeration instead of
    # letting the exception propagate and abort the whole script -- mirrors
    # click_in_dialog.find_dialog()'s handling of the same race.
    def test_skips_backend_when_windows_enumeration_raises(self):
        import re

        good_window = mock.Mock()
        good_window.handle = 123
        good_window.window_text.return_value = "Desktop - File Explorer"

        bad_desktop = mock.Mock()
        bad_desktop.windows.side_effect = RuntimeError("Handle 999 is not a vaild window handle")
        good_desktop = mock.Mock()
        good_desktop.windows.return_value = [good_window]

        with mock.patch.object(cwm, "Desktop", side_effect=[bad_desktop, good_desktop]):
            matches = cwm.find_matches(re.compile("File Explorer"), ["uia", "win32"])

        self.assertEqual(matches, [(123, "Desktop - File Explorer")])

    def test_returns_empty_when_every_backend_enumeration_raises(self):
        import re

        raising_desktop = mock.Mock()
        raising_desktop.windows.side_effect = RuntimeError("Handle X is not a vaild window handle")

        with mock.patch.object(cwm, "Desktop", return_value=raising_desktop):
            matches = cwm.find_matches(re.compile("File Explorer"), ["uia", "win32"])

        self.assertEqual(matches, [])


class StillAliveTests(unittest.TestCase):
    def test_still_alive_without_force_exits_2(self):
        hwnd = 777
        with mock.patch.object(cwm, "find_matches", return_value=[(hwnd, "This PC - File Explorer")]), \
                mock.patch.object(cwm.user32, "IsWindow", return_value=True), \
                mock.patch.object(cwm.user32, "PostMessageW", return_value=True), \
                mock.patch.object(cwm, "owning_pid", return_value=999):
            err = io.StringIO()
            with redirect_stderr(err):
                code = _run_main(["^This PC - File Explorer$", "--grace-ms", "50", "--poll-ms", "10"])

        self.assertEqual(code, 2)
        self.assertIn("still alive", err.getvalue())

    def test_still_alive_with_force_terminates_owning_process(self):
        hwnd = 888
        with mock.patch.object(cwm, "find_matches", return_value=[(hwnd, "This PC - File Explorer")]), \
                mock.patch.object(cwm.user32, "IsWindow", return_value=True), \
                mock.patch.object(cwm.user32, "PostMessageW", return_value=True), \
                mock.patch.object(cwm, "owning_pid", return_value=999), \
                mock.patch.object(cwm, "force_kill") as force_kill_mock:
            out = io.StringIO()
            with redirect_stdout(out):
                code = _run_main(["^This PC - File Explorer$", "--grace-ms", "50",
                                  "--poll-ms", "10", "--force"])

        self.assertEqual(code, 0)
        force_kill_mock.assert_called_once_with(999)
        self.assertIn("force-killed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
