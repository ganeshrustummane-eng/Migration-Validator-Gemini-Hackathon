# Bi-Directional Column Exclusion Guide

## Overview

This guide explains how to handle **target-only columns** — columns that exist in the target database but not in the source — which are common in migration scenarios.

## The Problem

### Real-World Scenario

```text
Source Table: customers (PostgreSQL)
┌─────────────────┬──────────────┐
│ Column          │ Type         │
├─────────────────┼──────────────┤
│ customer_id     │ bigint       │
│ first_name      │ varchar      │
│ last_name       │ varchar      │
│ email           │ varchar      │
│ created_at      │ timestamp    │
└─────────────────┴──────────────┘
5 columns

Target Table: CUSTOMERS (Snowflake)
┌─────────────────────────┬──────────────┐
│ Column                  │ Type         │
├─────────────────────────┼──────────────┤
│ CUSTOMER_ID             │ NUMBER       │
│ FIRST_NAME              │ VARCHAR      │
│ LAST_NAME               │ VARCHAR      │
│ EMAIL                   │ VARCHAR      │
│ CREATED_AT              │ TIMESTAMP_NTZ│
│ _FIVETRAN_SYNCED        │ TIMESTAMP_NTZ│ ← EXTRA
│ MIGRATED_AT             │ TIMESTAMP_NTZ│ ← EXTRA
│ MIGRATED_BY             │ VARCHAR      │ ← EXTRA
│ DATA_QUALITY_SCORE      │ NUMBER(3,2)  │ ← EXTRA
│ FULL_NAME               │ VARCHAR      │ ← EXTRA (derived)
└─────────────────────────┴──────────────┘
10 columns (5 extra)
```

### Previous Behavior

**Problem**: The validator only reported:

```text
✓ PASS: 5 columns validated
```

**Issue**: This gives a false sense of completeness. It doesn't show that the target has 5 additional columns that were never validated.

### New Behavior

**Solution**: The enhanced validator reports:

```text
✓ PASS: 5 of 10 target columns validated (50% target coverage)
      5 columns matched and validated
      5 target-only columns excluded:
        - _FIVETRAN_SYNCED: Fivetran metadata
        - MIGRATED_AT: Migration audit column
        - MIGRATED_BY: Migration audit column
        - DATA_QUALITY_SCORE: Data enrichment
        - FULL_NAME: Derived from first_name + last_name
```

## Common Target-Only Column Types

### 1. **Fivetran Metadata Columns**

Automatically added by Fivetran during ingestion:

```yaml
- _FIVETRAN_DELETED
- _FIVETRAN_SYNCED
- _FIVETRAN_ID
- _FIVETRAN_INDEX
```

**Auto-detected**: Yes (pattern: `^_FIVETRAN_.*`)

### 2. **Migration Audit Columns**

Added to track migration metadata:

```yaml
- migrated_at
- migrated_by
- migration_batch_id
- etl_loaded_at
- created_by_etl
- updated_by_pipeline
```

**Auto-detected**: Yes (patterns: `migrated_*`, `etl_*`, `created_by`, etc.)

### 3. **Data Enrichment Columns**

Added during or after migration to enrich data:

```yaml
- data_quality_score
- confidence_score
- segment_type
- risk_category
- customer_tier
```

**Auto-detected**: Yes (patterns: `*_score`, `segment_*`, `*_category`, etc.)

### 4. **Derived/Computed Columns**

Computed from existing source columns:

```yaml
- full_name           # from first_name + last_name
- age                 # computed from date_of_birth
- account_age_days    # computed from created_at
- display_name        # formatted name
```

**Auto-detected**: Yes (patterns: `full_*`, `calculated_*`, `derived_*`, etc.)

### 5. **Custom Business Logic Columns**

Added by business requirements:

```yaml
- is_vip_customer
- preferred_contact_method
- marketing_opt_in
- gdpr_consent_date
```

**Auto-detected**: No (must be manually configured)

## Using the Bi-Directional Exclusion Handler

### Basic Analysis (No AI Required)

```python
from exclusions.bidirectional_exclusion_handler import BiDirectionalExclusionHandler
from sql_extractor.extractors import ColumnMetadata

# Initialize handler
handler = BiDirectionalExclusionHandler()

# Analyze schemas
result = handler.analyze_schemas(
    source_columns=source_cols,
    target_columns=target_cols,
    table_name="customers",
    source_database="postgresql",
)

# Print summary
print(result.summary())
```

**Output:**

