"""
AI Rule Planner
================
Sends ONLY ambiguous columns (with their top fuzzy candidates) to the AI.
Returns resolved MatchDecisions with transformation rule assignments.

Token-efficiency design:
  - Receives ONLY the columns with status="ai_needed" from CandidateMatcher
  - Sends ONE column per AI call (focused prompt, not the full schema)
  - AI receives only top N candidates — never the entire target schema
  - Falls back gracefully to the best fuzzy candidate on any API error

Integration:
  decisions = CandidateMatcher().match(source_cols, target_cols)
  ai_needed = [d for d in decisions if d.needs_ai]

  planner = RulePlanner(api_key=..., model=...)
  result  = planner.resolve(ai_needed, table_name="orders", learned_examples=[...])

  for decision in result.decisions:
      # decision.status is "resolved" or "unmatched" (never "ai_needed")
      # decision.method is "fuzzy_ai" if AI resolved it, "fuzzy" for fallback

Security notes:
  - No credentials are passed to the AI prompt (PromptBuilder enforces this)
  - No SQL is generated here — rule ID only
  - No data is sent to the AI — only column metadata (name, type, position)
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from matching.candidate_matcher import MatchDecision
from matching.fuzzy_matcher import FuzzyCandidate
from sql_extractor.extractors import ColumnMetadata
from ai.prompt_builder import PromptBuilder
from ai.response_parser import ResponseParser, AIColumnDecision

try:
    from token_usage_analysis.token_logger import log_usage, extract_openai_usage
except ImportError:
    def log_usage(*args, **kwargs):  # pragma: no cover - logging is best-effort
        pass

    def extract_openai_usage(response):  # pragma: no cover
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# Constants — reuse the same DIAL defaults as AIRuleMapper
# ---------------------------------------------------------------------------

_DEFAULT_API_BASE    = "https://ai-proxy.lab.epam.com"
_DEFAULT_API_VERSION = "2025-04-01-preview"
_DEFAULT_MODEL       = "gpt-4o"


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class PlannerResult:
    """
    Output of RulePlanner.resolve().

    Attributes:
        decisions      : Updated MatchDecision list (all resolved or unmatched).
                         No decision in this list has status="ai_needed".
        ai_decisions   : Map of source_column_name → AIColumnDecision.
                         Use this when building CanonicalValidationPlan to get
                         the transformation_rule, confidence, and reason from AI.
        ai_calls_made  : Number of successful AI API calls made.
        errors         : List of error strings (one per failed AI call, if any).
    """
    decisions:    List[MatchDecision]
    ai_decisions: Dict[str, AIColumnDecision]
    ai_calls_made: int = 0
    errors:       List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RulePlanner
# ---------------------------------------------------------------------------

class RulePlanner:
    """
    Resolves ambiguous column mappings using the DIAL AI backend.

    Sends ONLY ambiguous columns to the AI — one focused call per column.
    Falls back to the best fuzzy candidate when AI is unavailable or fails.

    Usage
    -----
        planner = RulePlanner()
        result  = planner.resolve(
            ai_needed_decisions=decisions,
            table_name="orders",
            learned_examples=rule_book.learned_examples,
        )
        # result.decisions: all resolved (no more "ai_needed")
        # result.ai_decisions: transformation rules from AI
        # result.ai_calls_made: how many AI calls were made
    """

    def __init__(
        self,
        api_key:     Optional[str] = None,
        api_base:    Optional[str] = None,
        api_version: Optional[str] = None,
        model:       Optional[str] = None,
        top_n:       int = 5,
    ):
        """
        Args:
            api_key     : DIAL API key (default: DIAL_API_KEY env var)
            api_base    : DIAL base URL
            api_version : Azure OpenAI API version
            model       : Model deployment name
            top_n       : Max candidates to include per AI prompt
        """
        self.api_key     = api_key     or os.getenv("DIAL_API_KEY", "")
        self.api_base    = api_base    or os.getenv("DIAL_API_BASE",    _DEFAULT_API_BASE)
        self.api_version = api_version or os.getenv("DIAL_API_VERSION", _DEFAULT_API_VERSION)
        self.model       = model       or os.getenv("DIAL_MODEL",       _DEFAULT_MODEL)
        self.top_n       = top_n
        self._ai_active  = bool(self.api_key)
        self._builder    = PromptBuilder()
        self._parser     = ResponseParser()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def resolve(
        self,
        ai_needed_decisions: List[MatchDecision],
        table_name:          str = "unknown",
        learned_examples:    Optional[List[dict]] = None,
    ) -> PlannerResult:
        """
        Resolve all ai_needed decisions.

        For each decision the planner:
          1. Builds a focused prompt (source col + top N candidates only)
          2. Calls the DIAL API
          3. Validates the response (target must be from the candidate list)
          4. Returns an updated MatchDecision with status="resolved"
             and method="fuzzy_ai"

        If AI is unavailable or the call fails, the best fuzzy candidate is
        accepted automatically with method="fuzzy" (graceful degradation).

        Args:
            ai_needed_decisions: Decisions with status="ai_needed"
            table_name          : Table name for AI context and logging
            learned_examples    : Optional learned correction examples

        Returns:
            PlannerResult with all decisions resolved.
        """
        if not ai_needed_decisions:
            return PlannerResult(decisions=[], ai_decisions={})

        if not self._ai_active:
            print(
                f"  [RulePlanner] No DIAL_API_KEY — falling back to best fuzzy for "
                f"{len(ai_needed_decisions)} ambiguous column(s).",
                file=sys.stderr,
            )
            return self._fallback_all(ai_needed_decisions)

        try:
            from openai import AzureOpenAI  # type: ignore
        except ImportError:
            print(
                "  [RulePlanner] 'openai' not installed — falling back to fuzzy.",
                file=sys.stderr,
            )
            return self._fallback_all(ai_needed_decisions)

        client = AzureOpenAI(
            api_key=self.api_key,
            api_version=self.api_version,
            azure_endpoint=self.api_base,
        )

        system_prompt = self._builder.build_system_prompt()

        resolved_decisions: List[MatchDecision] = []
        ai_decisions:       Dict[str, AIColumnDecision] = {}
        ai_calls_made = 0
        errors:         List[str] = []

        for dec in ai_needed_decisions:
            src_col    = dec.source_col
            candidates = dec.candidates

            valid_target_names = [c.target_col.column_name for c in candidates[: self.top_n]]

            user_prompt = self._builder.build_user_prompt(
                source_col=src_col,
                candidates=candidates,
                learned_examples=learned_examples,
                table_name=table_name,
                top_n=self.top_n,
            )

            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                    extra_headers={"Api-Key": self.api_key},
                )
                raw = response.choices[0].message.content
                ai_calls_made += 1

                usage = extract_openai_usage(response)
                log_usage(
                    backend="dial",
                    model=self.model,
                    call_type="column_mapping",
                    context=f"{table_name}.{src_col.column_name}" if table_name else src_col.column_name,
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    total_tokens=usage["total_tokens"],
                )

                ai_dec = self._parser.parse(
                    raw_json=raw,
                    expected_source=src_col.column_name,
                    valid_target_names=valid_target_names,
                )

                if ai_dec.had_parse_error:
                    # Parse failed — fallback to best fuzzy
                    err_msg = (
                        f"[{src_col.column_name}] AI parse error: {ai_dec.parse_error}"
                    )
                    errors.append(err_msg)
                    print(f"  [RulePlanner] ⚠ {err_msg}", file=sys.stderr)
                    resolved_decisions.append(
                        _accept_best_fuzzy(dec, reason=f"AI parse error — fallback: {ai_dec.parse_error}")
                    )
                else:
                    # Find the target ColumnMetadata object for the chosen column
                    chosen_target = _find_target_col(ai_dec.target_column, candidates)

                    if chosen_target is None:
                        # Safety net — AI chose a column we can't find
                        err_msg = (
                            f"[{src_col.column_name}] AI target '{ai_dec.target_column}' "
                            "not found in candidate metadata — using best fuzzy"
                        )
                        errors.append(err_msg)
                        print(f"  [RulePlanner] ⚠ {err_msg}", file=sys.stderr)
                        resolved_decisions.append(_accept_best_fuzzy(dec, reason=err_msg))
                    else:
                        # AI successfully resolved the column
                        status = "resolved" if ai_dec.is_resolved else "ai_needed"
                        new_dec = MatchDecision(
                            source_col=dec.source_col,
                            target_col=chosen_target,
                            method="fuzzy_ai",
                            confidence=dec.confidence,
                            final_score=ai_dec.confidence,
                            fuzzy_score=dec.fuzzy_score,
                            candidates=dec.candidates,
                            status=status,
                            skip_validation=False,
                        )
                        resolved_decisions.append(new_dec)
                        ai_decisions[src_col.column_name] = ai_dec

                        print(
                            f"  [RulePlanner] ✓ {src_col.column_name} → "
                            f"{ai_dec.target_column}  "
                            f"rule={ai_dec.transformation_rule}  "
                            f"conf={ai_dec.confidence:.2f}  "
                            f"status={ai_dec.status}"
                        )

            except Exception as exc:
                err_msg = f"[{src_col.column_name}] DIAL API error: {exc}"
                errors.append(err_msg)
                print(f"  [RulePlanner] ✗ {err_msg} — using best fuzzy", file=sys.stderr)
                resolved_decisions.append(_accept_best_fuzzy(dec, reason=str(exc)))

        print(
            f"  [RulePlanner] Done: {ai_calls_made} AI call(s) made, "
            f"{len(errors)} error(s)."
        )
        return PlannerResult(
            decisions=resolved_decisions,
            ai_decisions=ai_decisions,
            ai_calls_made=ai_calls_made,
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # Fallback helpers
    # -----------------------------------------------------------------------

    def _fallback_all(self, decisions: List[MatchDecision]) -> PlannerResult:
        """Accept best fuzzy candidate for every decision (no AI calls)."""
        return PlannerResult(
            decisions=[
                _accept_best_fuzzy(d, reason="No DIAL API key — accepted best fuzzy candidate")
                for d in decisions
            ],
            ai_decisions={},
            ai_calls_made=0,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _accept_best_fuzzy(dec: MatchDecision, reason: str = "") -> MatchDecision:
    """
    Return a new MatchDecision resolved to the top fuzzy candidate.
    Used when AI is unavailable or fails.
    """
    if dec.candidates:
        best_target = dec.candidates[0].target_col
        return MatchDecision(
            source_col=dec.source_col,
            target_col=best_target,
            method="fuzzy",
            confidence=dec.confidence,
            final_score=dec.final_score,
            fuzzy_score=dec.fuzzy_score,
            candidates=dec.candidates,
            status="resolved",
            skip_validation=False,
        )
    # No candidates at all — mark unmatched
    return MatchDecision(
        source_col=dec.source_col,
        target_col=None,
        method=None,
        confidence=dec.confidence,
        final_score=0.0,
        fuzzy_score=0.0,
        candidates=[],
        status="unmatched",
        skip_validation=True,
        skip_reason=f"No candidates and AI unavailable. {reason}".strip(),
    )


def _find_target_col(
    target_name: str,
    candidates: List[FuzzyCandidate],
) -> Optional[ColumnMetadata]:
    """Find the ColumnMetadata for target_name in the candidates list."""
    target_upper = target_name.upper()
    for cand in candidates:
        if cand.target_col.column_name.upper() == target_upper:
            return cand.target_col
    return None
