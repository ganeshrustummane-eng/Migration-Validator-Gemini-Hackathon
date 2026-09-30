# 0045. Scope filters from the workbooks, implemented for Bronze and Silver

**Status:** Accepted — implemented; rendering changed to JOINs and AI interpretation added by [0046](0046-scope-filters-as-joins-with-ai-interpretation.md)
**Date:** 2026-09-29
**Implements (with changes):** [0042](0042-declarative-scope-filter-spec-rendered-per-layer.md), [0043](0043-applying-scope-filters-to-silver-validation.md), [0044](0044-ai-for-filter-interpretation-deferred.md)
**Resolves:** [0020](0020-silver-fivetran-active-filter-open-question.md)

## Context

ADRs 0041–0044 proposed a design for turning the test team's filter
workbooks (`docs/Excel-Files/`) into validation filters. The user then gave
these requirements:

1. **Bronze Snowflake:** `_FIVETRAN_ACTIVE = TRUE` on **every** table in the
   query. If a filter goes through five tables, all five get it.
2. **Silver Snowflake:** `IS_CURRENT = TRUE` on every table in the same way.
3. The filters used for source → Bronze must also be used for Bronze → Silver.
   The only change on the Silver side is the table name: `INT_<table>`.
4. The workbook should be saved in one place and picked from a list. People
   must be able to change the join path in the Streamlit UI, and add steps to
   it the way the Bronze JOIN rules work today.
5. Treat each filter cell as a prompt written by a person. The tool has to
   build the query from it, whether the cell is SQL (Postgres) or plain
   English (SiteLink).
6. Don't fake anything.

## Decision

### Where the code lives

- **`src/scope_filter.py`** is the only place filters are parsed and
  rendered.
  - `load_workbook()` reads each row once into a `ScopeFilter`: conditions on
    the table itself, plus a join path of hops (`fk → parent.pk`), each hop
    with its own conditions.
  - `render()` turns that one spec into a WHERE predicate for any side. Each
    hop becomes a nested `IN (SELECT …)` subquery, never a JOIN (0042 §3).
  - `render_bronze()` and `render_silver()` hold the naming rules for each
    layer.
- **`webapp/app.py`**:
  - `pick_scope_workbook()` lists `docs/Excel-Files/*.xlsx`. An uploaded
    workbook is saved there so it shows up in the list next time. An existing
    file is never overwritten.
  - `edit_scope_filter()` shows the original cell text, a "whole table / only
    matching rows" switch, the table's own conditions, and an editable,
    growable join-path grid.
  - Both are used in Bronze step 6, where the workbook filter is ANDed with
    any manual filter, and in each Silver node.
- **`src/silver/silver_sql_emitter.py`** now applies filters. Before this it
  applied none.

### What each side gets

| Side | Table itself | Every parent in the path |
|---|---|---|
| Source DB (Postgres, MSSQL) | workbook conditions | `<schema>.<parent>` |
| Bronze Snowflake (Bronze target) | existing `_FIVETRAN_ACTIVE = TRUE` + conditions | `<db>.<schema>.<PARENT>` **+ `_FIVETRAN_ACTIVE = TRUE`** |
| Bronze recompute (Silver source) | `_FIVETRAN_ACTIVE = TRUE` on **every table read**, including multisource joins + conditions | `"<bronze db>"."<schema>"."<PARENT>"` **+ `_FIVETRAN_ACTIVE = TRUE`** |
| Silver | `IS_CURRENT = TRUE` + conditions on the mapped Silver columns | `"<silver db>"."<schema>"."INT_<PARENT>"` **+ `IS_CURRENT = TRUE`** |

Row-count queries get exactly the same WHERE as the data queries.

### Choices made while building it

- **The active-row filter goes after the window function.** The Bronze
  recompute computes `SYS_VERSION` as `ROW_NUMBER() OVER (PARTITION BY ID …)`
  over every history row. A plain `WHERE _FIVETRAN_ACTIVE = TRUE` would drop
  the older rows first, so every current row would get `SYS_VERSION = 1` and
  fail against Silver. So when the SELECT has an `OVER (`, the active-row
  predicate goes in `QUALIFY`. Scope conditions stay in `WHERE`.
- **ADR 0020 is closed: filter both sides.** 0020's objection was that
  filtering only Bronze produces false `TARGET_ONLY` rows. Filtering Bronze
  by `_FIVETRAN_ACTIVE` and Silver by `IS_CURRENT`, which Coalesce copies from
  `_FIVETRAN_ACTIVE`, keeps the two sides symmetric. If a Silver table has no
  `IS_CURRENT` column, only the Bronze side can be filtered.
  `build_plan_from_metadata()` then adds a `review_reasons` entry so the
  asymmetry shows up in the YAML.