```text
Bi-Directional Schema Analysis: customers
======================================================================

Column Counts:
  Source columns:  5
  Target columns:  10
  Total unique:    10
  Matched pairs:   5

Coverage:
  Source validated:  5/5 (100.0%)
  Target validated:  5/10 (50.0%)
  Overall:           5/10 (50.0%)

Target-Only Columns (5):
  • _FIVETRAN_SYNCED (TIMESTAMP_NTZ)
    Reason: Fivetran metadata column — not present in source
    Category: Fivetran Metadata

  • MIGRATED_AT (TIMESTAMP_NTZ)
    Reason: Audit/tracking column added during migration
    Category: Audit Column

  • MIGRATED_BY (VARCHAR)
    Reason: Audit/tracking column added during migration
    Category: Audit Column

  • DATA_QUALITY_SCORE (NUMBER(3,2))
    Reason: Data enrichment column added post-migration
    Category: Data Enrichment

  • FULL_NAME (VARCHAR)
    Reason: Derived/computed column (e.g., full_name from first_name+last_name)
    Category: Derived/Computed
```

### Enhanced AI Mapping with Coverage

```python
from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper

# Initialize enhanced mapper
mapper = EnhancedAIRuleMapper(model="gpt-4o-mini")

# Map columns with coverage analysis
result = mapper.map_columns_with_coverage(
    source_columns=source_cols,
    target_columns=target_cols,
    primary_key_hints=["customer_id"],
    table_name="customers",
    source_database="postgresql",
)

# Print comprehensive report
print(result.summary_report())

# Export to file
mapper.export_coverage_report(
    result=result,
    output_path="output/customers_coverage_report.txt",
)
```

**Output:**

```text
================================================================================
ENHANCED COLUMN MAPPING REPORT
================================================================================

Table: customers

Coverage Metrics:
  • Source columns:     5
  • Target columns:     10
  • Matched & validated: 5 pairs
  • Source coverage:    100.0%
  • Target coverage:    50.0%

⚠️  STATUS: SCHEMA DRIFT DETECTED — Review exclusions

Warnings:
  ⚠️  5 columns exist in target but not in source — possible migration enrichment or schema drift

Target-Only Columns (5):
  (These columns exist in target but not in source)
  • _FIVETRAN_SYNCED (TIMESTAMP_NTZ)
    Category: Fivetran Metadata
    Reason: Fivetran metadata column — not present in source
  • MIGRATED_AT (TIMESTAMP_NTZ)
    Category: Audit Column
    Reason: Audit/tracking column added during migration
  • MIGRATED_BY (VARCHAR)
    Category: Audit Column
    Reason: Audit/tracking column added during migration
  • DATA_QUALITY_SCORE (NUMBER(3,2))
    Category: Data Enrichment
    Reason: Data enrichment column added post-migration
  • FULL_NAME (VARCHAR)
    Category: Derived/Computed
    Reason: Derived/computed column

Recommendations:
  💡 Document 2 audit columns added during migration in your migration spec
  💡 Document 1 enrichment columns in your data transformation guide
  💡 Run: python validate_cli.py generate-exclusions --table customers to auto-generate exclusion config

================================================================================
```

## Auto-Generating Exclusion Configs

The handler can automatically generate YAML exclusion configurations:

```python
yaml_config = handler.generate_exclusion_config(
    result=result,
    source_database="postgresql",
    target_database="snowflake",
)

print(yaml_config)
```

**Generated YAML:**

```yaml
# Auto-generated exclusions for customers
# Generated by BiDirectionalExclusionHandler
#
# Source coverage: 100.0%
# Target coverage: 50.0%

customers:
  source_database: postgresql
  target_database: snowflake
  exclusions:
    - column_name: _FIVETRAN_SYNCED
      reason: "Fivetran metadata column — not present in source"
      applies_to: ["target"]
      category: "Fivetran Metadata"

    - column_name: MIGRATED_AT
      reason: "Audit/tracking column added during migration"
      applies_to: ["target"]
      category: "Audit Column"

    - column_name: MIGRATED_BY
      reason: "Audit/tracking column added during migration"
      applies_to: ["target"]
      category: "Audit Column"

    - column_name: DATA_QUALITY_SCORE
      reason: "Data enrichment column added post-migration"
      applies_to: ["target"]
      category: "Data Enrichment"

    - column_name: FULL_NAME
      reason: "Derived/computed column"
      applies_to: ["target"]
      category: "Derived/Computed"
```

## Integration with Existing Pipeline

### Step 1: Update Orchestrator

Modify `ai_transformation/orchestrator.py` to use the enhanced mapper:

