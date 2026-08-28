# Rule Examples

## Normalization Rule Examples

### Boolean → String

**Problem:** PostgreSQL `true`/`false`, MSSQL `1`/`0`, and Snowflake `TRUE`/`FALSE` are dialects that don't compare as equal strings.

**Solution:**

```sql
-- PostgreSQL source
CASE WHEN active = true THEN '1'
     WHEN active = false THEN '0'
     ELSE NULL END AS active_normalized

-- Snowflake target
CASE WHEN ACTIVE = TRUE THEN '1'
     WHEN ACTIVE = FALSE THEN '0'
     ELSE NULL END AS active_normalized
```

---

### Timestamp → Normalized String

**Problem:** Timestamps include microseconds and timezone offsets that differ between systems.

**Solution:**

```sql
-- PostgreSQL source
TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at_normalized

-- Snowflake target
TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS created_at_normalized
```

Truncates to second precision — sub-second differences are not validated.

---

### NULL Sentinel

**Problem:** `NULL` values in one system but not the other cause joins to silently drop rows in comparison.

**Solution:**

```sql
COALESCE(CAST(email AS TEXT), '<<NULL>>') AS email_normalized
```

NULL becomes the literal string `'<<NULL>>'` so it participates in row comparison.

---

### hstore → text

**Problem:** PostgreSQL `hstore` is a key-value extension type with no Snowflake equivalent (stored as VARIANT).

**Solution:**

```sql
-- PostgreSQL source
CAST(metadata AS TEXT) AS metadata_normalized

-- Snowflake target (VARIANT stored as OBJECT)
CAST(METADATA AS STRING) AS metadata_normalized
```

---

### UUID → text

**Problem:** PostgreSQL has a native `uuid` type; Snowflake stores UUIDs as `TEXT`.

**Solution:**

```sql
-- PostgreSQL source
CAST(id AS TEXT) AS id_normalized

-- Snowflake target
CAST(ID AS STRING) AS id_normalized
```

---

## Exclusion Rule Examples

### Fivetran Pattern Exclusion

```yaml
pattern_exclusions:
  patterns:
    - pattern: "^_FIVETRAN_.*"
      reason: "Fivetran internal metadata columns"
      applies_to: [source, target]
```

Matches: `_FIVETRAN_SYNCED`, `_FIVETRAN_DELETED`, `_FIVETRAN_START`, etc.

---

### MSSQL rowversion (UTS) Exclusion

MSSQL `rowversion` (also called `timestamp`) is a binary sequence number with no semantic data value. It cannot be meaningfully validated.

```yaml
global_exclusions:
  columns:
    - column_name: UTS
      reason: "SQL Server rowversion — binary sequence, not suitable for validation"
      applies_to: [source]
```

---

### User-Added Exclusion Example

Engineer discovers a staging column that is not loaded to Snowflake:

```bash
python -m src.validate_cli
# Select: 6 (Add Exclusion)
# DB type: postgresql
# Column: staging_flag
# Reason: Staging-only column, not present in Snowflake target
# Scope: G (global, persist to YAML)
```

Result in `config/postgresql_exclusions.yaml`:
```yaml
user_global_exclusions:
  - column_name: staging_flag
    reason: "Staging-only column, not present in Snowflake target"
    applies_to: [source, target]
    added_by: CLI
    date_added: "2026-08-26"
```
