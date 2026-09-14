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
  or click **Run All** to queue every currently visible case.
- **Run All queues cases sequentially, not in parallel.** These tests
  drive real mouse/keyboard/UIA on the desktop, so running several at
  once on one DevBox would cause them to steal focus from each other and
  corrupt results. Selected cases run one after another; the status
  column updates live (`queued` -> `running` -> `pass` / `fail` /
  `error`, matching `run.ps1`'s documented exit codes 0/1/2).
- **Stop Queue** cancels every case that hasn't started yet. **Stop
  Current Run** additionally kills the in-flight `run.ps1` process (and
  its child processes) if you don't want to wait for it to finish on
  its own; that run is recorded with status `cancelled`.
- The log panel at the bottom streams the stdout/stderr of whichever
  case is currently running.

## Results tab

- A history table of every run recorded in `results/*.json` (name,
  start time, status, duration), newest first. Each row is colored
  green/red/etc. to match the Run tab's status colors.
- Selecting a row shows the stdout/stderr tail for that run, plus a
  list of clickable links (by filename) to any screenshots captured
  under that run's `screenshots/{name}-{timestamp}/` directory --
  clicking one opens it in the OS default image viewer.
- **Open Screenshots Folder** opens that directory in Explorer for full
  size viewing. **Refresh** re-scans `results/` in case another app
  session added new runs (a manual `run.ps1` invocation outside the GUI
  doesn't write to `results/` -- only runs started from this app do).

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
