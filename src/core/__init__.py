"""
Core package — the CanonicalValidationPlan contract.

The plan is the single source of truth. It is persisted as JSON by PlanStore;
SQL and YAML are render targets generated from it and are never read back to
reconstruct intent.
"""

from .validation_plan import (
    PLAN_SCHEMA_VERSION,
    CanonicalValidationPlan,
    ColumnMappingEntry,
    MatchMethod,
    PlanStatus,
)
from .plan_store import PlanStore, PlanStoreError
from .exclusion_report import (
    BatchExclusionReport,
    ExcludedColumn,
    ExclusionReport,
)

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "CanonicalValidationPlan",
    "ColumnMappingEntry",
    "MatchMethod",
    "PlanStatus",
    "PlanStore",
    "PlanStoreError",
    "ExclusionReport",
    "BatchExclusionReport",
    "ExcludedColumn",
]
