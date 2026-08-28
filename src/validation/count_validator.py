"""
Count validation executor
Compares row counts between source and target databases
"""
import logging
from datetime import datetime
from src.utils.summary_reporter import create_summary

logger = logging.getLogger(__name__)


def execute_count_validation(config, db_factory, run_id, run_at, output_path):
    """
    Execute count validation for a single table
    
    Compares the number of rows in source vs target table
    
    Args:
        config: Dict with validation configuration
            - source_table_name: Name of source table
            - source: Source database type ('postgresql', 'mssql', 'snowflake', 'athena')
            - source_name: Source credential name in .env (e.g., 'SRC_1')
            - sourcequery: SQL query to get source count
            - target_table_name: Name of target table
            - target: Target database type
            - target_name: Target credential name in .env
            - targetquery: SQL query to get target count
        db_factory: DatabaseFactory instance
        run_id: Unique run identifier
        run_at: Timestamp
        output_path: Output directory path
    
    Returns:
        Dict with validation result:
            - status: 'PASS', 'FAIL', or 'ERROR'
            - source_count: Row count from source
            - target_count: Row count from target
            - count_difference: Absolute difference
            - error: Error message if failed
    """
    batch_start_time = datetime.now()
    
    try:
        source_db_type = config.get('source', 'postgresql')
        source_name = config.get('source_name', 'SRC_1')
        target_db_type = config.get('target', 'snowflake')
        target_name = config.get('target_name', 'SNOWFLAKE')
        source_query = config.get('sourcequery')
        target_query = config.get('targetquery')
        source_table = config.get('source_table_name', 'unknown')
        target_table = config.get('target_table_name', 'unknown')
        
        logger.info(f"Executing count validation for {source_table}")
        
        # Get database connections
        source_db = db_factory.get_database(source_db_type, source_name)
        target_db = db_factory.get_database(target_db_type, target_name)
        
        # Execute queries
        logger.debug(f"Executing source query: {source_query[:100]}...")
        source_result = source_db.execute_query(source_query)
        
        logger.debug(f"Executing target query: {target_query[:100]}...")
        target_result = target_db.execute_query(target_query)
        
        # Extract counts (first row, first column)
        source_count = int(source_result.iloc[0, 0])
        target_count = int(target_result.iloc[0, 0])
        
        # Compare
        if source_count == target_count:
            status = 'PASS'
        else:
            status = 'FAIL'
        
        count_difference = abs(target_count - source_count)
        
        batch_end_time = datetime.now()
        time_taken = (batch_end_time - batch_start_time).total_seconds()
        
        # Create summary
        create_summary(
            run_at=run_at,
            run_id=run_id,
            validation_type='count_validation',
            source_table_name=source_table,
            source_type=source_db_type,
            target_table_name=target_table,
            target_type=target_db_type,
            source_rows=source_count,
            target_rows=target_count,
            output_file_path='',
            output_path=output_path,
            status=status,
            batch_start_time=batch_start_time,
            batch_end_time=batch_end_time,
            diff_batch=f"{time_taken:.2f}s"
        )
        
        logger.info(f"✓ Count validation {status}: source={source_count}, target={target_count}, diff={count_difference}")
        
        return {
            'status': status,
            'source_count': source_count,
            'target_count': target_count,
            'count_difference': count_difference,
            'error': None
        }
    
    except Exception as e:
        logger.error(f"✗ Count validation failed: {e}", exc_info=True)
        return {
            'status': 'ERROR',
            'error': str(e),
            'source_count': None,
            'target_count': None,
            'count_difference': None
        }
