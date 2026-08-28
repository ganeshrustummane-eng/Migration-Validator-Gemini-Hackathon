"""
Tests for config schema validation and idempotent YAML writing.

These cover the two bugs that made the YAML layer untrustworthy:
  1. write_count_yaml appended, producing duplicate top-level table keys that
     YAML silently resolved last-wins.
  2. Configs were consumed without schema validation, so typos surfaced deep
     inside execution after connections were already open.
"""

import pytest
import yaml

from generated_queries.sql_query_generator import ValidationQuerySet
from generated_queries.yaml_config_writer import YAMLConfigWriter
from validation.config_schema import (
    find_duplicate_table_keys,
    validate_config_dir,
    validate_config_file,
)

COUNT_SRC = "SELECT COUNT(*) AS source_row_count\nFROM dbo.AcctSoftware;"
COUNT_TGT = "SELECT COUNT(*) AS target_row_count\nFROM DB.SC.ACCTSOFTWARE;"


def _query_set(table="AcctSoftware"):
    qs = ValidationQuerySet(
        table_name=table,
        source_db_label="mssql",
        target_db_label="snowflake",
        generated_by="ai",
        model_used="gpt-4o",
    )
    qs.row_count_source = f"-- (1) ROW COUNT\n-- AI-generated\n{COUNT_SRC}"
    qs.row_count_target = f"-- (2) ROW COUNT\n{COUNT_TGT}"
    return qs


def _write_count(tmp_path, table="AcctSoftware"):
    return YAMLConfigWriter().write_count_yaml(
        query_set=_query_set(table),
        source_db_type="mssql",
        pg_schema="dbo",
        pg_table=table,
        sf_database="DB",
        sf_schema="SC",
        sf_table=table.upper(),
        has_fivetran_active=True,
        output_dir=tmp_path,
    )


class TestIdempotentWrites:
    def test_rewriting_same_table_does_not_duplicate(self, tmp_path):
        for _ in range(3):
            path = _write_count(tmp_path)
        assert find_duplicate_table_keys(path) == []
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert list(document["tables"]) == ["AcctSoftware"]

    def test_repeat_write_is_byte_identical(self, tmp_path):
        first = _write_count(tmp_path).read_text(encoding="utf-8")
        second = _write_count(tmp_path).read_text(encoding="utf-8")
        assert first == second

    def test_second_table_is_added_not_replaced(self, tmp_path):
        _write_count(tmp_path, "AcctSoftware")
        path = _write_count(tmp_path, "Addresses")
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(document["tables"]) == {"AcctSoftware", "Addresses"}

    def test_generator_comment_headers_are_stripped(self, tmp_path):
        path = _write_count(tmp_path)
        block = yaml.safe_load(path.read_text(encoding="utf-8"))["tables"]
        query = block["AcctSoftware"]["validations"]["count_validation"]["sourcequery"]
        assert not query.lstrip().startswith("--")
        assert query.lstrip().upper().startswith("SELECT")

    def test_unparseable_existing_file_is_rebuilt(self, tmp_path):
        path = _write_count(tmp_path)
        path.write_text("tables: [ this is not valid", encoding="utf-8")
        rebuilt = _write_count(tmp_path)
        assert yaml.safe_load(rebuilt.read_text(encoding="utf-8"))["tables"]


class TestDuplicateDetection:
    def test_detects_duplicate_table_key(self, tmp_path):
        path = tmp_path / "bronze.yaml"
        block = (
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: mssql\n"
            "        sourcequery: |\n"
            "          SELECT 1;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n"
        )
        path.write_text(f"tables:\n  AcctSoftware:\n{block}  AcctSoftware:\n{block}",
                        encoding="utf-8")
        assert find_duplicate_table_keys(path) == ["AcctSoftware"]

    def test_duplicate_is_reported_as_an_error(self, tmp_path):
        path = tmp_path / "bronze.yaml"
        block = (
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: mssql\n"
            "        sourcequery: |\n"
            "          SELECT 1;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n"
        )
        path.write_text(f"tables:\n  A:\n{block}  A:\n{block}", encoding="utf-8")
        _, errors = validate_config_file(path, check_credentials=False)
        assert any("more than once" in e for e in errors)


class TestSchemaValidation:
    def test_generated_config_is_valid(self, tmp_path):
        path = _write_count(tmp_path)
        document, errors = validate_config_file(path, check_credentials=False)
        assert errors == []
        assert document is not None

    def test_misspelled_field_is_caught(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            "tables:\n"
            "  A:\n"
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: mssql\n"
            "        source_query: |\n"          # should be 'sourcequery'
            "          SELECT 1;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n",
            encoding="utf-8",
        )
        document, errors = validate_config_file(path, check_credentials=False)
        assert document is None
        assert any("sourcequery" in e for e in errors)

    def test_non_select_query_is_rejected(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            "tables:\n"
            "  A:\n"
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: mssql\n"
            "        sourcequery: |\n"
            "          DROP TABLE users;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n",
            encoding="utf-8",
        )
        document, errors = validate_config_file(path, check_credentials=False)
        assert document is None
        assert any("SELECT" in e for e in errors)

    def test_unknown_dialect_is_rejected(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            "tables:\n"
            "  A:\n"
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: oracle\n"
            "        sourcequery: |\n"
            "          SELECT 1;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n",
            encoding="utf-8",
        )
        _, errors = validate_config_file(path, check_credentials=False)
        assert any("oracle" in e for e in errors)

    def test_empty_file_is_reported(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        document, errors = validate_config_file(path, check_credentials=False)
        assert document is None
        assert any("empty" in e for e in errors)

    def test_missing_env_credential_is_reported(self, tmp_path, monkeypatch):
        for key in list(__import__("os").environ):
            if key.startswith("SRC_99_"):
                monkeypatch.delenv(key, raising=False)
        path = tmp_path / "cred.yaml"
        path.write_text(
            "tables:\n"
            "  A:\n"
            "    validations:\n"
            "      count_validation:\n"
            "        source_table_name: A\n"
            "        source: mssql\n"
            "        source_name: SRC_99\n"
            "        sourcequery: |\n"
            "          SELECT 1;\n"
            "        target_table_name: A\n"
            "        target: snowflake\n"
            "        targetquery: |\n"
            "          SELECT 1;\n",
            encoding="utf-8",
        )
        _, errors = validate_config_file(path, check_credentials=True)
        assert any("SRC_99" in e for e in errors)

    def test_validate_dir_reports_per_file(self, tmp_path):
        _write_count(tmp_path)
        results = validate_config_dir(tmp_path, check_credentials=False)
        assert results
        assert all(errors == [] for errors in results.values())

    def test_policy_files_are_not_linted_as_validation_configs(self, tmp_path):
        _write_count(tmp_path)
        (tmp_path / "exclusions.yaml").write_text(
            "version: '1.0'\nglobal_exclusions:\n  columns: []\n", encoding="utf-8"
        )
        results = validate_config_dir(tmp_path, check_credentials=False)
        assert not any(p.name == "exclusions.yaml" for p in results)
