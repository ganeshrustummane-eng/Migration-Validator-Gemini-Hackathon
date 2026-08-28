"""
Integration Test: Enhanced Orchestrator with Bi-Directional Exclusion
=======================================================================
Tests the updated RuleMapperOrchestrator with bi-directional exclusion support.

This script demonstrates:
  1. Backward compatible mode (use_enhanced=False) - DEFAULT
  2. Enhanced mode with coverage analysis (use_enhanced=True)
  3. Integration with existing validation pipeline

Prerequisites:
  - DIAL_API_KEY must be set in environment (for AI mapping)
  - Or run in "dry-run" mode to see the structure without AI calls
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sql_extractor.extractors import ColumnMetadata
from ai_transformation.orchestrator import RuleMapperOrchestrator


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
    Create sample Snowflake columns with 5 extra columns:
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
        # Extra columns added during migration
        ColumnMetadata("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ", True, 11),
        ColumnMetadata("MIGRATED_AT", "TIMESTAMP_NTZ", True, 12),
        ColumnMetadata("MIGRATED_BY", "VARCHAR", True, 13),
        ColumnMetadata("DATA_QUALITY_SCORE", "NUMBER(3,2)", True, 14),
        ColumnMetadata("FULL_NAME", "VARCHAR", True, 15),
    ]


def test_backward_compatible_mode():
    """
    Test 1: Backward Compatible Mode (default behavior)
    
    This is the EXISTING behavior - no changes to current code.
    """
    print("\n" + "=" * 80)
    print("TEST 1: Backward Compatible Mode (use_enhanced=False)")
    print("=" * 80 + "\n")
    
    # Check if AI is configured
    if not os.getenv("DIAL_API_KEY"):
        print("⚠️  DIAL_API_KEY not set - skipping AI test")
        print("   This test requires DIAL_API_KEY to demonstrate AI mapping\n")
        return False
    
    try:
        # Create orchestrator in standard mode (default)
        orchestrator = RuleMapperOrchestrator(model="gpt-4o-mini")
        
        source_cols = create_sample_source_columns()
        target_cols = create_sample_target_columns()
        
        print(f"Source columns: {len(source_cols)}")
        print(f"Target columns: {len(target_cols)}")
        print(f"Extra target columns: {len(target_cols) - len(source_cols)}\n")
        
        # Call map_columns (standard method)
        mappings, explanation = orchestrator.map_columns(
            source_columns=source_cols,
            target_columns=target_cols,
            primary_key_hints=["customer_id"],
            table_name="customers",
        )
        
        print(f"\n✓ Mapping complete:")
        print(f"  • {len(mappings)} column pairs mapped")
        print(f"  • AI Model: {orchestrator.active_model}")
        print(f"\nAI Explanation (truncated):")
        print(f"  {explanation[:200]}...")
        
        # Note the limitation
        print(f"\n⚠️  NOTE: Standard mode does NOT report target-only columns")
        print(f"   You don't know that {len(target_cols) - len(source_cols)} target columns were not validated\n")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def test_enhanced_mode():
    """
    Test 2: Enhanced Mode with Bi-Directional Coverage
    
    This is the NEW enhanced behavior.
    """
    print("\n" + "=" * 80)
    print("TEST 2: Enhanced Mode with Coverage Analysis (use_enhanced=True)")
    print("=" * 80 + "\n")
    
    # Check if AI is configured
    if not os.getenv("DIAL_API_KEY"):
        print("⚠️  DIAL_API_KEY not set - skipping AI test")
        print("   This test requires DIAL_API_KEY to demonstrate enhanced mapping\n")
        return False
    
    try:
        # Create orchestrator in ENHANCED mode
        orchestrator = RuleMapperOrchestrator(model="gpt-4o-mini", use_enhanced=True)
        
        source_cols = create_sample_source_columns()
        target_cols = create_sample_target_columns()
        
        print(f"Source columns: {len(source_cols)}")
        print(f"Target columns: {len(target_cols)}")
        print(f"Extra target columns: {len(target_cols) - len(source_cols)}\n")
        
        # Option 1: Use map_columns() - backward compatible but with warnings
        print("─" * 80)
        print("Option 1: Using map_columns() (backward compatible)")
        print("─" * 80)
        
        mappings, explanation = orchestrator.map_columns(
            source_columns=source_cols,
            target_columns=target_cols,
            primary_key_hints=["customer_id"],
            table_name="customers",
            source_database="postgresql",
        )
        
        print(f"\n✓ Mapping complete:")
        print(f"  • {len(mappings)} column pairs mapped")
        print(f"  • Coverage warnings logged automatically\n")
        
        # Option 2: Use map_columns_with_coverage() - full enhanced mode
        print("─" * 80)
        print("Option 2: Using map_columns_with_coverage() (full enhanced mode)")
        print("─" * 80)
        
        result = orchestrator.map_columns_with_coverage(
            source_columns=source_cols,
            target_columns=target_cols,
            primary_key_hints=["customer_id"],
            table_name="customers",
            source_database="postgresql",
        )
        
        print(f"\n✓ Enhanced mapping complete:")
        print(f"  • {len(result.mappings)} column pairs mapped")
        print(f"  • Source coverage: {result.coverage.source_coverage_pct:.1f}%")
        print(f"  • Target coverage: {result.coverage.target_coverage_pct:.1f}%")
        print(f"  • Overall coverage: {result.coverage.overall_coverage_pct:.1f}%")
        
        if result.warnings:
            print(f"\n⚠️  Warnings ({len(result.warnings)}):")
            for warning in result.warnings:
                print(f"    • {warning}")
        
        if result.coverage.target_only_columns:
            print(f"\n📊 Target-Only Columns ({len(result.coverage.target_only_columns)}):")
            for col_info in result.coverage.target_only_columns:
                print(f"    • {col_info.column_name:25} ({col_info.category})")
                print(f"      └─ {col_info.reason}")
        
        if result.recommendations:
            print(f"\n💡 Recommendations:")
            for rec in result.recommendations:
                print(f"    • {rec}")
        
        # Print full summary report
        print(f"\n" + "─" * 80)
        print("Full Summary Report:")
        print("─" * 80)
        print(result.summary_report())
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_handling():
    """
    Test 3: Error Handling
    
    Verify that enhanced methods raise appropriate errors.
    """
    print("\n" + "=" * 80)
    print("TEST 3: Error Handling")
    print("=" * 80 + "\n")
    
    # Test 1: Calling map_columns_with_coverage() without use_enhanced=True
    print("Test 3a: Calling map_columns_with_coverage() without use_enhanced...")
    try:
        orchestrator = RuleMapperOrchestrator(use_enhanced=False)
        orchestrator.map_columns_with_coverage(
            source_columns=[],
            target_columns=[],
            table_name="test",
        )
        print("  ✗ Should have raised RuntimeError\n")
        return False
    except RuntimeError as e:
        print(f"  ✓ Correctly raised RuntimeError: {str(e)[:100]}...\n")
    
    # Test 2: Check properties work correctly
    print("Test 3b: Checking orchestrator properties...")
    orchestrator_standard = RuleMapperOrchestrator(use_enhanced=False)
    orchestrator_enhanced = RuleMapperOrchestrator(use_enhanced=True)
    
    print(f"  ✓ Standard orchestrator active: {orchestrator_standard.is_ai_active}")
    print(f"  ✓ Enhanced orchestrator active: {orchestrator_enhanced.is_ai_active}")
    print(f"  ✓ Standard model: {orchestrator_standard.active_model}")
    print(f"  ✓ Enhanced model: {orchestrator_enhanced.active_model}\n")
    
    return True


def show_integration_example():
    """
    Show how to integrate enhanced orchestrator into existing pipeline.
    """
    print("\n" + "=" * 80)
    print("INTEGRATION EXAMPLE: How to Update Existing Code")
    print("=" * 80 + "\n")
    
    print("BEFORE (Existing Code):")
    print("─" * 80)
    print("""
