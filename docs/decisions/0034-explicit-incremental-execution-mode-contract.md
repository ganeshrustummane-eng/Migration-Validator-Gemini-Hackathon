# 0034. Explicit execution-mode contract: separate incremental capability from the current run's mode

**Status:** Accepted — implemented (see "Implementation notes" at the end)
**Date:** 2026-09-26

## Context

ADR 0030 introduced per-table incremental configuration
(`validation_plan.incremental.{enabled, filter_column}`) and chose to carry
the per-run date range by writing `from_date`/`to_date` into the same YAML
just before a run, mirroring the existing `mismatch_threshold_pct` pattern.
ADRs 0031–0033 implemented it, fixed the count/data overlap problem in
Streamlit, and blocked `hybrid_v1` + incremental.

The final audit of 0030–0033 found:
- **D1 (HIGH):** Historical runs are broken for any table with incremental configured.
- **D2 (LOW):** `runner.py`'s summary discovery is broader than intended.

Repository facts this decision relies on (verified, not assumed):

- `Project/main.py:343`:
  `_incremental_enabled = bool(_incremental_block.get("enabled"))`. This
  flag is read only from the YAML. `main.py` has no other input telling it
  which mode the operator chose.
- `Project/main.py:382`: when that flag is set, it calls
  `apply_incremental_predicate()` with the `from_date`/`to_date` found in
  the YAML.
- `Project/utils/incremental_filter.py:28`: raises if either date is missing.
- `webapp/app.py:3841`: only writes dates into the YAML in Incremental mode.
  Historical mode never touches the YAML.
- `Project/runner.py:101`: `subprocess.Popen(args, cwd=..., stdout=..., stderr=..., text=True)`
  passes no `env=`, so the child process **inherits the parent's
  environment**.
- `Project/main.py:142`: `main.py` already reads a runtime setting from an
  environment variable (`VALIDATOR_MAX_TABLE_WORKERS`), not from argparse.
- Callers of `start_validation`/`run_validation`: the Run Validation tab
  (`webapp/app.py:3859`) and the scheduler (`webapp/app.py:4459`, which
  calls `run_validation(..., ["all"], True, True)` and always runs
  historically). There are no others.
- `src/validation/config_schema.py`: `ValidationPlanBlock` uses
  `extra="allow"`, so `incremental.*` keys are passed through untyped.
- No YAML in `Project/config/` has an `incremental:` block today, so D1
  has no live impact yet.

## Problem

`incremental.enabled` is doing two jobs: "this table can run incrementally"
(static configuration) and "run it incrementally now" (the current run's
mode). Because the per-run dates are stored in the same file, a Historical
run of a table that can run incrementally either:
- fails because no dates were ever written, or
- silently reuses the dates left by an earlier Incremental run and
  validates only that range.

A direct CLI run of `main.py` has the same problem. The second outcome
breaks ADR 0030's rule that a Historical run must never quietly change what
it validates.

## Decision

1. **`incremental.enabled` + `filter_column` describe what the table can
   do, and nothing more.** They never, by themselves, cause a run to filter.
2. **The run's mode and date range are passed to the validation process
   (the `main.py` subprocess) through its environment, not through the
   YAML.** Two environment variables, set only on the `main.py` subprocess
   `runner.py` launches:
   - `VALIDATOR_INCREMENTAL_FROM_DATE`
   - `VALIDATOR_INCREMENTAL_TO_DATE`

   If both are set, the run is Incremental. If neither is set, it is
   Historical. If only one is set, the run stops immediately, before any
   table executes.
3. **Dates are never written into YAML again.** The Streamlit injection
   block at `webapp/app.py:3841` is removed. Any leftover
   `validation_plan.incremental.from_date`/`to_date` keys in a YAML are
   ignored by `main.py`.
4. **`runner.start_validation()` gains one optional keyword argument,**
   `incremental_range: tuple[str, str] | None = None`. When it is `None`,
   the function **removes** both variables from the environment it hands
   to the subprocess, so a variable accidentally present in the Streamlit
   process can't leak into a Historical run.

**Why environment variables.** They are the smallest explicit contract the
existing architecture already supports:
- The subprocess boundary already exists.
- `main.py` already takes runtime settings this way (`VALIDATOR_MAX_TABLE_WORKERS`).
- No CLI argument is added.
- The value exists only for that one subprocess, so nothing persists into
  config or affects later runs.
