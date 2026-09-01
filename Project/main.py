import os
import sys
import warnings
# Suppress snowflake-connector-python's pyarrow version warning — pyarrow 25 is
# required by streamlit on Python 3.14 and cannot be downgraded.
warnings.filterwarnings("ignore", category=UserWarning, module="snowflake.connector")
import argparse
import yaml
import time
import pyodbc
import psycopg2
from db.factory import get_database
from utils.utility import (generate_runid,get_config_output_paths,create_summary,get_logger,add_file_handler)
from utils.semantic_normalize import canonicalize_frames
from datetime import datetime


start_time = datetime.now()
#Generating run id
run_id,run_at = generate_runid()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(BASE_DIR,"config")

#Logging module
logger = get_logger(__name__)

#Getting input report names as parameters
parser = argparse.ArgumentParser()

parser.add_argument(
"--layer_type",
nargs=1,
required = True,
choices=["bronze", "silver", "gold", "reporting"]
)

parser.add_argument(
    "--tables",
    nargs="+",
    required=True
)

parser.add_argument(
    "--count_validation",
    nargs=1,
    required=True,
    choices=['yes','no']
)

parser.add_argument(
    "--data_validation",
    nargs=1,
    required=True,
    choices=['yes','no']
)

parser.add_argument(
    "--environment",
    nargs=1,
    required=True,
    choices=['dev','uat','prod','local']
)

args = parser.parse_args()

layer = args.layer_type
tables = args.tables
environment = args.environment[0]

validation_dirs = []
if args.count_validation[0] == 'yes':
    validation_dirs.append("count_validation")

if args.data_validation[0] == 'yes':
    validation_dirs.append("data_validation")


tables = args.tables
print(args.tables)

outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,BASE_DIR,config_path,validation_dirs,tables)

logger = add_file_handler(
    logger=logger,
    log_directory=logpath,
    log_filename=f"validation_{run_id}.log"
)

