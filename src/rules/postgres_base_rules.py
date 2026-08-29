"""
PostgreSQL → Snowflake Transformation Rules
============================================
All type-specific rules for normalizing PostgreSQL (and MSSQL/Athena — their
extractors already map types to PG-compatible names) column values before
comparing them with Snowflake.

Rule application order (innermost → outermost):
  1. integer / uuid / json / bytea / hstore
  2. boolean
  3. timestamp_tz → timestamp_ntz → date
  4. numeric
  5. text (trim) / fallback
  6. null placeholder  ← always LAST (outermost, inherited by base class)
"""

import re
from abc import ABC, abstractmethod

from typing import List, Optional, Tuple

# ── NULL placeholder ─────────────────────────────────────────────────────────
NULL_PLACEHOLDER = "<<NULL>>"


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseValidationRule(ABC):
    """Abstract base for all validation rules. No source database is optional:
    every concrete rule MUST implement all four dialect expression methods
    below (_pg_expression, _ms_expression, _athena_expression, _sf_expression)
    — Python enforces this at class-definition time, since PostgreSQL, MSSQL,
    and Athena syntax genuinely differ (e.g. TO_CHAR/::jsonb/encode() are not
    valid MSSQL or Athena syntax). The public API wraps each dialect's
    expression with COALESCE(CAST(… AS TEXT/STRING), '<<NULL>>')."""

    @property
    @abstractmethod
    def rule_name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def trigger_pairs(self) -> List[Tuple[str, str]]: ...

    @abstractmethod
    def _pg_expression(self, col: str) -> str: ...

    @abstractmethod
    def _sf_expression(self, col: str) -> str: ...

    # ── MSSQL (SQL Server) source expression — REQUIRED, not optional ────
    # PG syntax (TO_CHAR, CAST AS TEXT, encode(), ::jsonb, etc.) is often not
    # valid on SQL Server, so every rule must supply its own MSSQL-correct
    # expression rather than silently inheriting PostgreSQL syntax.
    @abstractmethod
    def _ms_expression(self, col: str) -> str: ...

    # ── Athena (Trino/Presto) source expression — REQUIRED, not optional ──
    # Trino/Presto syntax also differs from PostgreSQL (e.g. date_format vs
    # TO_CHAR), so every rule must supply its own Athena-correct expression.
    @abstractmethod
    def _athena_expression(self, col: str) -> str: ...

    def apply_postgresql(self, col: str, alias: Optional[str] = None) -> str:
        wrapped = self._coalesce_pg(self._pg_expression(col))
        return f"{wrapped} AS {alias}" if alias else wrapped

    def apply_mssql(self, col: str, alias: Optional[str] = None) -> str:
        wrapped = self._coalesce_ms(self._ms_expression(col))
        return f"{wrapped} AS {alias}" if alias else wrapped

    def apply_athena(self, col: str, alias: Optional[str] = None) -> str:
        wrapped = self._coalesce_athena(self._athena_expression(col))
        return f"{wrapped} AS {alias}" if alias else wrapped

    def apply_snowflake(self, col: str, alias: Optional[str] = None) -> str:
        wrapped = self._coalesce_sf(self._sf_expression(col))
        return f"{wrapped} AS {alias}" if alias else wrapped

    def apply_source(
        self, source_db_type: str, col: str, alias: Optional[str] = None
    ) -> str:
        """Dispatch to the correct source-database dialect.

        Args:
            source_db_type: 'postgresql' | 'postgres' | 'mssql' | 'sqlserver'
                            | 'athena' | 'trino' | 'presto' | 'snowflake'
            col           : Column name to normalize
            alias         : Optional SELECT alias

        Returns:
            A COALESCE-wrapped, dialect-correct SQL expression.
        """
        db = (source_db_type or "postgresql").strip().lower()
        if db in ("mssql", "sqlserver", "sql_server", "mssqlserver"):
            return self.apply_mssql(col, alias)
        if db in ("athena", "trino", "presto"):
            return self.apply_athena(col, alias)
        if db in ("snowflake",):
            return self.apply_snowflake(col, alias)
        # postgres / postgresql / default
        return self.apply_postgresql(col, alias)

    @property
    def is_skip_rule(self) -> bool:
        return False

    @staticmethod
    def _coalesce_pg(expr: str) -> str:
        return f"COALESCE(CAST({expr} AS TEXT), '{NULL_PLACEHOLDER}')"

    @staticmethod
    def _coalesce_ms(expr: str) -> str:
        # SQL Server has no TEXT cast target for comparison; use VARCHAR(MAX).
        return f"COALESCE(CAST({expr} AS VARCHAR(MAX)), '{NULL_PLACEHOLDER}')"

    @staticmethod
    def _coalesce_athena(expr: str) -> str:
        # Athena/Trino uses VARCHAR (no TEXT type).
        return f"COALESCE(CAST({expr} AS VARCHAR), '{NULL_PLACEHOLDER}')"

    @staticmethod
    def _coalesce_sf(expr: str) -> str:
        return f"COALESCE(CAST({expr} AS STRING), '{NULL_PLACEHOLDER}')"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} rule='{self.rule_name}'>"


