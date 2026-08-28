"""
Batch Table Mapping Store
=========================
Persists confirmed source-table -> Snowflake-target-table mappings from the
Batch UI into a Snowflake control table, so an ambiguous match (e.g. source
table `addresses` fuzzy-matching both `ADDRESS` and `ADDRESSES` in Snowflake)
only ever needs a human decision once. Later batch runs look up the last
confirmed mapping for a source table instead of re-guessing.

Uses the same snowflake.connector pattern as
src/sql_extractor/extractors.py::SnowflakeExtractor._get_connection.
Never raises on the write path — persistence must never break a batch run
that already succeeded.
"""

from __future__ import annotations

from typing import Dict

_TABLE_NAME = "MAPPING_REVIEW_LOG"


def _get_connection(account: str, username: str, password: str, database: str):
    import snowflake.connector
    return snowflake.connector.connect(
        user=username, password=password, account=account,
        database=database, login_timeout=30,
    )


def ensure_table(account: str, username: str, password: str, database: str, schema: str) -> None:
    conn = _get_connection(account, username, password, database)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {database}.{schema}.{_TABLE_NAME} (
                    source_table STRING,
                    target_table STRING,
                    confirmed_by STRING,
                    confirmed_at TIMESTAMP_NTZ,
                    source_connection STRING
                )
            """)
    finally:
        conn.close()


def load_confirmed_mappings(
    account: str, username: str, password: str, database: str, schema: str,
) -> Dict[str, str]:
    """Latest confirmed target table per source table, or {} on any failure
    (missing table, unreachable Snowflake, etc.) — callers treat this as a
    best-effort hint, never a hard dependency."""
    try:
        ensure_table(account, username, password, database, schema)
        conn = _get_connection(account, username, password, database)
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT source_table, target_table
                    FROM {database}.{schema}.{_TABLE_NAME}
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY source_table ORDER BY confirmed_at DESC
                    ) = 1
                """)
                return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        return {}


def save_mapping(
    account: str, username: str, password: str, database: str, schema: str,
    source_table: str, target_table: str, confirmed_by: str = "", source_connection: str = "",
) -> None:
    try:
        ensure_table(account, username, password, database, schema)
        conn = _get_connection(account, username, password, database)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {database}.{schema}.{_TABLE_NAME}
                        (source_table, target_table, confirmed_by, confirmed_at, source_connection)
                    SELECT %s, %s, %s, CURRENT_TIMESTAMP(), %s
                    """,
                    (source_table, target_table, confirmed_by, source_connection),
                )
        finally:
            conn.close()
    except Exception:
        # Persistence is a convenience layer on top of a run that already
        # succeeded — never raise out of here.
        pass
