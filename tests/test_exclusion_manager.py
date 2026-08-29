"""
Tests for ExclusionManager loading.

The bug these guard: `_load_config` wrapped its success path in a broad
`except Exception`, so a UnicodeEncodeError raised by the *success message*
on a Windows cp1252 console was caught and treated as a config load failure.
Every rule in exclusions.yaml was silently discarded and replaced with a small
built-in default — with no indication that policy had stopped applying.
"""

import pytest
import yaml

from exclusions.exclusion_manager import ExclusionManager

CONFIG = {
    "version": "1.0",
    "global_exclusions": {
        # Note: global rules use 'columns', table-specific rules use 'exclusions'.
        "columns": [
            {"column_name": "_FIVETRAN_DELETED", "reason": "Fivetran metadata"},
        ]
    },
    "pattern_exclusions": {
        "patterns": [
            {"pattern": "^_FIVETRAN_.*", "reason": "Fivetran internal columns"},
        ]
    },
    "table_specific_exclusions": {
        "description": "per-table rules",
        "AcctSoftware": {
            "exclusions": [
                {
                    "column_name": "uTS",
                    "reason": "SQL Server rowversion - not comparable",
                    "applies_to": ["source", "target"],
                },
            ]
        },
    },
}


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "exclusions.yaml"
    path.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    return path


class TestConfigLoading:
    def test_valid_config_is_loaded(self, config_file):
        manager = ExclusionManager(config_path=config_file)
        assert manager._loaded is True

    def test_table_specific_rule_applies(self, config_file):
        manager = ExclusionManager(config_path=config_file)
        decision = manager.should_exclude(
            column_name="uTS",
            source_table="AcctSoftware",
            source_type="timestamp",
        )
        assert decision.excluded
        assert "rowversion" in decision.reason

    def test_pattern_rule_applies(self, config_file):
        manager = ExclusionManager(config_path=config_file)
        decision = manager.should_exclude(
            column_name="_FIVETRAN_SYNCED",
            source_table="AcctSoftware",
            source_type="timestamp",
        )
        assert decision.excluded

    def test_ordinary_column_is_not_excluded(self, config_file):
        manager = ExclusionManager(config_path=config_file)
        decision = manager.should_exclude(
            column_name="sSoftwareName",
            source_table="AcctSoftware",
            source_type="varchar",
        )
        assert not decision.excluded

    def test_every_exclusion_states_a_reason(self, config_file):
        """An exclusion with no reason is a defect, not a config choice."""
        manager = ExclusionManager(config_path=config_file)
        for column in ("uTS", "_FIVETRAN_SYNCED", "_FIVETRAN_DELETED"):
            decision = manager.should_exclude(
                column_name=column, source_table="AcctSoftware", source_type="timestamp"
            )
            if decision.excluded:
                assert decision.reason.strip(), f"{column} excluded without a reason"

    def test_console_encoding_failure_does_not_discard_config(
        self, config_file, monkeypatch, capsys
    ):
        """A print that cannot encode must not silently void exclusion policy."""
        real_print = print
        calls = {"n": 0}

        def exploding_print(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UnicodeEncodeError("charmap", "x", 0, 1, "boom")
            return real_print(*args, **kwargs)

        monkeypatch.setattr("builtins.print", exploding_print)
        with pytest.raises(UnicodeEncodeError):
            ExclusionManager(config_path=config_file)

        monkeypatch.undo()
        manager = ExclusionManager(config_path=config_file)
        assert manager._loaded is True

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        manager = ExclusionManager(config_path=tmp_path / "absent.yaml")
        assert manager._loaded is False
        decision = manager.should_exclude(
            column_name="_FIVETRAN_DELETED", source_table="X", source_type="boolean"
        )
        assert decision.excluded

    def test_malformed_yaml_falls_back_loudly(self, tmp_path, capsys):
        path = tmp_path / "exclusions.yaml"
        path.write_text("global_exclusions: [unclosed", encoding="utf-8")
        manager = ExclusionManager(config_path=path)
        assert manager._loaded is False
        assert "Failed to load exclusions config" in capsys.readouterr().out
