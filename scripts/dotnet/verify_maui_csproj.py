"""Verify a MAUI ``.csproj`` file contains the expected ``TargetFrameworks`` entries.

Given a project directory (where the ``.csproj`` lives) and a chosen
``.NET X.Y`` label plus a Windows target-framework triple (e.g.
``net10.0-windows10.0.19041.0``), assert every required TargetFrameworks
substring is present in the ``.csproj`` file:

  - ``netX.Y-android``
  - ``netX.Y-ios``
  - ``netX.Y-maccatalyst``
  - the given ``--win-target`` (must have the same major.minor as the label)

The MAUI template today combines the non-Windows target frameworks into a
single ``<TargetFrameworks>`` tag and adds the Windows one under a
platform-conditioned ``<TargetFrameworks Condition="...">`` tag. Because the
exact grouping and ordering can vary by template version, this checker uses
substring matching per token — that's what the ui-automation spec (steps
26-28) asks for ("if not match, fail").

Exit codes:
  0 all substrings present
  1 a required substring was missing (prints the missing tokens and dumps the
    csproj to stderr for the runner log)
  2 usage error / csproj not found / label unparseable
"""
import argparse, glob, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _csproj import require_csproj  # noqa: E402


DOTNET_RE = re.compile(r"\.NET\s+(\d+)\.(\d+)")
WIN_RE = re.compile(r"^net(\d+)\.(\d+)-windows[\d.]+$")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project_dir",
                   help="directory containing the .csproj (searched non-recursively first, then one level down)")
    p.add_argument("--dotnet-label", required=True,
                   help='the framework combo item text, e.g. ".NET 10.0 (Long Term Support)"')
    p.add_argument("--win-target", default=None,
                   help='(optional) explicit Windows TFM e.g. net10.0-windows10.0.19041.0; if omitted the checker searches the csproj for netX.Y-windows[.\\d]+ and uses the first match.')
    a = p.parse_args()

    m = DOTNET_RE.search(a.dotnet_label)
    if not m:
        print(f"ERROR: could not parse .NET major.minor from --dotnet-label {a.dotnet_label!r}",
              file=sys.stderr); sys.exit(2)
    major, minor = m.group(1), m.group(2)
    tfm = f"net{major}.{minor}"

    win_target = None
    if a.win_target:
        wm = WIN_RE.match(a.win_target.strip())
        if not wm:
            print(f"ERROR: --win-target {a.win_target!r} is not shaped like net<MAJOR>.<MINOR>-windows...",
                  file=sys.stderr); sys.exit(2)
        if (wm.group(1), wm.group(2)) != (major, minor):
            print(f"ERROR: --win-target major.minor ({wm.group(1)}.{wm.group(2)}) "
                  f"does not match --dotnet-label ({major}.{minor})", file=sys.stderr)
            sys.exit(1)
        win_target = a.win_target.strip()

    csproj = require_csproj(a.project_dir)
    with open(csproj, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    if win_target is None:
        wt_re = re.compile(rf"net{major}\.{minor}-windows[\d.]+")
        found = wt_re.search(content)
        if not found:
            print(f"ERROR: {csproj} has no net{major}.{minor}-windows<version> TFM",
                  file=sys.stderr)
            sys.stderr.write(content + "\n")
            sys.exit(1)
        win_target = found.group(0)

    required = [
        f"{tfm}-android",
        f"{tfm}-ios",
        f"{tfm}-maccatalyst",
        win_target,
    ]
    if "<Project Sdk=\"Microsoft.NET.Sdk\">" not in content:
        # spec step 24: must open with this line -- accept minor whitespace variance
        if not re.search(r'<Project\s+Sdk="Microsoft\.NET\.Sdk">', content):
            print(f"ERROR: {csproj} does not open with <Project Sdk=\"Microsoft.NET.Sdk\">",
                  file=sys.stderr)
            sys.stderr.write(content[:800] + "\n")
            sys.exit(1)

    missing = [t for t in required if t not in content]
    if missing:
        print(f"ERROR: {csproj} is missing required TargetFrameworks tokens: {missing}",
              file=sys.stderr)
        sys.stderr.write("--- csproj content ---\n")
        sys.stderr.write(content + "\n")
        sys.exit(1)
    # stdout: first line is the resolved Windows TFM (for `$.cols[0]` capture),
    # second line is a human OK summary.
    print(win_target)
    print(f"OK: {csproj} contains all required tokens for {tfm} + {win_target}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
