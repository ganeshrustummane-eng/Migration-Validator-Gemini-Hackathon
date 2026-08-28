"""
Validation Rule Engine
=======================
Maps a TableProfile to a list of ValidationRequirements.

This is the decision layer between profiling and SQL generation.
It has no AI dependency — all decisions are deterministic and based on
the column groups identified by SchemaProfiler.

Validation requirements produced
---------------------------------
BASELINE (always):
  ① row_count_source       — COUNT(*) on PostgreSQL
  ② row_count_target       — COUNT(*) on Snowflake
  ③ data_validation_source — Normalised full SELECT on PostgreSQL
  ④ data_validation_target — Normalised full SELECT on Snowflake

AGGREGATE (conditional — combined into one query per side):
  ⑤⑥ null_pct             — always (per active column)
  ⑦⑧ distinct_count       — always (per active column)
  ⑨⑩ min_max              — when NUMERIC_FINANCIAL, NUMERIC_QUANTITY, or NUMERIC_GENERIC
  ⑪⑫ sum                  — when NUMERIC_FINANCIAL or NUMERIC_QUANTITY
  ⑬⑭ duplicate_check      — when IDENTIFIER columns are NOT NULL (business key)
  ⑮⑯ value_distribution   — when STATUS_FLAG or TEXT_ENUM columns exist

The query optimizer later collapses all aggregate requirements for the
same side into a single query per side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from profiling.schema_profiler import (
    ColumnGroup,
    ColumnProfile,
    TableProfile,
)


# ---------------------------------------------------------------------------
# Validation type enum
# ---------------------------------------------------------------------------

class ValidationType(Enum):
    # Baseline — always generated
    ROW_COUNT        = "row_count"
    DATA_VALIDATION  = "data_validation"

    # Aggregate — always generated
    NULL_PCT         = "null_pct"
    DISTINCT_COUNT   = "distinct_count"

    # Conditional aggregate
    MIN_MAX          = "min_max"
    SUM              = "sum"
    DUPLICATE_CHECK  = "duplicate_check"
    VALUE_DIST       = "value_distribution"


# ---------------------------------------------------------------------------
# Validation requirement dataclass
# ---------------------------------------------------------------------------

@dataclass
class ValidationRequirement:
    """
    A single validation that should be generated for this table.

    Attributes
    ----------
    validation_type  : What type of validation this is
    columns          : Which columns this validation applies to
                       (empty = table-level, e.g. row count)
    label            : Human-readable label (used as SQL comment)
    query_number_src : ①-style number for the source query (display only)
    query_number_tgt : ①-style number for the target query (display only)
    reason           : Why this validation was triggered
    is_conditional   : False for baseline checks, True for conditional
    """
    validation_type:  ValidationType
    columns:          List[ColumnProfile] = field(default_factory=list)
    label:            str = ""
    query_number_src: str = ""
    query_number_tgt: str = ""
    reason:           str = ""
    is_conditional:   bool = False

    @property
    def column_names(self) -> List[str]:
        return [c.column_name for c in self.columns]


# ---------------------------------------------------------------------------
# ValidationRuleEngine
# ---------------------------------------------------------------------------

class ValidationRuleEngine:
    """
    Converts a TableProfile into an ordered list of ValidationRequirements.

    Usage
    -----
        engine  = ValidationRuleEngine()
        reqs    = engine.decide(table_profile)

        for req in reqs:
            print(req.label, req.reason, req.column_names)
    """

    def decide(self, profile: TableProfile) -> List[ValidationRequirement]:
        """
        Decide which validations are needed for this table.

        Args:
            profile : The TableProfile produced by SchemaProfiler.

        Returns:
            List of ValidationRequirement ordered:
              - Baseline checks first
              - Conditional aggregate checks in descending importance
        """
        requirements: List[ValidationRequirement] = []

        # ── BASELINE: always ──────────────────────────────────────────────
        requirements.append(ValidationRequirement(
            validation_type=ValidationType.ROW_COUNT,
            label="Row Count",
            query_number_src="①",
            query_number_tgt="②",
            reason="Always — foundational check for any migration validation.",
            is_conditional=False,
        ))

        requirements.append(ValidationRequirement(
            validation_type=ValidationType.DATA_VALIDATION,
            label="Full Data Validation (normalised)",
            query_number_src="③",
            query_number_tgt="④",
            reason="Always — proves every value survived migration with correct transformation.",
            is_conditional=False,
        ))

        requirements.append(ValidationRequirement(
            validation_type=ValidationType.NULL_PCT,
            columns=self._active(profile),
            label="NULL % Per Column",
            query_number_src="⑤",
            query_number_tgt="⑥",
            reason="Always — detects unexpected NULL introduction or NULL elimination during ETL.",
            is_conditional=False,
        ))

        requirements.append(ValidationRequirement(
            validation_type=ValidationType.DISTINCT_COUNT,
            columns=self._active(profile),
            label="Distinct Value Count Per Column",
            query_number_src="⑦",
            query_number_tgt="⑧",
            reason="Always — detects cardinality collapse (deduplication gone wrong).",
            is_conditional=False,
        ))

        # ── CONDITIONAL: MIN/MAX ──────────────────────────────────────────
        minmax_cols = profile.numeric_columns
        if minmax_cols:
            requirements.append(ValidationRequirement(
                validation_type=ValidationType.MIN_MAX,
                columns=minmax_cols,
                label="MIN / MAX Per Numeric Column",
                query_number_src="⑨",
                query_number_tgt="⑩",
                reason=(
                    f"Table has {len(minmax_cols)} numeric column(s) "
                    f"({', '.join(c.column_name for c in minmax_cols[:3])}…). "
                    "MIN/MAX detects range truncation, extreme-value loss, and sign flips."
                ),
                is_conditional=True,
            ))

        # ── CONDITIONAL: SUM ─────────────────────────────────────────────
        sum_cols = profile.financial_columns + profile.quantity_columns
        if sum_cols:
            # Deduplicate (a column cannot appear in both but be safe)
            seen: set = set()
            unique_sum_cols = []
            for c in sum_cols:
                if c.column_name not in seen:
                    seen.add(c.column_name)
                    unique_sum_cols.append(c)
            requirements.append(ValidationRequirement(
                validation_type=ValidationType.SUM,
                columns=unique_sum_cols,
                label="SUM Reconciliation (Financial / Quantity)",
                query_number_src="⑪",
                query_number_tgt="⑫",
                reason=(
                    f"Table has {len(unique_sum_cols)} financial/quantity column(s): "
                    f"{', '.join(c.column_name for c in unique_sum_cols[:4])}. "
                    "SUM catches value truncation that is invisible to row-count checks — "
                    "e.g. PG SUM=15,430,250.50 vs SF SUM=15,420,250.50 while rows match."
                ),
                is_conditional=True,
            ))

        # ── CONDITIONAL: DUPLICATE CHECK ──────────────────────────────────
        bk_cols = profile.business_keys
        if bk_cols:
            requirements.append(ValidationRequirement(
                validation_type=ValidationType.DUPLICATE_CHECK,
                columns=bk_cols,
                label="Duplicate Check (Business Key)",
                query_number_src="⑬",
                query_number_tgt="⑭",
                reason=(
                    f"Table has NOT NULL identifier column(s): "
                    f"{', '.join(c.column_name for c in bk_cols[:3])}. "
                    "Duplicate check catches ETL fan-out or dedup failures that "
                    "leave more copies in target than source, or vice versa."
                ),
                is_conditional=True,
            ))

        # ── CONDITIONAL: VALUE DISTRIBUTION ──────────────────────────────
        dist_cols = profile.status_flag_columns + profile.enum_columns
        if dist_cols:
            requirements.append(ValidationRequirement(
                validation_type=ValidationType.VALUE_DIST,
                columns=dist_cols,
                label="Value Distribution (Status / Enum Columns)",
                query_number_src="⑮",
                query_number_tgt="⑯",
                reason=(
                    f"Table has {len(dist_cols)} status/enum column(s): "
                    f"{', '.join(c.column_name for c in dist_cols[:3])}. "
                    "Distribution check catches boolean-to-integer mapping errors "
                    "and enum value truncation."
                ),
                is_conditional=True,
            ))

        return requirements

    def _active(self, profile: TableProfile) -> List[ColumnProfile]:
        """Return all non-skipped columns."""
        return [p for p in profile.column_profiles if p.group != ColumnGroup.SKIPPED]
