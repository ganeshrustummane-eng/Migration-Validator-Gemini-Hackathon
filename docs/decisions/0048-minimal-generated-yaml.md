# 0048. Generated YAML keeps only what the engine reads; files are named after the target table

**Status:** Accepted — implemented
**Date:** 2026-09-29
**Changes:** [0026](0026-validation-plan-metadata-block-in-silver-yaml.md) (the full `validation_plan` block is no longer in the YAML), [0047](0047-silver-configs-named-int-table.md) (Bronze now uses the target table name too)

## Context

Reviewers found the generated YAML confusing. Besides the two queries, each
file carried:

- a 25-line header (normalisation notes that don't even apply to Silver);
- a full `validation_plan` block: `population_scope`, `natural_key_candidates`,
  `scope_filter`, `scope_joins`, `relationships`, `review_reasons`,
  `requires_review`;
- `transformation_validation` and `aggregate_validation` blocks that were
  always `SELECT 1;` placeholders.

The fields the user asked to keep are the 14 `data_validation` fields: table
name, type, database, schema, key column, audit column and query, once for
the source and once for the target.

What the engine actually reads was checked in `Project/main.py`,
`Project/tiered_runner.py`, `Project/utils/utility.py`, `webapp/app.py` and
`src/validation/config_schema.py`:

- `data_validation`: the 14 fields.
- `validation_plan`:
  - `execution_strategy`, `identity` and `row_hash` for hybrid_v1
    (ADR 0035/0037);
  - `row_hash.columns` for the PK-less fallback;
  - `transformations` for hybrid;
  - `incremental`, which is hand-added and never generated (ADR 0034).
- Nothing reads `population_scope`, `relationships`, `review_reasons` or
  `requires_review` from the YAML. The UI reads them from the plan object.
  The full plan is still saved as `output/plans/<layer>/<table>.plan.json`
  (the contract, written before the YAML).
- `transformation_validation` / `aggregate_validation` come only from
  `plan.transformations` / AGGREGATE validations. Their only producer,
  `core/requirement_planner.py`, has no live caller, so both blocks were
  always placeholders, which `main.py` skips.

## Decision

`yaml_config_writer` now writes:

- A 3-line header: layer, config name, source → target, column count, when
  and by what it was generated, and where the full plan lives.
- `data_validation` with exactly the 14 fields.
  - `sourcecolumn`/`targetcolumn` stay a string for one key and a list for a
    composite key. `main.py` already handles both.
- `row_hash_validation`, kept as the user asked. It holds `SELECT 1;` until
  the plan has a row_hash spec. It is only used by hybrid_v1 (ADR 0035).
- `validation_plan` **only when the engine needs it** (`_engine_plan_block()`):
  - `execution_strategy` + `identity` when hybrid_v1;
  - `row_hash` when set;
  - `transformations` when set.

  For a normal table the block is absent.
- No `transformation_validation` or `aggregate_validation` blocks. An
  aggregate check can be written as the data query itself.

**File name = target table name** (`config_table_name()`), for both layers.
The same name is used for the `tables:` key and the count_validation key,
because the runner looks up file stem → `tables:` key:

- Bronze: `UNITS.yaml`, not `units.yaml`.
- Silver: always `INT_<table>`.

Windows file names ignore case. Writing `UNITS.yaml` over an old
`units.yaml` would keep the old lower-case name and break that lookup, so
the writer deletes the old file first.

Fixed along the way: `config_schema.DataValidationBlock` declared
`sourcecolumn`/`targetcolumn` twice. The second declaration (`str` only) won,
so any **composite-PK** YAML failed `validate_cli` even though the engine
supports it.

## Alternatives considered

- **Keep `validation_plan` but hide it with comments.** Still noise; nothing
  reads it.
- **Drop `row_hash_validation` when it's a placeholder.** The user asked to
  keep it, and it's harmless (skipped by the engine).

## Consequences

- Review notes (macro-skipped columns, missing `IS_CURRENT`) and the
  workbook filter details are no longer in the YAML. They're shown in the
  UI while generating and kept in the plan JSON.
- Existing YAMLs keep their old names and extra fields until regenerated:
  `units.yaml`, `facilities.yaml`, `DISCOUNT_LINES.yaml`, `CONTACTS.yaml`,
  and the old count keys. Regenerate them and delete the old
  files/count-keys, or the Run tab lists both.
- Not changed: the webapp's own YAML writers (JOIN-rules path, prompt tab,
  custom editor) and `excel_batch_loader.write_yaml()`. They are separate
  paths, as CLAUDE.md notes.
- Test: `test_generated_yaml_is_minimal_and_still_valid` checks the exact
  field list, the composite key, the hybrid-only `validation_plan`, and that
  the file passes the schema validator.
