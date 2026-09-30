# 0049. Several Bronze sources into one Silver table: one SELECT per source, combined like Coalesce does

**Status:** Accepted — implemented
**Date:** 2026-09-29
**Changes:** [0019](0019-silver-multisource-nodes-and-batch-node-ui.md) (adds the UNION shape; the join shape is unchanged)

## Context

Bronze can hold the same kind of data from two source systems (for example
Storedge Postgres and SiteLink MSSQL) that land in **one** Silver table. The
question was how validation handles that.

ADR 0019 assumed that a Coalesce node with several `sourceMapping` entries
**joins** them. It concatenates every entry's `joinCondition` into one
`FROM … JOIN …`. The fixture it was tested with is synthetic: its second
entry begins with `JOIN`.

In Coalesce, a node with several source mappings (`isMultisource`) works
differently. Each mapping is its own complete `SELECT`, with its own `FROM`,
its own joins, and its own column expressions: each column has one
`sources[i]` entry per mapping. The results are then combined as set by the
node's multi-source strategy (`config.insertStrategy`: `INSERT`, `UNION` or
`UNION ALL`). For that shape, the 0019 code produced `FROM A … FROM B`, which
is invalid SQL, and used only `sources[0]` for every column.

## Decision

- **Detecting it** (`coalesce_plan_builder._is_union_node`): more than one
  `sourceMapping`, and every `joinCondition` starts with `FROM`. The 0019
  join-chain shape (only the first one starts with `FROM`) keeps working as
  before.
- **Per branch** (`_union_branch_entries`): column `sources[i]` is classified
  against mapping *i*'s own aliases, with the same four column types as a
  single-source node.
  - A column is compared only if **every** branch has an expression for it.
  - If a branch has no source for the column, or its references point at
    another branch's tables, the column is skipped with a reason. It is never
    guessed.
- **Stored on the plan:** `population_scope["union_branches"]` holds, per
  branch, its name, the resolved `FROM` SQL and a `{column: expression}` map.
  `population_scope["union_strategy"]` holds the SQL set operator.
- **Strategy** (`_union_strategy`): `UNION` / `UNION DISTINCT` → `UNION`;
  `UNION ALL` / `INSERT` → `UNION ALL`. If the value is missing or unknown,
  `UNION ALL` is used and a `review_reasons` entry says so.
- **SQL** (`silver_sql_emitter._union_source`): one `SELECT` per branch.
  - Same column order and aliases in every branch.
  - Each branch has its own `_FIVETRAN_ACTIVE = TRUE` on every table it reads,
    in `QUALIFY` when the branch has a window function (ADR 0045).
  - The branches are joined with the node's operator.
  - The Bronze count is `SELECT COUNT(*) FROM (<the union>)`, which is right
    for both `UNION` and `UNION ALL`.
  - The Silver side is unchanged: one `INT_` table, `IS_CURRENT = TRUE`.
- **Review reason, always:** the key must include a column that tells the
  sources apart (such as a source-system column). Otherwise rows with the
  same ID from two sources are compared against each other.
- **Workbook scope filters:** not applied to UNION nodes yet. Each branch
  starts from a different Bronze table, and the Silver side has one table,
  so one workbook row doesn't map onto it. The UI says so and records
  `scope_filter: {mode: none, reason: multi-source node}`. The emitter
  raises an error if a filter is set anyway.

## Alternatives considered

- **Keep concatenating the join conditions (0019).** Rejected: it produces
  invalid SQL for real multi-source nodes.
- **Compare each source separately against Silver, filtered by a
  source-system column.** Rejected for now: it needs to know which Silver
  column identifies the source and what its values are, which the metadata
  doesn't tell us reliably. It is worth revisiting per node if needed.

## Consequences

- Two Bronze sources feeding one Silver table can now be validated end to
  end.
- **Not yet checked against a real multi-source node payload.** The shape
  comes from how Coalesce multi-source nodes work: `sources[i]` per mapping
  and `config.insertStrategy`. Paste the first real one and confirm it,
  including the strategy key name. The test fixture
  (`UNION_NODE` in `tests/src/test_coalesce_plan_builder.py`) is synthetic.
- Tests: `test_union_node_two_bronze_sources_into_one_silver_table`,
  `test_union_strategy_unknown_is_flagged_not_guessed_silently`.
