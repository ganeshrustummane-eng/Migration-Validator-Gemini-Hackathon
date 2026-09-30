"""
Silver-only SQL emission -- ADR 0018 (verbatim Coalesce transform, no
generic Bronze normalization *rule engine*), ADR 0019 (multisource join
concatenation), and ADR 0027 (blanket NULL-placeholder wrapper reinstated).

This bypasses src/generated_queries/sql_query_generator.py ->
ai_sql_generator.py -> rules/base_rules.py entirely: that path dispatches
per source/target type pair (e.g. Postgres hstore vs Snowflake VARIANT) to
pick a rule and wraps every column in
COALESCE(CAST(col AS STRING), '<<NULL>>'). Silver is always
Snowflake-to-Snowflake, so it never needs that per-type-pair dispatch -- but
per ADR 0027, it still applies the one fixed Snowflake wrapper string
directly (inlined below, not imported from base_rules.py) so generated
Silver SQL is visually NULL-safe like Bronze SQL. The Coalesce metadata's
own declared transform expression is still emitted exactly as written --
only wrapped, never rewritten.

Only entrypoint: emit_query_set(plan) -> ValidationQuerySet (the same shape
generated_queries/sql_query_generator.py's SQLQueryGenerator produces, so
YAMLConfigWriter.write_from_plan() keeps working unchanged, per ADR 0018 4).

resolve_ref_macro() is exported so coalesce_plan_builder.py can reuse the
exact same `{{ ref('LOCATION', 'NODE') }}` resolver when it builds
plan.population_scope["bronze_join_sql"] for multisource nodes (ADR 0019 3) --
implemented once, shared by both call sites, not duplicated.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

from generated_queries.sql_query_generator import ValidationQuerySet

if TYPE_CHECKING:
    from core.validation_plan import CanonicalValidationPlan

_REF_MACRO_RE = re.compile(r"\{\{\s*ref\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)\s*\}\}")

# ADR 0027: same fixed Snowflake-target wrapper base_rules.py uses -- inlined
# here rather than imported, since Silver never needs base_rules.py's
# per-type-pair dialect dispatch, just this one literal string.
NULL_PLACEHOLDER = "<<NULL>>"


def _wrap_null(expr: str) -> str:
    return f"COALESCE(CAST({expr} AS STRING), '{NULL_PLACEHOLDER}')"


def resolve_ref_macro(text: str, bronze_schema: str) -> str:
    """Resolve every Coalesce `{{ ref('LOCATION', 'NODE') }}` macro in *text*
    to a real `"LOCATION"."schema"."NODE"` reference. Everything else in
    *text* (the surrounding FROM/JOIN/ON SQL) passes through verbatim -- ADR
    0019 3 explicitly rejects parsing joinCondition into a structured join
    spec, only the ref() macro itself gets resolved.

    bronze_schema is the Bronze-side schema to plug into the resolved
    reference. Per ADR 0014 2 (still unverified against a second real
    sample), this reuses the Silver node's own schema for every Bronze table
    -- a known, flagged assumption, not something this function decides on
    its own.
    """
    def _sub(match: "re.Match[str]") -> str:
        location, node = match.group(1), match.group(2)
        return f'"{location}"."{bronze_schema}"."{node}"'
    return _REF_MACRO_RE.sub(_sub, text or "")


def _bronze_select_lines(plan: "CanonicalValidationPlan") -> List[str]:
    """One line per column, in metadata order (ADR 0018 1):
      - skipped (macro-computed / non-deterministic / unclassifiable) -> omitted
        entirely, no inline comment (ADR 0022 -- a comment survives as long as
        the query stays multi-line, but there is no query-language-agnostic
        comment syntax that's safe if this ever gets flattened again; the
        `skip_reason` on each mapping, persisted via PlanStore, is the record
        of what was excluded and why).
      - everything else (passthrough / recomputable expression) -> selected verbatim.
    """
    selected = [m for m in plan.mappings if not m.skip_validation]
    return [
        f'{_wrap_null(mapping.source_column)} AS "{mapping.target_column}"' + ("," if i < len(selected) - 1 else "")
        for i, mapping in enumerate(selected)
    ]


def _bronze_from_clause(plan: "CanonicalValidationPlan") -> str:
    """FROM/JOIN clause for the Bronze recompute query.

    Multisource nodes: plan.population_scope["bronze_join_sql"] (already
    macro-resolved, Coalesce's own join SQL concatenated verbatim -- ADR 0019
    3/4). Single-source nodes (the common case, untouched): the plain
    single-table FROM clause built from the plan's own source identity.
    """
    join_sql = (plan.population_scope or {}).get("bronze_join_sql")
    if join_sql:
        return join_sql
    return f'FROM "{plan.source_database}"."{plan.source_schema}"."{plan.source_table}"'


_TABLE_REF_RE = re.compile(r'"[^"]+"\."[^"]+"\."([^"]+)"(?:\s+(?:AS\s+)?"([^"]+)")?', re.I)
_WINDOW_RE = re.compile(r"\bOVER\s*\(", re.I)


def bronze_aliases(plan: "CanonicalValidationPlan") -> List[str]:
    """Alias (or table name) of every Bronze table the recompute query reads,
    driving table first. Single-source: just plan.source_table."""
    join_sql = (plan.population_scope or {}).get("bronze_join_sql")
    refs = _TABLE_REF_RE.findall(join_sql) if join_sql else []
    return [alias or table for table, alias in refs] or [plan.source_table]


def _scope_joins(plan: "CanonicalValidationPlan", side: str) -> str:
    """Workbook JOIN clauses for 'bronze' or 'silver' (ADR 0046), rendered by
    scope_filter.render_silver() into population_scope["scope_joins"]."""
    return ((plan.population_scope or {}).get("scope_joins") or {}).get(side, "")


def _bronze_from_with_joins(plan: "CanonicalValidationPlan") -> str:
    from_clause = _bronze_from_clause(plan)
    joins = _scope_joins(plan, "bronze")
    if joins and re.search(r"\bWHERE\b", from_clause, re.I):
        raise ValueError(
            f"{plan.target_table}: Coalesce join SQL already has a WHERE -- workbook JOINs "
            "can't be appended after it; choose Whole table for this node."
        )
    return from_clause + joins


def _silver_has_is_current(plan: "CanonicalValidationPlan") -> bool:
    return any(m.target_column.upper() == "IS_CURRENT" for m in plan.mappings)


def _bronze_filters(aliases: List[str], source_filter: str, from_clause: str, windowed: bool) -> str:
    """ADR 0045: _FIVETRAN_ACTIVE = TRUE on every Bronze table read, plus the
    scope filter (plan.source_filter). When the SELECT has a window function
    (SYS_VERSION = ROW_NUMBER() OVER ...), the active-row predicate goes in
    QUALIFY so it is applied after the window, exactly like Silver's own
    IS_CURRENT filter on the materialized SYS_VERSION.
    ponytail: joined (non-driving) tables use COALESCE(.., TRUE) so an unmatched
    LEFT JOIN row survives; a match that exists only as inactive history also
    drops the base row -- revisit if a multisource node shows that."""
    active = [f'"{aliases[0]}"."_FIVETRAN_ACTIVE" = TRUE'] + [
        f'COALESCE("{a}"."_FIVETRAN_ACTIVE", TRUE) = TRUE' for a in aliases[1:]
    ]
    where = [source_filter] if source_filter else []
    if not windowed:
        where = active + where
    keyword = "AND" if re.search(r"\bWHERE\b", from_clause, re.I) else "WHERE"
    sql = f"\n{keyword} " + "\n  AND ".join(where) if where else ""
    if windowed:
        sql += "\nQUALIFY " + "\n  AND ".join(active)
    return sql


def _silver_prefix(plan: "CanonicalValidationPlan") -> str:
    """Silver columns are qualified only once other tables are joined in."""
    return f'"{plan.target_table}".' if _scope_joins(plan, "silver") else ""


def _silver_filters(plan: "CanonicalValidationPlan") -> str:
    parts = [f'{_silver_prefix(plan)}"IS_CURRENT" = TRUE'] if _silver_has_is_current(plan) else []
    if plan.target_filter:
        parts.append(plan.target_filter)
    return ("\nWHERE " + "\n  AND ".join(parts)) if parts else ""


def _silver_select_lines(plan: "CanonicalValidationPlan") -> List[str]:
    active = plan.active_mappings
    lines = []
    for i, mapping in enumerate(active):
        col_ref = '{}"{}"'.format(_silver_prefix(plan), mapping.target_column)
        line = f'{_wrap_null(col_ref)} AS "{mapping.target_column}"'
        lines.append(line + ("," if i < len(active) - 1 else ""))
    return lines


def _union_source(plan: "CanonicalValidationPlan") -> str:
    """Several Bronze sources -> one Silver table (ADR 0049): one SELECT per
    Coalesce source mapping, each with its own column expressions, FROM/JOIN
    and _FIVETRAN_ACTIVE filter (QUALIFY when windowed), combined with the
    node's own set operator. Same column order/aliases in every branch."""
    scope = plan.population_scope
    if plan.source_filter or _scope_joins(plan, "bronze"):
        raise ValueError(
            f"{plan.target_table}: workbook scope filters are not supported on multi-source (UNION) "
            "nodes yet -- choose Whole table for this node (ADR 0049)."
        )
    selected = [m for m in plan.mappings if not m.skip_validation]
    parts = []
    for branch in scope["union_branches"]:
        lines = [
            f'{_wrap_null(branch["columns"][m.target_column])} AS "{m.target_column}"'
            + ("," if i < len(selected) - 1 else "")
            for i, m in enumerate(selected)
        ]
        from_clause = branch["from_sql"]
        refs = _TABLE_REF_RE.findall(from_clause)
        aliases = [alias or table for table, alias in refs] or [plan.source_table]
        windowed = any(_WINDOW_RE.search(line) for line in lines)
        parts.append("SELECT\n    " + "\n    ".join(lines) + "\n" + from_clause
                     + _bronze_filters(aliases, "", from_clause, windowed))
    return f"\n{scope.get('union_strategy', 'UNION ALL')}\n".join(parts)


