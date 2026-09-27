# 0036. PK-less `hybrid_v1` Tier-1 `record_key` is not case-normalized — MSSQL/Athena sources never match Snowflake

**Status:** Accepted — implemented 2026-09-27 (`_hash_expression` lower-cases MSSQL/Athena hex; test in `src/generated_queries/test_sql_query_generator.py`)
**Date:** 2026-09-27

## Summary

- **Verdict: confirmed bug.** It is latent: no YAML under `Project/config/` sets
  `execution_strategy: hybrid_v1` today.
- On a PK-less table, the Tier-1 `record_key` is the raw hex hash string.
  MSSQL and Athena produce UPPERCASE hex. Snowflake produces lowercase.
  `_collect_hash_multimap` lowercases `row_hash` but not `record_key`, and it
  matches keys with exact Python `set` operations. So no source key ever
  equals a target key.
- Outcome: the table is always reported as **ERROR**, even when the data is
  identical. It is never a false PASS.
- PostgreSQL, Redshift and every PK-based hybrid table are not affected by the
  casing problem.
- An adjacent bug was found during the trace and is independent of casing.
  Generated PK-less YAML carries a fallback `pksourcecolumn`, so any real
  difference crashes Tier 2 on every source type (see "Adjacent finding").

## 1. Context

ADR 0035 left a deferred concern open: MSSQL/Athena hash expressions may emit
uppercase hex while Snowflake emits lowercase. `row_hash` is lowercased in the
hybrid path, but it was unclear whether `record_key` gets the same treatment.
This ADR records a read-only investigation. No production code, test, YAML or
config was changed.

## 2. Execution path (traced from code, not docstrings)

```text
Project/main.py::_validate_table (main.py:216)
  ├─ skips row_hash_validation block (main.py:222, ADR 0035)
  ├─ _row_hash_block = validations["row_hash_validation"] (main.py:349)
  ├─ should_dispatch_hybrid("data_validation", plan, row_hash_block) (utility.py:32-54)
  └─ tiered_runner.run_table_hybrid(row_hash_config=_row_hash_block, ...) (main.py:384)
       ├─ is_pk_less = not pksourcecolumn or not pktargetcolumn (tiered_runner.py:576)
       ├─ Tier 1: _collect_hash_multimap(src_db, row_hash_config["sourcequery"]) (:591)
       │          _collect_hash_multimap(tgt_db, row_hash_config["targetquery"]) (:592)
       │            key_str  = str(key_val)                 (:124)  ← NO case normalization
       │            hash_str = str(hash_val).lower()        (:125)  ← normalized
       ├─ key matching: set(src_hash) - set(tgt_hash), & ... (:594-599) ← exact string equality
       ├─ is_pk_less branch: anything unresolved → raise RuntimeError (:611-626)
       └─ PK branch → Tier 2: _fetch_batch(... WHERE "<pk_col>" IN (<record_keys>)) (:538-553, :699-719)
```

The queries come from `SQLQueryGenerator._row_hash_queries`
(`src/generated_queries/sql_query_generator.py:299-336`). `yaml_config_writer.py:216-217, 522-535`
copies them verbatim into the `row_hash_validation:` block.

`PlanValidator` (`src/validation/plan_validator.py:263-275`) rejects
`hybrid_v1` only for composite PKs or a missing `row_hash` spec. A PK-less
plan (0 PKs) passes, so this path can be reached through normal generation.
Athena has no PK concept at all, so every Athena `hybrid_v1` table takes this
path.

## 3. `record_key` generation

PK-less means `plan.source_primary_keys` is empty. In that case
`_row_hash_queries` sets `source_key = source_hash` and
`target_key = target_hash` (`sql_query_generator.py:321-328`). The key is the
same SQL expression as `row_hash`, generated in SQL by `_hash_expression`
(`sql_query_generator.py:370-400`).

| Source | Exact expression (source side) | Paired Snowflake target expression | Output form | Case normalization |
|---|---|---|---|---|
| PostgreSQL | `MD5(COALESCE(CAST(c AS TEXT),'<<NULL>>') \|\| '\|' \|\| ...)` | `MD5(CONCAT_WS('\|', ...))` (`algorithm="md5"`, :315) | 32-char hex VARCHAR, **lowercase** on both | None, and none needed |
| Redshift | `SHA2(... \|\| '\|' \|\| ..., 256)` | `SHA2(CONCAT_WS('\|', ...), 256)` | 64-char hex, **lowercase** on both | None, and none needed |
| MSSQL | `CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', ... + '\|' + ...), 2)` | `SHA2(CONCAT_WS('\|', ...), 256)` | 64-char hex. **UPPERCASE** source vs lowercase target | None in SQL. Python lowers `row_hash` only |
| Athena | `to_hex(sha256(to_utf8(concat_ws('\|', ...))))` | `SHA2(CONCAT_WS('\|', ...), 256)` | 64-char hex. **UPPERCASE** source (Trino `to_hex`) vs lowercase target | None in SQL. Python lowers `row_hash` only |
| Snowflake (target) | see column 3 | — | lowercase hex (`SHA2`/`MD5`) | — |

Output casing of each database function is documented engine behaviour. It
was not measured against a live database in this investigation. The repo
already asserts the MSSQL half in `tiered_runner.py:115-118`. That docstring
says "the others are lowercase", which is wrong for Athena `to_hex`.

PK-based tables use `record_key = <pk column>` (`:321-326`): a raw column value
on both sides, with no hex involved. They are not affected by hex casing.

## 4. Matching behavior

