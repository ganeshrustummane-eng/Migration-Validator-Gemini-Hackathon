# Bi-Directional Exclusion Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        MIGRATION VALIDATOR                              │
│                     PostgreSQL → Snowflake                              │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        SOURCE SCHEMA EXTRACTION                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  PostgreSQL Database                                            │   │
│  │  • Tables                                                       │   │
│  │  • Columns (name, type, nullable, position)                    │   │
│  │  • Constraints                                                  │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ├──> source_columns: List[ColumnMetadata]
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        TARGET SCHEMA EXTRACTION                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Snowflake Database                                             │   │
│  │  • Tables                                                       │   │
│  │  • Columns (name, type, nullable, position)                    │   │
│  │  • Constraints                                                  │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ├──> target_columns: List[ColumnMetadata]
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     BI-DIRECTIONAL ANALYSIS                             │
│                BiDirectionalExclusionHandler                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 1: Build Column Maps                                     │   │
│  │    source_map = {col.upper(): col for col in source_columns}  │   │
│  │    target_map = {col.upper(): col for col in target_columns}  │   │
│  └────────────────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 2: Identify Column Sets                                  │   │
│  │    matched_names = source_names ∩ target_names                 │   │
│  │    source_only  = source_names - target_names                  │   │
│  │    target_only  = target_names - source_names ◄─── NEW         │   │
│  └────────────────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 3: Categorize Target-Only Columns                        │   │
│  │    For each target_only column:                                │   │
│  │      • Check Fivetran patterns (_FIVETRAN_*)                   │   │
│  │      • Check audit patterns (migrated_*, *_by, etl_*)          │   │
│  │      • Check enrichment patterns (*_score, segment_*)          │   │
│  │      • Check derived patterns (full_*, calculated_*)           │   │
│  │      • Mark as unknown if no match                             │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ├──> BiDirectionalAnalysisResult
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         AI RULE MAPPING                                 │
│                    EnhancedAIRuleMapper                                 │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 1: Bi-Directional Analysis (from above)                  │   │
│  └────────────────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 2: AI Column Mapping (matched columns only)              │   │
│  │    • Map source → target columns                               │   │
│  │    • Assign validation rules                                   │   │
│  │    • Detect primary keys                                       │   │
│  │    • Generate SQL queries                                      │   │
│  └────────────────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Step 3: Generate Warnings & Recommendations                   │   │
│  │    • Low coverage warnings                                     │   │
│  │    • Schema drift warnings                                     │   │
│  │    • High exclusion rate warnings                              │   │
│  │    • Actionable recommendations                                │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ├──> EnhancedMappingResult
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                                    │
│                   RuleMapperOrchestrator                                │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Mode Selection:                                                │   │
│  │    if use_enhanced:                                             │   │
│  │      ├─> EnhancedAIRuleMapper (NEW)                            │   │
│  │      └─> Log coverage warnings automatically                   │   │
│  │    else:                                                        │   │
│  │      └─> AIRuleMapper (EXISTING)                               │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ├──> (mappings, explanation) OR
                                  ├──> EnhancedMappingResult
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         VALIDATION EXECUTION                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Execute validation queries                                     │   │
│  │  Compare results                                                │   │
│  │  Generate reports                                               │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         ENHANCED REPORTING                              │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  Coverage Metrics:                                              │   │
│  │    • Source coverage: 100% (6/6 columns)                       │   │
│  │    • Target coverage: 55% (6/11 columns)                       │   │
│  │    • Overall coverage: 55% (6/11 unique columns)               │   │
│  │                                                                 │   │
│  │  Target-Only Columns (5):                                      │   │
│  │    • _FIVETRAN_SYNCED [Fivetran Metadata]                      │   │
│  │    • MIGRATED_AT [Audit Column]                                │   │
│  │    • MIGRATED_BY [Audit Column]                                │   │
│  │    • DATA_QUALITY_SCORE [Data Enrichment]                      │   │
│  │    • FULL_NAME [Derived Column]                                │   │
│  │                                                                 │   │
│  │  Warnings:                                                      │   │
│  │    ⚠️  5 columns exist in target but not in source              │   │
│  │    ⚠️  Target coverage 55% below threshold (80%)                │   │
│  │                                                                 │   │
│  │  Recommendations:                                               │   │
│  │    💡 Document audit columns in migration spec                  │   │
│  │    💡 Generate exclusion config                                 │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Interaction Flow

