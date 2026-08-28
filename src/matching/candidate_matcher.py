"""
Candidate Matcher
==================
Orchestrates the full deterministic matching pipeline and produces
MatchDecision objects — one per source column — that classify columns
into resolved/ai_needed/unmatched buckets.

Pipeline
--------
  source columns
    ↓
  ExactMatcher (exact + normalized_exact)
    ↓ unmatched columns
  FuzzyMatcher (ranked candidates)
    ↓ groups
  ConfidenceScorer (multi-factor score per candidate)
    ↓
  MatchDecision per source column:
      status = "resolved"   → auto-accepted, no AI needed
      status = "ai_needed"  → ambiguous, send to AI with candidate list
      status = "unmatched"  → nothing found above AI_REVIEW threshold

AI is called ONLY for columns with status = "ai_needed".
This is the core mechanism for token efficiency.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sql_extractor.extractors import ColumnMetadata
from matching.exact_matcher import ExactMatcher, ExactMatchResult
from matching.fuzzy_matcher import FuzzyMatcher, FuzzyCandidate
from matching.confidence import ConfidenceScorer, ConfidenceBreakdown


# ---------------------------------------------------------------------------
# Match Decision — the output of this module
# ---------------------------------------------------------------------------

@dataclass
class MatchDecision:
    """
    Final matching decision for one source column.

    Attributes:
        source_col     : The source column being matched
        target_col     : Best-matched target column (None if unmatched)
        method         : How it was matched:
                         'exact' | 'normalized_exact' | 'configured' |
                         'learned' | 'fuzzy' | 'fuzzy_ai' | None
        confidence     : ConfidenceBreakdown (None for exact matches — confidence = 1.0)
        final_score    : 1.0 for exact; confidence.final_score for fuzzy/AI
        fuzzy_score    : Raw fuzzy name similarity (0.0 for exact matches)
        candidates     : Top fuzzy candidates (for AI_NEEDED decisions)
        status         : 'resolved' | 'ai_needed' | 'unmatched'
        skip_validation: True if this column should not be validated
        skip_reason    : Why skipped (if skip_validation=True)
    """
    source_col:      ColumnMetadata
    target_col:      Optional[ColumnMetadata]
    method:          Optional[str]
    confidence:      Optional[ConfidenceBreakdown]
    final_score:     float
    fuzzy_score:     float = 0.0
    candidates:      List[FuzzyCandidate] = field(default_factory=list)
    status:          str = "unmatched"  # resolved | ai_needed | unmatched
    skip_validation: bool = False
    skip_reason:     str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def needs_ai(self) -> bool:
        return self.status == "ai_needed"

    @property
    def is_unmatched(self) -> bool:
        return self.status == "unmatched"

    def format_summary(self) -> str:
        """One-line summary for CLI display."""
        src = self.source_col.column_name
        if self.target_col:
            tgt = self.target_col.column_name
            return (
                f"{src} → {tgt}  "
                f"[{self.method}]  "
                f"score={self.final_score:.3f}  "
                f"status={self.status}"
            )
        return f"{src} → (unmatched)  status={self.status}"


# ---------------------------------------------------------------------------
# Candidate Matcher
# ---------------------------------------------------------------------------

class CandidateMatcher:
    """
    Full deterministic matching pipeline.

    Usage
    -----
        matcher = CandidateMatcher()
        decisions = matcher.match(source_columns, target_columns)

        resolved  = [d for d in decisions if d.is_resolved]
        ai_needed = [d for d in decisions if d.needs_ai]
        unmatched = [d for d in decisions if d.is_unmatched]

    The pipeline produces exactly one MatchDecision per source column.
    Fivetran metadata columns (_FIVETRAN_*) are automatically skipped.
    """

    _FIVETRAN_PREFIX = "_FIVETRAN_"

    def __init__(
        self,
        high_confidence_threshold: float = 0.95,
        ai_review_threshold:       float = 0.75,
    ):
        self._exact_matcher  = ExactMatcher()
        self._fuzzy_matcher  = FuzzyMatcher(
            high_confidence_threshold=high_confidence_threshold,
            ai_review_threshold=ai_review_threshold,
        )
        self._scorer         = ConfidenceScorer()
        self.high_threshold  = high_confidence_threshold
        self.ai_threshold    = ai_review_threshold

    def match(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        explicit_mappings: Optional[Dict[str, str]] = None,
        learned_examples: Optional[List[dict]] = None,
    ) -> List[MatchDecision]:
        """
        Run the full deterministic matching pipeline.

        Args:
            source_columns  : PostgreSQL columns
            target_columns  : Snowflake columns (Fivetran cols are matched separately)
            explicit_mappings: Optional user-configured {src_name: tgt_name} overrides
            learned_examples: Optional list of learned dicts from rule_book_learned.json

        Returns:
            List[MatchDecision] — one per source column, preserving ordinal order.
            Fivetran metadata columns get skip_validation=True.
        """
        # Separate Fivetran columns from regular columns
        fivetran_src = [c for c in source_columns if self._is_fivetran(c.column_name)]
        regular_src  = [c for c in source_columns if not self._is_fivetran(c.column_name)]

        decisions: List[MatchDecision] = []

        # Skip Fivetran source columns
        for col in fivetran_src:
            decisions.append(MatchDecision(
                source_col=col,
                target_col=None,
                method=None,
                confidence=None,
                final_score=0.0,
                status="resolved",
                skip_validation=True,
                skip_reason="Fivetran metadata column — excluded from validation",
            ))

        # ── Step 1: Exact matching ────────────────────────────────────────────
        exact_results, unmatched_tgt = self._exact_matcher.match_all(
            regular_src, target_columns, explicit_mappings
        )

        # Separate exact matches from remaining unmatched
        exact_matched:   List[MatchDecision] = []
        needs_fuzzy_src: List[ColumnMetadata] = []
        exact_by_name:   Dict[str, ExactMatchResult] = {}

        for result in exact_results:
            exact_by_name[result.source_col.column_name] = result
            if result.matched:
                exact_matched.append(MatchDecision(
                    source_col=result.source_col,
                    target_col=result.target_col,
                    method=result.method,
                    confidence=None,
                    final_score=1.0,
                    fuzzy_score=0.0,
                    status="resolved",
                    skip_validation=False,
                ))
            else:
                needs_fuzzy_src.append(result.source_col)

        # ── Step 1.5: Learned-correction short-circuit ───────────────────────
        # A previously confirmed human correction (rule_book_learned.json →
        # learned_corrections) must win outright, not just nudge a fuzzy score.
        # Without this, a learned pair whose names are too dissimilar to be the
        # fuzzy matcher's #1 guess (e.g. employee → USER) was silently ignored,
        # since `_has_learned_match` below only ever checks the single top
        # fuzzy candidate — it can't rescue a pair fuzzy search didn't surface.
        learned_set = _build_learned_lookup(learned_examples or [])
        unmatched_tgt_by_name: Dict[str, ColumnMetadata] = {
            c.column_name.upper(): c for c in unmatched_tgt
        }
        learned_matched:  List[MatchDecision]  = []
        still_needs_fuzzy: List[ColumnMetadata] = []
        for col in needs_fuzzy_src:
            src_upper = col.column_name.upper()
            tgt_name = next(
                (tgt for (src, tgt) in learned_set
                 if src == src_upper and tgt in unmatched_tgt_by_name),
                None,
            )
            if tgt_name is not None:
                tgt_col = unmatched_tgt_by_name.pop(tgt_name)
                learned_matched.append(MatchDecision(
                    source_col=col,
                    target_col=tgt_col,
                    method="learned",
                    confidence=None,
                    final_score=1.0,
                    fuzzy_score=0.0,
                    status="resolved",
                    skip_validation=False,
                ))
            else:
                still_needs_fuzzy.append(col)
        needs_fuzzy_src = still_needs_fuzzy
        unmatched_tgt = list(unmatched_tgt_by_name.values())

        # ── Step 2: Fuzzy matching for unmatched ─────────────────────────────
        fuzzy_groups = self._fuzzy_matcher.match_unmatched(
            needs_fuzzy_src, unmatched_tgt, top_n=5
        )

        total_cols = max(len(source_columns), len(target_columns), 1)

        fuzzy_decisions: List[MatchDecision] = []
        for group in fuzzy_groups:
            src = group.source_col

            if not group.candidates:
                fuzzy_decisions.append(MatchDecision(
                    source_col=src,
                    target_col=None,
                    method=None,
                    confidence=None,
                    final_score=0.0,
                    status="unmatched",
                    skip_validation=True,
                    skip_reason="No fuzzy candidates found — column may not exist in target",
                ))
                continue

            top = group.top
            has_learned = _has_learned_match(src.column_name, top.target_col.column_name, learned_set)

            breakdown = self._scorer.score(
                source_col=src,
                target_col=top.target_col,
                fuzzy_score=top.fuzzy_score,
                total_columns=total_cols,
                has_learned_example=has_learned,
            )

            if breakdown.final_score >= self.high_threshold:
                # High confidence — auto-accept without AI
                fuzzy_decisions.append(MatchDecision(
                    source_col=src,
                    target_col=top.target_col,
                    method="fuzzy",
                    confidence=breakdown,
                    final_score=breakdown.final_score,
                    fuzzy_score=top.fuzzy_score,
                    candidates=group.candidates,
                    status="resolved",
                ))
            elif breakdown.final_score >= self.ai_threshold:
                # Ambiguous — need AI to resolve
                fuzzy_decisions.append(MatchDecision(
                    source_col=src,
                    target_col=top.target_col,
                    method="fuzzy_ai",
                    confidence=breakdown,
                    final_score=breakdown.final_score,
                    fuzzy_score=top.fuzzy_score,
                    candidates=group.candidates,
                    status="ai_needed",
                ))
            else:
                # Below threshold — unmatched
                fuzzy_decisions.append(MatchDecision(
                    source_col=src,
                    target_col=None,
                    method=None,
                    confidence=breakdown,
                    final_score=breakdown.final_score,
                    fuzzy_score=top.fuzzy_score if top else 0.0,
                    candidates=group.candidates,
                    status="unmatched",
                    skip_validation=True,
                    skip_reason=(
                        f"Best fuzzy candidate score {breakdown.final_score:.2f} "
                        f"below threshold {self.ai_threshold:.2f}"
                    ),
                ))

        # ── Assemble in original source order ─────────────────────────────
        # Build lookup maps
        exact_by_src   = {d.source_col.column_name: d for d in exact_matched}
        learned_by_src = {d.source_col.column_name: d for d in learned_matched}
        fuzzy_by_src   = {d.source_col.column_name: d for d in fuzzy_decisions}

        for col in source_columns:
            if self._is_fivetran(col.column_name):
                # Already added above
                continue
            d = (
                exact_by_src.get(col.column_name)
                or learned_by_src.get(col.column_name)
                or fuzzy_by_src.get(col.column_name)
            )
            if d:
                decisions.append(d)

        return decisions

    @staticmethod
    def _is_fivetran(name: str) -> bool:
        return name.upper().startswith("_FIVETRAN_")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_learned_lookup(learned_examples: List[dict]) -> set:
    """Build a set of (src_name_upper, tgt_name_upper) pairs from learned examples."""
    result = set()
    for ex in learned_examples:
        src = ex.get("source_column", "").upper()
        tgt = ex.get("target_column", "").upper()
        if src and tgt:
            result.add((src, tgt))
    return result


def _has_learned_match(src_name: str, tgt_name: str, learned_set: set) -> bool:
    """Return True if (src_name, tgt_name) appears in the learned examples set."""
    return (src_name.upper(), tgt_name.upper()) in learned_set