- It also removes the concurrent-write race on the YAML that ADR 0030's
  Risks section noted for the date injection.

**Alternatives considered:**
- **Keep dates in YAML, and have Streamlit strip them in Historical mode.**
  Rejected: the dates still persist between runs, so a CLI run or a
  concurrent run still sees stale values, and `main.py` would still be
  guessing the mode from file contents.
- **Add CLI flags on `main.py`.** Rejected: forbidden by this ADR's
  constraints and by ADR 0030 Decision 4.
- **A run-request sidecar file whose path is passed in an env var.**
  Rejected for now: it does the same job with more moving parts (writing,
  cleaning up and naming a file). Revisit if the run ever needs more than
  two scalar values.
- **Add a runtime `active: true/false` key to the YAML.** Rejected: it
  still writes run state into config, which has the same persistence
  problem as the dates.

## Proposed execution/configuration contract

| Concept | Where it lives | Lifetime |
|---|---|---|
| Table *can* run incrementally | `validation_plan.incremental.enabled: true` (YAML) | Permanent config |
| Which column to filter on | `validation_plan.incremental.filter_column` (YAML) | Permanent config |
| This run is Incremental, and its range | `VALIDATOR_INCREMENTAL_FROM_DATE` / `_TO_DATE` (subprocess environment) | One `main.py` process |
| Operator's mode choice | Streamlit `exec_mode` radio | UI session only |

When `main.py` starts, next to `MAX_TABLE_WORKERS`, it reads both variables:
- neither set → `run_incremental = False`
- both set → `run_incremental = True`, and `from_date <= to_date` is
  checked before any table runs
- only one set → log an error and exit non-zero

Each block then decides whether to filter:

`apply_incremental = run_incremental and table_plan.incremental.enabled and table_plan.incremental.filter_column`

`apply_incremental_predicate()` stays exactly as it is. It receives the
dates from the environment instead of from the YAML.

## Historical execution behavior

Applies when neither variable is set. This is the default for every caller
that doesn't opt in: the scheduler, a direct CLI run, and Streamlit in
Historical mode.

- No block is ever filtered, whatever the YAML declares. Stale YAML date
  keys are ignored.
- A table that can run incrementally runs as an ordinary full historical
  validation. That includes a `hybrid_v1` table that can also run
  incrementally (see below).
- Nothing else changes: semantic normalization, quality checks, row
  comparison, count thresholds, environment resolution and hybrid
  execution all work as they do today.

## Incremental execution behavior

Applies when both variables are set. Only Streamlit sets them, when the
operator picks Incremental with a valid range.

- **`data_validation` block, table can run incrementally** (`enabled` +
  `filter_column`): the predicate is added to both `source_query` and
  `target_query` before they run. This is the existing code at
  `main.py:382`, now fed dates from the environment.
- **`data_validation` block, table can't run incrementally:** the block
  fails with a clear error and is recorded as an ERROR row. It never
  silently runs historically. This makes `main.py` itself enforce "no
  silent fallback", so the Streamlit overlap check (ADR 0032) becomes an
  earlier, clearer warning rather than the only protection. The Streamlit
  check stays.
- **`count_validation` blocks:** run unfiltered, as today. Count YAMLs are
  per-source files with no per-table `validation_plan`, so there is no
  place to configure a filter column for them. The Streamlit tab should
  say that counts stay full-table in Incremental mode. See Open questions.
- **`integrity_check` blocks:** unaffected. They run their own query
  through their own path.
- **Other sibling blocks with real queries** (`transformation_validation`,
  `aggregate_validation`): filtered only if their table can run
  incrementally. Missing configuration is not an error for them. This is
  the current behavior, and none exist today.
- **Streamlit:**
  - still reads `enabled`/`filter_column` from each YAML to show the column
    and to exclude tables that aren't configured (unchanged);
  - stops writing to the YAML;
  - calls `start_validation(..., incremental_range=(str(from), str(to)))`
    in Incremental mode and without the argument in Historical mode.

## Interaction with hybrid_v1 / ADR 0033