from ai_transformation import RuleMapperOrchestrator

# Standard orchestrator
orchestrator = RuleMapperOrchestrator(model="gpt-4o")

# Map columns
mappings, explanation = orchestrator.map_columns(
    source_columns=source_cols,
    target_columns=target_cols,
    table_name="my_table",
)

# Result: Only returns mappings and explanation
# Coverage of target-only columns is UNKNOWN
    """)
    
    print("\nAFTER (Enhanced Code - Option 1: Minimal Change):")
    print("─" * 80)
    print("""
from ai_transformation import RuleMapperOrchestrator

# Enhanced orchestrator - just add use_enhanced=True
orchestrator = RuleMapperOrchestrator(model="gpt-4o", use_enhanced=True)

# Map columns (SAME METHOD - backward compatible)
mappings, explanation = orchestrator.map_columns(
    source_columns=source_cols,
    target_columns=target_cols,
    table_name="my_table",
)

# Result: Returns same mappings and explanation
# BONUS: Automatically logs warnings about target-only columns
    """)
    
    print("\nAFTER (Enhanced Code - Option 2: Full Coverage):")
    print("─" * 80)
    print("""
from ai_transformation import RuleMapperOrchestrator

# Enhanced orchestrator
orchestrator = RuleMapperOrchestrator(model="gpt-4o", use_enhanced=True)

# Use NEW method for full coverage info
result = orchestrator.map_columns_with_coverage(
    source_columns=source_cols,
    target_columns=target_cols,
    table_name="my_table",
)

# Access mappings (backward compatible)
mappings = result.mappings
explanation = result.explanation

# Access NEW coverage info
print(f"Source coverage: {result.coverage.source_coverage_pct:.1f}%")
print(f"Target coverage: {result.coverage.target_coverage_pct:.1f}%")

# Review warnings and recommendations
for warning in result.warnings:
    print(f"Warning: {warning}")

# Review target-only columns
for col_info in result.coverage.target_only_columns:
    print(f"Target-only: {col_info.column_name} - {col_info.reason}")
    """)


def main():
    """Run all integration tests."""
    print("\n" + "=" * 80)
    print("ENHANCED ORCHESTRATOR INTEGRATION TESTS")
    print("=" * 80)
    
    results = []
    
    # Test 1: Backward compatible mode
    results.append(("Backward Compatible Mode", test_backward_compatible_mode()))
    
    # Test 2: Enhanced mode
    results.append(("Enhanced Mode", test_enhanced_mode()))
    
    # Test 3: Error handling
    results.append(("Error Handling", test_error_handling()))
    
    # Show integration example
    show_integration_example()
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80 + "\n")
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"  {status}: {test_name}")
    
    all_passed = all(result[1] for result in results if result[1] is not None)
    skipped = sum(1 for result in results if result[1] is None)
    
    print(f"\nTotal: {len(results)} tests")
    print(f"Passed: {sum(1 for r in results if r[1] is True)}")
    print(f"Failed: {sum(1 for r in results if r[1] is False)}")
    print(f"Skipped: {skipped}")
    
    if not os.getenv("DIAL_API_KEY"):
        print("\n⚠️  NOTE: Set DIAL_API_KEY to run full integration tests")
    
    print("\n" + "=" * 80)
    print("✓ Integration test complete")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
