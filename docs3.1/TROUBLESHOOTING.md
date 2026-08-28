# Troubleshooting

## "DIAL_API_KEY is not set"

Generation is AI-only. This is not a degraded mode you can work around — there is
no rule-based fallback, by design.

```text
AISQLGenerationError: DIAL_API_KEY is not set. Validation SQL is AI-generated only
AIRuleMappingError:   DIAL_API_KEY is not set — cannot map columns for 'Addresses'
```

Fix: set `DIAL_API_KEY` in `.env` and confirm VPN access to the DIAL endpoint.

The fallback was removed because its output was indistinguishable downstream from
reviewed AI output, so a missing key silently downgraded correctness while the
run still reported success.

Work that does **not** need a key: `pytest`, `validate_cli.py lint`, and
`run_all_tests.py --skip-live`.

## "AI could not produce valid SQL after 3 attempts"

The model produced SQL that failed dialect validation three times; the failures
from each attempt are listed in the error. Common causes:

- An exotic source type with no clean text cast — exclude it in
  `config/exclusions.yaml` with a recorded reason.
- A model that is weak at the target dialect — retry with `--model gpt-4o`.

Do not work around this by hand-writing the query into the YAML. The YAML is a
render target and will be overwritten on the next generation.

## Lint Failures

```powershell
python src\validate_cli.py lint
```

| Message | Meaning | Fix |
|---|---|---|
| `table 'X' is defined more than once` | Duplicate key; YAML keeps only the last | Regenerate |
| `sourcequery — Field required` | Misspelled or missing key | Regenerate |
| `query must be a SELECT statement` | Non-SELECT in a validation block | Regenerate |
| `no 'SRC_N_*' variables are defined` | `.env` renamed or missing | Fix `.env` |
| `plan exists but <table>.yaml is missing` | Config deleted after generation | Regenerate |

Configs are generated artefacts. Fix by regenerating, not by editing.

## Coverage Reported as UNKNOWN

```text
No canonical plan found for 'Addresses' — column coverage is UNKNOWN.
```

The YAML exists but `output/plans/bronze/addresses.plan.json` does not, so there
is no record of which columns were excluded or why. Regenerate the table.

Unknown coverage is deliberately **not** treated as full coverage.

## LOW COVERAGE Warning

```text
!! LOW COVERAGE — under 80.0% of columns were compared. Treat any PASS as partial.
```

The run passed, but on a minority of columns. Read the excluded list in the
report or the plan JSON and confirm each exclusion is intentional. Exclusions are
the easiest way to turn a validator into a rubber stamp.

## UnicodeEncodeError on Windows

Fixed — the CLI now forces UTF-8 on stdout/stderr. If you still see it in your own
scripts, set:

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

## Dependencies Missing

Run:

```powershell
python -m pip install -r requirements.txt
```

On Python 3.13/3.14, install with `--only-binary=:all:` to fail fast instead of
attempting a source build of the database drivers:

```powershell
python -m pip install --only-binary=:all: -r requirements.txt
```

Then run:

```powershell
python tests\e2e\run_all_tests.py --skip-live
```

## Wrong Python Interpreter

The project may have multiple Python installations. Use the same interpreter for dependencies and tests. The E2E runner searches available interpreters for pytest.

Check the interpreter:

```powershell
python -c "import sys; print(sys.executable)"
```

## Connection Failure

Run:

```powershell
python test_env_connections.py
```

Check the relevant `.env` values and verify the database server, port, driver, schema, and network/VPN access.

## MSSQL Pre-Login Failure

Confirm:

- Microsoft ODBC Driver 18 is installed.
- Host and port are reachable.
- Authentication type is correct.
- `SRC_2_AUTH` is correct.
- The server supports the configured encryption mode.

The adapter uses `Encrypt=optional` and `TrustServerCertificate=yes` for the current environment.

## YAML Not Listed

Place YAML under:

```text
config/bronze/count_validation/
config/bronze/data_validation/
```

Confirm the extension is `.yaml` and the file contains a `tables:` block.

## YAML Source Uses Wrong Database

Ensure the validation block contains:

```yaml
source: mssql
```

When `source_name` is absent, the factory maps the source type to its default `SRC_N` profile.

## Data Validation Key Error

Confirm `pksourcecolumn` and `pktargetcolumn` exist in the query output. Normalized aliases such as `id_normalized` are supported.

## Generated SQL Fails

Check the source dialect. MSSQL queries must not contain PostgreSQL syntax. Run:

```powershell
python src\validate_cli.py lint
python tests\e2e\run_all_tests.py --skip-live
```

Dialect violations are caught at generation time and fed back to the model, so
SQL that reaches a YAML file has already passed those checks. If it still fails
at execution, the mismatch is semantic (wrong column, wrong table) rather than
syntactic — compare the plan JSON against the actual schema.

## No CSV Output

Find the run ID in the console or log and inspect:

```text
output/<layer>/validation_<run_id>/
```

## FAIL Versus ERROR

- `FAIL`: source and target ran, but data differs.
- `ERROR`: the validation could not execute.

A FAIL should be investigated as a migration/data-quality finding. An ERROR should be investigated as infrastructure, configuration, SQL, or framework behavior.

Always read the coverage line alongside either. A PASS on 40% of columns and a
PASS on 100% of columns are not the same result.

## My Validation Always Passes — Should I Trust It?

Not until you have proven it can fail. Copy a table, corrupt one value and delete
one row, then re-run and confirm the mismatch is reported against the correct
primary key and column. A validator that has never failed has not been tested.