```
┌──────────────┐
│   User Code  │
└──────┬───────┘
       │
       │ orchestrator.map_columns_with_coverage(...)
       │
       ▼
┌────────────────────────────────┐
│  RuleMapperOrchestrator        │
│  (use_enhanced=True)           │
└────────────┬───────────────────┘
             │
             │ if use_enhanced
             │
             ▼
┌────────────────────────────────┐      ┌──────────────────────────────┐
│  EnhancedAIRuleMapper          │─────▶│  BiDirectionalExclusion     │
│                                │      │  Handler                     │
│  1. Analyze schemas            │◀─────│                              │
│  2. Call AI mapper             │      │  • Detect target-only cols   │
│  3. Generate warnings          │      │  • Categorize columns        │
│  4. Generate recommendations   │      │  • Calculate coverage        │
└────────────┬───────────────────┘      └──────────────────────────────┘
             │
             │ return EnhancedMappingResult
             │
             ▼
┌────────────────────────────────┐
│  EnhancedMappingResult         │
│                                │
│  • mappings (backward compat)  │
│  • explanation (backward compat│
│  • coverage (NEW)              │
│  • warnings (NEW)              │
│  • recommendations (NEW)       │
└────────────┬───────────────────┘
             │
             ▼
       ┌──────────────┐
       │   User Code  │
       └──────────────┘
```

---

## Data Flow: Column Analysis

```
SOURCE COLUMNS                    TARGET COLUMNS
┌─────────────┐                  ┌──────────────┐
│ customer_id │                  │ CUSTOMER_ID  │ ─┐
│ first_name  │                  │ FIRST_NAME   │  │
│ last_name   │                  │ LAST_NAME    │  │ Matched
│ email       │                  │ EMAIL        │  │ (Validated)
│ phone       │                  │ PHONE        │  │
│ created_at  │                  │ CREATED_AT   │ ─┘
└─────────────┘                  │              │
       6 columns                 │ _FIVETRAN_   │ ─┐
                                 │  SYNCED      │  │
                                 │ MIGRATED_AT  │  │
                                 │ MIGRATED_BY  │  │ Target-Only
                                 │ DATA_QUALITY_│  │ (Excluded with
                                 │  SCORE       │  │  reasons)
                                 │ FULL_NAME    │ ─┘
                                 └──────────────┘
                                    11 columns

                    ↓ Analysis ↓

┌─────────────────────────────────────────────────────────────────┐
│                    BiDirectionalAnalysisResult                  │
│                                                                 │
│  source_column_count: 6                                         │
│  target_column_count: 11                                        │
│  total_columns: 11                                              │
│                                                                 │
│  matched_columns: 6 pairs                                       │
│    [("customer_id", "CUSTOMER_ID"), ...]                       │
│                                                                 │
│  target_only_columns: 5                                         │
│    • _FIVETRAN_SYNCED → Category: Fivetran Metadata           │
│    • MIGRATED_AT → Category: Audit Column                      │
│    • MIGRATED_BY → Category: Audit Column                      │
│    • DATA_QUALITY_SCORE → Category: Data Enrichment            │
│    • FULL_NAME → Category: Derived Column                      │
│                                                                 │
│  Coverage Metrics:                                              │
│    source_coverage_pct: 100.0% (6/6)                           │
│    target_coverage_pct: 54.5% (6/11)                           │
│    overall_coverage_pct: 54.5% (6/11)                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pattern Matching Flow

```
Target-Only Column: "MIGRATED_AT"
        ↓
┌───────────────────────────────────────┐
│  Pattern Matching Engine              │
│                                       │
│  1. Check Fivetran patterns          │
│     ❌ Does not match ^_FIVETRAN_.*   │
│                                       │
│  2. Check audit patterns              │
│     ✅ Matches ^migrated_(at|by|date) │
│     ↓                                 │
│     Category: "Audit Column"          │
│     Reason: "Audit/tracking column    │
│              added during migration"  │
│                                       │
└───────────────────────────────────────┘
        ↓
┌───────────────────────────────────────┐
│  ColumnExclusionInfo                  │
│                                       │
│  column_name: "MIGRATED_AT"           │
│  data_type: "TIMESTAMP_NTZ"           │
│  excluded: True                       │
│  reason: "Audit/tracking column..."   │
│  direction: "target_only"             │
│  category: "Audit Column"             │
│  is_audit_column: True                │
└───────────────────────────────────────┘
```

---

## Coverage Calculation

```
Source Columns (6):              Target Columns (11):
┌────────────────┐              ┌────────────────┐
│ All 6 matched  │              │ 6 matched      │
│ with target    │              │ 5 target-only  │
└────────────────┘              └────────────────┘
       ↓                                ↓
