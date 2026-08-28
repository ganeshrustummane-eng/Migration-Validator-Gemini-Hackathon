"""
Bi-Directional Exclusion Handler
==================================
Handles column exclusions in BOTH directions: source → target AND target → source.

Problem Solved:
  When target tables have EXTRA columns not present in source (common in migrations):
    - Audit columns (created_by, modified_by, migrated_at)
    - Enrichment columns (data_quality_score, segment_type)
    - Transformation columns (full_name derived from first_name + last_name)
    - Metadata columns (_FIVETRAN_SYNCED, _UPDATED_BY_ETL)
  
  These columns should be:
    1. Identified and reported in coverage metrics
    2. Excluded from validation with documented reasons
    3. Tracked separately from source exclusions
    4. Included in exclusion reports

Architecture:
  ┌───────────────────────────────────────────────────────────────┐
  │ Source Schema (PostgreSQL)    Target Schema (Snowflake)       │
  │   - customer_id                  - CUSTOMER_ID                │
  │   - name                         - NAME                       │
  │   - email                        - EMAIL                      │
  │   - created_at                   - CREATED_AT                 │
  │                                  - _FIVETRAN_SYNCED  ← EXTRA  │
  │                                  - MIGRATED_BY       ← EXTRA  │
  │                                  - DATA_QUALITY_SCORE← EXTRA  │
  └───────────────────────────────────────────────────────────────┘
                    ↓
  ┌───────────────────────────────────────────────────────────────┐
  │ BiDirectionalExclusionHandler                                 │
  │  • Analyze source columns                                     │
  │  • Analyze target columns                                     │
  │  • Find columns only in source → mark as "no_target_match"    │
  │  • Find columns only in target → mark as "target_only"        │
  │  • Apply exclusion rules in BOTH directions                   │
  └───────────────────────────────────────────────────────────────┘
                    ↓
  ┌───────────────────────────────────────────────────────────────┐
  │ Enhanced Coverage Report                                      │
  │  Source: 4 columns → 4 validated (100% source coverage)       │
  │  Target: 7 columns → 4 validated (57% target coverage)        │
  │  Overall: 4 of 11 total columns validated (36%)               │
  │                                                                │
  │  Target-only columns (3):                                     │
  │    - _FIVETRAN_SYNCED: Fivetran metadata (auto-excluded)      │
  │    - MIGRATED_BY: Migration audit column (target enrichment)  │
  │    - DATA_QUALITY_SCORE: Post-migration enrichment            │
  └───────────────────────────────────────────────────────────────┘

Usage:
    from exclusions.bidirectional_exclusion_handler import BiDirectionalExclusionHandler
    
    handler = BiDirectionalExclusionHandler(exclusion_manager)
    result = handler.analyze_schemas(
        source_columns=pg_columns,
        target_columns=sf_columns,
        table_name="customers"
    )
    
    print(f"Source coverage: {result.source_coverage_pct:.1f}%")
    print(f"Target coverage: {result.target_coverage_pct:.1f}%")
    print(f"Target-only columns: {result.target_only_columns}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sql_extractor.extractors import ColumnMetadata
from exclusions.exclusion_manager import ExclusionManager, ExclusionDecision


# ───────────────────────────────────────────────────────────────────────────
# Output Data Structures
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class ColumnExclusionInfo:
    """Detailed exclusion information for a single column."""
    column_name: str
    data_type: str
    excluded: bool
    reason: str = ""
    direction: str = "both"  # "source", "target", "both", "source_only", "target_only"
    rule_type: str = ""      # from ExclusionDecision
    is_fivetran_metadata: bool = False
    is_audit_column: bool = False
    is_enrichment: bool = False
    is_derived: bool = False
    
    @property
    def category(self) -> str:
        """Human-readable category for reporting."""
        if self.is_fivetran_metadata:
            return "Fivetran Metadata"
        if self.is_audit_column:
            return "Audit Column"
        if self.is_enrichment:
            return "Data Enrichment"
        if self.is_derived:
            return "Derived/Computed"
        if self.direction == "target_only":
            return "Target-Only Column"
        if self.direction == "source_only":
            return "Source-Only Column"
        return "Excluded"


@dataclass
class BiDirectionalAnalysisResult:
    """Result of bi-directional schema analysis."""
    table_name: str
    
    # Column counts
    source_column_count: int = 0
    target_column_count: int = 0
    total_columns: int = 0
    
    # Matched columns
    matched_columns: List[Tuple[str, str]] = field(default_factory=list)  # (source_col, target_col)
    
    # Source-only columns (exist in source but not in target)
    source_only_columns: List[ColumnExclusionInfo] = field(default_factory=list)
    
    # Target-only columns (exist in target but not in source)
    target_only_columns: List[ColumnExclusionInfo] = field(default_factory=list)
    
    # Excluded matched columns (exist in both but excluded for other reasons)
    excluded_matched_columns: List[ColumnExclusionInfo] = field(default_factory=list)
    
    # Coverage metrics
    source_validated_count: int = 0
    target_validated_count: int = 0
    
    @property
    def source_coverage_pct(self) -> float:
        """Percentage of source columns validated."""
        if self.source_column_count == 0:
            return 0.0
        return (self.source_validated_count / self.source_column_count) * 100
    
    @property
    def target_coverage_pct(self) -> float:
        """Percentage of target columns validated."""
        if self.target_column_count == 0:
            return 0.0
        return (self.target_validated_count / self.target_column_count) * 100
    
    @property
    def overall_coverage_pct(self) -> float:
        """Percentage of total unique columns validated."""
        if self.total_columns == 0:
            return 0.0
        return (len(self.matched_columns) / self.total_columns) * 100
    
    @property
    def has_target_only_columns(self) -> bool:
        """True if target has columns not present in source."""
        return len(self.target_only_columns) > 0
    
    @property
    def has_source_only_columns(self) -> bool:
        """True if source has columns not present in target."""
        return len(self.source_only_columns) > 0
    
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Bi-Directional Schema Analysis: {self.table_name}",
            f"{'=' * 70}",
            f"",
            f"Column Counts:",
            f"  Source columns:  {self.source_column_count}",
            f"  Target columns:  {self.target_column_count}",
            f"  Total unique:    {self.total_columns}",
            f"  Matched pairs:   {len(self.matched_columns)}",
            f"",
            f"Coverage:",
            f"  Source validated:  {self.source_validated_count}/{self.source_column_count} ({self.source_coverage_pct:.1f}%)",
            f"  Target validated:  {self.target_validated_count}/{self.target_column_count} ({self.target_coverage_pct:.1f}%)",
            f"  Overall:           {len(self.matched_columns)}/{self.total_columns} ({self.overall_coverage_pct:.1f}%)",
            f"",
        ]
        
        if self.target_only_columns:
            lines.append(f"Target-Only Columns ({len(self.target_only_columns)}):")
            for col in self.target_only_columns:
                lines.append(f"  • {col.column_name} ({col.data_type})")
                lines.append(f"    Reason: {col.reason}")
                lines.append(f"    Category: {col.category}")
            lines.append("")
        
        if self.source_only_columns:
            lines.append(f"Source-Only Columns ({len(self.source_only_columns)}):")
            for col in self.source_only_columns:
                lines.append(f"  • {col.column_name} ({col.data_type})")
                lines.append(f"    Reason: {col.reason}")
            lines.append("")
        
        if self.excluded_matched_columns:
            lines.append(f"Excluded Matched Columns ({len(self.excluded_matched_columns)}):")
            for col in self.excluded_matched_columns:
                lines.append(f"  • {col.column_name} ({col.data_type})")
                lines.append(f"    Reason: {col.reason}")
            lines.append("")
        
        return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────────────
# Target-Only Column Patterns
# ───────────────────────────────────────────────────────────────────────────

# Common patterns for target-only columns (columns added during migration)
_TARGET_ONLY_PATTERNS = {
    # Audit/tracking columns
    "audit": [
        r"^(created|modified|updated|deleted|inserted)_(by|at|date|time|timestamp)$",
        r"^(created|modified|updated)_user$",
        r"^migrated_(at|by|date|timestamp)$",
        r"^etl_(loaded|updated|created|date|timestamp)$",
        r"^last_(modified|updated|changed)_(by|at|date)$",
        r"^audit_",
    ],
    
    # Data quality/enrichment columns
    "enrichment": [
        r"^(data_)?quality_score$",
        r"^confidence_score$",
        r"^enrichment_",
        r"^derived_",
        r"^computed_",
        r"^segment_type$",
        r"^classification$",
        r"^category_derived$",
    ],
    
    # Metadata columns (beyond Fivetran)
    "metadata": [
        r"^_UPDATED_BY_",
        r"^_LOADED_AT$",
        r"^_SOURCE_SYSTEM$",
        r"^_BATCH_ID$",
        r"^_PIPELINE_",
        r"^_HASH$",
        r"^_CHECKSUM$",
    ],
    
    # Derived/transformation columns
    "derived": [
        r"^full_name$",  # derived from first_name + last_name
        r"^display_name$",
        r"^calculated_",
        r"^aggregated_",
        r"^rollup_",
    ],
}


# ───────────────────────────────────────────────────────────────────────────
# Bi-Directional Exclusion Handler
# ───────────────────────────────────────────────────────────────────────────

class BiDirectionalExclusionHandler:
    """
    Analyzes schemas in BOTH directions to handle:
      1. Source columns without target match
      2. Target columns without source match (MAIN FOCUS)
      3. Matched columns that should be excluded
    
    This ensures accurate coverage reporting and proper handling of
    migration-added columns.
    """
    
    def __init__(self, exclusion_manager: Optional[ExclusionManager] = None):
        """
        Args:
            exclusion_manager: Optional ExclusionManager instance.
                              If None, creates a new one.
        """
        from exclusions.exclusion_manager import exclusion_manager as default_manager
        self.exclusion_manager = exclusion_manager or default_manager
    
    def analyze_schemas(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        table_name: str = "unknown",
        source_database: str = "postgresql",
    ) -> BiDirectionalAnalysisResult:
        """
        Perform bi-directional schema analysis.
        
        Args:
            source_columns: Source database column metadata
            target_columns: Target database column metadata
            table_name: Table name for reporting
            source_database: Source database type (for exclusion rules)
        
        Returns:
            BiDirectionalAnalysisResult with complete analysis
        """
        result = BiDirectionalAnalysisResult(
            table_name=table_name,
            source_column_count=len(source_columns),
            target_column_count=len(target_columns),
        )
        
        # Build column name mappings (case-insensitive)
        source_map = {col.column_name.upper(): col for col in source_columns}
        target_map = {col.column_name.upper(): col for col in target_columns}
        
        source_names = set(source_map.keys())
        target_names = set(target_map.keys())
        
        # Find matched, source-only, and target-only columns
        matched_names = source_names & target_names
        source_only_names = source_names - target_names
        target_only_names = target_names - source_names
        
        result.total_columns = len(source_names | target_names)
        
        # ── Process Matched Columns ────────────────────────────────────────
        for name_upper in matched_names:
            src_col = source_map[name_upper]
            tgt_col = target_map[name_upper]
            
            # Check if this matched column should be excluded
            decision = self.exclusion_manager.should_exclude(
                column_name=src_col.column_name,
                source_table=table_name,
                source_type=src_col.data_type,
                target_type=tgt_col.data_type,
                source_database=source_database,
                applies_to="both",
            )
            
            if decision.excluded:
                result.excluded_matched_columns.append(ColumnExclusionInfo(
                    column_name=src_col.column_name,
                    data_type=f"{src_col.data_type} → {tgt_col.data_type}",
                    excluded=True,
                    reason=decision.reason,
                    direction="both",
                    rule_type=decision.rule_type,
                ))
            else:
                result.matched_columns.append((src_col.column_name, tgt_col.column_name))
                result.source_validated_count += 1
                result.target_validated_count += 1
        
        # ── Process Source-Only Columns ─────────────────────────────────────
        for name_upper in source_only_names:
            src_col = source_map[name_upper]
            result.source_only_columns.append(ColumnExclusionInfo(
                column_name=src_col.column_name,
                data_type=src_col.data_type,
                excluded=True,
                reason="Column exists in source but not in target — possible schema drift or dropped column",
                direction="source_only",
            ))
        
        # ── Process Target-Only Columns (MAIN FOCUS) ────────────────────────
        for name_upper in target_only_names:
            tgt_col = target_map[name_upper]
            excl_info = self._analyze_target_only_column(tgt_col, table_name)
            result.target_only_columns.append(excl_info)
        
        return result
    
    def _analyze_target_only_column(
        self,
        col: ColumnMetadata,
        table_name: str,
    ) -> ColumnExclusionInfo:
        """
        Analyze a target-only column and determine why it exists.
        
        Args:
            col: Target column metadata
            table_name: Table name for context
        
        Returns:
            ColumnExclusionInfo with categorization and reason
        """
        col_name_upper = col.column_name.upper()
        
        # ── Check Fivetran Metadata ─────────────────────────────────────────
        if col_name_upper.startswith("_FIVETRAN_"):
            return ColumnExclusionInfo(
                column_name=col.column_name,
                data_type=col.data_type,
                excluded=True,
                reason="Fivetran metadata column — not present in source",
                direction="target_only",
                is_fivetran_metadata=True,
            )
        
        # ── Check Audit Column Patterns ─────────────────────────────────────
        for pattern in _TARGET_ONLY_PATTERNS["audit"]:
            if re.match(pattern, col.column_name, re.IGNORECASE):
                return ColumnExclusionInfo(
                    column_name=col.column_name,
                    data_type=col.data_type,
                    excluded=True,
                    reason="Audit/tracking column added during migration (e.g., created_by, migrated_at)",
                    direction="target_only",
                    is_audit_column=True,
                )
        
        # ── Check Enrichment Column Patterns ────────────────────────────────
        for pattern in _TARGET_ONLY_PATTERNS["enrichment"]:
            if re.match(pattern, col.column_name, re.IGNORECASE):
                return ColumnExclusionInfo(
                    column_name=col.column_name,
                    data_type=col.data_type,
                    excluded=True,
                    reason="Data enrichment column added post-migration (e.g., quality_score, segment_type)",
                    direction="target_only",
                    is_enrichment=True,
                )
        
        # ── Check Metadata Column Patterns ──────────────────────────────────
        for pattern in _TARGET_ONLY_PATTERNS["metadata"]:
            if re.match(pattern, col.column_name, re.IGNORECASE):
                return ColumnExclusionInfo(
                    column_name=col.column_name,
                    data_type=col.data_type,
                    excluded=True,
                    reason="ETL/pipeline metadata column (e.g., _BATCH_ID, _LOADED_AT)",
                    direction="target_only",
                    is_fivetran_metadata=True,
                )
        
        # ── Check Derived Column Patterns ───────────────────────────────────
        for pattern in _TARGET_ONLY_PATTERNS["derived"]:
            if re.match(pattern, col.column_name, re.IGNORECASE):
                return ColumnExclusionInfo(
                    column_name=col.column_name,
                    data_type=col.data_type,
                    excluded=True,
                    reason="Derived/computed column (e.g., full_name from first_name+last_name)",
                    direction="target_only",
                    is_derived=True,
                )
        
        # ── Default: Unknown Target-Only Column ─────────────────────────────
        return ColumnExclusionInfo(
            column_name=col.column_name,
            data_type=col.data_type,
            excluded=True,
            reason=f"Column exists in target but not in source — added during migration or transformation",
            direction="target_only",
        )
    
    def generate_exclusion_config(
        self,
        result: BiDirectionalAnalysisResult,
        source_database: str = "postgresql",
        target_database: str = "snowflake",
    ) -> str:
        """
        Generate YAML exclusion config for target-only columns.
        
        Args:
            result: Analysis result from analyze_schemas()
            source_database: Source database type
            target_database: Target database type
        
        Returns:
            YAML string for config/exclusions.yaml
        """
        lines = [
            f"# Auto-generated exclusions for {result.table_name}",
            f"# Generated by BiDirectionalExclusionHandler",
            f"#",
            f"# Source coverage: {result.source_coverage_pct:.1f}%",
            f"# Target coverage: {result.target_coverage_pct:.1f}%",
            f"",
            f"{result.table_name}:",
            f"  source_database: {source_database}",
            f"  target_database: {target_database}",
            f"  exclusions:",
        ]
        
        # Add target-only columns
        for col_info in result.target_only_columns:
            lines.extend([
                f"    - column_name: {col_info.column_name}",
                f"      reason: \"{col_info.reason}\"",
                f"      applies_to: [\"target\"]",
                f"      category: \"{col_info.category}\"",
                f"",
            ])
        
        # Add source-only columns
        for col_info in result.source_only_columns:
            lines.extend([
                f"    - column_name: {col_info.column_name}",
                f"      reason: \"{col_info.reason}\"",
                f"      applies_to: [\"source\"]",
                f"",
            ])
        
        return "\n".join(lines)
