"""
Tests for exclusion reporting.

The point of these tests is not that the strings are pretty — it is that a run
CANNOT report a result without also reporting what it declined to check.
"""

import pytest

from core.exclusion_report import (
    LOW_COVERAGE_THRESHOLD_PCT,
    BatchExclusionReport,
    ExcludedColumn,
    ExclusionReport,
)
from core.validation_plan import CanonicalValidationPlan, ColumnMappingEntry


def _entry(name, target, skip_reason=""):
    return ColumnMappingEntry(
        source_column=name,
        source_type="int",
        source_normalized=name.lower(),
        target_column=target,
        target_type="NUMBER",
        target_normalized=target.lower(),
        match_method="exact",
        skip_validation=bool(skip_reason),
        skip_reason=skip_reason,
    )


def _plan(table="Orders", mappings=None, unmatched=None):
    return CanonicalValidationPlan(
        source_table=table,
        source_db_type="mssql",
        mappings=mappings or [],
        unmatched_source_columns=unmatched or [],
    )


class TestHeadline:
    def test_reports_ratio_and_reasons(self):
        plan = _plan(mappings=[
            _entry("a", "A"), _entry("b", "B"), _entry("c", "C"),
            _entry("uTS", "", "binary type"),
            _entry("ssn", "", "PII policy"),
        ])
        headline = ExclusionReport.from_plan(plan).headline()
        assert "3 of 5 columns validated" in headline
        assert "60.0%" in headline
        assert "uTS (binary type)" in headline
        assert "ssn (PII policy)" in headline

    def test_clean_run_says_no_exclusions(self):
        plan = _plan(mappings=[_entry("a", "A"), _entry("b", "B")])
        headline = ExclusionReport.from_plan(plan).headline()
        assert "no exclusions" in headline
        assert "100.0%" in headline

    def test_headline_is_never_silent_about_exclusions(self):
        plan = _plan(mappings=[_entry("a", "A"), _entry("secret", "", "PII")])
        report = ExclusionReport.from_plan(plan)
        assert report.has_exclusions
        assert "secret" in report.headline()


class TestCoverage:
    def test_low_coverage_flagged(self):
        plan = _plan(mappings=[
            _entry("a", "A"),
            _entry("b", "", "excluded"),
            _entry("c", "", "excluded"),
        ])
        report = ExclusionReport.from_plan(plan)
        assert report.coverage_pct < LOW_COVERAGE_THRESHOLD_PCT
        assert report.is_low_coverage
        assert "LOW COVERAGE" in report.render()

    def test_full_coverage_not_flagged(self):
        plan = _plan(mappings=[_entry("a", "A"), _entry("b", "B")])
        assert not ExclusionReport.from_plan(plan).is_low_coverage

    def test_unmatched_columns_reduce_coverage(self):
        plan = _plan(mappings=[_entry("a", "A")], unmatched=["b", "c"])
        report = ExclusionReport.from_plan(plan)
        assert len(report.excluded) == 2
        assert {c.column for c in report.excluded} == {"b", "c"}

    def test_empty_table_is_zero_not_crash(self):
        report = ExclusionReport.from_plan(_plan(mappings=[]))
        assert report.coverage_pct == 0.0


class TestBatchReport:
    def test_aggregates_across_tables(self):
        batch = BatchExclusionReport()
        batch.add(ExclusionReport("A", total_columns=10, validated_columns=10))
        batch.add(ExclusionReport(
            "B", total_columns=10, validated_columns=4,
            excluded=[ExcludedColumn(f"c{i}", "excluded") for i in range(6)],
        ))
        assert batch.total_columns == 20
        assert batch.validated_columns == 14
        assert batch.coverage_pct == pytest.approx(70.0)

    def test_low_coverage_table_is_called_out_by_name(self):
        batch = BatchExclusionReport()
        batch.add(ExclusionReport("Good", total_columns=10, validated_columns=10))
        batch.add(ExclusionReport("Thin", total_columns=10, validated_columns=2))
        rendered = batch.render()
        assert "Thin" in rendered
        assert "below" in rendered
        assert [r.table for r in batch.low_coverage_tables] == ["Thin"]

    def test_serialisable(self):
        batch = BatchExclusionReport()
        batch.add(ExclusionReport("A", total_columns=4, validated_columns=3,
                                  excluded=[ExcludedColumn("x", "PII")]))
        payload = batch.to_dict()
        assert payload["tables"][0]["excluded"][0]["reason"] == "PII"
        assert payload["coverage_pct"] == pytest.approx(75.0)