ADR 0033's guard currently receives `_incremental_enabled`, which is the
static capability flag. Under this contract, that input would itself be a
D1-class bug: a `hybrid_v1` table that can also run incrementally could
never run a *Historical* hybrid validation.

The guard's input therefore changes to `apply_incremental` for this
particular run:

`check_hybrid_incremental_conflict(use_hybrid, apply_incremental)`

- Incremental run + hybrid table that can run incrementally → blocked,
  exactly as ADR 0033 requires.
- Historical run + the same table → runs the hybrid path normally.

The function and its message are unchanged. Only its argument changes, and
its docstring should say the flag means "incremental predicate would be
applied this run". `tiered_runner.py` is not touched. The combination stays
unsupported until someone decides to pass the predicate through Tier 1 and
Tier 2 (ADR 0032, option b).

## Backward compatibility

- **YAMLs without `incremental:`:** identical behavior in both modes.
- **Scheduler (`run_validation`) and direct CLI runs:** always historical,
  because they never set the variables. This also fixes D1 for the CLI,
  which previously would pick up stale dates from the YAML.
- **`start_validation`:** the new argument is optional with default `None`,
  so the existing positional call from the scheduler path is unaffected.
- **CLI arguments and YAML schema:** unchanged. No schema field is added;
  existing date keys are simply ignored.
- **Environment variables:** a shell user could set them by hand and run
  `main.py` incrementally. That is accepted as an internal transport, not a
  supported interface, on the same footing as `VALIDATOR_MAX_TABLE_WORKERS`.
  It doesn't change ADR 0030's Streamlit-only scope.

## D2 decision

**Narrow it now**, in the same change, because `runner.py` is being edited
anyway and the fix is local.

Replace the broad `run_dir.glob("**/*_summary.csv")` loop with an explicit
lookup of `run_dir.glob("*/integrity_check_summary.csv")`. That covers both
the count and data output folders. If both folders contain the file, their
rows are combined into one `integrity_check` summary.

Summaries from other sibling blocks go back to not being collected, which
matches the behavior before ADR 0031. Whether they *should* appear in the
UI or History is a separate decision and isn't made here.

## Consequences

- Choosing Historical or Incremental in Streamlit now controls what the
  engine does. Leftover YAML state can no longer change a run's scope.
- The mode is decided once, when the subprocess starts, instead of being
  inferred per table from file contents.
- Runs no longer write dates into the YAML, so the date-injection race is
  gone. The `mismatch_threshold_pct` injection still has its own race and
  is out of scope here.
- A small precedent is set: per-run settings travel through the
  subprocess environment. Any future per-run setting should follow the
  same pattern rather than being written into YAML.

## Deferred items

- Scoping count validation in Incremental mode (see Open questions).
- Passing the predicate through hybrid Tier 1/Tier 2 (ADR 0032, option b).
- D3: the error reason isn't shown on ERROR summary rows.
- D4: correcting ADR 0030's statement about the Validation type filter,
  and showing integrity-check violation CSVs in the UI.
- Datetime precision for the date range (ADR 0031, Q3).
- Removing leftover `from_date`/`to_date` keys from existing YAMLs.
  Unnecessary, since none exist today and `main.py` will ignore them.

## Implementation scope for the next step

1. **`Project/runner.py`:**
   - add `incremental_range=None` to `start_validation()`;
   - build the child environment from `os.environ` and either set both
     variables or remove both;
   - pass `env=` to `Popen`;
   - narrow the summary discovery (D2).
2. **`Project/main.py`:**
   - read and validate the two variables at startup, next to
     `MAX_TABLE_WORKERS`;
   - compute `apply_incremental` per block;
   - error for a `data_validation` block that isn't configured during an
     Incremental run;
   - take the dates from the environment, not from `_incremental_block`;
   - pass `apply_incremental` to `check_hybrid_incremental_conflict`.
3. **`Project/utils/utility.py`:** docstring change only, for
   `check_hybrid_incremental_conflict`.
4. **`webapp/app.py`:**
   - remove the date-injection block (around line 3841);
   - pass `incremental_range` to `start_validation` in Incremental mode;
   - add a caption that counts stay full-table in Incremental mode.

   The filter-column display, the exclusion of unconfigured tables and the
   count/data overlap block stay as they are.
