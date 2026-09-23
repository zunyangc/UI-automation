---
name: test-case-repair
description: Run a ui-auto CSV test case, repair each failing step with the smallest safe CSV change, rerun until it passes or is blocked, and write an HTML repair report matching result/repair-report-example.html.
---

# Test case repair

Repair one existing CSV test case automatically. Follow `AGENTS.md` for authoring rules and use `scripts/csvfmt/csv_loader.py` for strict validation.

## Input and output

- Required input: the CSV path.
- Default report path: `result\<csv-name>.html`.
- Create the `result\` directory when it does not exist.
- A user-provided report path overrides the default.
- If no CSV path is provided, ask for it instead of guessing.

## Workflow

1. Run the complete test and record its elapsed time:

```powershell
.\run.ps1 test_cases\<name>.csv -q
```

2. If it passes, create an empty repair report with final result `PASS`.
3. If it fails, record the attempt, failed step, failure, and exact fix.
4. Inspect the failed row, dependent rows, stderr, and relevant artifacts, and classify the failure using "Timing vs. real failure" below.
5. If timing, raise the smallest safe timeout/poll value using the reference table. Otherwise, if an existing script can handle the behavior, make the smallest deterministic CSV change supported by observed evidence.
6. If evidence proves that existing scripts cannot handle the observed behavior, automatically invoke `test-script-developer`. It adds the smallest backward-compatible script capability, its unit test, documentation, and the failed-row CSV change, then returns control to this repair run.
7. Do not weaken assertions, change tester intent, or guess unknown selectors.
8. Validate the edited CSV:

```powershell
uv run python scripts\csvfmt\csv_loader.py test_cases\<name>.csv
```

9. Rerun the complete test.
10. Repeat while failures provide evidence for a safe repair.
11. Stop with `BLOCKED` when progress requires unavailable software, credentials, permissions, user intent, a schema change, or a script behavior that cannot be verified.
12. Always write the HTML report.

## Timing vs. real failure

Raising a timeout only helps if the awaited thing was always going to appear, just late. Before changing a timeout, classify the failure:

**Likely timing** (raise the timeout/poll value):
- The step polls for a control/window (`find_control.py`, `find_window.py`, `wait_for.py`, `invoke_control.py`, `select_combo.py`) and the failure is a plain "not found within `<N>`ms" / poll-exhausted error, with no sign the target is structurally absent.
- The awaited condition depends on app/file/wizard-page rendering, a build/publish/pack/restore operation, NuGet metadata fetch, Live Unit Testing analysis, or a network-share file operation — all variable across machines, disks, and networks.
- The current `--timeout-ms` / `poll_total_ms` is below the matching floor in the reference table below, or is inconsistent with an already-tuned sibling step in the same or a similar CSV (e.g. one case already uses 120000ms for the same UI element that fails at 10000ms in another).

**Not timing** (fix the real cause or report `BLOCKED` instead):
- The target doesn't exist regardless of wait: a missing VS workload/template (e.g. `.NET MAUI App` absent because the MAUI workload isn't installed), a missing SDK/runtime (e.g. `dotnet new` rejects a target framework whose SDK isn't installed), or an edition-gated feature (e.g. Live Unit Testing requires Visual Studio Enterprise).
- A network share or path is unreachable from the very first check (e.g. the share Explorer window never opens).
- The step was intentionally safety-blocked (e.g. a broad process-kill step) — a design decision, not a timing bug.
- The selector is wrong (`--name`/`--auto-id`/control type) — confirm via screenshot that the control exists under a different name, then fix the selector instead.

If the evidence is ambiguous, prefer a modest increase over a large blind one, and say so in the repair report.

When applying a timing fix: raise `poll_total_ms` (or the script's `--timeout-ms`/`--poll-ms` args) rather than a fixed `wait_ms` — polling exits early once the condition is met, while a fixed wait always runs its full duration (AGENTS.md rule 11). Use a fixed `wait_ms` increase only when the step has no observable completion state to poll for. Change only the failing step, plus any verbatim copy of the same popup/menu/template pattern elsewhere in the file (the repo already does this for repeated loop/project iterations — see `prod-003-dotnet_core_cs.csv`'s repeated context-menu popup rows). Never randomize values or blanket-pad every step; only touch rows with real timing evidence.

### Reference table: step categories that commonly need more time on a different PC

These floors come from evidence already present in this repo (values other test cases were tuned to after live failures). Treat them as a starting floor, not a cap — raise higher if the observed near-miss duration plus a safety margin exceeds it.

| Category | Example steps | Timeout/poll floor |
|---|---|---|
| Cold-start "Create a new project" button / wizard "Create" button | Start Window → New Project dialog first render; final "Create" click before scaffolding begins | 20000 ms |
| Template search box / template tile / wizard Next / Back button | Typing into the template search, selecting a template tile, paging through the New Project wizard | 15000 ms |
| Top-menu "Build" / "Build Solution" / "Start Debugging" | Opening the Build or Debug menu and its dropdown items | 15000 ms |
| Right-click context-menu popup window (`--class Popup` / empty-title WPF popup) | Any `find_window.py` waiting for a just-opened context menu to render | 12000 ms |
| Menu item inside an open popup (`New Project...`, `Publish...`, `Pack`, `Properties`) | `find_control.py` locating a named `MenuItem` inside a captured popup hwnd | 15000 ms |
| Live Unit Testing UI (`Include all tests` link, `Configure Live Unit Testing` dialog, Start/Finish buttons, test group nodes) | LUT needs a background build/analysis pass before its UI renders | 60000 ms |
| Extract All / zip extraction command | Locating the "Extract all" command-bar button or the extraction dialog | 10000 ms |
| Publish/Pack completion banner, NuGet "Preview Changes" window, NuGet package-details "Update" button | First-time NuGet metadata fetch and Publish/Pack completion are disk- and network-dependent | 45000–60000 ms |
| Post-deploy welcome/banner text (e.g. MAUI "Welcome to" banner) | Verifying app UI text right after a debug deploy | 20000 ms |
| Network-share file lookup / paste-verify on Desktop | Locating a file on a mapped share or verifying a paste landed, before Visual Studio is even involved | 12000 ms |

When a step doesn't match any row above but still depends on an app launch, file open, project scaffold, build, or similar variable-duration action, use the closest analogous row's floor rather than guessing a new number.

Note: the `csv_loader.py <file>` CLI's `--strict` validation always requires a CONFIG `artifacts` row even though it is optional at runtime — a pre-existing, unrelated loader quirk that affects every CSV including `_template.csv`. Don't let it block a timing-only fix; validate with `csv_loader.load(path)` (non-strict) instead if it gets in the way.

## Report

Use `result/repair-report-example.html` as the format. Write standalone UTF-8 HTML and escape inserted text.

The report must show:

- A summary with the test case path, final result (`PASS` or `BLOCKED`), total run attempts, total run time, repairs applied, and final verification outcome.
- One repair-history row per failed attempt with the attempt number, failed step id and `Trigger`, observed failure, supporting evidence, exact CSV change, and the next run's outcome.
- The single blocking reason when the final result is `BLOCKED`.
- Links to relevant screenshots or other artifacts when they exist. Use relative paths so the report remains portable.

Do not add a repair-history row for the final successful run.

`Total run time` is the sum of the elapsed time of every `run.ps1` attempt. Do not include time spent inspecting failures or editing the CSV. Format it as `Hh Mm Ss`, omitting zero-value units.

## Final response

State the final result, repaired CSV path, and report path. If blocked, state the single blocking reason.
