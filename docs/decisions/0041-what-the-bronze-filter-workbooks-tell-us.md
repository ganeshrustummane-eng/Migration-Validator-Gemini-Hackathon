# 0041. What the Bronze filter workbooks tell us (Postgres + SiteLink)

**Status:** Investigation only — findings, no code change
**Date:** 2026-09-28
**Inputs:** `docs/Excel-Files/postgres Table filters.xlsx`, `docs/Excel-Files/Sitelink_bronze_filters.xlsx`
**Feeds:** [0042](0042-declarative-scope-filter-spec-rendered-per-layer.md), [0043](0043-applying-scope-filters-to-silver-validation.md), [0044](0044-ai-for-filter-interpretation-deferred.md)

## Context

The test team uses two workbooks to shrink the Bronze validation population
to something runnable. The next phase is to apply the **same** population to
Silver (`INT_<table>` in `{env}_EDGE_SILVER`). Many people edit these sheets,
and more sheets will come. This ADR records what the sheets actually contain,
what patterns hold, and where the sheets are inconsistent, so the design ADRs
(0042–0044) rest on evidence and not on guesses.

Every sheet in both workbooks was read (no formulas, no cell comments, plain values).

## Postgres workbook (`postgres Table filters.xlsx`)

### Sheets

| Sheet | What it is |
|---|---|
| `Sheet1` | Master list. Columns: Source Type, Target Table Name, Source Count, Size Tier, Filter to apply, Count reduced from, Snowflake (query), Postgresql (query), Postgres Issue, Snowflake Issue. 47 tables. |
| `Sheet3` | Same 47 rows. The last 4 columns are replaced by: Source YAML SQL verification, Target YAML SQL verification, Status, Framework Testing. For `addresses` and `invoiceable_amounts`, the "Status" cell holds the full corrected Snowflake SELECT that was actually used. |
| `Testing-postgres` | `Sheet3` plus a "Testing-Status" column (`Done` for all 27 filtered tables). The whole block is duplicated side by side (columns A–K and L–U are identical). |
| `Sheet7` | Bronze run outcome per table: `Fail` with reason `UUID`, `UUID compartable`, `Missing Items`, or blank. |
| `Count` | Scratch notes: Snowflake count per company (137 / 501 / 9088) next to one Postgres count, plus two timings ("27.11 mins" for `line_items`, "2.5 MINS" for `insurance_activities`). |

### Size tiers

| Tier | Tables | Source count range | Filter given? |
|---|---|---|---|
| Large | 2 | 1.16 B – 1.52 B | yes |
| Medium | 25 | 2.28 M – 448 M | yes |
| Small | 20 | 149 – 786 877 | **no → compared in full** |

The sheet never says where the cut-offs are. The data fits **Small < 1 M ≤
Medium < 1 B ≤ Large**. That is inferred, not stated, and needs confirming.
Note that `transactions` at 448 M is *Medium*.

### The 27 filters collapse into 6 shapes

Every filter reads "keep the rows that belong to companies **137, 9088, 501**",
"keep rows created after a cut-off date", or both. They differ only in **how
the table reaches `company_id`**:

| Shape | Path to `company_id` | Date cut-off | Tables |
|---|---|---|---|
| A | `t.facility_id → facilities.id` | yes | general_ledger_line_items (2024-08-01), events, transactions, account_balances, line_items, insurance_activities (2025-08-01), change_logged_events, payment_methods, discount_lines (2024-08-01) |
| B | `t.facility_id → facilities.id` | no | tenants, ledgers, leads, ledger_items, units |
| C | none (**no company scope at all**) | `created_at >= 2026-08-01` | account_balance_items, notes, account_balance_taxes, credit_items, ledger_delinquencies, invoiceable_amounts |
| D | two hops: `→ tenants → facilities` or `→ units → facilities` (plus `owner_type = 'Tenant'` for polymorphic owners) | no | phone_numbers, settings (`owner_id`+`owner_type`), contacts (`tenant_id`), unit_amenities_units (`unit_id`) |
| E | polymorphic `addressable_id / addressable_type` | no | addresses |
| F | `→ discount_plan_ledger_instances` | yes | discount_plan_ledger_instances_line_items, discount_plan_ledger_instances |

