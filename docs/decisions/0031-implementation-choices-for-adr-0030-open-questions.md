# 0031. Implementation choices made for ADR 0030's open questions, and what's deferred to production-hardening

**Status:** Accepted
**Date:** 2026-09-26

## Context

[0030](0030-progressive-decision-report-pack-incremental-sanity-streamlit.md)
left 4 open questions for the repo owner to answer before implementation.
The owner asked to proceed with implementation now, using best judgment for
those questions, on the condition that the judgment calls and what's
deliberately left less dynamic for now are tracked here — so a future pass
can revisit them for production hardening instead of the reasoning being
lost.

## Decision — what was implemented

All four phases from ADR 0030 except Phase 5 (Excel diff, still deferred)
and the pack-level rollup nice-to-have (included, cheaply):

- `src/validation/config_schema.py`: `IntegrityCheckBlock` (source-only,
  `extra="forbid"`) + `TableValidations.integrity_check`.
- `Project/utils/quality_checks.py::run_integrity_check()`.
- `Project/utils/incremental_filter.py::apply_incremental_predicate()`.
- `Project/main.py`: two new branches — `validation_name == "integrity_check"`
  (routes to `run_integrity_check`, never enters the source/target compare
  path) and `_incremental_block.get("enabled")` (applies the predicate to
  `source_query`/`target_query` before execution, in the standard
  non-hybrid path only). No new argparse flags.
- `Project/runner.py::collect_validation_result()`: generalized the
  `*_summary.csv` discovery so `integrity_check_summary.csv` (which lands
  inside whichever `data_validation`/`count_validation` output folder its
  table's YAML came from, not its own subdirectory) is picked up without
  hardcoding a third path template.
- `webapp/app.py` (`tab_execute` only): Execution Mode radio
  (Historical/Incremental), per-table filter-column resolution + explicit
  exclusion (not silent historical fallback) for tables missing
  `validation_plan.incremental`, date-range injection into the YAML
  immediately before running (mirrors the existing `mismatch_threshold_pct`
  pattern), a dynamically-discovered Report Pack selector (`config/report/`
  subdirectories, never a hardcoded list), and a pack-level pass/fail
  rollup line.
- Tests: `src/validation/test_config_schema.py`,
  `Project/utils/test_incremental_filter.py`, two additions to
  `Project/utils/test_quality_checks.py`. Full existing suite (100 tests
  across `Project/utils/`, `src/validation/`, `Project/test_tiered_runner.py`)
  passes unchanged.

## Judgment calls made on the open questions

**Q1 (author via Streamlit form, or hand-edit YAML for v1?)** — **Hand-edit
YAML for now.** Implemented the *runtime* side (Run Validation tab reads
`validation_plan.incremental`/`integrity_check` and injects dates), but did
NOT build a generation-side form for authoring these blocks from scratch.
**Why:** the generation tabs (`webapp-yaml-generation` skill's territory)
are a materially larger, separate surface, and ADR 0030 flagged this
explicitly as answerable later without blocking the run-side work. **Deferred
— production hardening:** if operators end up hand-editing YAML often
enough to be a real workflow, add a small form to the Batch/Single YAML
generation tabs for `incremental.filter_column` and an `integrity_check`
query, instead of continuing to require manual edits.

**Q2 (`source_database`/`source_schema` overrides on `IntegrityCheckBlock`
now, or later?)** — **Added now.** Cheap, and matches every other block
type's shape (`CountValidationBlock`/`DataValidationBlock` both have them),
so withholding them would just mean a near-identical follow-up schema PR
the first time a real integrity query needs a non-default source
database/schema. `source_name` (the separate `SRC_1`-style credential
reference field the other blocks have) was NOT added — it's unused in
every real generated YAML sampled during ADR 0030's research, so adding it
would be speculative. **Deferred:** add `source_name` to
`IntegrityCheckBlock` if a real integrity check ever needs a non-default
credential reference; not before.

**Q3 (date-only vs. datetime vs. timezone-aware incremental inputs?)** —
**Date-only** (`st.date_input`), matching the colleague's original fork's
precision and the simplest correct-enough option. Marked with a `ponytail:`
comment in `webapp/app.py` at the radio/date-input block, since this is a
deliberate corner cut with a known ceiling, not an oversight: a same-day
boundary (e.g. a table's real `updated_at` column has intra-day timestamps
and the operator needs a specific hour, not just a day) isn't representable
today. **Deferred — production hardening:** upgrade to
`st.date_input` + a time component (or a single datetime text input) if a
real incremental table's boundary needs finer than day precision, and check
whether the connectors' date-string-to-column comparison needs timezone
normalization at that point (`Project/utils/semantic_normalize.py` may
already have relevant conversion logic to reuse — check there first rather
than adding new timezone code).

**Q4 (pack-level rollup: required or nice-to-have?)** — **Implemented**,
since Discrepancy #1 in ADR 0030 already showed report-pack execution
mostly works without any new code, so the marginal cost of also adding the
one-line rollup (`st.info(f"📦 Report pack {pack}: {passed}/{total} passed")`)
was small enough not to defer.

## What's explicitly still deferred (beyond the four questions above)

- **Excel diff report** (ADR 0030 Decision 5) — untouched, not started.
- **Regex-based top-level `WHERE` detection** in
  `apply_incremental_predicate()` — ADR 0030's Risks section flagged this
  against a custom-SQL YAML with a subquery containing its own `WHERE`; not
  tested against one yet because no such YAML with `incremental` enabled
  exists in this repo today. Test against a real one before enabling
  incremental filtering on any custom-SQL-generated table.
- **Incremental filtering is not wired into the hybrid Tier-1/Tier-2 path**
  (`Project/tiered_runner.py`) — only the standard non-hybrid execution
  branch in `Project/main.py` applies the predicate. A table with both
  `execution_strategy: hybrid_v1` and `validation_plan.incremental.enabled: true`
  will run the hybrid path unfiltered today. Revisit if/when a table needs
  both simultaneously.
- **Concurrent-run YAML mutation risk** — inherited, not introduced or
  fixed, from the existing `mismatch_threshold_pct` pattern (see ADR 0030
  Risks). Two operators running the same incremental table at once could
  race on the same YAML file. Not blocking; worth its own ADR if it ever
  causes a real incident.

## Consequences

Nothing here changes ADR 0030's architecture — this is a record of the
specific, previously-open choices made while implementing it, so a future
production-hardening pass has the reasoning instead of having to
re-derive it (or worse, assume today's date-only picker or missing
`source_name` field was an oversight rather than a deliberate, documented
simplification).
