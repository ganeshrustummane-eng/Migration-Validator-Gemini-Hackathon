# 0037. PK-less `hybrid_v1` must honor canonical validation-plan identity

**Status:** Accepted — implemented 2026-09-27 (`run_table_hybrid(identity=...)`, passed from `main.py`)
**Date:** 2026-09-27

## Summary

- For `hybrid_v1`, the YAML's `validation_plan.identity.source_primary_keys`
  is the authoritative PK signal. An empty list means PK-less, even when
  `sourcecolumn` / `targetcolumn` carry the legacy first-column fallback.
- Hand-written YAML without an `identity` block keeps today's
  `sourcecolumn`-based check unchanged.
- Normal (non-hybrid) validation, the YAML writer, generated YAML files,
  PK-based hybrid and composite-PK rejection are all unchanged.
- No YAML regeneration is needed.
- This ADR is separate from ADR 0036 (hash casing). Both are needed before
  PK-less `hybrid_v1` is reliable.
- Implemented; see Status.

## Context

### Two representations of "no primary key" in one generated YAML file

For a table with no primary key, the canonical plan carries empty key lists.
The YAML writer serializes them into the table's `validation_plan:` block:

```yaml
validation_plan:
  identity:
    type: primary_key          # default value, even for PK-less
    source_primary_keys: []
    target_primary_keys: []
```

In the same file, `data_validation` carries:

```yaml
sourcecolumn: <first_active_column>_normalized
targetcolumn: <first_active_column>_normalized
```

The second form is a legacy fallback. Git history of
`src/generated_queries/yaml_config_writer.py`:

| Commit | Date | Change |
|---|---|---|
| `cff32c6` | 2026-08-28 | Writer always used the first active column as the PK; PK lists didn't exist. |
| `cb3c859` | 2026-09-01 | Explicit PK lists added. First-column `else` kept, commented *"single-PK tables where list wasn't supplied"*. |
| `cc0c1fe` | 2026-09-04 | `Project/main.py` gained blank PK → `row_hash` (`main.py:476-478`). |
| `47fa96c` | 2026-09-14 | Writer began emitting `validation_plan.identity` (`yaml_config_writer.py:196-211`). |
| `4cd7610` | 2026-09-22 | `row_hash_validation` block and hybrid PK-less support (`is_pk_less`) added, assuming a blank/absent PK. |

The caller the fallback's comment describes, one that omits the PK list, is
`QueryOutputManager.generate()` (`query_output_manager.py:202-213`). It has no
live callers; it appears only in docstring examples. Every live writer call
goes through `write_from_plan()`, which passes `plan.source_primary_keys`
(`yaml_config_writer.py:361`). So today the fallback is reached by genuinely
PK-less plans.

### Hybrid's current PK detection

```python
# Project/tiered_runner.py:574-576
pk_source_col = validation_config.get("sourcecolumn")
pk_target_col = validation_config.get("targetcolumn")
is_pk_less = not pk_source_col or not pk_target_col
```

With the fallback present, `is_pk_less` is `False`. The PK-less branch
(`tiered_runner.py:611-626`) is skipped, although Tier 1's `record_key` is the
row hash (`sql_query_generator.py:321-328`).

### Tier-2 failure

When Tier 1 has any unresolved key, Tier 2 re-fetches with
`WHERE "<first_col>_normalized" IN ('<hash>', ...)` (`tiered_runner.py:538-553`).
That returns 0 rows, so `compare_indexed_frames` receives two empty frames and
raises `KeyError: 'row_key'` at `row_compare.py:152`.

`main.py:621-633` records this as an ERROR summary row with
`system_error = True`. It should instead reach the documented PK-less refusal
(ADR 0010 line 52, ADR 0011 line 164).

### Independent of ADR 0036

This failure reproduces with lowercase hashes on both sides, i.e.
PostgreSQL/Redshift-style output. The MSSQL/Athena casing issue in ADR 0036
only adds a second route to the same crash.

## Evidence

