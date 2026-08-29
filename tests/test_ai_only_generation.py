"""
Tests that the static fallback paths are gone.

These are regression guards, not feature tests. The failure mode they protect
against is subtle: when AI was unavailable the tool used to quietly emit
rule-based mappings and SQL that were indistinguishable downstream from
AI-reviewed output. Runs looked green; nobody knew they were degraded.

Failing loudly is the feature.
"""

import importlib

import pytest


class TestStaticMapperRemoved:
    def test_module_no_longer_exists(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("ai_transformation.static_rule_mapper")

    def test_not_exported_from_package(self):
        package = importlib.import_module("ai_transformation")
        assert not hasattr(package, "StaticRuleMapper")

    def test_column_mapping_still_available(self):
        from ai_transformation import ColumnRuleMapping
        assert ColumnRuleMapping.__module__ == "ai_transformation.column_mapping"

    def test_alias_is_derived_from_source_column(self):
        """Both SELECT sides must carry the same alias even after a rename."""
        from ai_transformation.column_mapping import ColumnRuleMapping
        from rules import get_rule_for_type

        mapping = ColumnRuleMapping(
            source_column="customer_id",
            target_column="CUST_ID",
            source_type="int",
            target_type="NUMBER",
            rule=get_rule_for_type("int", "NUMBER"),
        )
        assert mapping.normalized_alias == "customer_id_normalized"


class TestAIRequired:
    def test_rule_mapper_raises_without_api_key(self, monkeypatch):
        from ai_transformation.ai_rule_mapper import AIRuleMappingError
        from ai_transformation.orchestrator import RuleMapperOrchestrator

        monkeypatch.delenv("DIAL_API_KEY", raising=False)
        orchestrator = RuleMapperOrchestrator()
        with pytest.raises(AIRuleMappingError, match="DIAL_API_KEY"):
            orchestrator.map_columns([], [], table_name="events")

    def test_sql_generator_raises_without_api_key(self, monkeypatch):
        from generated_queries.ai_sql_generator import AISQLGenerationError
        from generated_queries.sql_query_generator import SQLQueryGenerator

        monkeypatch.delenv("DIAL_API_KEY", raising=False)
        with pytest.raises(AISQLGenerationError, match="DIAL_API_KEY"):
            SQLQueryGenerator()

    def test_use_ai_false_is_rejected(self):
        from generated_queries.ai_sql_generator import AISQLGenerationError
        from generated_queries.sql_query_generator import SQLQueryGenerator

        with pytest.raises(AISQLGenerationError, match="no longer supported"):
            SQLQueryGenerator(use_ai=False)

    def test_ai_sql_generator_has_no_fallback_method(self):
        from generated_queries.ai_sql_generator import AISQLQueryGenerator

        assert not hasattr(AISQLQueryGenerator, "_fallback_query")

    def test_generator_raises_rather_than_degrading(self, monkeypatch):
        from generated_queries.ai_sql_generator import (
            AISQLGenerationError,
            AISQLQueryGenerator,
        )

        monkeypatch.delenv("DIAL_API_KEY", raising=False)
        generator = AISQLQueryGenerator(api_key="")
        with pytest.raises(AISQLGenerationError):
            generator.generate_validation_query(
                schema="dbo",
                table="Orders",
                mappings=[],
                source_db_type="mssql",
            )
