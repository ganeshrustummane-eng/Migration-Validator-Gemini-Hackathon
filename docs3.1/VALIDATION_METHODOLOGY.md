# Validation Methodology

## Coverage Comes First

A result is meaningless without knowing what it covered. Every run reports both:

```text
data_validation Addresses: PASS
24 of 27 columns validated (88.9%) — 3 excluded: uTS (rowversion — not
comparable), GeographyData (binary type), LegacyFlag (no matching target column)
```

Read them together, always:

| Pass rate | Coverage | Interpretation |
|---|---|---|
| PASS | 100% | The table is validated. |
| PASS | 88% | The compared columns match; three were never checked. |
| PASS | 40% | Flagged `LOW COVERAGE`. This is not a validated table. |
| PASS | UNKNOWN | No plan exists. Coverage is unproven — regenerate. |

Exclusions are the easiest way to turn a validator into a rubber stamp, so:

- every excluded column carries a recorded reason
- coverage below 80% is flagged and any PASS is labelled partial
- a table with no plan reports UNKNOWN coverage, never full coverage

## Result Meaning

### PASS

The source and target queries executed successfully and the compared results matched.

### FAIL

The source and target queries executed successfully, but the data differs. This is a migration finding, not a framework crash.

### ERROR

A connection, query, configuration, adapter, or comparison step could not execute.

## Count Validation

Count validation compares:

```text
source_count == target_count
```

A difference indicates possible data loss, filtering differences, duplicate loading, or an incorrect source/target query.

## Data Validation

Data validation:

1. Executes normalized source and target queries.
2. Identifies the source and target primary-key columns.
3. Resolves keys case-insensitively.
4. Supports normalized aliases such as `ID_normalized`.
5. Detects rows missing in either side.
6. Compares common rows.
7. Writes a mismatch CSV.

## Normalization

Rules can normalize:

- NULL values using `<<NULL>>`
- Whitespace using `TRIM` or `LTRIM/RTRIM`
- Booleans using `1` and `0`
- Numerics using controlled rounding
- Dates and timestamps using database-specific formatting
- UUID values using uppercase trimmed text
- JSON using canonical key ordering
- HStore using canonical JSON-like representation
- Binary values using hexadecimal text

## Dialect Rules

Source SQL must use the source database dialect.

MSSQL must not use PostgreSQL-only constructs such as `::type`, `AS TEXT`, `TO_CHAR`, `JSONB`, or `encode()`.

PostgreSQL may use PostgreSQL-specific casts, JSONB, timezone, and encoding functions.

### Enforcement

Dialect rules are enforced at generation time, not at execution time. AI output is
checked for forbidden constructs, the `<<NULL>>` sentinel, required aliases, and
missing commas. Failures are fed back to the model for up to three attempts; if
the last attempt still fails, generation raises and no YAML is written.

There is no fallback to rule-based SQL. Generation either produces validated SQL
or it fails — it never produces SQL of unknown quality.

### Alias alignment

Both sides of a comparison are AI-generated from the same plan and required to
use the identical alias, always derived from the **source** column name:

```text
source:  ... AS AcctSoftwareID_normalized
target:  ... AS "AcctSoftwareID_normalized"
```

This is what guarantees that a renamed target column is compared against the
correct source column rather than lining up by accident.

Snowflake target queries use Snowflake functions and preserve `_FIVETRAN_ACTIVE = TRUE` filtering when the target exposes that column.

## Dynamic Suite Checks

### NULL percentage

Calculates the percentage of rows with NULL values.

### Distinct count

Counts unique values for the selected column.

### MIN/MAX and SUM

Used for numeric, financial, quantity, and temporal profiles where applicable.

### Duplicate check

Groups by business-key columns and returns groups with `COUNT(*) > 1`.

### VALUE_DIST

Generates a separate grouped query:

```sql
SELECT
    value,
    COUNT(*) AS value_count
FROM table
GROUP BY value
ORDER BY value_count DESC;
```

It is not incorrectly represented as a distinct-count field in a scalar aggregate query.

## Interpreting Findings

When a validation fails:

1. Check the count summary.
2. Check the mismatch CSV.
3. Confirm source and target filters.
4. Confirm the primary-key mapping.
5. Confirm excluded columns.
6. Inspect normalized values for type or formatting differences.
7. Decide whether the finding is a migration defect, expected transformation, or configuration issue.