**Execution path** (Historical run, generated PK-less YAML):

```text
validation_pipeline.py:298-299   src_pk_cols = tgt_pk_cols = []  → plan PK lists = []
sql_query_generator.py:321-328   record_key = <hash expr> on both sides
yaml_config_writer.py:164-167    sourcecolumn = targetcolumn = "<first_col>_normalized"
yaml_config_writer.py:196-211    validation_plan.identity.source_primary_keys = []
main.py:348-352                  _plan_block = validations["validation_plan"]; should_dispatch_hybrid → True
main.py:374                      ADR 0033/0034 guard passes (Historical)
main.py:384-401                  run_table_hybrid(validation_config=<data_validation block>, ...)
                                 (_plan_block is NOT passed)
tiered_runner.py:576             is_pk_less = False
tiered_runner.py:591-599         Tier 1: keys are hash strings
tiered_runner.py:699-719         Tier 2: IN (<hashes>) against first data column → 0 rows
row_compare.py:152               pd.DataFrame([]).sort_values("row_key") → KeyError
main.py:621-633, 676-696         ERROR row, failure_count += 1, notify_failure, exit 1
```

**Reproduction.** A scratch script outside the repository drove the real
`run_table_hybrid()` with fake DBs modelled on `test_tiered_runner.py::_FakeDB`.

| Data | Fallback PK (generated YAML) | Blank PK |
|---|---|---|
| Identical | `is_match=True` | `is_match=True` |
| One difference | `KeyError: 'row_key'` | `RuntimeError` (documented refusal) |
| Multiple differences | `KeyError: 'row_key'` | — |
| Duplicate count 2 vs 1 | `KeyError: 'row_key'` | — |

**Why `identity` is safe to rely on for hybrid.**

- `should_dispatch_hybrid()` (`utility.py:32-54`) requires a non-placeholder
  `row_hash_validation` block.
- The only writer of that block is `yaml_config_writer.py:522-535`
  (ADR 0035 §"No UI path writes it").
- Its only live entry point is `write_from_plan()`, which always passes `plan`,
  so `identity` is always written (`yaml_config_writer.py:211`).
- `identity` (`47fa96c`) predates `row_hash_validation` (`4cd7610`).
- Therefore every generated YAML that can reach hybrid already contains
  `validation_plan.identity`.

## Decision

**Proposed:** for `hybrid_v1` execution, `validation_plan.identity` is
authoritative for whether the table has a primary key.

1. When the table's `validation_plan.identity` block is present and
   `source_primary_keys` is an empty list, `run_table_hybrid` treats the table
   as PK-less, regardless of `sourcecolumn` / `targetcolumn`.
   - `source_primary_keys` is the deciding list because it is the same list
     the SQL generator uses to decide that `record_key` is the hash
     (`sql_query_generator.py:321-324`).
   - The same list drives the writer's fallback (`yaml_config_writer.py:158-167`).
2. When the `identity` block is absent (hand-written YAML), detection falls
   back to today's rule, `not sourcecolumn or not targetcolumn`. It is
   unchanged.
3. When `identity.source_primary_keys` is non-empty, `sourcecolumn` /
   `targetcolumn` keep driving Tier 2 exactly as today.
4. `yaml_config_writer.py` and the legacy fallback are not changed.

## Behavior

**Single PK.** Unchanged. `identity.source_primary_keys` has one entry, so the
Tier-1/Tier-2 PK path runs exactly as today.

**Composite PK.** Unchanged.

- `PlanValidator` rejects `hybrid_v1` with more than one PK
  (`plan_validator.py:264-270`).
- `run_table_hybrid` raises `NotImplementedError` for list PKs
  (`tiered_runner.py:577-582`).
- A non-empty `identity` list never selects the PK-less branch.

**PK-less.**

- Hybrid recognizes the table from `identity.source_primary_keys: []`.
- Tier 1 still uses the row hash as `record_key`.
- If every key is resolved, the result is the existing single `row_key=ALL`,
  `PASS` row (`tiered_runner.py:617-620`).
