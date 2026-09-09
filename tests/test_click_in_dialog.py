"""Unit tests for scripts/window/click_in_dialog.py's find_dialog(), covering
the tolerant-by-design no-op path without a live window.

A transient window (e.g. a prior step's dialog closing mid-scan) can make
pywinauto's Desktop(...).windows() raise instead of just omitting that window
from the list. find_dialog() must retry rather than let that exception
propagate and fail the whole (supposed to be optional) step."""
import os
import sys
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "window"))

import click_in_dialog  # noqa: E402


class FindDialogEnumerationRaceTests(unittest.TestCase):
    def test_retries_when_windows_enumeration_raises(self):
        good_window = mock.Mock()
        good_window.window_text.return_value = "Replace or Skip Files"

        calls = {"n": 0}

        def fake_windows():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Handle 6685198 is not a vaild window handle")
            return [good_window]

        desktop_instance = mock.Mock()
        desktop_instance.windows.side_effect = fake_windows

        with mock.patch.object(click_in_dialog, "Desktop", return_value=desktop_instance), \
                mock.patch.object(time, "sleep"):
            found = click_in_dialog.find_dialog("Replace or Skip Files", "win32",
                                                 deadline=time.time() + 5)

        self.assertIs(found, good_window)
        self.assertGreaterEqual(calls["n"], 2)

    def test_times_out_as_no_op_when_enumeration_keeps_raising(self):
        desktop_instance = mock.Mock()
        desktop_instance.windows.side_effect = RuntimeError("Handle X is not a vaild window handle")

        with mock.patch.object(click_in_dialog, "Desktop", return_value=desktop_instance), \
                mock.patch.object(time, "sleep"):
            found = click_in_dialog.find_dialog("Replace or Skip Files", "win32",
                                                 deadline=time.time() - 1)

        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
