# 0032. Audit of ADR 0031's implementation against the repository

**Status:** Accepted
**Date:** 2026-09-26

Audit of the report-pack/incremental/sanity implementation
([0030](0030-progressive-decision-report-pack-incremental-sanity-streamlit.md),
[0031](0031-implementation-choices-for-adr-0030-open-questions.md)) against
the actual current code, traced line-by-line rather than re-derived from the
design docs. One critical issue was found and fixed (minimal, targeted fix,
per the audit's own constraint — no new features). One issue was confirmed
as a known, already-documented limitation, not a new regression.

## Audit findings

**1. Incremental filtering applied to both source and target — confirmed
correct.** `Project/main.py:378-379`:
```python
source_query = apply_incremental_predicate(source_query, _filter_column, _from_date, _to_date)
target_query = apply_incremental_predicate(target_query, _filter_column, _from_date, _to_date)
```
Same `_filter_column`/`_from_date`/`_to_date` triple applied to both. No
asymmetry.

**2. `execution_strategy: hybrid_v1` + `incremental.enabled: true` —
confirmed unsupported, exactly as ADR 0031 already documented (not a new
finding, but verified precisely). `Project/main.py:342-379`: the
`_incremental_block`/`_incremental_enabled` check and the
`apply_incremental_predicate()` calls exist **only inside the `else:`
branch** (the non-hybrid path, line 373 on). The `if use_hybrid:` branch
(lines 345-372) calls `tiered_runner.run_table_hybrid(...)`, which never
receives `source_query`/`target_query` as arguments at all — its Tier 1
(`Project/tiered_runner.py:590-592`) builds its row-hash multimap directly
from `row_hash_config["sourcequery"]`/`["targetquery"]` (the separate
`row_hash_validation:` sibling block), which `main.py` never touches. A
table with both flags set will therefore run the full, unfiltered hybrid
Tier 1/Tier 2 scan — the incremental date range is silently ignored, with no
error, no warning, and no row-count reduction. This is a real functional
gap, but it was already disclosed in ADR 0031 ("Incremental filtering is
not wired into the hybrid Tier-1/Tier-2 path... will run the hybrid path
unfiltered today") — confirmed accurate, not a new regression from the
audit's perspective, but see Critical Issues for the recommendation.

**3. Historical mode unchanged when incremental is absent/disabled —
confirmed correct.** `_incremental_block = _plan_block.get("incremental") or {}`
and `_incremental_enabled = bool(_incremental_block.get("enabled"))` both
default to falsy when the key is missing; the `if _incremental_enabled:`
guard (line 374) means `source_query`/`target_query` are passed to
`execute_query()` completely unmodified for any table that never declares
`validation_plan.incremental`. Verified by the full existing regression
suite passing unchanged (below) and by reading the code path directly — no
new code executes on this path when the flag is absent.

**4. `IntegrityCheckBlock` cannot enter the source/target comparison path —
confirmed correct.** `Project/main.py:233-284`: the
`if validation_name == "integrity_check":` branch is keyed purely on the
validations-dict key name (not on the presence/absence of a `target` field),
sits immediately after the placeholder-skip check and before any
hybrid-dispatch or comparison code, and unconditionally `continue`s at the
end of its `try`/`except`. There is no code path by which a block named
`integrity_check` falls through into the `_plan_block`/`use_hybrid`/
`compare_indexed_frames` machinery below it.

**5. Streamlit silently falling back to historical when incremental
configuration is missing — CRITICAL ISSUE FOUND AND FIXED.** See below.

**6. Full test suite — all pass, no regressions.** See Tests/results.

**7. Other correctness issues found during the audit:** none beyond #5.
Schema (`IntegrityCheckBlock`'s `extra="forbid"`) is enforced only if
something calls `validate_config_file()`/`validate_config_dir()` — neither
`Project/main.py` nor the Run Validation tab invokes schema validation before
executing a YAML, so a hand-edited YAML with a structural mistake in an
`integrity_check`/`incremental` block would only be caught if a separate
lint/validate step is run first. This is a pre-existing property of the
whole config-schema module (`Project/main.py` has never validated schema at
run time, for any block type), not something ADR 0030/0031 introduced or
regressed — noted for completeness, not a new finding.

## Critical issues

**Streamlit's incremental exclusion was ineffective whenever the excluded
table was also selected for count validation — now fixed.**

Reproduction path (as implemented before this audit's fix):
1. Operator selects "Incremental" mode in the Run Validation tab.
2. "Select all" (the tab's default) checks a table for both
   `count_validation` and `data_validation`.
3. That table's YAML has no `validation_plan.incremental.filter_column`
   configured.
4. The (now-fixed) code removed the table from `picked_data_tables` and
   recomputed `do_data`/`selected_tables` — but `selected_tables` is
   `sorted(set(picked_count_tables) | set(picked_data_tables))`, and the
   table remained in `picked_count_tables`, so it stayed in
   `selected_tables`.
5. `Project/runner.py::start_validation()` passes `selected_tables` as one
   flat `--tables` argument shared by both `--count_validation` and
   `--data_validation` — there is no per-table, per-validation-type
   argument.
6. `Project/main.py:173-178` builds `tables_to_process` for **every**
   validation directory (`count_validation` and `data_validation`) from that
   same flat `--tables` list, filtered only by "is this table name present
   in this YAML file" — not by which Streamlit checkbox column it came
   from.
7. Result: the table's `data_validation` block still ran, historically,
   completely unfiltered — while the UI displayed "These tables are
   excluded from this run rather than run historical," which was false in
   this specific, common (select-all-by-default) case.

This directly violates the explicit ADR 0030 requirement: "If incremental
execution is requested but no incremental filter column is configured, the
system should produce a clear validation/configuration error rather than
silently executing a historical/full-table validation." The failure mode
(a large table's incremental run silently degrading to a full historical
scan) is also the specific scalability risk ADR 0030's "Large-Data
Considerations" section called out incremental filtering as existing to
prevent.

**Fix applied (minimal, per this audit's constraint):** `webapp/app.py`'s
Run Validation tab now computes `incremental_leak_tables = sorted(set(incremental_missing_tables) & set(picked_count_tables))`
and, when non-empty, shows a hard `st.error` naming the exact tables and
why, and disables the "Run validation" button (folded into the existing
`_incremental_invalid` gate already used for missing/invalid date ranges).
The softer `st.warning` (informational, "these tables are simply excluded")
now only fires for tables that are *not* also selected for count
validation, where the exclusion is genuinely effective. This blocks the
unsafe run instead of letting it proceed with an inaccurate promise — it
does not attempt the larger fix (splitting count/data validation into two
independent `start_validation()` calls so both could run correctly
simultaneously), which is a real option but not a "small fix" and is
recorded as an open follow-up below.

**Not fixed, and not blocking per the audit's own scope:** the hybrid_v1 +
incremental gap (#2). No code changed for it in this audit — see
Recommended next action.

## Tests/results

```
Project/utils/           50 passed  (test_quality_checks.py, test_incremental_filter.py,
                                      test_row_compare.py, test_semantic_normalize.py,
                                      test_utility_checks.py)
src/validation/          12 passed  (test_execution_strategy.py, test_config_schema.py)
Project/test_tiered_runner.py   38 passed
──────────────────────────────────────────
Total                    100 passed, 0 failed
```
Run both before and after the fix in this audit — identical pass counts,
confirming the fix changed only Streamlit UI/gating logic, not engine
behavior covered by these suites (none of them exercise the Streamlit
layer directly, which is exactly where the bug was).

`py_compile`/`ast.parse` clean on every file touched by the fix
(`webapp/app.py`).

## Recommended next action

1. **Hybrid + incremental (finding #2):** do not enable
   `validation_plan.incremental.enabled: true` on any table that also has
   `execution_strategy: hybrid_v1` until this is explicitly wired up —
   today it silently runs unfiltered. Two options, not decided here: (a)
   add a fail-fast guard in `Project/main.py` that raises before dispatching
   to `tiered_runner` when both flags are set on the same table (cheap,
   prevents the silent-unfiltered case, but blocks a real combination
   rather than supporting it), or (b) thread the incremental predicate into
   `tiered_runner.run_table_hybrid()`'s Tier 1 hash-streaming query and
   Tier 2 fetch (the real fix, larger, needs its own design pass since Tier
   1 reads from `row_hash_config`, not `validation_config`, and applying the
   predicate there needs the same `apply_incremental_predicate()` call
   threaded through a different code path). Recommend (a) now as a cheap
   safety net, (b) as a separate future ADR if a real large table needs both
   hybrid execution and incremental scoping simultaneously.
2. **Count/data table-set overlap (finding #5):** the fix applied here
   blocks the unsafe run but doesn't let the operator actually run
   incremental data validation on a table that's also needed for count
   validation in the same click — they must run count validation for that
   table separately (deselect it from data validation's incremental leak,
   run, then re-select and run data validation alone). If this friction
   turns out to matter in practice, revisit splitting `start_validation()`
   into two independent calls (one per validation type) as the real fix,
   rather than the current block-and-ask-the-operator-to-resolve-it
   approach.
3. No other code changes recommended from this audit — the four confirmed-
   correct findings (#1, #3, #4) need no follow-up.
