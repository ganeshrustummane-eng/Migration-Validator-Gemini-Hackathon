"""
AI package — focused, token-efficient AI reasoning for ambiguous cases only.

The AI is called ONLY when deterministic matching cannot resolve a column.
It receives a minimal prompt with:
  - The ambiguous source column
  - Top fuzzy candidate target columns (not the whole schema)
  - Relevant transformation rules (not the whole rule book)
  - Relevant learned examples (not the entire learning file)

The AI returns a structured JSON mapping decision.
Python code validates and applies the decision.
"""

from ai.rule_planner import RulePlanner
from ai.prompt_builder import PromptBuilder
from ai.response_parser import ResponseParser, AIColumnDecision

__all__ = [
    "RulePlanner",
    "PromptBuilder",
    "ResponseParser",
    "AIColumnDecision",
]
