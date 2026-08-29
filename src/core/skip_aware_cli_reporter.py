"""
Skip-Aware CLI Reporter  —  Step 2
=====================================
Renders the enhanced CLI output showing skipped columns in two sections:

  SECTION A — TABLE SUMMARY
    Shows each table with: Validated | Skipped | Failed counts

  SECTION B — SKIPPED COLUMNS DETAIL
    For each table that has skipped columns, shows:
      ✅ JUSTIFIED SKIPS  (acceptable — Fivetran, config, type-incompatible)
      ❌ UNJUSTIFIED SKIPS → AUTO-PROMOTED TO FAIL

  SECTION C — MISSING TABLES (from TablePresenceChecker)
    Tables that exist in source but are absent from target.

Design Rule:
    Any UNJUSTIFIED skip is reported alongside FAIL.
    The summary table shows the EFFECTIVE status:
      - If original status was PASS but unjustified skips exist → shows as FAIL
      - Coverage % is always printed next to status

Usage:
    from core.skip_aware_cli_reporter import SkipAwareCLIReporter
    from core.skip_classifier import SkipClassifier
    from core.table_presence_checker import TablePresenceResult

    reporter = SkipAwareCLIReporter()
    reporter.print_full_report(
        validation_results=results,   # dict from ValidationExecutor.execute_batch()
        plans=plans,                  # dict of table_name → CanonicalValidationPlan
        presence_result=presence,     # TablePresenceResult (from Step 3)
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.skip_classifier import SkipClassifier, SkipResult

if TYPE_CHECKING:
    from core.validation_plan import CanonicalValidationPlan
    from core.table_presence_checker import TablePresenceResult


# ---------------------------------------------------------------------------
# ANSI colour helpers (same palette as validate_cli.py)
# ---------------------------------------------------------------------------

class _C:
    BOLD    = "\033[1m"
    GREEN   = "\033[92m"
    CYAN    = "\033[96m"
    BLUE    = "\033[94m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"


# ---------------------------------------------------------------------------
# Per-table reporting result
# ---------------------------------------------------------------------------

@dataclass
class TableSkipReport:
    """
    Skip analysis for one table.

    Produced by SkipAwareCLIReporter.analyse_table() and consumed by
    print_full_report() to build the CLI output.
    """
    table_name:        str
    original_status:   str                    # 'PASS', 'FAIL', 'ERROR'
    effective_status:  str                    # May differ if unjustified skips exist
    validated_count:   int = 0
    total_count:       int = 0
    justified_skips:   List[SkipResult] = field(default_factory=list)
    unjustified_skips: List[SkipResult] = field(default_factory=list)
    warn_only_skips:   List[SkipResult] = field(default_factory=list)
    coverage_pct:      float = 0.0
    error_message:     str = ""

    @property
    def total_skipped(self) -> int:
        return len(self.justified_skips) + len(self.unjustified_skips) + len(self.warn_only_skips)

    @property
    def has_unjustified(self) -> bool:
        return bool(self.unjustified_skips)

    @property
    def has_any_skips(self) -> bool:
        return self.total_skipped > 0


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class SkipAwareCLIReporter:
    """
    Renders the enhanced CLI validation report with skip visibility.

    Three output sections:
      A. TABLE SUMMARY     — one row per table, shows effective status + counts
      B. SKIPPED COLUMNS   — detail block per table (justified vs unjustified)
      C. MISSING TABLES    — from TablePresenceChecker (critical failures)
    """

    def __init__(self, use_color: bool = True):
        self._classifier = SkipClassifier()
        self._c = _C() if use_color else _NoColor()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def analyse_table(
        self,
        table_name: str,
        validation_result: Dict[str, Any],
        plan: Optional["CanonicalValidationPlan"] = None,
    ) -> TableSkipReport:
        """
        Analyse one table's validation result and classify all skips.

        Args:
            table_name:        Table name
            validation_result: Dict from ValidationExecutor (has 'status', 'error', etc.)
            plan:              CanonicalValidationPlan if available

        Returns:
            TableSkipReport with classified skips and effective status
        """
        original_status = validation_result.get("status", "ERROR")
        error_message   = validation_result.get("error", "") or ""

        # Get exclusion summary from plan
        validated_count = 0
        total_count     = 0
        all_skip_results: List[SkipResult] = []

        if plan is not None:
            excl = plan.exclusion_summary()
            validated_count = excl.get("validated", 0)
            total_count     = excl.get("total_source_columns", 0)
            excluded_items  = excl.get("excluded", [])

            # Classify every skipped column
            all_skip_results = self._classifier.classify_all(excluded_items)

        # Partition into justified / unjustified / warn_only
        justified   = self._classifier.justified(all_skip_results)
        unjustified = self._classifier.unjustified(all_skip_results)
        warn_only   = self._classifier.warn_only(all_skip_results)

        # Effective status: if UNJUSTIFIED skips exist, override to FAIL
        if unjustified and original_status == "PASS":
            effective_status = "FAIL"
        else:
            effective_status = original_status

        coverage_pct = (
            round(100.0 * validated_count / total_count, 1)
            if total_count else 0.0
        )

        return TableSkipReport(
            table_name=table_name,
            original_status=original_status,
            effective_status=effective_status,
            validated_count=validated_count,
            total_count=total_count,
            justified_skips=justified,
            unjustified_skips=unjustified,
            warn_only_skips=warn_only,
            coverage_pct=coverage_pct,
            error_message=error_message,
        )

    def print_full_report(
        self,
        validation_results: Dict[str, Dict[str, Any]],
        plans: Optional[Dict[str, "CanonicalValidationPlan"]] = None,
        presence_result: Optional["TablePresenceResult"] = None,
    ) -> List[TableSkipReport]:
        """
        Print the complete three-section CLI report.

        Args:
            validation_results: Dict of result_key → result dict from ValidationExecutor
            plans:              Optional dict of table_name → CanonicalValidationPlan
            presence_result:    Optional TablePresenceResult from TablePresenceChecker

        Returns:
            List of TableSkipReport (one per table, for programmatic use)
        """
        plans = plans or {}

        # Build per-table reports (deduplicate by table name)
        table_reports: Dict[str, TableSkipReport] = {}
        for result_key, result in validation_results.items():
            table_name = result.get("table", result_key)
            if table_name in table_reports:
                continue  # Already processed this table
            plan = plans.get(table_name)
            report = self.analyse_table(table_name, result, plan)
            table_reports[table_name] = report

        reports_list = list(table_reports.values())

        # ── SECTION A: TABLE SUMMARY ────────────────────────────────────────
        self._print_section_a(reports_list)

        # ── SECTION B: SKIPPED COLUMNS DETAIL ──────────────────────────────
        tables_with_skips = [r for r in reports_list if r.has_any_skips]
        if tables_with_skips:
            self._print_section_b(tables_with_skips)

        # ── SECTION C: MISSING TABLES ───────────────────────────────────────
        if presence_result and presence_result.has_critical_failures:
            print(presence_result.render_cli(use_color=isinstance(self._c, _C)))

        # ── OVERALL VERDICT ─────────────────────────────────────────────────
        self._print_overall_verdict(reports_list, presence_result)

        return reports_list

    # -----------------------------------------------------------------------
    # Section A — Table Summary
    # -----------------------------------------------------------------------

    def _print_section_a(self, reports: List[TableSkipReport]) -> None:
        c = self._c
        print(f"\n{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}  SECTION A — TABLE VALIDATION SUMMARY{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print()
        print(
            f"  {c.BOLD}{'Table':<30} {'Status':<22} {'Validated':>10} "
            f"{'Skipped':>8} {'Coverage':>10}{c.RESET}"
        )
        print(f"  {'─' * 76}")

        for r in reports:
            status_str, status_color = self._format_status(r)

            # Flag if status was promoted from PASS → FAIL
            promotion_tag = ""
            if r.original_status == "PASS" and r.effective_status == "FAIL":
                promotion_tag = f"  {c.YELLOW}[was PASS → promoted to FAIL]{c.RESET}"

            # Skip count breakdown
            skip_str = ""
            if r.total_skipped:
                justified_n   = len(r.justified_skips)
                unjustified_n = len(r.unjustified_skips)
                warn_n        = len(r.warn_only_skips)
                parts = []
                if justified_n:
                    parts.append(f"{c.DIM}{justified_n} ok{c.RESET}")
                if unjustified_n:
                    parts.append(f"{c.RED}{unjustified_n} FAIL{c.RESET}")
                if warn_n:
                    parts.append(f"{c.YELLOW}{warn_n} warn{c.RESET}")
                skip_str = f"({', '.join(parts)})"

            validated_str = (
                f"{r.validated_count}/{r.total_count}"
                if r.total_count else "N/A"
            )
            coverage_str = (
                f"{r.coverage_pct}%"
                if r.total_count else "UNKNOWN"
            )
            coverage_color = (
                c.GREEN if r.coverage_pct >= 80
                else c.YELLOW if r.coverage_pct >= 50
                else c.RED
            ) if r.total_count else c.DIM

            print(
                f"  {c.BOLD}{r.table_name:<30}{c.RESET} "
                f"{status_color}{status_str:<22}{c.RESET} "
                f"{validated_str:>10} "
                f"{r.total_skipped:>8} {skip_str:<25} "
                f"{coverage_color}{coverage_str:>10}{c.RESET}"
                f"{promotion_tag}"
            )

        print()

    # -----------------------------------------------------------------------
    # Section B — Skipped Columns Detail
    # -----------------------------------------------------------------------

    def _print_section_b(self, tables_with_skips: List[TableSkipReport]) -> None:
        c = self._c
        print(f"\n{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}  SECTION B — SKIPPED COLUMNS DETAIL{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print(f"  {c.DIM}Every skip must be justified. Unjustified skips are FAIL.{c.RESET}")

        for r in tables_with_skips:
            print(f"\n  {c.BOLD}{c.MAGENTA}Table: {r.table_name}{c.RESET}")
            print(f"  {'─' * 70}")

            # ── JUSTIFIED SKIPS ──────────────────────────────────────────────
            if r.justified_skips:
                print(f"\n  {c.GREEN}{c.BOLD}✅ JUSTIFIED SKIPS  ({len(r.justified_skips)}):{c.RESET}")
                print(f"  {c.DIM}  These are acceptable — documented and recognized.{c.RESET}")
                for skip in r.justified_skips:
                    print(
                        f"    {c.DIM}{skip.column_name:<30}{c.RESET}  "
                        f"[{c.CYAN}{skip.category.value}{c.RESET}]  "
                        f"{c.DIM}{skip.message}{c.RESET}"
                    )

            # ── UNJUSTIFIED SKIPS → FAIL ─────────────────────────────────────
            if r.unjustified_skips:
                print(f"\n  {c.RED}{c.BOLD}❌ UNJUSTIFIED SKIPS — AUTO-PROMOTED TO FAIL  ({len(r.unjustified_skips)}):{c.RESET}")
                print(f"  {c.DIM}  These columns were skipped without a valid reason.{c.RESET}")
                for skip in r.unjustified_skips:
                    print(
                        f"    {c.RED}{skip.column_name:<30}{c.RESET}  "
                        f"[{c.YELLOW}{skip.category.value}{c.RESET}]"
                    )
                    print(f"    {c.RED}    ↳ {skip.message}{c.RESET}")
                print()
                print(f"  {c.YELLOW}  ACTION:{c.RESET}")
                print(f"  {c.DIM}    • If column should be excluded: add to config/exclusions.yaml with a reason{c.RESET}")
                print(f"  {c.DIM}    • If column was missing in migration: fix the migration and re-validate{c.RESET}")

            # ── WARN ONLY ────────────────────────────────────────────────────
            if r.warn_only_skips:
                print(f"\n  {c.YELLOW}{c.BOLD}⚠️  TARGET-ONLY COLUMNS — WARN ONLY  ({len(r.warn_only_skips)}):{c.RESET}")
                print(f"  {c.DIM}  These columns exist in target but not in source (enrichment, derived, etc.){c.RESET}")
                for skip in r.warn_only_skips:
                    print(
                        f"    {c.YELLOW}{skip.column_name:<30}{c.RESET}  "
                        f"[{skip.category.value}]  "
                        f"{c.DIM}{skip.message}{c.RESET}"
                    )

        print()

    # -----------------------------------------------------------------------
    # Overall verdict
    # -----------------------------------------------------------------------

    def _print_overall_verdict(
        self,
        reports: List[TableSkipReport],
        presence_result: Optional["TablePresenceResult"],
    ) -> None:
        c = self._c

        pass_count   = sum(1 for r in reports if r.effective_status == "PASS")
        fail_count   = sum(1 for r in reports if r.effective_status == "FAIL")
        error_count  = sum(1 for r in reports if r.effective_status == "ERROR")
        missing_count = (
            len(presence_result.critical_failures)
            if presence_result else 0
        )
        promoted_count = sum(
            1 for r in reports
            if r.original_status == "PASS" and r.effective_status == "FAIL"
        )

        total_fail = fail_count + missing_count

        print(f"\n{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}  OVERALL VALIDATION RESULT{c.RESET}")
        print(f"{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}")
        print()
        print(f"  {c.GREEN}✅ PASS       : {pass_count}{c.RESET}")
        print(f"  {c.RED}❌ FAIL       : {fail_count}{c.RESET}")
        if missing_count:
            print(f"  {c.RED}❌ MISSING    : {missing_count}  (tables absent from target){c.RESET}")
        if error_count:
            print(f"  {c.YELLOW}⚠️  ERROR      : {error_count}{c.RESET}")
        if promoted_count:
            print(f"  {c.YELLOW}⚠️  PROMOTED   : {promoted_count}  (PASS → FAIL due to unjustified skips){c.RESET}")
        print()

        if total_fail > 0 or error_count > 0:
            print(f"  {c.RED}{c.BOLD}FINAL RESULT: ❌  FAIL{c.RESET}")
            if promoted_count:
                print(
                    f"  {c.YELLOW}  Note: {promoted_count} table(s) had unjustified skips. "
                    f"Their PASS was automatically converted to FAIL.{c.RESET}"
                )
        else:
            print(f"  {c.GREEN}{c.BOLD}FINAL RESULT: ✅  PASS{c.RESET}")
        print(f"\n{c.BOLD}{c.CYAN}{'═' * 80}{c.RESET}\n")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _format_status(self, r: TableSkipReport) -> tuple:
        """Return (status_string, color_code) for the summary table."""
        c = self._c
        if r.effective_status == "PASS":
            return "✅ PASS", c.GREEN
        elif r.effective_status == "FAIL":
            return "❌ FAIL", c.RED
        elif r.effective_status == "ERROR":
            return "⚠️  ERROR", c.YELLOW
        else:
            return r.effective_status, c.DIM


# ---------------------------------------------------------------------------
# No-color fallback (for log files)
# ---------------------------------------------------------------------------

class _NoColor:
    """Dummy color class that returns empty strings for all attributes."""
    def __getattr__(self, _):
        return ""
