"""
Contract tests for CanonicalValidationPlan and PlanStore.

The plan is the contract: SQL and YAML are rendered from it. If it does not
round-trip losslessly, regeneration silently changes what gets validated.
"""

import json

import pytest

from core.plan_store import PlanStore, PlanStoreError
from core.validation_plan import (
    PLAN_SCHEMA_VERSION,
    CanonicalValidationPlan,
    ColumnMappingEntry,
)


def _entry(source, target, **kw):
    return ColumnMappingEntry(
        source_column=source,
        source_type=kw.pop("source_type", "int"),
        source_normalized=source.lower(),
        target_column=target,
        target_type=kw.pop("target_type", "NUMBER"),
        target_normalized=target.lower(),
        match_method=kw.pop("match_method", "exact"),
        **kw,
    )


@pytest.fixture
def plan():
    return CanonicalValidationPlan(
        source_database="DevT5000",
        source_db_type="mssql",
        source_schema="dbo",
        source_table="AcctSoftware",
        target_database="DEV_SITELINK_BRONZE",
        target_schema="AWSDEV_DEVT5000_DBO",
        target_table="ACCTSOFTWARE",
        mappings=[
            _entry("AcctSoftwareID", "ACCTSOFTWAREID", is_primary_key=True, pk_ordinal=1),
            _entry("sSoftwareName", "SSOFTWARENAME", source_type="varchar",
                   target_type="VARCHAR", match_method="normalized_exact"),
            _entry("dDeleted", "DDELETED", match_method="fuzzy",
                   fuzzy_score=0.82, confidence=0.71, ai_resolved=True),
            _entry("uTS", "", skip_validation=True,
                   skip_reason="rowversion — not comparable"),
        ],
        unmatched_source_columns=["LegacyFlag"],
        source_primary_keys=["AcctSoftwareID"],
        target_primary_keys=["ACCTSOFTWAREID"],
        has_fivetran_active=True,
        model_used="gpt-4o",
        generated_by="ai",
        ai_calls_made=2,
        warnings=["dDeleted matched with confidence 0.71"],
    )


class TestPlanRoundTrip:
    def test_dict_round_trip_is_lossless(self, plan):
        rebuilt = CanonicalValidationPlan.from_dict(plan.to_dict())
        assert rebuilt.to_dict() == plan.to_dict()

    def test_column_level_fields_survive(self, plan):
        rebuilt = CanonicalValidationPlan.from_dict(plan.to_dict())
        fuzzy = next(m for m in rebuilt.mappings if m.source_column == "dDeleted")
        assert fuzzy.match_method == "fuzzy"
        assert fuzzy.fuzzy_score == pytest.approx(0.82)
        assert fuzzy.confidence == pytest.approx(0.71)
        assert fuzzy.ai_resolved is True

    def test_skip_reason_survives(self, plan):
        rebuilt = CanonicalValidationPlan.from_dict(plan.to_dict())
        skipped = next(m for m in rebuilt.mappings if m.skip_validation)
        assert skipped.source_column == "uTS"
        assert "rowversion" in skipped.skip_reason

    def test_schema_version_is_recorded(self, plan):
        assert plan.to_dict()["schema_version"] == PLAN_SCHEMA_VERSION

    def test_future_schema_version_is_rejected(self, plan):
        payload = plan.to_dict()
        payload["schema_version"] = PLAN_SCHEMA_VERSION + 1
        with pytest.raises(ValueError, match="newer than this build"):
            CanonicalValidationPlan.from_dict(payload)


class TestExclusionSummary:
    def test_counts_skipped_and_unmatched(self, plan):
        summary = plan.exclusion_summary()
        assert summary["total_source_columns"] == 4
        assert summary["validated"] == 3
        # uTS (skipped) + LegacyFlag (unmatched)
        assert summary["excluded_count"] == 2

    def test_unmatched_column_carries_a_reason(self, plan):
        summary = plan.exclusion_summary()
        legacy = next(e for e in summary["excluded"] if e["column"] == "LegacyFlag")
        assert legacy["reason"] == "no matching target column"

    def test_every_exclusion_has_a_reason(self, plan):
        assert all(e["reason"] for e in plan.exclusion_summary()["excluded"])

    def test_coverage_pct(self, plan):
        assert plan.exclusion_summary()["coverage_pct"] == pytest.approx(75.0)

    def test_summary_lines_name_excluded_columns(self, plan):
        text = "\n".join(plan.summary_lines())
        assert "EXCLUDED" in text
        assert "uTS" in text


class TestPlanStore:
    def test_save_then_load(self, plan, tmp_path):
        store = PlanStore(plan_dir=tmp_path)
        path = store.save(plan)
        assert path.exists()
        assert store.load(path).to_dict() == plan.to_dict()

    def test_save_is_idempotent(self, plan, tmp_path):
        store = PlanStore(plan_dir=tmp_path)
        first = store.save(plan).read_text(encoding="utf-8")
        second = store.save(plan).read_text(encoding="utf-8")
        assert first == second

    def test_load_for_table_is_case_insensitive(self, plan, tmp_path):
        store = PlanStore(plan_dir=tmp_path)
        store.save(plan)
        assert store.load_for_table("ACCTSOFTWARE") is not None

    def test_missing_plan_returns_none(self, tmp_path):
        assert PlanStore(plan_dir=tmp_path).load_for_table("nope") is None

    def test_corrupt_plan_raises(self, tmp_path):
        store = PlanStore(plan_dir=tmp_path)
        path = store.path_for("broken")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PlanStoreError, match="not valid JSON"):
            store.load(path)

    def test_plan_json_is_human_readable(self, plan, tmp_path):
        path = PlanStore(plan_dir=tmp_path).save(plan)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["source"]["db_type"] == "mssql"
        assert payload["exclusions"]["excluded_count"] == 2
