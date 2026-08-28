"""
Fuzzy Column Matcher
=====================
Uses RapidFuzz (or a pure-Python fallback) to generate ranked candidate
target columns for source columns that could not be exactly matched.

Algorithm
---------
For each unmatched source column, compare its normalized name against every
unmatched target column's normalized name using the token_ratio scorer.
Return results sorted descending by score.

Thresholds (configurable)
-------------------------
HIGH_CONFIDENCE_THRESHOLD = 0.95
  Above this: accept automatically without AI review.

AI_REVIEW_THRESHOLD = 0.80
  Between AI_REVIEW and HIGH_CONFIDENCE: send to AI for verification.
  Below AI_REVIEW (< 80%): column is reported as low-confidence / FAIL.

These can be overridden per call or via environment variables.

RapidFuzz availability
----------------------
If rapidfuzz is not installed, falls back to SequenceMatcher from difflib.
The fallback produces lower-quality scores but the pipeline still works.
Install for best results: pip install rapidfuzz
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from sql_extractor.extractors import ColumnMetadata
from matching.normalizer import normalize_column_name


# ---------------------------------------------------------------------------
# Default thresholds — configurable via env vars or constructor
# ---------------------------------------------------------------------------

_DEFAULT_HIGH_CONFIDENCE = float(os.getenv("FUZZY_HIGH_CONFIDENCE", "0.95"))
_DEFAULT_AI_REVIEW       = float(os.getenv("FUZZY_AI_REVIEW",       "0.80"))


@dataclass
class FuzzyCandidate:
    """
    One candidate target column ranked by fuzzy similarity to a source column.

    Attributes:
        source_col        : The source column being matched
        target_col        : Candidate target column
        fuzzy_score       : Similarity score 0.0–1.0 (normalized name comparison)
        source_norm       : Normalized source name (informational)
        target_norm       : Normalized target name (informational)
        above_high        : True if score ≥ HIGH_CONFIDENCE_THRESHOLD (auto-accept)
        needs_ai_review   : True if score in [AI_REVIEW_THRESHOLD, HIGH_CONFIDENCE)
        below_threshold   : True if score < AI_REVIEW_THRESHOLD
    """
    source_col: ColumnMetadata
    target_col: ColumnMetadata
    fuzzy_score: float
    source_norm: str
    target_norm: str
    above_high: bool = False
    needs_ai_review: bool = False
    below_threshold: bool = False

    def __str__(self) -> str:
        return (
            f"{self.source_col.column_name} → {self.target_col.column_name} "
            f"({self.source_norm} ~ {self.target_norm}): {self.fuzzy_score:.3f}"
        )


@dataclass
class FuzzyMatchGroup:
    """
    All ranked candidates for one source column.

    Attributes:
        source_col  : The source column being matched
        candidates  : Sorted list of FuzzyCandidate (best score first)
        top          : Best candidate (None if no candidates)
    """
    source_col: ColumnMetadata
    candidates: List[FuzzyCandidate] = field(default_factory=list)

    @property
    def top(self) -> Optional[FuzzyCandidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def top_score(self) -> float:
        return self.top.fuzzy_score if self.top else 0.0

    @property
    def has_high_confidence_match(self) -> bool:
        return bool(self.top and self.top.above_high)

    @property
    def needs_ai_review(self) -> bool:
        return bool(self.top and self.top.needs_ai_review)


class FuzzyMatcher:
    """
    Generates ranked fuzzy match candidates using normalized name similarity.

    Usage
    -----
        matcher = FuzzyMatcher()
        groups = matcher.match_unmatched(unmatched_src, unmatched_tgt)
        for group in groups:
            print(group.source_col.column_name, "best:", group.top)

    The matcher does NOT make any matching decisions — it only ranks candidates.
    CandidateMatcher uses these rankings to decide what to auto-accept vs send to AI.
    """

    def __init__(
        self,
        high_confidence_threshold: float = _DEFAULT_HIGH_CONFIDENCE,
        ai_review_threshold:       float = _DEFAULT_AI_REVIEW,
    ):
        """
        Args:
            high_confidence_threshold : Score ≥ this → auto-accept (default 0.95)
            ai_review_threshold       : Score ≥ this → send to AI (default 0.75)
        """
        self.high_confidence_threshold = high_confidence_threshold
        self.ai_review_threshold       = ai_review_threshold
        self._use_rapidfuzz            = self._check_rapidfuzz()

    @staticmethod
    def _check_rapidfuzz() -> bool:
        """Check if rapidfuzz is available."""
        try:
            import rapidfuzz  # noqa: F401
            return True
        except ImportError:
            return False

    def _similarity(self, a: str, b: str) -> float:
        """
        Compute normalized similarity score between two strings.
        Uses rapidfuzz.fuzz.token_ratio if available, else difflib.SequenceMatcher.
        Score range: 0.0 (completely different) to 1.0 (identical).
        """
        if not a or not b:
            return 0.0

        if self._use_rapidfuzz:
            from rapidfuzz import fuzz
            # token_ratio handles reordered words; normalized to 0.0–1.0
            return fuzz.token_ratio(a, b) / 100.0

        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio()

    def score_pair(
        self,
        source_col: ColumnMetadata,
        target_col: ColumnMetadata,
    ) -> FuzzyCandidate:
        """
        Score a specific source-target column pair.

        Args:
            source_col : Source column
            target_col : Target column

        Returns:
            FuzzyCandidate with the computed score and threshold flags.
        """
        src_norm = normalize_column_name(source_col.column_name)
        tgt_norm = normalize_column_name(target_col.column_name)
        score    = self._similarity(src_norm, tgt_norm)

        return FuzzyCandidate(
            source_col=source_col,
            target_col=target_col,
            fuzzy_score=score,
            source_norm=src_norm,
            target_norm=tgt_norm,
            above_high=score >= self.high_confidence_threshold,
            needs_ai_review=(
                self.ai_review_threshold <= score < self.high_confidence_threshold
            ),
            below_threshold=score < self.ai_review_threshold,
        )

    def match_unmatched(
        self,
        unmatched_sources: List[ColumnMetadata],
        unmatched_targets: List[ColumnMetadata],
        top_n: int = 5,
    ) -> List[FuzzyMatchGroup]:
        """
        For every unmatched source column, rank all unmatched target columns
        by fuzzy similarity and return the top N candidates.

        Args:
            unmatched_sources : Source columns that exact matching failed on
            unmatched_targets : Target columns still available for matching
            top_n             : Maximum candidates to return per source column

        Returns:
            List of FuzzyMatchGroup, one per source column (preserving order).
            Groups with zero candidates have an empty candidates list.
        """
        groups: List[FuzzyMatchGroup] = []

        for src in unmatched_sources:
            candidates: List[FuzzyCandidate] = []

            for tgt in unmatched_targets:
                candidate = self.score_pair(src, tgt)
                candidates.append(candidate)

            # Sort by score descending, keep top N
            candidates.sort(key=lambda c: c.fuzzy_score, reverse=True)
            candidates = candidates[:top_n]

            groups.append(FuzzyMatchGroup(source_col=src, candidates=candidates))

        return groups
