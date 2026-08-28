# Change Record

## Version 3.2 — Plan Contract, AI-Only Generation, Loud Exclusions

### Breaking changes

- **`DIAL_API_KEY` is now required.** Column mapping and validation SQL are
  AI-only. Without a key, generation raises instead of producing rule-based
  output.
- **Removed `StaticRuleMapper`** and the module `ai_transformation.static_rule_mapper`.
  `ColumnRuleMapping` moved to `ai_transformation.column_mapping`
  (re-exported from `ai_transformation`, so `from ai_transformation import
  ColumnRuleMapping` still works).
- **`SQLQueryGenerator(use_ai=False)` now raises.** No non-AI path exists.
- Removed `AISQLQueryGenerator._fallback_query`.

  *Rationale:* static output was indistinguishable downstream from reviewed AI
  output, so a missing API key silently downgraded correctness without
  downgrading reported confidence. Runs looked green while comparing guesses.

### The plan is now the contract

- Added `core.PlanStore` — atomic save/load of `CanonicalValidationPlan` JSON to
  `output/plans/<layer>/<table>.plan.json`.
- `CanonicalValidationPlan.to_dict()` / `.from_dict()` now round-trip losslessly;
  added `PLAN_SCHEMA_VERSION` with forward-compatibility rejection.
- The plan is persisted **before** SQL/YAML rendering, so a failed render still
  leaves a durable record of intent.
- YAML is a render target and is never read back to reconstruct intent.

### Exclusions are always visible

- Added `core.ExclusionReport` and `core.BatchExclusionReport`.
- Every generation and every batch prints coverage next to the result:
  `6 of 9 columns validated (66.7%) — 3 excluded: uTS (binary type), ...`
- Runs below 80% column coverage are flagged `LOW COVERAGE`.
- Tables with no plan report coverage as UNKNOWN, never as complete.
- Added `CanonicalValidationPlan.exclusion_summary()`; `summary_lines()` now
  names every excluded column and its reason.

### YAML correctness

- **Fixed duplicate-table bug:** `write_count_yaml` appended raw text, so
  regenerating a table added a second top-level key that YAML silently resolved
  last-wins. It now parses, upserts, and rewrites the document.
- Repeated generation of an unchanged table is byte-identical (removed the
  volatile timestamp from the shared count file header).
- `_strip_generator_header` now removes all leading comment lines, not just one.

### Config validation at load

- Added `src/validation/config_schema.py` (Pydantic): required fields, SELECT-only
  queries, known dialects, `.env` credential references, duplicate table keys.
- `ValidationExecutor` validates every config before opening any connection and
  reports a named `ERROR` instead of a mid-run `KeyError`.

### New: `validate_cli.py lint`

- Offline structural check of all generated configs plus plan/config drift.
- Exit code 0/1; safe for CI. Requires no database.

### Other

- AI SQL generation gained a self-correction loop: dialect-check failures are fed
  back to the model for up to 3 attempts before raising.
- Target-side SQL is now AI-generated with enforced alias alignment, removing the
  source/target alias-case mismatch.
- Added `pydantic>=2.0` to requirements.
- Fixed `UnicodeEncodeError` on Windows cp1252 consoles (even `--help` crashed).
- Rebuilt `config/database_registry.yaml` with documented schema.
- Added the first real pytest suite (50 tests) — previously zero `test_*.py`
  files existed, so the `regression_tests()` stage always reported success.

---

# Version 3.1 Change Record

## Framework and Execution

- Integrated PostgreSQL, MSSQL, Snowflake, and Athena adapters.
- Added `.env`-based credential loading.
- Added database-type profile inference when YAML omits `source_name`.
- Standardized runtime reports under repository `output/`.
- Added non-secret database/schema fallback metadata.

## Validation

- Added count validation.
- Added row-level data validation.
- Added case-insensitive and normalized primary-key resolution.
- Added mismatch CSV output.
- Added summary CSV output and execution logs.
- Added JSON/HStore canonical comparison.
- Added order-independent row comparison.

## SQL and AI

- Added source-dialect-aware SQL generation.
- Added MSSQL SQL safety checks.
- Added source-to-target type/castability context to AI prompts.
- Added AI response validation and deterministic fallback. *(fallback removed in 3.2)*
- Fixed aggregate SELECT comma generation.
- Implemented real grouped VALUE_DIST queries.
- Preserved Snowflake Fivetran active-row filtering.

## Exclusions

- Added automatic ETL/Fivetran exclusions.
- Added YAML-based exclusion rules.
- Added interactive single-table exclusions.
- Added per-table exclusions for multi-table workflows.
- Excluded columns are removed before mapping and SQL generation.

## Testing

- Added `tests/e2e/run_all_tests.py`.
- Added non-live and live test modes.
- Added dependency, import, YAML, dialect, regression, connection, validation, and output stages.
- Added JSON test reports under `output/test_runs/`.
