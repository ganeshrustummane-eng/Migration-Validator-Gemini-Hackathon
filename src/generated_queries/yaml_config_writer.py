"""
YAML Config Writer
===================
Generates YAML validation configuration files AUTOMATICALLY —
triggered by QueryOutputManager.generate().

YOU NEVER CREATE YAML FILES MANUALLY.
When the pipeline runs for any table, it automatically produces:
  Project/config/bronze/data_validation/<table>.yaml
      ← data validation queries (column-level comparison)
  Project/config/bronze/count_validation/bronze.yaml
      ← all tables' row count blocks in one shared file

NOTE: Migration Validator only generates YAML configs.
      The PROJECT folder executor consumes and runs them.

Output format — data_validation YAML (per-table file):
────────────────────────────────────────────────────────────────
tables:
  <source_table>:
    validations:
      data_validation:            ← normalised full-scan SELECT
        source_table_name: ...
        sourcecolumn: <first_column>
        sourcequery: |
          SELECT col1_normalized, ... FROM ...;
        target_table_name: ...
        pktargetcolumn: <FIRST_COLUMN>
        targetquery: |
          SELECT COL1_normalized, ... FROM ...;
────────────────────────────────────────────────────────────────

Output format — bronze_count_validation.yaml (shared file):
────────────────────────────────────────────────────────────────
tables:
  <table_1>:
    validations:
      count_validation:           ← ① / ② COUNT(*) only
        source_table_name: ...
        source: postgres
        sourcequery: |
          SELECT count(*) as count from ...;
        target_table_name: ...
        target: snowflake
        targetquery: |
          SELECT count(*) as count from ...;

  <table_2>:
    validations:
      count_validation:
        ...
────────────────────────────────────────────────────────────────

Key design decisions:
  - pksourcecolumn / pktargetcolumn only on data_validation (not needed for aggregates)
  - YAML literal block scalar (|) used for all multi-line queries
  - Query content is indented 10 spaces (YAML requires > 8 for nested block)
  - Only the generator header comment is stripped; all SELECT lines kept
  - NULL placeholder: <<NULL>>  (consistent with all SQL rules)
  - Fivetran: WHERE _FIVETRAN_ACTIVE = TRUE added on Snowflake side when detected
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import yaml

from ai_transformation.column_mapping import ColumnRuleMapping
from generated_queries.sql_query_generator import ValidationQuerySet, _plan_to_rule_mappings

if TYPE_CHECKING:
    from core.validation_plan import CanonicalValidationPlan


# Bronze config output root: Project/config/bronze/
# Migration Validator generates YAMLs here; PROJECT folder consumes them
_BRONZE_CONFIG_DIR = Path(__file__).parent.parent.parent / "Project" / "config" / "bronze"

# Indentation for YAML literal block scalar content.
# The 'sourcequery: |' key sits at 8-space depth inside the YAML tree.
# All content lines must be indented MORE than 8 → we use 10 spaces.
_QUERY_INDENT = 10


class YAMLConfigWriter:
    """
    Writes YAML validation config files automatically when the pipeline runs.

    This class is called by QueryOutputManager.generate() — never directly.
    For every table the pipeline processes, one YAML file is written to
    config/bronze/data_validation/<table_name>_validation.yaml

    The YAML embeds the full normalised SELECT queries for both source
    and target (Snowflake) so automated validation runners can consume
    the file without any manual editing.
    """

    def write(
        self,
        query_set: ValidationQuerySet,
        source_db_type: str,
        pg_schema: str,
        pg_table: str,
        sf_database: str,
        sf_schema: str,
        sf_table: str,
        mappings: List[ColumnRuleMapping],
        has_fivetran_active: bool = False,
        output_dir: Optional[Path] = None,
        source_audit_column: str = "",
        target_audit_column: str = "",
    ) -> Path:
        """
        Write the data validation YAML for a single table.

        Output: config/bronze/data_validation/<table>_validation.yaml

        Contains three validation blocks (row count is in the separate
        bronze_count_validation.yaml written by write_count_yaml):
          data_validation           — normalised full-scan SELECT
          null_pct_validation       — NULL % per column
          distinct_count_validation — distinct value counts per column

        Args:
            query_set          : ValidationQuerySet with the generated SQL
            source_db_type     : Source database type (e.g. 'postgresql', 'mssql')
            pg_schema          : PostgreSQL schema (e.g. 'public')
            pg_table           : PostgreSQL table name
            sf_database        : Snowflake database name
            sf_schema          : Snowflake schema name
            sf_table           : Snowflake table name
            mappings           : Active ColumnRuleMapping list
            has_fivetran_active: True → WHERE _FIVETRAN_ACTIVE = TRUE on SF side
            output_dir         : Override output dir (default: config/bronze/data_validation/)
            source_audit_column: Optional source-side audit column name (blank by default)
            target_audit_column: Optional target-side audit column name (blank by default)

        Returns:
            Path to the written YAML file.
        """
        out_dir = (output_dir or _BRONZE_CONFIG_DIR) / "data_validation"
        out_dir.mkdir(parents=True, exist_ok=True)

        active = [m for m in mappings if not m.skip_validation]
        first_src_col = active[0].source_column if active else "id"
        first_tgt_col = active[0].target_column if active else "ID"

        def _prep(sql: str) -> str:
            return _indent_for_yaml(_strip_generator_header(sql), _QUERY_INDENT)

        yaml_content = _build_data_yaml(
            table_name_source=pg_table,
            table_name_target=sf_table,
            source_db_type=source_db_type,
            pg_schema=pg_schema,
            sf_database=sf_database,
            sf_schema=sf_schema,
            source_column=first_src_col,
            target_column=first_tgt_col,
            data_source_yaml=_prep(query_set.main_validation_source),
            data_target_yaml=_prep(query_set.main_validation_target),
            null_pct_source_yaml=_prep(query_set.null_pct_source),
            null_pct_target_yaml=_prep(query_set.null_pct_target),
            distinct_source_yaml=_prep(query_set.distinct_count_source),
            distinct_target_yaml=_prep(query_set.distinct_count_target),
            column_count=len(active),
            has_fivetran_active=has_fivetran_active,
            generated_at=query_set.generated_at,
            generated_by=query_set.generated_by,
            model_used=query_set.model_used,
            source_audit_column=source_audit_column,
            target_audit_column=target_audit_column,
        )

        yaml_path = out_dir / f"{pg_table}.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        print(f"  📋 Data YAML saved     : {yaml_path.resolve()}")
        return yaml_path

    def write_count_yaml(
        self,
        query_set: ValidationQuerySet,
        source_db_type: str,
        pg_schema: str,
        pg_table: str,
        sf_database: str,
        sf_schema: str,
        sf_table: str,
        has_fivetran_active: bool = False,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Upsert this table's count_validation block into the shared
        config/<layer>/count_validation/<layer>.yaml.

        Idempotent by construction: the existing file is parsed, this table's
        key is replaced (not appended), and the whole document is rewritten.
        Regenerating the same table N times yields byte-identical output.

        The previous implementation appended raw text, so a second run produced
        a duplicate top-level key. YAML resolves duplicates by last-wins, which
        meant the file silently disagreed with itself about what would run.

        Returns:
            Path to the <layer>.yaml file.
        """
        out_dir = (output_dir or _BRONZE_CONFIG_DIR) / "count_validation"
        out_dir.mkdir(parents=True, exist_ok=True)
        layer = out_dir.parent.name or "bronze"
        yaml_path = out_dir / f"{layer}.yaml"

        block = {
            "validations": {
                "count_validation": {
                    "source_table_name": pg_table,
                    "source": source_db_type,
                    "sourcequery": _strip_generator_header(query_set.row_count_source) + "\n",
                    "target_table_name": sf_table or pg_table,
                    "target": "snowflake",
                    "targetquery": _strip_generator_header(query_set.row_count_target) + "\n",
                }
            }
        }

        document = _load_yaml_document(yaml_path)
        tables = document.setdefault("tables", {})
        action = "updated" if pg_table in tables else "added"
        tables[pg_table] = block

        header = [
            "# ============================================================",
            f"# Migration Validator — {layer.capitalize()} Count Validation",
            f"# Contains   : row count checks for all {layer.capitalize()} tables.",
            "#",
            "# GENERATED FILE — do not hand-edit.",
            "# Rendered from the canonical validation plans in output/plans/.",
            "# Re-running generation upserts each table in place; it never appends.",
            "#",
            "# No generation timestamp is recorded here on purpose: this file is",
            "# shared by every table, so a timestamp would make each run produce a",
            "# diff even when nothing changed. Per-table provenance (when, which",
            "# model) lives in the plan JSON.",
            "# ============================================================",
            "",
        ]
        _dump_yaml_document(yaml_path, document, header)

        print(f"  📋 Count YAML {action:<7}: {yaml_path.resolve()}  ({pg_table})")
        return yaml_path

    def write_count_yaml_from_plan(
        self,
        plan: "CanonicalValidationPlan",
        query_set: ValidationQuerySet,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Write the count-only YAML directly from a CanonicalValidationPlan."""
        return self.write_count_yaml(
            query_set=query_set,
            source_db_type=plan.source_db_type,
            pg_schema=plan.source_schema,
            pg_table=plan.source_table,
            sf_database=plan.target_database,
            sf_schema=plan.target_schema,
            sf_table=plan.target_table,
            has_fivetran_active=plan.has_fivetran_active,
            output_dir=output_dir,
        )

    def write_from_plan(
        self,
        plan: "CanonicalValidationPlan",
        query_set: ValidationQuerySet,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Write the YAML config file directly from a CanonicalValidationPlan.

        This is the new plan-driven entry point. The plan is the single
        source of truth — no separate column list is needed.

        Args:
            plan      : Fully constructed CanonicalValidationPlan
            query_set : ValidationQuerySet already generated from the same plan
            output_dir: Output directory (default: config/bronze/data_validation/)

        Returns:
            Path to the written YAML file.
        """
        mappings = _plan_to_rule_mappings(plan.active_mappings)
        return self.write(
            query_set=query_set,
            source_db_type=plan.source_db_type,
            pg_schema=plan.source_schema,
            pg_table=plan.source_table,
            sf_database=plan.target_database,
            sf_schema=plan.target_schema,
            sf_table=plan.target_table,
            mappings=mappings,
            has_fivetran_active=plan.has_fivetran_active,
            output_dir=output_dir,
        )


# ---------------------------------------------------------------------------
# YAML content builder — exact format per project specification
# ---------------------------------------------------------------------------

def _build_data_yaml(
    table_name_source: str,
    table_name_target: str,
    source_db_type: str,
    pg_schema: str,
    sf_database: str,
    sf_schema: str,
    source_column: str,
    target_column: str,
    data_source_yaml: str,
    data_target_yaml: str,
    null_pct_source_yaml: str,
    null_pct_target_yaml: str,
    distinct_source_yaml: str,
    distinct_target_yaml: str,
    column_count: int,
    has_fivetran_active: bool,
    generated_at: str,
    generated_by: str,
    model_used: str,
    source_audit_column: str = "",
    target_audit_column: str = "",
) -> str:
    """
    Build the data validation YAML for one table (no row count block).

    Output goes to config/bronze/data_validation/<table>_validation.yaml.
    Row count lives in config/bronze/count_validation/bronze.yaml.

    Indentation structure:
      tables:                               <- 0 spaces
        <table>:                            <- 2 spaces
          validations:                      <- 4 spaces
            data_validation:               <- 6 spaces
              source_table_name: ...        <- 8 spaces
              sourcequery: |               <- 8 spaces
                <sql line>                 <- 10 spaces  (| block content)
            null_pct_validation:           <- 6 spaces
            distinct_count_validation:     <- 6 spaces
    """
    fivetran_comment = (
        "\n#   - Fivetran  : WHERE _FIVETRAN_ACTIVE = TRUE (Snowflake side — active records only)"
        if has_fivetran_active
        else ""
    )

    lines = [
        "# ============================================================",
        "# Migration Validator — Data Validation Config",
        f"# Table      : {table_name_source}",
        f"# Generated  : {generated_at}",
        f"# By         : {generated_by} (model: {model_used})",
        f"# Columns    : {column_count} comparable columns",
        "#",
        "# Validation blocks (row count is in bronze.yaml):",
        "#   data_validation           — normalised full-scan SELECT (all columns)",
        "#",
        "# Note: null_pct_validation and distinct_count_validation have been",
        "# removed. Only data_validation and count_validation are used.",
        "#",
        "# Normalization rules applied automatically:",
        "#   - Boolean    : TRUE/FALSE -> '1'/'0'",
        "#   - Numeric    : ROUND to 2 decimal places, then text",
        "#   - Timestamp  : 'YYYY-MM-DD HH24:MI:SS'  (microseconds stripped)",
        "#   - Timestamp_TZ: convert to UTC -> 'YYYY-MM-DD HH24:MI:SS'",
        "#   - Date       : 'YYYY-MM-DD'",
        "#   - Text/Char  : TRIM leading/trailing spaces",
        "#   - UUID       : UPPER(TRIM()) — case-insensitive comparison",
        "#   - Integer    : CAST to text",
        "#   - JSON/JSONB : canonical serialization (jsonb::text / TO_JSON)",
        "#   - Bytea      : hex text encoding",
        f"#   - NULL       : COALESCE -> '<<NULL>>' sentinel (ALL columns){fivetran_comment}",
        "# ============================================================",
        "",
        "tables:",
        f"  {table_name_source}:",
        "    validations:",
        "",
        "      # ── ③ / ④ Normalised data validation (all columns) ───────────",
        "      data_validation:",
        f"        source_table_name: {table_name_source}",
        f"        source: {source_db_type}",
        f"        pksourcecolumn: {source_column + '_normalized' if source_column else ''}",
        f"        source_audit_column: {source_audit_column}",
        "        sourcequery: |",
        data_source_yaml,
        f"        target_table_name: {table_name_target}",
        "        target: snowflake",
        f"        pktargetcolumn: {target_column + '_normalized' if target_column else ''}",
        f"        target_audit_column: {target_audit_column}",
        "        targetquery: |",
        data_target_yaml,
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# YAML document helpers — idempotent upsert support
# ---------------------------------------------------------------------------

class _LiteralStr(str):
    """A string that always serialises as a YAML literal block scalar (|)."""


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_LiteralStr, _literal_representer, Dumper=yaml.SafeDumper)


def _load_yaml_document(path: Path) -> dict:
    """
    Read an existing generated YAML file, tolerating absence.

    A malformed file is treated as empty rather than fatal: it is a render
    target that this writer is about to overwrite, so refusing to proceed
    would strand the user with a file only this code can repair.
    """
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        print(f"  [YAMLConfigWriter] {path.name} was unparseable ({exc}); rebuilding it.")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dump_yaml_document(path: Path, document: dict, header_lines: List[str]) -> None:
    """Write a YAML document with a comment header, SQL as literal blocks."""
    normalized = _mark_sql_literals(document)
    body = yaml.safe_dump(
        normalized,
        default_flow_style=False,
        sort_keys=False,
        width=10_000,
        allow_unicode=True,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(header_lines))
        handle.write("\n")
        handle.write(body)


def _mark_sql_literals(node):
    """Recursively tag *query fields so they round-trip as readable SQL blocks."""
    if isinstance(node, dict):
        return {
            key: (
                _LiteralStr(value)
                if isinstance(key, str)
                and key.endswith("query")
                and isinstance(value, str)
                else _mark_sql_literals(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_mark_sql_literals(item) for item in node]
    return node


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def _strip_generator_header(sql: str) -> str:
    """
    Strip the leading generator comment lines from a SQL string.

    SQLQueryGenerator prefixes each query with one or more comment lines:
        -- ③ SOURCE: MSSQL (dbo.AcctSoftware)
        -- AI-generated (gpt-4o, confidence 0.95)

    All leading '--' lines are removed so the YAML sourcequery/targetquery
    holds clean, runnable SQL. Comments inside the SELECT body are preserved.

    Args:
        sql: SQL string that may begin with header comment lines

    Returns:
        Clean SQL without the leading comment header.
    """
    if not sql:
        return "SELECT 1;"

    lines = sql.strip().splitlines()
    while lines and lines[0].strip().startswith("--"):
        lines = lines[1:]

    while lines and not lines[0].strip():
        lines = lines[1:]

    return "\n".join(lines).strip() if lines else sql.strip()


def _indent_for_yaml(sql: str, indent: int = 10) -> str:
    """
    Indent every line of a SQL query for embedding in a YAML literal block (|).

    YAML literal block scalars require ALL content lines to be indented
    more deeply than the key that introduces the block.

    Example (indent=10):
        sourcequery: |          ← key at 8-space indent
          SELECT                ← content at 10-space indent  ✓
              col1,
              col2
          FROM public.events;

    Args:
        sql   : Cleaned SQL query string
        indent: Number of spaces to prepend to every content line

    Returns:
        SQL with each line prefixed by 'indent' spaces.
    """
    prefix = " " * indent
    lines  = sql.strip().splitlines()
    return "\n".join(f"{prefix}{line}" for line in lines)
