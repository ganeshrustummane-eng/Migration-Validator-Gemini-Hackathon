"""
Column Mapping
===============
The ColumnRuleMapping dataclass — one source→target column pair plus the
transformation rule assigned to it.

This lives in its own module (rather than beside a mapper implementation)
because it is a pure data type shared by the AI mapper, the SQL generators,
the YAML writer, and the exclusion reporter. It has no strategy attached.

Mappings are produced ONLY by AIRuleMapper. The former StaticRuleMapper —
deterministic type-pair matching used as a fallback when AI was unavailable —
was removed: it silently produced mappings indistinguishable from reviewed
ones, which is precisely the failure this validator exists to catch.
"""

from dataclasses import dataclass

from rules import BaseValidationRule


@dataclass
class ColumnRuleMapping:
    """
    A single source→target column pair with its assigned validation rule.

    Attributes:
        source_column   : Column name in the source database
        target_column   : Column name in Snowflake
        source_type     : Source data type string
        target_type     : Snowflake data type string
        rule            : Validation rule assigned to this column pair
        is_primary_key  : True if this column is (part of) the table's PK
        skip_validation : True if the column is excluded from validation
        skip_reason     : Human-readable reason for the exclusion
        matched_by      : 'name' | 'ai' | 'position' | 'exclusion'
    """
    source_column: str
    target_column: str
    source_type: str
    target_type: str
    rule: BaseValidationRule
    is_primary_key: bool = False
    skip_validation: bool = False
    skip_reason: str = ""
    matched_by: str = "name"

    @property
    def normalized_alias(self) -> str:
        """
        Alias used on BOTH sides of the comparison.

        Always derived from the SOURCE column name so the two SELECT lists
        carry identical aliases even when the target column was renamed.
        """
        return f"{self.source_column}_normalized"

    def __repr__(self) -> str:
        pk_tag   = " [PK]"   if self.is_primary_key  else ""
        skip_tag = " [SKIP]" if self.skip_validation else ""
        return (
            f"ColumnRuleMapping({self.source_column}({self.source_type})"
            f" → {self.target_column}({self.target_type})"
            f" rule={self.rule.rule_name}{pk_tag}{skip_tag})"
        )