- If any row-hash difference is unresolved, the existing `RuntimeError`
  refusal is raised (`:621-626`). Tier 2 is never attempted with the fallback
  column.
- Two consequences of reaching the documented branch:
  - `_validate_expected_grain_hybrid` and `_run_quality_checks_hybrid` are not
    run, as already documented for PK-less
    (`docs/large-table-scalable-architecture/README.md` §S.6).
  - A refusal is still an ERROR row, now with the intended message, and no
    `KeyError`.

**Normal non-hybrid validation.** No change. `main.py`'s non-hybrid path does
not read `validation_plan.identity`. It keeps using the fallback column as the
compare key (`main.py:476-486`, `compare_indexed_frames`). A future ADR decides
whether that should change.

**Incremental + hybrid.** No change.

- The ADR 0033 guard, with ADR 0034's `apply_incremental` input
  (`main.py:374-377`), still blocks incremental hybrid runs.
- That guard runs before `run_table_hybrid` is called, so this decision never
  reaches incremental runs.

## Compatibility

The approach is intended to preserve:

- normal (non-hybrid) validation behavior for every table, PK-less included;
- all existing generated YAML, and the writer's output;
- the first-column fallback's semantics outside hybrid;
- PK-based hybrid behavior;
- composite-PK rejection (plan-time and runtime);
- hand-written hybrid YAML without an `identity` block (old detection rule).

**No regeneration should be required.** The fix reads
`validation_plan.identity.source_primary_keys`, which the Evidence section
shows is already present in every generated YAML that can dispatch hybrid.

A table would see a behavior change only if it is PK-less and runs
`hybrid_v1`. No YAML under `Project/config/` sets `execution_strategy: hybrid_v1`
today.

## Alternatives considered

| Option | Normal validation change | Hybrid change | YAML regeneration | Complexity | Risk |
|---|---|---|---|---|---|
| **A**: remove the fallback globally (blank PK for PK-less) | Yes. PK-less tables move to `main.py`'s `row_hash` path: different `row_key` values and status granularity, drift warning activates, per-row Python hashing cost | Fixed: blank PK selects `is_pk_less` | Yes, every PK-less YAML | Writer change only | Changes results of existing normal PK-less validations. Must emit blank, not `"row_hash"`: `is_pk_less` treats `"row_hash"` as a real PK column |
| **B**: suppress the fallback only when `execution_strategy == hybrid_v1` | None | Fixed for newly generated hybrid YAML | Yes, for PK-less hybrid YAML | Writer change; PK-less encoding depends on strategy | Two YAML encodings of PK-less; YAML generated before the fix still crashes |
| **C**: new explicit PK-less marker | None, if the marker is only read by hybrid | Fixed once the marker is emitted | Yes | New plan field + writer + schema + runner | Duplicates information already in `identity.source_primary_keys`. The existing `identity.type` can't serve, because it defaults to `"primary_key"` for PK-less plans |
| **D** (proposed): hybrid reads existing `validation_plan.identity` | None | Fixed | No | `main.py` passes the plan block; `tiered_runner` checks one list | `sourcecolumn` remains inaccurate for PK-less YAML; readers of that key must know `identity` wins for hybrid |

## Interaction with ADR 0035

ADR 0035 prevents `row_hash_validation` from executing as an independent
validation block (`main.py:222`). ADR 0037 does not change that. The
`row_hash_validation` block remains an internal helper input to hybrid Tier 1,
passed as `row_hash_config` (`main.py:349, 388`).

## Interaction with ADR 0036

- ADR 0036 fixes hash representation: uppercase MSSQL/Athena hex vs lowercase
  Snowflake hex in `record_key`.
- ADR 0037 fixes PK-less identity detection and Tier-2 routing.

Both are required for reliable PK-less hybrid support on MSSQL/Athena sources.
They are separate concerns with separate fixes and tests, and they are not
merged.

- After 0037 alone, an MSSQL/Athena PK-less hybrid table with identical data
  reaches the documented refusal instead of a `KeyError`. It still can't pass
  until 0036 is fixed.
