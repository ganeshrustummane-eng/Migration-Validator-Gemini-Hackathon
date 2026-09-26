# 0027. Silver SQL gets the blanket `COALESCE(CAST(col AS STRING), '<<NULL>>')` wrapper back, reversing ADR 0018 on this point

**Status:** Accepted
**Date:** 2026-09-25
**Supersedes:** [0018](0018-silver-sql-verbatim-transform-no-generic-normalization.md) (only its "no blanket NULL wrapper, ever" clause — everything else in 0018 stands, see below)

## Context

ADR 0018 deliberately removed any NULL-placeholder wrapping from Silver's
generated SQL (`src/silver/silver_sql_emitter.py`), reasoning that
`Project/utils/row_compare.py`'s `_cell_str()` already maps Python
`None`/`NaN` to the `"<<NULL>>"` sentinel *after* the query runs, so the
row-level comparison doesn't need the SQL itself to do it — the same
correctness outcome either way.

That reasoning is still true for correctness. What it didn't weigh: the
generated Silver YAML's SQL is also a human-readable artifact — a DQE
reviewer or the user diffing it against the Coalesce node's declared
transforms (ADR 0013's whole trust model) has no visibility into what
`row_compare.py` does in Python. A column that is NULL in Bronze and NULL in
Silver reads identically to a column that is NULL in Bronze and `''` in
Silver when staring at raw SQL output — the Python-side normalization that
makes both cases compare correctly is invisible at the point someone is
actually eyeballing the query. The user asked for NULL handling to be
visible directly in the SQL text, not just enforced downstream in Python,
and confirmed (when given the choice between a narrow execution-error-only
fix and the full Bronze-style blanket wrapper) that they want the latter —
full parity with how Bronze's `base_rules.py` wraps every column.

## Decision

`silver_sql_emitter.py` now wraps every selected column, on both the Bronze
recompute side and the plain Silver side, in the same wrapper
`src/rules/base_rules.py` already uses for a Snowflake target:

```
COALESCE(CAST(<expr> AS STRING), '<<NULL>>')
```

Applied in `_bronze_select_lines()` and `_silver_select_lines()`:

- **Passthrough / recomputable-expression columns** (Bronze side): the
  verbatim column reference or transform expression from the plan mapping
  is wrapped — `COALESCE(CAST("LEADS"."ID" AS STRING), '<<NULL>>') AS
  "LEAD_ID"` instead of the bare `"LEADS"."ID" AS "LEAD_ID"`.
- **Silver side**: `COALESCE(CAST("LEAD_ID" AS STRING), '<<NULL>>') AS
  "LEAD_ID"` instead of the bare `"LEAD_ID"`.
- **Macro-skip / non-deterministic columns**: unchanged — still omitted from
  the SELECT (ADR 0018 §1's macro-skip comment behavior and ADR 0014 §3's
  non-deterministic existence-only check are untouched by this ADR).

Everything else in ADR 0018 stands: Silver still doesn't route through
`sql_query_generator.py` → `ai_sql_generator.py` → `base_rules.py`'s rule
*engine* (no per-type-pair rule dispatch, no dialect switching — Silver is
always Snowflake-to-Snowflake, so it only ever needs the one Snowflake
wrapper string, applied directly in `silver_sql_emitter.py` itself rather
than by importing `base_rules.py`). The Coalesce-declared transform string
itself is still emitted exactly as written, just wrapped rather than bare.
ORDER BY is still skipped (ADR 0018's resolved note stands).

## Alternatives considered

- **Keep 0018's "no wrapper, ever" and only add the wrapper where it
  prevents a SQL execution error** (the narrower option offered to the
  user). Rejected by the user's explicit choice — they want the same visual
  NULL-safety Bronze SQL has, not just error-avoidance.
- **Import and call `base_rules.py`'s `SnowflakeRule._coalesce_sf()`
  directly** instead of inlining the wrapper string in
  `silver_sql_emitter.py`. Rejected: that reintroduces exactly the coupling
  ADR 0018 removed (Silver depending on Bronze's rule-engine module for
  something that, for Silver, is a single fixed string with no dialect
  branching) — one f-string constant in `silver_sql_emitter.py` is simpler
  and doesn't risk pulling in the rest of `base_rules.py`'s per-type-pair
  dispatch that Silver doesn't need.

## Consequences

- Gets easier: Silver's generated SQL now visually matches what a reviewer
  expects from seeing Bronze SQL — no separate mental model needed for "why
  does Bronze wrap NULLs but Silver doesn't."
- Gets harder / watch for: Silver SQL is no longer a byte-for-byte verbatim
  reflection of the Coalesce transform string — the wrapper is now our
  addition around it. Anyone diffing generated SQL against Coalesce's raw
  `transform` field needs to mentally strip the wrapper first (or diff
  against the pre-0027 SQL) — this is the exact tradeoff 0018 chose to avoid
  and 0027 reintroduces deliberately, on the user's explicit call.
- `test_coalesce_plan_builder.py`'s literal generated-SQL-text assertions
  (added per ADR 0018's "Consequences" note) must be updated to expect the
  wrapped form, or they will fail after this change — implementing agent
  must update them in the same pass, not leave them red.
