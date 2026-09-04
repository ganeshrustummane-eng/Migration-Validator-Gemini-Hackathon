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

                        # Fall back to row_hash when no PK configured.
                        if not pksourcecolumn or not pktargetcolumn:
                            pksourcecolumn = "row_hash"
                            pktargetcolumn = "row_hash"

                        # Support both scalar PK (string) and composite PK (list)
                        if isinstance(pksourcecolumn, list):
                            pk_src = [c.lower() for c in pksourcecolumn]
                            pk_tgt = [c.lower() for c in pktargetcolumn]
                        else:
                            pk_src = pksourcecolumn.lower()
                            pk_tgt = pktargetcolumn.lower()

                        # row_hash mode: when pk is 'row_hash' but the SQL didn't
                        # produce that column, compute it in Python from the common
                        # columns so any JOIN query can be compared without a real PK.
                        if (pk_src == "row_hash" or (isinstance(pk_src, list) and pk_src == ["row_hash"])) \
                                and "row_hash" not in source_df.columns:
                            import hashlib
                            _common = [c for c in source_df.columns if c in set(target_df.columns)]
                            def _hash_row(row, cols=_common):
                                def _v(c):
                                    v = row[c]
                                    if v is None or (isinstance(v, float) and v != v):
                                        return "<<NULL>>"
                                    return str(v)
                                return hashlib.md5("|".join(_v(c) for c in cols).encode()).hexdigest()
                            source_df["row_hash"] = source_df.apply(_hash_row, axis=1)
                            target_df["row_hash"] = target_df.apply(_hash_row, axis=1)
                            pk_src = pk_tgt = "row_hash"

                        composite = isinstance(pk_src, list)
                        src = source_df.set_index(pk_src).sort_index()
                        tgt = target_df.set_index(pk_tgt).sort_index()
                        if composite:
                            tgt.index.names = src.index.names
                        else:
                            tgt.index.name = src.index.name

                        all_src_cols  = list(src.columns)
                        all_tgt_cols  = list(tgt.columns)
                        tgt_col_set   = set(all_tgt_cols)
                        src_col_set   = set(all_src_cols)

                        # Columns present in both sides — comparison happens only here.
                        common_cols   = [c for c in all_src_cols if c in tgt_col_set]
                        # Schema drift — reported as warnings, not row-level FAILs.
                        src_only_cols = [c for c in all_src_cols if c not in tgt_col_set]
                        tgt_only_cols = [c for c in all_tgt_cols if c not in src_col_set]
                        if src_only_cols:
                            logger.warning(
                                "Schema drift: column(s) %s exist in SOURCE but not in TARGET — "
                                "excluded from row comparison; check target schema.",
                                src_only_cols,
                            )
                        if tgt_only_cols:
                            logger.warning(
                                "Schema drift: column(s) %s exist in TARGET but not in SOURCE — "
                                "excluded from row comparison.",
                                tgt_only_cols,
                            )

                        # All columns from both sides appear in the output CSV for traceability.
                        display_cols = all_src_cols + [c for c in all_tgt_cols if c not in src_col_set]

                        def _row_key_str(pk_val):
                            if isinstance(pk_val, tuple):
                                return "|".join(str(v) for v in pk_val)
                            return str(pk_val)

                        def _cell_str(v):
                            """Normalize a cell for comparison:
                            - None/NaN            → '<<NULL>>'
                            - float/Decimal       → 2-dp string (matches COALESCE CAST output)
                            - everything else     → str()
                            """
                            if v is None or (isinstance(v, float) and v != v):
                                return "<<NULL>>"
                            if isinstance(v, float):
                                return f"{v:.2f}"
                            try:
                                from decimal import Decimal as _Dec
                                if isinstance(v, _Dec):
                                    return f"{float(v):.2f}"
                            except Exception:
                                pass
                            return str(v)

                        def _to_df(frame, pk_val):
                            if pk_val not in frame.index:
                                return pd.DataFrame(columns=frame.columns)
                            chunk = frame.loc[pk_val]
                            return chunk if isinstance(chunk, pd.DataFrame) else chunk.to_frame().T

                        # Single loop over all unique PKs — handles duplicates as sorted multisets
                        # so row-order differences between engines never cause false FAILs.
                        # Comparison is on common_cols only; schema drift is logged above.
                        # ponytail: O(n log n) sort per PK group; fine for migration data volumes.
                        all_pks = sorted(
                            set(src.index.unique()) | set(tgt.index.unique()),
                            key=lambda x: str(x),
                        )
                        rows = []
                        for pk_val in all_pks:
                            s_df = _to_df(src, pk_val)
                            t_df = _to_df(tgt, pk_val)
                            pk_str = _row_key_str(pk_val)

                            def _rec(pk_str, status, s_row=None, t_row=None):
                                rec = {"row_key": pk_str, "status": status}
                                for col in display_cols:
                                    rec[f"{col}__source"] = (s_row[col] if s_row is not None and col in s_row.index else "")
                                    rec[f"{col}__target"] = (t_row[col] if t_row is not None and col in t_row.index else "")
                                return rec

                            if s_df.empty:
                                rows.append(_rec(pk_str, "TARGET_ONLY", t_row=t_df.iloc[0]))
                            elif t_df.empty:
                                rows.append(_rec(pk_str, "SOURCE_ONLY", s_row=s_df.iloc[0]))
                            else:
                                s_sorted = sorted(
                                    s_df[common_cols].apply(
                                        lambda r: "|".join(_cell_str(r[c]) for c in common_cols), axis=1
                                    ).tolist()
                                )
                                t_sorted = sorted(
                                    t_df[common_cols].apply(
                                        lambda r: "|".join(_cell_str(r[c]) for c in common_cols), axis=1
                                    ).tolist()
                                )
                                row_status = "PASS" if s_sorted == t_sorted else "FAIL"
                                rows.append(_rec(pk_str, row_status, s_row=s_df.iloc[0], t_row=t_df.iloc[0]))

                        result_df = pd.DataFrame(rows).sort_values("row_key").reset_index(drop=True)
                        filepath = os.path.join(output_path, f"{table_name}_{validation}_result_{run_id}.csv")
                        result_df.to_csv(filepath, index=False)
                        logger.info("Saved row-level results (%d rows) to %s", len(result_df), filepath)

                        failed_df = result_df[result_df["status"] != "PASS"]
                        if not failed_df.empty:
                            failed_path = os.path.join(output_path, f"{table_name}_{validation}_failed_{run_id}.csv")
                            failed_df.to_csv(failed_path, index=False)
                            logger.info("Saved failed rows (%d rows) to %s", len(failed_df), failed_path)

                        n_fail_rows = int((result_df["status"] != "PASS").sum())
                        total_rows = len(result_df)
                        # Optional per-table mismatch threshold (e.g. 0.1 for ≤0.1%)
                        threshold_pct = float(validation_config.get("mismatch_threshold_pct", 0))
                        if threshold_pct > 0 and total_rows > 0:
                            actual_pct = (n_fail_rows / total_rows) * 100
                            is_match = actual_pct <= threshold_pct
                            logger.info("Threshold %.4f%% vs actual %.4f%%", threshold_pct, actual_pct)
                        else:
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

if failure_count > 0:
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(BASE_DIR), "src"))
        from notifier import notify_failure
        _errs = notify_failure(
            subject=f"[Migration Validator] {failure_count} failure(s) — {layer[0] if isinstance(layer, list) else layer}",
            body=(
                f"Run ID : {run_id}\n"
                f"Layer  : {layer[0] if isinstance(layer, list) else layer}\n"
                f"Tables : {', '.join(tables)}\n"
                f"Failures: {failure_count}\n"
                f"Duration: {total_time_taken}"
            ),
        )
        if _errs:
            logger.warning("Notification errors: %s", _errs)
    except Exception as _ne:
        logger.warning("Could not send failure notification: %s", _ne)

sys.exit(1 if system_error else 0)