- After 0036 alone, it still crashes on any real difference.

## Deferred (not decided here)

- Changing normal (non-hybrid) PK-less validation semantics (first-column
  grouping vs `row_hash`).
- Whether the legacy first-column fallback in `yaml_config_writer.py` should
  eventually be removed.
- `compare_indexed_frames` raising `KeyError` when both input frames are empty
  (`row_compare.py:152`).
- Broader schema cleanup, e.g. `identity.type` defaulting to `"primary_key"`
  for PK-less plans.
- ADR 0036's hash canonicalization.
- The inconsistent-identity edge case: `source_primary_keys: []` with a
  non-empty `target_primary_keys`. Under this decision it is treated as PK-less
  because the source `record_key` is the hash. Its target keys can never match,
  so it reaches the refusal. That is a clean refusal, not a guess.

## Tests (to add with the implementation, not now)

1. Generated PK-less shape (fallback `sourcecolumn` + `identity.source_primary_keys: []`),
   identical data → `is_match=True`.
2. Same shape, one difference → the documented PK-less `RuntimeError`, not `KeyError`.
3. Same shape, duplicate-count mismatch (2 vs 1 identical rows) → the documented PK-less `RuntimeError`.
4. PK-based hybrid (identity `["id"]`) → unchanged. The existing
   `test_hybrid_matches_untiered_oracle_on_row_level_status` and siblings keep
   passing.
5. Composite PK → the existing `NotImplementedError` / `PlanValidator` rejection
   is unchanged (`test_execution_strategy.py::test_hybrid_v1_with_composite_pk_is_rejected`).
6. Normal non-hybrid PK-less validation → `row_key`/status unchanged. It still
   keys on the fallback column.
7. A YAML already generated with `validation_plan.identity.source_primary_keys: []`
   routes to the PK-less branch without regeneration. This is a `main.py`-level
   check that the plan block reaches `run_table_hybrid`.
8. Hand-written hybrid YAML without an `identity` block → old detection rule
   unchanged.

MSSQL/Athena casing tests belong to ADR 0036 and are not part of this set.

## Implementation scope (expected)

- `Project/main.py`: pass the existing `_plan_block` (or its `identity`) into
  `run_table_hybrid` (`main.py:384-401`). `main.py:384` is the only production
  caller.
- `Project/tiered_runner.py`: `run_table_hybrid` takes an optional argument,
  defaulting to `None`, and uses it in the `is_pk_less` decision (`:574-576`).
  The optional default keeps the 15 existing test call sites in
  `test_tiered_runner.py` valid.
- `Project/test_tiered_runner.py`: tests 1-4 and 8.
- A focused `main.py`-level test for 7, following
  `Project/test_hybrid_dispatch.py` / `Project/test_incremental_mode.py`
  conventions.
- No change to `yaml_config_writer.py`, SQL generation, config schema or YAML.
  No new production helper added only for testability.

## Acceptance criteria

- PK-less hybrid no longer treats the legacy fallback column as a real PK when
  `identity.source_primary_keys` is empty.
- Tier 2 is not attempted for PK-less hybrid tables.
- Unresolved row-hash differences on PK-less hybrid reach the existing
  documented refusal (`tiered_runner.py:621-626`).
- No `KeyError: 'row_key'` from the fallback-key path.
- Normal non-hybrid behavior is unchanged, including PK-less tables.
- PK-based hybrid behavior is unchanged.
- Composite-PK validation and rejection are unchanged.
- ADR 0035 behavior is unchanged: `row_hash_validation` is never run standalone.
- ADR 0036 remains an independent fix.
- The full test suites pass after implementation (`Project/test_tiered_runner.py`,
  `Project/test_hybrid_dispatch.py`, `Project/test_incremental_mode.py`,
  `src/validation/test_execution_strategy.py`,
  `src/generated_queries/test_sql_query_generator.py`), and every touched `.py`
  file passes `py_compile`.
