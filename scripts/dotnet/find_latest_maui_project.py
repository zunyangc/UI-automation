"""Find the newest MAUI project directory under the default Projects root.

Visual Studio's default location for new C# projects is
``%USERPROFILE%\\source\\repos``, so this helper searches there by default
(override with ``--root``). It picks the directory whose ``.csproj`` file
was most recently modified and whose name matches ``MauiApp\\d+`` (the
default auto-suggested MAUI project name).

Prints the absolute path to that project directory as a single stdout row
so a test can capture it via ``$.cols[0]``.

Exit codes:
  0 project found; path printed
  1 no matching project found
  2 usage / IO error
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _csproj import find_csproj  # noqa: E402


PROJECT_RE = re.compile(r"^MauiApp\d+$")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=None,
                   help='project search root (defaults to %%USERPROFILE%%\\source\\repos)')
    p.add_argument("--newer-than", type=float, default=0,
                   help="unix mtime lower bound; useful to make sure we pick up a "
                        "project created after the current test iteration started")
    a = p.parse_args()

    root = a.root or os.path.join(os.path.expanduser("~"), "source", "repos")
    if not os.path.isdir(root):
        print(f"ERROR: root {root!r} not found", file=sys.stderr); sys.exit(2)

    candidates = []
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        if not os.path.isdir(full) or not PROJECT_RE.match(entry):
            continue
        csproj = find_csproj(full)
        if not csproj:
            continue
        mt = os.path.getmtime(csproj)
        if mt < a.newer_than:
            continue
        candidates.append((mt, os.path.dirname(csproj)))

    if not candidates:
        print(f"no MauiApp<N> project with .csproj under {root}", file=sys.stderr)
        sys.exit(1)
    candidates.sort(reverse=True)
    print(candidates[0][1])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
