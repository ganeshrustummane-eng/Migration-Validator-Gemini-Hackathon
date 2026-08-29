"""
Enhanced AI Rule Mapper with Bi-Directional Exclusion Support
===============================================================
Extension of the original AIRuleMapper that integrates bi-directional
exclusion handling to properly report target-only columns.

Key Enhancements:
  1. Analyzes BOTH source and target schemas comprehensively
  2. Identifies and reports target-only columns (migration-added)
  3. Provides accurate coverage metrics (source vs target)
  4. Generates enhanced validation plans with exclusion details
  5. Integrates with BiDirectionalExclusionHandler

Usage:
    from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper
    
    mapper = EnhancedAIRuleMapper(model="gpt-4o")
    result = mapper.map_columns_with_coverage(
        source_columns=pg_columns,
        target_columns=sf_columns,
        table_name="customers",
    )
    
    print(f"Source coverage: {result.coverage.source_coverage_pct:.1f}%")
    print(f"Target coverage: {result.coverage.target_coverage_pct:.1f}%")
    print(f"Target-only columns: {len(result.coverage.target_only_columns)}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sql_extractor.extractors import ColumnMetadata
from ai_transformation.column_mapping import ColumnRuleMapping
from ai_transformation.ai_rule_mapper import AIRuleMapper, AIRuleMappingError
from exclusions.bidirectional_exclusion_handler import (
    BiDirectionalExclusionHandler,
    BiDirectionalAnalysisResult,
)


# ───────────────────────────────────────────────────────────────────────────
# Enhanced Result with Coverage
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class EnhancedMappingResult:
    """
    Result of enhanced column mapping with bi-directional coverage analysis.
    
    Attributes:
        mappings: List of ColumnRuleMapping (as before)
        explanation: AI reasoning explanation (as before)
        coverage: BiDirectionalAnalysisResult with detailed coverage metrics
        warnings: List of warning messages for low coverage, drift, etc.
        recommendations: List of recommended actions
    """
    mappings: List[ColumnRuleMapping]
    explanation: str
    coverage: BiDirectionalAnalysisResult
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    
    @property
    def has_low_coverage(self) -> bool:
        """True if overall coverage is below 80%."""
        return self.coverage.overall_coverage_pct < 80.0
    
    @property
    def has_target_schema_drift(self) -> bool:
        """True if target has columns not in source."""
        return self.coverage.has_target_only_columns
    
    @property
    def has_source_schema_drift(self) -> bool:
        """True if source has columns not in target."""
        return self.coverage.has_source_only_columns
    
    def summary_report(self) -> str:
        """Generate comprehensive human-readable report."""
        lines = [
            "=" * 80,
            "ENHANCED COLUMN MAPPING REPORT",
            "=" * 80,
            "",
            f"Table: {self.coverage.table_name}",
            "",
            "Coverage Metrics:",
            f"  • Source columns:     {self.coverage.source_column_count}",
            f"  • Target columns:     {self.coverage.target_column_count}",
            f"  • Matched & validated: {len(self.mappings)} pairs",
            f"  • Source coverage:    {self.coverage.source_coverage_pct:.1f}%",
            f"  • Target coverage:    {self.coverage.target_coverage_pct:.1f}%",
            "",
        ]
        
        # Status badge
        if self.has_low_coverage:
            lines.append("⚠️  STATUS: LOW COVERAGE — Validation results may be incomplete")
        elif self.has_target_schema_drift or self.has_source_schema_drift:
            lines.append("⚠️  STATUS: SCHEMA DRIFT DETECTED — Review exclusions")
        else:
            lines.append("✅ STATUS: FULL COVERAGE — All columns validated")
        lines.append("")
        
        # Warnings
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  ⚠️  {warning}")
            lines.append("")
        
        # Target-only columns (most important)
        if self.coverage.target_only_columns:
            lines.append(f"Target-Only Columns ({len(self.coverage.target_only_columns)}):")
            lines.append("  (These columns exist in target but not in source)")
            for col_info in self.coverage.target_only_columns:
                lines.append(f"  • {col_info.column_name} ({col_info.data_type})")
                lines.append(f"    Category: {col_info.category}")
                lines.append(f"    Reason: {col_info.reason}")
            lines.append("")
        
        # Source-only columns
        if self.coverage.source_only_columns:
            lines.append(f"Source-Only Columns ({len(self.coverage.source_only_columns)}):")
            lines.append("  (These columns exist in source but not in target)")
            for col_info in self.coverage.source_only_columns:
                lines.append(f"  • {col_info.column_name} ({col_info.data_type})")
                lines.append(f"    Reason: {col_info.reason}")
            lines.append("")
        
        # Excluded matched columns
        if self.coverage.excluded_matched_columns:
            lines.append(f"Excluded Matched Columns ({len(self.coverage.excluded_matched_columns)}):")
            for col_info in self.coverage.excluded_matched_columns:
                lines.append(f"  • {col_info.column_name} ({col_info.data_type})")
                lines.append(f"    Reason: {col_info.reason}")
            lines.append("")
        
        # Recommendations
        if self.recommendations:
            lines.append("Recommendations:")
            for rec in self.recommendations:
                lines.append(f"  💡 {rec}")
            lines.append("")
        
        lines.append("=" * 80)
        return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────────────
# Enhanced AI Rule Mapper
# ───────────────────────────────────────────────────────────────────────────

class EnhancedAIRuleMapper(AIRuleMapper):
    """
    Enhanced version of AIRuleMapper with bi-directional exclusion support.
    
    Provides comprehensive coverage analysis and handles target-only columns
    that are common in migration scenarios.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(api_key, api_base, api_version, model)
        self.bidirectional_handler = BiDirectionalExclusionHandler()
    
    def map_columns_with_coverage(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        primary_key_hints: Optional[List[str]] = None,
        table_name: str = "unknown",
        source_database: str = "postgresql",
    ) -> EnhancedMappingResult:
        """
        Enhanced column mapping with bi-directional coverage analysis.
        
        This is the RECOMMENDED method to use instead of the base map_columns().
        
        Args:
            source_columns: Source database column metadata
            target_columns: Target database column metadata
            primary_key_hints: Optional PK column names
            table_name: Table name for context and reporting
            source_database: Source database type (default: postgresql)
        
        Returns:
            EnhancedMappingResult with mappings, coverage, warnings, and recommendations
        
        Raises:
            AIRuleMappingError: If AI mapping fails
        """
        # ── Step 1: Perform Bi-Directional Schema Analysis ─────────────────
        print(f"  [EnhancedAIRuleMapper] Analyzing schemas for '{table_name}'...")
        coverage = self.bidirectional_handler.analyze_schemas(
            source_columns=source_columns,
            target_columns=target_columns,
            table_name=table_name,
            source_database=source_database,
        )
        
        # ── Step 2: Call Base AI Mapper for Column Mapping ─────────────────
        print(f"  [EnhancedAIRuleMapper] Running AI mapping (model: {self.model})...")
        mappings, explanation = self.map_columns(
            source_columns=source_columns,
            target_columns=target_columns,
            primary_key_hints=primary_key_hints,
            table_name=table_name,
        )
        
        # ── Step 3: Generate Warnings and Recommendations ──────────────────
        warnings = self._generate_warnings(coverage, mappings)
        recommendations = self._generate_recommendations(coverage, mappings)
        
        # ── Step 4: Build Enhanced Result ───────────────────────────────────
        result = EnhancedMappingResult(
            mappings=mappings,
            explanation=explanation,
            coverage=coverage,
            warnings=warnings,
            recommendations=recommendations,
        )
        
        # ── Step 5: Print Summary ───────────────────────────────────────────
        print(f"  [EnhancedAIRuleMapper] ✓ Mapping complete:")
        print(f"    • {len(mappings)} column pairs mapped")
        print(f"    • Source coverage: {coverage.source_coverage_pct:.1f}%")
        print(f"    • Target coverage: {coverage.target_coverage_pct:.1f}%")
        
        if coverage.target_only_columns:
            print(f"    ⚠️  {len(coverage.target_only_columns)} target-only columns detected")
        
        if result.has_low_coverage:
            print(f"    ⚠️  LOW COVERAGE WARNING: Overall coverage {coverage.overall_coverage_pct:.1f}%")
        
        return result
    
    def _generate_warnings(
        self,
        coverage: BiDirectionalAnalysisResult,
        mappings: List[ColumnRuleMapping],
    ) -> List[str]:
        """Generate warnings based on coverage analysis."""
        warnings = []
        
        # Low coverage warning
        if coverage.overall_coverage_pct < 80.0:
            warnings.append(
                f"Low coverage ({coverage.overall_coverage_pct:.1f}%) — "
                f"Only {len(mappings)} of {coverage.total_columns} total columns validated"
            )
        
        # Target-only columns warning
        if coverage.has_target_only_columns:
            warnings.append(
                f"{len(coverage.target_only_columns)} columns exist in target but not in source — "
                f"possible migration enrichment or schema drift"
            )
        
        # Source-only columns warning
        if coverage.has_source_only_columns:
            warnings.append(
                f"{len(coverage.source_only_columns)} columns exist in source but not in target — "
                f"possible dropped columns or incomplete migration"
            )
        
        # High exclusion rate
        total_excluded = (
            len(coverage.source_only_columns)
            + len(coverage.target_only_columns)
            + len(coverage.excluded_matched_columns)
        )
        exclusion_rate = (total_excluded / coverage.total_columns * 100) if coverage.total_columns > 0 else 0
        if exclusion_rate > 50:
            warnings.append(
                f"High exclusion rate ({exclusion_rate:.1f}%) — "
                f"{total_excluded} of {coverage.total_columns} columns excluded"
            )
        
        return warnings
    
    def _generate_recommendations(
        self,
        coverage: BiDirectionalAnalysisResult,
        mappings: List[ColumnRuleMapping],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        # Recommend documenting target-only columns
        if coverage.has_target_only_columns:
            audit_cols = [c for c in coverage.target_only_columns if c.is_audit_column]
            enrichment_cols = [c for c in coverage.target_only_columns if c.is_enrichment]
            
            if audit_cols:
                recommendations.append(
                    f"Document {len(audit_cols)} audit columns added during migration in your migration spec"
                )
            
            if enrichment_cols:
                recommendations.append(
                    f"Document {len(enrichment_cols)} enrichment columns in your data transformation guide"
                )
            
            # Generate exclusion config
            recommendations.append(
                "Run: python validate_cli.py generate-exclusions --table "
                f"{coverage.table_name} to auto-generate exclusion config"
            )
        
        # Recommend investigating source-only columns
        if coverage.has_source_only_columns:
            recommendations.append(
                f"Review {len(coverage.source_only_columns)} source-only columns — "
                "verify they should not be in target"
            )
        
        # Recommend increasing coverage if low
        if coverage.overall_coverage_pct < 80.0:
            recommendations.append(
                "Increase coverage to at least 80% by mapping or excluding unmapped columns"
            )
        
        return recommendations
    
    def export_coverage_report(
        self,
        result: EnhancedMappingResult,
        output_path: str = "output/coverage_report.txt",
    ) -> bool:
        """
        Export coverage report to file.
        
        Args:
            result: EnhancedMappingResult to export
            output_path: Output file path
        
        Returns:
            True if export successful
        """
        try:
            from pathlib import Path
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result.summary_report())
            
            print(f"  [EnhancedAIRuleMapper] ✓ Coverage report exported: {output_path}")
            return True
        except Exception as e:
            print(f"  [EnhancedAIRuleMapper] ✗ Failed to export report: {e}")
            return False
