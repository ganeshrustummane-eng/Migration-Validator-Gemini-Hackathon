"""Quick verification that source SQL is dialect-correct per database."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from rules import get_rule_for_type

samples = [
    ("boolean", "boolean", "bPermanent"),
    ("numeric", "number", "dcLongitude"),
    ("timestamp without time zone", "timestamp_ntz", "dUpdated"),
    ("date", "date", "dBirth"),
    ("character varying", "string", "sCity"),
    ("integer", "number", "AddressID"),
    ("uuid", "string", "guid"),
]

for pg_t, sf_t, col in samples:
    rule = get_rule_for_type(pg_t, sf_t)
    print(f"\n[{rule.rule_name}]  {pg_t} -> {sf_t}   col={col}")
    print("  MSSQL :", rule.apply_source("mssql", col, alias=f"{col}_normalized"))
    print("  PG    :", rule.apply_source("postgresql", col, alias=f"{col}_normalized"))
    print("  ATHENA:", rule.apply_source("athena", col, alias=f"{col}_normalized"))
    print("  SF    :", rule.apply_snowflake(col, alias=f"{col}_normalized"))