5. **Not touched:**
   - `Project/utils/incremental_filter.py`
   - `Project/tiered_runner.py`
   - `src/validation/config_schema.py`
   - `semantic_normalize.py`, `quality_checks.py`, `row_compare.py`
   - `Project/db/`
   - argparse
6. **Tests to add in that step:**
   - environment present/absent/partial handling;
   - a Historical run ignores leftover YAML dates;
   - Historical + `hybrid_v1` + incremental capability reaches the hybrid
     path;
   - Incremental + `hybrid_v1` is blocked;
   - an Incremental run with an unconfigured `data_validation` block
     produces an ERROR;
   - `start_validation(incremental_range=None)` removes inherited variables;
   - D2 discovery picks up only `integrity_check_summary.csv`.

## Open questions

1. **Should count validation be scoped in Incremental mode?** Leaving it
   full-table is safe and consistent with today. But the operator then sees
   an incremental data result next to a full-table count, and the source
   and target counts may legitimately differ from what the data comparison
   covered. Scoping counts needs somewhere to put a filter column for each
   table in the per-source count YAMLs, which is a config-location decision
   the repository can't answer by itself. For this ADR the counts stay
   full-table and the UI says so.

## Implementation notes (2026-09-26)

Implemented as scoped above. Where the code differs from, or adds to, the
text of this ADR:

- **Where the operator enters dates:** only the Streamlit Run Validation
  tab's From/To date pickers, which are chosen again for every run. The two
  environment variables are the internal hand-off from `runner.py` to the
  `main.py` subprocess. They are **not** meant to be set in `.env` /
  `.env.*`, and nothing documents them there. At the user's direction, the
  UI is the only supported way to enter the date range.
- **Helpers instead of inline logic.** The startup parsing and the
  per-block decision live in `Project/utils/utility.py`, next to
  `check_hybrid_incremental_conflict`, so they can be tested without
  importing `main.py` (which has module-level side effects):
  - `read_incremental_range(env)` returns `None` (Historical) or
    `(from, to)`. It raises `ValueError` when only one variable is set or
    when from > to.
  - `incremental_filter_column(validation_name, plan_block, run_incremental)`
    returns the filter column, or `None`. In an Incremental run it raises
    for an unconfigured `data_validation` block.
  - `main.py` computes `apply_incremental = bool(filter_column)` from that
    result.
- **Strict date parsing (addition).** `read_incremental_range` parses both
  values with `date.fromisoformat`. Only a plain `YYYY-MM-DD` can reach
  `apply_incremental_predicate()`'s SQL string, since the environment is a
  hand-settable input and the predicate is built with an f-string.
- **Startup failure exit code:** an invalid range logs an error and exits
  with code `2` before the validation loop starts. The run's log directory
  has already been created by then, so the error lands in that run's log.
- **Parameter name of the hybrid guard kept.** It is still called
  `incremental_enabled`, so existing callers and tests don't change. Only
  its docstring changed, to say it means "predicate would be applied this
  run".
- **Hybrid guard is table-scoped (follow-up fix, 2026-09-27).** The
  conflict guard receives the table's hybrid status, determined from
  `data_validation` (`should_dispatch_hybrid("data_validation", ...)`),
  while `use_hybrid` stays block-scoped for dispatch. Previously
  `row_hash_validation` of an incremental-capable `hybrid_v1` table still
  ran with the predicate while `data_validation` was ERROR (a partial run).
- **D2:** `collect_validation_result` reads `*/integrity_check_summary.csv`
  and concatenates the matches into one `integrity_check` summary.
- **UI caption:** "The date range applies to data validation only. Count
  validation still runs full-table in Incremental mode."
- **Tests:** `Project/test_incremental_mode.py` has 15 tests covering
  Implementation scope item 6, plus two extras:
  - an unconfigured sibling block runs unfiltered in an Incremental run;
  - a non-ISO date is rejected.

  The "fails before execution" cases are tested on the helper, not by
  launching `main.py`, which would create real output directories.
  Full suite: 148 passed.
- **Files touched:**
  - `Project/runner.py`
  - `Project/main.py`
  - `Project/utils/utility.py`
  - `webapp/app.py`
  - `Project/test_incremental_mode.py` (new)

  `tiered_runner.py`, `incremental_filter.py`, `config_schema.py`, the
  connectors and argparse were not modified.
