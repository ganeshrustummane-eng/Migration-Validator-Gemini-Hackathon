import os
import sys
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
# Suppress snowflake-connector-python's pyarrow version warning — pyarrow 25 is
# required by streamlit on Python 3.14 and cannot be downgraded.
warnings.filterwarnings("ignore", category=UserWarning, module="snowflake.connector")
import argparse
import yaml
import time
import pyodbc
import psycopg2
from pathlib import Path
from db.factory import get_database
from utils.utility import (generate_runid,get_config_output_paths,create_summary,get_logger,add_file_handler,
                            count_validation_match,row_hash_fallback_looks_like_column_drift,
                            should_dispatch_hybrid,check_hybrid_incremental_conflict,
                            read_incremental_range,incremental_filter_column)
from utils.semantic_normalize import canonicalize_frames
from utils.quality_checks import append_validation_audit, run_integrity_check, run_quality_checks, validate_expected_grain
from utils.row_compare import compare_indexed_frames
from utils.incremental_filter import apply_incremental_predicate
from utils.environments import ENVIRONMENTS, resolve_env_placeholders
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
    choices=list(ENVIRONMENTS)
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


def _write_error_summary(table_name, validation_name, source_table_name, source_type,
                          target_table_name, target_type, source_rows, target_rows,
                          output_path, batch_start_time):
    """Best-effort ERROR summary row so a table that crashed mid-comparison still
    shows up in the summary CSV/dashboard instead of silently vanishing from the
    run with the dashboard still showing green."""
    try:
        end = datetime.now()
        start_str = batch_start_time.strftime("%H:%M:%S") if hasattr(batch_start_time, "strftime") else str(batch_start_time)
        create_summary(
            run_at, run_id, validation_name, source_table_name, source_type,
            target_table_name, target_type, source_rows, target_rows, "", output_path,
            "ERROR", start_str, end.strftime("%H:%M:%S"), "0:00:00",
        )
    except Exception:
        logger.warning("Could not write ERROR summary row for table=%s", table_name)


failure_count = 0
system_error = False
processed_tables = set()  # every table_name that actually got a validation attempt
_warned_stale_exclusions = set()  # (yamlfile, exclusions_file) pairs already warned about

# Tables within one yamlfile/validation_dir are independent (own connections,
# own output files) -- see docs/decisions/0002-table-level-thread-pool-parallelism.md.
# Bounded so we don't open more concurrent source/Snowflake connections than
# the DB side can take; override with VALIDATOR_MAX_TABLE_WORKERS if needed.
MAX_TABLE_WORKERS = int(os.environ.get("VALIDATOR_MAX_TABLE_WORKERS", "4"))

# Run mode (docs/decisions/0034-explicit-incremental-execution-mode-contract.md):
# the date range comes only from this process's environment, set by
# runner.start_validation() from the Streamlit date pickers -- never from the
# YAML. Neither set = Historical. A half-set/invalid range stops the run
# before any table executes.
try:
    INCREMENTAL_RANGE = read_incremental_range(os.environ)
except ValueError as e:
    logger.error("Invalid incremental run range, nothing was executed: %s", e)
    sys.exit(2)
run_incremental = INCREMENTAL_RANGE is not None
logger.info("Execution mode: %s", f"Incremental {INCREMENTAL_RANGE[0]}..{INCREMENTAL_RANGE[1]}" if run_incremental else "Historical")

# Guards mutation of the module-level counters/sets below (failure_count,
# system_error, processed_tables, _warned_stale_exclusions) now that multiple
# tables can be validated concurrently on worker threads.
_state_lock = threading.Lock()