# ── Rule Registry ─────────────────────────────────────────────────────────────

class RuleRegistry:
    """Maps (pg_type, sf_type) pairs to validation rules — first match wins."""

    def __init__(self):
        self._rules: List[BaseValidationRule] = []
        self._default: Optional[BaseValidationRule] = None

    def register(self, rule: BaseValidationRule):
        self._rules.append(rule)
        if rule.rule_name == "text":
            self._default = rule

    def lookup(self, pg_type: str, sf_type: str) -> BaseValidationRule:
        pg_norm = _normalize_type(pg_type)
        sf_norm = _normalize_type(sf_type)
        for rule in self._rules:
            for pg_pat, sf_pat in rule.trigger_pairs:
                if _type_matches(pg_norm, pg_pat) and _type_matches(sf_norm, sf_pat):
                    return rule
        return self._default or (self._rules[0] if self._rules else _NoOpRule())

    def lookup_specific(self, pg_type: str, sf_type: str) -> Optional[BaseValidationRule]:
        """
        Same as lookup(), but ignores wildcard ("*", "*") trigger pairs.

        Returns None when no rule has a SPECIFIC type-pair match — i.e. the
        type pair would otherwise only be caught by TextRule's ("*", "*")
        catch-all. This is the seam the learned-rules gap-filler layer uses:
        it is only ever consulted here, so it can never shadow a rule that
        already owns this type pair specifically.
        """
        pg_norm = _normalize_type(pg_type)
        sf_norm = _normalize_type(sf_type)
        for rule in self._rules:
            for pg_pat, sf_pat in rule.trigger_pairs:
                if pg_pat == "*" and sf_pat == "*":
                    continue
                if _type_matches(pg_norm, pg_pat) and _type_matches(sf_norm, sf_pat):
                    return rule
        return None

    def get_by_name(self, rule_name: str) -> Optional[BaseValidationRule]:
        """Look up a registered base rule by its rule_name (e.g. 'text', 'boolean')."""
        for rule in self._rules:
            if rule.rule_name == rule_name:
                return rule
        return None

    def all_rules(self) -> List[BaseValidationRule]:
        return list(self._rules)


def _normalize_type(type_str: str) -> str:
    if not type_str:
        return ""
    normalized = re.sub(r"\([^)]*\)", "", type_str)
    normalized = re.sub(r"\s+without\s+time\s+zone", "_NTZ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+with\s+time\s+zone",    "_TZ",  normalized, flags=re.IGNORECASE)
    return normalized.strip().upper()


def _type_matches(type_str: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    return type_str.upper().startswith(pattern.upper())


class _NoOpRule(BaseValidationRule):
    @property
    def rule_name(self) -> str: return "noop"
    @property
    def description(self) -> str: return "No-op fallback."
    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]: return [("*", "*")]
    def _pg_expression(self, col: str) -> str: return f"CAST({col} AS TEXT)"
    def _sf_expression(self, col: str) -> str: return f"CAST({col} AS STRING)"
    def _ms_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR(MAX))"
    def _athena_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR)"


# ── Concrete Rules ────────────────────────────────────────────────────────────

