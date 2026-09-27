# 0035. `row_hash_validation` is an internal helper block — never executed standalone by `main.py`

**Status:** Accepted (implemented)
**Date:** 2026-09-27

## Approved decision (summary)

- `row_hash_validation` is an internal helper block, not a supported standalone validation.
- `Project/main.py` never executes it independently. It is skipped at the top of the per-block loop in `_validate_table`, before the placeholder check.
- The hybrid_v1 Tier 1 engine still consumes the block directly (`main.py`'s `_row_hash_block` lookup → `tiered_runner.run_table_hybrid(row_hash_config=...)`, unchanged).
- Existing YAML `row_hash_validation:` blocks stay, because hybrid needs them. No writer, schema, connector or UI change.
- A hand-edited standalone `row_hash_validation` is treated as an unsupported configuration and skipped.
- Whether config validation should reject such a configuration explicitly is deferred (see the open question and Deferred #2).
- The skip applies only to `row_hash_validation`. `transformation_validation` / `aggregate_validation` behavior is unchanged (Deferred #4).

## 1. Context / problem

A table's `validations:` mapping can contain a `row_hash_validation:` block
(emitted by `src/generated_queries/yaml_config_writer.py:522-535` whenever the
plan has a `row_hash` spec). Its only intended consumer is the hybrid_v1
Tier-1 engine: `Project/tiered_runner.py:591-592` reads
`row_hash_config["sourcequery"]` / `["targetquery"]` to stream
`(record_key, row_hash)` pairs.

`Project/main.py`'s per-block loop (`main.py:216`) iterates every entry of
`validations:` and executes any block that has a non-placeholder `source` +
`sourcequery` (`main.py:236-241`). `row_hash_validation` has both, so it is
executed a second time as if it were an independent validation. The Phase 2
audit fix F1 (`should_dispatch_hybrid()`, `utility.py:32-54`) stopped it being
*hybrid-dispatched* on its own, but it still falls through to the ordinary
fetch+compare branch (`main.py:401-593`). F1 was a partial fix.

## 2. Evidence the block is a helper / reserved slot

| Source | What it says |
|---|---|
| `yaml_config_writer.py:517-521` | "Consumed only when a table's `validation_plan.execution_strategy` is also set to hybrid_v1; every other table ignores this block entirely." |
| `utility.py:37-42` (`should_dispatch_hybrid` docstring) | `row_hash_validation` / `transformation_validation` / `aggregate_validation` / `validation_plan` are "sibling helper blocks … not independent validations, and must never be dispatched on their own"; F1: it "was previously getting re-executed as its own phantom validation because it also has a source/sourcequery key." |
| `main.py:336-341` | Same statement inline: "must never be dispatched as if they were their own independent validation (Phase 2 audit finding F1)." |
| `test_hybrid_dispatch.py:71-76` | `test_row_hash_validation_never_dispatches_as_its_own_validation` — "must never be executed as its own validation". (It only asserts the hybrid predicate, not non-execution.) |
| ADR 0026:48-53 | These blocks "are reserved slots in an existing, already-parsed schema, not new speculative machinery." |
| ADR 0032:31-35 | Tier 1 builds its multimap "from `row_hash_config[...]` (the separate `row_hash_validation:` sibling block), which `main.py` never touches." (Factually wrong about the loop today; correct about intent.) |
| `tiered_runner.py:3-5` | hybrid dispatch happens "only when … a populated row_hash_validation block exists" — the block is an input to hybrid, not a validation. |
| `src/validation/config_schema.py:162-168` | `TableValidations` types only `count_validation`, `data_validation`, `validation_plan`, `integrity_check`; `row_hash_validation` has no schema and passes via `extra="allow"`. |

## 3. Current (incorrect) execution flow

Per table, in YAML order (the writer emits `data_validation` first, `row_hash_validation` last):

1. `data_validation` → `should_dispatch_hybrid` True (hybrid tables) → `tiered_runner.run_table_hybrid()` — Tier 1 streams `row_hash_validation`'s queries in 50k chunks, Tier 2 re-fetches only unresolved keys. Correct.
2. `row_hash_validation` → passes the placeholder check (`main.py:236`) → `should_dispatch_hybrid` False (`utility.py:43`) → ordinary branch:
   - `obj.execute_query(source_query)` / `execute_query(target_query)` (`main.py:418, 426`) — **full, non-streamed** fetch of both sides into pandas.
   - No `pksourcecolumn` in the block → falls back to `pk = "row_hash"` (`main.py:470-472`); the SQL already returns a `row_hash` column, so the **raw SQL hash string becomes the join key** (`main.py:488` branch not taken).
   - `compare_indexed_frames` → result/failed CSVs → `PASS`/`FAIL` → `local_failure_count += 1` on FAIL (`main.py:564`) → `create_summary(..., validation_type="row_hash_validation")` → `row_hash_validation_summary.csv` (`utility.py:240`).

**False-failure mechanism (MSSQL, Athena).** Source hashes are uppercase hex:
MSSQL `CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', …), 2)` and Athena
`to_hex(sha256(…))` (`sql_query_generator.py:377, 380`); Snowflake `SHA2(…)` is
lowercase (`:388`). Tier 1 compensates explicitly: `_collect_hash_multimap`
lower-cases every hash (`tiered_runner.py:115-125`). The ordinary path does
not. `canonicalize_frames` only lower-cases for the `"true"`/`"false"` check
(`semantic_normalize.py:224-228`) and returns other strings unchanged. So every
row keys differently on each side → every row `SOURCE_ONLY` + `TARGET_ONLY` →
`FAIL`, plus a misleading "column drift" warning (`main.py:535`), while the
hybrid `data_validation` result for the same table is correct. PostgreSQL
sources use `MD5` on both sides (lowercase, `sql_query_generator.py:315, 386`)
and are not expected to hit this. The failure is still a redundant second
full scan.

**Blast radius.**
- `failure_count` → `notify_failure` fires (`main.py:670-688`) naming a failure the operator cannot find in the UI summary.
- Exit status changes **only** if the second path raises (DB/network error, `MemoryError`) → `local_system_error = True` → `sys.exit(1)` (`main.py:604-627, 690`). A plain false `FAIL` does not change the exit code.
- `runner.collect_validation_result` collects only `count_validation`, `data_validation` and `integrity_check` summaries (`runner.py:176-187`), and `results_store.record_run` receives only those (`:193`). The `row_hash_validation` summary never reaches the UI summary table or History. Its `*_result_*` / `*_failed_*` CSVs **are** globbed into `diff_files` / `failed_files` (`runner.py:189-190`) and listed for download (`webapp/app.py:3914-3925`), so they appear there with no summary row.

## 4. Decision

`row_hash_validation` is an internal helper block. `Project/main.py` must
never execute it as an independent validation block, for any table,
regardless of `execution_strategy` or run mode.

Implementation: skip it at the top of the per-block loop in `_validate_table`
(`main.py:216`), **before** the placeholder check, the stale-exclusions
warning, the incremental decision and the hybrid/incremental guard:

```python
if validation_name == "row_hash_validation":
    continue
```

(Optionally a `logger.debug(...)`, matching the existing placeholder-skip idiom at `main.py:237-240`.)

The `row_hash_block` lookup at `main.py:343` is unaffected. It reads the
block by key from `table_config["validations"]` and does not depend on the loop
visiting it, so hybrid dispatch for `data_validation` keeps working.

## 5. Historical hybrid behavior after the change

- `data_validation` → hybrid Tier 1/Tier 2, unchanged.
- `row_hash_validation` → skipped. No second full fetch, no second compare, no `row_hash_validation_*` CSVs or summary, no false MSSQL/Athena `FAIL`, no spurious `failure_count` / notification.
- `count_validation`, `transformation_validation`, `aggregate_validation`, `integrity_check` → unchanged.

## 6. Historical non-hybrid behavior after the change

A non-hybrid table gets a real `row_hash_validation` block whenever its plan
has a `row_hash` spec but `execution_strategy` is `standard`. The writer emits
the block on `row_hash` alone (`yaml_config_writer.py:522`). Today that block
runs as a full standalone compare. After the change it is skipped, which
matches the writer's documented contract ("every other table ignores this
block entirely"). `data_validation` is unchanged, including its own Python
`row_hash` fallback for PK-less tables (`main.py:469-508`), which is a
different mechanism and does not read this block.

## 7. Incremental hybrid behavior after the change

Today, in an Incremental run of a hybrid_v1 table, the table-scoped guard
(`main.py:365-371`, ADR 0034 §344) raises for **both** `data_validation` and
`row_hash_validation`, giving two ERROR rows and `failure_count += 2`. After:

- `data_validation` → ERROR via `check_hybrid_incremental_conflict`, unchanged. `system_error` stays True.
- `row_hash_validation` → skipped before the guard. **One** ERROR row per table instead of two.
- The table-scoped guard stays: it still protects `transformation_validation` / `aggregate_validation` from running filtered on a blocked hybrid table.

Incremental non-hybrid: `row_hash_validation` currently runs filtered (if
`incremental` is configured) or unfiltered (`utility.py:88-98`). After the
change it is skipped in both cases.

## 8. Why all tables, not only hybrid tables

- The only consumer of the block's queries is Tier 1. On a non-hybrid table nothing is supposed to read them (writer comment, `yaml_config_writer.py:519-521`), so there is no correct consumer to keep.
- A hybrid-only skip would need `should_dispatch_hybrid("data_validation", …)` in the skip condition. That couples a static "is this a helper?" fact to a runtime strategy decision, and it would leave the MSSQL/Athena case-mismatch false `FAIL` live on every non-hybrid table that has a `row_hash` spec.
- A one-name, unconditional skip is the smallest diff and has no mode matrix to test.

## 9. Output / compatibility consequences

- `row_hash_validation_summary.csv`, `{table}_row_hash_validation_result_{run_id}.csv` and `{table}_row_hash_validation_failed_{run_id}.csv` are no longer produced.
- No code consumes `row_hash_validation_summary.csv`. A grep of `src/`, `webapp/` and `Project/` finds no reader; `runner.py:176-187` reads only the three summaries named above. The result/failed CSVs disappear from the Run Validation download lists (`runner.py:189-190`), which is correct, since they were misleading.
- `validation_audit.jsonl` loses its `row_hash_validation` entries.
- `failure_count`, notifications and (on exception) exit code can go **down** for tables that were failing only through this path. That is the fix, not a regression, but a table that "starts passing" after deploy should be understood as this change.
- No YAML or schema change. Existing YAMLs keep their `row_hash_validation` blocks, and hybrid still needs them.

## 10. Performance consequences

For a 200-300M-row hybrid table, the second path today does a full,
non-chunked `execute_query` of `(record_key, row_hash)` on both sides into
pandas, then `canonicalize_frames`, then an indexed compare over the full frame,
then writes a full-table result CSV. That defeats the bounded design of hybrid_v1
(`TIER1_FETCH_CHUNK`, `TIER2_BATCH_SIZE`, never materializing matched rows;
`tiered_runner.py:38-43, 721-732`), and it is the most likely out-of-memory
point in the run. Skipping it removes one full scan per side per table, and
the peak memory of the run drops to what the hybrid engine itself uses.
Non-hybrid tables with a `row_hash` spec also save one full scan per side.

## 11. Test plan

Existing tests encode the current behavior and must change with the fix:

- `Project/test_incremental_mode.py::_replay` (`:95-115`) mirrors the main.py loop; add the same skip.
  - `test_incremental_hybrid_capable_blocks_every_block` (`:118-122`): expect `set(got) == {"data_validation"}`.
  - `test_historical_hybrid_capable_runs_hybrid` (`:125-128`): assert `"row_hash_validation" not in got`.
  - `test_incremental_hybrid_unconfigured_keeps_sibling_behavior` (`:137-141`): currently asserts `row_hash_validation == ("plain", None)`, which is this bug. Replace with "not in got". Add a `transformation_validation` block to `_table()` if sibling behavior still needs coverage.
- `Project/test_hybrid_dispatch.py`: keep the existing dispatch assertions. Add one test that the main.py-mirroring loop never *executes* `row_hash_validation` (dispatch=False was never the same as not-executed).
- Manual/integration check, one MSSQL (or Athena) hybrid table in a Historical run: exactly one `data_validation` summary row, no `row_hash_validation_*` files in the run dir, `failure_count` equal to the `data_validation` result only, no notification if the data matches.
- `py_compile Project/main.py` (CLAUDE.md rule 4).

As implemented: the three tests above are updated, plus
`test_standard_table_skips_row_hash_helper` and
`test_real_sibling_blocks_still_execute` (`test_incremental_mode.py`), plus
`test_main_skips_row_hash_validation_before_placeholder_check`
(`test_hybrid_dispatch.py`). That last test reads `main.py`'s source and checks
that the skip exists and comes before the placeholder check, so the mirrors
cannot silently drift from the real loop on this point.

Known limit: both test files **mirror** main.py's loop instead of calling it
(main.py runs at module import). A future edit to main.py can drift from the
mirrors without any test failing. See Deferred.

## 12. Deferred issues

1. **PK-less hybrid keys are not case-normalized.** For PK-less tables the source `record_key` *is* the hash (`sql_query_generator.py:323-324`), but `_collect_hash_multimap` lower-cases only `row_hash`, not the key (`tiered_runner.py:124`). MSSQL/Athena PK-less hybrid tables would then classify every key `SOURCE_ONLY` / `TARGET_ONLY` and raise at `tiered_runner.py:621`. Found by reading the code; not reproduced. Separate fix.
2. **Schema gap.** `row_hash_validation` is untyped (`config_schema.py:163`, `extra="allow"`), so nothing validates or flags it. It could get a typed block, or a plan-validation warning when `row_hash` is set without `hybrid_v1`.
3. **ADR 0032 wording.** "`main.py` never touches" (`0032:35`) is inaccurate for the loop before this ADR. Annotate 0032 to point here.
4. **Other sibling blocks.** `transformation_validation` / `aggregate_validation` are grouped with `row_hash_validation` as "helper blocks" in `utility.py:37-40`, yet `main.py` executes them standalone today and the incremental tests treat that as intended (`test_incremental_mode.py:67-69`). Whether they are checks or helpers is ambiguous in the repo and is **out of scope** here.
5. **Pre-existing wrong-level lookup.** `main.py:490` reads `validation_config["validation_plan"]` for `row_hash.columns` (acknowledged at `main.py:328-331`), so `data_validation`'s own Python fallback never sees the configured columns. Unrelated to this ADR.
6. **Loop testability.** main.py's per-block decision logic lives inline in `_validate_table`. Tests can only mirror it (see §11).

## Question: "Can a user intentionally configure `row_hash_validation` as a standalone validation?"

**Answer: No supported path exists. It is mechanically possible and was never
designed or documented. One point is left open.**

Evidence it is not a supported standalone validation:
- Every statement of intent in the repo calls it a Tier-1 input or reserved slot (§2). None describes it as a check a user opts into.
- No UI path writes it. The only writer is `yaml_config_writer.py:522-535`. The webapp's three `yaml.dump` paths and `excel_batch_loader.write_yaml` never emit it (grep of `webapp/app.py` finds no `row_hash_validation`). The webapp's own no-PK mode puts `pksourcecolumn: row_hash` inside `data_validation` instead (`webapp/app.py:2105-2117, 3243-3253`).
- No UI flow sets `plan.row_hash` (ADR 0011:95-99, 126-131). The one code path that sets it from user intent, `build_plan_from_requirement` (`requirement_planner.py:124`), has no caller outside `src/core/__init__.py`.
- Its output summary is never collected (`runner.py:176-187`), so a standalone result would never reach the summary UI or History. That is not a working feature.

How it can still happen: a hand-edited YAML, the Custom YAML editor, or a
persisted plan JSON with `row_hash` and no `hybrid_v1`
(`validation_plan.py:550`). All of these pass the schema via `extra="allow"`,
and main.py would run the block today.

**Open question:** ADR 0026:51-52 says these blocks "only carry real SQL when
a table's plan actually configures that check." That can be read as "a
configured `row_hash` is a check that should run." Nothing in the repo
confirms anyone relies on that reading. **Resolved for now:** a hand-written
standalone `row_hash_validation` is treated as an unsupported configuration
and skipped. **Still deferred:** whether it should instead be **rejected** at
config/plan validation (Deferred #2), so the user gets told rather than
getting a quiet no-op.

## Alternatives considered

- **Skip only on hybrid tables:** rejected (§8). It leaves the MSSQL/Athena false `FAIL` on non-hybrid tables and couples a static fact to runtime strategy.
- **Lower-case hashes in the ordinary path so the second run passes:** rejected. It fixes the symptom and keeps the redundant full scan and out-of-memory risk.
- **Stop the writer emitting the block:** rejected. Hybrid Tier 1 needs it.
- **Collect `row_hash_validation_summary.csv` into the UI:** rejected. It would surface a redundant, non-canonicalized duplicate of `data_validation`.
