"""
Learned Rule Retrieval
=======================
Retrieves relevant learned examples from rule_book_learned.json.

The retriever is used at two points in the pipeline:
  1. CandidateMatcher — to give learned examples a confidence boost
     (has_learned_example=True in ConfidenceScorer)
  2. PromptBuilder — to inject relevant past corrections into AI prompts
     (so AI benefits from previous human corrections without re-doing them)

Retrieval criteria (any match is considered relevant):
  - Exact source column name match (case-insensitive)
  - Source type match
  - Target type match
  - Similar normalized name (edit distance ≤ 2 from source or target name)

The retriever reads from rule_book_learned.json directly and does NOT load
the full RuleBook singleton (to avoid startup cost when called from tests).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from matching.normalizer import normalize_column_name


_LEARNED_PATH = Path(__file__).parent.parent / "rule_book_learned.json"


@dataclass
class LearnedExample:
    """
    A single learned correction record from rule_book_learned.json.

    Attributes:
        source_column  : PG column name at time of correction
        target_column  : SF column name chosen by the human
        source_type    : PG data type
        target_type    : SF data type
        correct_rule   : Rule ID the human confirmed
        reason         : Human-provided reason (free text)
        table_name     : Table this correction came from (optional)
        corrected_at   : ISO timestamp
    """
    source_column: str
    target_column: str
    source_type:   str
    target_type:   str
    correct_rule:  str
    reason:        str
    table_name:    str = ""
    corrected_at:  str = ""

    @property
    def source_normalized(self) -> str:
        return normalize_column_name(self.source_column)

    @property
    def target_normalized(self) -> str:
        return normalize_column_name(self.target_column)

    def to_dict(self) -> dict:
        return {
            "source_column": self.source_column,
            "target_column": self.target_column,
            "source_type":   self.source_type,
            "target_type":   self.target_type,
            "correct_rule":  self.correct_rule,
            "reason":        self.reason,
            "table_name":    self.table_name,
            "corrected_at":  self.corrected_at,
        }


class LearnedRuleRetriever:
    """
    Loads and searches learned corrections from rule_book_learned.json.

    Usage
    -----
        retriever = LearnedRuleRetriever()
        examples  = retriever.find_relevant(
            source_name="created_at",
            source_type="timestamp without time zone",
            target_types=["TIMESTAMP_NTZ", "VARCHAR"],
        )
        # Pass examples to PromptBuilder or use for confidence boost
    """

    def __init__(self, learned_path: Optional[Path] = None):
        self._path     = learned_path or _LEARNED_PATH
        self._examples: List[LearnedExample] = []
        self._loaded   = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)

            for item in data.get("learned_corrections", data.get("learned_rules", [])):
                self._examples.append(LearnedExample(
                    source_column=item.get("source_column", ""),
                    target_column=item.get("target_column", ""),
                    source_type=  item.get("source_type", ""),
                    target_type=  item.get("target_type", ""),
                    correct_rule= item.get("correct_rule", item.get("rule", "")),
                    reason=       item.get("reason", ""),
                    table_name=   item.get("table_name", ""),
                    corrected_at= item.get("corrected_at", item.get("learned_at", "")),
                ))
        except Exception as exc:
            print(f"  [LearnedRuleRetriever] Could not load {self._path}: {exc}")

    def all_examples(self) -> List[LearnedExample]:
        """Return all learned examples."""
        self._ensure_loaded()
        return list(self._examples)

    def find_relevant(
        self,
        source_name:  str,
        source_type:  str,
        target_names: Optional[List[str]] = None,
        target_types: Optional[List[str]] = None,
        max_results:  int = 5,
    ) -> List[LearnedExample]:
        """
        Return learned examples relevant to this source column.

        Relevance (any match):
          - Exact source column name (case-insensitive)
          - Normalized source name matches (within edit distance ≤ 1)
          - Source type matches
          - Target type matches any of the provided types

        Results are sorted: exact name match first, then type match, then others.

        Args:
            source_name  : PG column name to search for
            source_type  : PG data type
            target_names : Optional list of SF column names to match against
            target_types : Optional list of SF data types to match against
            max_results  : Maximum results to return

        Returns:
            List of LearnedExample sorted by relevance score (best first).
        """
        self._ensure_loaded()
        if not self._examples:
            return []

        src_upper      = source_name.upper()
        src_norm       = normalize_column_name(source_name)
        src_type_upper = source_type.upper()
        tgt_names_upper = {n.upper() for n in (target_names or [])}
        tgt_types_upper = {t.upper() for t in (target_types or [])}

        scored: List[tuple] = []
        for ex in self._examples:
            score = 0

            # Exact name match → highest priority
            if ex.source_column.upper() == src_upper:
                score += 10

            # Normalized name match
            elif ex.source_normalized == src_norm:
                score += 6

            # Near-normalized match (edit distance 1)
            elif _edit_distance_le1(ex.source_normalized, src_norm):
                score += 3

            # Source type match
            if ex.source_type.upper() == src_type_upper:
                score += 2

            # Target name/type match
            if tgt_names_upper and ex.target_column.upper() in tgt_names_upper:
                score += 4
            if tgt_types_upper and ex.target_type.upper() in tgt_types_upper:
                score += 2

            if score > 0:
                scored.append((score, ex))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:max_results]]

    def has_correction_for(self, source_name: str, target_name: str) -> bool:
        """
        Return True if there is a learned correction for this specific
        source → target column pair (case-insensitive).
        """
        self._ensure_loaded()
        src_upper = source_name.upper()
        tgt_upper = target_name.upper()
        return any(
            ex.source_column.upper() == src_upper
            and ex.target_column.upper() == tgt_upper
            for ex in self._examples
        )

    def as_prompt_dicts(
        self,
        source_name:  str,
        source_type:  str,
        target_types: Optional[List[str]] = None,
        max_results:  int = 3,
    ) -> List[dict]:
        """
        Return relevant examples as plain dicts for injection into AI prompts.

        This is the format PromptBuilder's _filter_learned() expects.
        """
        examples = self.find_relevant(
            source_name=source_name,
            source_type=source_type,
            target_types=target_types,
            max_results=max_results,
        )
        return [ex.to_dict() for ex in examples]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edit_distance_le1(a: str, b: str) -> bool:
    """Return True if Levenshtein distance between a and b is ≤ 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False

    # Check substitution (same length)
    if la == lb:
        diffs = sum(1 for x, y in zip(a, b) if x != y)
        return diffs <= 1

    # Check insertion/deletion (length differs by 1)
    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = diffs = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] != longer[j]:
            diffs += 1
            j += 1
        else:
            i += 1
            j += 1
    return diffs <= 1
