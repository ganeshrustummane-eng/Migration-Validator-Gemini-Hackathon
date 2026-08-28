"""
Exclusion Report
=================
Makes excluded columns impossible to overlook.

A validator that silently drops columns is worse than no validator: it emits
a green 100% that nobody questions. Every run therefore carries an
ExclusionReport, and every surface (CLI, batch summary, run manifest) prints
the headline alongside the pass rate:

    6 of 9 columns validated (66.7%) — 3 excluded: uTS (rowversion — not
    comparable), _FIVETRAN_SYNCED (pattern ^_FIVETRAN_.*), SSN (PII policy)

Coverage and pass rate are reported together, never separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.validation_plan import CanonicalValidationPlan

# Below this fraction of columns actually validated, the run is flagged loudly:
# a "pass" covering less than 80% of the table is not a meaningful pass.
LOW_COVERAGE_THRESHOLD_PCT = 80.0


@dataclass(frozen=True)
class ExcludedColumn:
    column: str
    reason: str
    data_type: str = ""

    def render(self) -> str:
        return f"{self.column} ({self.reason})"


@dataclass
class ExclusionReport:
    """Column coverage for a single table validation."""

    table: str
    total_columns: int
    validated_columns: int
    excluded: List[ExcludedColumn] = field(default_factory=list)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_plan(cls, plan: "CanonicalValidationPlan") -> "ExclusionReport":
        summary = plan.exclusion_summary()
        return cls(
            table=plan.source_table,
            total_columns=summary["total_source_columns"],
            validated_columns=summary["validated"],
            excluded=[
                ExcludedColumn(
                    column=item["column"],
                    reason=item["reason"],
                    data_type=item.get("type", ""),
                )
                for item in summary["excluded"]
            ],
        )

    # -- derived ------------------------------------------------------------

    @property
    def coverage_pct(self) -> float:
        if not self.total_columns:
            return 0.0
        return round(100.0 * self.validated_columns / self.total_columns, 1)

    @property
    def is_low_coverage(self) -> bool:
        return self.coverage_pct < LOW_COVERAGE_THRESHOLD_PCT

    @property
    def has_exclusions(self) -> bool:
        return bool(self.excluded)

    # -- rendering ----------------------------------------------------------

    def headline(self) -> str:
        """One-line coverage statement — print this next to every pass rate."""
        base = (
            f"{self.validated_columns} of {self.total_columns} columns validated "
            f"({self.coverage_pct}%)"
        )
        if not self.excluded:
            return f"{base} — no exclusions"
        detail = ", ".join(c.render() for c in self.excluded)
        return f"{base} — {len(self.excluded)} excluded: {detail}"

    def render(self, indent: str = "  ") -> str:
        """Multi-line block for terminal output and log files."""
        lines = [
            f"{indent}COLUMN COVERAGE — {self.table}",
            f"{indent}{'-' * 58}",
            f"{indent}Validated : {self.validated_columns} / {self.total_columns}"
            f" ({self.coverage_pct}%)",
        ]
        if self.excluded:
            lines.append(f"{indent}Excluded  : {len(self.excluded)}")
            width = max(len(c.column) for c in self.excluded)
            for col in self.excluded:
                type_tag = f" [{col.data_type}]" if col.data_type else ""
                lines.append(f"{indent}  ✗ {col.column:<{width}}{type_tag} — {col.reason}")
        else:
            lines.append(f"{indent}Excluded  : none")

        if self.is_low_coverage:
            lines.append(
                f"{indent}!! LOW COVERAGE — under {LOW_COVERAGE_THRESHOLD_PCT}% of columns "
                f"were compared. Treat any PASS as partial."
            )
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table":             self.table,
            "total_columns":     self.total_columns,
            "validated_columns": self.validated_columns,
            "excluded_count":    len(self.excluded),
            "coverage_pct":      self.coverage_pct,
            "low_coverage":      self.is_low_coverage,
            "headline":          self.headline(),
            "excluded": [
                {"column": c.column, "type": c.data_type, "reason": c.reason}
                for c in self.excluded
            ],
        }


@dataclass
class BatchExclusionReport:
    """Aggregates per-table coverage so a batch cannot hide a thin run."""

    reports: List[ExclusionReport] = field(default_factory=list)

    def add(self, report: ExclusionReport) -> None:
        self.reports.append(report)

    @property
    def total_columns(self) -> int:
        return sum(r.total_columns for r in self.reports)

    @property
    def validated_columns(self) -> int:
        return sum(r.validated_columns for r in self.reports)

    @property
    def coverage_pct(self) -> float:
        if not self.total_columns:
            return 0.0
        return round(100.0 * self.validated_columns / self.total_columns, 1)

    @property
    def low_coverage_tables(self) -> List[ExclusionReport]:
        return [r for r in self.reports if r.is_low_coverage]

    def render(self, indent: str = "  ") -> str:
        if not self.reports:
            return f"{indent}No column coverage recorded."
        lines = [
            f"{indent}COLUMN COVERAGE — {len(self.reports)} table(s)",
            f"{indent}{'=' * 58}",
            f"{indent}Overall   : {self.validated_columns} / {self.total_columns} columns"
            f" ({self.coverage_pct}%)",
            "",
        ]
        for report in self.reports:
            lines.append(f"{indent}• {report.headline()}")
        if self.low_coverage_tables:
            lines.append("")
            lines.append(f"{indent}!! {len(self.low_coverage_tables)} table(s) below "
                         f"{LOW_COVERAGE_THRESHOLD_PCT}% column coverage:")
            for report in self.low_coverage_tables:
                lines.append(f"{indent}   - {report.table} ({report.coverage_pct}%)")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_columns":     self.total_columns,
            "validated_columns": self.validated_columns,
            "coverage_pct":      self.coverage_pct,
            "low_coverage_tables": [r.table for r in self.low_coverage_tables],
            "tables": [r.to_dict() for r in self.reports],
        }


def report_from_yaml_block(
    table: str,
    config: Dict[str, Any],
    plan_lookup: Optional[Any] = None,
) -> Optional[ExclusionReport]:
    """
    Recover the coverage report for an executing YAML block.

    The YAML is only a render target, so coverage is read from the plan that
    produced it. Returns None when no plan exists — the caller must then warn
    that coverage is unknown rather than assume full coverage.
    """
    if plan_lookup is None:
        return None
    plan = plan_lookup(table)
    return ExclusionReport.from_plan(plan) if plan else None
