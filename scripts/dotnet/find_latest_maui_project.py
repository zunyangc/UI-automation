"""Find the newest MAUI project directory under the default Projects root.

Visual Studio's default location for new C# projects is normally
``%USERPROFILE%\\source\\repos``, so this helper searches there by default
(override with ``--root``). Some machines/VS installs are instead configured
(or fall back) to create new projects directly under ``%USERPROFILE%``, so
as a fallback this also searches the home directory itself when nothing is
found under the primary root. It picks the directory whose ``.csproj`` file
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

    home = os.path.expanduser("~")
    if a.root:
        roots = [a.root]
    else:
        # Primary VS default, with a fallback to the home directory itself
        # for machines/installs where new projects land directly under
        # %USERPROFILE% instead of %USERPROFILE%\source\repos.
        roots = [os.path.join(home, "source", "repos"), home]

    candidates = []
    searched = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        searched.append(root)
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

    if not searched:
        print(f"ERROR: no root found among {roots!r}", file=sys.stderr); sys.exit(2)
    if not candidates:
        print(f"no MauiApp<N> project with .csproj under {searched!r}", file=sys.stderr)
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
