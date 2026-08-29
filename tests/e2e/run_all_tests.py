#!/usr/bin/env python
"""Orchestrated framework test from configuration through source/target output."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
OUTPUT_ROOT = REPO_ROOT / "output" / "test_runs"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:
    import yaml
except ImportError as exc:  # pragma: no cover - runner diagnostics
    yaml = None
    YAML_IMPORT_ERROR = str(exc)
else:
    YAML_IMPORT_ERROR = ""


class TestRun:
    """Collect stage results without hiding failures behind one large exception."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.report_dir = OUTPUT_ROOT / run_id
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.stages = []

    def stage(self, name: str, action):
        started = time.perf_counter()
        print(f"\n[{len(self.stages) + 1}] {name}")
        try:
            details = action() or {}
            result = {"name": name, "status": "PASS", "details": details}
            print("    PASS")
        except Exception as exc:  # noqa: BLE001 - stage runner must continue
            result = {"name": name, "status": "ERROR", "error": str(exc)}
            print(f"    ERROR: {exc}")
        result["duration_seconds"] = round(time.perf_counter() - started, 3)
        self.stages.append(result)
        return result

    def write_report(self):
        report = {
            "run_id": self.run_id,
            "started_at": self.run_id,
            "repository": str(REPO_ROOT),
            "stages": self.stages,
            "summary": {
                "passed": sum(stage["status"] == "PASS" for stage in self.stages),
                "errors": sum(stage["status"] == "ERROR" for stage in self.stages),
            },
        }
        path = self.report_dir / "test_report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return path


def required_imports():
    modules = [
        "dotenv",
        "yaml",
        "pandas",
        "psycopg2",
        "pyodbc",
        "snowflake.connector",
        "boto3",
        "src.db.factory",
        "src.validation.validation_executor",
        "src.generated_queries.ai_sql_generator",
        "src.dynamic_suite.query_optimizer",
    ]
    missing = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report exact missing dependency
            missing.append(f"{module}: {exc}")
    if missing:
        raise RuntimeError("Missing or broken imports: " + "; ".join(missing))
    return {"modules_checked": len(modules)}


def yaml_configs():
    if yaml is None:
        raise RuntimeError(f"PyYAML is unavailable: {YAML_IMPORT_ERROR}")
    config_root = REPO_ROOT / "config"
    files = sorted(config_root.glob("**/*.yaml"))
    loaded = 0
    validation_blocks = 0
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            continue
        loaded += 1
        for table in (data.get("tables", {}) if isinstance(data, dict) else {}).values():
            validation_blocks += len(table.get("validations", {})) if isinstance(table, dict) else 0
    if not files:
        raise RuntimeError(f"No YAML files found under {config_root}")
    return {"yaml_files": len(files), "loaded": loaded, "validation_blocks": validation_blocks}


def generated_sql_checks():
    from dynamic_suite.sql_validator import GeneratedSQLValidationError, validate_sql_pair

    validate_sql_pair(
        "SELECT CAST(id AS VARCHAR(MAX)) AS id_normalized FROM dbo.T",
        "SELECT CAST(ID AS STRING) AS id_normalized FROM DB.S.T",
        "mssql",
    )
    try:
        validate_sql_pair("SELECT CAST(id AS TEXT) FROM dbo.T", "SELECT 1", "mssql")
    except GeneratedSQLValidationError:
        pass
    else:
        raise AssertionError("MSSQL SQL validator accepted PostgreSQL TEXT syntax")

    from dynamic_suite.query_optimizer import QueryOptimizer
    from profiling.schema_profiler import ColumnGroup, ColumnProfile
    from profiling.validation_rule_engine import ValidationRequirement, ValidationType
    from sql_extractor.extractors import ColumnMetadata

    column = ColumnProfile(ColumnMetadata(1, "status", "varchar"), ColumnGroup.TEXT_ENUM)
    requirement = ValidationRequirement(
        validation_type=ValidationType.VALUE_DIST,
        columns=[column],
        is_conditional=True,
    )
    queries = QueryOptimizer().optimize(
        [requirement], None, "dbo", "T", "DB.S.T", True, source_db_type="mssql"
    )
    if not queries:
        raise AssertionError("VALUE_DIST did not produce a query")
    query = queries[0].source_sql
    if "GROUP BY" not in query or "COUNT(*) AS value_count" not in query:
        raise AssertionError("VALUE_DIST did not generate a grouped query")
    return {"mssql_guard": "PASS", "value_dist": "PASS"}


