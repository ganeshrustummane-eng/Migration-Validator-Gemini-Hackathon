"""
Exact Column Matcher
=====================
Deterministic matching before any fuzzy or AI logic runs.

Matching priority (tried in order, first success wins):
  1. Exact original-name match     (case-insensitive): 'amount' == 'AMOUNT'
  2. Normalized-name match         : 'created_at' == 'CREATEDAT' (both → 'createdat')
  3. Configured explicit mapping   : user-specified overrides (future hook)

No AI is called for exact matches. Confidence is always 1.0.

Results
-------
ExactMatchResult.method values:
  "exact"            — original names matched case-insensitively
  "normalized_exact" — normalized names matched
  None               — no exact match found (proceed to fuzzy)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sql_extractor.extractors import ColumnMetadata
from matching.normalizer import normalize_column_name


@dataclass
class ExactMatchResult:
    """
    Result of an exact matching attempt for one source column.

    Attributes:
        source_col   : The source ColumnMetadata being matched
        target_col   : The matched target ColumnMetadata (None if unmatched)
        method       : 'exact' | 'normalized_exact' | None
        confidence   : 1.0 for any exact match, 0.0 if unmatched
    """
    source_col: ColumnMetadata
    target_col: Optional[ColumnMetadata]
    method: Optional[str]
    confidence: float

    @property
    def matched(self) -> bool:
        return self.target_col is not None


class ExactMatcher:
    """
    Performs deterministic exact matching between source and target columns.

    Call match_all() to get results for every source column.
    Unmatched columns are returned with target_col=None and method=None —
    they proceed to the fuzzy/AI pipeline.

    Token-efficiency guarantee: zero AI calls are made for exact matches.
    """

    def match_all(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        explicit_mappings: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[ExactMatchResult], List[ColumnMetadata]]:
        """
        Try to exactly match every source column to a target column.

        Args:
            source_columns  : PostgreSQL columns
            target_columns  : Snowflake columns
            explicit_mappings: Optional dict {source_name: target_name} for
                               user-configured overrides (highest priority)

        Returns:
            Tuple of:
              - results       : ExactMatchResult for every source column
              - unmatched_tgt : Target columns that no source matched to
                                (candidates for fuzzy matching against unmatched sources)
        """
        explicit = {k.upper(): v.upper() for k, v in (explicit_mappings or {}).items()}

        # Build lookup indices for target columns
        by_original:   Dict[str, ColumnMetadata] = {}  # UPPER(original_name) → col
        by_normalized: Dict[str, ColumnMetadata] = {}  # normalized_name → col
        for col in target_columns:
            by_original[col.column_name.upper()] = col
            norm = normalize_column_name(col.column_name)
            # First registered wins for normalized duplicates (preserve ordinal order)
            if norm not in by_normalized:
                by_normalized[norm] = col

        matched_target_names: set = set()
        results: List[ExactMatchResult] = []

        for src in source_columns:
            src_upper = src.column_name.upper()
            src_norm  = normalize_column_name(src.column_name)

            # Priority 0: Explicit configured mapping
            if src_upper in explicit:
                tgt_name = explicit[src_upper]
                tgt = by_original.get(tgt_name)
                if tgt:
                    matched_target_names.add(tgt.column_name.upper())
                    results.append(ExactMatchResult(src, tgt, "configured", 1.0))
                    continue

            # Priority 1: Exact case-insensitive original name match
            tgt = by_original.get(src_upper)
            if tgt:
                matched_target_names.add(tgt.column_name.upper())
                results.append(ExactMatchResult(src, tgt, "exact", 1.0))
                continue

            # Priority 2: Normalized name match
            tgt = by_normalized.get(src_norm)
            if tgt:
                matched_target_names.add(tgt.column_name.upper())
                results.append(ExactMatchResult(src, tgt, "normalized_exact", 1.0))
                continue

            # No exact match found
            results.append(ExactMatchResult(src, None, None, 0.0))

        # Target columns that weren't matched — available for fuzzy matching
        unmatched_tgt = [
            col for col in target_columns
            if col.column_name.upper() not in matched_target_names
        ]

        return results, unmatched_tgt

    def match_one(
        self,
        source_col: ColumnMetadata,
        target_columns: List[ColumnMetadata],
    ) -> ExactMatchResult:
        """
        Try to exactly match a single source column.

        Args:
            source_col     : Source column to match
            target_columns : All available target columns

        Returns:
            ExactMatchResult (target_col=None if not matched)
        """
        results, _ = self.match_all([source_col], target_columns)
        return results[0]