`_collect_hash_multimap` builds `{str(record_key): sorted([row_hash.lower(), ...])}`.
Tier 1 then compares the keys with plain Python set algebra (`:594-599`), which
is exact, case-sensitive string equality. Given source `ABC123` and target
`abc123`, the current code treats them as **two different rows**: one
source-only key and one target-only key.

## 5. Failure mode (reproduced)

Repro: a scratch script outside the repo drives the real `run_table_hybrid`
with fake DBs, modelled on `Project/test_tiered_runner.py::_FakeDB`. It uses 3
identical logical rows, source keys = `sha256hex.upper()`, target keys =
`sha256hex`.

| Case | YAML shape | Tier-1 classification | Result of `run_table_hybrid` |
|---|---|---|---|
| A | Generator output: `pksourcecolumn: <first_col>_normalized` (see adjacent finding) | source_only=3, target_only=3, match=0 | `KeyError: 'row_key'`, raised by `compare_indexed_frames` (`row_compare.py:152`) via `tiered_runner.py:703`. Tier 2 fetches `WHERE [id_normalized] IN ('<HEX>', ...)`, gets 0 rows, and gets an empty result frame |
| A-control | same, same casing | match=3 | `is_match=True`, 3 PASS |
| B | Hand-written, blank `pksourcecolumn` | source_only=3, target_only=3 | `RuntimeError` "found row-hash differences on PK-less table" (`tiered_runner.py:621`) |
| B-control | same, same casing | match=3 | `is_match=True`, 1 PASS row (`row_key=ALL`) |

In both failing cases the exception reaches `main.py:621-633` (`except
Exception`). Only effects that the code shows are listed here:

- `_write_error_summary(...)` writes an **ERROR** summary row. It is not FAIL,
  and not a false PASS.
- `local_failure_count += 1`, which adds 1 to the run's total `failure_count`
  (per table, not per row).
- `local_system_error = True` → **exit code 1** (`main.py:696`).
- `notify_failure(...)` fires because `failure_count > 0` (`main.py:676-692`).
- No per-row SOURCE_ONLY/TARGET_ONLY detail reaches the CSV. Case A crashes
  before the first batch is written. Case B raises before writing anything.

## 6. Adjacent finding (not casing — separate issue, same code path)

For a PK-less plan, `yaml_config_writer.write()` (`:164-167`) never emits an
empty `pksourcecolumn`. It falls back to `<first active column>_normalized`.
The `row_hash_validation` block still uses the hash as `record_key`. At
runtime `is_pk_less` is therefore **False**, and Tier 2 filters the first data
column by hash strings.

Result: any real difference on a generated PK-less `hybrid_v1` table, on
**any** source type including PostgreSQL, raises
`KeyError: 'row_key'`. This was reproduced with the same case on both sides and
1 changed row. The `is_pk_less` refusal branch in `tiered_runner.py:611-626`
can only be reached from hand-written YAML. This issue is out of scope for
0036 and needs its own decision.

## 7. Existing test coverage

- `Project/test_tiered_runner.py`: `test_pk_less_duplicate_rows_matching_count_passes`
  (:204), `test_pk_less_duplicate_count_mismatch_refuses_rather_than_guessing`
  (:238), `test_quality_checks_hybrid_pk_less_early_return_...` (:1036). All of
  them use identical-case `h AS record_key, h AS row_hash` on both sides and a
  blank PK.
- `src/generated_queries/test_sql_query_generator.py`: asserts the algorithm
  choice only (MD5 vs SHA2), not output casing.
- `Project/test_hybrid_dispatch.py`, `Project/test_incremental_mode.py`: cover
  dispatch and skip only.
- All pass: 47 pytest cases + 4 script checks. **None would catch this.**

Smallest missing regression test: in `test_tiered_runner.py`, run a PK-less
table where source rows are `(H.upper(), H.upper())` and target rows are
`(H, H)` for the same `H`. Assert `is_match is True` with no exception. Add
the same case using the generator's fallback `pksourcecolumn` shape.

## Decision

Classify this as a **confirmed correctness bug** and fix it in the SQL
generator. Do not fix it in the Python matcher.

The smallest fix is to make `SQLQueryGenerator._hash_expression` return
lowercase hex for MSSQL (`LOWER(CONVERT(VARCHAR(64), HASHBYTES(...), 2))`) and
Athena (`lower(to_hex(...))`). This is the one function every `record_key` and
`row_hash` expression goes through, so both columns come out canonical at the
source. The `.lower()` at `tiered_runner.py:125` then becomes a harmless
backstop.

The fix is not implemented in this ADR. It needs its own change, plus the
regression test described in §7.

## Alternatives considered

- **Lowercase `key_str` in `_collect_hash_multimap`**: this is wrong for
  PK-based tables. String PKs `'ABC'` and `'abc'` are distinct rows, and
  folding them together would hide real SOURCE_ONLY/TARGET_ONLY rows.
- **Lowercase the key only when `is_pk_less`**: this misses case A. Generated
  YAML is never `is_pk_less` at runtime (§6).
- **Wrap only the PK-less `record_key` in `LOWER(...)` inside
  `_row_hash_queries`**: this works too, but leaves `row_hash` case-inconsistent
  in SQL and still depends on the Python backstop. Fixing `_hash_expression`
  handles both columns in one place.

## Consequences

- Existing generated YAMLs are not fixed until they are regenerated, because
  the SQL is baked in at generation time. No YAML under `Project/config/` opts
  into `hybrid_v1` today, so nothing needs regenerating right now.
- The fix does not change results for PostgreSQL, Redshift or PK-based tables,
  whose hex output is already lowercase or which use no hex key.
- Even after this fix, a generated PK-less `hybrid_v1` table still crashes on
  any real difference, because of §6. Track that separately before enabling
  `hybrid_v1` on any PK-less table.
