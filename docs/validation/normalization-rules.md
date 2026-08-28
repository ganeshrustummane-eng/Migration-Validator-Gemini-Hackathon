# Normalization Rules

## Purpose

Data validation requires comparing values from heterogeneous databases — PostgreSQL, MSSQL, Athena — against Snowflake. These systems have different:
- Type names for equivalent concepts
- Boolean representations (true/false vs. 1/0 vs. TRUE/FALSE)
- Timestamp formats and timezone handling
- NULL behavior
- String cast syntax

Normalization rules apply dialect-specific SQL transforms so both sides produce identical strings for equal data values.

---

## Type Normalization Table

| Source Type | PostgreSQL SQL | MSSQL SQL | Athena SQL | Snowflake SQL |
|------------|---------------|-----------|------------|---------------|
| `boolean`, `bit` | `CASE WHEN col = true THEN '1' WHEN col = false THEN '0' ELSE NULL END` | `CASE WHEN col = 1 THEN '1' WHEN col = 0 THEN '0' ELSE NULL END` | `CASE WHEN col = true THEN '1' WHEN col = false THEN '0' ELSE NULL END` | `CASE WHEN col = TRUE THEN '1' WHEN col = FALSE THEN '0' ELSE NULL END` |
| `timestamp`, `datetime`, `TIMESTAMP_NTZ` | `TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS')` | `CONVERT(VARCHAR, col, 120)` | `DATE_FORMAT(col, '%Y-%m-%d %H:%i:%s')` | `TO_VARCHAR(col, 'YYYY-MM-DD HH24:MI:SS')` |
| `integer`, `bigint`, `smallint`, `NUMBER` | `CAST(col AS TEXT)` | `CAST(col AS VARCHAR)` | `CAST(col AS VARCHAR)` | `CAST(col AS STRING)` |
| `character varying`, `varchar`, `TEXT` | `CAST(col AS TEXT)` | `CAST(col AS VARCHAR)` | `CAST(col AS VARCHAR)` | `CAST(col AS STRING)` |
| `hstore`, `json`, `jsonb`, `VARIANT` | `CAST(col AS TEXT)` | N/A | N/A | `CAST(col AS STRING)` |
| `uuid` | `CAST(col AS TEXT)` | N/A | N/A | `CAST(col AS STRING)` |
| All types (NULL) | `COALESCE(..., '<<NULL>>')` | `COALESCE(..., '<<NULL>>')` | `COALESCE(..., '<<NULL>>')` | `COALESCE(..., '<<NULL>>')` |

---

## NULL Sentinel

All normalized columns are wrapped in `COALESCE`:

```sql
COALESCE(<normalized_expression>, '<<NULL>>')
```

This converts NULL to the literal string `'<<NULL>>'`, enabling NULL values to participate in row-level string comparison without being dropped from join results.

---

## Primary Key Normalization

Primary key columns are also normalized (to enable the row-level join):

```yaml
pksourcecolumn: id_normalized
pktargetcolumn: ID_normalized
```

The PK is cast to text using the same normalization, then used as the join key in row comparison.

---

## PK Mismatch Handling

When source and target primary keys differ (e.g., source has `[id]`, target has `[ID, _FIVETRAN_START]`):

- The mismatch is recorded in the plan: `pk_mismatch: true`, `pk_mismatch_reason: "..."`
- A warning is included in the validation output
- The validation proceeds using the source PK for joining (Fivetran SCD2 columns are excluded)

---

## Dialect Detection

The `transformation_rule` value in each `ColumnMappingEntry` determines which normalization is applied. The SQL generator selects the appropriate dialect expression based on:

1. `source_type` (from schema extraction)
2. `target_type` (from Snowflake schema)
3. `transformation_rule` assigned by the AI mapping pipeline

---

## Column Name Normalization (for Matching)

Column names are normalized for matching purposes (not for SQL output):

```python
normalized = name.lower().strip().replace(" ", "_")
# "Customer ID" → "customer_id"
# "CREATED_AT"  → "created_at"
# "cust_email"  → "cust_email"
```

This normalized form is stored as `source_normalized` and `target_normalized` in the plan but is not used in the generated SQL (which uses the original column names).