class BooleanRule(BaseValidationRule):
    """Boolean: TRUE→'1', FALSE→'0'. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "boolean"

    @property
    def description(self) -> str:
        return "Boolean: TRUE→'1', FALSE→'0'. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("BOOLEAN", "BOOLEAN"), ("BOOLEAN", "BOOL"),
                ("BOOL", "BOOLEAN"),   ("BOOL", "BOOL")]

    def _pg_expression(self, col: str) -> str:
        return (f"CASE WHEN {col} = true THEN '1' "
                f"WHEN {col} = false THEN '0' ELSE NULL END")

    def _sf_expression(self, col: str) -> str:
        return (f"CASE WHEN {col} = TRUE THEN '1' "
                f"WHEN {col} = FALSE THEN '0' ELSE NULL END")

    def _ms_expression(self, col: str) -> str:
        # SQL Server BIT: 1/0 (no true/false literals).
        return (f"CASE WHEN {col} = 1 THEN '1' "
                f"WHEN {col} = 0 THEN '0' ELSE NULL END")

    def _athena_expression(self, col: str) -> str:
        return (f"CASE WHEN {col} = true THEN '1' "
                f"WHEN {col} = false THEN '0' ELSE NULL END")


class IntegerRule(BaseValidationRule):
    """Integer types: cast to text for cross-system comparison. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "integer"

    @property
    def description(self) -> str:
        return "Integer: cast to text. Handles SMALLINT/INT/BIGINT/SERIAL→NUMBER. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("SMALLINT", "NUMBER"), ("SMALLINT", "INTEGER"),
                ("INTEGER",  "NUMBER"), ("INTEGER",  "INTEGER"),
                ("INT",      "NUMBER"), ("INT",      "INTEGER"),
                ("BIGINT",   "NUMBER"), ("BIGINT",   "INTEGER"),
                ("SERIAL",   "NUMBER"), ("SERIAL",   "INTEGER"),
                ("BIGSERIAL","NUMBER"), ("BIGSERIAL","INTEGER")]

    def _pg_expression(self, col: str) -> str: return f"CAST({col} AS TEXT)"
    def _sf_expression(self, col: str) -> str: return f"CAST({col} AS STRING)"
    def _ms_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR(MAX))"
    def _athena_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR)"


DEFAULT_DECIMAL_PLACES: int = 2  # kept for backward-compat imports; no longer used to round




class NumericRule(BaseValidationRule):
    """Numeric/decimal: compare at full native precision, no rounding. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "numeric"

    @property
    def description(self) -> str:
        return "Numeric: cast to text at full native precision (no rounding). NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("NUMERIC",          "NUMBER"),  ("NUMERIC",          "NUMERIC"),
                ("DECIMAL",          "NUMBER"),  ("DECIMAL",          "DECIMAL"),
                ("FLOAT",            "FLOAT"),   ("REAL",             "FLOAT"),
                ("DOUBLE PRECISION", "FLOAT"),   ("DOUBLE PRECISION", "NUMBER"),
                ("MONEY",            "NUMBER"),  ("MONEY",            "NUMERIC")]

    def _pg_expression(self, col: str) -> str:
        # CAST to NUMERIC first strips MONEY's currency formatting ('$100.00' → 100.00)
        # without touching precision.
        return f"CAST(CAST({col} AS NUMERIC) AS TEXT)"

    def _sf_expression(self, col: str) -> str:
        return f"CAST({col} AS STRING)"

    def _ms_expression(self, col: str) -> str:
        return f"CAST({col} AS VARCHAR(MAX))"

    def _athena_expression(self, col: str) -> str:
        return f"CAST({col} AS VARCHAR)"


class TimestampTZRule(BaseValidationRule):
    """Timestamp TZ: convert to UTC then format to microsecond precision. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "timestamp_tz"

    @property
    def description(self) -> str:
        return "Timestamp TZ: UTC normalize, format to microsecond precision. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("TIMESTAMP_TZ", "TIMESTAMP_TZ"),
                ("TIMESTAMP_TZ", "TIMESTAMPTZ"),
                ("TIMESTAMPTZ",  "TIMESTAMP_TZ")]

    def _pg_expression(self, col: str) -> str:
        return f"TO_CHAR({col} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')"

    def _sf_expression(self, col: str) -> str:
        return f"TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col}), 'YYYY-MM-DD HH24:MI:SS.FF6')"

    def _ms_expression(self, col: str) -> str:
        # SQL Server: shift to UTC then format (24-hour clock, 6-digit fractional seconds).
        return f"FORMAT({col} AT TIME ZONE 'UTC', 'yyyy-MM-dd HH:mm:ss.ffffff')"

    def _athena_expression(self, col: str) -> str:
        # Trino/Athena: normalize to UTC then format (%f = 6-digit microseconds).
        return f"date_format(at_timezone({col}, 'UTC'), '%Y-%m-%d %H:%i:%s.%f')"


