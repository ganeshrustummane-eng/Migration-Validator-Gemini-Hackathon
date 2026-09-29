# 0039. Bronze data YAML keeps multi-line SQL, same as Silver

**Status:** Accepted — implemented
**Date:** 2026-09-28

## Context

ADR 0022/0023 made Silver's `sourcequery`/`targetquery` multi-line and left
Bronze flattened to one line by `_to_single_line()` in
`src/generated_queries/yaml_config_writer.py`. A single-line query that
selects dozens of normalized columns is hard to review by eye. Reviewing
generated YAML before running it is a required step (Guide tab: Generate
YAML → Review & Approve → Run). Bronze SQL is already assembled one column
per line (`ai_sql_generator.py`'s `"SELECT\n  " + ",\n  ".join(...)`), and
flattening only threw those line breaks away.

## Decision

`write()`'s `_prep()` uses `_to_indented_multiline()` for both layers.
`_to_single_line()` had no other caller, so it was deleted. Count YAML is
unchanged: its `SELECT COUNT(*)` queries stay one line for both layers.

## Alternatives considered

- **A per-layer or per-user formatting toggle.** Rejected: nobody needs
  flattened SQL, so a toggle would be a knob with no user.

## Consequences

- Bronze YAML is readable, and a `--` comment inside generated SQL can no
  longer swallow the rest of the query (the ADR 0022 bug class) on Bronze
  either.
- Execution is unchanged. `main.py` passes the query string to the driver
  as-is, and `apply_incremental_predicate` already handles multi-line SQL,
  because Silver has used it since ADR 0023.
- YAML generated before this change stays single-line until it is
  regenerated.
- Related fix, found in the same pass: `write()` appended `_normalized` to
  the PK column name for Silver too (`sourcecolumn: UNIT_ID_normalized`),
  but Silver SQL aliases columns by their plain target name (`AS "UNIT_ID"`).
  As a result, every Silver data validation failed with a KeyError at
  `set_index`, including the existing `DISCOUNT_LINES.yaml`. The suffix is now
  Bronze-only, and Silver's no-PK fallback uses the target column name.
