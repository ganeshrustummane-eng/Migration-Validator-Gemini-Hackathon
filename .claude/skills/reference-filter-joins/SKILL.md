---
name: reference-filter-joins
description: "Use when a test lead gives a natural-language validation condition (filter, multi-table join, transformation check, batch scope) and the AI must turn it into a CanonicalValidationPlan + YAML + SQL. Covers row-restriction filters, multi-table JOIN comparisons, transformation-rule verification (e.g. currency conversion), batch/scope filters, and row-hash PK comparison. Files: CanonicalValidationPlan, ai_sql_generator.py, yaml_config_writer.py."
---

# AI-Driven Condition Validation (filters, joins, transformations)

Core capability: test lead states condition in plain English. AI parses it into a structured spec, plan generator turns spec into `CanonicalValidationPlan` fields, SQL/YAML generators render it. Same pipeline handles all cases below — don't build a separate path per case.

## Natural-language condition → spec (examples test leads actually give)

| Test lead says | Parsed as |
|---|---|
| "customer.status = 'ACTIVE' and has account in accounts, created after Jan 1" | filter (status=ACTIVE) + filter (created_at > date) + `EXISTS` join to accounts |
| "orders for customers in region WEST, include customer and account info" | filter (region=WEST) + join orders→customers→accounts (select columns from all) |
| "verify order_amount transformed USD→INR by expected rate" | transformation-rule check: `target.order_amount = source.order_amount * rate` (rate from rule, not literal) |
| "only employees in departments migrated in this batch" | filter: department_id IN (batch's migrated department set) — batch scope filter |
| "A joins B via A.customer_id = B.customer_id, compare transformed result in Snowflake" | explicit join spec, target = transformed comparison |

AI's job: extract (a) filter predicates, (b) join specs (tables + keys + type), (c) transformation rule to verify (reuse `rule_book.py` rules where one already exists — don't invent a new rate/formula silently), (d) batch/scope constraint. Ask the user for the missing piece instead of guessing when a condition is ambiguous (e.g. "recent" with no date).

Two structural cases this maps to underneath:

## 1. Reference-filter join (row restriction, not a comparison join)

Goal: on a huge table (e.g. 200M rows), only validate rows whose key also exists in another table — reduce scan cost instead of comparing the whole table.

Shape it as a `WHERE EXISTS` / `IN` predicate pushed into both the source query and the target query, expressed once on the `CanonicalValidationPlan` and rendered per-dialect:

```
source_filter = "EXISTS (SELECT 1 FROM <ref_table> r WHERE r.<ref_key> = <table>.<key>)"
target_filter = "EXISTS (SELECT 1 FROM <ref_table_sf> r WHERE r.<ref_key> = <table>.<key>)"
```

Rules:
- The filter must be **symmetric**: the same logical rows must be excluded on both sides, or the row counts will never match even when data is correct.
- Prefer `EXISTS`/semi-join over `IN (SELECT ...)` for large reference sets — most dialects optimize `EXISTS` better and it avoids duplicate expansion when the reference table has repeated keys.
- Do not fetch the reference key list into Python and inline it as a literal list — this doesn't scale past a few thousand keys and defeats the purpose of a 200M-row optimization.
- Persist the filter (reference table, reference key, target key) in the generated YAML, not just the final rendered SQL string, so it can be regenerated if the reference table/key changes.

## 2. Multi-table JOIN as the comparison itself

Goal: the "table" being validated on one side is actually the result of joining 2+ source tables (e.g. `orders JOIN order_items`), compared against one target table/view.

This is a bigger change than a filter — it means the plan's source side is no longer a single table:
- Extend `CanonicalValidationPlan` with an explicit `join_sources: List[JoinSpec]` (or similar), where `JoinSpec` = `{table, join_type, on_condition}`. Keep this optional/empty for existing single-table plans.
- Column mapping entries (`ColumnMappingEntry.source_column`) must stay qualified (`table.column`) once more than one source table is present, to avoid ambiguous references in generated SQL.
- Reuse the existing CTE pattern in `ai_sql_generator.py` (`_build_snowflake_cte_query`, `snowflake_cte_join_clause`) as the template for building the multi-table FROM/JOIN clause — don't invent a second query-building path.
- Validate join cardinality risk explicitly: a join that fans out rows (1:N) will inflate row counts and produce false mismatches. Flag this to the user and prefer aggregating (e.g. pre-aggregate the N-side) before comparison, or document that count validation must use a distinct source-row count, not `COUNT(*)` after the join.

## Row-hash comparison — what actually exists

- `plan.row_hash` makes `yaml_config_writer.py` emit a `row_hash_validation:` block (SQL from `sql_query_generator.py::_row_hash_queries`/`_hash_expression`). That block is **only** an input to the opt-in `hybrid_v1` Tier-1 engine; `Project/main.py` never runs it as a validation (ADR 0035). Row-hash is not a replacement for `data_validation` on standard tables.
- Hash hex must be lowercase on every dialect so it matches Snowflake: MSSQL/Athena are wrapped in `LOWER`/`lower` (ADR 0036). Postgres uses `MD5` on both sides (no `pgcrypto`).
- `hybrid_v1` supports single-column PK or PK-less only (composite rejected by `PlanValidator`). PK-less is decided from `validation_plan.identity.source_primary_keys: []` (ADR 0037).
- Separate mechanism: for PK-less standard tables, `main.py` computes its own Python `row_hash` fallback key inside `data_validation`.

## Where each capability plugs in

| Layer | File | Change |
|---|---|---|
| Plan (source of truth) | [src/core/validation_plan.py](../../../src/core/validation_plan.py) | Add optional `reference_filter`, `join_sources`, `transformation_check`, `row_hash_key` fields |
| AI parsing | `src/ai/prompt_builder.py`, `src/ai/rule_planner.py` | Parse test-lead condition into filter/join/transformation/batch-scope spec; reuse existing rules from `rule_book.py` before inventing a new transformation |
| SQL generation | [src/generated_queries/ai_sql_generator.py](../../../src/generated_queries/ai_sql_generator.py) | Render filter into WHERE; render joins into FROM/JOIN using the existing CTE pattern; render row-hash expression when PK known |
| YAML persistence | [src/generated_queries/yaml_config_writer.py](../../../src/generated_queries/yaml_config_writer.py) | Serialize filter/join/transformation/row-hash spec so re-running from YAML reproduces the same SQL |
| UI entry point | `webapp/views/generate_yamls.py` (step 6 filters, JOIN rules, workbook scope filters) | Free-text box for the test-lead condition, calling the backend parse+generate function — do not build SQL in the UI |

## Verification checklist
- [ ] Filter/join condition is identical in intent on both source and target dialects
- [ ] Large-table filter is pushed into SQL (EXISTS/JOIN), not evaluated in Python
- [ ] YAML regenerated from a saved plan produces byte-identical SQL to the original run
- [ ] Existing single-table plans (no filter/join) still generate unchanged SQL
- [ ] If a join can fan out rows, count validation logic is confirmed correct (pre-aggregated or explicitly documented)
- [ ] Transformation check reuses an existing rule from `rule_book.py` when one matches, instead of a new hardcoded formula
- [ ] Any hash-expression change keeps both sides the same algorithm and lowercase hex
- [ ] Filters are baked into SQL at generation time (`source_filter`/`target_filter`); per-run date ranges are *not* — those are the incremental env-var contract (ADR 0034), see `data-comparison-report`