class TimestampNTZRule(BaseValidationRule):
    """Timestamp NTZ: format to microsecond precision. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "timestamp_ntz"

    @property
    def description(self) -> str:
        return "Timestamp NTZ: format to microsecond precision. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("TIMESTAMP",     "TIMESTAMP_NTZ"),
                ("TIMESTAMP_NTZ", "TIMESTAMP_NTZ"),
                ("TIMESTAMP",     "TIMESTAMP"),
                ("TIMESTAMP_NTZ", "TIMESTAMP"),
                ("DATETIME",        "TIMESTAMP_NTZ")]

    def _pg_expression(self, col: str) -> str:
        return f"TO_CHAR({col}, 'YYYY-MM-DD HH24:MI:SS.US')"

    def _sf_expression(self, col: str) -> str:
        return f"TO_VARCHAR({col}, 'YYYY-MM-DD HH24:MI:SS.FF6')"

    def _ms_expression(self, col: str) -> str:
        return f"FORMAT({col}, 'yyyy-MM-dd HH:mm:ss.ffffff')"

    def _athena_expression(self, col: str) -> str:
        return f"date_format({col}, '%Y-%m-%d %H:%i:%s.%f')"


class DateRule(BaseValidationRule):
    """Date: format as 'YYYY-MM-DD'. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "date"

    @property
    def description(self) -> str: return "Date: format YYYY-MM-DD. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("DATE", "DATE")]

    def _pg_expression(self, col: str) -> str: return f"TO_CHAR({col}, 'YYYY-MM-DD')"
    def _sf_expression(self, col: str) -> str: return f"TO_VARCHAR({col}, 'YYYY-MM-DD')"
    def _ms_expression(self, col: str) -> str: return f"FORMAT({col}, 'yyyy-MM-dd')"
    def _athena_expression(self, col: str) -> str: return f"date_format({col}, '%Y-%m-%d')"


class TextRule(BaseValidationRule):
    """Text/VARCHAR: TRIM whitespace. Wildcard fallback for all unmatched types. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "text"

    @property
    def description(self) -> str:
        return "Text: trim leading/trailing spaces. Empty string stays empty. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("CHARACTER VARYING", "TEXT"),    ("CHARACTER VARYING", "VARCHAR"),
                ("CHARACTER VARYING", "STRING"),  ("VARCHAR",           "VARCHAR"),
                ("VARCHAR",           "STRING"),  ("VARCHAR",           "TEXT"),
                ("CHAR",              "CHAR"),    ("CHAR",              "VARCHAR"),
                ("CHAR",              "STRING"),  ("TEXT",              "TEXT"),
                ("TEXT",              "VARCHAR"), ("TEXT",              "STRING"),
                ("*",                 "*")]       # wildcard fallback — must be LAST

    def _pg_expression(self, col: str) -> str: return f"TRIM({col})"
    def _sf_expression(self, col: str) -> str: return f"TRIM({col})"
    def _ms_expression(self, col: str) -> str: return f"LTRIM(RTRIM({col}))"
    def _athena_expression(self, col: str) -> str: return f"TRIM({col})"


class UUIDRule(BaseValidationRule):
    """UUID: UPPER(TRIM(CAST AS TEXT)) — case-insensitive per RFC 4122. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "uuid"

    @property
    def description(self) -> str:
        return "UUID: UPPER(TRIM()), case-insensitive comparison. Handles PG UUID→SF VARCHAR/STRING. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("UUID", "TEXT"), ("UUID", "VARCHAR"),
                ("UUID", "STRING"), ("UUID", "UUID")]

    def _pg_expression(self, col: str) -> str: return f"UPPER(TRIM(CAST({col} AS TEXT)))"
    def _sf_expression(self, col: str) -> str: return f"UPPER(TRIM(CAST({col} AS STRING)))"
    def _ms_expression(self, col: str) -> str: return f"UPPER(LTRIM(RTRIM(CAST({col} AS VARCHAR(MAX)))))"
    def _athena_expression(self, col: str) -> str: return f"UPPER(TRIM(CAST({col} AS VARCHAR)))"


