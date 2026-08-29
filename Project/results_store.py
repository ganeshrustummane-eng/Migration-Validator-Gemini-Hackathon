"""
SQLite-backed history for Project/main.py validation runs — replaces
grepping through Project/output/<layer>/validation_<run_id>/*_summary.csv
by hand. Every run_validation() call in runner.py appends here, so the
webapp can show trends/drill-down without re-parsing CSVs from disk.

Counts and status only — never row-level values (those stay in the
per-table diff CSVs on local disk, untouched by this module).
"""
import sqlite3
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "output" / "validation_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    layer       TEXT NOT NULL,
    environment TEXT NOT NULL,
    returncode  INTEGER NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    layer              TEXT NOT NULL,
    validation_type    TEXT NOT NULL,
    source_table_name  TEXT,
    source_type        TEXT,
    target_table_name  TEXT,
    target_type        TEXT,
    source_count       INTEGER,
    target_count       INTEGER,
    count_difference   INTEGER,
    missing_in_source  INTEGER,
    missing_in_target  INTEGER,
    status             TEXT,
    run_at             TEXT,
    batch_start_time   TEXT,
    batch_end_time     TEXT,
    total_time_taken   TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_results_table  ON results(source_table_name);
CREATE INDEX IF NOT EXISTS idx_results_run    ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON results(status);
"""

_RESULT_COLUMNS = [
    "source_table_name", "source_type", "target_table_name", "target_type",
    "source_count", "target_count", "count_difference",
    "missing_in_source", "missing_in_target", "status",
    "run_at", "batch_start_time", "batch_end_time", "total_time_taken",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def record_run(run_id: str, layer: str, environment: str, returncode: int,
               summaries: dict) -> None:
    """summaries: {"count_validation": DataFrame, "data_validation": DataFrame}
    as produced by runner.run_validation()."""
    if not run_id:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, layer, environment, returncode) VALUES (?, ?, ?, ?)",
            (run_id, layer, environment, returncode),
        )
        conn.execute("DELETE FROM results WHERE run_id = ?", (run_id,))
        for vtype, df in summaries.items():
            for _, row in df.iterrows():
                values = [row[c] if c in df.columns and pd.notna(row.get(c)) else None for c in _RESULT_COLUMNS]
                conn.execute(
                    f"INSERT INTO results (run_id, layer, validation_type, {', '.join(_RESULT_COLUMNS)}) "
                    f"VALUES (?, ?, ?, {', '.join(['?'] * len(_RESULT_COLUMNS))})",
                    (run_id, layer, vtype, *values),
                )
        conn.commit()


def query_runs(layer: str = None, limit: int = 50) -> pd.DataFrame:
    """One row per run, with pass/fail counts aggregated from results."""
    with _connect() as conn:
        where = "WHERE r.layer = ?" if layer else ""
        params = [layer] if layer else []
        sql = f"""
            SELECT r.run_id, r.layer, r.environment, r.returncode, r.recorded_at,
                   COUNT(res.id) AS checks,
                   SUM(CASE WHEN res.status = 'PASS' THEN 1 ELSE 0 END) AS passed,
                   SUM(CASE WHEN res.status = 'FAIL' THEN 1 ELSE 0 END) AS failed
            FROM runs r
            LEFT JOIN results res ON res.run_id = r.run_id
            {where}
            GROUP BY r.run_id
            ORDER BY r.recorded_at DESC
            LIMIT ?
        """
        return pd.read_sql_query(sql, conn, params=[*params, limit])


def query_results(layer: str = None, table: str = None, status: str = None,
                   validation_type: str = None, limit: int = 1000) -> pd.DataFrame:
    with _connect() as conn:
        clauses, params = [], []
        if layer:
            clauses.append("layer = ?"); params.append(layer)
        if table:
            clauses.append("source_table_name = ?"); params.append(table)
        if status:
            clauses.append("status = ?"); params.append(status)
        if validation_type:
            clauses.append("validation_type = ?"); params.append(validation_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM results {where} ORDER BY run_at DESC LIMIT ?"
        return pd.read_sql_query(sql, conn, params=[*params, limit])


def distinct_tables(layer: str = None) -> list:
    with _connect() as conn:
        if layer:
            rows = conn.execute(
                "SELECT DISTINCT source_table_name FROM results WHERE layer = ? ORDER BY 1", (layer,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT source_table_name FROM results ORDER BY 1").fetchall()
        return [r[0] for r in rows if r[0]]


def table_trend(table_name: str, validation_type: str = None) -> pd.DataFrame:
    with _connect() as conn:
        clauses, params = ["source_table_name = ?"], [table_name]
        if validation_type:
            clauses.append("validation_type = ?"); params.append(validation_type)
        sql = f"SELECT * FROM results WHERE {' AND '.join(clauses)} ORDER BY run_at ASC"
        return pd.read_sql_query(sql, conn, params=params)