logger.info("Start Time: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
logger.info("File logging initialized")

print("="*100)
logger.info("Validation job started")
logger.info("Run ID: %s", run_id)

logger.info(
    "Input parameters - layer=%s, tables=%s, count_validation=%s, data_validation=%s",
    args.layer_type[0],
    args.tables,
    args.count_validation[0],
    args.data_validation[0]
)

logger.debug("Validation directories: %s", validation_dirs)

failure_count = 0
system_error = False

#Each validation is process in order
for validation in validation_dirs:
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]
    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    for yamlfile in config_path_yaml:
        with open(yamlfile) as f:
                config = yaml.safe_load(f)       
        logger.info("Loaded configuration: %s", config_path_yaml)
        
        if "all" in tables:
            tables_to_process = config["tables"].items()
        else:
            tables_to_process = [
                (table, config["tables"][table]) for table in tables if table in config["tables"]]

        print("-"*100)
        for table_name, table_config in tables_to_process:
            logger.info("Processing table: %s", table_name)
            for validation_name, validation_config in table_config["validations"].items():
                logger.debug("Validation configuration: %s", validation_name)
                source = validation_config.get("source")
                source_query = validation_config.get("sourcequery")
                target = validation_config.get("target")
                target_query = validation_config.get("targetquery")
                sourcecolumn = validation_config.get("sourcecolumn")
                targetcolumn = validation_config.get("targetcolumn")
                source_table_name = validation_config.get("source_table_name")
                target_table_name = validation_config.get("target_table_name")
                pksourcecolumn = validation_config.get("pksourcecolumn")
                pktargetcolumn = validation_config.get("pktargetcolumn")
                # Database/schema written into YAML at generation time — overrides .env
                source_database = validation_config.get("source_database", "")
                source_schema   = validation_config.get("source_schema", "")
                target_database = validation_config.get("target_database", "")
                target_schema   = validation_config.get("target_schema", "")

                try:
                    batch_start_time = datetime.now()
                    #source
                    logger.info("Executing source query for table %s", table_name)
                    logger.debug("Source query: %s", source_query)
                    obj = get_database(source, BASE_DIR, "local",
                                       override_database=source_database,
                                       override_schema=source_schema)
                    source_df = obj.execute_query(source_query)

                    #target
                    logger.info("Executing target query for table %s", table_name)
                    logger.debug("Target query: %s", target_query)
                    obj = get_database(target, BASE_DIR, environment,
                                       override_database=target_database,
                                       override_schema=target_schema)
                    target_df = obj.execute_query(target_query)

                    source_rows = len(source_df)
                    target_rows = len(target_df)

                    source_df.columns = source_df.columns.str.strip().str.lower()
                    target_df.columns = target_df.columns.str.strip().str.lower()

                    # JSON/JSONB/HStore arrive as raw document text (the SQL side
                    # no longer tries to canonicalize them — two engines could not
                    # be made to agree on key order, number formatting or NULL
                    # sentinels). Canonicalize both frames here with one function
                    # so equal documents become byte-identical strings before the
                    # row comparison below.
                    source_df, target_df = canonicalize_frames(source_df, target_df)

                    output_file_path = ""
                    if validation_name == "count_validation":
                        source_rows = source_df['source_row_count'].iloc[0]
                        target_rows = target_df['target_row_count'].iloc[0]
                        logger.debug("Source row count: %s", source_rows)
                        logger.debug("Target row count: %s", target_rows)
                        is_match = int(source_rows) == int(target_rows)
                    else:
                        import pandas as pd
                        source_rows = len(source_df)
                        target_rows = len(target_df)
                        logger.debug("Source row count: %s", source_rows)
                        logger.debug("Target row count: %s", target_rows)

                        # Support both scalar PK (string) and composite PK (list)
                        if isinstance(pksourcecolumn, list):
                            pk_src = [c.lower() for c in pksourcecolumn]
                            pk_tgt = [c.lower() for c in pktargetcolumn]
                            composite = True
                        else:
                            pk_src = pksourcecolumn.lower()
                            pk_tgt = pktargetcolumn.lower()
                            composite = False

                        src = source_df.set_index(pk_src).sort_index()
                        tgt = target_df.set_index(pk_tgt).sort_index()
                        # Rename target index levels to match source so intersection works
                        if composite:
                            tgt.index.names = src.index.names
                        else:
                            tgt.index.name = src.index.name

                        common_idx    = src.index.intersection(tgt.index)
                        missing_in_tgt = src.index.difference(tgt.index)
                        missing_in_src = tgt.index.difference(src.index)
                        data_cols = list(src.columns)

                        def _row_key_str(pk_val):
                            """Composite PK tuples → 'v1|v2'; scalar → str(v)."""
                            if isinstance(pk_val, tuple):
                                return "|".join(str(v) for v in pk_val)
                            return str(pk_val)

                        # One row per PK — every row present, PASS or FAIL
                        rows = []
                        for pk_val in common_idx:
                            s_row = src.loc[pk_val]
                            t_row = tgt.loc[pk_val]
                            # Guard: if the PK is not unique, loc returns a DataFrame.
                            # squeeze() collapses a 1-row DataFrame to a Series so the
                            # comparison logic below always works on a Series.
                            if isinstance(s_row, pd.DataFrame):
                                s_row = s_row.iloc[0]
                            if isinstance(t_row, pd.DataFrame):
                                t_row = t_row.iloc[0]
                            row_status = "PASS" if s_row.equals(t_row) else "FAIL"
                            rec = {"row_key": _row_key_str(pk_val), "status": row_status}
                            for col in data_cols:
                                rec[f"{col}__source"] = s_row[col] if col in s_row.index else ""
                                rec[f"{col}__target"] = t_row[col] if col in t_row.index else ""
                            rows.append(rec)
                        for pk_val in missing_in_tgt:
                            rec = {"row_key": _row_key_str(pk_val), "status": "SOURCE_ONLY"}
                            for col in data_cols:
                                rec[f"{col}__source"] = src.loc[pk_val][col] if col in src.columns else ""
                                rec[f"{col}__target"] = ""
                            rows.append(rec)
                        for pk_val in missing_in_src:
                            rec = {"row_key": _row_key_str(pk_val), "status": "TARGET_ONLY"}
                            for col in data_cols:
                                rec[f"{col}__source"] = ""
                                rec[f"{col}__target"] = tgt.loc[pk_val][col] if col in tgt.columns else ""
                            rows.append(rec)

                        result_df = pd.DataFrame(rows).sort_values("row_key").reset_index(drop=True)
                        filepath = os.path.join(output_path, f"{table_name}_{validation}_result_{run_id}.csv")
                        result_df.to_csv(filepath, index=False)
                        logger.info("Saved row-level results (%d rows) to %s", len(result_df), filepath)

                        n_fail_rows = int((result_df["status"] != "PASS").sum())
                        is_match = (n_fail_rows == 0)

                    if is_match:
                        logger.info("Match/Mismatch: Match")
                        status = "PASS"
                        logger.info("Validation passed for table=%s validation=%s", table_name, validation_name)
                    else:
                        logger.info("Match/Mismatch: Mismatch")
                        status = "FAIL"
                        logger.warning("Validation failed for table=%s validation=%s", table_name, validation_name)
                        failure_count += 1
                        logger.info("Current failure count: %s", failure_count)

                    logger.info("Creating summary file")
                    batch_end_time = datetime.now()
                    diff_batch = batch_end_time - batch_start_time
                    batch_start_time = batch_start_time.strftime("%H:%M:%S")
                    batch_end_time = batch_end_time.strftime("%H:%M:%S")
                    total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                    create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,source_rows,target_rows,output_file_path,output_path,status,batch_start_time,batch_end_time,total_batch_time_taken)
                    print("+"*100)


                except (pyodbc.Error, psycopg2.Error):
                    logger.error(
                        "Database/network error for table=%s validation=%s",
                        table_name,
                        validation_name,
                        exc_info=True
                    )
                    system_error = True
                    continue

                except Exception:
                    logger.error(
                        "Unexpected error for table=%s validation=%s",
                        table_name,
                        validation_name,
                        exc_info=True
                    )
                    continue

end_time = datetime.now()
duration = end_time - start_time
total_time_taken = time.strftime("%H:%M:%S",time.gmtime(duration.total_seconds()))
logger.info("Validation job completed")
logger.info("End Time: %s", end_time.strftime("%Y-%m-%d %H:%M:%S"))
logger.info("Duration: %s", total_time_taken)
logger.info("Total failures: %s", failure_count)
sys.exit(1 if system_error else 0)

