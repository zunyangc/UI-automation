# Dev spec: link the auto-retry suite runner into the GUI

Status: DRAFT for review — not implemented, not committed.

Builds on the already-merged `run_suite.py` / `run_suite.ps1` (branch
`feature/run-suite-auto-retry`). This spec covers surfacing that
capability from the Tkinter GUI (`runner_app/app.py`, launched via
`run_gui.ps1`) instead of only the command line.

## 1. Goal

Let a tester, from inside the GUI's **Run** tab, launch the same
select-cases → retry-only-failures → stuck-detection → local-report flow
that `run_suite.py` already provides on the CLI, and get to the generated
`report.html` without leaving the app.

## 2. Non-goals

- No change to `run_suite.py`'s algorithm, CLI flags, retry/stuck logic,
  or report format — the GUI drives the exact same code path.
- No change to the existing `RunWorker` sequential-queue behavior used by
  **Run Selected** / **Run All** / **Run Failed** — those keep working
  exactly as documented in `docs/gui-runner.md`. This is an additional
  mode, not a replacement.
- No parallel execution, no change to the single-desktop-session
  constraint.

## 3. Current GUI shape (for reference)

`runner_app/app.py` has three tabs:

- **Run** (`RunTab`): checkbox list of `test_catalog.discover()` cases +
  `Run Selected` / `Run All` / `Run Failed` / `Stop Queue` / `Stop
  Current Run` / `Stop All`, driven by `RunWorker` (one case subprocess at
  a time, queue of `TestCase` objects), with a live log panel fed by
  `RunEvent.OUTPUT`.
- **Results** (`ResultsTab`): history table over `results/*.json`
  (`results_store.list_runs()`), with a stdout/stderr tail view, clickable
  failure-screenshot links, "Open Screenshots Folder", and "Download
  Failed Report" (`results_store.write_failure_report_zip`).
- **Settings**: destructive cleanup actions + a repo link
  (`webbrowser.open`).

`run_suite.py` currently has its own self-contained orchestration loop
(`main()`), its own subprocess launch/timeout/kill
(`run_one_attempt()`/`_terminate()`), and its own report writer
(`write_report()`) — it does not go through `RunWorker`/`RunEvent` at all.

## 4. Design decision: reuse `run_suite.py` as a subprocess, not a library

Two integration options were considered:

- **(A) Import `run_suite` functions into the GUI process** and drive them
  from a new worker thread, emitting `RunEvent`-style updates per attempt.
- **(B) Shell out to `run_suite.py` as a child process** (same pattern
  `RunWorker._run_one()` already uses for `run.ps1`), streaming its stdout
  into the existing log panel and polling for its final report path.

**Decision: (B).** Reasons:

- `run_suite.py` already does its own process management (spawns and
  kills `run.ps1`, which itself spawns `devenv`/`MSBuild`/etc.). Running
  that orchestration *inside* the already single-threaded Tkinter process
  would mean either blocking the UI thread for the whole suite or
  re-implementing `run_suite.py`'s timeout/kill logic a second time on a
  background thread — pure duplication of logic that already works and is
  unit-tested.
  Thus the GUI's job is only to build the right `run_suite.py` argv,
  launch it, stream its output, and react to its exit code + report path.
- It mirrors the existing `RunWorker` pattern exactly (`Popen`,
  merged stderr, `taskkill /T /F` for Stop), so there is no second
  process-control idiom to maintain.
- A suite run can take hours; running it as a visible child process means
  "Stop" can kill the entire tree the same way `stop_current()` already
  does, without new cancellation plumbing.

## 5. New Run tab controls

Added to the existing button row in `RunTab.__init__` (after `Stop All`):

| Control | Behavior |
|---|---|
| **Run Suite (Auto-Retry)** button | Launches `run_suite.py` against the currently *selected* checkboxes (same selection model as `Run Selected`). Disabled while a suite run or a regular queued run is in-flight (mirrors how `Run Selected`/`Run All` already queue rather than overlap). |
| **Suite options** gear/button (or inline fields) | Opens a small modal (`tk.Toplevel`) to set `--max-retries` (default 2), `--case-timeout-min` (default 35), `--suite-timeout-min` (default 0/disabled) before launching. Persist last-used values in-memory for the session (not written to disk) so repeated runs don't require re-entering them. |
| **Stop Suite Run** button | Visible/enabled only while a suite run is in-flight; kills the `run_suite.py` process tree via the same `taskkill /PID <pid> /T /F` call `RunWorker._terminate()` uses. The suite's own report is still written for whatever completed before the kill (`run_suite.py`'s `write_report()` runs from its own `finally`-equivalent path — confirm/adjust in implementation so a killed suite still leaves a partial report instead of nothing). |