- **Silver parents are `INT_<PARENT>`, as the user asked.** 0043 §2 proposed
  that the Silver side read Bronze parents instead; that is dropped. The
  table-name rule holds, but **one column name does not**. In the real
  `INT_FACILITIES` metadata (`docs/metadata_1.txt`), Bronze `ID` becomes
  `FACILITY_ID`. `COMPANY_ID` and the other columns keep their names.
  - `silver_parent_column()` maps `id` → `<SINGULAR>_ID` with a simple
    singular rule, marked `ponytail:`.
  - The UI checks every column name against live `INT_` columns when
    Snowflake is reachable. A missing column blocks generation.
  - The UI says so when it could not check.
- **Silver columns on the table itself come from the plan's own Coalesce
  mappings** (passthrough `"T"."FACILITY_ID"` → `FACILITY_ID`). If the Silver
  table has no passthrough of a filter column, generation is blocked.
  Filtering only one side is never allowed (0043 §3).
- **Column names are checked against live columns on each side.** Fivetran
  renames SiteLink `SiteID` to `SITE_ID`, and the prose says `site_id`, so
  matching ignores case and underscores (`match_column()`).
  - A name matches only when exactly one live column fits.
  - A column that doesn't exist blocks the table. This is how ADR 0041
    defect #4 is caught: `addresses` has no `facility_id`.
- **No scope-registry file (a change from 0042 §2).** Each row already
  carries its values (`company_id in (137,9088,501)`). SiteLink rows name the
  path but not the values, so those come from the workbook's own `Codes`
  sheet (the "Corp Code" column). Nothing is hard-coded.
- **The filter text is read as a prompt without AI (0044 stands).** The
  deterministic reader handles both SQL and prose. All 75 rows of the two
  workbooks parse. Only the two defective cells are blocked:
  - `transactions`: missing quote;
  - `discount_plan_ledger_instances`: its filter reads a different table.

  Nothing is silently repaired. Anything that doesn't parse becomes an error
  that a person fixes in the grid.
- **A small `openpyxl` reader, not `excel_batch_loader.load_excel()`.**
  `load_excel()` is built for report-pack sheets and requires a "Yaml file
  name" column that these workbooks don't have (0041). Changing it would put
  a second meaning on the report-pack loader.
- **Provenance goes into the Silver YAML.**
  `validation_plan.population_scope.scope_filter` holds the full spec with
  its workbook, sheet and row. If no row matched, it records
  `{mode: none, reason: …}`, so "not filtered" never looks like "filtered".
  Bronze YAML shows only the resulting SQL: `run_with_plan()` builds its plan
  internally and has no place for the spec.
- **Size tier is shown, nothing more.** The UI warns when a Medium or Large
  table is compared in full. There is no automatic hybrid switch (ADR 0011).

## Alternatives considered

- **Silver parents read Bronze (0043 §2).** Rejected by the user: Silver has
  to be checked against Silver's own parents.
- **Blanket `WHERE _FIVETRAN_ACTIVE` on the recompute.** Rejected: it breaks
  every `SYS_VERSION` (see above).
- **Pass the sheet's SQL through.** Rejected in 0042. It would inherit the
  sheet's defects, and it can't express SiteLink prose.
- **AI to read the prompts.** Deferred (0044): 75 of 75 rows are covered
  without it.

## Consequences

- Silver now validates **current rows only**. History rows
  (`IS_CURRENT = FALSE`) are no longer compared. This follows directly from
  requirement 2. If history needs checking later, that is a separate,
  opt-in mode.
- Multisource nodes: joined tables use
  `COALESCE(alias._FIVETRAN_ACTIVE, TRUE) = TRUE`, so an unmatched LEFT JOIN
  row survives. A base row whose only match is an inactive history row is
  dropped. This is marked `ponytail:` in the emitter; revisit it when a real
  multisource node shows the case.
- The open questions in 0041 still stand: Shape C rows have no company scope,
  the SiteLink location codes are unused, and NationalMasterAccounts'
  corp-code status is unclear. The tool uses what the sheet says and nothing
  more.
- Tests: `tests/src/test_scope_filter.py` parses both real workbooks and
  asserts the rendered SQL for each layer, the Silver gate, and a blocked row
  fixed through the editor. The emitter tests in
  `test_coalesce_plan_builder.py` were updated for the new filters.