def regression_tests():
    if importlib.util.find_spec("pytest"):
        command = [sys.executable, "-m", "pytest", "-q"]
    elif shutil.which("pytest"):
        command = [shutil.which("pytest"), "-q"]
    else:
        candidates = [Path("C:/Program Files/Python313/python.exe"), Path("C:/Python313/python.exe")]
        command = next(
            ([str(candidate), "-m", "pytest", "-q"] for candidate in candidates if candidate.exists()),
            None,
        )
        if command is None:
            raise RuntimeError("pytest is not installed in a discoverable Python environment")
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stdout + "\n" + completed.stderr)

    output = completed.stdout.strip()
    match = re.search(r"(\d+) passed", output)
    collected = int(match.group(1)) if match else 0
    # pytest exits 0 when it collects nothing, so this stage used to report PASS
    # while running no tests at all. Refuse to call an empty run a success.
    if collected == 0:
        raise RuntimeError(
            "pytest collected 0 tests — the regression stage would have reported "
            "PASS without running anything. Check pytest.ini and tests/.\n" + output
        )
    return {"command": " ".join(command), "tests_passed": collected, "output": output}


def connection_tests():
    from test_env_connections import test_athena, test_mssql, test_postgresql, test_snowflake

    checks = {
        "postgresql": test_postgresql(),
        "mssql": test_mssql(),
        "snowflake": test_snowflake(),
        "athena": test_athena(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"Connection failures: {', '.join(failed)}")
    return checks


def live_validation(args):
    from src.validation.validation_executor import ValidationExecutor

    executor = ValidationExecutor(base_dir=str(REPO_ROOT))
    results = executor.execute_batch(
        layer=args.layer,
        tables=args.tables or ["all"],
        validation_types=args.validation_types,
        config_dir=str(REPO_ROOT / "config"),
    )
    errors = {key: value.get("error") for key, value in results.items() if value.get("status") == "ERROR"}
    data_findings = {key: value.get("status") for key, value in results.items() if value.get("status") == "FAIL"}
    if errors:
        raise RuntimeError(f"Validation execution errors: {errors}")
    return {"checks": len(results), "data_quality_findings": data_findings}


def output_artifacts(run: TestRun):
    artifacts = [
        path for path in (REPO_ROOT / "output").glob("**/*")
        if path.is_file() and path != run.report_dir / "test_report.json"
    ]
    if not artifacts:
        raise RuntimeError("No output artifacts found under repository output/")
    return {"artifact_count": len(artifacts), "latest": str(max(artifacts, key=lambda item: item.stat().st_mtime))}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-live", action="store_true", help="Skip database connections and live YAML execution")
    parser.add_argument("--layer", default="bronze", choices=["bronze", "silver", "gold", "reporting"])
    parser.add_argument("--tables", nargs="*", help="Optional source table names; default is all configured tables")
    parser.add_argument(
        "--validation-types",
        nargs="+",
        default=["count_validation", "data_validation"],
        choices=["count_validation", "data_validation"],
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run = TestRun(run_id)
    run.stage("Dependencies and imports", required_imports)
    run.stage("YAML configuration loading", yaml_configs)
    run.stage("Generated SQL and dialect checks", generated_sql_checks)
    run.stage("Regression tests", regression_tests)
    if not args.skip_live:
        run.stage("Live database connections", connection_tests)
        run.stage("Live source-to-target validation", lambda: live_validation(args))
    else:
        print("\nLive stages skipped (--skip-live)")
    run.stage("Output artifacts", lambda: output_artifacts(run))
    report_path = run.write_report()
    errors = sum(stage["status"] == "ERROR" for stage in run.stages)
    print(f"\nReport: {report_path}")
    print(f"Passed stages: {len(run.stages) - errors}/{len(run.stages)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