```python
from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper

class RuleMapperOrchestrator:
    def __init__(self, model: Optional[str] = None):
        self._ai_mapper = EnhancedAIRuleMapper(model=model)
    
    def map_columns(self, source_columns, target_columns, **kwargs):
        # Use enhanced mapping with coverage
        result = self._ai_mapper.map_columns_with_coverage(
            source_columns=source_columns,
            target_columns=target_columns,
            **kwargs
        )
        
        # Log warnings
        for warning in result.warnings:
            print(f"  ⚠️  {warning}")
        
        # Return mappings and explanation (backward compatible)
        return result.mappings, result.explanation
```

### Step 2: Update Validation Plan

The `CanonicalValidationPlan` should include coverage information:

```python
@dataclass
class CanonicalValidationPlan:
    # ... existing fields ...
    
    # Coverage metrics
    source_coverage_pct: float = 100.0
    target_coverage_pct: float = 100.0
    target_only_columns: List[str] = field(default_factory=list)
    source_only_columns: List[str] = field(default_factory=list)
```

### Step 3: Update Summary Reporter

Modify `utils/summary_reporter.py` to include coverage:

```python
def create_summary(
    # ... existing params ...
    source_coverage_pct: float = None,
    target_coverage_pct: float = None,
    target_only_columns: List[str] = None,
):
    summary = {
        # ... existing fields ...
        "source_coverage_pct": source_coverage_pct,
        "target_coverage_pct": target_coverage_pct,
        "target_only_columns": target_only_columns or [],
    }
    # ... rest of function ...
```

## CLI Commands

### Generate Exclusion Config

```bash
python validate_cli.py generate-exclusions --table customers
```

**Output:** `config/exclusions_customers.yaml`

### Run Validation with Coverage Report

```bash
python validate_cli.py validate --table customers --coverage-report
```

**Output:**
- Standard validation results
- `output/customers_coverage_report.txt`

### Analyze Schema Drift

```bash
python validate_cli.py analyze-drift --schema public --target-schema bronze
```

**Output:** Report of all tables with target-only columns

## Best Practices

### 1. **Document Target-Only Columns**

Always document WHY each target-only column exists:

```yaml
customers:
  exclusions:
    - column_name: DATA_QUALITY_SCORE
      reason: "Calculated by our ML pipeline post-migration"
      applies_to: ["target"]
      added_by: "Data Science Team"
      ticket: "JIRA-12345"
      date_added: "2025-01-15"
```

### 2. **Set Coverage Thresholds**

Define minimum acceptable coverage:

```yaml
# config/validation_rules.yaml
coverage_thresholds:
  source_min: 95.0    # At least 95% of source columns must be validated
  target_min: 80.0    # At least 80% of target columns must be validated
  overall_min: 85.0   # Overall coverage must be 85%+
```

### 3. **Regular Coverage Audits**

Schedule regular reviews of coverage metrics:

```bash
# Weekly coverage audit
python validate_cli.py coverage-audit --all-tables > output/weekly_coverage.txt
```

### 4. **Track Coverage Over Time**

Store coverage metrics in a database:

```python
coverage_history = {
    "table": "customers",
    "date": "2025-01-15",
    "source_coverage": 100.0,
    "target_coverage": 50.0,
    "target_only_count": 5,
}
```

## Troubleshooting

### Issue: Low Target Coverage

**Symptom:**
```text
⚠️  Target coverage: 45.0% — LOW
```

**Solution:**

1. Review target-only columns
2. Determine if they should exist
3. Document exclusions or fix schema

### Issue: High Source Coverage, Low Target Coverage

**Symptom:**
```text
Source coverage: 98.0%
Target coverage: 52.0%
```

**Meaning:** Target has many extra columns — common in migrations with enrichment.

**Action:** Document target-only columns as intentional.

### Issue: Low Source Coverage, High Target Coverage

**Symptom:**
```text
Source coverage: 55.0%
Target coverage: 95.0%
```

**Meaning:** Source has columns that weren't migrated — potential data loss.

**Action:** Investigate source-only columns urgently.

## Examples

See `examples/bidirectional_exclusion_example.py` for complete working examples:

```bash
python examples/bidirectional_exclusion_example.py
```

## Summary

**Before:**
- Only reported matched columns
- No visibility into target-only columns
- Coverage metrics misleading

**After:**
- Full bi-directional analysis
- Target-only columns identified and categorized
- Accurate coverage metrics (source vs target vs overall)
- Auto-generated exclusion configs
- Actionable warnings and recommendations

**Key Benefit:** You now have **complete transparency** into what is and isn't being validated, with proper context for why certain columns are excluded.