So the real content of the whole sheet is small:

1. **one scope list**: `company_id IN (137, 9088, 501)`, the same in every row;
2. **three anchor tables**: `facilities`, `tenants`, `units`;
3. **a per-table date cut-off** (three distinct values);
4. **one or two extra predicates** (`owner_type = 'Tenant'`, `addressable_type`).

### Defects found in the sheet itself

These matter because the sheet is the *input*. A tool that trusts it
verbatim inherits every one of these errors.

| # | Table | Problem |
|---|---|---|
| 1 | transactions | `j.created_at >= 2025-08-01'`: the opening quote is missing, so the SQL is invalid. |
| 2 | account_balance_taxes | The Snowflake and Postgres query cells contain the **change_logged_events** query (copy-paste). |
| 3 | credit_items | The "Snowflake" cell contains the Postgres query (`public.credit_items`, no `_FIVETRAN_ACTIVE`). |
| 4 | addresses | The filter joins on `facility_id`, which the sheet itself says does not exist on `addresses`. The query actually used (`Sheet3` Status cell) joins `addressable_id = f.id`, keeps only `addressable_type = 'Facility'` (not `'Tenant'`), and **drops the company filter**. The stated filter and the executed filter disagree. |
| 5 | discount_plan_ledger_instances_line_items | `created_at` is ambiguous: the sheet notes it does not exist on the parent. |
| 6 | discount_plan_ledger_instances | Its filter is a copy of the child table's filter (it counts line items, not instances), and its "reduced" count is the child's count. |
| 7 | all | "Source Count" differs from the first number in "Count reduced from" (e.g. GLLI 1 520 173 879 vs 1 914 371 031). The counts were taken at different times or in different environments. |
| 8 | units, contacts | "Count reduced from" has no "to" (`4598726 \n 78134`). It is free text, not a number. |
| 9 | all Snowflake queries | Hard-coded `DEV_EDGE_BRONZE.STOREDGE_FMS_PUBLIC`. This conflicts with the `{env}` placeholder rule (ADR 0038). |
| 10 | `Count` sheet | `settings` shows 12 235 rows in Snowflake for company 137 against 6 996 418 in Postgres, which is the reverse of every other table. These are exploratory numbers, not assertions. |

### Other observations

- The **Shape C** tables have no company scope. Those tables are validated for
  *all* companies since 2026-08-01. That may be intended (they have no
  `facility_id`), but it means their population is not the same "3 test
  companies" population as the rest. **Open question for the test team.**
- The Snowflake queries add `_FIVETRAN_ACTIVE = TRUE` on **every joined
  table** (`j.` and `f.`, and `t.` for two-hop). Our current Bronze generator adds it
  only to the base table, unqualified (`sql_query_generator.py:528-536`).
- `Sheet7`: 13 tables fail. Seven of them give the reason "UUID
  compatible/UUID". That points at a type-normalization gap for UUID columns, which is a
  separate issue from filtering and is **not** investigated here. It needs its own ticket.
- The timings in the `Count` sheet ("27 min" for one count on `line_items`)
  are the reason the filters exist: without them, Medium and Large tables are
  impractical to validate.

## SiteLink workbook (`Sitelink_bronze_filters.xlsx`)

### Sheets

| Sheet | What it is |
|---|---|
| `Sheet1` | 28 tables. Columns: s.no, table, column. The "column" cell is prose, not SQL. |
| `Sheet2` | Row count per table (1 to 1 011 424, so all small by the Postgres tiers), plus a matrix of **which corp codes actually exist in which table**. |
| `Sheet4` | Empty. |
| `Codes` | Corp code → location code: SLQA→TEST7, STP2DEV→L001, BIGSSO→L001, CVIJAY→Test1. |

### The 28 rows collapse into 3 shapes

