"""Print the active .NET SDK version (`dotnet --version`), optionally asserting its major version.

Discovers whatever .NET SDK is currently active on this machine instead of a
test case hardcoding one specific SDK patch (e.g. ``10.0.401``), per the
"discover machine-varying values at runtime" convention (see AGENTS.md). This
keeps a test that pins a ``global.json`` SDK version (via
``dotnet new globaljson --sdk-version <version>``) working unchanged as the
machine's SDK is serviced/patched over time, while still letting the test
assert the SDK is the expected *major* version (e.g. "must be a .NET 10.x
SDK") without caring which exact patch is installed.

Prints the version string (e.g. ``10.0.401``) as a single stdout line so a
test can capture it via ``$.cols[0]``.

  --require-major N   fail (exit 1) unless the SDK's major version equals N.

Exit codes:
  0 - version printed (and matches --require-major, if given)
  1 - version doesn't match --require-major
  2 - `dotnet` not found / usage error
"""
import argparse
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--require-major", type=int, default=None,
                   help="fail unless the active SDK's major version equals this value")
    a = p.parse_args()

    try:
        cp = subprocess.run(["dotnet", "--version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print("ERROR: `dotnet` not found on PATH", file=sys.stderr)
        sys.exit(2)
    if cp.returncode != 0:
        print(f"ERROR: `dotnet --version` exit={cp.returncode}: {cp.stderr}", file=sys.stderr)
        sys.exit(2)

    lines = (cp.stdout or "").strip().splitlines()
    version = lines[-1].strip() if lines else ""
    m = re.match(r"^(\d+)\.", version)
    if not version or not m:
        print(f"ERROR: could not parse SDK version from {version!r}", file=sys.stderr)
        sys.exit(2)

    if a.require_major is not None and int(m.group(1)) != a.require_major:
        print(f"ERROR: active SDK version {version!r} is not major version {a.require_major}",
              file=sys.stderr)
        sys.exit(1)

    print(version)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
