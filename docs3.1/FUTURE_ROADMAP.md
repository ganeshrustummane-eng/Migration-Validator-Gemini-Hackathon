# Future Roadmap

## Near-Term Improvements

### 1. Add automated tests for generated SQL

Add fixture-based tests for every supported source dialect and data type combination:

- MSSQL integer, decimal, bit, datetime, uniqueidentifier, binary
- PostgreSQL JSONB, HStore, bytea, timestamp with timezone
- Snowflake VARIANT, BOOLEAN, NUMBER, TIMESTAMP types

### 2. Improve mismatch detail

Extend mismatch reports to include:

- Changed columns for common keys
- Source value and target value
- Normalization rule used
- Difference category
- Suggested remediation

### 3. Add SQL parser validation

The current validator uses deterministic pattern checks. Add a dialect-aware parser or database `EXPLAIN`/compile mode where available before executing large queries.

### 4. Improve retry and timeout handling

Add configurable retries for transient network failures and separate connection/query timeouts in the report.

### 5. Add CI mode

Support a non-interactive command that returns:

- Exit code `0` when infrastructure passes and no data findings exist
- Exit code `2` when data mismatches exist
- Exit code `1` when infrastructure or configuration errors occur

## Medium-Term Improvements

### 6. Parallel table validation

Use bounded worker pools for independent tables while preventing connection exhaustion.

### 7. Incremental validation

Support partition filters, watermark columns, date ranges, and CDC windows for large tables.

### 8. Data sampling and scalability

Add sampling, chunked comparison, hash comparison, and configurable full-scan modes.

### 9. Better schema drift detection

Report added, removed, renamed, and type-changed columns before generating SQL.

### 10. Rule governance

Add versioning, approval status, owner, effective date, and audit history for custom rules.

### 11. Secure secret management

Support Azure Key Vault, AWS Secrets Manager, or another approved secret provider while retaining `.env` for local development.

## Long-Term Improvements

### 12. Web dashboard

Expose run history, pass/fail trends, mismatch details, and downloadable reports.

### 13. Data-quality baseline history

Store historical counts, null rates, distinct counts, and distributions for trend analysis.

### 14. More source systems

Add Oracle, MySQL, Databricks, BigQuery, and additional lakehouse connectors through the existing adapter and dialect contracts.

### 15. AI feedback loop

Use confirmed user corrections to improve mapping and SQL recommendations while maintaining deterministic safety validation.

## Extension Rules

Every new feature should include:

1. Implementation code
2. Configuration example
3. Unit or focused test
4. E2E orchestrator stage where appropriate
5. Output/report behavior
6. Documentation update in `docs3.1`