If no checkboxes are selected, **Run Suite (Auto-Retry)** is a no-op
(same convention as `Run Selected`'s empty-selection check).

### Why not reuse `RunEvent`/`RunWorker` queueing

`RunWorker.enqueue()` queues individual `TestCase` specs for one-at-a-time
`run.ps1` invocations with no retry concept. A suite run is semantically
one unit of work ("run this set with retries") rather than N independent
queue items, and it needs its own distinct status (queued specs still
show `queued`/`running`/`pass`/`fail` per `RunEvent`, which doesn't fit
"this case failed once but is being retried"). So suite runs get a
parallel, simpler state machine (see §6) instead of flowing through
`RunWorker`.

## 6. New `SuiteRunWorker` (parallel to `RunWorker`, not inside it)

A second small worker class, `runner_app/suite_worker.py`, following the
same shape as `run_worker.py` but launching exactly one `run_suite.py`
process per invocation (no internal queue — the GUI already prevents
overlapping suite runs per §5):

```python
class SuiteRunEvent:
    STARTED = "started"     # suite process launched
    OUTPUT = "output"        # one streamed stdout/stderr line
    DONE = "done"             # process exited; includes exit_code, report_dir
    CANCELLED = "cancelled"  # Stop Suite Run was used

class SuiteRunWorker:
    def start(self, spec_paths, max_retries, case_timeout_min, suite_timeout_min): ...
    def stop(self):  # taskkill /T /F, mirrors RunWorker._terminate()
    # events: queue.Queue drained by the GUI's root.after() poll, same
    # pattern as RunWorker.events.
```

`start()` builds:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File run_suite.ps1 <spec1> <spec2> ... `
  --max-retries <n> --case-timeout-min <n> --suite-timeout-min <n> --report-dir <path>
```

`--report-dir` is passed explicitly (not left to `run_suite.py`'s default)
so the GUI knows exactly where to find `report.html` afterward without
parsing stdout for it — e.g. `result/gui-suite-{timestamp}/`.

The GUI's existing `root.after(...)` poll loop (already draining
`worker.events`) is extended to also drain `suite_worker.events` each
tick.

## 7. Surfacing the report once the suite finishes

On `SuiteRunEvent.DONE`:

- Append a one-line summary to the Run tab's log panel, e.g.
  `=== Suite finished: 7 pass, 2 fail, 1 stuck -- report: result/gui-suite-.../report.html ===`
  (counts parsed from the written `summary.json`, matching `run_suite.py`'s
  own console summary — no new counting logic needed, just read the file).
- Add an **Open Suite Report** button next to **Run Suite (Auto-Retry)**
  that becomes enabled once a report exists in this session, and opens
  `report.html` via `webbrowser.open(...)` (same mechanism the Settings
  tab's repo link already uses) — opens in the default browser, not
  embedded in Tkinter.
- Each case's row status pill in the Run tab's list updates to the suite's
  **final** per-case status (`pass`/`fail`/`stuck`/`retries_exhausted`/
  `timeout`/`not_run_timeout`) by reading `summary.json`'s `cases[]` after
  the process exits — reusing the existing `STATUS_COLOR` map, extended
  with entries for `stuck`, `retries_exhausted`, `timeout`, and
  `not_run_timeout` (all mapped to a red/amber color consistent with the
  existing `fail`/`error` treatment).
- Individual retry attempts are **not** streamed as separate row-status
  transitions (that would conflict with `RunEvent`'s existing
  queued/running/done vocabulary) — the live log panel already shows
  `run_suite.py`'s own per-attempt console lines (`--- [name] attempt N
  ---`, `stuck -- will not retry further`, etc.) verbatim, which is enough
  granularity while it's running. Only the final status lands on the row.

## 8. Results tab — no change required

`run_suite.py` already calls `results_store.save_run()` for every attempt,
so every attempt a GUI-launched suite run makes already appears in the
Results tab exactly like a manual `run.ps1`/`Run Selected` run would —
multiple rows per case (one per attempt) with normal pass/fail/duration
columns. No new Results tab work is needed; this is an explicit benefit of
design decision §4 (reusing `run_suite.py`'s existing persistence instead
of writing a separate code path).

## 9. Settings tab addition

One link-style addition, consistent with the existing "Repository" link:

- **Open Last Suite Report** — enabled only if at least one suite report
  exists under `result/gui-suite-*/report.html` this session (or, if
  persisted across restarts is wanted, by scanning `result/gui-suite-*`
  on startup and picking the newest `report.html` by mtime). Opens via
  `webbrowser.open(...)`.

## 10. Documentation updates needed (once implemented)

- `docs/gui-runner.md`: new "Run Suite (Auto-Retry)" subsection under
  **Run tab**, describing the options modal, Stop Suite Run, and Open
  Suite Report — mirroring the existing bullet style for `Run
  Failed`/`Stop Queue`/etc.
- `README.md`: no change expected (the CLI section already documents
  `run_suite.ps1`; the GUI section just gains one more bullet pointing at
  `docs/gui-runner.md`).

## 11. Testing plan

- Unit tests for `SuiteRunWorker` (mocked `subprocess.Popen`, matching the
  existing `tests/test_run_worker.py` style): process launched with the
  expected argv (including `--report-dir`), stdout streamed as `OUTPUT`
  events, `DONE` event carries the parsed `summary.json` counts/report
  path, `stop()` kills via `taskkill` and still emits `DONE`/`CANCELLED`
  appropriately.
- Manual smoke test: run a 2-case selection (one fast-pass case, one
  synthetic always-fail CSV) through **Run Suite (Auto-Retry)**, confirm
  the log streams attempts, the row pills land on final status, **Open
  Suite Report** opens the right `report.html`, and the Results tab shows
  one row per attempt.

## 12. Open questions for reviewer

1. Should **Run Suite (Auto-Retry)** be mutually exclusive with the
   regular `RunWorker` queue (i.e. grey out `Run Selected`/`Run All`/`Run
   Failed` while a suite run is in-flight), or should both be allowed to
   queue independently? Draft assumes mutually exclusive, since both
   ultimately drive `run.ps1` processes that shouldn't compete for
   mouse/keyboard focus.
2. Should suite-run options (`--max-retries` etc.) be remembered across
   GUI restarts (e.g. a small `runner_app` settings file), or is
   session-only memory (§5) sufficient for v1?
3. Is a modal options dialog preferred, or inline spinbox/entry fields
   directly in the Run tab's button row? Draft leans modal to avoid
   cluttering the existing row, but it's a minor UX call.
4. Confirm the desired default `--report-dir` naming/location for
   GUI-launched suites (`result/gui-suite-{timestamp}/` proposed in §6) —
   should it instead reuse the CLI's bare `result/suite-{timestamp}/`
   pattern for consistency, distinguished only by who launched it?
