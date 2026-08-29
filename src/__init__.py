"""
Migration Validator — PostgreSQL → Snowflake Validation Framework v3.0
========================================================================
Modular pipeline:
  1. sql_extractor/      — Live schema extraction (PostgreSQL, MSSQL, Snowflake)
  2. ai_transformation/  — Column mapping + rule assignment (AI or static)
  3. generated_queries/  — SQL + YAML output file generation
  4. rules/              — Type-specific SQL normalization rules
  5. batch/              — Multi-table batch processing
  6. validation_pipeline.py — End-to-end pipeline orchestrator
  7. validate_cli.py     — Interactive CLI with model selection

Quick Start
-----------
  # Single table:
  python src/validate_cli.py generate --pg-table events --sf-table EVENTS

  # Batch mode:
  python src/validate_cli.py batch --config tables.yaml

  # List all commands:
  python src/validate_cli.py --help

Output
------
  config/bronze/data_validation/<table>_validation.yaml   ← column-level validation
  config/bronze/count_validation/bronze_count_validation.yaml ← row counts
  src/runs/batch_run_*/                   ← Batch run manifests and plan JSON

Environment Variables (.env)
-----------------------------
  SOURCE_HOST, SOURCE_PORT, SOURCE_DATABASE, SOURCE_SCHEMA
  SOURCE_USERNAME, SOURCE_PASSWORD
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA
  SNOWFLAKE_USERNAME, SNOWFLAKE_PASSWORD
  DIAL_API_KEY      (optional — enables AI rule mapping)
  DIAL_MODEL        (optional — default: gpt-4o)
"""

__version__     = "3.0.0"
__author__      = "Migration Validator Team"
__description__ = (
    "PostgreSQL → Snowflake data completeness validation. "
    "Multi-source, batch processing, PK-aware SQL generation."
)

# ── Rule registry helpers (optional, imported on demand) ────────────────────
# Note: rules module is optional and only imported when explicitly needed
# This allows new validation system to work independently

__all__ = [
    # Pipeline entry point
    "ValidationPipeline",       # import separately: from validation_pipeline import ...

    # Version info
    "__version__",
    "__author__",
    "__description__",
]
