"""
Summary report CSV generation utilities
"""
import os
import pandas as pd


def create_summary(run_at, run_id, validation_type, source_table_name, source_type,
                   target_table_name, target_type, source_rows, target_rows, 
                   output_file_path, output_path, status, batch_start_time, 
                   batch_end_time, diff_batch, missing_in_source=0, missing_in_target=0):
    """
    Create summary CSV for validation results
    Appends one row per table validation
    
    Args:
        run_at: Timestamp of run
        run_id: Unique run identifier
        validation_type: 'count_validation' or 'data_validation'
        source_table_name: Name of source table
        source_type: Type of source database
        target_table_name: Name of target table
        target_type: Type of target database
        source_rows: Row count from source
        target_rows: Row count from target
        output_file_path: Path to detailed output (mismatch CSV)
        output_path: Directory to write summary CSV
        status: 'PASS' or 'FAIL'
        batch_start_time: Start time of validation
        batch_end_time: End time of validation
        diff_batch: Time taken (string with unit)
        missing_in_source: Count of rows missing in source
        missing_in_target: Count of rows missing in target
    
    Returns:
        str: Path to summary CSV file
    """
    
    if validation_type == 'count_validation':
        summary_df = pd.DataFrame([{
            "run_id": run_id,
            "run_at": run_at,
            "validation_performed": validation_type,
            "source_table_name": source_table_name,
            "source_type": source_type,
            "target_table_name": target_table_name,
            "target_type": target_type,
            "source_count": source_rows,
            "target_count": target_rows,
            "count_difference": abs(int(target_rows) - int(source_rows)),
            "status": status,
            "batch_start_time": batch_start_time,
            "batch_end_time": batch_end_time,
            "total_time_taken": diff_batch
        }])
    else:  # data_validation
        summary_df = pd.DataFrame([{
            "run_id": run_id,
            "run_at": run_at,
            "validation_performed": validation_type,
            "source_table_name": source_table_name,
            "source_type": source_type,
            "target_table_name": target_table_name,
            "target_type": target_type,
            "source_count": source_rows,
            "missing_in_source": missing_in_source,
            "target_count": target_rows,
            "missing_in_target": missing_in_target,
            "status": status,
            "output_file_path": output_file_path,
            "batch_start_time": batch_start_time,
            "batch_end_time": batch_end_time,
            "total_time_taken": diff_batch
        }])
    
    summary_file = os.path.join(output_path, f"{validation_type}_summary.csv")
    
    # Append mode - create header only on first write
    summary_df.to_csv(
        summary_file,
        mode="a",
        index=False,
        header=not os.path.exists(summary_file)
    )
    
    return summary_file
