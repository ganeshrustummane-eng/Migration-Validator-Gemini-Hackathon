"""
Skip Classifier  —  Step 1
===========================
Classifies every skipped column as JUSTIFIED or UNJUSTIFIED.

Design Rule (Data Quality Engineering principle):
    A skip is JUSTIFIED when it is explicitly documented in:
      - config/exclusions.yaml       (table-specific or global)
      - A known auto-pattern         (Fivetran metadata, audit columns, etc.)
      - A type-incompatibility rule  (rowversion, binary, geometry, etc.)

    A skip is UNJUSTIFIED when:
      - A source column has NO matching target column AND no exclusion config
      - A skip_reason is empty / unknown
      - The column silently disappeared without documentation

    UNJUSTIFIED skips are AUTOMATICALLY promoted to FAIL status.
    They must never allow a PASS to hide missing data.

Skip Categories (in priority order):
    1. FIVETRAN_METADATA    → JUSTIFIED  (Fivetran internal columns)
    2. CONFIG_EXCLUSION     → JUSTIFIED  (explicitly in exclusions.yaml)
    3. TYPE_INCOMPATIBLE    → JUSTIFIED  (rowversion, binary, geometry, etc.)
    4. AUDIT_COLUMN         → JUSTIFIED  (etl_*, dw_*, migrated_at, etc.)
    5. NO_TARGET_MATCH      → UNJUSTIFIED → FAIL  (source col absent from target)
    6. NO_SOURCE_MATCH      → WARN only           (target col absent from source)
    7. UNKNOWN              → UNJUSTIFIED → FAIL  (no reason documented)

Usage:
    from core.skip_classifier import SkipClassifier, SkipCategory, SkipVerdict

    classifier = SkipClassifier()
    verdict = classifier.classify(column_name="order_status", skip_reason="")
    if verdict.is_unjustified:
        print(f"FAIL: {verdict.message}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SkipCategory(Enum):
    """Category assigned to a skipped column."""
    FIVETRAN_METADATA   = "fivetran_metadata"    # _FIVETRAN_* columns
    CONFIG_EXCLUSION    = "config_exclusion"     # Documented in exclusions.yaml
    TYPE_INCOMPATIBLE   = "type_incompatible"    # Cannot be compared (binary, rowversion, etc.)
    AUDIT_COLUMN        = "audit_column"         # ETL/DW audit columns
    NO_TARGET_MATCH     = "no_target_match"      # Source col absent from target → FAIL
    NO_SOURCE_MATCH     = "no_source_match"      # Target col absent from source → WARN
    UNKNOWN             = "unknown"              # No documented reason → FAIL


class SkipVerdict(Enum):
    """Outcome decision for a skipped column."""
    JUSTIFIED   = "JUSTIFIED"    # Skip is documented, acceptable
    UNJUSTIFIED = "UNJUSTIFIED"  # Skip has no justification → treated as FAIL
    WARN_ONLY   = "WARN_ONLY"    # Target-only column → warn but don't fail


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkipResult:
    """
    Result of classifying a single skipped column.

    Attributes:
        column_name:  The column that was skipped
        category:     Why it was skipped (SkipCategory)
        verdict:      JUSTIFIED / UNJUSTIFIED / WARN_ONLY
        message:      Human-readable explanation for CLI display
        original_reason: The raw skip_reason from the plan
    """
    column_name:     str
    category:        SkipCategory
    verdict:         SkipVerdict
    message:         str
    original_reason: str = ""

    @property
    def is_justified(self) -> bool:
        return self.verdict == SkipVerdict.JUSTIFIED

    @property
    def is_unjustified(self) -> bool:
        return self.verdict == SkipVerdict.UNJUSTIFIED

    @property
    def is_warn_only(self) -> bool:
        return self.verdict == SkipVerdict.WARN_ONLY

    def cli_line(self, width: int = 30) -> str:
        """Return a formatted single-line string for CLI output."""
        verdict_tag = {
            SkipVerdict.JUSTIFIED:   "✅ JUSTIFIED",
            SkipVerdict.UNJUSTIFIED: "❌ UNJUSTIFIED → FAIL",
            SkipVerdict.WARN_ONLY:   "⚠️  WARN ONLY",
        }[self.verdict]
        return (
            f"    {self.column_name:<{width}} "
            f"[{self.category.value:<20}]  "
            f"{verdict_tag}  —  {self.message}"
        )


# ---------------------------------------------------------------------------
# Pattern registries
# ---------------------------------------------------------------------------

# Fivetran metadata column names (exact or prefix match)
_FIVETRAN_PATTERNS = [
    r"^_fivetran_",
    r"^fivetran_",
    r"^last_modified_by_fivetran$",
    r"^fivetran_sync_status$",
]

# Audit / ETL columns that are always target-side additions
_AUDIT_PATTERNS = [
    r"^etl_",
    r"^dw_",
    r"^load_",
    r"^migrated_",
    r"^created_at_dw$",
    r"^updated_at_dw$",
    r"^record_source$",
    r"^hash_diff$",
    r"^dv_",
    r"^_updated_by_",
    r"^_loaded_at$",
    r"^_source_system$",
    r"^_batch_id$",
    r"^_pipeline_",
    r"^_hash$",
    r"^_checksum$",
]

# Data types that physically cannot be compared cross-database
_INCOMPATIBLE_TYPE_KEYWORDS = [
    "rowversion", "timestamp",     # MSSQL rowversion / PostgreSQL timestamp used as rowversion
    "bytea", "binary", "varbinary", "image",
    "geometry", "geography", "hierarchyid",
    "xml", "cursor", "sql_variant",
    "uniqueidentifier",             # UUID handled differently; flagged when skip_reason says so
]

# Phrases in skip_reason that indicate a config exclusion was applied
_CONFIG_EXCLUSION_PHRASES = [
    "exclusion", "excluded by config", "exclusions.yaml",
    "global exclusion", "table-specific exclusion",
    "pattern exclusion", "type-based exclusion",
    "fivetran metadata", "fivetran soft-delete",
    "pii policy", "pii exclusion",
]

# Phrases in skip_reason that indicate type incompatibility
_TYPE_INCOMPATIBLE_PHRASES = [
    "rowversion", "binary", "not comparable", "incompatible type",
    "no comparison rule", "geometry", "geography", "xml",
    "type mismatch", "no matching rule",
]

# Phrases that indicate the column has no target match
_NO_TARGET_MATCH_PHRASES = [
    "no matching target", "no target match", "not in target",
    "missing in target", "no target column", "unmatched source",
    "absent from target", "dropped column",
]

# Phrases that indicate a target-only column (target has it, source doesn't)
_NO_SOURCE_MATCH_PHRASES = [
    "no matching source", "target only", "target-only",
    "not in source", "missing in source", "enrichment",
    "derived column", "computed column",
]


def _matches_any(text: str, patterns: List[str]) -> bool:
    """Return True if text matches any of the regex patterns (case-insensitive)."""
    lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, lower):
            return True
    return False


def _contains_any(text: str, phrases: List[str]) -> bool:
    """Return True if text contains any of the phrases (case-insensitive)."""
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class SkipClassifier:
    """
    Classifies each skipped column into a category and verdict.

    This is the single authority deciding whether a skip is acceptable.
    It must be strict: any skip without a recognized justification is FAIL.

    Priority order (first match wins):
      1. Fivetran metadata pattern on column name
      2. Config exclusion phrase in skip_reason
      3. Type-incompatible phrase in skip_reason OR data_type
      4. Audit column pattern on column name
      5. No-target-match phrase in skip_reason
      6. No-source-match phrase in skip_reason
      7. Everything else → UNJUSTIFIED (FAIL)
    """

    def classify(
        self,
        column_name: str,
        skip_reason: str,
        data_type: str = "",
    ) -> SkipResult:
        """
        Classify a single skipped column.

        Args:
            column_name:  Original column name from the plan
            skip_reason:  The skip_reason field from ColumnMappingEntry
            data_type:    Source data type (helps classify type-incompatible skips)

        Returns:
            SkipResult with category, verdict, and a human-readable message
        """
        name_lower   = column_name.lower()
        reason_lower = skip_reason.lower()
        type_lower   = data_type.lower()

        # Priority 1 — Fivetran metadata (name pattern takes precedence)
        if _matches_any(name_lower, _FIVETRAN_PATTERNS):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.FIVETRAN_METADATA,
                verdict=SkipVerdict.JUSTIFIED,
                message="Fivetran internal metadata column — not present in source",
                original_reason=skip_reason,
            )

        # Priority 2 — Explicitly in exclusions.yaml / known config phrases
        if _contains_any(reason_lower, _CONFIG_EXCLUSION_PHRASES):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.CONFIG_EXCLUSION,
                verdict=SkipVerdict.JUSTIFIED,
                message=f"Excluded by configuration — {skip_reason}",
                original_reason=skip_reason,
            )

        # Priority 3 — Type incompatibility (reason or data type)
        if (
            _contains_any(reason_lower, _TYPE_INCOMPATIBLE_PHRASES)
            or any(kw in type_lower for kw in _INCOMPATIBLE_TYPE_KEYWORDS)
        ):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.TYPE_INCOMPATIBLE,
                verdict=SkipVerdict.JUSTIFIED,
                message=(
                    f"Data type '{data_type}' cannot be compared cross-database"
                    if data_type else f"Type incompatibility — {skip_reason}"
                ),
                original_reason=skip_reason,
            )

        # Priority 4 — Audit/ETL column name patterns
        if _matches_any(name_lower, _AUDIT_PATTERNS):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.AUDIT_COLUMN,
                verdict=SkipVerdict.JUSTIFIED,
                message="Audit/ETL column added during migration or load process",
                original_reason=skip_reason,
            )

        # Priority 5 — Source column has no target match → UNJUSTIFIED → FAIL
        if _contains_any(reason_lower, _NO_TARGET_MATCH_PHRASES) or (
            not skip_reason and "no" in reason_lower
        ):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.NO_TARGET_MATCH,
                verdict=SkipVerdict.UNJUSTIFIED,
                message=(
                    "Column exists in SOURCE but has NO matching column in TARGET — "
                    "migration may be incomplete. Add to exclusions.yaml if intentional."
                ),
                original_reason=skip_reason,
            )

        # Priority 6 — Target-only column (target has it, source doesn't) → WARN only
        if _contains_any(reason_lower, _NO_SOURCE_MATCH_PHRASES):
            return SkipResult(
                column_name=column_name,
                category=SkipCategory.NO_SOURCE_MATCH,
                verdict=SkipVerdict.WARN_ONLY,
                message="Column exists in TARGET only — added during migration or enrichment",
                original_reason=skip_reason,
            )

        # Priority 7 — No reason at all or unrecognized reason → UNJUSTIFIED → FAIL
        if not skip_reason.strip():
            message = (
                "Column was skipped with NO documented reason — "
                "this is not acceptable. Add a reason to exclusions.yaml."
            )
        else:
            message = (
                f"Skip reason '{skip_reason}' is not recognized as a valid justification. "
                "Update exclusions.yaml or fix the skip_reason."
            )

        return SkipResult(
            column_name=column_name,
            category=SkipCategory.UNKNOWN,
            verdict=SkipVerdict.UNJUSTIFIED,
            message=message,
            original_reason=skip_reason,
        )

    def classify_all(
        self,
        skipped_columns: List[dict],
    ) -> List[SkipResult]:
        """
        Classify a list of skipped column dicts.

        Each dict should have keys: 'column', 'reason', 'type' (optional).

        Args:
            skipped_columns: List of dicts from exclusion_summary()['excluded']

        Returns:
            List of SkipResult, one per column
        """
        results = []
        for item in skipped_columns:
            result = self.classify(
                column_name=item.get("column", ""),
                skip_reason=item.get("reason", ""),
                data_type=item.get("type", ""),
            )
            results.append(result)
        return results

    def has_unjustified(self, results: List[SkipResult]) -> bool:
        """Return True if any skip is UNJUSTIFIED (should cause FAIL)."""
        return any(r.is_unjustified for r in results)

    def unjustified(self, results: List[SkipResult]) -> List[SkipResult]:
        """Return only the UNJUSTIFIED skips."""
        return [r for r in results if r.is_unjustified]

    def justified(self, results: List[SkipResult]) -> List[SkipResult]:
        """Return only the JUSTIFIED skips."""
        return [r for r in results if r.is_justified]

    def warn_only(self, results: List[SkipResult]) -> List[SkipResult]:
        """Return only the WARN_ONLY skips."""
        return [r for r in results if r.is_warn_only]
