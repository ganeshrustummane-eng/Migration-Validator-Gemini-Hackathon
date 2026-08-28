#!/usr/bin/env python
# quick_start.py - Copy this to run a quick validation test

"""
Quick Start Example: Count Validation PostgreSQL → Snowflake

This script demonstrates how to use the .env-based validation system
without creating YAML configs. Perfect for testing!

Run: python quick_start.py
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from src.db.factory import get_database
from src.utils.runid import generate_runid
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def main():
    """Run a quick validation"""
    
    logger.info("=" * 70)
    logger.info("QUICK START: .env-Based Validation Example")
    logger.info("=" * 70)
    
    # Generate run ID
    run_id, run_at = generate_runid()
    logger.info(f"\nRun ID: {run_id}")
    logger.info(f"Run At: {run_at}\n")
    
    # ========== EXAMPLE 1: Test Database Connections ==========
    logger.info("STEP 1: Testing database connections...")
    logger.info("-" * 70)
    
    try:
        db_pg = get_database('postgresql', 'SRC_1')
        logger.info("✓ PostgreSQL connection successful")
    except Exception as e:
        logger.error(f"✗ PostgreSQL failed: {e}")
        return False
    
    try:
        db_sf = get_database('snowflake', 'SNOWFLAKE')
        logger.info("✓ Snowflake connection successful")
    except Exception as e:
        logger.error(f"✗ Snowflake failed: {e}")
        return False
    
    # ========== EXAMPLE 2: Simple Count Query ==========
    logger.info("\nSTEP 2: Running count queries...")
    logger.info("-" * 70)
    
    try:
        # PostgreSQL count
        pg_result = db_pg.execute_query(
            "SELECT COUNT(*) as count FROM information_schema.tables"
        )
        pg_count = int(pg_result.iloc[0, 0])
        logger.info(f"✓ PostgreSQL: {pg_count} tables in system")
        
        # Snowflake count  
        sf_result = db_sf.execute_query("SELECT 1 as test_connection")
        logger.info(f"✓ Snowflake: Connection test successful")
        
    except Exception as e:
        logger.error(f"✗ Query execution failed: {e}")
        return False
    
    # ========== EXAMPLE 3: Using Validation Executor ==========
    logger.info("\nSTEP 3: Running full validation batch...")
    logger.info("-" * 70)
    
    try:
        from src.validation.validation_executor import ValidationExecutor
        
        executor = ValidationExecutor(
            base_dir=os.path.dirname(__file__),
            environment="dev"
        )
        
        # This will look for config files in config/bronze/ directory
        # If no configs exist, it will show what was attempted
        results = executor.execute_batch(
            layer="bronze",
            validation_types=['count_validation'],
            tables=['all'],
            config_dir=os.path.join(os.path.dirname(__file__), "config")
        )
        
        logger.info(f"✓ Batch execution completed with {len(results)} validations")
        
    except Exception as e:
        logger.error(f"⚠ Batch execution skipped (no configs): {e}")
    
    logger.info("\n" + "=" * 70)
    logger.info("QUICK START COMPLETE!")
    logger.info("=" * 70)
    
    logger.info("\nNext steps:")
    logger.info("1. Read ENV_BASED_INTEGRATION_GUIDE.md for full setup")
    logger.info("2. Read CONFIG_EXAMPLES.md for validation configs")
    logger.info("3. Create YAML configs in config/bronze/")
    logger.info("4. Run: python -m src.validation.validation_executor")
    
    return True


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
