"""
Profiling Package
==================
Analyses a table's column metadata to determine which validation checks
are relevant.  No database query is executed here — this is pure schema
analysis from the ColumnMetadata objects already extracted.

Public API
----------
  SchemaProfiler          — classifies columns into semantic groups
  ValidationRuleEngine    — maps column groups to validation requirements
  AIRecommendationEngine  — asks DIAL to suggest additional checks
"""

from profiling.schema_profiler import SchemaProfiler, TableProfile, ColumnProfile
from profiling.validation_rule_engine import ValidationRuleEngine, ValidationRequirement
from profiling.ai_recommendation import AIRecommendationEngine

__all__ = [
    "SchemaProfiler",
    "TableProfile",
    "ColumnProfile",
    "ValidationRuleEngine",
    "ValidationRequirement",
    "AIRecommendationEngine",
]
