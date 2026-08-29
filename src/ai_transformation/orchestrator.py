"""
Rule Mapper Orchestrator
=========================
Single entry point the pipeline uses for column mapping.

AI-only
-------
This orchestrator previously fell back to StaticRuleMapper when DIAL_API_KEY
was missing or the API failed. That fallback was removed. A mapping produced
by type-pair guessing is indistinguishable downstream from a reviewed one, so
the fallback quietly converted "we could not determine this" into "validated".
Mapping now fails loudly instead.

Bi-Directional Exclusion Support (Enhanced)
--------------------------------------------
Now supports comprehensive bi-directional schema analysis to handle:
  - Target-only columns (common in migrations)
  - Source-only columns (potential data loss)
  - Accurate coverage reporting (source vs target vs overall)

Enable enhanced mode by setting use_enhanced=True in constructor.
This provides detailed coverage metrics and warnings.

Model Selection
---------------
  1. orchestrator = RuleMapperOrchestrator(model="gpt-4o-mini")
  2. DIAL_MODEL environment variable
  3. Interactive CLI selection (validate_cli.py)

Usage:
    from ai_transformation import RuleMapperOrchestrator

    # Basic usage (backward compatible)
    orchestrator = RuleMapperOrchestrator(model="gpt-4o")
    mappings, explanation = orchestrator.map_columns(
        source_columns=pg_columns,
        target_columns=sf_columns,
        table_name="events",
    )

    # Enhanced usage (with coverage analysis)
    orchestrator = RuleMapperOrchestrator(model="gpt-4o", use_enhanced=True)
    result = orchestrator.map_columns_with_coverage(
        source_columns=pg_columns,
        target_columns=sf_columns,
        table_name="events",
    )
    print(f"Target coverage: {result.coverage.target_coverage_pct:.1f}%")
"""

from typing import List, Optional, Tuple, Union

from sql_extractor.extractors import ColumnMetadata
from ai_transformation.column_mapping import ColumnRuleMapping
from ai_transformation.ai_rule_mapper import (
    AIRuleMapper,
    AIRuleMappingError,
    AVAILABLE_MODELS,
)


