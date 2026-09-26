# 0026. Silver YAML carries the full CanonicalValidationPlan, not just source/target SQL

**Status:** Accepted
**Date:** 2026-09-25

## Context

Generated Silver YAMLs (e.g. `Project/config/silver/data_validation/snowflake/LEADS.yaml`)
contain more than the `data_validation` source/target queries: a `validation_plan`
block (`population_scope.natural_key_candidates`, `identity.primary_keys` /
`candidate_keys`, `relationships`, `row_hash`, `requires_review` /
`review_reasons`, `execution_strategy`), plus `transformation_validation`,
`aggregate_validation`, and `row_hash_validation` blocks that are often just
`SELECT 1;` stubs. This looks like scope creep nobody asked for, and needs to
be explainable to a reviewer/manager who didn't request it.

It is not new scope invented for Silver. `src/core/validation_plan.py` states
the `CanonicalValidationPlan` is "the SINGLE SOURCE OF TRUTH for validation" —
"both the SQL generator and the YAML generator consume this plan... This
guarantees SQL and YAML are always in sync." This has been the Bronze
architecture since before Silver existed (see ADR 0003); `Project/config/bronze/data_validation/postgres/orders.yaml`
and `customers.yaml` already carry the same `validation_plan` block. Silver's
`coalesce_plan_builder.py` (ADR 0013, 0014) reuses the same plan object and
the same `yaml_config_writer.py` serialization path — it didn't add a new
mechanism, it fed a new source (Coalesce node metadata) into the existing one.

## Decision

Keep serializing the full `CanonicalValidationPlan` into every generated
YAML, Bronze and Silver alike, because each field is either consumed at
validation runtime or exists to make a row-comparison decision auditable:

- `population_scope.natural_key_candidates` / `identity` — records which
  key `Project/main.py` actually keyed the row-level PASS/FAIL/SOURCE_ONLY/
  TARGET_ONLY comparison on. For Silver, the real key is often a
  macro-computed surrogate (e.g. `LEAD_KEY`) that can't be resolved without a
  macro engine (ADR 0014 §3, ADR 0022) — `requires_review`/`review_reasons`
  is how that gap survives into the YAML instead of silently picking a wrong
  key.
- `row_hash` — read by `Project/main.py` (`validation_plan.row_hash`) to
  build a Python-computed row hash fallback when no usable PK exists, and by
  `Project/tiered_runner.py` for the opt-in hybrid_v1 large-table strategy.
- `relationships` / `transformations` — read by `Project/main.py` for
  join-based population scope and transformation-rule checks (currency
  conversion, etc.) when a table's mapping needs them; empty otherwise.
- `execution_strategy` — the switch `Project/main.py` reads to decide
  standard vs. hybrid_v1 dispatch (ADR 0010, 0011).
- `transformation_validation` / `aggregate_validation` / `row_hash_validation`
  blocks default to `SELECT 1;` placeholders and are explicitly skipped at
  runtime (`Project/main.py:221-223`, matched literally against `"SELECT 1;"`)
  — they only carry real SQL when a table's plan actually configures that
  check. They are reserved slots in an existing, already-parsed schema, not
  new speculative machinery.

Nothing here is Silver-specific ceremony — it is the pre-existing Bronze
contract, unchanged, now also serving Silver.

## Alternatives considered

- **Move this metadata to a separate file/store per table**, keeping the
  YAML to just the two SQL queries. Rejected: it would fork the "SQL and
  YAML always in sync" guarantee `validation_plan.py` was built for — a
  second file can drift from the query it describes, and every consumer
  (`Project/main.py`, `plan_validator.py`, `tiered_runner.py`) already reads
  this data off the same YAML the runner already opens; a second file is a
  second thing to load, path, and keep in sync for no behavior change.
- **Strip the block entirely and re-derive key/hash config at runtime.**
  Rejected: `requires_review`/`review_reasons` exists specifically because
  some of this (the Silver surrogate-key case) *can't* be re-derived
  automatically today — it's a human-in-the-loop flag, not cached
  convenience.

## Consequences

- Generated YAMLs are longer than a naive "two SQL queries" file would
  suggest, but every extra field is either read by `Project/main.py` at
  validation time or documents *why* a human still needs to look at this
  table (`requires_review`) — nothing in the block is decorative.
- Anyone reviewing a Silver YAML for the first time should expect this shape
  — it is identical in structure to Bronze's, not a Silver-only extension.
- If a future change removes a consumer of one of these fields (e.g. drops
  hybrid_v1), that field becomes genuinely dead and should be pruned from
  `yaml_config_writer.py`'s output at that time, not before.
