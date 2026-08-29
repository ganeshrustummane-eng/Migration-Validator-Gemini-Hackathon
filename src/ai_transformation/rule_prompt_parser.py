"""
Rule Prompt Parser
===================
Turns free-text or a pasted type-mapping table (e.g. "MSSQL bit -> Snowflake
BOOLEAN") into structured, closed-set rule proposals for the Rule Book.

Anti-hallucination contract
---------------------------
The model is NEVER allowed to invent SQL. Every proposal must set
`reuses_rule` to one of the known base rule ids passed in (boolean, numeric,
text, ...), reusing that rule's already-tested SQL. If the model cannot
confidently match an existing rule, it must leave `reuses_rule` null and set
`needs_review=True` instead of guessing — the UI then blocks that row from
being saved until a human picks a rule manually.

This module only produces proposals. Nothing is persisted here — the caller
(webapp) shows them for review and calls rule_book.save_learned_rule().
"""

from __future__ import annotations

import json
from typing import List, Optional

from ai_transformation.ai_rule_mapper import AIRuleMapper, AIRuleMappingError, _BACKEND_CLAUDE


class RuleParseError(RuntimeError):
    """Raised when the AI response could not be parsed into rule proposals."""


class RuleProposal:
    """One parsed source-type -> target-type rule proposal."""

    def __init__(
        self,
        source_type: str,
        target_type: str,
        dialect: str = "any",
        reuses_rule: Optional[str] = None,
        confidence: float = 0.0,
        note: str = "",
        needs_review: bool = False,
    ):
        self.source_type = source_type
        self.target_type = target_type
        self.dialect = dialect or "any"
        self.reuses_rule = reuses_rule
        self.confidence = confidence
        self.note = note
        self.needs_review = needs_review or not reuses_rule


class RuleTypeParser:
    """AI-assisted parser: pasted text -> List[RuleProposal].

    Reuses the same DIAL/Claude backend detection as AIRuleMapper so no
    second API client/config path needs to exist.
    """

    def __init__(self, model: Optional[str] = None):
        self._mapper = AIRuleMapper(model=model)

    @property
    def is_ai_active(self) -> bool:
        return self._mapper._ai_active

    @property
    def active_model(self) -> str:
        return self._mapper.model

    def parse(self, raw_text: str, known_rule_ids: List[str]) -> List[RuleProposal]:
        """
        Parse pasted text into rule proposals constrained to known_rule_ids.

        Raises:
            AIRuleMappingError: no AI backend configured or the API call failed.
            RuleParseError: the model's response could not be parsed as JSON.
        """
        if not self._mapper._ai_active:
            raise AIRuleMappingError(
                "No AI API key configured — cannot parse rules.\n"
                "  Set DIAL_API_KEY or CLAUDE_API_KEY in .env."
            )
        if not raw_text.strip():
            return []

        system_prompt = _build_system_prompt(known_rule_ids)

        if self._mapper._backend == _BACKEND_CLAUDE:
            raw = self._mapper._call_claude(system_prompt, raw_text, "rule_parse")
        else:
            raw = self._mapper._call_dial(system_prompt, raw_text, "rule_parse")

        return _parse_response(raw, known_rule_ids)


def _build_system_prompt(known_rule_ids: List[str]) -> str:
    rule_list = ", ".join(known_rule_ids)
    return f"""You convert a pasted database type-mapping table or free text into a strict JSON
list of source-type -> target-type validation rule proposals for a PostgreSQL/MSSQL/Athena
-> Snowflake migration validator.

CLOSED-SET CONSTRAINT (must follow exactly):
  Every proposal's "reuses_rule" field MUST be one of these existing rule ids, or null:
  {rule_list}
  You are NEVER allowed to invent a new rule id, invent SQL, or invent a transformation.
  Reusing an existing rule means: this type pair should be treated exactly like that
  rule's already-implemented behavior (e.g. "text" = TRIM, "boolean" = TRUE/FALSE -> '1'/'0').
  If you cannot confidently match an existing rule's behavior to a type pair, set
  "reuses_rule" to null and "needs_review" to true with a short "note" explaining why
  (e.g. "MSSQL timestamp/rowversion is a binary row-version column, not a datetime").

Respond with JSON only, in this exact shape:
{{
  "rules": [
    {{
      "source_type": "nvarchar",
      "target_type": "TEXT",
      "dialect": "mssql",
      "reuses_rule": "text",
      "confidence": 0.9,
      "needs_review": false,
      "note": "short reason"
    }}
  ]
}}

"dialect" is one of: mssql, postgres, athena, any (use "any" if the input doesn't specify).
Parse every row/pair mentioned in the input. Do not skip rows. Do not add commentary
outside the JSON object."""


def _parse_response(raw: str, known_rule_ids: List[str]) -> List[RuleProposal]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuleParseError(f"Model response was not valid JSON: {exc}\n---\n{raw[:500]}") from exc

    rows = data.get("rules", []) if isinstance(data, dict) else []
    known_upper = {r.upper() for r in known_rule_ids}

    proposals: List[RuleProposal] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reuses = row.get("reuses_rule")
        # Anti-hallucination guard enforced in code too, not just by prompt:
        # a rule id outside the known closed set is treated as no match.
        if reuses and reuses.upper() not in known_upper:
            reuses = None
        proposals.append(RuleProposal(
            source_type=str(row.get("source_type", "")).strip(),
            target_type=str(row.get("target_type", "")).strip(),
            dialect=str(row.get("dialect", "any")).strip().lower() or "any",
            reuses_rule=reuses,
            confidence=float(row.get("confidence", 0.0) or 0.0),
            note=str(row.get("note", "")),
            needs_review=bool(row.get("needs_review", False)),
        ))
    return proposals
