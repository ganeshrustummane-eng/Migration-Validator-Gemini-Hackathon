# 0043. Applying the Bronze scope filters to Silver validation

**Status:** Implemented with changes — see [0045](0045-scope-filters-implemented-bronze-and-silver.md) (§2 replaced: Silver parents are `INT_<PARENT>` with `IS_CURRENT`)
**Date:** 2026-09-28
**Follows on from:** [0042](0042-declarative-scope-filter-spec-rendered-per-layer.md)
**Related:** [0019](0019-silver-multisource-nodes-and-batch-node-ui.md) (multisource), [0020](0020-silver-fivetran-active-filter-open-question.md) (`_FIVETRAN_ACTIVE`), [0026](0026-validation-plan-metadata-block-in-silver-yaml.md)

## Context

Silver validation compares a Bronze recompute query (Bronze Snowflake, Coalesce
transform verbatim) with the materialized Silver table (`INT_<table>` in
`{env}_EDGE_SILVER`). `src/silver/silver_sql_emitter.py::emit_query_set()`
currently emits **no WHERE clause on either side** and ignores
`plan.source_filter`/`target_filter`. The requirement is to validate Silver on
**the same population Bronze was validated on**, using the filters from the
workbooks.

A real Silver YAML already exists: `Project/config/silver/data_validation/snowflake/DISCOUNT_LINES.yaml`.
It shows three things:

- `FACILITY_ID` passes through unchanged (`"DISCOUNT_LINES"."FACILITY_ID" AS "FACILITY_ID"`).
- `FACILITY_KEY` is a macro-computed surrogate and is skipped.
- The table is SCD-shaped (`IS_CURRENT`, `EFFECTIVE_FROM/TO`, `SYS_VERSION`), so it
  keeps history rows.

## Decision

### 1. Look up the filter by the Bronze table name, not the `INT_` name

