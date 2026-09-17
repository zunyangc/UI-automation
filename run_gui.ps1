# run_gui.ps1 — Shortcut for: uv run python -m runner_app.app
# Launches the in-DevBox Tkinter GUI runner: pick test case(s) from
# test_cases/, run them (sequential queue), and inspect results +
# screenshots from the Results tab. See docs/gui-runner.md.
#
# Usage:
#   .\run_gui.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv run python -m runner_app.app
exit $LASTEXITCODE