#Each validation is process in order
for validation in validation_dirs:
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]
    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    for yamlfile in config_path_yaml:
        # One bad --tables entry used to build a path to a config file that
        # doesn't exist (see utils/utility.py get_config_output_paths), and
        # open() here — outside any try/except — crashed the whole run,
        # aborting every OTHER valid table too. Now it just skips this one
        # config file and keeps going.
        try:
            with open(yamlfile) as f:
                # {env}_EDGE_SILVER -> DEV_/STG_/QAT_/PRD_EDGE_SILVER for this run (ADR 0038).
                config = resolve_env_placeholders(yaml.safe_load(f), environment)
        except (FileNotFoundError, yaml.YAMLError) as e:
            logger.error("Could not load config file %s — skipping it, other tables still run: %s", yamlfile, e)
            failure_count += 1
            system_error = True
            continue
        logger.info("Loaded configuration: %s", config_path_yaml)

        if "all" in tables:
            tables_to_process = config["tables"].items()
        else:
            tables_to_process = [
                (table, config["tables"][table]) for table in tables if table in config["tables"]]

        if not tables_to_process:
            # Not a failure: a validation-type directory holds one config file per
            # source system (postgres.yaml, mssql.yaml, ...) and a requested table
            # only ever lives in one of them. The real "nobody validated this table
            # at all" check is the processed_tables/missing_tables gate at the end
            # of the run, which sees every file across every validation type.
            logger.debug(
                "No tables to process for validation=%s from %s (requested tables=%s not in this particular config file)",
                validation, yamlfile, tables,
            )
            continue

        print("-"*100)

        def _validate_table(table_name, table_config):
            """One table's full validations dict, run on a worker thread — see
            docs/decisions/0002-table-level-thread-pool-parallelism.md. Returns
            (local_failure_count, local_system_error) instead of mutating the
            module-level failure_count/system_error directly, since those aren't
            safe to += from multiple threads without a lock."""
            logger.info("Processing table: %s", table_name)
            local_failure_count = 0
            local_system_error = False
            for validation_name, validation_config in table_config["validations"].items():
                logger.debug("Validation configuration: %s", validation_name)
                # row_hash_validation is the hybrid_v1 Tier-1 helper input (read
                # directly via _row_hash_block below), not an independent
                # validation -- never execute it on its own. See
                # docs/decisions/0035-row-hash-validation-is-helper-block-never-standalone.md.
                if validation_name == "row_hash_validation":
                    continue
                source = validation_config.get("source")
                source_query = validation_config.get("sourcequery")
                target = validation_config.get("target")
                target_query = validation_config.get("targetquery")
                sourcecolumn = validation_config.get("sourcecolumn")
                targetcolumn = validation_config.get("targetcolumn")
                source_table_name = validation_config.get("source_table_name")
                target_table_name = validation_config.get("target_table_name")
                sourcecolumn = validation_config.get("sourcecolumn")
                targetcolumn = validation_config.get("targetcolumn")
                # Database/schema written into YAML at generation time — overrides .env
                source_database = validation_config.get("source_database", "")
                source_schema   = validation_config.get("source_schema", "")
                target_database = validation_config.get("target_database", "")
                target_schema   = validation_config.get("target_schema", "")
                validation_plan = validation_config.get("validation_plan") or {}
                transformation_specs = validation_plan.get("transformations") or []

                if not source or not source_query or str(source_query).strip() in ("SELECT 1;", "SELECT 1"):
                    logger.debug(
                        "Skipping validation_block=%s table=%s — placeholder/metadata block.",
                        validation_name, table_name,
                    )
                    continue

                # Source-only integrity/orphan-key check -- no target, so it
                # never enters the source/target comparison path below. See
                # docs/decisions/0030-progressive-decision-report-pack-incremental-sanity-streamlit.md
                # Decision 2.
                if validation_name == "integrity_check":
                    batch_start_time = datetime.now()
                    try:
                        logger.info("Executing integrity check for table %s", table_name)
                        obj = get_database(source, BASE_DIR, environment,
                                           override_database=source_database,
                                           override_schema=source_schema)
                        source_df = obj.execute_query(source_query)
                        integrity_failures = run_integrity_check(source_df, validation_config)
                        row_count = integrity_failures[0]["row_count"] if integrity_failures else 0
                        status = "FAIL" if integrity_failures else "PASS"
                        if status == "FAIL":
                            local_failure_count += 1
                            logger.warning("Integrity check failed for table=%s: %s violating rows", table_name, row_count)
                        else:
                            logger.info("Integrity check passed for table=%s", table_name)

                        output_file_path = ""
                        if integrity_failures:
                            output_file_path = os.path.join(output_path, f"{table_name}_{validation_name}_violations_{run_id}.csv")
                            source_df.to_csv(output_file_path, index=False)

                        append_validation_audit(
                            Path(output_path) / "validation_audit.jsonl",
                            {
                                "run_id": run_id, "table": table_name, "validation": validation_name,
                                "source": source, "source_query": source_query,
                                "row_count": row_count, "status": status,
                                "quality_failures": integrity_failures,
                            },
                        )
                        batch_end_time = datetime.now()
                        diff_batch = batch_end_time - batch_start_time
                        create_summary(
                            run_at, run_id, validation_name, source_table_name, source, None, None,
                            row_count, None, output_file_path, output_path, status,
                            batch_start_time.strftime("%H:%M:%S"), batch_end_time.strftime("%H:%M:%S"),
                            time.strftime("%H:%M:%S", time.gmtime(diff_batch.total_seconds())),
                        )
                    except (pyodbc.Error, psycopg2.Error):
                        logger.error("Database/network error running integrity check for table=%s", table_name, exc_info=True)
                        local_failure_count += 1
                        local_system_error = True
                        _write_error_summary(table_name, validation_name, source_table_name, source,
                                             None, None, None, None, output_path, batch_start_time)
                    except Exception:
                        logger.error("Unexpected error running integrity check for table=%s", table_name, exc_info=True)
                        local_failure_count += 1
                        local_system_error = True
                        _write_error_summary(table_name, validation_name, source_table_name, source,
                                             None, None, None, None, output_path, batch_start_time)
                    continue

                try:
                    batch_start_time = datetime.now()
                    source_rows = target_rows = None  # defined even if an exception fires before either query runs

                    # Warn once per (yaml, exclusions file) if the exclusions config was
                    # edited after this YAML was generated — exclusion rules are baked in
                    # at generation time (src/core/exclusion_report.py / skip_classifier.py)
                    # and Project/main.py never re-reads config/*_exclusions.yaml, so a
                    # newer exclusions file means this YAML is running on stale rules.
                    if source:
                        excl_file = os.path.join(os.path.dirname(BASE_DIR), "config", f"{source}_exclusions.yaml")
                        _excl_key = (yamlfile, excl_file)
                        with _state_lock:
                            _already_warned = _excl_key in _warned_stale_exclusions
                            if not _already_warned:
                                _warned_stale_exclusions.add(_excl_key)
                        if (not _already_warned
                                and os.path.exists(excl_file) and os.path.exists(yamlfile)
                                and os.path.getmtime(excl_file) > os.path.getmtime(yamlfile)):
                            logger.warning(
                                "%s was modified after %s was generated — regenerate the YAML "
                                "to pick up the new exclusion rules (table=%s).",
                                excl_file, yamlfile, table_name,
                            )

                    # Hybrid Tier-1/Tier-2 large-table path -- opt-in per table via a
                    # `validation_plan.execution_strategy: hybrid_v1` flag (a sibling of
                    # this validation block, not nested inside it -- the `_intent` lookup
                    # a few lines below, inside the non-hybrid branch, reads
                    # validation_config.get("validation_plan") for row_hash column config;
                    # that is a separate, pre-existing lookup at the wrong nesting level
                    # too and is left exactly as-is here (see
                    # docs/large-table-scalable-architecture). Every table that hasn't
                    # opted in falls straight through to today's unchanged fetch+compare
                    # path below -- which is every table today.
                    #
                    # should_dispatch_hybrid() only ever returns True for
                    # validation_name == "data_validation" -- row_hash_validation itself
                    # (and transformation_validation/aggregate_validation) are sibling
                    # blocks under the same "validations:" mapping and must never be
                    # dispatched as if they were their own independent validation
                    # (Phase 2 audit finding F1).
                    _plan_block = table_config["validations"].get("validation_plan") or {}
                    _row_hash_block = table_config["validations"].get("row_hash_validation") or {}
                    _row_hash_spec = (_plan_block.get("row_hash") or {})
                    _row_hash_columns = _row_hash_spec.get("columns") or []
                    use_hybrid = should_dispatch_hybrid(validation_name, _plan_block, _row_hash_block)

                    # Incremental filtering (docs/decisions/0034-explicit-incremental-
                    # execution-mode-contract.md): the YAML's incremental.enabled +
                    # filter_column only say the table CAN run incrementally; whether this
                    # run filters comes from run_incremental (process env). Any leftover
                    # YAML from_date/to_date keys are ignored. An Incremental run of an
                    # unconfigured data_validation block raises here -- caught by this
                    # block's own try/except below as an ERROR row, never silently
                    # falls back to historical.
                    _filter_column = incremental_filter_column(validation_name, _plan_block, run_incremental)
                    apply_incremental = bool(_filter_column)

                    # Fail-fast guard (see docs/decisions/0032-adr-0031-implementation-audit.md
                    # finding #2 / recommendation 1, option (a)): raises instead of silently
                    # running the full, unfiltered hybrid scan -- caught by this block's own
                    # try/except below as an ERROR row, same as every other pre-execution
                    # config problem. See check_hybrid_incremental_conflict()'s docstring for
                    # why hybrid_v1 + incremental can't just work together today.
                    # Table-scoped: the table's hybrid status (from data_validation), not
                    # this block's use_hybrid, so row_hash_validation and other siblings of
                    # a hybrid table are blocked too instead of running filtered (partial run).
                    check_hybrid_incremental_conflict(
                        should_dispatch_hybrid("data_validation", _plan_block, _row_hash_block),
                        apply_incremental,
                    )

                    if use_hybrid:
                        import tiered_runner
                        quality_failures = []
                        grain_failures = []
                        output_file_path = ""
                        hybrid_result = tiered_runner.run_table_hybrid(
                            table_name=table_name,
                            validation_name=validation_name,
                            validation_config=validation_config,
                            row_hash_config=_row_hash_block,
                            row_hash_columns=_row_hash_columns,
                            transformation_specs=transformation_specs,
                            source=source,
                            target=target,
                            environment=environment,
                            base_dir=BASE_DIR,
                            source_database=source_database,
                            source_schema=source_schema,
                            target_database=target_database,
                            target_schema=target_schema,
                            output_path=output_path,
                            run_id=run_id,
                            identity=_plan_block.get("identity"),
                        )
                        source_rows = hybrid_result["source_rows"]
                        target_rows = hybrid_result["target_rows"]
                        is_match = hybrid_result["is_match"]
                        grain_failures = hybrid_result.get("grain_failures", [])
                        quality_failures = hybrid_result.get("quality_failures", [])
                    else:
                        if apply_incremental:
                            _from_date, _to_date = INCREMENTAL_RANGE
                            source_query = apply_incremental_predicate(source_query, _filter_column, _from_date, _to_date)
                            target_query = apply_incremental_predicate(target_query, _filter_column, _from_date, _to_date)
                            logger.info("Incremental filter applied for table %s: %s between %s and %s",
                                        table_name, _filter_column, _from_date, _to_date)

                        #source
                        logger.info("Executing source query for table %s", table_name)
                        logger.debug("Source query: %s", source_query)
                        # Source connects via `environment` too (not hardcoded "local") —
                        # a --environment prod run must read source creds from .env.prod,
                        # same as the target does below, not always from the local .env.
                        obj = get_database(source, BASE_DIR, environment,
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
                        quality_failures = []
                        grain_failures = []

                        output_file_path = ""
                        if validation_name == "count_validation":
                            source_rows = int(source_df['source_row_count'].iloc[0])
                            target_rows = int(target_df['target_row_count'].iloc[0])
                            logger.debug("Source row count: %s", source_rows)
                            logger.debug("Target row count: %s", target_rows)
                            # Opt-in tolerance for tables under active CDC/replication —
                            # default stays 0 (exact match), so existing YAMLs behave
                            # exactly as before unless a table explicitly sets this.
                            # Strict == on a live table produces intermittent FAILs with
                            # no real drift, which trains people to ignore the alert.
                            count_threshold_pct = float(validation_config.get("count_mismatch_threshold_pct", 0))
                            is_match, count_diff_pct = count_validation_match(source_rows, target_rows, count_threshold_pct)
                            if count_threshold_pct > 0:
                                logger.info(
                                    "Count threshold %.4f%% vs actual %.4f%% (source=%s, target=%s)",
                                    count_threshold_pct, count_diff_pct, source_rows, target_rows,
                                )
                        else:
                            import pandas as pd
                            source_rows = len(source_df)
                            target_rows = len(target_df)
                            logger.debug("Source row count: %s", source_rows)
                            logger.debug("Target row count: %s", target_rows)

                            # Fall back to row_hash when no PK configured.
                            if not sourcecolumn or not targetcolumn:
                                sourcecolumn = "row_hash"
                                targetcolumn = "row_hash"

                            # Support both scalar PK (string) and composite PK (list)
                            if isinstance(sourcecolumn, list):
                                pk_src = [c.lower() for c in sourcecolumn]
                                pk_tgt = [c.lower() for c in targetcolumn]
                            else:
                                pk_src = sourcecolumn.lower()
                                pk_tgt = targetcolumn.lower()

                            # row_hash mode: when pk is 'row_hash' but the SQL didn't
                            # produce that column, compute it in Python from the common
                            # columns so any JOIN query can be compared without a real PK.
                            used_row_hash_fallback = (
                                pk_src == "row_hash" or (isinstance(pk_src, list) and pk_src == ["row_hash"])
                            )
                            if used_row_hash_fallback and "row_hash" not in source_df.columns:
                                import hashlib
                                _intent = validation_config.get("validation_plan") or {}
                                _hash_spec = _intent.get("row_hash") or {}
                                _configured = [str(c).lower() for c in (_hash_spec.get("columns") or [])]
                                _common = [c for c in source_df.columns if c in set(target_df.columns)]
                                if _configured:
                                    _common = [c for c in _configured if c in source_df.columns and c in target_df.columns]
                                _algorithm = str(_hash_spec.get("algorithm", "MD5")).upper()
                                _hash_name = "sha256" if _algorithm == "SHA256" else "md5"
                                def _hash_row(row, cols=_common):
                                    def _v(c):
                                        v = row[c]
                                        if v is None or (isinstance(v, float) and v != v):
                                            return "<<NULL>>"
                                        text = str(v).strip() if isinstance(v, str) else str(v)
                                        return text
                                    return hashlib.new(_hash_name, "|".join(_v(c) for c in cols).encode()).hexdigest()
                                source_df["row_hash"] = source_df.apply(_hash_row, axis=1)
                                target_df["row_hash"] = target_df.apply(_hash_row, axis=1)
                                pk_src = pk_tgt = "row_hash"

                            quality_failures = run_quality_checks(source_df, target_df, validation_config)
                            grain_failures = validate_expected_grain(source_df, target_df, validation_config)
                            for quality_failure in quality_failures + grain_failures:
                                logger.warning("Quality check failed for %s: %s", table_name, quality_failure)

                            # PK-indexed multiset comparison — extracted to utils/row_compare.py
                            # so this exact algorithm is shared with the hybrid Tier-1/Tier-2
                            # engine (tiered_runner.py) instead of existing as two copies.
                            result_df = compare_indexed_frames(source_df, target_df, pk_src, pk_tgt, transformation_specs)
                            filepath = os.path.join(output_path, f"{table_name}_{validation_name}_result_{run_id}.csv")
                            result_df.to_csv(filepath, index=False)
                            logger.info("Saved row-level results (%d rows) to %s", len(result_df), filepath)

                            failed_df = result_df[result_df["status"] != "PASS"]
                            if not failed_df.empty:
                                failed_path = os.path.join(output_path, f"{table_name}_{validation_name}_failed_{run_id}.csv")
                                failed_df.to_csv(failed_path, index=False)
                                logger.info("Saved failed rows (%d rows) to %s", len(failed_df), failed_path)

                            n_fail_rows = int((result_df["status"] != "PASS").sum())
                            total_rows = len(result_df)

                            if used_row_hash_fallback and total_rows > 0:
                                n_source_only = int((result_df["status"] == "SOURCE_ONLY").sum())
                                n_target_only = int((result_df["status"] == "TARGET_ONLY").sum())
                                if row_hash_fallback_looks_like_column_drift(n_source_only, n_target_only, total_rows):
                                    logger.warning(
                                        "row_hash fallback for %s: %d SOURCE_ONLY / %d TARGET_ONLY out of %d rows — "
                                        "roughly equal counts on both sides usually means ONE un-normalized column "
                                        "is desyncing the whole row hash, not real missing rows. Configure "
                                        "sourcecolumn/targetcolumn for accurate column-level diffs.",
                                        table_name, n_source_only, n_target_only, total_rows,
                                    )

                            # Optional per-table mismatch threshold (e.g. 0.1 for ≤0.1%)
                            threshold_pct = float(validation_config.get("mismatch_threshold_pct", 0))
                            if threshold_pct > 0 and total_rows > 0:
                                actual_pct = (n_fail_rows / total_rows) * 100
                                is_match = actual_pct <= threshold_pct
                                logger.info("Threshold %.4f%% vs actual %.4f%%", threshold_pct, actual_pct)
                            else:
                                is_match = (n_fail_rows == 0)

                            if quality_failures or grain_failures:
                                is_match = False

                    if is_match:
                        logger.info("Match/Mismatch: Match")
                        status = "PASS"
                        logger.info("Validation passed for table=%s validation=%s", table_name, validation_name)
                    else:
                        logger.info("Match/Mismatch: Mismatch")
                        status = "FAIL"
                        logger.warning("Validation failed for table=%s validation=%s", table_name, validation_name)
                        local_failure_count += 1
                        logger.info("Local failure count for table=%s: %s", table_name, local_failure_count)

                    append_validation_audit(
                        Path(output_path) / "validation_audit.jsonl",
                        {
                            "run_id": run_id,
                            "table": table_name,
                            "validation": validation_name,
                            "source": source,
                            "target": target,
                            "source_query": source_query,
                            "target_query": target_query,
                            "source_filter": validation_config.get("source_filter", ""),
                            "target_filter": validation_config.get("target_filter", ""),
                            "joins": validation_config.get("joins", []),
                            "source_rows": source_rows,
                            "target_rows": target_rows,
                            "status": status,
                            "quality_failures": quality_failures + grain_failures,
                        },
                    )

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
                    local_failure_count += 1
                    local_system_error = True
                    _write_error_summary(table_name, validation_name, source_table_name, source,
                                         target_table_name, target, source_rows, target_rows,
                                         output_path, batch_start_time)
                    continue

                # A crash here (bad PK, malformed YAML block, unexpected schema, ...) used
                # to be logged and swallowed with no effect on failure_count or exit code —
                # notify_failure never fired and the dashboard kept showing green while a
                # table silently dropped out of validation. Now it counts as a real failure.
                except Exception:
                    logger.error(
                        "Unexpected error for table=%s validation=%s",
                        table_name,
                        validation_name,
                        exc_info=True
                    )
                    local_failure_count += 1
                    local_system_error = True
                    _write_error_summary(table_name, validation_name, source_table_name, source,
                                         target_table_name, target, source_rows, target_rows,
                                         output_path, batch_start_time)
                    continue
            return local_failure_count, local_system_error

        with ThreadPoolExecutor(max_workers=MAX_TABLE_WORKERS) as _executor:
            _futures = {}
            for table_name, table_config in tables_to_process:
                with _state_lock:
                    processed_tables.add(table_name)
                _futures[_executor.submit(_validate_table, table_name, table_config)] = table_name
            for _future in as_completed(_futures):
                _table_name = _futures[_future]
                try:
                    _lf, _lse = _future.result()
                except Exception:
                    logger.error("Unhandled exception validating table=%s", _table_name, exc_info=True)
                    _lf, _lse = 1, True
                with _state_lock:
                    failure_count += _lf
                    if _lse:
                        system_error = True

# Manifest/completeness check — nothing else verifies "every requested table
# actually produced a result." A typo'd table name, a table missing from every
# config file, or an empty tables_to_process used to just leave a silent gap:
# no error, no summary row, exit 0. Now a missing table is a counted failure.
if "all" not in tables:
    missing_tables = sorted(set(tables) - processed_tables)
    if missing_tables:
        logger.error(
            "Requested tables never validated (not found in any %s config): %s",
            "/".join(validation_dirs), missing_tables,
        )
        failure_count += len(missing_tables)
        system_error = True

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

