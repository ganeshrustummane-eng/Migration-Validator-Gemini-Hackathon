# Migration Validator — Web UI

A thin Streamlit UI over the existing `validate_cli.py` logic. It doesn't
reimplement connection/matching/generation logic — it imports and calls the
exact same functions the CLI uses, so behavior stays identical in both
places and any future fix in `validate_cli.py` is automatically reflected here.

## Why

The interactive terminal flow for a single table is a chain of ~8 sequential
prompts (pick source → pick database → pick schema → pick table → pick
Snowflake table → exclude columns y/n → change model y/n → confirm → pick
layer). This UI collapses that into one page: pick a connection (dropdown,
populated from `.env` — zero extra clicks to "connect"), then pick **both
sides live**:

- **Source**: database → schema → table, each a dropdown discovered live
  from the actual server using the credentials already in `.env` (same
  discovery calls the setup wizard uses — `_discover_postgres_databases`,
  `_discover_mssql_schemas`, etc.). Athena has no separate database/schema
  step since its "database" is a fixed Glue Data Catalog database.
- **Target (Snowflake)**: database → schema → table, same pattern, via
  `SHOW DATABASES`/`SHOW SCHEMAS IN DATABASE`, with the Snowflake table name
  auto-suggested as `SOURCE_TABLE.upper()`.

Every dropdown also has a "✏️ Type manually…" option, so a failed discovery
call (bad driver, permissions, or a table that doesn't exist yet) never
becomes a dead end — it falls back to a text box pre-filled with the `.env`
default.

## Running it

From the project root, with the virtual environment active and dependencies
installed (`streamlit` is now in `requirements.txt`):

```bash
streamlit run webapp/app.py
```

This opens `http://localhost:8501` in your browser. `.env` is loaded the same
way `validate_cli.py` loads it — no separate configuration step.

## What's covered

| Tab | Wraps |
|---|---|
| 🔌 Connections | `setup_wizard.print_connection_registry()` + a one-click "Test all connections" using the same extractors the CLI uses |
| ▶️ Generate — Single Table | `ValidationPipeline.run()` — same as menu `[1]` / `generate` |
| 📋 Generate — Batch | `ValidationPipeline.run()` looped per table — same as menu `[2]` / `multi`. Source database/schema and the table list are all live dropdowns (multi-select); Snowflake database/schema are live dropdowns too. Simplified: uses `table.upper()` as each Snowflake table name; use the CLI's `batch --config tables.yaml` for per-table overrides like composite primary keys or `explicit_mappings` |
| 📖 Rule Book | `rule_book.stats()` / `.base_rules()` / `.learned_rules()` / `.save_learned_rule()` — same as menu `[5]`/`[6]` |
| 🚫 Exclusions | `_get_all_exclusions()` / `_save_global_user_exclusion()` per source type — same as menu `[E]` |

## What's intentionally not in the UI yet

- `lint`, `list-models`, connection **profiles** (profiles are already
  disabled/deprecated in the CLI itself — credentials come from `.env` only).
- Composite primary keys / per-table `explicit_mappings` in batch mode — the
  UI's batch tab is for the common case (single-column PK inferred, same
  exclusions for every table); use `validate_cli.py batch --config
  tables.yaml` directly for anything needing per-table overrides.

## Notes

- The UI runs in the same Python process as your terminal — AI calls still
  go through the same `DIAL_API_KEY`/`CLAUDE_API_KEY` from `.env`, and are
  still logged to `token_usage_analysis/logs/token_usage.jsonl` like any
  other run.
- Progress/log lines that the pipeline prints (`print(...)`) go to the
  terminal Streamlit was launched from, not the browser — the UI shows the
  final result (coverage headline, output file paths, pass/fail), not the
  live step-by-step console output.
