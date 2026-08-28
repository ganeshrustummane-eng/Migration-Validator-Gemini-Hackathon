"""
AI Transformation Package
===========================
Maps source columns to Snowflake columns and assigns the correct validation
rule for each column pair.

AI-only mapping
---------------
  AIRuleMapper — DIAL/GPT-4o (or any configured model). Requires DIAL_API_KEY.
                 The model is user-selectable at runtime.

  The former StaticRuleMapper (deterministic type-pair matching, used as an
  offline fallback) has been REMOVED. Its output was indistinguishable from a
  reviewed AI mapping, so a missing API key silently downgraded correctness
  without downgrading the reported confidence. Mapping now raises
  AIRuleMappingError instead of guessing.

RuleMapperOrchestrator
----------------------
  Single entry point. Supports runtime model switching via
  orchestrator.set_model('gpt-4o-mini').

Available Models (DIAL)
-----------------------
  gpt-4o            ← default, best accuracy
  gpt-4o-mini       ← faster, lower cost
  gpt-4-turbo
  claude-3-5-sonnet
  gemini-pro
  (any model on your DIAL endpoint)

Usage
-----
    from ai_transformation import RuleMapperOrchestrator, AVAILABLE_MODELS

    # List models user can choose from:
    print(AVAILABLE_MODELS)

    # Use specific model:
    mapper = RuleMapperOrchestrator(model="gpt-4o-mini")
    mappings, explanation = mapper.map_columns(
        source_columns=pg_columns,
        target_columns=sf_columns,
        table_name="events",
    )
"""

from ai_transformation.column_mapping import ColumnRuleMapping
from ai_transformation.ai_rule_mapper import (
    AIRuleMapper,
    AIRuleMappingError,
    AVAILABLE_MODELS,
    MODEL_DESCRIPTIONS,
)
from ai_transformation.orchestrator import RuleMapperOrchestrator

__all__ = [
    "ColumnRuleMapping",
    "AIRuleMapper",
    "AIRuleMappingError",
    "RuleMapperOrchestrator",
    "AVAILABLE_MODELS",
    "MODEL_DESCRIPTIONS",
]
