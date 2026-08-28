"""
Schema Profiler
================
Analyses ColumnMetadata extracted from a live schema and classifies each
column into one or more semantic groups.

Classification is purely name + type based — no data is read from the
database.  This makes the profiler extremely fast (milliseconds) and safe
to run before any query is executed.

Semantic groups
---------------
  NUMERIC_FINANCIAL  — amount, balance, price, revenue, salary, cost…
  NUMERIC_QUANTITY   — quantity, count, qty, units, total…
  NUMERIC_GENERIC    — any other numeric / decimal column
  TEMPORAL           — date, timestamp columns
  IDENTIFIER         — *_id, *_key, uuid, guid columns
  STATUS_FLAG        — boolean, is_*, has_*, status, active, flag columns
  TEXT_ENUM          — short VARCHAR that looks like an enum (status, type, …)
  TEXT_GENERIC       — all other text columns
  SKIPPED            — JSON, ARRAY, BYTEA — not comparable
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set

from sql_extractor.extractors import ColumnMetadata


# ---------------------------------------------------------------------------
# Semantic column group enum
# ---------------------------------------------------------------------------

class ColumnGroup(Enum):
    NUMERIC_FINANCIAL = "numeric_financial"
    NUMERIC_QUANTITY  = "numeric_quantity"
    NUMERIC_GENERIC   = "numeric_generic"
    TEMPORAL          = "temporal"
    IDENTIFIER        = "identifier"
    STATUS_FLAG       = "status_flag"
    TEXT_ENUM         = "text_enum"
    TEXT_GENERIC      = "text_generic"
    SKIPPED           = "skipped"


# ---------------------------------------------------------------------------
# Keyword sets used for name-based heuristics
# ---------------------------------------------------------------------------

_FINANCIAL_KEYWORDS: Set[str] = {
    "amount", "balance", "price", "cost", "revenue", "salary",
    "fee", "charge", "tax", "discount", "refund", "payment",
    "total", "subtotal", "gross", "net", "value", "rate",
    "commission", "budget", "expense", "income", "profit", "loss",
}

_QUANTITY_KEYWORDS: Set[str] = {
    "quantity", "qty", "count", "units", "stock", "inventory",
    "volume", "level", "capacity", "limit", "threshold",
    "pages", "items", "lines", "rows", "seats",
}

_TEMPORAL_TYPES: Set[str] = {
    "date", "timestamp", "timestamp without time zone",
    "timestamp with time zone", "timestamp_ntz", "timestamp_tz",
    "datetime", "time", "timetz", "timestamptz",
}

_SKIP_TYPES: Set[str] = {
    "json", "jsonb", "array", "bytea", "hstore",
    "tsvector", "tsquery", "xml", "oid", "regclass",
}

_ID_SUFFIXES  = ("_id", "_key", "_code", "_ref", "_uuid", "_guid")
_ENUM_NAMES   = {"status", "type", "state", "category", "kind", "mode",
                 "role", "tier", "stage", "phase", "class"}
_BOOL_PREFIXES = ("is_", "has_", "can_", "should_", "was_", "will_")


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    """
    Classification of a single column.

    Attributes
    ----------
    metadata        : Original ColumnMetadata from the extractor
    group           : Primary semantic group
    extra_groups    : Additional groups (a column can belong to multiple)
    is_nullable     : Mirrors metadata.is_nullable (convenience)
    business_key    : True when column looks like a natural business key
                      (identifier-type, NOT NULL, likely unique)
    has_precision   : True for numeric columns with explicit scale/precision
    """
    metadata:     ColumnMetadata
    group:        ColumnGroup
    extra_groups: List[ColumnGroup] = field(default_factory=list)
    business_key: bool = False
    has_precision: bool = False

    @property
    def column_name(self) -> str:
        return self.metadata.column_name

    @property
    def source_type(self) -> str:
        return self.metadata.data_type

    @property
    def is_nullable(self) -> bool:
        return self.metadata.is_nullable

    @property
    def all_groups(self) -> List[ColumnGroup]:
        return [self.group] + self.extra_groups

    def in_group(self, group: ColumnGroup) -> bool:
        return group in self.all_groups


@dataclass
class TableProfile:
    """
    Full profile of one table: all column profiles + aggregate summaries.

    Attributes
    ----------
    source_schema   : PG schema name
    source_table    : PG table name
    column_profiles : One ColumnProfile per source column
    business_keys   : Columns that look like natural business keys
    has_financial   : Table has financial/monetary columns
    has_temporal    : Table has date/timestamp columns
    has_identifiers : Table has *_id or similar identifier columns
    has_enums       : Table has enum-like text columns
    """
    source_schema:    str
    source_table:     str
    column_profiles:  List[ColumnProfile] = field(default_factory=list)

    # ── Aggregate flags ────────────────────────────────────────────────────

    @property
    def business_keys(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.business_key]

    @property
    def financial_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.NUMERIC_FINANCIAL)]

    @property
    def quantity_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.NUMERIC_QUANTITY)]

    @property
    def numeric_columns(self) -> List[ColumnProfile]:
        """All numeric columns (financial + quantity + generic)."""
        numeric_groups = {
            ColumnGroup.NUMERIC_FINANCIAL,
            ColumnGroup.NUMERIC_QUANTITY,
            ColumnGroup.NUMERIC_GENERIC,
        }
        return [
            p for p in self.column_profiles
            if any(g in numeric_groups for g in p.all_groups)
        ]

    @property
    def temporal_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.TEMPORAL)]

    @property
    def identifier_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.IDENTIFIER)]

    @property
    def status_flag_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.STATUS_FLAG)]

    @property
    def enum_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.in_group(ColumnGroup.TEXT_ENUM)]

    @property
    def skipped_columns(self) -> List[ColumnProfile]:
        return [p for p in self.column_profiles if p.group == ColumnGroup.SKIPPED]

    @property
    def has_financial(self) -> bool:
        return bool(self.financial_columns)

    @property
    def has_temporal(self) -> bool:
        return bool(self.temporal_columns)

    @property
    def has_identifiers(self) -> bool:
        return bool(self.identifier_columns)

    @property
    def has_enums(self) -> bool:
        return bool(self.enum_columns)

    @property
    def has_business_keys(self) -> bool:
        return bool(self.business_keys)

    def summary(self) -> str:
        lines = [
            f"Table: {self.source_schema}.{self.source_table}",
            f"  Total columns    : {len(self.column_profiles)}",
            f"  Financial        : {len(self.financial_columns)} — {_names(self.financial_columns)}",
            f"  Quantity         : {len(self.quantity_columns)} — {_names(self.quantity_columns)}",
            f"  Temporal         : {len(self.temporal_columns)} — {_names(self.temporal_columns)}",
            f"  Identifiers      : {len(self.identifier_columns)} — {_names(self.identifier_columns)}",
            f"  Status/Flag      : {len(self.status_flag_columns)} — {_names(self.status_flag_columns)}",
            f"  Text Enum        : {len(self.enum_columns)} — {_names(self.enum_columns)}",
            f"  Skipped          : {len(self.skipped_columns)} — {_names(self.skipped_columns)}",
            f"  Business keys    : {_names(self.business_keys)}",
        ]
        return "\n".join(lines)


def _names(profiles: List[ColumnProfile]) -> str:
    names = [p.column_name for p in profiles]
    if not names:
        return "(none)"
    if len(names) <= 5:
        return ", ".join(names)
    return ", ".join(names[:5]) + f" … +{len(names) - 5} more"


# ---------------------------------------------------------------------------
# SchemaProfiler
# ---------------------------------------------------------------------------

class SchemaProfiler:
    """
    Classifies a list of ColumnMetadata into a TableProfile.

    Usage
    -----
        profiler = SchemaProfiler()
        profile  = profiler.profile(source_columns, schema="public", table="orders")

    The profiler uses two signals:
      1. Column name keywords (financial amounts, quantity terms, id suffixes, …)
      2. Data type (numeric/decimal → potential financial; boolean → flag; etc.)

    Rules are intentionally conservative — a column is flagged financial only
    if BOTH its name AND its type suggest it.  This avoids false positives on
    integer IDs named "total_lines".
    """

    def profile(
        self,
        source_columns: List[ColumnMetadata],
        schema: str,
        table: str,
    ) -> TableProfile:
        """
        Build a TableProfile from raw column metadata.

        Args:
            source_columns : Columns from the PostgreSQL extractor
            schema         : Source schema name (for display)
            table          : Source table name (for display)

        Returns:
            TableProfile with all columns classified.
        """
        profiles: List[ColumnProfile] = []
        for col in source_columns:
            cp = self._classify(col)
            profiles.append(cp)

        return TableProfile(
            source_schema=schema,
            source_table=table,
            column_profiles=profiles,
        )

    # -----------------------------------------------------------------------
    # Private classification logic
    # -----------------------------------------------------------------------

    def _classify(self, col: ColumnMetadata) -> ColumnProfile:
        name_lower = col.column_name.lower()
        type_lower = col.data_type.lower().strip()
        base_type  = re.sub(r"\s*\([^)]*\)", "", type_lower).strip()

        # ── Skip non-comparable types immediately ──────────────────────────
        if _match_type(base_type, _SKIP_TYPES):
            return ColumnProfile(metadata=col, group=ColumnGroup.SKIPPED)

        # ── Temporal ───────────────────────────────────────────────────────
        if _match_type(base_type, _TEMPORAL_TYPES):
            return ColumnProfile(metadata=col, group=ColumnGroup.TEMPORAL)

        # ── Identifier (checked before numeric — name wins over type) ──────
        # e.g. customer_id bigint NOT NULL is an identifier, not generic numeric
        is_uuid_type = "uuid" in base_type
        is_id_name   = (
            name_lower == "id"
            or name_lower.endswith(_ID_SUFFIXES)
        )
        if is_uuid_type or is_id_name:
            is_bk = not col.is_nullable
            return ColumnProfile(
                metadata=col,
                group=ColumnGroup.IDENTIFIER,
                business_key=is_bk,
            )

        # ── Status / boolean flag ──────────────────────────────────────────
        is_bool_type = base_type in ("boolean", "bool")
        is_flag_name = (
            any(name_lower.startswith(p) for p in _BOOL_PREFIXES)
            or name_lower in {"active", "enabled", "deleted", "archived",
                               "verified", "published", "locked", "visible"}
        )
        if is_bool_type or is_flag_name:
            return ColumnProfile(metadata=col, group=ColumnGroup.STATUS_FLAG)

        # ── Numeric classification ─────────────────────────────────────────
        is_numeric_type = _is_numeric_type(base_type)
        has_precision   = col.numeric_scale is not None and col.numeric_scale > 0

        if is_numeric_type:
            keywords_in_name = _extract_keywords(name_lower)

            if keywords_in_name & _FINANCIAL_KEYWORDS:
                return ColumnProfile(
                    metadata=col,
                    group=ColumnGroup.NUMERIC_FINANCIAL,
                    has_precision=has_precision,
                )
            if keywords_in_name & _QUANTITY_KEYWORDS:
                return ColumnProfile(
                    metadata=col,
                    group=ColumnGroup.NUMERIC_QUANTITY,
                    has_precision=has_precision,
                )
            return ColumnProfile(
                metadata=col,
                group=ColumnGroup.NUMERIC_GENERIC,
                has_precision=has_precision,
            )

        # ── Text: enum-like vs generic ─────────────────────────────────────
        if _is_text_type(base_type):
            root_word = name_lower.split("_")[-1]
            if root_word in _ENUM_NAMES or name_lower in _ENUM_NAMES:
                return ColumnProfile(metadata=col, group=ColumnGroup.TEXT_ENUM)
            return ColumnProfile(metadata=col, group=ColumnGroup.TEXT_GENERIC)

        # ── Fallback ───────────────────────────────────────────────────────
        return ColumnProfile(metadata=col, group=ColumnGroup.TEXT_GENERIC)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _match_type(base_type: str, type_set: Set[str]) -> bool:
    """True if base_type exactly matches any member of type_set."""
    return base_type in type_set


def _is_numeric_type(base_type: str) -> bool:
    numeric_markers = (
        "int", "integer", "bigint", "smallint", "serial", "bigserial",
        "numeric", "decimal", "float", "double", "real", "money",
        "number",
    )
    return any(base_type.startswith(m) or base_type == m for m in numeric_markers)


def _is_text_type(base_type: str) -> bool:
    text_markers = (
        "character", "varchar", "text", "char", "bpchar", "string", "nvarchar",
    )
    return any(base_type.startswith(m) or base_type == m for m in text_markers)


def _extract_keywords(name_lower: str) -> Set[str]:
    """Split a snake_case column name into individual word tokens."""
    return set(re.split(r"[_\s]+", name_lower))
