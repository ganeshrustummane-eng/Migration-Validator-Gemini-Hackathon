# 0030. Progressive decision: extending the live validation engine with report-pack, incremental, and sanity execution (Streamlit-only)

**Status:** Proposed
**Date:** 2026-09-26

This supersedes the capability-level reasoning in [0029](0029-review-report-pack-sanity-incremental-execution-support.md)
with implementation-ready detail, verified directly against the current
repository (not the earlier prompt's assumptions — two material discrepancies
were found and are called out explicitly below). No code is changed by this
document.

## Decision Owners / Context

Requested by the repo owner, reviewing ~3 months of a colleague's parallel
`Project/main.py`/`Project/utils/utility.py` fork (pasted in full, not
present in this checkout) for capabilities worth porting into the live
engine. Scope constraint from this task: the ported capabilities are
**Streamlit-UI-only** — no new CLI flags/commands for report-pack execution,
sanity checks, or incremental execution unless the existing architecture
already requires the change as shared internal plumbing for both interfaces.

## Problem Statement

Three capabilities exist in the colleague's fork with no live equivalent:
report-pack-scoped execution, single-source sanity/orphan-key checks, and
historical-vs-incremental date-range execution. Their implementation is a
structural fork (own comparison, own environment handling, own config
loader) that would regress the live engine's semantic normalization,
quality checks, and hybrid Tier-1/Tier-2 path if merged as-is. The task is
to decide exactly how to port only the missing *capabilities* into the live
architecture, Streamlit-only, dynamically (no hardcoded columns/lists),
without duplicating or replacing anything that already works.

## Repository Findings

Verified by reading the actual files, not inferred from the prompt:

