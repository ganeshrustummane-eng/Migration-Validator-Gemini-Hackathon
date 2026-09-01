"""Dry-run verification for the C:\\DB semi-structured fixture pair.

Loads insert_postgres_semistructured.sql into an uncommitted Postgres
transaction and insert_snowflake_semistructured.sql into a Snowflake TEMPORARY
table, applies the real normalization rules plus the Python canonicalizer, and
checks every row's verdict against its own expected_result column.

Nothing is persisted: the Postgres work is rolled back and the Snowflake temp
table disappears with the session.

Run from Project/:  python _verify_semistructured_fixture.py
"""

import importlib.util
import re
import sys

sys.path.insert(0, "..")
sys.path.insert(0, "../src")
sys.path.insert(0, ".")

from db.factory import get_database  # noqa: E402
from rules import get_rule_for_type  # noqa: E402

_spec = importlib.util.spec_from_file_location("sn", "utils/semantic_normalize.py")
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)

PG_FILE = r"C:\DB\insert_postgres_semistructured.sql"
SF_FILE = r"C:\DB\insert_snowflake_semistructured.sql"
SF_FQN = "dev_edge_bronze.STOREDGE_FMS_PUBLIC.SEMISTRUCTURED_TEST"
TMP = "SEMISTRUCTURED_TEST_VERIFY"

# source column -> (source_type, target_type) for rule lookup
COLS = {
    "col_json":         ("json",   "VARIANT"),
    "col_jsonb":        ("jsonb",  "VARIANT"),
    "col_hstore":       ("hstore", "VARIANT"),
    "col_text_array":   ("ARRAY",  "VARIANT"),
    "col_int_array":    ("ARRAY",  "VARIANT"),
    "col_nested_array": ("ARRAY",  "VARIANT"),
}


def _pg_select() -> str:
    exprs = ["COALESCE(CAST(TRIM(test_id) AS TEXT), '<<NULL>>') AS test_id",
             "COALESCE(CAST(TRIM(expected_result) AS TEXT), '<<NULL>>') AS expected_result"]
    for col, (st, tt) in COLS.items():
        exprs.append(get_rule_for_type(st, tt).apply_source("postgresql", col, alias=f"{col}_n"))
    return "SELECT " + ", ".join(exprs) + " FROM postgres_test.semistructured_test"


def _sf_select(table: str) -> str:
    exprs = ['COALESCE(CAST(TRIM(TEST_ID) AS STRING), \'<<NULL>>\') AS "test_id"',
             'COALESCE(CAST(TRIM(EXPECTED_RESULT) AS STRING), \'<<NULL>>\') AS "expected_result"']
    for col, (st, tt) in COLS.items():
        exprs.append(
            get_rule_for_type(st, tt).apply_snowflake(col.upper(), alias=f'"{col}_n"')
        )
    return (f"SELECT " + ", ".join(exprs)
            + f" FROM {table} WHERE _FIVETRAN_ACTIVE = TRUE")


def main() -> int:
    # ── Postgres: load in a transaction we never commit ──────────────────────
    pg_sql = open(PG_FILE, encoding="utf-8").read()
    conn = get_database("postgresql", ".", "local").connect()
    cur = conn.cursor()
    try:
        cur.execute(pg_sql)
        cur.execute(_pg_select())
        pg_cols = [d[0] for d in cur.description]
        pg_rows = {r[0]: dict(zip(pg_cols, r)) for r in cur.fetchall()}
    finally:
        conn.rollback()
        conn.close()
    print(f"Postgres : loaded and normalized {len(pg_rows)} rows (rolled back)")

    # ── Snowflake: same script against a session TEMPORARY table ─────────────
    sf_sql = open(SF_FILE, encoding="utf-8").read()
    # Keep only the CREATE TABLE + INSERT, retargeted at a temp table.
    sf_sql = sf_sql.replace("CREATE OR REPLACE TABLE " + SF_FQN,
                            "CREATE TEMPORARY TABLE " + TMP)
    sf_sql = sf_sql.replace(SF_FQN, TMP)
    sf_sql = re.sub(r"CREATE SCHEMA IF NOT EXISTS[^;]*;", "", sf_sql)
    # Drop the trailing sanity-check SELECTs; we run our own below. Each
    # statement in the file is preceded by comment lines, so strip those before
    # deciding what kind of statement it is.
    def _kind(stmt: str) -> str:
        body = "\n".join(ln for ln in stmt.splitlines()
                         if ln.strip() and not ln.strip().startswith("--"))
        return body.strip().upper()

    statements = [s.strip() for s in sf_sql.split(";") if s.strip()]
    statements = [s for s in statements
                  if _kind(s).startswith(("CREATE TEMPORARY TABLE", "INSERT INTO"))]
    if len(statements) != 2:
        print(f"  !! expected CREATE + INSERT, got {len(statements)} statement(s)")

    sfdb = get_database("snowflake", ".", "dev")
    conn = sfdb.connect()
    cur = conn.cursor()
    try:
        for stmt in statements:
            cur.execute(stmt)
        cur.execute(f"SELECT COUNT(*) FROM {TMP}")
        total = cur.fetchone()[0]
        cur.execute(_sf_select(TMP))
        sf_cols = [d[0] for d in cur.description]
        sf_rows = {r[0]: dict(zip(sf_cols, r)) for r in cur.fetchall()}
    finally:
        conn.close()
    print(f"Snowflake: loaded {total} rows incl. stale, "
          f"{len(sf_rows)} active after _FIVETRAN_ACTIVE filter (temp table)")

    if total - len(sf_rows) != 1:
        print("  !! expected exactly 1 stale row to be filtered out")

    # ── Canonicalize both sides and compare ─────────────────────────────────
    value_cols = [f"{c}_n" for c in COLS]
    bad = []
    for tid in sorted(pg_rows):
        if tid not in sf_rows:
            bad.append((tid, "?", "MISSING IN TARGET", []))
            continue
        s, t = pg_rows[tid], sf_rows[tid]
        diff = [c for c in value_cols
                if sn.canonicalize_value(s[c]) != sn.canonicalize_value(t[c])]
        actual = "FAIL" if diff else "PASS"
        expected = s["expected_result"]
        if actual != expected:
            bad.append((tid, expected, actual, diff))
        print(f"  {'ok ' if actual == expected else 'BAD'} {tid:34} "
              f"expected={expected} actual={actual}"
              + (f"  diff={','.join(c[4:-2] for c in diff)}" if diff else ""))

    print()
    only_tgt = set(sf_rows) - set(pg_rows)
    if only_tgt:
        print(f"rows only in target: {sorted(only_tgt)}")
    if bad:
        print(f"{len(bad)} row(s) did NOT match their expected_result:")
        for tid, exp, act, diff in bad:
            print(f"  {tid}: expected {exp}, got {act} ({diff})")
        return 1
    print(f"ALL {len(pg_rows)} ROWS MATCH THEIR EXPECTED RESULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
