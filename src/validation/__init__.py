"""
Validation package — plan-level validation before SQL/YAML generation.

PlanValidator checks a CanonicalValidationPlan for structural integrity
before allowing SQL and YAML generators to consume it.  This is the
"plan validator" step in the pipeline:

  CanonicalValidationPlan
      → PlanValidator.validate()       ← this package
          OK → SQLQueryGenerator.generate_from_plan()
          INVALID → raise PlanValidationError (never generate SQL)
"""

from .plan_validator import PlanValidator, PlanValidationError

__all__ = ["PlanValidator", "PlanValidationError"]
