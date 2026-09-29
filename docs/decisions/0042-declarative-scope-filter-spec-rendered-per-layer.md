# 0042. One declarative scope-filter spec per table, rendered to SQL per layer

**Status:** Proposed — awaiting review, nothing implemented
**Date:** 2026-09-28
**Follows on from:** [0041](0041-what-the-bronze-filter-workbooks-tell-us.md)
**Related:** [0034](0034-explicit-incremental-execution-mode-contract.md) (incremental), [0011](0011-hybrid-v1-front-door.md) (no automatic hybrid switch), [0038](0038-environment-database-placeholders-in-generated-yaml.md) (`{env}`)

## Context

One filter per table must produce **the same row population** in three queries:

1. the source DB (Postgres, MSSQL, …) for Bronze validation,
2. Bronze Snowflake (the target for Bronze, and the recompute source for Silver),
3. Silver Snowflake (`INT_<table>`).

Today the workbook gives this as Postgres `select count(*) …` text, with
hand-copied Snowflake variants. Future sheets will give only *Source Type,
Source Table, Target Table, Source Count (optional), Size Tier, Filter to apply*.
SiteLink already gives only prose. 0041 showed that the free text is
unreliable. It also showed that the actual information is small: a scope list,
an anchor path, a date cut-off, and an optional extra predicate.

If each layer keeps its own copy of the filter SQL, they drift apart. The
workbook already proves this: in 4 of 27 rows the Snowflake cell does not
match the filter cell. That violates the Consistency dimension (CLAUDE.md).

## Decision

**Parse each workbook row once into a small structured spec. Render SQL from
that spec, deterministically, for each layer. Never pass the sheet's SQL
through.**

### 1. The spec (per table)

```yaml
table: phone_numbers            # Bronze / source table name: the lookup key
source_system: storedge_postgres
tier: medium                    # small | medium | large
mode: scoped                    # full | scoped
scope: company                  # names an entry in the scope registry (below)
path:                           # hops from this table to the scope column
  - {fk: owner_id, parent: tenants, pk: id, where: "owner_type = 'Tenant'"}
  - {fk: facility_id, parent: facilities, pk: id}
date_cutoff: null               # or {column: created_at, from: '2025-08-01'}
provenance: "postgres Table filters.xlsx / Sheet1 row 17"
```

### 2. The scope registry (per source system, a handful of lines)

```yaml
storedge_postgres:
  company: {table: facilities, column: company_id, values: [137, 9088, 501]}
sitelink_mssql:
  corp:    {table: Owners, column: sCorpCode, values: [SLQA, STP2DEV, BIGSSO, CVIJAY]}
```

The scope values sit in one place and not in 47 rows. Changing the test
companies becomes a one-line edit.

The same file can also carry the few **known anchor hops**
(`facility_id → facilities`, `tenant_id → tenants`, `unit_id → units`,
`SiteID → Sites → Owners`). A future row whose filter is blank can then get a
*proposed* path from its columns. It is proposed only, and shown for human
approval (see 0044).

### 3. Rendering: a semi-join, never a JOIN

Each hop becomes a nested `IN` subquery on the base table. The base table is
never joined:

```sql
WHERE t.owner_id IN (
        SELECT id FROM <db>.<schema>.tenants
        WHERE owner_type = 'Tenant'            -- hop predicate
          AND facility_id IN (
                SELECT id FROM <db>.<schema>.facilities
                WHERE company_id IN (137, 9088, 501)))
  AND t.created_at >= '2025-08-01'             -- only if date_cutoff
```

Why `IN` and not the sheet's `JOIN`:

- **A JOIN can duplicate base rows.** Fivetran history-mode Bronze tables
  (for example the `FACILITIES` table behind SCD-shaped `INT_FACILITIES`, see
  ADR 0020) hold **several rows per `id`**. `JOIN facilities f` then
  multiplies child rows. That creates false Uniqueness failures and wrong
  counts unless every joined table also carries `_FIVETRAN_ACTIVE = TRUE`,
  and that rule is exactly what the sheet's hand-written queries got wrong in places.
  `IN` cannot duplicate rows.
- The SELECT list stays the base table's own columns, so the existing
  column/normalization generation is unchanged. Only the WHERE grows.
