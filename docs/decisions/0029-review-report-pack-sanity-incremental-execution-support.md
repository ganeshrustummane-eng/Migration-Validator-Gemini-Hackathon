# 0029. Reviewing a colleague's report-pack / sanity / incremental execution work for possible adoption

**Status:** Proposed
**Date:** 2026-09-26

## Context

A colleague spent ~3 months extending validation *execution* (not YAML
generation — that part is considered done) with a parallel `Project/main.py`
+ `Project/utils/utility.py` pair, developed outside this checkout and not
yet merged. Comparing it line-by-line against the live
`Project/main.py`/`Project/utils/utility.py` (verified by reading both, not
assumed from the diff):

**Three capabilities exist in their version that do not exist live:**

1. **Report-pack execution** — `--report_pack` CLI arg, and
   `get_config_output_paths()` builds
   `config/<layer>/<report_pack>/<validation>/...` paths. This is the missing
   *execution* half of something that already exists on the *generation*
   side: `src/validate_cli.py` already writes YAMLs to
   `config/report/<pack>/data_validation/`. The live `Project/main.py`'s
   `--layer_type` choices (`bronze`, `silver`, `gold`, `reporting`) accept
   `reporting` as a value but nothing in `main.py` or `utility.py`
   special-cases it — there's a CLI choice with no behavior behind it today.
2. **`sanity` layer** — single-source integrity/orphan-key checks (a query
   against one source only, FAIL if it returns any rows, tracked with
   `test_case`/`summary` metadata). No equivalent exists live at all.
3. **Historical vs. incremental runs** — `--run_type` (`historical` default,
   `incremental` requires `--from_date`/`--to_date`) appends
   `WHERE created_date between {from_date} and {to_date}` to both source and
   target queries. No equivalent exists live.

**What their version does NOT have, that the live engine does:**
`Project/utils/semantic_normalize.py` (type canonicalization),
`Project/utils/quality_checks.py` (audit trail, expected-grain validation),
`Project/utils/row_compare.py` (indexed frame comparison used for the
hybrid Tier-1/Tier-2 strategy), and the `should_dispatch_hybrid` /
`count_validation_match` helpers. Their comparison is a plain
`source_df.equals(target_df)` after `.astype(str)` — no semantic
normalization, no configurable count-mismatch threshold. It's an earlier,
independently-evolved fork of the same problem, not a superset.

**Concretely hardcoded / static, matching the "prefix and prefix" note:**

- Environment → schema resolution is two separate `if/elif` chains inline in
  `main.py` (`query.format(env="DEV")` for `sanity`; `target_query.format(env=
  "STG", schema_env="STAGING")` for the value-comparison branch), one
  chain per query, both duplicating logic. The live engine already solves
  this **generically**: `Project/db/factory.py::_ENV_FILE_BY_ENVIRONMENT`
  maps an environment name to a `.env.<environment>` file, and
  `SNOWFLAKE_DATABASE`/`SNOWFLAKE_SCHEMA` come from that file per connector
  — no environment name is ever string-substituted into SQL text. Their
  version reinvents this statically instead of reusing what's already
  dynamic.
- The incremental date column (`created_date`) is a hardcoded literal in
  `main.py`, not read from the YAML — breaks silently for any table whose
  real date column has a different name.
- `--report_pack` choices are a hardcoded argparse list
  (`emanagement, smanagement, egrowth, sgrowth, eperformance, sperformance`)
  — if `src/validate_cli.py` has its own list of the same packs (it does, for
  YAML generation), this is a second copy that can drift.

## Decision (proposed — not yet implemented)

Recommend a **selective port**, not a wholesale merge and not leaving the
fork as-is:

1. Port the three new *capabilities* (report-pack execution, `sanity` layer,
   historical/incremental date filtering) into the live
   `Project/main.py`/`Project/utils/utility.py` — they fill real gaps.
2. Do NOT port their comparison logic, environment handling, or
   `create_summary`/`get_config_output_paths` implementations verbatim — run
   the ported capabilities through the live engine's existing
   `semantic_normalize`/`quality_checks`/`row_compare` path, and resolve
   environment/schema the way `factory.py` already does (per-environment
   `.env` file → `SNOWFLAKE_DATABASE`/`SNOWFLAKE_SCHEMA`), not via inline
   `.format(env=...)` string substitution.
3. Make the incremental filter column and the report-pack list
   YAML/config-driven instead of hardcoded in Python — see open questions
   below for exactly how.

## Alternatives considered

- **Merge their `main.py`/`utility.py` wholesale, replacing the live
  files.** Rejected — it would drop semantic normalization, quality checks,
  the hybrid Tier-1/Tier-2 strategy, and the count-mismatch threshold, all of
  which the live engine has and theirs doesn't. That's a regression, not an
  upgrade.
- **Leave the fork as a separate script for report_pack/sanity/incremental
  runs only.** Rejected — CLAUDE.md already establishes "no redundant
  implementations" / one live engine as a ground rule; a second `main.py`
  with its own comparison logic is exactly the kind of drift that rule
  exists to prevent, and it would silently diverge further the next time
  either one changes.
- **Selective port (recommended).** Keeps one engine, adds the three
  missing capabilities, and fixes the hardcoding along the way instead of
  importing it.

## Open questions (for you to decide before anyone implements)

1. **Incremental date column** — should it be a new per-table YAML key
   (e.g. `incremental_filter_column: created_date`, defaulting to `None` =
   historical-only), or a fixed convention enforced at YAML-generation time?
   Either avoids the hardcoded literal; they have different maintenance
   costs.
2. **`sanity` layer's relationship to `quality_checks.py`** — their
   integrity/orphan-key check overlaps conceptually with
   `Project/utils/quality_checks.py::run_quality_checks` /
   `validate_expected_grain`. Should `sanity` become a new mode of the
   existing quality-checks module (reusing its audit trail) rather than a
   fourth `--layer_type` value with its own code path?
3. **Report-pack list source of truth** — should `Project/main.py` read the
   same pack list `src/validate_cli.py` uses (wherever that's centralized),
   or should both read from one shared constant/config file?
4. **Excel diff report** — their per-column-sheet mismatch workbook is more
   detailed than the live engine's failed-only CSV. Is that granularity
   wanted for report-pack runs specifically, or as a general upgrade to
   every layer's output? This changes whether it's scoped to the
   report-pack port or is a separate, bigger decision.

## Consequences

- Nothing changes until these open questions are answered — this ADR is a
  review artifact, not a merge.
- Once answered, the implementation work should go through the
  `validation-query-yaml-generator` / `data-comparison-report` skill
  boundaries already documented in `CLAUDE.md`, not as a new standalone
  script.
