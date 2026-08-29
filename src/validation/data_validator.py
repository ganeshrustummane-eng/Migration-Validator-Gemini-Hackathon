"""
Data validation executor
Performs row-by-row comparison between source and target databases
"""
import logging
import pandas as pd
from datetime import datetime
from src.utils.summary_reporter import create_summary

logger = logging.getLogger(__name__)


def execute_data_validation(config, db_factory, run_id, run_at, output_path):
    """
    Execute data validation for a single table with row-level comparison
    
    Compares actual data rows using primary key matching
    
    Args:
        config: Dict with validation configuration
            - source_table_name: Name of source table
            - source: Source database type
            - source_name: Source credential name in .env
            - pksourcecolumn: Primary key column name in source
            - sourcequery: SQL query to fetch source data
            - target_table_name: Name of target table
            - target: Target database type
            - target_name: Target credential name in .env
            - pktargetcolumn: Primary key column name in target
            - targetquery: SQL query to fetch target data
        db_factory: DatabaseFactory instance
        run_id: Unique run identifier
        run_at: Timestamp
        output_path: Output directory path
    
    Returns:
        Dict with validation result:
            - status: 'PASS', 'FAIL', or 'ERROR'
            - source_count: Row count from source
            - target_count: Row count from target
            - missing_in_source: Rows missing in source
            - missing_in_target: Rows missing in target
            - mismatch_file: Path to mismatch CSV (if any)
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
        pk_source_col = config.get('pksourcecolumn')
        pk_target_col = config.get('pktargetcolumn')
        
        logger.info(f"Executing data validation for {source_table}")
        
        # Get database connections
        source_db = db_factory.get_database(source_db_type, source_name)
        target_db = db_factory.get_database(target_db_type, target_name)
        
        # Execute queries
        logger.debug(f"Fetching source data from {source_table}...")
        source_data = source_db.execute_query(source_query)
        
        logger.debug(f"Fetching target data from {target_table}...")
        target_data = target_db.execute_query(target_query)
        
        source_count = len(source_data)
        target_count = len(target_data)
        
        logger.info(f"Source rows: {source_count}, Target rows: {target_count}")
        
        # If no primary key, just compare counts
        if not pk_source_col or not pk_target_col:
            logger.warning(f"No primary key specified for {source_table} - count-only comparison")
            status = 'PASS' if source_count == target_count else 'FAIL'
            batch_end_time = datetime.now()
            time_taken = (batch_end_time - batch_start_time).total_seconds()
            
            create_summary(
                run_at=run_at,
                run_id=run_id,
                validation_type='data_validation',
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
                diff_batch=f"{time_taken:.2f}s",
                missing_in_source=0,
                missing_in_target=0
            )
            
            return {
                'status': status,
                'source_count': source_count,
                'target_count': target_count,
                'missing_in_source': 0,
                'missing_in_target': 0,
                'mismatch_file': None,
                'error': None
            }
        
        # Generated queries commonly return normalized aliases such as
        # ACCTSOFTWAREID_normalized instead of the physical PK name.
        def resolve_column(dataframe, requested):
            requested_upper = requested.upper()
            for column in dataframe.columns:
                if str(column).upper() == requested_upper:
                    return column
            normalized = f"{requested}_normalized".upper()
            for column in dataframe.columns:
                if str(column).upper() == normalized:
                    return column
            return None

        source_pk = resolve_column(source_data, pk_source_col)
        target_pk = resolve_column(target_data, pk_target_col)
        if source_pk is None or target_pk is None:
            raise KeyError(
                f"Primary-key columns not found: source={pk_source_col}, target={pk_target_col}; "
                f"source columns={list(source_data.columns)}, target columns={list(target_data.columns)}"
            )

        logger.debug(f"Setting source index to {source_pk}, target index to {target_pk}")
        source_data.set_index(source_pk, inplace=True)
        target_data.set_index(target_pk, inplace=True)
        
        # Find differences
        missing_in_target = list(source_data.index.difference(target_data.index))
        missing_in_source = list(target_data.index.difference(source_data.index))
        
        logger.info(f"Missing in target: {len(missing_in_target)}, Missing in source: {len(missing_in_source)}")
        
        # Determine status
        if missing_in_source or missing_in_target:
            status = 'FAIL'
        else:
            status = 'PASS'
        
        # Generate mismatch CSV if needed
        mismatch_file = None
        if missing_in_source or missing_in_target:
            mismatch_file = f"{source_table}_data_validation_mismatch_{run_id}.csv"
            mismatch_path = f"{output_path}/{mismatch_file}"
            
            # Create mismatch data
            mismatch_data = []
            for pk in missing_in_target:
                mismatch_data.append({
                    'pk': pk,
                    'row_status': 'MISSING_IN_TARGET',
                    'details': f'Row {pk} exists in source but not in target'
                })
            for pk in missing_in_source:
                mismatch_data.append({
                    'pk': pk,
                    'row_status': 'MISSING_IN_SOURCE',
                    'details': f'Row {pk} exists in target but not in source'
                })
            
            if mismatch_data:
                mismatch_df = pd.DataFrame(mismatch_data)
                mismatch_df.to_csv(mismatch_path, index=False)
                logger.info(f"✓ Mismatch file created: {mismatch_file}")
        
        batch_end_time = datetime.now()
        time_taken = (batch_end_time - batch_start_time).total_seconds()
        
        create_summary(
            run_at=run_at,
            run_id=run_id,
            validation_type='data_validation',
            source_table_name=source_table,
            source_type=source_db_type,
            target_table_name=target_table,
            target_type=target_db_type,
            source_rows=source_count,
            target_rows=target_count,
            output_file_path=mismatch_file or '',
            output_path=output_path,
            status=status,
            batch_start_time=batch_start_time,
            batch_end_time=batch_end_time,
            diff_batch=f"{time_taken:.2f}s",
            missing_in_source=len(missing_in_source),
            missing_in_target=len(missing_in_target)
        )
        
        logger.info(f"✓ Data validation {status}")
        
        return {
            'status': status,
            'source_count': source_count,
            'target_count': target_count,
            'missing_in_source': len(missing_in_source),
            'missing_in_target': len(missing_in_target),
            'mismatch_file': mismatch_file,
            'error': None
        }
    
    except Exception as e:
        logger.error(f"✗ Data validation failed: {e}", exc_info=True)
        return {
            'status': 'ERROR',
            'error': str(e),
            'source_count': None,
            'target_count': None,
            'missing_in_source': None,
            'missing_in_target': None,
            'mismatch_file': None
        }
