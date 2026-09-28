# 0040. All test files live in one top-level `tests/` folder

**Status:** Accepted — implemented
**Date:** 2026-09-28

## Context

The 18 test files (plus one `conftest.py`) were scattered next to the code
they test, across six folders: `Project/`, `Project/db/`, `Project/utils/`,
`src/generated_queries/`, `src/silver/`, `src/validation/`. That has two costs:

- **Presentation.** This framework is being shown to leadership and will be
  used by the quality team. A source folder where a third of the files are
  `test_*.py` reads as noise, and it hides what the product actually is.
- **Running them.** There was no single command. `Project` tests ran from the
  repo root; `src` tests only passed when run from *inside* `src/` (from the
  root, `test_sql_query_generator.py` failed to collect with
  `ModuleNotFoundError: generated_queries`).

## Decision

Every test file lives under `tests/`, split by which tree it tests:

| Folder | Tests code in | `conftest.py` puts on `sys.path` |
|---|---|---|
| `tests/project/` | `Project/` (the validation engine, incl. `db/` and `utils/`) | `Project/`, `Project/utils/` |
| `tests/src/` | `src/` (YAML/SQL generation, Silver, config schema) | `src/` |

Run everything with one command from the repo root:

```
python -m pytest tests -q
```

No test files go back into `Project/`, `src/`, or `webapp/`. New tests go into
the matching `tests/<tree>/` folder, and the name must be unique across all of
`tests/`: pytest's default import mode fails on two files with the same
basename.

Old path → new path is mechanical: `Project/**/test_x.py` → `tests/project/test_x.py`,
`src/**/test_x.py` → `tests/src/test_x.py`. Older ADRs (0002–0037) and
`docs/large-table-scalable-architecture/README.md` still name the old paths.
They are historical records and were deliberately not rewritten. Read them
through this mapping.

## Alternatives considered

- **One flat `tests/` with one `conftest.py`.** Rejected: `src/utils/` and
  `Project/utils/` are both importable as `utils`, so one shared `sys.path`
  would make one of them silently shadow the other.
- **A folder named `test/` (singular).** Rejected: `test` is a Python standard
  library package name. `tests/` is the Python convention and can't shadow it.
- **Mirror the full source tree (`tests/project/db/`, `tests/project/utils/`, ...).**
  Rejected: that adds more folders to look at, and every basename was already unique.
- **Remove the per-file `sys.path.insert` lines and rely only on `conftest.py`.**
  Rejected for now: most test files also run as plain scripts
  (`python tests/project/test_x.py`), and their docstrings document that.

## Consequences

- `python -m pytest tests -q` runs the whole suite in one process: 162 passed
  at the time of the move (135 `project` + 27 `src`, the same counts as before).
- `test_snowflake.py` no longer needs its hack that stripped the test's own
  directory from `sys.path`. That hack existed only because the test sat next to
  `Project/db/snowflake.py`, which shadows the real `snowflake` package.
  Rule that still holds: never put `Project/db/` itself on `sys.path`.
- Pre-existing, not caused by this move: `tests/src/test_sql_query_generator.py`
  and `tests/src/test_coalesce_plan_builder.py` fail when run as plain scripts,
  because `src/generated_queries/yaml_config_writer.py:73` imports
  `Project.utils.environments`, which needs the repo root on `sys.path`. They
  pass under pytest. They failed the same way before the move.
- `.claude/settings.json` has allow-list entries that still name the old test
  paths. Those are permission rules and were left for the user to update.

## Other clutter that could be removed (not done — needs a decision)

Found while doing this. Listed with evidence. None of it was deleted.

| Item | What it is | Suggestion |
|---|---|---|
| `fixture_table_data_validation_result_test.csv` (repo root, tracked) | A stray output of `test_tiered_runner.py` from a run whose working directory wasn't its temp dir. The tests write this file into a `tempfile` directory. | Delete. |
| `graphify-out/` (209 tracked files) | Generated knowledge-graph output from the `graphify` tool, and the largest tracked folder in the repo. | Untrack it and add it to `.gitignore`. It can be regenerated at any time. |
| `.dial_model_cache.json` (tracked) | Runtime cache written by `src/model_probe.py` (`_CACHE_FILE`). | Untrack it and add it to `.gitignore`. |
| `token_usage_analysis/logs/token_usage.jsonl` (tracked, modified on every run) | Log output. | Untrack the log and keep `logs/.gitkeep`. |
| `.pytest_cache/`, `Project/.pytest_cache/`, `src/.pytest_cache/` | pytest cache (untracked, but visible in the tree). | Delete. `.pytest_cache/` has been added to `.gitignore`. |
| `plans/` (root, 5 `.md` files) | Old improvement plans (001–004). Unrelated to `output/plans/`, which is live and read by `src/core/plan_store.py` and `webapp/app.py`. | Move them into `docs/` so the root holds only the product. |
| `src/trash/`, `src/.codemie/` (untracked) | Local scratch inside the source tree, on top of the top-level `trash/` and `.codemie/`. | Merge them into the top-level `trash/`, or delete them. |
| `.codemie/virtual_assistants/*.yaml` (8 tracked) | Config for the CodeMie tool, not part of the validator. | Keep it if the team uses CodeMie. Otherwise untrack it. |