- It is the same shape as the `EXISTS` that
  `sql_query_generator._plan_filters()` already emits for
  `relationships[purpose="population"]`. **Extend that code; don't add a second
  one.** It needs three additions:
  - a predicate on the parent,
  - chaining for more than one hop,
  - fully qualified parent names (`{env}` database + schema). Today they are unqualified.

  Rendering is written once and called by both the Bronze generator and the
  Silver emitter (0043).

### 4. Where the spec lives on the plan

- There is no new plan field. The rendered predicates go into
  `source_filter`/`target_filter`, which already exist. They are baked into the
  generated SQL at YAML-generation time, as Bronze does today.
- The spec itself is recorded under `validation_plan.population_scope.scope_filter`
  (ADR 0026: every plan field is visible in the YAML). A reviewer can see *why*
  the WHERE says what it says, and which workbook row it came from.

### 5. The date cut-off is a static filter, not the incremental mode

`created_at >= '2025-08-01'` looks like incremental, but it defines the agreed
test population and does not change per run. It is baked into the SQL like the
company scope. The incremental mode (ADR 0034) still works on top of it with
`AND`. A static filter also keeps working with `hybrid_v1`, while incremental
does not (ADR 0033). Large tables need exactly that combination.

### 6. Size tier and source count

- If the sheet gives a tier, use it. If not, compute it from the row count
  using the thresholds confirmed in 0041 (inferred as Small < 1 M ≤ Medium < 1 B ≤ Large).
- If the count is missing, compute it cheaply. Only the tier bucket needs it:
  - Snowflake: `INFORMATION_SCHEMA.TABLES.ROW_COUNT`, or plain `COUNT(*)`,
    which Snowflake answers from metadata.
  - Postgres: `pg_class.reltuples` (an estimate, good enough for a bucket).
    Not `COUNT(*)`: that is the 27-minute query.
  - MSSQL: `sys.dm_db_partition_stats`.
- Count the table that is **actually scanned**. For Silver that is Bronze,
  which may hold history rows and be larger than the source.
- What the tier does:
  - **Small** → `mode: full` is allowed.
  - **Medium / Large** with no filter → a **warning at YAML generation**, not a
    silent full scan.
  - **Large** → *suggest* `hybrid_v1` in the review panel. Never switch
    automatically (ADR 0011).
- "Count reduced from" is **not** used as a pass/fail expectation. It is free
  text and was taken at a different time (0041 #7, #8). At most, show it next
  to the generated count as a sanity hint.

## Alternatives considered

- **Pass the sheet's SQL through (strip `select count(*)`, keep the rest).**
  Rejected: this inherits every defect in 0041 (invalid quotes, copy-pasted
  tables, a wrong join column). It is Postgres dialect, so it would still need
  rewriting per layer, and it cannot handle SiteLink, which has no SQL.
- **Keep three hand-written queries per table (Postgres, Bronze, Silver).**
  Rejected: this is the drift the sheet already shows. It triples the upkeep for every scope change.
- **Render as a `JOIN` like the sheet does.** Rejected: row duplication on history-mode
  parents (point 3).
- **A generic filter DSL or plugin system.** Rejected (no speculative abstraction): the
  spec covers all 55 real rows (27 Postgres + 28 SiteLink) with four fields.
- **Put the date cut-off into `validation_plan.incremental`.** Rejected: ADR 0034 keeps
  run windows out of the YAML, and the conflict with hybrid (0033) would block Large tables.

## Consequences

- Easier: scope changes are one line. Bronze and Silver are guaranteed the
  same population. SiteLink's prose rows become 3 specs, not 28 queries.
- Harder: someone has to confirm the anchor paths once per source system,
  especially SiteLink, which has no SQL to check against.
- Watch for:
  - a parent table missing from the Snowflake schema that the path assumes
    (fail at generation, not at run time);
  - polymorphic tables (`addresses`) that don't fit a single path. Allow
    `mode: custom` with a reviewed raw predicate as the one escape hatch, and
    show it clearly in the review UI.
- Implementation order when approved:
  1. the spec and the reader for these two sheet layouts, reusing
     `excel_batch_loader`'s header-matching helper and not a second openpyxl
     parser;
  2. extend `_plan_filters()` rendering;
  3. apply to Bronze;
  4. apply to Silver (0043).
