from pathlib import Path

import pandas as pd

from quality_checks import (
    append_validation_audit,
    run_integrity_check,
    run_quality_checks,
    validate_expected_grain,
)


def test_quality_checks_detect_null_and_sum_drift():
    source = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    target = pd.DataFrame({"id": [1, 2], "amount": [10, None]})
    failures = run_quality_checks(
        source,
        target,
        {"quality_checks": {"enabled": True, "null_rate_tolerance_pct": 0, "aggregate_tolerance_pct": 0}},
    )
    assert {failure["check"] for failure in failures} == {"null_rate", "sum", "max"}


def test_expected_grain_rejects_duplicate_join_rows():
    source = pd.DataFrame({"id": [1, 1], "value": ["a", "b"]})
    target = pd.DataFrame({"id": [1], "value": ["a"]})
    failures = validate_expected_grain(
        source,
        target,
        {"expected_grain": "one_row_per_key", "grain_columns": ["id"]},
    )
    assert failures[0]["check"] == "expected_grain"
    assert failures[0]["side"] == "source"


def test_integrity_check_passes_on_zero_rows():
    empty = pd.DataFrame({"id": []})
    assert run_integrity_check(empty, {"test_case": "orphan_customer_id"}) == []


def test_integrity_check_fails_on_any_violation_rows():
    violations = pd.DataFrame({"id": [1, 2]})
    failures = run_integrity_check(violations, {"test_case": "orphan_customer_id", "summary": "orphans"})
    assert failures[0]["check"] == "integrity"
    assert failures[0]["row_count"] == 2
    assert failures[0]["test_case"] == "orphan_customer_id"


def test_audit_appends_jsonl(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    append_validation_audit(audit_path, {"status": "FAIL", "table": "orders"})
    assert audit_path.read_text(encoding="utf-8").count("orders") == 1
