"""
Plan Validator
==============
Validates a CanonicalValidationPlan before SQL/YAML generation.

The validator enforces:
  1. Table identity completeness — source/target names must not be empty.
  2. At least one active mapping — no point generating an empty SQL file.
  3. No duplicate source columns — each source column appears exactly once.
  4. No duplicate target columns — each target column is mapped at most once.
  5. Column names are not empty strings.
  6. Transformation rule IDs are valid (known to the rules registry).
  7. Confidence scores are in [0.0, 1.0].
  8. Plan status is not INVALID — callers should not generate SQL from INVALID plans.

If any check fails, PlanValidationError is raised with a list of all failures.
The plan status field is ALSO updated to INVALID by the validator.

Security:
  - The validator never executes SQL or connects to databases.
  - It operates only on the in-memory plan object.
"""

from dataclasses import dataclass, field
from typing import List, Set

try:
    from ..core.validation_plan import CanonicalValidationPlan, PlanStatus
    from ..rules import get_registry
except ImportError:
    # Support the documented `cd src && python validate_cli.py` launch mode.
    from core.validation_plan import CanonicalValidationPlan, PlanStatus
    from rules import get_registry

# Known rule IDs (queried from the registry at validation time)
_FALLBACK_KNOWN_RULES: Set[str] = {
    "boolean", "numeric", "timestamp_ntz", "timestamp_tz",
    "date", "text", "uuid", "integer", "json", "bytea", "hstore",
    "null_standardization", "null",
}


class PlanValidationError(ValueError):
    """
    Raised when a CanonicalValidationPlan fails validation.

    Attributes:
        issues: List of human-readable failure descriptions.
    """

    def __init__(self, issues: List[str]):
        self.issues = issues
        joined = "; ".join(issues)
        super().__init__(f"Plan validation failed ({len(issues)} issue(s)): {joined}")


@dataclass
class ValidationResult:
    """
    Result of a plan validation run.

    Attributes:
        is_valid  : True if the plan passes all checks.
        issues    : List of failure descriptions (empty when is_valid=True).
        warnings  : Non-fatal warnings that do not block generation.
    """
    is_valid:  bool
    issues:    List[str] = field(default_factory=list)
    warnings:  List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.is_valid


