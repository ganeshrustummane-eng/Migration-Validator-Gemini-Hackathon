"""
Matching package — deterministic column matching pipeline.

Priority order:
  1. exact_matcher    — case-insensitive and normalized-exact matching
  2. fuzzy_matcher    — RapidFuzz similarity ranking
  3. candidate_matcher — combines both into a scored candidate list
  4. confidence        — multi-factor explainable confidence scoring

AI is called ONLY for columns that cannot be resolved deterministically.
"""

from matching.normalizer import normalize_column_name
from matching.exact_matcher import ExactMatcher, ExactMatchResult
from matching.fuzzy_matcher import FuzzyMatcher, FuzzyCandidate
from matching.candidate_matcher import CandidateMatcher, MatchDecision
from matching.confidence import ConfidenceScorer, ConfidenceBreakdown

__all__ = [
    "normalize_column_name",
    "ExactMatcher",
    "ExactMatchResult",
    "FuzzyMatcher",
    "FuzzyCandidate",
    "CandidateMatcher",
    "MatchDecision",
    "ConfidenceScorer",
    "ConfidenceBreakdown",
]