Source Coverage:                Target Coverage:
  6 validated                     6 validated
  ───────────── = 100%            ───────────── = 54.5%
  6 total                         11 total


Overall Coverage:
  6 matched pairs
  ─────────────────────────── = 54.5%
  11 total unique columns
```

---

## Backward Compatibility

```
┌─────────────────────────────────────────────────────────────────┐
│  EXISTING CODE (No Changes)                                     │
│                                                                 │
│  orchestrator = RuleMapperOrchestrator(model="gpt-4o")          │
│                                                                 │
│  mappings, explanation = orchestrator.map_columns(              │
│      source_columns=source_cols,                                │
│      target_columns=target_cols,                                │
│  )                                                              │
│                                                                 │
│  # Works exactly as before ✅                                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  ENHANCED CODE (Opt-in)                                         │
│                                                                 │
│  orchestrator = RuleMapperOrchestrator(                         │
│      model="gpt-4o",                                            │
│      use_enhanced=True  # ← Just add this                       │
│  )                                                              │
│                                                                 │
│  # Option 1: Backward compatible (automatic warnings)           │
│  mappings, explanation = orchestrator.map_columns(...)          │
│  # → Returns same tuple + logs coverage warnings                │
│                                                                 │
│  # Option 2: Full enhanced mode                                 │
│  result = orchestrator.map_columns_with_coverage(...)           │
│  # → Returns EnhancedMappingResult with coverage                │
└─────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
src/
├── exclusions/
│   ├── exclusion_manager.py (EXISTING)
│   └── bidirectional_exclusion_handler.py (NEW - 467 lines)
│       └── BiDirectionalExclusionHandler
│           ├── analyze_schemas()
│           ├── _analyze_target_only_column()
│           └── generate_exclusion_config()
│
├── ai_transformation/
│   ├── ai_rule_mapper.py (EXISTING)
│   ├── ai_rule_mapper_enhanced.py (NEW - 341 lines)
│   │   └── EnhancedAIRuleMapper
│   │       ├── map_columns_with_coverage()
│   │       ├── _generate_warnings()
│   │       ├── _generate_recommendations()
│   │       └── export_coverage_report()
│   │
│   └── orchestrator.py (UPDATED - 243 lines)
│       └── RuleMapperOrchestrator
│           ├── __init__(use_enhanced=False)  # NEW parameter
│           ├── map_columns()  # Enhanced when use_enhanced=True
│           └── map_columns_with_coverage()  # NEW method
│
docs3.1/
├── BIDIRECTIONAL_EXCLUSION_GUIDE.md (658 lines)
├── BIDIRECTIONAL_EXCLUSION_IMPLEMENTATION_GUIDE.md (450 lines)
└── ARCHITECTURE_DIAGRAM.md (THIS FILE)

examples/
├── bidirectional_exclusion_example.py (331 lines)
└── integration_test_orchestrator.py (380 lines)

Root files:
├── BIDIRECTIONAL_EXCLUSION_SOLUTION.md (420 lines)
├── FINAL_DELIVERY_SUMMARY.md (400 lines)
├── QUICK_START_CHECKLIST.md (300 lines)
└── README_BIDIRECTIONAL_EXCLUSION.md (500 lines)
```

---

## Integration Points

```
┌──────────────────────────────────────────────────────────────────┐
│  EXISTING VALIDATION PIPELINE                                    │
│                                                                  │
│  Schema Profiler → Orchestrator → Validator → Reporter          │
│                          ↑                                       │
│                          │                                       │
│                   🆕 Enhanced mode                                │
│                      (opt-in)                                    │
│                          │                                       │
│         ┌────────────────┴────────────────┐                     │
│         │                                  │                     │
│    🆕 BiDirectional           🆕 Enhanced                         │
│       Exclusion                  Reporting                       │
│       Handler                                                    │
│         │                                  │                     │
│         └────────────────┬────────────────┘                     │
│                          │                                       │
│                   Coverage Metrics                               │
│                   Warnings                                       │
│                   Recommendations                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Summary

This architecture delivers:

✅ **Minimal Disruption** - Uses existing pipeline, adds new capabilities
✅ **Backward Compatible** - All existing code continues to work
✅ **Opt-in Enhancement** - New features enabled via `use_enhanced=True`
✅ **Complete Coverage** - Tracks source, target, and overall coverage
✅ **Actionable Insights** - Warnings and recommendations
✅ **Production Ready** - Comprehensive error handling and logging

**Key Innovation:** Moving from "what we validated" to "what we validated vs. what exists"
