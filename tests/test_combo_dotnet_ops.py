"""Unit tests for scripts/uia/combo_dotnet_ops.py version selection."""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "uia"))

import combo_dotnet_ops  # noqa: E402


class PickLatestTests(unittest.TestCase):
    def test_ignores_non_dotnet_labels(self):
        name, element = combo_dotnet_ops.pick_latest(
            [("Choose a framework", object()), ("Custom", object())], False)
        self.assertIsNone(name)
        self.assertIsNone(element)

    def test_picks_highest_version(self):
        items = [(".NET 8.0", "8"), (".NET 10.0", "10"), (".NET 9.0", "9")]
        self.assertEqual(combo_dotnet_ops.pick_latest(items, False), (".NET 10.0", "10"))

    def test_prefers_lts_for_same_version(self):
        items = [
            (".NET 10.0 (Standard Term Support)", "sts"),
            (".NET 10.0 (Long Term Support)", "lts"),
        ]
        self.assertEqual(
            combo_dotnet_ops.pick_latest(items, False),
            (".NET 10.0 (Long Term Support)", "lts"))

    def test_preview_requires_preference_when_stable_exists(self):
        items = [
            (".NET 11.0", "stable"),
            (".NET 11.0 Preview 1", "preview"),
        ]
        self.assertEqual(combo_dotnet_ops.pick_latest(items, False), (".NET 11.0", "stable"))
        self.assertEqual(
            combo_dotnet_ops.pick_latest(items, True),
            (".NET 11.0 Preview 1", "preview"))


class _FakeElement:
    def __init__(self, name, combo=None):
        self.name = name
        self.selected = False
        self._combo = combo

    def select(self):
        self.selected = True
        if self._combo is not None:
            self._combo._selected = self.name


class _FakeCombo:
    """Minimal stand-in for the pywinauto ComboBox used by cmd_* handlers."""

    def __init__(self, items, initial_selected):
        self._items = items
        self._selected = initial_selected
        self.collapsed = False
        for _, elem in items:
            elem._combo = self

    def expand(self):
        pass

    def collapse(self):
        self.collapsed = True

    def descendants(self, control_type=None):
        return [elem for _, elem in self._items]

    def selected_text(self):
        return self._selected

    def window_text(self):
        return self._selected

    def select(self, name):
        self._selected = name


class EnsureDefaultIsLatestTests(unittest.TestCase):
    """Exercises the ensure-default-is-latest self-healing logic directly,
    bypassing UIA connection (connect_combo) since that requires a live
    window. list_items() normally reads element_info.name off descendants;
    monkeypatch it here to read our fake elements' .name instead."""

    def setUp(self):
        self._orig_list_items = combo_dotnet_ops.list_items

        def fake_list_items(combo):
            return list(combo._items)

        combo_dotnet_ops.list_items = fake_list_items
        self.addCleanup(setattr, combo_dotnet_ops, "list_items", self._orig_list_items)

    def _run(self, combo, prefer_preview=False, kill_pid=None):
        class Args:
            pass
        args = Args()
        args.hwnd = 0
        args.auto_id = None
        args.name = "Framework"
        args.prefer_preview = prefer_preview
        args.kill_pid = kill_pid

        orig_connect = combo_dotnet_ops.connect_combo
        combo_dotnet_ops.connect_combo = lambda hwnd, auto_id, name: combo
        try:
            combo_dotnet_ops.cmd_ensure_default_is_latest(args)
        finally:
            combo_dotnet_ops.connect_combo = orig_connect

    def test_noop_when_default_already_latest(self):
        items = [(".NET 9.0", _FakeElement(".NET 9.0")),
                  (".NET 10.0 (Long Term Support)", _FakeElement(".NET 10.0 (Long Term Support)"))]
        combo = _FakeCombo(items, ".NET 10.0 (Long Term Support)")
        self._run(combo)
        self.assertFalse(items[1][1].selected)

    def test_corrects_stale_sticky_default(self):
        items = [(".NET 9.0 (Standard Term Support)", _FakeElement(".NET 9.0 (Standard Term Support)")),
                  (".NET 10.0 (Long Term Support)", _FakeElement(".NET 10.0 (Long Term Support)"))]
        combo = _FakeCombo(items, ".NET 9.0 (Standard Term Support)")
        self._run(combo)
        self.assertTrue(items[1][1].selected)
        self.assertEqual(combo._selected, ".NET 10.0 (Long Term Support)")

    def test_exits_nonzero_when_no_items_found(self):
        combo = _FakeCombo([], "Choose a framework")
        with self.assertRaises(SystemExit) as ctx:
            self._run(combo)
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
