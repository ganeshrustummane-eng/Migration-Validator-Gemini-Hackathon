"""
Table Presence Checker  —  Step 3
===================================
Checks whether every source table EXISTS in the target BEFORE validation starts.

Design Rule:
    If a source table is NOT found in the target, validation CANNOT proceed —
    the migration did not happen (or the table was renamed / wrong schema).
    This is a CRITICAL FAIL, not a skip, not a warning.

    The check runs as the FIRST step in the pipeline:
        1. List all source tables
        2. List all target tables
        3. Diff → tables present in source but absent in target → CRITICAL FAIL
        4. Only proceed to column-level validation for matched tables

Exclusion Safety Valve:
    Some source tables are legitimately absent from the target (e.g. staging
    tables, temp tables, archive tables). These MUST be documented in
    config/exclusions.yaml under the 'table_exclusions' section.
    An undocumented absence is always a CRITICAL FAIL.

Usage:
    from core.table_presence_checker import TablePresenceChecker, TablePresenceResult

    checker = TablePresenceChecker(exclusions_config_path="config/exclusions.yaml")
    result = checker.check(
        source_tables=["orders", "customers", "products"],
        target_tables=["CUSTOMERS", "PRODUCTS"],   # orders MISSING
    )

    if result.has_critical_failures:
        print(result.render_cli())   # Shows CRITICAL FAIL for 'orders'
        sys.exit(1)
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TableStatus(Enum):
    """Presence status for a single source table."""
    MATCHED          = "MATCHED"           # Found in target (case-insensitive)
    MISSING_CRITICAL = "MISSING_CRITICAL"  # Not in target, not excluded → FAIL
    MISSING_EXCLUDED = "MISSING_EXCLUDED"  # Not in target, but documented in config
    TARGET_ONLY      = "TARGET_ONLY"       # In target but not in source → WARN


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableCheckEntry:
    """
    Result for a single table in the presence check.

    Attributes:
        source_table:    Table name as it appears in the source
        target_table:    Matched table name in the target (empty if missing)
        status:          MATCHED / MISSING_CRITICAL / MISSING_EXCLUDED / TARGET_ONLY
        exclusion_reason: If MISSING_EXCLUDED, why it was excluded
        message:         Human-readable message for CLI output
    """
    source_table:     str
    target_table:     str
    status:           TableStatus
    exclusion_reason: str = ""
    message:          str = ""

    @property
    def is_critical(self) -> bool:
        return self.status == TableStatus.MISSING_CRITICAL

    @property
    def is_matched(self) -> bool:
        return self.status == TableStatus.MATCHED

    @property
    def is_excluded(self) -> bool:
        return self.status == TableStatus.MISSING_EXCLUDED

    def cli_line(self, width: int = 30) -> str:
        """Return a single formatted line for CLI output."""
        status_tag = {
            TableStatus.MATCHED:          "  ✅ MATCHED          ",
            TableStatus.MISSING_CRITICAL: "  ❌ CRITICAL FAIL    ",
            TableStatus.MISSING_EXCLUDED: "  ⏭️  EXCLUDED (OK)    ",
            TableStatus.TARGET_ONLY:      "  ⚠️  TARGET ONLY      ",
        }[self.status]

        match_info = f"→ {self.target_table}" if self.target_table else "→ (not found)"
        return f"{status_tag}  {self.source_table:<{width}} {match_info}  {self.message}"


@dataclass
class TablePresenceResult:
    """
    Result of the full table presence check across all source tables.

    Attributes:
        entries:          One TableCheckEntry per source table
        target_only:      Target tables that have no source counterpart
        source_count:     Total source tables checked
        target_count:     Total target tables available
    """
    entries:      List[TableCheckEntry] = field(default_factory=list)
    target_only:  List[str] = field(default_factory=list)
    source_count: int = 0
    target_count: int = 0

    # -- derived --

    @property
    def matched(self) -> List[TableCheckEntry]:
        return [e for e in self.entries if e.is_matched]

    @property
    def critical_failures(self) -> List[TableCheckEntry]:
        return [e for e in self.entries if e.is_critical]

    @property
    def excluded_tables(self) -> List[TableCheckEntry]:
        return [e for e in self.entries if e.is_excluded]

    @property
    def has_critical_failures(self) -> bool:
        return bool(self.critical_failures)

    @property
    def matched_pairs(self) -> List[tuple]:
        """Return (source_table, target_table) pairs for validated tables only."""
        return [(e.source_table, e.target_table) for e in self.matched]

    # -- rendering --

    def render_cli(self, use_color: bool = True) -> str:
        """
        Render the full table presence report for CLI output.

        Shows:
          - MATCHED tables
          - CRITICAL FAIL tables (missing in target, not excluded)
          - EXCLUDED tables (missing but documented)
          - TARGET ONLY tables (in target, not in source)
        """
        GREEN   = "\033[92m" if use_color else ""
        RED     = "\033[91m" if use_color else ""
        YELLOW  = "\033[93m" if use_color else ""
        BOLD    = "\033[1m"  if use_color else ""
        DIM     = "\033[2m"  if use_color else ""
        RESET   = "\033[0m"  if use_color else ""
        CYAN    = "\033[96m" if use_color else ""

        lines = [
            f"\n{BOLD}{CYAN}{'═' * 70}{RESET}",
            f"{BOLD}{CYAN}  TABLE PRESENCE CHECK{RESET}",
            f"{BOLD}{CYAN}{'═' * 70}{RESET}",
            f"",
            f"  Source tables : {self.source_count}",
            f"  Target tables : {self.target_count}",
            f"  Matched       : {GREEN}{len(self.matched)}{RESET}",
            f"  Critical FAILs: {RED}{len(self.critical_failures)}{RESET}",
            f"  Excluded      : {DIM}{len(self.excluded_tables)}{RESET}",
            f"",
        ]

        # Matched tables
        if self.matched:
            lines.append(f"  {BOLD}MATCHED TABLES  ({len(self.matched)}){RESET}")
            lines.append(f"  {'─' * 66}")
            for e in self.matched:
                lines.append(
                    f"  {GREEN}✅{RESET}  {e.source_table:<30}  →  {e.target_table}"
                )
            lines.append("")

        # CRITICAL FAIL — missing tables
        if self.critical_failures:
            lines.append(f"  {BOLD}{RED}CRITICAL FAILURES — TABLE MISSING IN TARGET  ({len(self.critical_failures)}){RESET}")
            lines.append(f"  {'─' * 66}")
            for e in self.critical_failures:
                lines.append(
                    f"  {RED}❌  {e.source_table:<30}  →  NOT FOUND IN TARGET{RESET}"
                )
                lines.append(
                    f"  {DIM}     {e.message}{RESET}"
                )
            lines.append("")
            lines.append(
                f"  {RED}{BOLD}ACTION REQUIRED:{RESET}"
            )
            lines.append(
                f"  {DIM}  • Verify the table was migrated to the target database{RESET}"
            )
            lines.append(
                f"  {DIM}  • Check if the table name changed (rename mapping needed){RESET}"
            )
            lines.append(
                f"  {DIM}  • If absence is intentional, add to config/exclusions.yaml under table_exclusions{RESET}"
            )
            lines.append("")

        # EXCLUDED tables (documented absences)
        if self.excluded_tables:
            lines.append(f"  {BOLD}EXCLUDED TABLES  ({len(self.excluded_tables)}){RESET}  {DIM}(documented, acceptable){RESET}")
            lines.append(f"  {'─' * 66}")
            for e in self.excluded_tables:
                lines.append(
                    f"  {DIM}⏭️   {e.source_table:<30}  reason: {e.exclusion_reason}{RESET}"
                )
            lines.append("")

        # TARGET-ONLY tables (in target but not in source list being validated)
        if self.target_only:
            lines.append(f"  {BOLD}TARGET-ONLY TABLES  ({len(self.target_only)}){RESET}  {DIM}(not in source — FYI only){RESET}")
            lines.append(f"  {'─' * 66}")
            for t in self.target_only:
                lines.append(f"  {YELLOW}⚠️   {t}{RESET}")
            lines.append("")

        # Final verdict
        lines.append(f"  {'═' * 68}")
        if self.has_critical_failures:
            lines.append(
                f"  {RED}{BOLD}RESULT: ❌ FAIL — {len(self.critical_failures)} source table(s) not found in target{RESET}"
            )
            lines.append(
                f"  {RED}Validation CANNOT proceed for the missing tables.{RESET}"
            )
        else:
            lines.append(
                f"  {GREEN}{BOLD}RESULT: ✅ ALL SOURCE TABLES PRESENT IN TARGET{RESET}"
            )
        lines.append(f"  {'═' * 68}\n")

        return "\n".join(lines)

    def summary_dict(self) -> Dict:
        """Return a serialisable summary for logging and reporting."""
        return {
            "source_count":       self.source_count,
            "target_count":       self.target_count,
            "matched":            len(self.matched),
            "critical_failures":  len(self.critical_failures),
            "excluded":           len(self.excluded_tables),
            "has_critical_failures": self.has_critical_failures,
            "critical_tables": [e.source_table for e in self.critical_failures],
            "matched_tables":  [e.source_table for e in self.matched],
            "excluded_tables": [e.source_table for e in self.excluded_tables],
        }


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class TablePresenceChecker:
    """
    Checks whether every source table exists in the target before validation.

    Algorithm:
      1. Build a case-insensitive lookup from target table names
      2. For each source table:
         a. If found in target → MATCHED
         b. If not found:
            - Is it in exclusions.yaml table_exclusions? → MISSING_EXCLUDED
            - Otherwise → MISSING_CRITICAL (FAIL)
      3. Identify target-only tables (not in source list) for info
    """

    def __init__(self, exclusions_config_path: Optional[str] = None):
        """
        Args:
            exclusions_config_path: Path to config/exclusions.yaml.
                                   Used to check if a missing table is documented.
        """
        self._excluded_tables: Dict[str, str] = {}  # table_name_lower → reason
        if exclusions_config_path:
            self._load_table_exclusions(exclusions_config_path)

    def _load_table_exclusions(self, config_path: str) -> None:
        """
        Load table-level exclusions from config/exclusions.yaml.

        Expected YAML format under 'table_exclusions':
            table_exclusions:
              - table_name: "staging_orders"
                reason: "Staging table — not migrated to target by design"
              - table_name: "temp_customers"
                reason: "Temporary table — dropped after ETL"
        """
        try:
            path = Path(config_path)
            if not path.exists():
                return
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            exclusions = data.get("table_exclusions", [])
            if isinstance(exclusions, list):
                for entry in exclusions:
                    if isinstance(entry, dict):
                        name   = str(entry.get("table_name", "")).strip().lower()
                        reason = str(entry.get("reason", "Excluded by config")).strip()
                        if name:
                            self._excluded_tables[name] = reason
        except Exception:
            # Silently ignore parse errors — missing exclusions is safe (strict mode)
            pass

    def check(
        self,
        source_tables: List[str],
        target_tables: List[str],
    ) -> TablePresenceResult:
        """
        Perform the table presence check.

        Args:
            source_tables: List of table names from the source database
            target_tables: List of table names from the target database

        Returns:
            TablePresenceResult with complete analysis
        """
        # Build case-insensitive target lookup: UPPER(name) → original name
        target_upper: Dict[str, str] = {t.upper(): t for t in target_tables}
        source_upper_set: Set[str]   = {s.upper() for s in source_tables}

        entries: List[TableCheckEntry] = []

        for src_table in source_tables:
            src_upper = src_table.upper()
            matched_target = target_upper.get(src_upper, "")

            if matched_target:
                # Found in target
                entries.append(TableCheckEntry(
                    source_table=src_table,
                    target_table=matched_target,
                    status=TableStatus.MATCHED,
                    message="",
                ))
            else:
                # Not found — check exclusions
                exclusion_reason = self._excluded_tables.get(src_table.lower(), "")
                if exclusion_reason:
                    entries.append(TableCheckEntry(
                        source_table=src_table,
                        target_table="",
                        status=TableStatus.MISSING_EXCLUDED,
                        exclusion_reason=exclusion_reason,
                        message=f"Excluded: {exclusion_reason}",
                    ))
                else:
                    entries.append(TableCheckEntry(
                        source_table=src_table,
                        target_table="",
                        status=TableStatus.MISSING_CRITICAL,
                        message=(
                            f"Table '{src_table}' exists in source but was NOT found in target. "
                            f"Migration may be incomplete or table name changed."
                        ),
                    ))

        # Target-only tables (in target but not in source list being validated)
        target_only = [
            original_name
            for upper, original_name in target_upper.items()
            if upper not in source_upper_set
        ]

        return TablePresenceResult(
            entries=entries,
            target_only=sorted(target_only),
            source_count=len(source_tables),
            target_count=len(target_tables),
        )

    def check_single(
        self,
        source_table: str,
        target_tables: List[str],
    ) -> TableCheckEntry:
        """
        Convenience method to check a single source table against a target list.

        Args:
            source_table:  Source table name to look up
            target_tables: All available target tables

        Returns:
            TableCheckEntry for the source table
        """
        result = self.check(
            source_tables=[source_table],
            target_tables=target_tables,
        )
        return result.entries[0] if result.entries else TableCheckEntry(
            source_table=source_table,
            target_table="",
            status=TableStatus.MISSING_CRITICAL,
            message=f"Table '{source_table}' not found in target.",
        )
