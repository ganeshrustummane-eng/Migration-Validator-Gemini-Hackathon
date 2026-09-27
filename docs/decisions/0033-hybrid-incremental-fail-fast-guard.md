# 0033. Fail-fast guard for hybrid_v1 + incremental (ADR 0032 finding #2 fix)

**Status:** Accepted
**Date:** 2026-09-26

## Context

[0032](0032-adr-0031-implementation-audit.md)'s audit confirmed finding #2:
a table configured with both `execution_strategy: hybrid_v1` and
`validation_plan.incremental.enabled: true` would silently run the full,
unfiltered hybrid Tier-1/Tier-2 scan. `tiered_runner.run_table_hybrid()`'s
Tier 1 reads `row_hash_config["sourcequery"]`/`["targetquery"]` directly
(`Project/tiered_runner.py:591-592`) and never receives the incremental
predicate main.py's non-hybrid branch applies to `source_query`/
`target_query` — the two code paths don't share any data that would let one
affect the other. No error, no warning; the date range in the YAML/UI was
just ignored.

The audit recommended two options: (a) a cheap fail-fast guard that blocks
the combination outright, or (b) actually threading the incremental
predicate into `tiered_runner`'s Tier 1/Tier 2. This decision implements
(a) only, as explicitly scoped — hybrid+incremental support itself is not
attempted here.

## Decision

Block the combination before it can execute, with a clear error, rather
than let it run unfiltered.

**Guard logic**, `Project/utils/utility.py::check_hybrid_incremental_conflict(use_hybrid, incremental_enabled)`:
```python
def check_hybrid_incremental_conflict(use_hybrid: bool, incremental_enabled: bool) -> None:
    if use_hybrid and incremental_enabled:
        raise ValueError(
            "Incremental validation is currently not supported with hybrid_v1. "
            "Disable incremental mode or use the standard execution strategy."
        )
```
Implemented as a small pure function next to the existing
`should_dispatch_hybrid()` in the same file, rather than inlined directly in
`Project/main.py` — `main.py` is not import-safe (module-level side effects,
required `argparse` at import time), so a helper function is the only way
this guard is actually unit-testable, matching `should_dispatch_hybrid`'s
own existing pattern exactly.

**Call site**, `Project/main.py` (right before the `if use_hybrid:`
dispatch, after `use_hybrid` and `_incremental_enabled` are both already
computed):
```python
check_hybrid_incremental_conflict(use_hybrid, _incremental_enabled)

if use_hybrid:
    ...
```
The `raise` happens inside the same `try` block that already wraps every
other per-validation-block execution, so it's caught by the existing
`except Exception:` handler, which calls `_write_error_summary(...)` and
marks the run as having a system error — the same fail-fast/ERROR-row
treatment every other pre-execution config problem in this loop already
gets (e.g. a malformed row_hash config, a bad PK). It does not crash the
whole run or block other tables; that table's block is recorded as an
ERROR and the loop continues.

## Files changed

- `Project/utils/utility.py` — new `check_hybrid_incremental_conflict()`.
- `Project/main.py` — import + one guard call, inserted between the
  existing `_incremental_enabled` computation and the `if use_hybrid:`
  branch. No other line in `main.py` changed.
- `Project/test_hybrid_dispatch.py` — 4 new tests (see below), added to the
  file that already tests `should_dispatch_hybrid()` against the exact
  per-block loop shape `main.py` uses, since this guard is the direct
  sibling decision to that one.

`tiered_runner.py` was not touched, per the explicit constraint — Tier 1/
Tier 2 still don't know incremental filtering exists; they simply can no
longer be reached when it's enabled for the same table.

## Tests added

In `Project/test_hybrid_dispatch.py`:
1. `test_hybrid_plus_incremental_fails_fast` — `use_hybrid=True,
   incremental_enabled=True` → raises `ValueError` matching "not supported
   with hybrid_v1".
2. `test_hybrid_without_incremental_still_works` — `use_hybrid=True,
   incremental_enabled=False` → no raise.
3. `test_standard_execution_with_incremental_still_works` —
   `use_hybrid=False, incremental_enabled=True` → no raise.
4. `test_standard_historical_execution_still_works` — `use_hybrid=False,
   incremental_enabled=False` → no raise.

## Test results

```
Project/test_hybrid_dispatch.py + Project/test_tiered_runner.py   46 passed
Project/utils/ (test_quality_checks.py, test_incremental_filter.py,
                 test_row_compare.py, test_semantic_normalize.py,
                 test_utility_checks.py)                          50 passed
src/validation/ (test_execution_strategy.py, test_config_schema.py) 12 passed
──────────────────────────────────────────────────────────────────
Total                                                             108 passed, 0 failed
```
`py_compile`/`ast.parse` clean on `Project/main.py`, `Project/utils/utility.py`,
`Project/test_hybrid_dispatch.py`.

## Confirmation of scope

No other behavior changed:
- Standard incremental execution — unchanged (the guard only fires when
  `use_hybrid` is also true; the non-hybrid branch's predicate injection is
  untouched).
- Historical execution — unchanged (`_incremental_enabled` still defaults
  false when absent; the guard is a no-op in that case).
- Semantic normalization, quality checks, row comparison — untouched files,
  not on this call path.
- Hybrid execution when incremental is disabled — unchanged; the guard is a
  no-op (`incremental_enabled=False`).
- Streamlit — no change beyond what already existed; the new ERROR row this
  guard produces surfaces through the same existing summary-CSV/ERROR-row
  display path every other pre-execution failure already uses. No new UI
  code was added for this fix.
- CLI arguments — none added or changed.
- YAML schema — unchanged; this is a runtime check on values already
  present in a loaded YAML, not a new schema field.

## Consequences

A table cannot be run with both `execution_strategy: hybrid_v1` and
`validation_plan.incremental.enabled: true` until [0032](0032-adr-0031-implementation-audit.md)'s
option (b) (threading the predicate through `tiered_runner`'s Tier 1/Tier 2)
is designed and implemented as its own decision. Attempting the combination
today produces a clear, named ERROR row instead of an incorrect, unfiltered
result silently reported as a normal run.
