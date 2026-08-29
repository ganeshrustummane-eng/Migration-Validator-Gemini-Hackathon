"""
Exclusion Manager
==================
Central manager for all column exclusion logic in the Migration Validator.

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │ config/exclusions.yaml                                  │
  │  ├─ Global exclusions                                   │
  │  ├─ Pattern-based exclusions (regex)                    │
  │  ├─ Type-based exclusions (source→target type pairs)    │
  │  ├─ Table-specific exclusions                           │
  │  ├─ Database-specific exclusions                        │
  │  ├─ Compliance exclusions (PII/PHI/PCI)                 │
  │  └─ Performance exclusions                              │
  └─────────────────────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────────────────────┐
  │ ExclusionManager (this module)                          │
  │  • Load & parse exclusions.yaml                         │
  │  • Evaluate exclusion rules (priority order)            │
  │  • Cache decisions for performance                      │
  │  • Generate exclusion reports                           │
  │  • CLI integration                                      │
  └─────────────────────────────────────────────────────────┘
                    ↓
  ┌─────────────────────────────────────────────────────────┐
  │ static_rule_mapper.py / validation pipeline             │
  │  • Query: "Should column X be excluded?"                │
  │  • Response: ExclusionDecision (excluded, reason, rule) │
  └─────────────────────────────────────────────────────────┘

Priority Order (highest to lowest):
  1. Table-specific exclusions
  2. Type-based exclusions
  3. Database-specific exclusions
  4. Compliance exclusions (PII/PHI/PCI)
  5. Pattern-based exclusions (regex)
  6. Global exclusions
  7. Performance exclusions

Design Principles:
  • Fail-open: If config can't be loaded, validation proceeds with warnings
  • Explicit > Implicit: Table-specific overrides everything
  • Performance: Cache all decisions (exclusions rarely change mid-run)
  • Auditability: Every exclusion logged with reason
  • Thread-safe: Uses locks for concurrent validation batches

Usage:
  from exclusions.exclusion_manager import exclusion_manager
  
  decision = exclusion_manager.should_exclude(
      column_name="uTS",
      source_table="AcctSoftware",
      source_type="timestamp",
      target_type="BINARY",
      source_database="mssql",
  )
  
  if decision.excluded:
      print(f"Excluded: {decision.reason}")
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml


# ── Exclusion Decision Result ─────────────────────────────────────────────

@dataclass
class ExclusionDecision:
    """Result of an exclusion evaluation."""
    excluded: bool
    reason: str = ""
    rule_type: str = ""  # e.g., "table_specific", "pattern", "global"
    rule_name: str = ""  # e.g., "AcctSoftware.uTS", "_FIVETRAN_.*"
    applies_to: List[str] = field(default_factory=lambda: ["source", "target"])
    metadata: Dict[str, any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        if not self.excluded:
            return "Not excluded"
        return f"Excluded ({self.rule_type}): {self.reason}"


# ── Exclusion Manager ─────────────────────────────────────────────────────

class ExclusionManager:
    """
    Central manager for all column exclusion logic.
    
    Loads exclusions.yaml at startup and provides decision API.
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the exclusion manager.
        
        Args:
            config_path: Path to exclusions.yaml. If None, uses default location.
        """
        self._config_path = config_path or Path(__file__).parent.parent.parent / "config" / "exclusions.yaml"
        self._config: Dict = {}
        self._cache: Dict[str, ExclusionDecision] = {}
        self._lock = threading.RLock()
        self._loaded = False
        self._load_config()
    
    # ──────────────────────────────────────────────────────────────────────
    # Configuration Loading
    # ──────────────────────────────────────────────────────────────────────
    
    def _load_config(self) -> bool:
        """Load exclusions from YAML config file."""
        if not self._config_path.exists():
            print(f"  [WARN] Exclusions config not found at {self._config_path}")
            print(f"  [INFO] Proceeding with built-in defaults only")
            self._config = self._default_config()
            return False

        try:
            with open(self._config_path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            # Only real load failures fall back. A broad except here previously
            # swallowed a UnicodeEncodeError from the success message below,
            # silently discarding every configured exclusion rule on Windows.
            print(f"  [ERROR] Failed to load exclusions config: {exc}")
            print(f"  [INFO] Falling back to built-in defaults")
            self._config = self._default_config()
            return False

        self._loaded = True
        print(f"  [OK] Loaded exclusion rules from {self._config_path.name}")
        self._print_stats()
        return True
    
    def _default_config(self) -> Dict:
        """Return minimal default config if file is missing."""
        return {
            "global_exclusions": {
                "columns": [
                    {"column_name": "_FIVETRAN_DELETED", "reason": "Fivetran metadata"},
                    {"column_name": "_FIVETRAN_SYNCED", "reason": "Fivetran metadata"},
                    {"column_name": "_FIVETRAN_ID", "reason": "Fivetran metadata"},
                ]
            },
            "pattern_exclusions": {
                "patterns": [
                    {"pattern": "^_FIVETRAN_.*", "reason": "Fivetran internal columns"},
                ]
            },
        }
    
    def _print_stats(self):
        """Print loaded exclusion rules statistics."""
        global_count = len(self._config.get("global_exclusions", {}).get("columns", []))
        pattern_count = len(self._config.get("pattern_exclusions", {}).get("patterns", []))
        type_count = len(self._config.get("type_based_exclusions", {}).get("rules", []))
        table_count = len(self._config.get("table_specific_exclusions", {})) - 1  # -1 for description key
        
        print(f"    - Global exclusions: {global_count}")
        print(f"    - Pattern rules: {pattern_count}")
        print(f"    - Type-based rules: {type_count}")
        print(f"    - Table-specific: {table_count} table(s)")
    
    # ──────────────────────────────────────────────────────────────────────
    # Public API — Exclusion Decision
    # ──────────────────────────────────────────────────────────────────────
    
    def should_exclude(
        self,
        column_name: str,
        source_table: str = "",
        source_type: str = "",
        target_type: str = "",
        source_database: str = "",
        target_database: str = "snowflake",
        applies_to: str = "both",  # "source", "target", "both"
    ) -> ExclusionDecision:
        """
        Determine if a column should be excluded from validation.
        
        Evaluates exclusion rules in priority order:
          1. Table-specific
          2. Type-based
          3. Database-specific
          4. Compliance (PII/PHI/PCI)
          5. Pattern-based
          6. Global
          7. Performance
        
        Args:
            column_name: Name of the column to check
            source_table: Source table name (for table-specific rules)
            source_type: Source column data type
            target_type: Target column data type
            source_database: Source database type (mssql, postgresql, athena)
            target_database: Target database type (default: snowflake)
            applies_to: Where to check ("source", "target", "both")
        
        Returns:
            ExclusionDecision with excluded flag and reason
        """
        # Build cache key
        cache_key = f"{source_table}::{column_name}::{source_type}::{target_type}::{source_database}::{applies_to}"
        
        # Check cache
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        # Evaluate rules in priority order
        decision = None
        
        # 1. Table-specific (highest priority)
        decision = self._check_table_specific(column_name, source_table, source_database, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 2. Type-based
        decision = self._check_type_based(source_type, target_type, source_database, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 3. Database-specific
        decision = self._check_database_specific(column_name, source_database, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 4. Compliance (PII/PHI/PCI)
        decision = self._check_compliance(column_name, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 5. Pattern-based
        decision = self._check_patterns(column_name, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 6. Global
        decision = self._check_global(column_name, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # 7. Performance (lowest priority)
        decision = self._check_performance(source_type, target_type, applies_to)
        if decision.excluded:
            self._cache[cache_key] = decision
            return decision
        
        # Not excluded
        decision = ExclusionDecision(excluded=False)
        self._cache[cache_key] = decision
        return decision
    
    # ──────────────────────────────────────────────────────────────────────
    # Rule Evaluation Methods (Priority Order)
    # ──────────────────────────────────────────────────────────────────────
    
    def _check_table_specific(
        self,
        column_name: str,
        source_table: str,
        source_database: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check table-specific exclusions (highest priority)."""
        if not source_table:
            return ExclusionDecision(excluded=False)
        
        table_exclusions = self._config.get("table_specific_exclusions", {})
        
        # Case-insensitive table name lookup
        table_config = None
        for table_key, config in table_exclusions.items():
            if table_key == "description":
                continue
            if table_key.upper() == source_table.upper():
                table_config = config
                break
        
        if not table_config:
            return ExclusionDecision(excluded=False)
        
        # Check if source database matches (if specified)
        if "source_database" in table_config:
            if table_config["source_database"].lower() != source_database.lower():
                return ExclusionDecision(excluded=False)
        
        # Check exclusions list
        for exclusion in table_config.get("exclusions", []):
            # Check column name (case-insensitive)
            excluded_names = [exclusion.get("column_name", "")]
            excluded_names.extend(exclusion.get("source_column_names", []))
            excluded_names.extend(exclusion.get("target_column_names", []))
            
            for excluded_name in excluded_names:
                if excluded_name.upper() == column_name.upper():
                    # Check applies_to
                    rule_applies_to = exclusion.get("applies_to", ["source", "target"])
                    if self._matches_applies_to(applies_to, rule_applies_to):
                        return ExclusionDecision(
                            excluded=True,
                            reason=exclusion.get("reason", "Table-specific exclusion"),
                            rule_type="table_specific",
                            rule_name=f"{source_table}.{column_name}",
                            applies_to=rule_applies_to,
                            metadata={
                                "table": source_table,
                                "pii": exclusion.get("pii", False),
                                "compliance": exclusion.get("compliance", ""),
                                "performance": exclusion.get("performance", False),
                            }
                        )
        
        return ExclusionDecision(excluded=False)
    
    def _check_type_based(
        self,
        source_type: str,
        target_type: str,
        source_database: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check type-based exclusions."""
        if not source_type or not target_type:
            return ExclusionDecision(excluded=False)
        
        type_rules = self._config.get("type_based_exclusions", {}).get("rules", [])
        
        for rule in type_rules:
            if not rule.get("enabled", True):
                continue
            
            # Check source type match
            source_types = [t.lower() for t in rule.get("source_types", [])]
            if source_type.lower() not in source_types:
                continue
            
            # Check target type match
            target_types = [t.upper() for t in rule.get("target_types", [])]
            if target_type.upper() not in target_types:
                continue
            
            # Check source database match (if specified)
            if "source_database" in rule:
                if rule["source_database"].lower() != source_database.lower():
                    continue
            
            # Match found
            return ExclusionDecision(
                excluded=True,
                reason=rule.get("reason", "Type-based exclusion"),
                rule_type="type_based",
                rule_name=f"{source_type}→{target_type}",
                applies_to=["source", "target"],
                metadata={"notes": rule.get("notes", "")}
            )
        
        return ExclusionDecision(excluded=False)
    
    def _check_database_specific(
        self,
        column_name: str,
        source_database: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check database-specific exclusions."""
        if not source_database:
            return ExclusionDecision(excluded=False)
        
        db_exclusions = self._config.get("database_specific_exclusions", {}).get(source_database.lower(), [])
        
        for exclusion in db_exclusions:
            # Check column name match (if specified)
            if "column_name" in exclusion:
                if exclusion["column_name"].upper() == column_name.upper():
                    rule_applies_to = exclusion.get("applies_to", ["source"])
                    if self._matches_applies_to(applies_to, rule_applies_to):
                        return ExclusionDecision(
                            excluded=True,
                            reason=exclusion.get("reason", f"{source_database} specific exclusion"),
                            rule_type="database_specific",
                            rule_name=f"{source_database}.{column_name}",
                            applies_to=rule_applies_to,
                        )
            
            # Check pattern match (if specified)
            if "pattern" in exclusion:
                try:
                    if re.match(exclusion["pattern"], column_name, re.IGNORECASE):
                        rule_applies_to = exclusion.get("applies_to", ["source"])
                        if self._matches_applies_to(applies_to, rule_applies_to):
                            return ExclusionDecision(
                                excluded=True,
                                reason=exclusion.get("reason", f"{source_database} pattern exclusion"),
                                rule_type="database_specific_pattern",
                                rule_name=exclusion["pattern"],
                                applies_to=rule_applies_to,
                            )
                except re.error:
                    pass  # Invalid regex — skip
        
        return ExclusionDecision(excluded=False)
    
    def _check_compliance(
        self,
        column_name: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check compliance-based exclusions (PII/PHI/PCI)."""
        compliance_config = self._config.get("compliance_exclusions", {})
        
        # Check each compliance category
        for category_name, category_config in compliance_config.items():
            if category_name == "description":
                continue
            
            if not category_config.get("enabled", False):
                continue
            
            patterns = category_config.get("patterns", [])
            for pattern in patterns:
                try:
                    if re.search(pattern, column_name, re.IGNORECASE):
                        rule_applies_to = category_config.get("applies_to", ["source", "target"])
                        if self._matches_applies_to(applies_to, rule_applies_to):
                            return ExclusionDecision(
                                excluded=True,
                                reason=category_config.get("reason", f"{category_name} compliance"),
                                rule_type="compliance",
                                rule_name=category_name,
                                applies_to=rule_applies_to,
                                metadata={"category": category_name}
                            )
                except re.error:
                    pass  # Invalid regex — skip
        
        return ExclusionDecision(excluded=False)
    
    def _check_patterns(
        self,
        column_name: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check pattern-based exclusions (regex)."""
        pattern_config = self._config.get("pattern_exclusions", {})
        patterns = pattern_config.get("patterns", [])
        
        for pattern_rule in patterns:
            pattern = pattern_rule.get("pattern", "")
            if not pattern:
                continue
            
            try:
                if re.match(pattern, column_name, re.IGNORECASE):
                    rule_applies_to = pattern_rule.get("applies_to", ["source", "target"])
                    if self._matches_applies_to(applies_to, rule_applies_to):
                        return ExclusionDecision(
                            excluded=True,
                            reason=pattern_rule.get("reason", "Pattern-based exclusion"),
                            rule_type="pattern",
                            rule_name=pattern,
                            applies_to=rule_applies_to,
                        )
            except re.error as exc:
                print(f"  [WARN] Invalid regex pattern '{pattern}': {exc}")
                continue
        
        return ExclusionDecision(excluded=False)
    
    def _check_global(
        self,
        column_name: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check global exclusions."""
        global_config = self._config.get("global_exclusions", {})
        columns = global_config.get("columns", [])
        
        for column_rule in columns:
            if column_rule.get("column_name", "").upper() == column_name.upper():
                rule_applies_to = column_rule.get("applies_to", ["source", "target"])
                if self._matches_applies_to(applies_to, rule_applies_to):
                    return ExclusionDecision(
                        excluded=True,
                        reason=column_rule.get("reason", "Global exclusion"),
                        rule_type="global",
                        rule_name=column_name,
                        applies_to=rule_applies_to,
                    )
        
        return ExclusionDecision(excluded=False)
    
    def _check_performance(
        self,
        source_type: str,
        target_type: str,
        applies_to: str,
    ) -> ExclusionDecision:
        """Check performance-based exclusions (lowest priority)."""
        perf_config = self._config.get("performance_exclusions", {})
        
        # Check large columns rule
        large_columns_config = perf_config.get("large_columns", {})
        if large_columns_config.get("enabled", False):
            # This would require actual column size analysis
            # For now, just a placeholder
            pass
        
        return ExclusionDecision(excluded=False)
    
    # ──────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ──────────────────────────────────────────────────────────────────────
    
    def _matches_applies_to(self, requested: str, rule: List[str]) -> bool:
        """Check if requested applies_to matches rule's applies_to."""
        if requested == "both":
            return True
        if requested == "source" and "source" in rule:
            return True
        if requested == "target" and "target" in rule:
            return True
        return False
    
    # ──────────────────────────────────────────────────────────────────────
    # Public API — Reporting
    # ──────────────────────────────────────────────────────────────────────
    
    def get_excluded_columns(
        self,
        source_table: str = "",
        source_database: str = "",
    ) -> List[Tuple[str, str]]:
        """
        Get list of all excluded columns for a table.
        
        Returns:
            List of (column_name, reason) tuples
        """
        excluded = []
        
        # Get table-specific exclusions
        table_exclusions = self._config.get("table_specific_exclusions", {})
        table_config = None
        
        for table_key, config in table_exclusions.items():
            if table_key == "description":
                continue
            if table_key.upper() == source_table.upper():
                table_config = config
                break
        
        if table_config:
            for exclusion in table_config.get("exclusions", []):
                column_name = exclusion.get("column_name", "")
                reason = exclusion.get("reason", "No reason provided")
                excluded.append((column_name, reason))
        
        return excluded
    
    def clear_cache(self):
        """Clear the decision cache (useful after config reload)."""
        with self._lock:
            self._cache.clear()
    
    def reload_config(self) -> bool:
        """Reload the exclusions configuration from disk."""
        self.clear_cache()
        return self._load_config()
    
    def export_decisions(self, output_path: Path) -> bool:
        """Export all cached decisions to JSON for auditing."""
        import json
        
        try:
            decisions = {}
            with self._lock:
                for key, decision in self._cache.items():
                    decisions[key] = {
                        "excluded": decision.excluded,
                        "reason": decision.reason,
                        "rule_type": decision.rule_type,
                        "rule_name": decision.rule_name,
                        "applies_to": decision.applies_to,
                        "metadata": decision.metadata,
                    }
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(decisions, f, indent=2)
            
            return True
        except Exception as exc:
            print(f"  [ERROR] Failed to export decisions: {exc}")
            return False


# ── Module-level singleton ────────────────────────────────────────────────

exclusion_manager = ExclusionManager()
