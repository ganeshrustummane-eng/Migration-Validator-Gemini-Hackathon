"""
Example: Using Bi-Directional Exclusion Handler
=================================================
Demonstrates how to use the enhanced AI rule mapper with bi-directional
exclusion handling to properly handle target-only columns.

Scenario:
  Source (PostgreSQL): 10 columns
  Target (Snowflake):  15 columns (5 extra columns added during migration)

This example shows:
  1. How to detect target-only columns
  2. How to categorize them (audit, enrichment, metadata, etc.)
  3. How to generate accurate coverage reports
  4. How to auto-generate exclusion configs
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sql_extractor.extractors import ColumnMetadata
from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper
from exclusions.bidirectional_exclusion_handler import BiDirectionalExclusionHandler


def create_sample_source_columns() -> list:
    """Create sample PostgreSQL columns."""
    return [
        ColumnMetadata("customer_id", "bigint", False, 1),
        ColumnMetadata("first_name", "varchar", True, 2),
        ColumnMetadata("last_name", "varchar", True, 3),
        ColumnMetadata("email", "varchar", True, 4),
        ColumnMetadata("phone", "varchar", True, 5),
        ColumnMetadata("date_of_birth", "date", True, 6),
        ColumnMetadata("account_balance", "numeric(10,2)", True, 7),
        ColumnMetadata("is_active", "boolean", True, 8),
        ColumnMetadata("created_at", "timestamp", False, 9),
        ColumnMetadata("updated_at", "timestamp", True, 10),
    ]


def create_sample_target_columns() -> list:
    """
    Create sample Snowflake columns.
    Includes 5 extra columns not in source:
      - _FIVETRAN_SYNCED (metadata)
      - migrated_at (audit)
      - migrated_by (audit)
      - data_quality_score (enrichment)
      - full_name (derived)
    """
    return [
        ColumnMetadata("CUSTOMER_ID", "NUMBER(38,0)", False, 1),
        ColumnMetadata("FIRST_NAME", "VARCHAR", True, 2),
        ColumnMetadata("LAST_NAME", "VARCHAR", True, 3),
        ColumnMetadata("EMAIL", "VARCHAR", True, 4),
        ColumnMetadata("PHONE", "VARCHAR", True, 5),
        ColumnMetadata("DATE_OF_BIRTH", "DATE", True, 6),
        ColumnMetadata("ACCOUNT_BALANCE", "NUMBER(10,2)", True, 7),
        ColumnMetadata("IS_ACTIVE", "BOOLEAN", True, 8),
        ColumnMetadata("CREATED_AT", "TIMESTAMP_NTZ", False, 9),
        ColumnMetadata("UPDATED_AT", "TIMESTAMP_NTZ", True, 10),
        # ── Extra columns added during migration ──────────────────────────
        ColumnMetadata("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ", True, 11),
        ColumnMetadata("MIGRATED_AT", "TIMESTAMP_NTZ", True, 12),
        ColumnMetadata("MIGRATED_BY", "VARCHAR", True, 13),
        ColumnMetadata("DATA_QUALITY_SCORE", "NUMBER(3,2)", True, 14),
        ColumnMetadata("FULL_NAME", "VARCHAR", True, 15),
    ]


def example_basic_analysis():
    """
    Example 1: Basic Bi-Directional Analysis
    Shows how to analyze schemas without AI mapping.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 1: Basic Bi-Directional Analysis (No AI)")
    print("=" * 80 + "\n")
    
    handler = BiDirectionalExclusionHandler()
    
    source_cols = create_sample_source_columns()
    target_cols = create_sample_target_columns()
    
    result = handler.analyze_schemas(
        source_columns=source_cols,
        target_columns=target_cols,
        table_name="customers",
        source_database="postgresql",
    )
    
    # Print summary
    print(result.summary())
    
    # Print YAML config
    print("\n" + "-" * 80)
    print("Auto-Generated YAML Exclusion Config:")
    print("-" * 80 + "\n")
    yaml_config = handler.generate_exclusion_config(
        result=result,
        source_database="postgresql",
        target_database="snowflake",
    )
    print(yaml_config)