| Shape | Rule | Tables |
|---|---|---|
| Direct | `sCorpCode IN (...)` on the table itself | NationalMasterAccounts, Promotions, Owners, PromoAccounts, PromoOffers |
| Full | no filter | AcctTypes, AcctSoftware |
| Via site | `t.SiteID → Sites.SiteID`, `Sites.OwnerID → Owners.OwnerID`, `Owners.sCorpCode IN (...)` | 20 tables (Addresses, Charges, Ledgers, Tenants, Units, …). `Sites` itself uses only the second hop. |

The scope list is the four corp codes: `SLQA, STP2DEV, BIGSSO, CVIJAY`.

### Observations

- **No SQL is given for SiteLink at all**, only a description of the join path.
  This is exactly the "future sheets won't have the queries" case, and it is
  already here.
- The path is **the same for 20 of 28 tables**. It is one relationship fact
  (`SiteID → Sites → Owners`), not 20 separate filters.
- Contradiction: `Sheet1` says NationalMasterAccounts is filtered by
  `scorpcode`, but `Sheet2` lists it as "No-Corp-code". **Open question.**
- `L001` maps to two corp codes, so a location code alone does not identify a
  site. The `Codes` sheet suggests the intended scope might be *(corp,
  location)* pairs, not just corps. `Sheet1` never uses location codes.
  **Open question.**
- The `Sheet2` presence matrix (e.g. Addresses → SLQA only) gives us a free
  **extra check**: after filtering, the distinct corp codes found must be a
  subset of the expected set. This catches a filter that silently matched
  nothing.

## What the existing code does with sheets like these today

Verified against the code, not assumed:

- `src/excel_batch_loader.py::load_excel()` is built for **report-pack** sheets.
  It requires a "Yaml file name" column (`:178-179`), which neither workbook has,
  so it would reject both.
- Its filter-header regex is anchored (`^filter$|^condition$`), so "Filter to
  apply." would not match without the AI header classifier.
- `_looks_like_sql()` (`:488-502`) would accept the whole
  `select count(*) from … join … where …` string **verbatim as a WHERE
  predicate**, which produces invalid SQL. Nothing unwraps the SELECT or the join.
- For multi-table rows, the filter is set on the spec but never reaches the AI
  prompt (`_instruction()`, `ai_sql_generator.py:435-446`), so it is dropped.
- `CanonicalValidationPlan` already has the right hooks: `source_filter`,
  `target_filter`, `population_scope["filters"]`, and
  `relationships[purpose="population"]`. The last one renders as
  `EXISTS (SELECT 1 FROM parent WHERE parent.pk = base.fk)`
  (`sql_query_generator.py:505-521`). But this is **one hop only**, with **no
  predicate on the parent** and **unqualified table names**. It cannot express
  `company_id IN (…)` on the parent, or a two-hop path, today.
- `src/silver/silver_sql_emitter.py` applies **no WHERE at all** on either side.
- No `company_id`, `sCorpCode`, `SiteID` or size-tier handling exists anywhere
  in `src/`, `webapp/`, `Project/` or `config/`.

## Decision

Treat the workbooks as **human-authored intent, not executable SQL**. The
durable content is *(scope list, anchor path, date cut-off, extra predicate,
tier)* per table. That is what 0042 turns into a spec. The SQL cells are
useful as a cross-check only: they disagree with the filter column in at least
4 of 27 rows.

## Consequences

- The work is much smaller than "parse 75 free-text filters". It is about 4 anchor
  paths plus a scope list per source system.
- The ten sheet defects above should go back to the test team. If they are not
  fixed, the Silver population will copy Bronze's mistakes faithfully.
- Open questions to resolve with the test team before implementation:
  1. Tier thresholds: are they 1 M / 1 B?
  2. Shape C tables with no company scope: intended?
  3. addresses: which rule is correct, the stated one or the executed one?
  4. discount_plan_ledger_instances: its real filter.
  5. SiteLink NationalMasterAccounts: is it filtered by corp code or not?
  6. SiteLink: do location codes restrict scope further?