class JSONRule(BaseValidationRule):
    """JSON/JSONB → path-flattened canonical representation.

    Source (PostgreSQL):
      Recursively flattens the JSONB value using a recursive CTE that walks
      every key/index.  Each leaf is emitted as  path=<typed-value>  where the
      value retains its JSON datatype (number unquoted, string double-quoted,
      boolean as true/false, json-null as 'null').  Object keys are sorted
      alphabetically at every level; array elements keep their 0-based index.
      Empty objects {} and empty arrays [] are emitted as distinct sentinels.
      SQL NULL → '<<NULL>>' via the outer COALESCE wrapper (inherited).

    Target (Snowflake VARIANT / STRING):
      Uses LATERAL FLATTEN(INPUT => PARSE_JSON(col), RECURSIVE => TRUE) to
      traverse the full document in one pass.  Leaf rows are assembled into
      path=<typed-value> pairs using the same conventions as the PG side.
      Pairs are sorted and concatenated so the final string is order-stable.

    Why no TRIM():
      Whitespace inside a JSON string value is semantically significant and
      must not be stripped.  The outer COALESCE cast (inherited from
      BaseValidationRule) is the only wrapper applied after flattening.
    """

    @property
    def rule_name(self) -> str: return "json"

    @property
    def description(self) -> str:
        return (
            "JSON/JSONB → path-flattened canonical text. "
            "Leaf values retain their datatype (number/string/bool/null). "
            "Object keys sorted; array indexes retained. "
            "SQL NULL → '<<NULL>>'; JSON null/{}/ [] are distinct sentinels."
        )

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [
            ("JSON",  "VARIANT"), ("JSON",  "VARCHAR"), ("JSON",  "STRING"), ("JSON",  "TEXT"),
            ("JSONB", "VARIANT"), ("JSONB", "VARCHAR"), ("JSONB", "STRING"), ("JSONB", "TEXT"),
            ("JSON",  "ARRAY"),   ("JSONB", "ARRAY"),
        ]

    def _pg_expression(self, col: str) -> str:
        # Recursive CTE that flattens a JSONB document into sorted path=value pairs.
        # jsonb_typeof() lets us distinguish objects, arrays, scalars, and null.
        #
        # PostgreSQL only allows a recursive CTE's recursive term to reference
        # itself (_jf) exactly once in its FROM clause — an earlier version of
        # this rule split object-traversal and array-traversal into two
        # separate "FROM _jf" branches joined by UNION ALL, which Postgres
        # rejects outright ("recursive reference ... must not appear within
        # its non-recursive term" / "... more than once"). The single LATERAL
        # subquery below unifies both traversal cases behind one "FROM _jf",
        # satisfying that restriction. Verified directly against Postgres —
        # this used to fail to parse at all, silently making JSON/JSONB
        # validation unusable for every table that hit this rule.
        return (
            f"(WITH RECURSIVE _jf(path, val) AS ("
            f"SELECT k, v "
            f"FROM jsonb_each({col}::jsonb) AS t(k, v) "
            f"UNION ALL "
            f"SELECT "
            f"  CASE WHEN jsonb_typeof(_jf.val) = 'object' THEN _jf.path || '.' || kv.k "
            f"       ELSE _jf.path || '[' || kv.k || ']' END, "
            f"  kv.v "
            f"FROM _jf, LATERAL ("
            f"  SELECT key AS k, value AS v FROM jsonb_each(_jf.val) WHERE jsonb_typeof(_jf.val) = 'object' "
            f"  UNION ALL "
            f"  SELECT ord::text AS k, v FROM jsonb_array_elements(_jf.val) WITH ORDINALITY AS t(v, ord) WHERE jsonb_typeof(_jf.val) = 'array' "
            f") AS kv(k, v) "
            f"WHERE jsonb_typeof(_jf.val) IN ('object','array') "
            f") "
            f"SELECT COALESCE("
            f"  CASE WHEN {col} IS NULL THEN NULL "
            f"       WHEN {col}::jsonb = '{{}}' THEN '{{}}' "
            f"       WHEN {col}::jsonb = '[]'   THEN '[]' "
            f"       ELSE ("
            f"         SELECT string_agg("
            f"           path || '=' || CASE jsonb_typeof(val) "
            f"             WHEN 'string'  THEN '\"' || (val #>> '{{}}') || '\"' "
            f"             WHEN 'null'    THEN 'null' "
            f"             ELSE val::text END, "
            f"           '|' ORDER BY path) "
            f"         FROM _jf "
            f"         WHERE jsonb_typeof(val) NOT IN ('object','array') "
            f"       ) END, '<<EMPTY>>') "
            f")"
        )

    def _sf_expression(self, col: str) -> str:
        # LATERAL FLATTEN with RECURSIVE => TRUE visits every leaf in the document.
        # PATH gives the dotted/bracketed path; VALUE is the leaf VARIANT cell.
        # TYPEOF() distinguishes datatypes; IS_NULL_VALUE() detects JSON null vs SQL NULL.
        # Pairs are sorted and concatenated via LISTAGG.
        #
        # NOTE: Snowflake rejects this as a correlated scalar subquery
        # ("Unsupported subquery type cannot be evaluated") when {col} is a
        # column reference from an enclosing query — LATERAL FLATTEN can't be
        # used inside a scalar subquery that correlates to an outer row this
        # way. This form only works when {col} is NOT correlated (e.g. a
        # literal, or already resolved). For the normal case — generating one
        # column's comparison expression inside a table's full SELECT list —
        # use apply_snowflake_correlated() instead, which works around this
        # via an explicit self-join rather than an implicit correlated
        # reference. Verified against a live Snowflake instance.
        return (
            f"(SELECT COALESCE("
            f"  CASE WHEN {col} IS NULL THEN NULL "
            f"       WHEN {col} = PARSE_JSON('{{}}') THEN '{{}}' "
            f"       WHEN {col} = PARSE_JSON('[]')   THEN '[]' "
            f"       ELSE ("
            f"         SELECT LISTAGG("
            f"           f.path || '=' || "
            f"             CASE WHEN IS_NULL_VALUE(f.value) THEN 'null' "
            f"                  WHEN TYPEOF(f.value) = 'VARCHAR'    THEN '\"' || f.value::STRING || '\"' "
            f"                  WHEN TYPEOF(f.value) = 'BOOLEAN' THEN f.value::STRING "
            f"                  ELSE f.value::STRING END, "
            f"           '|') WITHIN GROUP (ORDER BY f.path) "
            f"         FROM LATERAL FLATTEN(INPUT => PARSE_JSON(CAST({col} AS STRING)), RECURSIVE => TRUE) f "
            f"         WHERE TYPEOF(f.value) NOT IN ('OBJECT', 'ARRAY') "
            f"       ) END, '<<EMPTY>>')"
            f")"
        )

    def _ms_expression(self, col: str) -> str:
        # SQL Server has no recursive JSON flattening equivalent in pure SQL.
        # Cast to NVARCHAR for transport; comparison correctness relies on the
        # Snowflake target side producing the canonical form.
        return f"CAST({col} AS NVARCHAR(MAX))"

    def _athena_expression(self, col: str) -> str:
        return f"CAST({col} AS VARCHAR)"

    def apply_snowflake_correlated(self, col: str, table_fqn: str, pk_col: str, alias: Optional[str] = None) -> str:
        """Same canonical flattening as apply_snowflake(), but as a scalar
        subquery correlated via an explicit self-join on pk_col instead of a
        direct outer-column reference inside LATERAL FLATTEN — Snowflake
        rejects the latter ("Unsupported subquery type cannot be evaluated").
        Use this when generating the column's expression inside a table's
        full SELECT list (the outer query must reference the same table,
        unaliased or aliased consistently with table_fqn/pk_col below); use
        apply_snowflake() only where col is already an uncorrelated value.
        Verified against a live Snowflake instance.
        """
        expr = (
            f"(SELECT LISTAGG("
            f"    f.path || '=' || "
            f"      CASE WHEN IS_NULL_VALUE(f.value) THEN 'null' "
            f"           WHEN TYPEOF(f.value) = 'VARCHAR'    THEN '\"' || f.value::STRING || '\"' "
            f"           WHEN TYPEOF(f.value) = 'BOOLEAN' THEN f.value::STRING "
            f"           ELSE f.value::STRING END, "
            f"    '|') WITHIN GROUP (ORDER BY f.path) "
            f" FROM {table_fqn} AS _flt, "
            f"   LATERAL FLATTEN(INPUT => PARSE_JSON(CAST(_flt.{col} AS STRING)), RECURSIVE => TRUE) f "
            f" WHERE _flt.{pk_col} = {table_fqn}.{pk_col} "
            f"   AND TYPEOF(f.value) NOT IN ('OBJECT', 'ARRAY'))"
        )
        wrapped = f"COALESCE(CAST({expr} AS STRING), '{NULL_PLACEHOLDER}')"
        return f"{wrapped} AS {alias}" if alias else wrapped