The workbook's "Target Table Name" is the **Bronze** table (`discount_lines`).
The Silver plan already knows its Bronze source (`plan.source_table`, from the
Coalesce dependency's `nodeName`). Match on that, case-insensitively. Do **not**
derive `INT_` + table name: the Silver name comes from Coalesce metadata, and
the `INT_` prefix is a naming convention that no code relies on today.

### 2. Same spec, two renderings, and the scope anchors always read Bronze

- **Bronze recompute side:** the base-table predicates use the Bronze column
  names (`"DISCOUNT_LINES"."FACILITY_ID" IN (…)`).
- **Silver side:** the same predicates use the **mapped Silver column** for
  each FK/date column. It is found through `plan.mappings`
  (source_column → target_column), so renamed columns work.
- **The parent subqueries (`facilities`, `tenants`, `Owners`, …) read the
  Bronze tables on both sides.** Bronze is the single source of truth for
  "which facility belongs to company 137". Joining the Silver side to Silver
  parents (`INT_FACILITIES`) would be worse for three reasons: those parents
  are SCD tables, they are keyed by macro surrogates (`FACILITY_KEY`), and a
  Silver defect in the *parent* would then change the *child's* population,
  which would look like a child failure. Snowflake reads Bronze and Silver
  databases in one query without trouble.

Example for `discount_lines` (shape A):

```sql
-- Bronze recompute
... FROM "BRONZE_EDGE"."<schema>"."DISCOUNT_LINES"
WHERE "DISCOUNT_LINES"."FACILITY_ID" IN (SELECT ID FROM <bronze>.FACILITIES WHERE COMPANY_ID IN (137,9088,501))
  AND "DISCOUNT_LINES"."CREATED_AT" >= '2024-08-01'
-- Silver
... FROM "{env}_EDGE_SILVER"."<schema>"."INT_DISCOUNT_LINES"
WHERE "FACILITY_ID" IN (SELECT ID FROM <bronze>.FACILITIES WHERE COMPANY_ID IN (137,9088,501))
  AND "CREATED_AT" >= '2024-08-01'
```

The row-count queries get the identical WHERE. Today they are unfiltered, and
they must never disagree with the data queries.

### 3. A generation gate when the Silver side cannot be filtered

If an FK or date column the spec needs is **missing from the Silver table**,
the Silver side cannot be filtered. This happens when the column is
macro-skipped, dropped by the node, or replaced by a surrogate key (for
example the Silver table has only `FACILITY_KEY`). In that case, **block YAML
generation for that node** with a clear reason. This is the same UX as the
schema-drift gate (ADR 0015).

Two tempting fallbacks are rejected:

- **Filter only the Bronze side.** Every out-of-scope Silver row would become a
  false `TARGET_ONLY`.
- **Filter the Silver side by a key semi-join against the filtered Bronze rows.**
  This hides real `TARGET_ONLY` rows, which are extra Silver rows, so
  Completeness could never fail.

The human resolves it in one of two ways:

- give a Silver column to use (UI override, stored with the plan, like the
  natural-key override);
- mark the node "validate in full" if it is small.

### 4. Multisource nodes

For nodes with `bronze_join_sql` (ADR 0019), the filter anchors on the
**driving table**, meaning the first dependency, which is already
`plan.source_table`. It is appended after the verbatim join SQL, qualified
with that table's alias or name as it appears in the join text:

- If the join text already contains a `WHERE`, append with `AND`.
- If the driving table's alias can't be found in the join text, apply the
  step-3 gate (never guess).

This needs checking against a second real multisource sample before it is
trusted.

### 5. It does not answer ADR 0020, but it gives it a place to live

The scope filter adds **no** `_FIVETRAN_ACTIVE` predicate on the base table.
SCD Silver tables keep history, and 0020's evidence still holds. The parent
subqueries follow whatever the Bronze sheet does, which is
`f._FIVETRAN_ACTIVE = TRUE`, so that Silver's population matches Bronze's.
Both sides use the same subquery, so the choice cannot create an asymmetry.

The spec (0042) is the natural home for 0020's "config-driven per-node flag":
an optional `active_only: true|false` per table. Proposal: ask the test team to
add an "Active rows only (Y/N)" column to future sheets. That closes 0020
without guessing from column names.

### 6. Visibility

The generated Silver YAML records the applied spec under
`validation_plan.population_scope.scope_filter`, with its provenance (workbook +
row) (ADR 0026). A node with no matching sheet row gets
`review_reasons: "no scope filter found for <bronze table>; validated in full"`.
"Not filtered" and "filtered" must never look the same.

## Alternatives considered

- **A separate Silver filter sheet.** Rejected: the whole point is the *same*
  population as Bronze. Two sheets would drift like the SQL cells did (0041).
- **Filter the Silver side through Silver parent tables (`INT_FACILITIES`).**
  Rejected: SCD duplicates, surrogate keys, and parent defects leaking into the child population.
- **Silver side filtered by a key semi-join on the Bronze result.** Rejected: hides
  `TARGET_ONLY` (point 3).
- **Evaluate the filter in Python after fetching.** Rejected: the skill already rules
  it out (filters are pushed into SQL), and it defeats the purpose on 1 B-row tables.

## Consequences

- Easier: Silver results line up with Bronze results table by table (same
  companies, same window), so a Silver failure can be traced back to Bronze.
- Harder: the silver emitter now depends on the mapping lookup for FK
  columns, and needs a new gate state.
- Watch for:
  - Silver tables whose FK exists only as a surrogate key (step-3 gate
    frequency; if it is common, revisit);
  - date columns transformed in Silver (`TO_TIMESTAMP_NTZ`), where comparing to a date
    literal is fine but a timezone-converted one is not;
  - PK uniqueness on SCD Silver tables. `DISCOUNT_LINE_ID` alone repeats across versions.
    This is a separate issue, but a filter makes it more visible.
