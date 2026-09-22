"""Scan test_cases/*.csv and expose {path, name, description} for the GUI.

Reuses the same `csv_loader.load()` that `run_test.py` uses to parse the
`# CONFIG` section, so this module doesn't duplicate any CSV-format
knowledge -- it only reads the two fields it needs (`name`, `description`)
out of the already-parsed spec dict.
"""
import glob
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_CASES_DIR = os.path.join(REPO_ROOT, "test_cases")

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "csvfmt"))
import csv_loader  # noqa: E402

# Authoring scaffold, not a runnable test case.
SKIP_FILENAMES = {"_template.csv"}


class TestCase:
    """One discovered test case."""

    def __init__(self, path, name, description, error=None):
        self.path = path  # absolute path
        self.name = name
        self.description = description
        self.error = error  # set if the CSV failed to parse

    @property
    def rel_path(self):
        return os.path.relpath(self.path, REPO_ROOT)

    @property
    def file_stem(self):
        """The CSV filename without extension, e.g. `prod-002-hot_reload`."""
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def display_name(self):
        # Always show the actual filename under test_cases/ (not the CSV's
        # internal `# CONFIG name` field) so testers can match what they see
        # in the GUI to what they see when browsing the folder. The CONFIG
        # name (if different) is still shown as part of the description --
        # see RunRow in app.py.
        return self.file_stem

    def __repr__(self):
        return f"TestCase({self.rel_path!r}, name={self.name!r})"


def discover(test_cases_dir=TEST_CASES_DIR):
    """Return a list of TestCase, sorted by filename, skipping the template."""
    paths = sorted(glob.glob(os.path.join(test_cases_dir, "*.csv")))
    cases = []
    for path in paths:
        if os.path.basename(path) in SKIP_FILENAMES:
            continue
        try:
            spec = csv_loader.load(path)
            cases.append(TestCase(
                path=path,
                name=spec.get("name") or os.path.splitext(os.path.basename(path))[0],
                description=spec.get("description") or "",
            ))
        except Exception as e:
            # Don't let one malformed CSV break the whole catalog listing --
            # surface it as a disabled/errored entry instead.
            cases.append(TestCase(
                path=path,
                name=os.path.splitext(os.path.basename(path))[0],
                description="",
                error=str(e),
            ))
    return cases


if __name__ == "__main__":
    for tc in discover():
        marker = f"  [ERROR: {tc.error}]" if tc.error else ""
        print(f"{tc.rel_path}: {tc.display_name} -- {tc.description}{marker}")
