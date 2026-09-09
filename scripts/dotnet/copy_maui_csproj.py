"""Copy the MAUI project's ``.csproj`` into an artifacts directory.

Given a project directory (searched non-recursively first, then one level
down for a single ``*.csproj``) and a destination directory, this copies
the ``.csproj`` file verbatim so it can be inspected after the test run,
alongside the screenshots and ``frameworks.txt`` queue file.

To avoid one iteration's copy overwriting the previous iteration's copy
when the LOOP body runs multiple times with different .NET versions, an
optional ``--tag`` is inserted before the ``.csproj`` extension. For
example, tag ``net10.0`` copies ``MauiApp7.csproj`` to
``MauiApp7-net10.0.csproj`` in the destination.

Exit codes:
  0 copy succeeded
  1 no .csproj found under the project dir
  2 usage error / destination not writable
"""
import argparse, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _csproj import require_csproj  # noqa: E402


DOTNET_RE = re.compile(r"\.NET\s+(\d+)\.(\d+)")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project_dir",
                   help="directory containing the .csproj (searched non-recursively first, then one level down)")
    p.add_argument("dest_dir",
                   help="destination directory (created if missing)")
    p.add_argument("--tag", default=None,
                   help="optional tag inserted before .csproj extension (e.g. 'net10.0'). "
                        "If it looks like a '.NET X.Y (...)' label, the netX.Y form is extracted.")
    a = p.parse_args()

    src = require_csproj(a.project_dir)
    stem, ext = os.path.splitext(os.path.basename(src))

    tag = a.tag
    if tag:
        m = DOTNET_RE.search(tag)
        if m:
            tag = f"net{m.group(1)}.{m.group(2)}"
        tag = re.sub(r"[^A-Za-z0-9._-]+", "_", tag).strip("_")

    dest_name = f"{stem}-{tag}{ext}" if tag else f"{stem}{ext}"
    try:
        os.makedirs(a.dest_dir, exist_ok=True)
    except Exception as e:
        print(f"ERROR: cannot create dest dir {a.dest_dir}: {e}", file=sys.stderr); sys.exit(2)

    dest = os.path.join(a.dest_dir, dest_name)
    shutil.copy2(src, dest)
    print(dest)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