class ByteaRule(BaseValidationRule):
    """Binary/BYTEA: hex encoding for cross-system comparison. NULL→'<<NULL>>'."""

    @property
    def rule_name(self) -> str: return "bytea"

    @property
    def description(self) -> str:
        return "Binary/BYTEA: hex text representation. NULL→'<<NULL>>'."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [("BYTEA",     "BINARY"),   ("BYTEA",     "VARBINARY"),
                ("BINARY",    "BINARY"),   ("VARBINARY", "BINARY"),
                ("BYTEA",     "VARCHAR"),  ("BYTEA",     "STRING")]

    def _pg_expression(self, col: str) -> str: return f"encode({col}, 'hex')"
    def _sf_expression(self, col: str) -> str: return f"LOWER(HEX_ENCODE({col}))"
    def _ms_expression(self, col: str) -> str: return f"LOWER(CONVERT(VARCHAR(MAX), {col}, 2))"
    def _athena_expression(self, col: str) -> str: return f"LOWER(to_hex({col}))"


class HStoreRule(BaseValidationRule):
    """HStore → sorted key=value canonical representation.

    Source (PostgreSQL):
      hstore_to_jsonb() converts the hstore to a JSONB object, then keys are
      extracted via jsonb_each() and assembled into sorted key=value pairs
      separated by '|'.  This produces a deterministic string regardless of
      insertion order.  Whitespace inside values is preserved (no TRIM).
      SQL NULL → '<<NULL>>'; empty hstore → '<<EMPTY>>'.

    Target (Snowflake VARCHAR / VARIANT):
      Fivetran and most loaders migrate hstore as a JSON-formatted string
      (e.g. '{"a":"1","b":"2"}').  PARSE_JSON + LATERAL FLATTEN produces the
      same sorted key=value pairs as the PG side.

    Why no TRIM():
      Whitespace is semantically meaningful inside hstore values and must not
      be stripped.  Trimming is reserved for pure character/text columns.
    """

    @property
    def rule_name(self) -> str: return "hstore"

    @property
    def description(self) -> str:
        return (
            "HStore → sorted key=value canonical text. "
            "Keys sorted alphabetically; values preserve whitespace (no TRIM). "
            "SQL NULL → '<<NULL>>'; empty hstore → '<<EMPTY>>'."
        )

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return [
            ("HSTORE", "TEXT"), ("HSTORE", "VARCHAR"),
            ("HSTORE", "STRING"), ("HSTORE", "VARIANT"),
        ]

    def _pg_expression(self, col: str) -> str:
        # hstore_to_jsonb converts hstore → jsonb preserving string values.
        # jsonb_each extracts key/value pairs; string_agg sorts and joins them.
        return (
            f"(SELECT COALESCE("
            f"  CASE WHEN {col} IS NULL THEN NULL "
            f"       WHEN {col} = ''::hstore THEN '<<EMPTY>>' "
            f"       ELSE ("
            f"         SELECT string_agg("
            f"           k || '=' || (v #>> '{{}}'), "
            f"           '|' ORDER BY k) "
            f"         FROM jsonb_each(hstore_to_jsonb({col})) AS t(k, v) "
            f"       ) END, '<<EMPTY>>') "
            f")"
        )

    def _sf_expression(self, col: str) -> str:
        # Fivetran stores hstore as a JSON string in Snowflake.
        # LATERAL FLATTEN produces one row per key; LISTAGG sorts and joins them.
        #
        # NOTE: same limitation as JSONRule — Snowflake rejects this as a
        # correlated scalar subquery when {col} is an outer-row reference.
        # Use apply_snowflake_correlated() when generating this column's
        # expression inside a table's full SELECT list.
        return (
            f"(SELECT COALESCE("
            f"  CASE WHEN {col} IS NULL THEN NULL "
            f"       WHEN TRIM(CAST({col} AS STRING)) IN ('{{}}', '') THEN '<<EMPTY>>' "
            f"       ELSE ("
            f"         SELECT LISTAGG("
            f"           f.key || '=' || f.value::STRING, "
            f"           '|') WITHIN GROUP (ORDER BY f.key) "
            f"         FROM LATERAL FLATTEN(INPUT => PARSE_JSON(CAST({col} AS STRING))) f "
            f"       ) END, '<<EMPTY>>')"
            f")"
        )

    def _ms_expression(self, col: str) -> str:
        # SQL Server has no hstore type; treat as raw text if encountered.
        return f"CAST({col} AS NVARCHAR(MAX))"

    def _athena_expression(self, col: str) -> str:
        return f"CAST({col} AS VARCHAR)"

    def apply_snowflake_correlated(self, col: str, table_fqn: str, pk_col: str, alias: Optional[str] = None) -> str:
        """Same canonical flattening as apply_snowflake(), but correlated via
        an explicit self-join on pk_col — see JSONRule.apply_snowflake_correlated
        for why. Verified against a live Snowflake instance."""
        expr = (
            f"(SELECT LISTAGG(f.key || '=' || f.value::STRING, '|') WITHIN GROUP (ORDER BY f.key) "
            f" FROM {table_fqn} AS _flt, "
            f"   LATERAL FLATTEN(INPUT => PARSE_JSON(CAST(_flt.{col} AS STRING))) f "
            f" WHERE _flt.{pk_col} = {table_fqn}.{pk_col})"
        )
        wrapped = f"COALESCE(CAST({expr} AS STRING), '{NULL_PLACEHOLDER}')"
        return f"{wrapped} AS {alias}" if alias else wrapped


