"""Shared helpers for MAUI .csproj discovery.

The three dotnet scripts (`find_latest_maui_project`, `verify_maui_csproj`,
`copy_maui_csproj`) all need to locate the first ``*.csproj`` under a
directory. Centralising the glob here removes drift between the callers.
"""
import glob
import os
import sys
from typing import Optional


def find_csproj(project_dir: str) -> Optional[str]:
    """Return the first ``*.csproj`` under ``project_dir``.

    Searches non-recursively first, then falls back to one subdirectory
    level. Returns ``None`` when no csproj is found so the caller can
    emit a suitable error and exit code.
    """
    if not os.path.isdir(project_dir):
        return None
    hits = sorted(glob.glob(os.path.join(project_dir, "*.csproj")))
    if not hits:
        hits = sorted(glob.glob(os.path.join(project_dir, "*", "*.csproj")))
    return hits[0] if hits else None


def require_csproj(project_dir: str, exit_code: int = 1) -> str:
    """Return the resolved csproj path or exit with ``exit_code``."""
    if not os.path.isdir(project_dir):
        print(f"ERROR: project dir not found: {project_dir}", file=sys.stderr)
        sys.exit(2)
    path = find_csproj(project_dir)
    if not path:
        print(f"ERROR: no .csproj under {project_dir}", file=sys.stderr)
        sys.exit(exit_code)
    return path
