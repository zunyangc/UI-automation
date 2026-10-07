# Run tests from the in-DevBox GUI

An alternative to the [GitHub Actions self-hosted-runner model](REMOTE_RUNNING.md):
a local Tkinter app that wraps `run.ps1` directly on the DevBox. No fork,
no runner registration, no GitHub PAT, no Actions storage quota to manage
-- the tester never leaves the DevBox to trigger or watch a run.

## One-time setup

Same as today -- nothing changes here:

```powershell
irm https://raw.githubusercontent.com/william051200/UI-automation/main/ops/install.ps1 | iex
```

This installs `uv` + `git`, clones the repo to `%USERPROFILE%\UI-automation`,
and installs pinned dependencies.

## Launch the GUI

```powershell
cd $HOME\UI-automation
.\run_gui.ps1
```

(equivalent to `uv run python -m runner_app.app`.) Run this whenever you
want to test -- there's no separate "start" step beyond the one-time
setup above.

## Run tab

- Every `test_cases/*.csv` (except `_template.csv`) is listed with its
  `name` / `description` from the CSV's `# CONFIG` section.
- Type in the **Filter** box to narrow the list by name or description.
- Check the box next to one or more cases, then click **Run Selected**;
  or click **Run All** to queue every currently visible case. **Run
  Failed** re-runs only the cases whose most recently recorded result was
  `fail`/`error` (a `cancelled` run doesn't count as a failure, so it's
  left out).
- **Run All queues cases sequentially, not in parallel.** These tests
  drive real mouse/keyboard/UIA on the desktop, so running several at
  once on one DevBox would cause them to steal focus from each other and
  corrupt results. Selected cases run one after another; the status
  column updates live (`queued` -> `running` -> `pass` / `fail` /
  `error`, matching `run.ps1`'s documented exit codes 0/1/2).
- **Stop Queue** cancels every case that hasn't started yet. **Stop
  Current Run** additionally kills the in-flight `run.ps1` process (and
  its child processes) if you don't want to wait for it to finish on
  its own; that run is recorded with status `cancelled`. **Stop All**
  does both at once.
- The log panel at the bottom streams the stdout/stderr of whichever
  case is currently running.

### Run Suite (Auto-Retry)

A second row of buttons drives [`run_suite.py`](../run_suite.py) (via
`run_suite.ps1`) against the checked cases instead of the regular queue:

- **Run Suite (Auto-Retry)** launches a suite run: each selected case
  runs, any that fail are retried (up to **Max retries**), and the suite
  stops retrying a case once it's "stuck" -- it fails the same way twice
  in a row -- or its own per-case/overall timeouts are hit. This is the
  same auto-retry logic the CLI uses; see the [README](../README.md#run-a-suite-with-auto-retry-and-a-local-report)
  for the full algorithm.
- **Suite Options...** opens a dialog to set **Max retries**, **Per-case
  timeout (minutes)**, and **Overall suite timeout (minutes, 0 =
  disabled)** before launching. These values are kept only for the
  current GUI session (not written to disk), so they reset to the
  defaults (2 retries / 35 min / disabled) the next time you launch the
  app.
- **Stop Suite Run** force-kills the in-flight suite process. Because
  Windows force-kill has no graceful-shutdown hook, this usually means
  no `summary.json`/`report.html` was written for that attempt -- the
  log panel notes "no report was generated" in that case, matching how
  **Stop Current Run** doesn't produce a special report for a cancelled
  regular run either.
- **Open Suite Report** opens the most recently completed suite's
  `report.html` in your default browser. It's disabled until a suite run
  finishes with a report on disk.
- Each case's status pill on the Run tab updates *live* as the suite
  progresses: `queued` while waiting for its turn in the current round,
  `running` while its attempt is in flight, `retrying` (amber) if that
  attempt just failed but another round will try it again, and a settled
  final color (`pass`, `fail`, `stuck`, `retries_exhausted`, `timeout`,
  `not_run_timeout`) once that case has no more attempts coming. A case
  that already settled in an earlier round keeps its final status/color
  instead of being reset to `queued` when a later round starts for the
  cases still being retried. The final sweep over `summary.json` once the
  whole suite finishes is still applied as a safety net, in case any
  individual progress line was missed.
- Suite runs write into `result/gui-suite-{timestamp}/` (as opposed to
  the CLI default `result/suite-{timestamp}/`), so it's clear at a
  glance which tool produced a given report folder. Suite runs still
  call the same `save_run()` used elsewhere, so every case attempt also
  shows up in the Results tab like any other run.
- **The regular run queue (Run Selected / Run All / Run Failed) and Run
  Suite are mutually exclusive** -- both ultimately drive `run.ps1`
  processes that would otherwise fight over mouse/keyboard focus on one
  desktop session, so each set of buttons is disabled while the other
  mode is active.

## Results tab

- A history table of every run recorded in `results/*.json` (name,
  start time, status, duration), newest first. Each row is colored
  green/red/etc. to match the Run tab's status colors.
- The **Filter** dropdown (All/Pass/Fail/Cancelled) narrows the table to
  just that outcome; `Fail` matches `fail`/`error` only, `Cancelled` is
  its own option since stopping a run isn't the same as it failing.
- Selecting a row shows the stdout/stderr tail for that run, plus a
  list of clickable links (by filename) to any screenshots captured
  under that run's `screenshots/{name}-{timestamp}/` directory --
  clicking one opens it in the OS default image viewer.
- **Open Screenshots Folder** opens that directory in Explorer for full
  size viewing. **Refresh** re-scans `results/` in case another app
  session added new runs (a manual `run.ps1` invocation outside the GUI
  doesn't write to `results/` -- only runs started from this app do).
- **Download Failed Report** saves a zip you can share with someone else:
  a `summary.txt` with one line per failed run ("Failed at `<case>` test
  case, failed message is `<reason>`.") plus that run's failure
  screenshots (the `*_FAILURE_*.png` files `run_test.py` captures on a
  failing step) under `screenshots/`. With a row selected, it exports
  just that run; with nothing selected, it bundles every recorded
  `fail`/`error` run (again, `cancelled` runs are excluded).

## Settings tab

- **Clear Results** deletes everything under `results/` (all run
  history shown in the Results tab) and also resets the Run tab's live
  status pills and log panel back to their initial (not-yet-run) state,
  so no stale pass/fail/log from a deleted run is left showing.
- **Clear Screenshots** deletes everything under `screenshots/`.
- **Clear All Repos** deletes everything under `%USERPROFILE%\source\repos`
  -- Visual Studio's default project location, where most test cases
  create/build throwaway projects. Use this to wipe leftover clutter from
  past runs.
- **Open Last Suite Report** opens the most recently completed Run Suite
  (Auto-Retry) report in your default browser (same report as the Run
  tab's **Open Suite Report** button, kept here too since Settings is
  the tab testers check between sessions). Disabled until a suite run
  has produced a report in this session.
- All three cleanup buttons are shown with red text (right-aligned, same
  size) and, when clicked, show a Yes/No warning dialog stating the
  action is irreversible before doing anything.
- **Repository** shows a link to the upstream repo, opened in your
  default browser when clicked.

## Notes

- `results/` is a local, gitignored directory (like `screenshots/`) --
  it isn't committed and isn't shared between DevBoxes.
- This is purely additive: the GitHub Actions self-hosted-runner model
  described in [REMOTE_RUNNING.md](REMOTE_RUNNING.md) still works
  unchanged if you prefer it.
