"""Regression test for Phase 2 audit finding F1: row_hash_validation (and any
other sibling helper block under a table's "validations:" mapping) must never
be dispatched as if it were its own independent validation -- only the real
data_validation block may trigger the hybrid_v1 path.

Exercises the exact per-block iteration main.py's loop performs
(`for validation_name, validation_config in table_config["validations"].items()`)
against a table_config shaped exactly like what
src/generated_queries/yaml_config_writer.py now emits for a hybrid_v1 table,
using the real should_dispatch_hybrid() predicate main.py calls.

Run:  python -m pytest Project/test_hybrid_dispatch.py -q
  or: python Project/test_hybrid_dispatch.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from utils.utility import check_hybrid_incremental_conflict, should_dispatch_hybrid  # noqa: E402

# Shaped exactly like a real generated YAML's "validations:" mapping for a
# table opted into hybrid_v1 with a populated row_hash spec (see
# src/generated_queries/yaml_config_writer.py's row_hash_validation: block).
TABLE_CONFIG = {
    "validations": {
        "data_validation": {
            "source": "postgresql", "sourcequery": "SELECT id, name FROM t;",
            "target": "snowflake", "targetquery": "SELECT id, name FROM T;",
            "pksourcecolumn": "id", "pktargetcolumn": "id",
        },
        "validation_plan": {
            "execution_strategy": "hybrid_v1",
            "row_hash": {"columns": ["id", "name"]},
        },
        "row_hash_validation": {
            "source": "postgresql", "sourcequery": "SELECT id AS record_key, h AS row_hash FROM t;",
            "target": "snowflake", "targetquery": "SELECT id AS record_key, h AS row_hash FROM T;",
        },
        "transformation_validation": {
            "source": "postgresql", "sourcequery": "SELECT amt_value FROM t;",
            "target": "snowflake", "targetquery": "SELECT amt_value FROM T;",
        },
        "count_validation": {
            "source": "postgresql", "sourcequery": "SELECT count(*) FROM t;",
            "target": "snowflake", "targetquery": "SELECT count(*) FROM T;",
        },
    }
}


def _dispatch_decisions(table_config):
    """Mirrors main.py's exact per-block loop: for every validation_name in
    the table's validations mapping, what does should_dispatch_hybrid() say?"""
    plan_block = table_config["validations"].get("validation_plan") or {}
    row_hash_block = table_config["validations"].get("row_hash_validation") or {}
    decisions = {}
    for validation_name in table_config["validations"]:
        decisions[validation_name] = should_dispatch_hybrid(validation_name, plan_block, row_hash_block)
    return decisions


def test_only_data_validation_dispatches_to_hybrid():
    decisions = _dispatch_decisions(TABLE_CONFIG)
    assert decisions["data_validation"] is True


def test_row_hash_validation_never_dispatches_as_its_own_validation():
    decisions = _dispatch_decisions(TABLE_CONFIG)
    assert decisions["row_hash_validation"] is False, (
        "row_hash_validation must never be executed as its own validation "
        "(Phase 2 audit finding F1)"
    )


def test_sibling_helper_blocks_never_dispatch():
    decisions = _dispatch_decisions(TABLE_CONFIG)
    assert decisions["validation_plan"] is False
    assert decisions["transformation_validation"] is False
    assert decisions["count_validation"] is False


def test_no_dispatch_at_all_when_execution_strategy_not_set():
    """Every existing table today (no execution_strategy configured) must
    keep falling through to the unchanged default engine for every block."""
    table_config = {
        "validations": {
            "data_validation": TABLE_CONFIG["validations"]["data_validation"],
            "row_hash_validation": TABLE_CONFIG["validations"]["row_hash_validation"],
        }
    }
    decisions = _dispatch_decisions(table_config)
    assert decisions == {"data_validation": False, "row_hash_validation": False}


def test_main_skips_row_hash_validation_before_placeholder_check():
    """ADR 0035: dispatch=False is not the same as not-executed. main.py runs
    at import, so check its loop source directly: the row_hash_validation
    skip must exist and come before the placeholder check (i.e. before any
    query can run for that block)."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
        src = f.read()
    skip = src.find('if validation_name == "row_hash_validation":\n                    continue')
    placeholder = src.find('if not source or not source_query or str(source_query).strip() in ("SELECT 1;", "SELECT 1"):')
    assert skip != -1 and placeholder != -1
    assert skip < placeholder


def test_main_passes_plan_identity_to_hybrid():
    """ADR 0037: already-generated YAML reaches the PK-less branch without
    regeneration only if main.py hands validation_plan.identity to hybrid."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
        src = f.read()
    call = src[src.find("tiered_runner.run_table_hybrid("):]
    assert 'identity=_plan_block.get("identity")' in call[:call.find(")\n")]


# docs/decisions/0032-adr-0031-implementation-audit.md finding #2 / recommendation
# 1 option (a): hybrid_v1's Tier 1 never receives the incremental predicate, so
# the two must not be allowed to run together silently. These four cases are
# the exact matrix the audit's fix requires.

def test_hybrid_plus_incremental_fails_fast():
    with pytest.raises(ValueError, match="not supported with hybrid_v1"):
        check_hybrid_incremental_conflict(use_hybrid=True, incremental_enabled=True)


def test_hybrid_without_incremental_still_works():
    check_hybrid_incremental_conflict(use_hybrid=True, incremental_enabled=False)  # no raise


def test_standard_execution_with_incremental_still_works():
    check_hybrid_incremental_conflict(use_hybrid=False, incremental_enabled=True)  # no raise


def test_standard_historical_execution_still_works():
    check_hybrid_incremental_conflict(use_hybrid=False, incremental_enabled=False)  # no raise


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
