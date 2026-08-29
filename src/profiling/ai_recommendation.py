"""
AI Recommendation Engine
=========================
Asks the DIAL API to suggest additional business-rule validations that
the deterministic rule engine cannot derive from schema metadata alone.

What AI adds
------------
  1. Business-rule validation hints (e.g. "amount >= 0", "end_date >= start_date")
  2. Semantic cross-column checks (foreign key consistency, date range ordering)
  3. Domain-specific anomaly suggestions (negative balances, implausible ages)

What AI does NOT do here
------------------------
  - It does NOT change which baseline validations are run (those are deterministic)
  - It does NOT generate SQL (the QueryOptimizer does that from typed requirements)
  - It does NOT receive any row-level data — only column names and types

Fallback
--------
  When DIAL_API_KEY is absent, the engine returns an empty list of
  AIRecommendation objects and the pipeline continues with only the
  rule-engine requirements.  This makes AI strictly additive.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from profiling.schema_profiler import ColumnGroup, TableProfile
from profiling.validation_rule_engine import ValidationType


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class AIRecommendation:
    """
    A single AI-suggested additional validation.

    Attributes
    ----------
    check_name    : Short identifier (snake_case)
    description   : Human-readable description of what is checked
    columns       : Column names this check applies to
    pg_expr       : PostgreSQL WHERE / HAVING condition fragment (no SELECT)
    sf_expr       : Snowflake equivalent (usually identical to pg_expr)
    severity      : "error" | "warning" | "info"
    rationale     : Why AI recommended this check
    """
    check_name:  str
    description: str
    columns:     List[str]
    pg_expr:     str
    sf_expr:     str
    severity:    str = "warning"
    rationale:   str = ""

    def as_sql_comment(self) -> str:
        return (
            f"-- AI Recommendation: {self.description}\n"
            f"-- Severity : {self.severity}\n"
            f"-- Columns  : {', '.join(self.columns)}\n"
            f"-- Condition: {self.pg_expr}"
        )


# ---------------------------------------------------------------------------
# AIRecommendationEngine
# ---------------------------------------------------------------------------

_DEFAULT_API_BASE    = "https://ai-proxy.lab.epam.com"
_DEFAULT_API_VERSION = "2025-04-01-preview"
_DEFAULT_MODEL       = "gpt-4o-mini"


class AIRecommendationEngine:
    """
    Optionally calls DIAL to get AI-suggested business-rule checks.

    The engine sends only column names and data types to the AI — never
    any row-level data or credentials.

    Usage
    -----
        engine = AIRecommendationEngine()
        recs   = engine.recommend(table_profile)
        for r in recs:
            print(r.description, r.pg_expr)
    """

    def __init__(
        self,
        api_key:     Optional[str] = None,
        api_base:    Optional[str] = None,
        api_version: Optional[str] = None,
        model:       Optional[str] = None,
    ):
        self.api_key     = api_key     or os.getenv("DIAL_API_KEY", "")
        self.api_base    = api_base    or os.getenv("DIAL_API_BASE",    _DEFAULT_API_BASE)
        self.api_version = api_version or os.getenv("DIAL_API_VERSION", _DEFAULT_API_VERSION)
        self.model       = model       or os.getenv("DIAL_MODEL",       _DEFAULT_MODEL)
        self._ai_active  = bool(self.api_key)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def recommend(
        self,
        profile: TableProfile,
        max_suggestions: int = 5,
    ) -> List[AIRecommendation]:
        """
        Return AI-suggested business-rule checks for the given table profile.

        Returns an empty list if AI is unavailable (graceful degradation).

        Args:
            profile         : TableProfile from SchemaProfiler
            max_suggestions : Cap on the number of AI suggestions (cost guard)

        Returns:
            List of AIRecommendation (may be empty)
        """
        if not self._ai_active:
            print(
                "  [AIRecommendation] No DIAL_API_KEY — skipping AI suggestions.",
                file=sys.stderr,
            )
            return []

        try:
            from openai import AzureOpenAI  # type: ignore
        except ImportError:
            print(
                "  [AIRecommendation] 'openai' not installed — skipping AI suggestions.",
                file=sys.stderr,
            )
            return []

        prompt = self._build_prompt(profile, max_suggestions)

        try:
            client = AzureOpenAI(
                api_key=self.api_key,
                api_version=self.api_version,
                azure_endpoint=self.api_base,
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                extra_headers={"Api-Key": self.api_key},
            )
            raw = response.choices[0].message.content
            return self._parse_response(raw, profile)

        except Exception as exc:
            print(
                f"  [AIRecommendation] DIAL API error: {exc} — skipping AI suggestions.",
                file=sys.stderr,
            )
            return []

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _build_prompt(self, profile: TableProfile, max_suggestions: int) -> str:
        """Build the user prompt with column metadata (no row data)."""
        col_lines = []
        for cp in profile.column_profiles:
            grp = cp.group.value
            nullable = "NULLABLE" if cp.is_nullable else "NOT NULL"
            col_lines.append(
                f"  - {cp.column_name} ({cp.source_type}, {nullable}, group={grp})"
            )

        return (
            f"Table: {profile.source_schema}.{profile.source_table}\n\n"
            f"Columns:\n" + "\n".join(col_lines) + "\n\n"
            f"Task:\n"
            f"Suggest up to {max_suggestions} additional business-rule validation checks "
            f"for this PostgreSQL→Snowflake migration. Each check should be a SQL condition "
            f"fragment (WHERE or HAVING clause expression, not a full query).\n\n"
            f"Focus on:\n"
            f"  - Numeric constraints (amount >= 0, quantity > 0)\n"
            f"  - Date ordering (end_date >= start_date)\n"
            f"  - Domain-specific sanity (email format, phone length)\n"
            f"  - Referential plausibility (foreign key columns should be NOT NULL)\n\n"
            f"Return JSON with this exact schema:\n"
            f'{{"recommendations": ['
            f'{{"check_name": "...", "description": "...", "columns": [...], '
            f'"pg_expr": "...", "sf_expr": "...", "severity": "warning|error|info", '
            f'"rationale": "..."}}]}}'
        )

    def _parse_response(
        self,
        raw_json: str,
        profile: TableProfile,
    ) -> List[AIRecommendation]:
        """Parse AI JSON response into AIRecommendation objects."""
        known_columns = {cp.column_name.lower() for cp in profile.column_profiles}
        try:
            data  = json.loads(raw_json)
            items = data.get("recommendations", [])
            recs: List[AIRecommendation] = []

            for item in items:
                cols = item.get("columns", [])
                # Validate that AI only references real columns
                valid_cols = [
                    c for c in cols
                    if c.lower() in known_columns
                ]
                if not valid_cols:
                    continue  # skip if AI hallucinated columns

                recs.append(AIRecommendation(
                    check_name=item.get("check_name", "unknown_check"),
                    description=item.get("description", ""),
                    columns=valid_cols,
                    pg_expr=item.get("pg_expr", ""),
                    sf_expr=item.get("sf_expr", item.get("pg_expr", "")),
                    severity=item.get("severity", "warning"),
                    rationale=item.get("rationale", ""),
                ))

            print(
                f"  [AIRecommendation] ✓ {len(recs)} recommendation(s) received."
            )
            return recs

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            print(
                f"  [AIRecommendation] Parse error: {exc} — returning empty.",
                file=sys.stderr,
            )
            return []


# ---------------------------------------------------------------------------
# System prompt (module constant)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are a data quality expert for PostgreSQL → Snowflake migration validation.

Your task is to suggest additional SQL business-rule checks based only on column
metadata (names and types). You do NOT have access to actual row data.

Rules:
1. Only reference column names that appear in the provided column list.
2. Return conditions as SQL expression fragments — not full SELECT statements.
3. Be conservative — only suggest checks that are very likely to apply.
4. Prefer checks that catch issues invisible to row-count or distinct-count comparisons.
5. Return valid JSON matching the required schema exactly.
6. If you cannot suggest any meaningful check, return {"recommendations": []}.
""".strip()