def emit_query_set(plan: "CanonicalValidationPlan") -> ValidationQuerySet:
    """Build the Bronze recompute SQL and the plain Silver SELECT directly
    from *plan*. Each selected column is wrapped in the same
    COALESCE(CAST(col AS STRING), '<<NULL>>') placeholder AISQLQueryGenerator
    applies for Bronze (ADR 0027) -- but without AISQLQueryGenerator's
    per-type-pair rule dispatch, since Silver is always Snowflake-to-Snowflake
    and only ever needs this one fixed wrapper string. Returns the same
    ValidationQuerySet shape YAMLConfigWriter.write_from_plan() already
    consumes -- no change needed there.
    """
    bronze_lines = _bronze_select_lines(plan)
    bronze_select = "\n    ".join(bronze_lines)
    silver_select = "\n    ".join(_silver_select_lines(plan))
    from_clause = _bronze_from_with_joins(plan)
    windowed = any(_WINDOW_RE.search(line) for line in bronze_lines)
    silver_from = (f'FROM "{plan.target_database}"."{plan.target_schema}"."{plan.target_table}"'
                   + _scope_joins(plan, "silver"))

    # Same population on both sides, data and count queries alike (ADR 0045).
    if (plan.population_scope or {}).get("union_branches"):
        main_validation_source = _union_source(plan)
        row_count_source = f"SELECT COUNT(*) AS count FROM (\n{main_validation_source}\n)"
    else:
        aliases = bronze_aliases(plan)
        main_validation_source = (f"SELECT\n    {bronze_select}\n{from_clause}"
                                  f"{_bronze_filters(aliases, plan.source_filter, from_clause, windowed)}")
        row_count_source = (f"SELECT COUNT(*) AS count\n{from_clause}"
                            f"{_bronze_filters(aliases, plan.source_filter, from_clause, False)}")
    main_validation_target = f"SELECT\n    {silver_select}\n{silver_from}{_silver_filters(plan)}"
    row_count_target = f"SELECT COUNT(*) AS count {silver_from}{_silver_filters(plan)}"

    return ValidationQuerySet(
        table_name=plan.source_table,
        source_db_label=f"snowflake://{plan.source_database}.{plan.source_schema}.{plan.source_table}",
        target_db_label=f"snowflake://{plan.target_database}.{plan.target_schema}.{plan.target_table}",
        generated_by="coalesce_metadata",
        model_used="N/A",
        main_validation_source=main_validation_source,
        main_validation_target=main_validation_target,
        row_count_source=row_count_source,
        row_count_target=row_count_target,
    )
