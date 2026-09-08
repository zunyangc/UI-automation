"""Unit tests for scripts/uia/read_console.py without a live window."""
import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "uia"))

import read_console  # noqa: E402


def _control(control_type, class_name, name=None, value=None):
    control = mock.Mock()
    control.element_info = SimpleNamespace(
        control_type=control_type, class_name=class_name, element=mock.Mock())
    control.window_text.return_value = name
    if value is not None:
        control.iface_value = SimpleNamespace(CurrentValue=value)
    else:
        control.iface_value = None
    control.legacy_properties.return_value = {}
    return control


def _run(hwnd, win):
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["read_console.py", str(hwnd)]), \
            mock.patch.object(read_console, "Application") as application, \
            contextlib.redirect_stdout(out):
        application.return_value.connect.return_value.window.return_value = win
        read_console.main()
    return out.getvalue()


class TermControlNameTextTests(unittest.TestCase):
    """The Windows Terminal-hosted VS Debug Console exposes its scrollback as the
    `name` of a control_type=Text / class=TermControl control (verified live).
    This is checked before the risky Ctrl+A clipboard fallback, which would
    otherwise close the console (it closes on any keypress once the app exits)."""

    def test_reads_term_control_name_without_keystrokes(self):
        term = _control("Text", "TermControl",
                        name="Hello, World!\r\nPress any key to close this window . . .\r\n")
        win = mock.Mock()
        win.descendants.return_value = [term]

        with mock.patch.object(read_console, "send_keys") as send_keys:
            out = _run(123, win)

        send_keys.assert_not_called()
        self.assertIn("Hello, World!", out)
        self.assertIn("Press any key to close this window", out)

    def test_prefers_longest_term_control_when_multiple(self):
        short = _control("Text", "TermControl", name="short")
        long_ctrl = _control("Text", "TermControl", name="a much longer console body")
        win = mock.Mock()
        win.descendants.return_value = [short, long_ctrl]

        out = _run(123, win)

        self.assertIn("a much longer console body", out)

    def test_document_control_still_takes_priority(self):
        # Pre-existing behavior (classic console / PowerShell Document with a
        # readable Value) must still win over the new TermControl fallback.
        document = _control("Document", "", value="classic console text")
        term = _control("Text", "TermControl", name="should not be used")
        win = mock.Mock()
        win.descendants.return_value = [document, term]

        out = _run(123, win)

        self.assertIn("classic console text", out)
        self.assertNotIn("should not be used", out)

    def test_falls_back_to_clipboard_when_no_document_or_term_control(self):
        plain = _control("Text", "SomeOtherClass", name="visible label")
        win = mock.Mock()
        win.descendants.return_value = [plain]

        with mock.patch.object(read_console, "send_keys") as send_keys, \
                mock.patch.object(read_console, "_read_clipboard_text",
                                  return_value="clipboard text"):
            out = _run(123, win)

        send_keys.assert_any_call("^a")
        send_keys.assert_any_call("^c")
        self.assertIn("clipboard text", out)


if __name__ == "__main__":
    unittest.main()
