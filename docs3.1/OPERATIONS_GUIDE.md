# Operations Guide

> **`DIAL_API_KEY` is required.** Column mapping and validation SQL are
> AI-generated with no fallback. Without a key, generation fails with a clear
> message rather than producing unreviewed rule-based output.

## Lint Configs (do this first)

Catches duplicate table keys, missing/misspelled fields, non-SELECT queries, and
`.env` references that resolve to nothing — with no database connection and no
API key.

```powershell
python src\validate_cli.py lint
```

Exit code 0 = clean, 1 = problems found. Safe to run in CI. It also reports
plan/config drift and per-table column coverage.

If lint reports problems, **regenerate** — do not hand-edit. YAML files under
`config/bronze/` are render targets.

## Run the Interactive CLI

From the repository root:

```powershell
python src\\validate_cli.py
```

The CLI provides:

- Connections: test configured databases.
- Single Table: generate SQL and YAML for one table.
- Run Tables: generate SQL and YAML for multiple tables.
- List Tables: discover source and target tables.
- Select AI Model: choose the active model for the session.
- View Rule Book: inspect transformation rules.
- Add Custom Rule: add a learned rule.
- Execute YAML: execute saved source and target queries.

Connection profiles are not part of the normal workflow. Credentials come from `.env`.

## Generate One Table

```powershell
python src\\validate_cli.py generate `
  --source 2 `
  --pg-table Addresses `
  --sf-table ADDRESSES `
  --pg-schema dbo `
  --sf-schema AWSDEV_DEVT5000_DBO `
  --sf-database DEV_SITELINK_BRONZE
```

The source profile number maps to `SRC_2_*` in `.env`.

## Exclude Columns

```powershell
python src\\validate_cli.py generate `
  --source 2 `
  --pg-table Addresses `
  --sf-table ADDRESSES `
  --exclude uTS,GeographyData
```

Interactive workflows display source columns and accept column numbers or names. Excluded columns are removed before mapping and are not written into generated SQL.

### Every run reports what it did not check

Exclusions are never silent. Generation and batch runs both print:

```text
COLUMN COVERAGE — Addresses
----------------------------------------------------------
Validated : 24 / 27 (88.9%)
Excluded  : 3
  ✗ uTS             [timestamp] — rowversion — not comparable
  ✗ GeographyData   [geography] — binary type, excluded by policy
  ✗ LegacyFlag                  — no matching target column
```

Below 80% coverage the run is flagged `LOW COVERAGE` and any PASS is labelled
partial. Treat a high pass rate with low coverage as a warning, not a result.

## Run YAML Validation

Use menu option `[9]`, or execute the Python API:

```python
from src.validation.validation_executor import ValidationExecutor

executor = ValidationExecutor(base_dir=".")
results = executor.execute_batch(
    layer="bronze",
    tables=["all"],
    validation_types=["count_validation", "data_validation"],
    config_dir="config",
)
```

## Test the Whole Framework

Unit and contract tests (no database, no API key):

```powershell
python -m pytest -q
```

Non-live checks:

```powershell
python tests\e2e\run_all_tests.py --skip-live
```

Full live checks:

```powershell
python tests\\e2e\\run_all_tests.py
```

Selected tables:

```powershell
python tests\\e2e\\run_all_tests.py --layer bronze --tables Addresses AcctSoftware
```

Selected validation type:

```powershell
python tests\\e2e\\run_all_tests.py --validation-types count_validation
```

## Output Locations

The contract (read this to learn what a run intended to validate and why a
column was skipped):

```text
output/plans/bronze/<table>.plan.json
```

Generated configuration (render targets — regenerate, never hand-edit):

```text
config/bronze/count_validation/
config/bronze/data_validation/
```

Hand-authored policy (safe to edit):

```text
config/exclusions.yaml
config/database_registry.yaml
```

Runtime reports:

```text
output/bronze/validation_<run_id>/
```

Inside each run:

- `validation_<run_id>.log`
- `count_validation_<run_id>/count_validation_summary.csv`
- `data_validation_<run_id>/data_validation_summary.csv`
- table-specific mismatch CSV files

E2E reports:

```text
output/test_runs/<run_id>/test_report.json
```
