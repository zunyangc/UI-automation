---
name: csv-test-formatter
description: Reformat a rough, hand-authored CSV test case into the standard ui-auto CSV layout that runs directly via run.ps1. Use when the user has a messy/freeform CSV (wrong column order, missing headers, prose notes, no section markers) describing a UI-automation scenario and wants it turned into a runnable standard-format CSV.
---

# CSV test formatter

Turn a user's **rough CSV** describing a Windows UI-automation scenario into a **standard-format CSV** that `run_test.py` loads directly (`scripts/csvfmt/csv_loader.py`). You only **reformat and rearrange** the user's intent into the canonical layout — you do not run tests. CSV runs straight from disk:

```powershell
.\run.ps1 test_cases\<name>.csv -q
```

> **Markdown style:** do not hard-wrap prose in this file. Write one paragraph (and one list item) per line and let the editor soft-wrap — manual mid-sentence line breaks create noisy diffs. See the "Documentation style" rules in [`AGENTS.md`](../../../AGENTS.md).

## Inputs and output

- **Input:** a CSV (or CSV-like text) the user wrote freeform — e.g. `test_cases/new_case.csv`. Columns may be in any order, headers may be missing, and cells may contain prose notes.
- **Output:** one standard-format `.csv` written to `test_cases/<name>.csv`. Mirror the canonical layout in `test_cases/_template.csv`. Output ONLY the file (no surrounding prose) unless the user asks for an explanation.

## The standard format (authoritative references)

- Template to copy: `test_cases/_template.csv`
- Full spec: `docs/csv-test-format.md`
- Column schema (do not deviate): `scripts/csvfmt/csv_schema.py`
- Available step scripts (one per step `type`): the files under `scripts/` (e.g. `scripts/input/key.py`, `scripts/input/type_text.py`, `scripts/input/click.py`, `scripts/window/find_window.py`, `scripts/uia/find_control.py`, `scripts/uia/read_console.py`, `scripts/files/screenshot.py`).

### File layout

Two marker-delimited sections, each with its own header row. Ragged rows are fine.

```
# CONFIG
Section,Key,Value
name,,<test_name>
description,,"<one-line description>"

# STEPS
No,Main step,Trigger,script,args,wait_ms,capture,expect_exit,expected_contains,poll_total_ms,poll_interval_ms,screenshot_pass,screenshot_fail,Expected
```

Do not add an `artifacts,screenshot_dir` row unless the test case needs a custom location — omitting it defaults to the standard `screenshots/{name}-{timestamp}` folder naming.

### `# STEPS` columns (exact order, from csv_schema.py)

| Column | Meaning |
|---|---|
| `No` | Phase number; repeat or leave blank to continue within a phase (readability only). |
| `step no` | Global sequential step counter (1, 2, 3… across every step row). |
| `Main step` | Phase name, on the first row of each phase only (readability only). |
| `Trigger` | Human-readable action → becomes the step `description`. |
| `script` | Path under `scripts/`; required for every real step. A blank-`script` row is skipped. |
| `args` | JSON list, e.g. `["enter"]` or `["{vars.x}", "{vars.y}"]`. |
| `wait_ms` | Literal milliseconds to wait after the step (e.g. `700`). |
| `capture` | JSON object mapping `vars.<name>` → a `$.cols[i]` / `$.rows[j].cols[i]` selector. |
| `expect_exit` | Non-zero to assert the script fails (e.g. `1` for "window gone"). |
| `expected_contains` | Presence makes the step an `assert_console_contains`. |
| `poll_total_ms` / `poll_interval_ms` | Literal milliseconds for the assert's polling. |
| `screenshot_pass` / `screenshot_fail` | JSON list of filename patterns; presence makes the step a `screenshot`. |
| `Expected` | Expected-result note (readability only). |

There are **no `id`, `type`, or `args_mode` columns** — the loader auto-generates the id (`step_1`, …) and infers the type (`screenshot_pass` set → `screenshot`; `expected_contains` set → `assert_console_contains`; otherwise the script basename).

## Reformatting rules

1. **Map the user's intent onto the standard columns.** Infer phases (`No` / `Main step`) and a plain-English `Trigger` for each step from the user's notes. Pick the correct `script` for each action from `scripts/`. Never invent step types or scripts that don't exist — if an action doesn't map to a known script, ask the user instead of guessing.
2. **JSON-encode complex cells.** `args` is a JSON list, `capture` is a JSON object, `screenshot_pass`/`screenshot_fail` are JSON lists. Let the CSV writer handle quoting/escaping (cells with commas/quotes get wrapped in double quotes, inner quotes doubled).
3. **Use literal milliseconds** for `wait_ms`, `poll_total_ms`, `poll_interval_ms` — never a timing-key name.
4. **Screenshots are their own rows.** Use the `{ss}` ordering placeholder in the filename (e.g. `{ss}.png` / `{ss}_FAIL.png`); the loader prepends `{artifacts.screenshot_dir}/`.
5. **Unroll loops with a known count.** There is no `foreach` in CSV — repeat the rows for each iteration. The `{ss}` counter then runs globally `ss_1..ss_N`.
5b. **Use a `# LOOP` block for unknown-count loops.** When repetition continues *until a condition clears* (e.g. "repeat on each vulnerable package until none remain"), don't guess a count — emit a `# LOOP` / `# END LOOP` block. The `# LOOP` row's `script`/`args` is the condition (loop runs while its exit code == `expect_exit`, default `0`); its `capture` re-reads the current target each pass; `max_iter` caps iterations. Rows up to `# END LOOP` are the body. See `docs/csv-test-format.md` ("Conditional loops").
6. **Do NOT randomize any values.** Reproducibility requires identical inputs every run — preserve the exact literals the user provides.
7. **Selectors:** prefer `auto_id` + `name` together in `find_control` args; always pass a captured window hwnd as the control's parent.
8. Keep only `name`, `description`, and `artifacts` in `# CONFIG` (the simplified CSV config). Do not add `inputs`, `timing`, or `expected_results` blocks.
9. **Minimize waits.** Prefer polling assertions (`expected_contains` with `poll_total_ms`/`poll_interval_ms`, or `wait_for`) over long fixed `wait_ms` when there's an observable state to wait on; when a fixed `wait_ms` is needed, use the smallest reliable value plus a small margin — don't pad delays. Keep values identical every run (no randomization).
10. **Keep paths machine-portable.** Never hardcode user/profile paths (e.g. `C:\Users\<you>`) — resolve home via `scripts/files/print_home.py` → `{vars.home}`, locate VS via `scripts/window/find_devenv.py` → `{vars.devenv}`, use `{timestamp}` for artifact dirs, match window titles by regex, and discover machine-varying values at runtime so the case runs on any PC/user.

## Workflow

1. Read the user's rough CSV.
2. Read `test_cases/_template.csv` and `scripts/csvfmt/csv_schema.py` for the exact layout.
3. Produce the standard CSV, writing it to `test_cases/<name>.csv`.
4. Validate by parsing it: `uv run python scripts\csvfmt\csv_loader.py test_cases\<name>.csv` (prints the parsed spec as JSON; fix any `ERROR:` before finishing).
