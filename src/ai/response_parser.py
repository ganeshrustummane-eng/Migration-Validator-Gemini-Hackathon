"""
AI Response Parser
==================
Parses and validates the structured JSON response from the AI.

The AI must return this exact JSON structure (no markdown):
{
  "status": "resolved" | "ambiguous",
  "source_column": "<source_name>",
  "target_column": "<chosen_target_name>",
  "source_type": "<pg_type>",
  "target_type": "<sf_type>",
  "transformation_rule": "<rule_id>",
  "confidence": 0.0,
  "reason": "<one sentence>"
}

Validation rules:
  - status must be "resolved" or "ambiguous"
  - source_column must match the requested source column
  - target_column must be one of the provided candidates (not invented)
  - transformation_rule must be a known rule ID
  - confidence must be in [0.0, 1.0]

On parse failure: falls back to the best fuzzy candidate with a warning.
"""

import json
from dataclasses import dataclass
from typing import List, Optional, Set


# ---------------------------------------------------------------------------
# Known rule IDs — parser validates against this set
# ---------------------------------------------------------------------------

KNOWN_RULE_IDS: Set[str] = {
    "boolean", "numeric", "timestamp_ntz", "timestamp_tz",
    "date", "text", "uuid", "integer", "json", "bytea", "hstore",
    "null_standardization", "null",
}


@dataclass
class AIColumnDecision:
    """
    Validated AI response for one column mapping decision.

    Attributes:
        source_column       : Source column name
        target_column       : Chosen target column name
        source_type         : PG type
        target_type         : SF type
        transformation_rule : Rule ID to apply
        confidence          : AI-reported confidence [0.0, 1.0]
        reason              : AI reasoning text
        status              : 'resolved' | 'ambiguous' | 'parse_error'
        parse_error         : Description of any parse failure (if applicable)
    """
    source_column:       str
    target_column:       str
    source_type:         str
    target_type:         str
    transformation_rule: str
    confidence:          float
    reason:              str
    status:              str
    parse_error:         Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def is_ambiguous(self) -> bool:
        return self.status == "ambiguous"

    @property
    def had_parse_error(self) -> bool:
        return self.status == "parse_error"


class ResponseParser:
    """
    Parses and validates AI JSON responses.

    Usage
    -----
        parser = ResponseParser()
        decision = parser.parse(
            raw_json=response_text,
            expected_source=source_col.column_name,
            valid_target_names=["CREATEDAT", "TRANSACTION_DT"],
        )
        if decision.is_resolved:
            use(decision.target_column, decision.transformation_rule)
        else:
            fallback_to_best_fuzzy_candidate()
    """

    def parse(
        self,
        raw_json: str,
        expected_source: str,
        valid_target_names: List[str],
    ) -> AIColumnDecision:
        """
        Parse and validate an AI response.

        Args:
            raw_json           : Raw string from the AI
            expected_source    : Source column name we asked about
            valid_target_names : Column names from the provided candidates

        Returns:
            AIColumnDecision — always returns an object, never raises.
            On failure, decision.status = "parse_error".
        """
        # Strip markdown code fences if AI included them
        cleaned = _strip_markdown(raw_json)

        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as exc:
            return AIColumnDecision(
                source_column=expected_source,
                target_column="",
                source_type="",
                target_type="",
                transformation_rule="text",
                confidence=0.0,
                reason="",
                status="parse_error",
                parse_error=f"JSON parse failed: {exc}",
            )

        # Extract fields with safe defaults
        status           = str(data.get("status", "ambiguous")).lower()
        source_column    = str(data.get("source_column", expected_source))
        target_column    = str(data.get("target_column", ""))
        source_type      = str(data.get("source_type", ""))
        target_type      = str(data.get("target_type", ""))
        rule             = str(data.get("transformation_rule", "text")).lower()
        confidence_raw   = data.get("confidence", 0.5)
        reason           = str(data.get("reason", ""))

        # Normalize and validate status
        if status not in ("resolved", "ambiguous"):
            status = "ambiguous"

        # Validate: target must be from the provided candidates
        valid_upper = {n.upper() for n in valid_target_names}
        if target_column.upper() not in valid_upper:
            return AIColumnDecision(
                source_column=expected_source,
                target_column=target_column,
                source_type=source_type,
                target_type=target_type,
                transformation_rule="text",
                confidence=0.0,
                reason=reason,
                status="parse_error",
                parse_error=(
                    f"AI returned target '{target_column}' which is not in "
                    f"the provided candidates {list(valid_target_names)}"
                ),
            )

        # Validate: rule must be known
        if rule not in KNOWN_RULE_IDS:
            # Don't reject — just warn and default to text
            rule = "text"
            reason = f"{reason} [rule defaulted to 'text' — unknown rule ID returned]"

        # Validate: confidence in range
        try:
            confidence = float(confidence_raw)
            confidence = min(1.0, max(0.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        # Find the original-case target column name from valid_target_names
        orig_tgt = _find_original_case(target_column, valid_target_names)

        return AIColumnDecision(
            source_column=expected_source,
            target_column=orig_tgt,
            source_type=source_type,
            target_type=target_type,
            transformation_rule=rule,
            confidence=confidence,
            reason=reason,
            status=status,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_markdown(text: str) -> str:
    """Remove markdown code fences if the AI wrapped its response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first line (``` or ```json) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner).strip()
    return stripped


def _find_original_case(name: str, candidates: List[str]) -> str:
    """Return the original-case version of name from candidates (case-insensitive)."""
    name_upper = name.upper()
    for c in candidates:
        if c.upper() == name_upper:
            return c
    return name