class RuleMapperOrchestrator:
    """
    Facade over AIRuleMapper with optional bi-directional exclusion support.

    Backward compatible by default. Set use_enhanced=True for advanced features.
    """

    def __init__(self, model: Optional[str] = None, use_enhanced: bool = False):
        """
        Args:
            model: AI model name to use (e.g. 'gpt-4o', 'gpt-4o-mini').
                   Defaults to DIAL_MODEL env var, then 'gpt-4o'.
            use_enhanced: If True, use EnhancedAIRuleMapper with bi-directional
                         coverage analysis. Default False for backward compatibility.
        """
        self._use_enhanced = use_enhanced

        if use_enhanced:
            # Import enhanced mapper only when needed
            from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper
            self._ai_mapper = EnhancedAIRuleMapper(model=model)
            print(f"  [Orchestrator] Using ENHANCED AI mapper with bi-directional coverage analysis")
        else:
            self._ai_mapper = AIRuleMapper(model=model)

    def set_model(self, model: str):
        """
        Switch the AI model at runtime.

        When Claude backend is active, model must be a Claude model name.
        set_model() re-reads env vars to preserve the correct backend.
        """
        import os
        from ai_transformation.ai_rule_mapper import _is_claude_model

        dial_key   = os.getenv("DIAL_API_KEY", "")
        claude_key = os.getenv("CLAUDE_API_KEY", "")

        # Guard: if Claude backend active but a non-Claude model was selected,
        # update CLAUDE_MODEL env var so AIRuleMapper picks the right model.
        if claude_key and not dial_key:
            if not _is_claude_model(model):
                print(
                    f"  [Orchestrator] ⚠️  '{model}' is not a Claude direct model name. "
                    f"Keeping current Claude model: {self._ai_mapper.model}"
                )
                return
            # Set env var so the new AIRuleMapper picks it up
            os.environ["CLAUDE_MODEL"] = model
        else:
            os.environ["DIAL_MODEL"] = model

        # Recreate mapper — it auto-detects backend from env vars
        if self._use_enhanced:
            from ai_transformation.ai_rule_mapper_enhanced import EnhancedAIRuleMapper
            self._ai_mapper = EnhancedAIRuleMapper(model=model)
        else:
            self._ai_mapper = AIRuleMapper(model=model)

        print(f"  [Orchestrator] AI model set to '{self._ai_mapper.model}' [{self._ai_mapper._backend}].")

    @property
    def active_model(self) -> str:
        """Return the name of the currently active AI model."""
        return self._ai_mapper.model

    @property
    def is_ai_active(self) -> bool:
        """Return True if AI mapping is configured and available."""
        return self._ai_mapper._ai_active

    def map_columns(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        primary_key_hints: Optional[List[str]] = None,
        table_name: str = "unknown",
        source_database: str = "postgresql",
    ) -> Tuple[List[ColumnRuleMapping], str]:
        """
        Map source → target columns and assign validation rules.

        Args:
            source_columns    : Source column metadata
            target_columns    : Snowflake column metadata
            primary_key_hints : Known PK column names (informational)
            table_name        : Table name for logging and AI context
            source_database   : Source database type (default: postgresql)

        Returns:
            Tuple of (mappings, explanation).

        Raises:
            AIRuleMappingError: AI is unconfigured or the call failed.
        """
        if not self._ai_mapper._ai_active:
            raise AIRuleMappingError(
                f"No AI API key configured — cannot map columns for '{table_name}'.\n"
                "  Set one of the following in .env:\n"
                "    DIAL_API_KEY=...    (EPAM DIAL — access to GPT/Claude/Gemini)\n"
                "    CLAUDE_API_KEY=...  (Anthropic direct — no VPN needed)\n"
                "  Or run: python validate_cli.py  →  choose [8] Configure API key"
            )

        print(
            f"  [Orchestrator] Using AI mapper "
            f"(model: '{self._ai_mapper.model}') for '{table_name}'."
        )

        # If using enhanced mapper, log coverage warnings
        if self._use_enhanced:
            result = self._ai_mapper.map_columns_with_coverage(
                source_columns=source_columns,
                target_columns=target_columns,
                primary_key_hints=primary_key_hints,
                table_name=table_name,
                source_database=source_database,
            )

            # Log coverage warnings
            if result.warnings:
                print(f"  [Orchestrator] Coverage warnings for '{table_name}':")
                for warning in result.warnings:
                    print(f"    ⚠️  {warning}")

            # Log target-only columns if present
            if result.coverage.target_only_columns:
                print(f"  [Orchestrator] ⚠️  {len(result.coverage.target_only_columns)} "
                      f"target-only columns detected")
                for col_info in result.coverage.target_only_columns[:3]:  # Show first 3
                    print(f"    • {col_info.column_name}: {col_info.category}")
                if len(result.coverage.target_only_columns) > 3:
                    print(f"    ... and {len(result.coverage.target_only_columns) - 3} more")

            # Return backward-compatible tuple
            return result.mappings, result.explanation
        else:
            # Standard mapping (backward compatible)
            return self._ai_mapper.map_columns(
                source_columns, target_columns, primary_key_hints, table_name
            )

    def map_columns_with_coverage(
        self,
        source_columns: List[ColumnMetadata],
        target_columns: List[ColumnMetadata],
        primary_key_hints: Optional[List[str]] = None,
        table_name: str = "unknown",
        source_database: str = "postgresql",
    ):
        """
        Map columns with comprehensive bi-directional coverage analysis.

        This method is only available when use_enhanced=True was set in constructor.

        Args:
            source_columns    : Source column metadata
            target_columns    : Snowflake column metadata
            primary_key_hints : Known PK column names (informational)
            table_name        : Table name for logging and AI context
            source_database   : Source database type (default: postgresql)

        Returns:
            EnhancedMappingResult with mappings, explanation, coverage, warnings, etc.

        Raises:
            RuntimeError: If use_enhanced was not set to True
            AIRuleMappingError: If AI is unconfigured or the call failed
        """
        if not self._use_enhanced:
            raise RuntimeError(
                "map_columns_with_coverage() requires use_enhanced=True in constructor. "
                "Create orchestrator with: RuleMapperOrchestrator(use_enhanced=True)"
            )

        if not self._ai_mapper._ai_active:
            raise AIRuleMappingError(
                f"DIAL_API_KEY is not set — cannot map columns for '{table_name}'. "
                "Set DIAL_API_KEY in .env and re-run."
            )

        return self._ai_mapper.map_columns_with_coverage(
            source_columns=source_columns,
            target_columns=target_columns,
            primary_key_hints=primary_key_hints,
            table_name=table_name,
            source_database=source_database,
        )