class NullPlaceholderRule(BaseValidationRule):
    """Bare NULL→'<<NULL>>' with plain text cast. Used when no other transformation needed."""

    @property
    def rule_name(self) -> str: return "null_placeholder"

    @property
    def description(self) -> str:
        return f"NULL→'{NULL_PLACEHOLDER}'. Cast to text. Universal rule."

    @property
    def trigger_pairs(self) -> List[Tuple[str, str]]:
        return []  # not auto-matched; explicitly registered for direct use

    def _pg_expression(self, col: str) -> str: return f"CAST({col} AS TEXT)"
    def _sf_expression(self, col: str) -> str: return f"CAST({col} AS STRING)"
    def _ms_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR(MAX))"
    def _athena_expression(self, col: str) -> str: return f"CAST({col} AS VARCHAR)"

    def apply_postgresql(self, col: str, alias=None) -> str:
        expr = f"COALESCE(CAST({col} AS TEXT), '{NULL_PLACEHOLDER}')"
        return f"{expr} AS {alias}" if alias else expr

    def apply_snowflake(self, col: str, alias=None) -> str:
        expr = f"COALESCE(CAST({col} AS STRING), '{NULL_PLACEHOLDER}')"
        return f"{expr} AS {alias}" if alias else expr

    def apply_mssql(self, col: str, alias=None) -> str:
        expr = f"COALESCE(CAST({col} AS VARCHAR(MAX)), '{NULL_PLACEHOLDER}')"
        return f"{expr} AS {alias}" if alias else expr

    def apply_athena(self, col: str, alias=None) -> str:
        expr = f"COALESCE(CAST({col} AS VARCHAR), '{NULL_PLACEHOLDER}')"
        return f"{expr} AS {alias}" if alias else expr