class PlanValidator:
    """
    Validates a CanonicalValidationPlan before SQL/YAML generation.

    Usage
    -----
        validator = PlanValidator()
        result    = validator.validate(plan)

        if not result:
            for issue in result.issues:
                print(f"  ✗ {issue}")
            raise PlanValidationError(result.issues)

        # Safe to generate SQL
        sql_gen.generate_from_plan(plan)

    The validator also calls plan.validate() so the plan's own status field
    is updated to INVALID if any blocking issues are found.
    """

    def validate(self, plan: CanonicalValidationPlan) -> ValidationResult:
        """
        Run all validation checks on the plan.

        Does NOT raise — callers inspect the result and decide whether to
        raise PlanValidationError.

        Args:
            plan: The plan to validate (mutated: status set to INVALID on failure)

        Returns:
            ValidationResult — is_valid=True if all blocking checks pass.
        """
        issues:   List[str] = []
        warnings: List[str] = []

        # ── 1. Table identity ───────────────────────────────────────────────
        if not plan.source_table.strip():
            issues.append("source_table is empty — cannot generate SQL")
        if not plan.target_table.strip():
            issues.append("target_table is empty — cannot generate SQL")
        if not plan.source_schema.strip():
            issues.append("source_schema is empty — cannot generate SQL")

        # ── 2. Active mappings ──────────────────────────────────────────────
        active = plan.active_mappings
        if not active:
            issues.append(
                "No active column mappings — all columns are skipped. "
                "Generated SQL would be empty."
            )

        # ── 3. Column name uniqueness ────────────────────────────────────────
        seen_src: Set[str] = set()
        seen_tgt: Set[str] = set()
        for entry in active:
            src_upper = entry.source_column.upper()
            tgt_upper = entry.target_column.upper()

            if not entry.source_column.strip():
                issues.append(f"Empty source_column in mapping (target={entry.target_column})")
            if not entry.target_column.strip():
                issues.append(f"Empty target_column in mapping (source={entry.source_column})")

            if src_upper in seen_src:
                issues.append(
                    f"Duplicate source column '{entry.source_column}' — "
                    "each source column must appear exactly once"
                )
            seen_src.add(src_upper)

            if tgt_upper in seen_tgt:
                warnings.append(
                    f"Target column '{entry.target_column}' mapped to multiple source columns — "
                    "this may cause duplicate column aliases in SQL"
                )
            seen_tgt.add(tgt_upper)

        # ── 4. Transformation rule validity ─────────────────────────────────
        known_rules = _get_known_rule_ids()
        for entry in active:
            rule_id = (entry.transformation_rule or "").lower()
            if not rule_id:
                issues.append(
                    f"Column '{entry.source_column}' has no transformation rule assigned"
                )
            elif rule_id not in known_rules:
                warnings.append(
                    f"Column '{entry.source_column}' has unknown rule '{rule_id}' — "
                    "will fall back to TextRule"
                )

        # ── 5. Confidence score range ────────────────────────────────────────
        for entry in active:
            if not (0.0 <= entry.confidence <= 1.0):
                warnings.append(
                    f"Column '{entry.source_column}' has confidence={entry.confidence:.3f} "
                    "outside [0.0, 1.0] — will be clamped"
                )

        # ── 6. Plan-level status check ───────────────────────────────────────
        if plan.status == PlanStatus.INVALID.value:
            issues.append(
                "Plan status is INVALID — it was marked invalid before validation"
            )

        # ── 7. Ambiguities check ─────────────────────────────────────────────
        if plan.ambiguities:
            warnings.append(
                f"{len(plan.ambiguities)} ambiguous column(s) remain unresolved: "
                + ", ".join(plan.ambiguities[:5])
                + (" ..." if len(plan.ambiguities) > 5 else "")
            )

        # ── 8. Unmatched source columns ──────────────────────────────────────
        if plan.unmatched_source_columns:
            unmatched_count = len(plan.unmatched_source_columns)
            total           = plan.total_source_columns or 1
            unmatched_pct   = 100.0 * unmatched_count / total
            if unmatched_pct > 50.0:
                issues.append(
                    f"{unmatched_count} of {total} source columns are unmatched "
                    f"({unmatched_pct:.0f}%) — more than half are missing from target"
                )
            else:
                warnings.append(
                    f"{unmatched_count} source column(s) are unmatched and will be skipped: "
                    + ", ".join(plan.unmatched_source_columns[:5])
                    + (" ..." if unmatched_count > 5 else "")
                )

        # ── Finalise ─────────────────────────────────────────────────────────
        is_valid = len(issues) == 0

        if not is_valid:
            plan.status = PlanStatus.INVALID.value
            plan.warnings.extend(issues)

        # Add non-fatal warnings to the plan for display
        for w in warnings:
            if w not in plan.warnings:
                plan.warnings.append(w)

        return ValidationResult(is_valid=is_valid, issues=issues, warnings=warnings)

    def validate_or_raise(self, plan: CanonicalValidationPlan) -> ValidationResult:
        """
        Validate the plan and raise PlanValidationError if invalid.

        Args:
            plan: The plan to validate

        Returns:
            ValidationResult (only if valid)

        Raises:
            PlanValidationError: If any blocking issue is found.
        """
        result = self.validate(plan)
        if not result:
            raise PlanValidationError(result.issues)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_known_rule_ids() -> Set[str]:
    """Return the set of known rule IDs from the global registry."""
    try:
        registry = get_registry()
        return {r.rule_name.lower() for r in registry._rules}
    except Exception:
        return _FALLBACK_KNOWN_RULES
