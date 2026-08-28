# Data Schemas

## CanonicalValidationPlan

The core data structure. Stored in `output/plans/<layer>/<table>.plan.json`.

```json
{
  "schema_version": 1,
  "source_database": "fms",
  "source_db_type": "postgresql",
  "source_schema": "public",
  "source_table": "events",
  "target_database": "dev_edge_bronze",
  "target_schema": "storedge_fms_public",
  "target_table": "EVENTS",
  "has_fivetran_active": true,
  "source_primary_keys": ["id"],
  "target_primary_keys": ["ID", "_FIVETRAN_START"],
  "pk_mismatch": true,
  "pk_mismatch_reason": "Source PK ['id'] does not match target PK ['ID', '_FIVETRAN_START']",
  "status": "complete",
  "warnings": [],
  "ambiguities": [],
  "unmatched_source_columns": [],
  "unmatched_target_columns": [],
  "ai_calls_made": 0,
  "model_used": "gpt-4o",
  "generated_by": "ai",
  "generated_at": "2026-08-25T17:00:25.314498",
  "mappings": [ ... ]
}
```

### status values

| Value | Meaning |
|-------|---------|
| `complete` | All source columns matched or intentionally excluded |
| `partial` | Some source columns unmatched |
| `ambiguous` | AI could not resolve some mappings |
| `invalid` | Plan has structural errors |

---

## ColumnMappingEntry

```json
{
  "source_column": "created_at",
  "source_type": "timestamp without time zone",
  "source_normalized": "created_at",
  "target_column": "CREATED_AT",
  "target_type": "TIMESTAMP_NTZ",
  "target_normalized": "created_at",
  "match_method": "exact",
  "fuzzy_score": null,
  "confidence": 1.0,
  "confidence_breakdown": null,
  "transformation_rule": "timestamp",
  "validation_rules": [],
  "reason": "Matched by exact (confidence=1.00)",
  "ai_resolved": false,
  "learned_example_used": false,
  "skip_validation": false,
  "skip_reason": null,
  "is_primary_key": false,
  "pk_ordinal": null,
  "composite_pk_group": null
}
```

### match_method values

| Value | Description |
|-------|-------------|
| `exact` | Case-insensitive direct match |
| `normalized_exact` | Match after snake_case normalization |
| `fuzzy` | RapidFuzz token_sort_ratio |
| `fuzzy_ai` | Fuzzy match confirmed by AI |
| `ai` | AI-resolved from DIAL proxy |
| `configured` | Explicitly mapped in config |
| `skip` | Column intentionally excluded |
| `static` | Matched by static rule (legacy) |

### transformation_rule values

| Value | SQL Applied |
|-------|-------------|
| `text` | `CAST(col AS TEXT)` / `CAST(col AS STRING)` |
| `timestamp` | `TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS')` / `TO_VARCHAR(...)` |
| `boolean` | `CASE WHEN col = true THEN '1' WHEN col = false THEN '0' ELSE NULL END` |
| `integer` | `CAST(col AS TEXT)` |
| `json` | `col::text` / `col::STRING` |

---

## ApprovalRecord

```json
{
  "id": "uuid-abc-123",
  "entity_type": "mapping",
  "table": "customer",
  "source_column": "cust_id",
  "target_column": "CUSTOMER_ID",
  "confidence": 0.82,
  "match_method": "fuzzy",
  "transformation_rule": "text",
  "ai_recommendation": "High probability match based on naming convention",
  "reason": null,
  "status": "pending",
  "decided_by": null,
  "decided_at": null,
  "modified_target": null,
  "modified_rule": null,
  "rejection_reason": null,
  "metadata": {},
  "created_at": "2026-08-25T17:00:00Z"
}
```

### status values

| Value | Meaning |
|-------|---------|
| `pending` | Awaiting human review |
| `approved` | Human approved |
| `rejected` | Human rejected |
| `modified` | Human modified target/rule |
| `auto_accepted` | Confidence ≥ 0.95, accepted automatically |
| `excluded` | Column excluded from validation |

---

## AuditRecord

```json
{
  "audit_id": "uuid-xyz",
  "action": "approve_mapping",
  "entity_type": "mapping",
  "entity_id": "mapping/customer/cust_id",
  "actor": "jane.doe@company.com",
  "user_id": "user-456",
  "timestamp": "2026-08-25T17:10:00.000000Z",
  "request_id": "uuid-req",
  "previous": { "status": "pending" },
  "new_state": { "status": "approved", "decided_by": "jane.doe@company.com" },
  "reason": "Confirmed via source system DDL review",
  "plan_version": 1,
  "source_system": "postgresql",
  "table": "customer",
  "column": "cust_id",
  "rule_id": null,
  "run_id": null,
  "metadata": {}
}
```

---

## RunMetrics

```json
{
  "run_id": "uuid-run",
  "operation": "generate_validation_plan",
  "table": "customer",
  "source_type": "postgresql",
  "timestamp": "2026-08-25T17:00:00Z",
  "tables_processed": 1,
  "columns_processed": 42,
  "mappings_automated": 40,
  "mappings_reviewed": 2,
  "mappings_rejected": 0,
  "validations_run": 1,
  "pass_count": 41,
  "fail_count": 0,
  "warning_count": 1,
  "failures_detected": 0,
  "coverage_pct": 95.2,
  "manual_sql_avoided": 4,
  "execution_time_s": 8.3,
  "human_review_time_s": 0,
  "ai_token_usage": 1250,
  "ai_calls_made": 2,
  "metadata": {}
}
```

---

## Validation YAML (Generated Config)

### Count Validation (`Project/config/<layer>/count_validation/<layer>.yaml`)

```yaml
tables:
  customer:
    validations:
      count_validation:
        source_table_name: customer
        source: postgresql
        sourcequery: |
          SELECT COUNT(*) AS source_row_count
          FROM public.customer;
        target_table_name: CUSTOMER
        target: snowflake
        targetquery: |
          SELECT COUNT(*) AS target_row_count
          FROM DEV_SITELINK_BRONZE.PUBLIC.CUSTOMER
          WHERE _FIVETRAN_ACTIVE = TRUE;
```

### Data Validation (`Project/config/<layer>/data_validation/<table>.yaml`)

```yaml
tables:
  customer:
    validations:
      data_validation:
        pksourcecolumn: id_normalized
        pktargetcolumn: ID_normalized
        source_table_name: customer
        source: postgresql
        sourcequery: |
          SELECT
            CAST(id AS TEXT) AS id_normalized,
            COALESCE(CAST(email AS TEXT), '<<NULL>>') AS email_normalized,
            COALESCE(
              CASE WHEN active = true THEN '1'
                   WHEN active = false THEN '0'
                   ELSE NULL END,
            '<<NULL>>') AS active_normalized
          FROM public.customer
          ORDER BY id;
        target_table_name: CUSTOMER
        target: snowflake
        targetquery: |
          SELECT
            CAST(ID AS STRING) AS id_normalized,
            COALESCE(CAST(EMAIL AS STRING), '<<NULL>>') AS email_normalized,
            COALESCE(
              CASE WHEN ACTIVE = TRUE THEN '1'
                   WHEN ACTIVE = FALSE THEN '0'
                   ELSE NULL END,
            '<<NULL>>') AS active_normalized
          FROM DEV_SITELINK_BRONZE.PUBLIC.CUSTOMER
          WHERE _FIVETRAN_ACTIVE = TRUE
          ORDER BY ID;
```