- **`Project/main.py`** (588 lines): single-threaded-per-table-pool executor
  (`ThreadPoolExecutor`, `MAX_TABLE_WORKERS`), reads `--layer_type` (`bronze`,
  `silver`, `gold`, `reporting` — not `bronze_postgres`/`bronze_mssql`/
  `reports`/`sanity` as the fork has), `--environment` (`dev`, `uat`, `prod`,
  `local` — not the fork's `dev`/`stg`/`qat`/`prod`). Every `sourcequery`/
  `targetquery` it executes is a **complete, pre-rendered SELECT string read
  verbatim from YAML** — main.py never formats, templates, or string-edits
  SQL. Environment/schema resolution is delegated entirely to
  `get_database(source, BASE_DIR, environment, override_database, override_schema)`;
  `override_database`/`override_schema` come from YAML fields
  (`source_database`, `source_schema`, `target_database`, `target_schema`)
  written at generation time. There is no environment-name-to-schema string
  substitution anywhere in `main.py` — confirming ADR 0029's recommendation
  was correctly grounded.
- **`Project/db/factory.py`**: `_ENV_FILE_BY_ENVIRONMENT` maps `local/dev/uat/prod`
  → a `.env`/`.env.<env>` file; `get_database()` resolves `SNOWFLAKE_DATABASE`/
  `SNOWFLAKE_SCHEMA` (or the YAML overrides) from that file per connector.
  This is the one, single environment-resolution mechanism; nothing in this
  decision introduces a second one.
- **`Project/utils/quality_checks.py`**: two frame-to-frame functions,
  `run_quality_checks` (null-rate/distinct-count/aggregate/sample-hash
  tolerances) and `validate_expected_grain` (duplicate-key detection), plus
  `append_validation_audit` (JSONL audit log, one per run, lock-guarded for
  the thread pool). **No single-source-only (orphan/integrity) check exists
  today** — every function takes both `source_df` and `target_df`.
- **`Project/utils/row_compare.py`**: `compare_indexed_frames` — the one PK-indexed
  comparison algorithm, shared verbatim by `main.py` and
  `Project/tiered_runner.py`'s hybrid path. Not a candidate for change here.
- **`src/core/validation_plan.py`**: `CanonicalValidationPlan` dataclass —
  already carries `source_filter`/`target_filter` (WHERE predicates "applied
  during SQL generation", i.e. **baked into the rendered SQL once, at
  generation time** — not re-evaluated per run), `row_hash` (`RowHashSpec`),
  and `execution_strategy` (`"standard"` / `"hybrid_v1"`). These serialize
  into the YAML's sibling `validation_plan:` block (see below) — this is the
  established extension point for plan-level metadata that isn't itself a
  query.
- **`src/validation/config_schema.py`**: `_QueryBlock` (base of
  `CountValidationBlock`/`DataValidationBlock`) is a Pydantic model with
  `model_config = ConfigDict(extra="forbid")` — **any new top-level key added
  to a `data_validation`/`count_validation` block must be explicitly typed
  here or the YAML fails schema validation at load time.** `ValidationPlanBlock`
  (the `validation_plan:` sibling), by contrast, is
  `ConfigDict(extra="allow")` — it already passes through untyped keys
  (`row_hash`, `relationships`, `identity`, etc. all live there without each
  needing a typed field). This is a real, load-bearing distinction for where
  new per-table metadata should live (see Decision 1).
- **An actual generated YAML** (`Project/config/bronze/data_validation/postgres/customers.yaml`):
  confirms `sourcequery`/`targetquery` end in a bare `FROM <table>;` — no
  `WHERE` clause present in the common case. Appending a filter predicate
  therefore requires stripping the trailing `;`, checking for an existing
  `WHERE` (e.g. a baked-in Fivetran-active filter would already be one), and
  appending `WHERE`/`AND` accordingly — never blind string concatenation.
- **`webapp/app.py`'s Run Validation tab** (`tab_execute`, ~line 3484 on):
  already discovers **every** YAML generically by globbing
  `config/<layer>/{count_validation,data_validation}/**/*.yaml` **and**
  `config/report/**/*.yaml`, tagging report-pack rows with
  `folder: f"report/{subfolder}"` (line ~3546). These rows already appear,
  selectable and runnable, in the same file-picker grid as bronze/silver/gold
  rows, filterable today via the existing "Filter by folder" and "Validation
  type" multiselects (lines 3561–3572). **Report-pack execution already
  works end-to-end today** — this is Discrepancy #1 below.
- **Runtime YAML mutation precedent**: the same tab already writes a
  runtime-selected value into the on-disk YAML immediately before calling
  `start_validation()` — `mismatch_threshold_pct` for data validation
  (lines ~3694–3710) and `count_mismatch_threshold_pct` for count validation
  (lines ~3712–3728), both via `yaml.safe_load` → mutate → `yaml.dump` back
  to the same file, wrapped in `try/except: pass`. This is the exact,
  already-accepted pattern for "a value the user picks at run time, injected
  into the YAML the engine reads" — and it requires **zero new CLI flags** on
  `Project/main.py`, because the value travels through the YAML, not through
  `argparse`.
- **`Project/runner.py`**: `start_validation(layer, environment, tables, do_count, do_data)` /
  `run_validation(...)` — both take exactly the same five arguments `main.py`'s
  argparse exposes. No `report_pack`, `run_type`, `from_date`, `to_date`
  parameter exists here today.

## Existing Architecture (execution flow, confirmed by tracing, not assumed)

```
Streamlit (webapp/app.py tab_execute)
    → YAML inventory scan (config/<layer>/**, config/report/**)
    → (optional) runtime YAML mutation: threshold_pct written into the picked YAML
    → Project/runner.py::start_validation(layer, environment, tables, do_count, do_data)
    → subprocess: python Project/main.py --layer_type ... --tables ... --environment ...
    → Project/main.py loads YAML, per table/validation block:
        └── Project/db/factory.py::get_database(...)  [env + YAML overrides → connector]
        └── source_df = obj.execute_query(sourcequery)   ← already-complete SQL string
        └── target_df = obj.execute_query(targetquery)
        └── canonicalize_frames()                        [Project/utils/semantic_normalize.py]
        └── run_quality_checks() / validate_expected_grain()  [Project/utils/quality_checks.py]
        └── compare_indexed_frames()                      [Project/utils/row_compare.py]
          (or, if validation_plan.execution_strategy == "hybrid_v1":
           Project/tiered_runner.py::run_table_hybrid(...))
        └── create_summary()/append_validation_audit()     [Project/utils/utility.py, quality_checks.py]
    → CSV outputs + summary CSV + validation_audit.jsonl
    → Streamlit reads results back (collect_validation_result)
```

Nothing in this decision changes any step above except: (a) main.py gains a
predicate-injection call sourced from YAML fields before executing
`sourcequery`/`targetquery` when `validation_plan.incremental` is present,
and (b) main.py gains a branch for a new source-only block type
(`integrity_check`) that skips the source/target comparison path entirely
and calls a new `quality_checks.py` function instead.

## Colleague Implementation Findings (recap, cross-checked against the above)

Confirmed genuinely new (no live equivalent): report-pack CLI arg + output
paths (but see Discrepancy #1), `sanity` layer (single-source integrity
query, FAIL if any rows returned, `test_case`/`summary` metadata — note
`DataValidationBlock` in `config_schema.py` **already has** `test_case`,
`summary`, `report_tile` as optional fields, unused by any block that has
no target), and `--run_type historical/incremental` with
`.format()`-injected `WHERE created_date BETWEEN ...`.
Confirmed NOT worth porting: `source_df.astype(str).equals(target_df)`
comparison (no semantic normalization, no threshold), inline
`query.format(env="DEV")`/`schema_env="STAGING"` environment handling
(duplicates what `factory.py` already does dynamically), hardcoded
`--report_pack` argparse choices list (see Discrepancy #2), hardcoded
`created_date` incremental column.

## Discrepancies between this task's assumptions and the actual repository

**Discrepancy #1 — report-pack execution already exists.** The task
prompt assumes report-pack execution needs to be built from scratch
("Determine how the generated report-pack configuration currently maps to
execution... Design the equivalent using the existing live architecture").
It already does, generically: any YAML under `config/report/<pack>/...` is
already discovered, listed, selectable, and runnable through the existing
Run Validation tab's inventory scan — no `report_pack` concept needs to
reach `Project/main.py`/`runner.py` at all. **Impact:** the real remaining
gap is UX ergonomics (a dedicated "Report Pack" picker instead of the
generic folder multiselect, and pack-level result rollup), not a new
execution path. Decision 3 and the Streamlit UX section below are scoped
down accordingly — this removes an entire "new engine" concern from scope.

**Discrepancy #2 — there is no report-pack "source of truth" to
consolidate.** The task prompt (and ADR 0029, written before this deeper
check) assumed `src/validate_cli.py` has a canonical hardcoded list of pack
names (`emanagement`, `smanagement`, ...) that a Streamlit picker and a
generation-side list could drift apart from. A repo-wide grep for those pack
names found exactly one hit: a placeholder string `"e.g. emanagement"` in a
text-input hint (`webapp/app.py:3459`). **Report packs are free-text
directory names** (`config/report/<whatever-the-user-typed>/...`), not an
enum, anywhere in the live system. **Impact:** Decision 3 changes from "pick
one canonical list" to "never hardcode a list — discover pack names from
`config/report/`'s actual subdirectories, the same way the Run Validation
tab's inventory scan already does." The colleague's hardcoded argparse
`choices` list is a regression relative to the live system's existing
flexibility, not a gap to fill.

## Capability Gap Analysis

### Report-Pack Execution
Gap is smaller than assumed (Discrepancy #1). What's missing: (a) a
Streamlit-side "Report Pack" selector that discovers pack names dynamically
from `config/report/`'s subdirectories and pre-filters the existing
inventory grid to that pack, instead of making the user manually work the
generic folder multiselect; (b) a pack-level result summary (aggregate
pass/fail across the pack's tables) using the same `collect_validation_result`
data already returned per run — purely a Streamlit presentation addition,
no engine change.

### Sanity / Integrity Checks
Real gap. `quality_checks.py` has no single-source check function. Needs:
(1) a new function there, `run_integrity_check(source_df, config) -> list[dict]`
(same failure-list shape as `run_quality_checks`, reusing `_as_float`-style
helpers where relevant); (2) a new Pydantic block type in `config_schema.py`
(`IntegrityCheckBlock`, source-only — `source_table_name`, `source`,
`sourcequery`, plus the already-present-but-currently-unused `test_case`/
`summary` fields from `DataValidationBlock`, minus every target-side
requirement); (3) a small branch in `main.py`'s per-table loop: a block with
no `target` field routes to the new integrity path (`obj.execute_query()` →
row count → PASS if 0 rows, FAIL otherwise → `create_summary()`/
`append_validation_audit()`), never the source/target comparison path.

### Incremental Execution
Real gap, and the one with the most design surface. See Decision 1.

## Capabilities Explicitly NOT Adopted

### Comparison Logic
`source_df.astype(str).equals(target_df)` is not adopted. `canonicalize_frames()`
(semantic normalization), `run_quality_checks`/`validate_expected_grain`
(configurable tolerances), and `compare_indexed_frames` (PK-indexed
multiset comparison, shared with the hybrid path) remain the only row/value
comparison logic. All three new capabilities feed into this path unchanged
— incremental only narrows the row set via SQL `WHERE`; sanity uses a
different, source-only path by design (there is no target to compare); 
report-pack changes nothing about comparison at all.

### Environment Handling
`query.format(env=..., schema_env=...)` is not adopted in any form. Every
new capability resolves environment/schema exclusively through
`Project/db/factory.py::get_database()` and its existing `.env.<environment>`
+ YAML-override mechanism. No environment name is ever substituted into SQL
text.

### Duplicate Utility Implementations
The colleague's own `generate_runid`, `get_config_output_paths`,
`create_summary` are not adopted — the live `Project/utils/utility.py`
versions (which already have thread-safety locking, the row-hash fallback
heuristic, and the hybrid-dispatch guard that the fork's versions lack) are
extended in place, not replaced or shadowed.

### Hardcoded Configuration
Not adopted: hardcoded `created_date` (Decision 1 makes this per-table and
config-driven), hardcoded report-pack `choices` list (Discrepancy #2 — packs
are discovered from the filesystem, never enumerated in code).

### Other Fork-Specific Behavior
Not adopted: the fork's own `--run_type historical/incremental` CLI surface,
its own environment `choices` list (`dev/stg/qat/prod`), its own
`--layer_type` choices (`bronze_postgres/bronze_mssql/reports/sanity`) — the
live `--layer_type`/`--environment` choices are unchanged by this decision.

## Architectural Decisions

### Decision 1 — Incremental Filtering

**Configuration home:** per-table, inside the existing `validation_plan:`
sibling block (`ConfigDict(extra="allow")` — confirmed above), as:

```yaml
validation_plan:
  incremental:
    enabled: true
    filter_column: updated_at
```

This is **Option B** from the task's own framing (structured, not a bare
`incremental_filter_column: updated_at` top-level key), chosen over:
- *Option A (flat per-table key)* — would require adding the key to both
  `CountValidationBlock`/`DataValidationBlock` in `config_schema.py`
  (`extra="forbid"` blocks anything untyped), for no benefit over B.
- *Option C (global convention at generation time)* — rejected outright per
  the task's own stated expectation and this analysis agrees: the temporal
  column is a property of the table, not the environment or a global
  default: `customers.updated_at` and `orders.created_at` legitimately
  differ, and a global convention silently breaks the first table that
  doesn't match it.

Landing it under `validation_plan` (not a new top-level `data_validation`
key) means **zero `config_schema.py` migration** for the static
`enabled`/`filter_column` declaration — it already round-trips through
`ValidationPlanBlock`'s passthrough today.

**Where the actual date range comes from:** `from_date`/`to_date` are
**runtime** values (they change every run) and must NOT be baked into the
YAML at generation time the way `source_filter`/`target_filter` are. They
follow the exact precedent already in `webapp/app.py` for
`mismatch_threshold_pct`: written into the picked table's YAML (under
`validation_plan.incremental.from_date`/`to_date`) immediately before
`start_validation()` is called, read back out by `main.py` when it loads
that YAML for this run. No new argparse flag on `main.py`, no new parameter
on `runner.py::start_validation()` — the YAML is the transport, exactly as
established. (Risk: this mutates a shared on-disk file per run, same
accepted risk as the existing threshold injection — see Risks.)

**SQL construction — never string replacement.** A new shared helper
(`Project/utils/incremental_filter.py`, one function,
`apply_incremental_predicate(query: str, filter_column: str, from_date: str, to_date: str) -> str`)
does the following, and is the *only* place this logic exists:
1. Strip the trailing `;` (and trailing whitespace) from the query.
2. Regex-detect whether the query (outside of subqueries/CTEs — a simple
   top-level `\bWHERE\b` check is sufficient given this codebase's generated
   SQL is a single flat `SELECT ... FROM ... [WHERE ...]`) already has a
   `WHERE` clause (e.g. a baked-in Fivetran-active filter).
3. Append `AND <filter_column> BETWEEN '<from_date>' AND '<to_date>'` if a
   `WHERE` exists, else `WHERE <filter_column> BETWEEN '<from_date>' AND '<to_date>'`.
4. Re-append `;`.
`Project/main.py` calls this once for `source_query` and once for
`target_query`, only when `validation_plan.incremental.enabled` is true for
that block, right before `obj.execute_query(...)` — this is also what
satisfies the "push the filter into the database, don't fetch-then-filter
in Python" requirement (Scalability section).

**Required error behavior (fail fast, no silent fallback):**
- Incremental requested (`enabled: true`) but `filter_column` missing/blank
  → raise before any query executes (a `ValueError`/config error surfaced
  through the same `_write_error_summary`/ERROR-status path `main.py`
  already uses for other pre-execution failures) — never silently run
  historical.
- `from_date`/`to_date` missing when incremental is requested → same
  fail-fast treatment, surfaced in Streamlit as a disabled "Run" state
  (caught before the subprocess is even started — see UX below), not as a
  mid-run crash.
- `from_date > to_date` → validated in Streamlit before the run starts
  (`st.error`, run button disabled), *and* defensively checked in
  `apply_incremental_predicate()` so a future non-UI caller can't skip it.

### Decision 2 — Sanity / Quality Checks

**Chosen: Option B/C hybrid** — extend the existing quality-check framework
rather than create a fourth independent execution engine (`--layer_type
sanity` with its own code path, Option A) or a fully generic "everything is
a quality check category" abstraction speculatively built out now
(over-engineering the task doesn't ask for). Concretely:
- `run_integrity_check(source_df, config) -> list[dict]` added to
  `Project/utils/quality_checks.py`, next to `run_quality_checks`/
  `validate_expected_grain`, reusing the same failure-dict shape so
  downstream reporting code doesn't need a third format.
- `IntegrityCheckBlock` added to `config_schema.py` as a new optional field
  on `TableValidations` (source-only: `source_table_name`, `source`,
  `sourcequery`, `test_case`, `summary` — the last two already exist as
  optional fields on `DataValidationBlock` today, unused by anything; this
  reuses that precedent instead of inventing new field names).
- `Project/main.py`'s per-table loop gets one new branch: a validation block
  with no `target` key routes to `run_integrity_check()` +
  `create_summary()`/`append_validation_audit()` (same functions, same
  output directory conventions, same run_id) instead of the source/target
  fetch-compare path. This is a conditional inside the existing loop, not a
  parallel loop or a new file.

**Why not Option A (independent `sanity` layer/engine):** would duplicate
execution-loop, audit-write, and summary-write logic that already exists
and is thread-safety-hardened (`_AUDIT_WRITE_LOCK`, `_SUMMARY_WRITE_LOCK`).
**Why not full Option C (generic quality-check category framework):** no
current requirement needs more than one new check type; a
category/plugin abstraction for a single check type is exactly the kind of
speculative generality this decision should avoid.

### Decision 3 — Report-Pack Source of Truth

Per Discrepancy #2: there is no list to consolidate — packs are directory
names under `config/report/`. The decision is to **never introduce a
hardcoded list anywhere** (Streamlit, `main.py`, or `validate_cli.py`).
Streamlit's report-pack picker (new, additive) discovers pack names via
`sorted(p.name for p in (PROJECT_DIR/"config"/"report").iterdir() if p.is_dir())`
— the same directory `webapp/app.py`'s existing inventory scan already
walks (`_rep_root` at line ~3539). Selecting a pack pre-filters the
*existing* folder multiselect to `report/<pack>`; it does not create a
second, parallel selection mechanism.

### Decision 4 — Streamlit vs. CLI Scope

`Project/main.py`'s `argparse` interface is shared internal plumbing — the
webapp's *only* way to run it is a subprocess call built from
`runner.py::start_validation()` with exactly those flags (Repository
Findings, above). Because both incremental and sanity metadata travel
through the **YAML**, not through new argparse flags (Decisions 1 and 2),
**zero new flags are added to `main.py`'s argparse**, and **zero new
commands are added to `src/validate_cli.py`'s interactive CLI menu** for any
of the three capabilities. This satisfies the task's CLI-scope constraint
by construction, not by a judgment call: there is no new CLI surface to
withhold, because the design never introduces one. All new user-facing
controls (execution-mode radio, date pickers, integrity-check selection,
report-pack picker) are additive UI in `webapp/app.py`'s existing
`tab_execute`/generation tabs only.

### Decision 5 — Excel Diff Report

**Status: DEFERRED**, not in scope, not adopted even partially. The
colleague's per-column-sheet mismatch workbook is a materially larger
change (memory profile for wide/tall diffs, `openpyxl` writer performance,
interaction with the 200–300M-row hybrid path where `Project/tiered_runner.py`
deliberately avoids materializing full frames) than any of the three ported
capabilities, and none of report-pack/incremental/sanity requires it as a
dependency. Revisit as its own decision if/when requested; do not bundle it
into this phase's implementation.

## Configuration Design

| Concept | Location | New/existing |
|---|---|---|
| Incremental enabled + filter column (static, per table) | `validation_plan.incremental.{enabled,filter_column}` in the table's YAML | New key, existing passthrough block — no schema migration |
| Incremental date range (per run) | `validation_plan.incremental.{from_date,to_date}`, written into the YAML immediately before `start_validation()` (same file, same mechanism as `mismatch_threshold_pct`) | New keys, same existing runtime-mutation mechanism |
| Integrity/sanity check definition | New `integrity_check:` sibling block under a table's `validations:`, typed by new `IntegrityCheckBlock` | New — requires a `config_schema.py` change (unlike incremental) |
| Report-pack identity | Directory name under `config/report/<pack>/` | Existing, unchanged — no config addition |
| Execution-mode selection (Historical/Incremental), date-picker values, selected report pack | Streamlit `st.session_state` only | UI-only state, never persisted beyond the pre-run YAML mutation above |

Distinguishing existing vs. new vs. derived vs. UI-only, per the task's
requirement: `source_filter`/`target_filter`/`row_hash`/`execution_strategy`
are existing; `validation_plan.incremental.*` and `integrity_check:` are new
config; the resolved `filter_column` shown in the UI is derived (read
straight from the picked table's YAML, never typed by the user); the
Historical/Incremental radio and date-picker widget values are UI-only
until the moment they're written into the YAML for that run.

## Streamlit UX Design

All changes are inside `webapp/app.py`'s existing `tab_execute` (Run
Validation) and the existing generation tabs — no new top-level tab.

**Execution Mode (incremental):**
- A new radio next to the existing `layer`/`environment` selectboxes
  (~line 3498-3500): `Historical` (default) / `Incremental`.
- When `Incremental` is chosen, for each table currently checked in the run
  grid, read `validation_plan.incremental.filter_column` from that table's
  YAML (already loaded during the inventory scan) and display it read-only
  next to the table row, e.g. `Filter column: updated_at`.
- A table whose YAML has no `validation_plan.incremental.enabled: true`
  shows `Incremental unavailable for this table — configure an incremental
  filter column first` and is excluded from that run's incremental set (not
  silently run historical — surfaced, per Decision 1's error-handling rule).
- Two date/datetime inputs (`From`, `To`) appear only in Incremental mode;
  `st.error` + disabled Run button if `To < From` or either is blank.
  Datetime vs. date-only: match whatever precision the connectors already
  round-trip cleanly (Postgres/MSSQL/Snowflake all handle a `YYYY-MM-DD
  HH:MM:SS` string in a `BETWEEN`) — use datetime inputs by default, since a
  date-only picker under-specifies the boundary and users can always type
  midnight.
- On "Run validation": for each incremental table, write
  `validation_plan.incremental.from_date`/`to_date` into its YAML using the
  exact same `yaml.safe_load` → mutate → `yaml.dump` pattern already used
  for `mismatch_threshold_pct` (lines ~3694-3710), then call
  `start_validation()` unchanged.

**Sanity / Integrity checks:**
- The existing "Validation type" multiselect (line ~3568-3572,
  `_vtype_opts = sorted({r["vtype"] for r in _inventory})`) already becomes
  aware of a new `integrity_check` vtype the moment the inventory scan
  recognizes `integrity_check:` blocks — no new UI widget needed there, it's
  additive by construction of the existing generic scan.
- Results: integrity-check rows in the results table show `row_count`
  (violations found) and `status` using the same summary-reading code path
  as everything else — no new results view.

**Report Pack:**
- New selectbox above the existing folder multiselect, populated by
  `Decision 3`'s directory scan; selecting a pack sets the folder
  multiselect's default to `["report/<pack>"]` instead of `_all_folders`.
  This is a convenience default on top of the existing filter, not a new
  code path.
- A small aggregate line above the results table when a pack is active:
  `Report pack '<pack>': N/M tables passed` — computed from the same
  per-table results Streamlit already has after a run.

## Execution Flow (delta from the existing flow, above)

```
Project/main.py, per table_config["validations"] item:
    if "target" not in validation_config:
        → run_integrity_check(source_df, validation_config)      [NEW branch]
    elif (validation_plan.get("incremental") or {}).get("enabled"):
        → apply_incremental_predicate(source_query, filter_column, from_date, to_date)
        → apply_incremental_predicate(target_query, filter_column, from_date, to_date)
        → (existing fetch/canonicalize/compare path, unchanged)
    else:
        → (existing fetch/canonicalize/compare path, completely unchanged)
```

## Environment Resolution

Unchanged. All three capabilities call `get_database()` exactly as today;
none introduces a second resolution path (see "Capabilities Explicitly NOT
Adopted → Environment Handling").

## Error Handling

| Case | Behavior |
|---|---|
| Incremental requested, no `filter_column` configured | Fail before query execution; ERROR status via existing `_write_error_summary` path; Streamlit disables Run for that table pre-flight |
| Incremental requested, missing/blank `from_date`/`to_date` | Same — caught in Streamlit before subprocess start |
| `from_date > to_date` | Caught in Streamlit (disabled Run) and defensively in `apply_incremental_predicate()` |
| Invalid timestamp format | `apply_incremental_predicate()` raises; surfaced as ERROR row, not a silent pass-through |
| Unsupported temporal datatype for the configured column | Surfaces as the connector's own SQL error (existing `pyodbc.Error`/`psycopg2.Error` handling in `main.py` already catches and records this as FAIL/ERROR) — no new handling needed |
| Unknown report pack (folder doesn't exist) | Streamlit picker only ever lists real directories — can't select an unknown one; N/A |
| Report pack has no YAMLs yet | Existing "No YAML configs found" warning already covers this (line ~3552) |
| Malformed integrity-check query | Existing `pyodbc.Error`/`psycopg2.Error`/generic `Exception` handling in `main.py`'s per-table try/except already wraps the new branch — reuse, not new handling |
| Integrity check returns violations | Not an error — FAIL status with `row_count` = violation count, same as any other FAIL |

## Backward Compatibility

No existing YAML needs to change. `validation_plan.incremental` absent →
today's unconditional historical execution (the new branch is only entered
when `.get("incremental", {}).get("enabled")` is true). No `integrity_check:`
block → today's behavior exactly. No pack selected in Streamlit → today's
generic folder/vtype filtering, unchanged. `config_schema.py`'s new
`IntegrityCheckBlock` is additive (`Optional[...] = None` on
`TableValidations`, which is already `extra="allow"`) — existing YAMLs
validate exactly as before.

## Scalability Considerations

Incremental filtering is pushed into the SQL text before
`obj.execute_query()` runs — the database filters, not Python, so it
reduces (not adds to) the in-memory DataFrame size for large tables, which
matters given this codebase's existing `fetchall()`/full-DataFrame
constraints (per `data-comparison-report` skill's 200-300M-row context).
Integrity checks likewise execute the violation query in the database and
only materialize the (expected-small) violation rows — never a full-table
fetch-then-filter in Python for either capability.

## Testing Strategy

**Incremental:** `created_at`-configured table, `updated_at`-configured
table (proves the column is genuinely per-table, not defaulted), a table
with `incremental.enabled: false`/absent (must run historical unchanged),
missing `filter_column` (must error, not silently run historical), missing
`from_date`/`to_date` (must error), `apply_incremental_predicate()` unit
tests for: no existing `WHERE` (adds one), existing `WHERE` (adds `AND`),
trailing-`;` handling, invalid date string. Confirm identical predicate
text/semantics applied to both `source_query` and `target_query`.

**Sanity:** zero violations → PASS; violations returned → FAIL with correct
`row_count`; query execution failure → ERROR (existing exception path);
audit record written via `append_validation_audit` with the same shape as
other validations; `IntegrityCheckBlock` schema rejects a block that also
has a `target` (proves it can't be confused with a comparison block).

**Report Pack:** pack picker lists exactly the real `config/report/`
subdirectories (no hardcoded fixture list to keep in sync); selecting a
pack narrows the folder multiselect default; a pack with zero YAMLs shows
the existing "no configs" warning; pack-level pass/fail rollup matches a
manual count of the same per-table results.

**Regression (must still pass unmodified):**
`Project/test_tiered_runner.py`, `Project/utils/test_quality_checks.py`,
`Project/utils/test_row_compare.py`, `Project/utils/test_semantic_normalize.py`,
`Project/utils/test_utility_checks.py`, plus a manual historical CLI/`main.py`
run against an existing bronze YAML with no `incremental`/`integrity_check`
keys, confirming byte-identical `sourcequery`/`targetquery` execution and
unchanged CSV output shape.

## Progressive Implementation Plan

**Phase 1 — Architecture/configuration (no behavior change).**
Add `IntegrityCheckBlock` to `config_schema.py`; add `TableValidations.integrity_check: Optional[IntegrityCheckBlock] = None`;
document the `validation_plan.incremental.{enabled,filter_column,from_date,to_date}`
shape (no code reads it yet). Regression suite must still pass unchanged.

**Phase 2 — Incremental execution.**
`Project/utils/incremental_filter.py::apply_incremental_predicate()` +
its unit tests; wire into `Project/main.py`'s per-table loop (guarded by
`validation_plan.incremental.enabled`); Streamlit Execution Mode
radio + date inputs + filter-column display + pre-flight validation +
YAML-mutation-before-run, mirroring the existing threshold-injection code.

**Phase 3 — Sanity/integrity checks.**
`run_integrity_check()` in `quality_checks.py` + unit tests; `main.py`'s
no-target branch; Streamlit: confirm the existing vtype multiselect
surfaces `integrity_check` rows with no changes needed, add results
presentation for `row_count`/violations if the generic table doesn't
already show it.

**Phase 4 — Report-pack execution.**
Streamlit-only: dynamic pack discovery, pack selectbox, folder-multiselect
pre-filter, pack-level result rollup. No engine change (Discrepancy #1).

**Phase 5 — Output enhancements.**
Excluded from this implementation entirely per Decision 5 (deferred). No
work scheduled.

**Phase 6 — Integration/regression.**
Full existing test suite; a combined manual run: one table with
`incremental`, one with `integrity_check`, one plain historical table, one
report-pack table, in a single `Run validation` click, confirming no
cross-contamination between the four paths and unchanged behavior for
tables using none of them.

## Files Expected to Change

- `src/validation/config_schema.py` — add `IntegrityCheckBlock`, extend `TableValidations`.
- `Project/utils/quality_checks.py` — add `run_integrity_check()`.
- `Project/utils/incremental_filter.py` — new file, `apply_incremental_predicate()`.
- `Project/main.py` — two new branches in the per-table loop (no-target → integrity path; `incremental.enabled` → predicate injection before `execute_query`). No argparse changes.
- `webapp/app.py` — `tab_execute` additions (Execution Mode radio, date inputs, filter-column display, report-pack selectbox, pack rollup); generation tabs may need an "Integrity check" input surface if operators are expected to author `integrity_check:` blocks via the UI rather than hand-editing YAML (open question below).
- New/updated tests: `Project/utils/test_incremental_filter.py`, additions to `Project/utils/test_quality_checks.py`, additions to `src/validation/test_config_schema.py` if one exists (verify before creating).

## Files That Must NOT Be Replaced

`Project/main.py`'s existing fetch/canonicalize/compare path,
`Project/utils/utility.py`'s `create_summary`/`get_config_output_paths`/
`generate_runid`/`should_dispatch_hybrid`, `Project/utils/row_compare.py`,
`Project/utils/semantic_normalize.py`, `Project/tiered_runner.py`,
`Project/db/factory.py`'s environment resolution, `src/validate_cli.py`'s
existing command surface (no new commands added there), and
`Project/runner.py::start_validation()`'s existing five-parameter signature
(no new parameter — everything travels through the YAML).

## Risks

- **Concurrent-run YAML mutation.** The existing threshold-injection pattern
  (and this decision's `from_date`/`to_date` injection) mutates a shared
  on-disk YAML file immediately before a run. Two operators running the
  same table concurrently could race. This risk already exists today for
  `mismatch_threshold_pct`/`count_mismatch_threshold_pct` and is not
  introduced by this decision — but it is now exercised by two more fields.
  Not blocking; worth a future ADR if it ever causes a real incident.
- **`IntegrityCheckBlock` under-specification.** The exact set of fields a
  real integrity/orphan query needs beyond `sourcequery`/`test_case`/
  `summary` (e.g. does it ever need `source_database`/`source_schema`
  overrides like other blocks?) should be nailed down against 1-2 real
  integrity queries before Phase 3, not guessed here.
- **Regex-based `WHERE` detection.** `apply_incremental_predicate()`'s
  top-level `WHERE` check is sufficient for this codebase's generated SQL
  shape (flat `SELECT...FROM...[WHERE...]`, confirmed by inspection) but
  would misfire on a hand-authored custom-SQL YAML (`webapp-yaml-generation`
  skill's manual editor path) containing a subquery with its own `WHERE`.
  Should be tested explicitly against at least one custom-SQL YAML before
  Phase 2 ships.

## Open Questions

1. Should operators be able to author `integrity_check:`/`validation_plan.incremental`
   blocks through a Streamlit form, or is hand-editing the YAML (then
   re-uploading/placing it) acceptable for the first version? This decides
   whether `webapp/app.py`'s generation tabs need new input widgets in
   Phase 1/3, or only the Run Validation tab needs changes.
2. For `integrity_check`, should `source_database`/`source_schema` override
   fields be added to `IntegrityCheckBlock` from the start (matching every
   other block type), or omitted until a real integrity query needs them?
3. Datetime precision for the incremental date pickers — confirm with actual
   source-column types (are any candidate incremental columns `DATE` rather
   than `TIMESTAMP`? A `BETWEEN` against a `DATE` column with a
   full-timestamp bound needs care at the day boundary).
4. Should the pack-level rollup in Streamlit be a strict requirement for
   Phase 4, or nice-to-have — given Discrepancy #1 shows execution already
   works without it?

## Final Decision Summary

Port report-pack execution (mostly already live — Streamlit UX only),
sanity/integrity checks (new `quality_checks.py` function + new schema
block + one new `main.py` branch), and incremental execution (new
`validation_plan.incremental` config, a new shared SQL-predicate helper, one
new `main.py` branch, Streamlit-only date-range UX) into the live engine.
Preserve semantic normalization, quality checks, hybrid execution, and
environment resolution exactly as they are. No new CLI surface anywhere.
No hardcoded report-pack list, no hardcoded incremental column. Excel diff
reporting is explicitly deferred, not adopted.

## Implementation Readiness Checklist

- [ ] Open questions 1-4 answered by the repo owner
- [ ] `IntegrityCheckBlock` field list confirmed against ≥1 real integrity query
- [ ] `apply_incremental_predicate()` tested against ≥1 custom-SQL YAML with an existing subquery `WHERE`
- [ ] Phase 1 schema changes merged with full regression suite green
- [ ] Phase 2 (incremental) implemented + tested before Phase 3 starts
- [ ] Phase 3 (sanity) implemented + tested before Phase 4 starts
- [ ] Phase 4 (report-pack UX) implemented
- [ ] Phase 5 (Excel diff) explicitly skipped, not silently dropped
- [ ] Phase 6 full regression + combined manual run performed
