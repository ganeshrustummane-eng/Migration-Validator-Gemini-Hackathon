#!/usr/bin/env python3
"""
Bi-Directional Exclusion Solution - Verification Script
========================================================
Verifies that all components are correctly installed and working.

Usage:
    python verify_installation.py
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))

# Color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_header(text):
    """Print colored header."""
    print(f"\n{BLUE}{'=' * 80}{RESET}")
    print(f"{BLUE}{text}{RESET}")
    print(f"{BLUE}{'=' * 80}{RESET}\n")


def print_success(text):
    """Print success message."""
    print(f"{GREEN}✓ {text}{RESET}")


def print_error(text):
    """Print error message."""
    print(f"{RED}✗ {text}{RESET}")


def print_warning(text):
    """Print warning message."""
    print(f"{YELLOW}⚠  {text}{RESET}")


def print_info(text):
    """Print info message."""
    print(f"  {text}")


def check_file_exists(filepath, description):
    """Check if a file exists."""
    path = Path(filepath)
    if path.exists():
        lines = len(path.read_text(encoding='utf-8').splitlines())
        print_success(f"{description}: {filepath} ({lines} lines)")
        return True
    else:
        print_error(f"{description}: {filepath} NOT FOUND")
        return False


def check_import(module_path, class_name, description):
    """Check if a module can be imported."""
    try:
        parts = module_path.split('.')
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        print_success(f"{description}: {module_path}.{class_name}")
        return True
    except ImportError as e:
        print_error(f"{description}: {module_path}.{class_name}")
        print_info(f"Error: {e}")
        return False
    except AttributeError as e:
        print_error(f"{description}: {class_name} not found in {module_path}")
        print_info(f"Error: {e}")
        return False


def check_environment():
    """Check environment variables."""
    import os
    
    dial_key = os.getenv("DIAL_API_KEY")
    if dial_key:
        print_success("DIAL_API_KEY is set")
        return True
    else:
        print_warning("DIAL_API_KEY is not set (required for AI features)")
        print_info("Set in .env or export DIAL_API_KEY=your-key")
        return False


def run_quick_test():
    """Run a quick functionality test."""
    try:
        from exclusions.bidirectional_exclusion_handler import (
            BiDirectionalExclusionHandler,
            BiDirectionalAnalysisResult,
            ColumnExclusionInfo,
        )
        from sql_extractor.extractors import ColumnMetadata
        
        # Create test data
        source_cols = [
            ColumnMetadata("id", "bigint", False, 1),
            ColumnMetadata("name", "varchar", True, 2),
        ]
        
        target_cols = [
            ColumnMetadata("ID", "NUMBER", False, 1),
            ColumnMetadata("NAME", "VARCHAR", True, 2),
            ColumnMetadata("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ", True, 3),
        ]
        
        # Run analysis
        handler = BiDirectionalExclusionHandler()
        result = handler.analyze_schemas(
            source_columns=source_cols,
            target_columns=target_cols,
            table_name="test_table",
        )
        
        # Verify results
        assert result.source_column_count == 2, "Source column count mismatch"
        assert result.target_column_count == 3, "Target column count mismatch"
        assert len(result.matched_columns) == 2, "Matched columns count mismatch"
        assert len(result.target_only_columns) == 1, "Target-only count mismatch"
        assert result.target_only_columns[0].column_name == "_FIVETRAN_SYNCED"
        assert result.source_coverage_pct == 100.0, "Source coverage mismatch"
        assert 60 < result.target_coverage_pct < 70, "Target coverage mismatch"
        
        print_success("Quick functionality test PASSED")
        print_info(f"  Source coverage: {result.source_coverage_pct:.1f}%")
        print_info(f"  Target coverage: {result.target_coverage_pct:.1f}%")
        print_info(f"  Target-only columns: {len(result.target_only_columns)}")
        return True
        
    except Exception as e:
        print_error(f"Quick functionality test FAILED: {e}")
        import traceback
        print_info(traceback.format_exc())
        return False


def main():
    """Run all verification checks."""
    print_header("BI-DIRECTIONAL EXCLUSION SOLUTION - INSTALLATION VERIFICATION")
    
    results = {
        "files": [],
        "imports": [],
        "environment": [],
        "tests": [],
    }
    
    # Check core implementation files
    print_header("1. CORE IMPLEMENTATION FILES")
    results["files"].append(check_file_exists(
        "src/exclusions/bidirectional_exclusion_handler.py",
        "BiDirectionalExclusionHandler"
    ))
    results["files"].append(check_file_exists(
        "src/ai_transformation/ai_rule_mapper_enhanced.py",
        "EnhancedAIRuleMapper"
    ))
    results["files"].append(check_file_exists(
        "src/ai_transformation/orchestrator.py",
        "Enhanced Orchestrator"
    ))
    
    # Check documentation files
    print_header("2. DOCUMENTATION FILES")
    results["files"].append(check_file_exists(
        "docs3.1/BIDIRECTIONAL_EXCLUSION_GUIDE.md",
        "User Guide"
    ))
    results["files"].append(check_file_exists(
        "docs3.1/BIDIRECTIONAL_EXCLUSION_IMPLEMENTATION_GUIDE.md",
        "Implementation Guide"
    ))
    results["files"].append(check_file_exists(
        "docs3.1/ARCHITECTURE_DIAGRAM.md",
        "Architecture Diagram"
    ))
    results["files"].append(check_file_exists(
        "BIDIRECTIONAL_EXCLUSION_SOLUTION.md",
        "Solution Summary"
    ))
    results["files"].append(check_file_exists(
        "FINAL_DELIVERY_SUMMARY.md",
        "Delivery Summary"
    ))
    results["files"].append(check_file_exists(
        "README_BIDIRECTIONAL_EXCLUSION.md",
        "README"
    ))
    results["files"].append(check_file_exists(
        "QUICK_START_CHECKLIST.md",
        "Quick Start Checklist"
    ))
    
    # Check example files
    print_header("3. EXAMPLE FILES")
    results["files"].append(check_file_exists(
        "examples/bidirectional_exclusion_example.py",
        "Basic Examples"
    ))
    results["files"].append(check_file_exists(
        "examples/integration_test_orchestrator.py",
        "Integration Tests"
    ))
    
    # Check imports
    print_header("4. PYTHON IMPORTS")
    results["imports"].append(check_import(
        "exclusions.bidirectional_exclusion_handler",
        "BiDirectionalExclusionHandler",
        "BiDirectionalExclusionHandler"
    ))
    results["imports"].append(check_import(
        "exclusions.bidirectional_exclusion_handler",
        "BiDirectionalAnalysisResult",
        "BiDirectionalAnalysisResult"
    ))
    results["imports"].append(check_import(
        "exclusions.bidirectional_exclusion_handler",
        "ColumnExclusionInfo",
        "ColumnExclusionInfo"
    ))
    results["imports"].append(check_import(
        "ai_transformation.ai_rule_mapper_enhanced",
        "EnhancedAIRuleMapper",
        "EnhancedAIRuleMapper"
    ))
    results["imports"].append(check_import(
        "ai_transformation.ai_rule_mapper_enhanced",
        "EnhancedMappingResult",
        "EnhancedMappingResult"
    ))
    results["imports"].append(check_import(
        "ai_transformation.orchestrator",
        "RuleMapperOrchestrator",
        "RuleMapperOrchestrator"
    ))
    
    # Check environment
    print_header("5. ENVIRONMENT VARIABLES")
    results["environment"].append(check_environment())
    
    # Run quick test
    print_header("6. FUNCTIONALITY TEST")
    results["tests"].append(run_quick_test())
    
    # Summary
    print_header("VERIFICATION SUMMARY")
    
    total_files = len(results["files"])
    passed_files = sum(results["files"])
    print_info(f"Files:       {passed_files}/{total_files} found")
    
    total_imports = len(results["imports"])
    passed_imports = sum(results["imports"])
    print_info(f"Imports:     {passed_imports}/{total_imports} successful")
    
    total_env = len(results["environment"])
    passed_env = sum(results["environment"])
    print_info(f"Environment: {passed_env}/{total_env} configured")
    
    total_tests = len(results["tests"])
    passed_tests = sum(results["tests"])
    print_info(f"Tests:       {passed_tests}/{total_tests} passed")
    
    print()
    
    # Overall status
    all_critical = (
        passed_files == total_files and
        passed_imports == total_imports and
        passed_tests == total_tests
    )
    
    if all_critical:
        print_success("ALL CRITICAL CHECKS PASSED ✓")
        print()
        print_info("The bi-directional exclusion solution is correctly installed!")
        print_info("Next steps:")
        print_info("  1. Review: cat README_BIDIRECTIONAL_EXCLUSION.md")
        print_info("  2. Test:   python examples/integration_test_orchestrator.py")
        print_info("  3. Start:  Follow QUICK_START_CHECKLIST.md")
        
        if not results["environment"][0]:
            print()
            print_warning("Note: DIAL_API_KEY not set - AI features will not work")
            print_info("      Set DIAL_API_KEY in .env for full functionality")
        
        return 0
    else:
        print_error("SOME CHECKS FAILED ✗")
        print()
        print_info("Please review the errors above and:")
        print_info("  1. Ensure all files were created correctly")
        print_info("  2. Check that src/ is in PYTHONPATH")
        print_info("  3. Verify dependencies are installed")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
