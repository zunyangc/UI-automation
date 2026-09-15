"""In-DevBox GUI runner for the UI-automation test suite.

Wraps the existing `run.ps1 <spec> -q` contract in a Tkinter GUI so a
tester can pick test case(s), run them, and inspect results/screenshots
without needing GitHub Actions or a self-hosted runner registration.

Entry point: `run_gui.ps1` (repo root) -> `runner_app/app.py`.
"""
