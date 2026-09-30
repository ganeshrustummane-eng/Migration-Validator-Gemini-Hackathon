# 0047. Silver config files are named `INT_<table>`

**Status:** Accepted — implemented; Bronze naming changed to the target table name by [0048](0048-minimal-generated-yaml.md)
**Date:** 2026-09-29

## Context

Silver YAMLs were named after the Bronze table (`DISCOUNT_LINES.yaml`,
`CONTACTS.yaml`), the same way Bronze YAMLs are. The team wants to tell the
two layers apart from the file name alone.

The runner uses that name in two ways:

1. `Project/utils/utility.py::get_config_output_paths()` finds a data YAML
   **by file stem**.
2. `Project/main.py` then looks the table up **by the same name** under
   `tables:` inside that file.

The Run Validation tab passes the stem as `--tables`. Renaming only the file
would make the run skip the table without any error.

## Decision

A new function, `yaml_config_writer.config_table_name(plan, layer)`, decides
the name:

- **Bronze:** the source table, unchanged.
- **Silver:** `INT_<table>`. The Coalesce node name is used when it already
  starts with `INT_`; otherwise the prefix is added (it is never doubled).

That one name is used in three places:

- the data YAML file name;
- its `tables:` key;
- the table's key in `config/silver/count_validation/snowflake.yaml`.

`source_table_name` / `target_table_name` inside the blocks stay the real
Bronze and Silver table names.

## Alternatives considered

- **Rename only the file.** Rejected: the runner looks the table up by stem
  and then by key, so the table would be silently skipped.
- **A separate folder instead of a prefix.** Already the case
  (`config/silver/`), but the name alone didn't show the layer.

## Consequences

- YAMLs generated before this change (`DISCOUNT_LINES.yaml`,
  `CONTACTS.yaml` and their count keys) keep their old names. Regenerate
  them, then delete the old files and keys, or the Run tab will list both.
- Test: `test_silver_config_is_named_int_table`.
