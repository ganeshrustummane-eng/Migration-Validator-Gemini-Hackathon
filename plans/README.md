# Plans — Migration Validator

Written against commit `cb3c859` (branch `version1.2`).

## Execution order

| # | Plan | Status | Effort | Depends on |
|---|---|---|---|---|
| 001 | [Fix YAML `source:` field in Custom SQL tab](001-fix-custom-sql-yaml-source-field.md) | DONE | XS | — |
| 002 | [Add AI-generated Snowflake SQL in Custom SQL tab](002-custom-sql-tab-snowflake-ai-generation.md) | DONE | S | 001 recommended first |

## Dependency note

Ship 001 first. It is a 2-line critical fix that makes Custom SQL YAMLs actually
executable. Plan 002 is additive and safe to do independently, but executing 001
first means you can test 002's YAML output end-to-end without the dialect bug
masking results.

## What was audited / not audited

Audited: `webapp/app.py` (Custom SQL Validation tab + `render_custom_sql_section`),
`src/generated_queries/ai_sql_generator.py`, `src/generated_queries/yaml_config_writer.py`,
`src/validation/config_schema.py`, `src/sql_extractor/extractors.py`, `src/rules/`.

Not audited in this session: `src/validate_cli.py`, `Project/main.py` execution path,
CI configuration, test suite coverage of the Custom SQL tab.

## Considered and rejected

- Adding MySQL support: no `mysql` extractor exists anywhere in the codebase. Adding
  one would be a separate, larger effort outside the scope of these two plans.
- Changing `render_custom_sql_section` (Surface B): it already handles "Both sides"
  correctly and saves `.sql` files. Not broken.
- Modifying `src/generated_queries/ai_sql_generator.py`: the AI generator is already
  fully dialect-parameterized. No changes needed there.