def example_enhanced_ai_mapping():
    """
    Example 2: Enhanced AI Mapping with Coverage
    Shows how to use EnhancedAIRuleMapper with full coverage reporting.
    
    NOTE: Requires DIAL_API_KEY environment variable to be set.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Enhanced AI Mapping with Coverage")
    print("=" * 80 + "\n")
    
    # Check if AI is configured
    if not os.getenv("DIAL_API_KEY"):
        print("⚠️  DIAL_API_KEY not set — skipping AI mapping example")
        print("   Set DIAL_API_KEY in .env to run this example")
        return
    
    try:
        mapper = EnhancedAIRuleMapper(model="gpt-4o-mini")
        
        source_cols = create_sample_source_columns()
        target_cols = create_sample_target_columns()
        
        result = mapper.map_columns_with_coverage(
            source_columns=source_cols,
            target_columns=target_cols,
            primary_key_hints=["customer_id"],
            table_name="customers",
            source_database="postgresql",
        )
        
        # Print comprehensive report
        print(result.summary_report())
        
        # Export report to file
        mapper.export_coverage_report(
            result=result,
            output_path="output/customers_coverage_report.txt",
        )
        
        # Print individual mappings
        print("\n" + "-" * 80)
        print("Column Mappings:")
        print("-" * 80 + "\n")
        for mapping in result.mappings:
            status = " [SKIP]" if mapping.skip_validation else ""
            pk_tag = " [PK]" if mapping.is_primary_key else ""
            print(f"  • {mapping.source_column:20} → {mapping.target_column:20} "
                  f"({mapping.rule.rule_name}){pk_tag}{status}")
            if mapping.skip_validation:
                print(f"    Reason: {mapping.skip_reason}")
        
    except Exception as e:
        print(f"✗ AI mapping failed: {e}")
        print("  This is expected if DIAL is unreachable or API key is invalid")


def example_coverage_scenarios():
    """
    Example 3: Different Coverage Scenarios
    Shows how different schema configurations affect coverage metrics.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Coverage Scenarios")
    print("=" * 80 + "\n")
    
    handler = BiDirectionalExclusionHandler()
    
    scenarios = [
        {
            "name": "Perfect Match (100% coverage)",
            "source": [
                ColumnMetadata("id", "bigint", False, 1),
                ColumnMetadata("name", "varchar", True, 2),
                ColumnMetadata("created_at", "timestamp", False, 3),
            ],
            "target": [
                ColumnMetadata("ID", "NUMBER", False, 1),
                ColumnMetadata("NAME", "VARCHAR", True, 2),
                ColumnMetadata("CREATED_AT", "TIMESTAMP_NTZ", False, 3),
            ],
        },
        {
            "name": "Target Enrichment (67% coverage)",
            "source": [
                ColumnMetadata("id", "bigint", False, 1),
                ColumnMetadata("name", "varchar", True, 2),
            ],
            "target": [
                ColumnMetadata("ID", "NUMBER", False, 1),
                ColumnMetadata("NAME", "VARCHAR", True, 2),
                ColumnMetadata("DATA_QUALITY_SCORE", "NUMBER", True, 3),
            ],
        },
        {
            "name": "Source Dropped Columns (67% coverage)",
            "source": [
                ColumnMetadata("id", "bigint", False, 1),
                ColumnMetadata("name", "varchar", True, 2),
                ColumnMetadata("legacy_field", "varchar", True, 3),
            ],
            "target": [
                ColumnMetadata("ID", "NUMBER", False, 1),
                ColumnMetadata("NAME", "VARCHAR", True, 2),
            ],
        },
    ]
    
    for scenario in scenarios:
        print(f"\nScenario: {scenario['name']}")
        print("-" * 60)
        
        result = handler.analyze_schemas(
            source_columns=scenario["source"],
            target_columns=scenario["target"],
            table_name="test_table",
        )
        
        print(f"  Source columns:    {result.source_column_count}")
        print(f"  Target columns:    {result.target_column_count}")
        print(f"  Matched pairs:     {len(result.matched_columns)}")
        print(f"  Source coverage:   {result.source_coverage_pct:.1f}%")
        print(f"  Target coverage:   {result.target_coverage_pct:.1f}%")
        print(f"  Overall coverage:  {result.overall_coverage_pct:.1f}%")
        
        if result.target_only_columns:
            print(f"\n  Target-only columns:")
            for col in result.target_only_columns:
                print(f"    • {col.column_name} — {col.reason}")
        
        if result.source_only_columns:
            print(f"\n  Source-only columns:")
            for col in result.source_only_columns:
                print(f"    • {col.column_name} — {col.reason}")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("BI-DIRECTIONAL EXCLUSION HANDLER — EXAMPLES")
    print("=" * 80)
    
    # Run examples
    example_basic_analysis()
    example_enhanced_ai_mapping()
    example_coverage_scenarios()
    
    print("\n" + "=" * 80)
    print("✓ Examples complete")
    print("=" * 80 + "\n")
