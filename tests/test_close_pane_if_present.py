"""Unit tests for scripts/uia/close_pane_if_present.py: tolerant closing of a
nested tool-window pane (e.g. Visual Studio's "Live Unit Testing" window) by
title, scoped to an already-known parent window's descendants."""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "uia"))

import close_pane_if_present as cpip  # noqa: E402


def _control(control_type, name, buttons=None):
    control = mock.Mock()
    control.element_info = mock.Mock(control_type=control_type)
    control.window_text.return_value = name
    control.descendants.return_value = buttons or []
    return control


def _button(name):
    b = mock.Mock()
    b.element_info = mock.Mock(control_type="Button")
    b.window_text.return_value = name
    return b


def _run(argv, win):
    with mock.patch.object(sys, "argv", ["close_pane_if_present.py"] + argv), \
            mock.patch.object(cpip, "Application") as application:
        application.return_value.connect.return_value.window.return_value = win
        try:
            cpip.main()
            return 0
        except SystemExit as e:
            return e.code or 0


class PaneAbsentTests(unittest.TestCase):
    def test_no_matching_pane_is_a_tolerant_no_op(self):
        win = mock.Mock()
        win.descendants.return_value = []

        out = io.StringIO()
        with redirect_stdout(out):
            code = _run(["123", "^Live Unit Testing$", "--timeout-ms", "0"], win)

        self.assertEqual(code, 0)
        self.assertIn("skipping", out.getvalue())

    def test_no_matching_pane_with_required_exits_1(self):
        win = mock.Mock()
        win.descendants.return_value = []

        err = io.StringIO()
        with redirect_stderr(err):
            code = _run(["123", "^Live Unit Testing$", "--timeout-ms", "0", "--required"], win)

        self.assertEqual(code, 1)


class PaneClosedTests(unittest.TestCase):
    def test_closes_plain_close_button_not_the_hide_variant(self):
        # VS panes expose multiple similarly-named buttons (e.g. a "hide" action
        # labeled "Close (Shift+Esc)"); only the plain "Close" title-bar button
        # should be matched and invoked with the default exact match.
        hide_btn = _button("Close (Shift+Esc)")
        close_btn = _button("Close")
        pane = _control("Window", "Live Unit Testing", buttons=[hide_btn, close_btn])
        win = mock.Mock()
        win.descendants.return_value = [pane]

        out = io.StringIO()
        with redirect_stdout(out):
            code = _run(["123", "^Live Unit Testing$"], win)

        self.assertEqual(code, 0)
        close_btn.invoke.assert_called_once()
        hide_btn.invoke.assert_not_called()
        self.assertIn("closed pane", out.getvalue())

    def test_pane_found_but_button_missing_is_tolerant_no_op(self):
        pane = _control("Window", "Live Unit Testing", buttons=[])
        win = mock.Mock()
        win.descendants.return_value = [pane]

        out = io.StringIO()
        with redirect_stdout(out):
            code = _run(["123", "^Live Unit Testing$"], win)

        self.assertEqual(code, 0)
        self.assertIn("skipping", out.getvalue())

    def test_falls_back_to_click_input_when_invoke_fails(self):
        close_btn = _button("Close")
        close_btn.invoke.side_effect = RuntimeError("invoke unsupported")
        pane = _control("Window", "Live Unit Testing", buttons=[close_btn])
        win = mock.Mock()
        win.descendants.return_value = [pane]

        code = _run(["123", "^Live Unit Testing$"], win)

        self.assertEqual(code, 0)
        close_btn.click_input.assert_called_once()


if __name__ == "__main__":
    unittest.main()
