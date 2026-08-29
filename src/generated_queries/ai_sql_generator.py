"""
AI-Powered SQL Query Generator
================================
Uses AI to dynamically generate database-specific SQL queries for validation.

This module leverages the AI model to write optimized, database-specific SQL
queries based on the source and target database types, ensuring proper syntax
and data type conversions for:
  - MS SQL Server → Snowflake
  - PostgreSQL → Snowflake
  - Athena → Snowflake
  - Any database → Any database

Key Features:
  - Database-specific syntax (CAST, CONVERT, FORMAT functions)
  - Proper data type conversions (INT → VARCHAR(MAX) for MSSQL, TEXT for PG)
  - Timezone handling per database
  - NULL placeholder insertion
  - Fivetran active record filtering
  - Self-correcting: dialect-check failures are fed back to the model

AI-only by design
-----------------
There is NO rule-based fallback. If AI is unavailable or cannot produce SQL
that passes dialect validation, generation raises AISQLGenerationError.
A silently degraded query is more dangerous than a failed run: it yields
validation results that look authoritative but were never trustworthy.

Environment Variables Required:
  DIAL_API_KEY      — EPAM DIAL API key (REQUIRED)
  DIAL_API_BASE     — defaults to https://ai-proxy.lab.epam.com
  DIAL_API_VERSION  — defaults to 2025-04-01-preview
  DIAL_MODEL        — defaults to gpt-4o
"""

import json
import os
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass

from ai_transformation.column_mapping import ColumnRuleMapping

try:
    from token_usage_analysis.token_logger import (
        log_usage, extract_openai_usage, extract_anthropic_usage,
    )
except ImportError:
    def log_usage(*args, **kwargs):  # pragma: no cover - logging is best-effort
        pass

    def extract_openai_usage(response):  # pragma: no cover
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def extract_anthropic_usage(response):  # pragma: no cover
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_API_BASE     = "https://ai-proxy.lab.epam.com"
_DEFAULT_API_VERSION  = "2025-04-01-preview"
_DEFAULT_DIAL_MODEL   = "gpt-4o"
_DEFAULT_CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
NULL_PLACEHOLDER      = "<<NULL>>"

_BACKEND_DIAL   = "dial"
_BACKEND_CLAUDE = "claude"

# Failed dialect checks are fed back to the model; this caps the correction loop.
MAX_GENERATION_ATTEMPTS = 3


class AISQLGenerationError(RuntimeError):
    """Raised when AI cannot produce SQL that passes dialect validation."""


# ---------------------------------------------------------------------------
# AI SQL Generator
# ---------------------------------------------------------------------------

@dataclass
class AIGeneratedQuery:
    """Container for AI-generated SQL query with metadata."""
    query: str
    database_type: str
    explanation: str
    confidence: float
    warnings: List[str]


class AISQLQueryGenerator:
    """
    Generates database-specific SQL queries using AI.
    
    This generator understands the nuances of different SQL dialects and
    produces optimized, correct queries for each source/target combination.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Args:
            api_key    : DIAL API key (default: DIAL_API_KEY env var)
            api_base   : DIAL endpoint base URL
            api_version: Azure OpenAI API version
            model      : Model deployment name (e.g. 'gpt-4o', 'gpt-4o-mini')
        """
        # Priority 1: EPAM DIAL
        dial_key   = api_key or os.getenv("DIAL_API_KEY", "")
        # Priority 2: Claude direct (only when DIAL key absent)
        claude_key = os.getenv("CLAUDE_API_KEY", "") if not dial_key else ""

        if dial_key:
            self._backend    = _BACKEND_DIAL
            self.api_key     = dial_key
            self.api_base    = api_base    or os.getenv("DIAL_API_BASE",    _DEFAULT_API_BASE)
            self.api_version = api_version or os.getenv("DIAL_API_VERSION", _DEFAULT_API_VERSION)
            self.model       = model       or os.getenv("DIAL_MODEL",        _DEFAULT_DIAL_MODEL)
        elif claude_key:
            self._backend    = _BACKEND_CLAUDE
            self.api_key     = claude_key
            self.api_base    = ""
            self.api_version = ""
            # Guard: never send a DIAL/GPT model name to Anthropic API
            claude_env_model = os.getenv("CLAUDE_MODEL", _DEFAULT_CLAUDE_MODEL)
            if model and model.lower().startswith("claude-"):
                self.model = model
            else:
                self.model = claude_env_model
        else:
            self._backend    = _BACKEND_DIAL
            self.api_key     = ""
            self.api_base    = _DEFAULT_API_BASE
            self.api_version = _DEFAULT_API_VERSION
            self.model       = model or _DEFAULT_DIAL_MODEL

        self._ai_active = bool(self.api_key)

    def generate_validation_query(
        self,
        schema: str,
        table: str,
        mappings: List[ColumnRuleMapping],
        source_db_type: str,
        target_db_type: str = "snowflake",
        query_type: str = "data_validation",
        has_fivetran_active: bool = False,
    ) -> AIGeneratedQuery:
        """
        Generate a database-specific validation query using AI.

        There is no rule-based fallback. If AI cannot produce SQL that passes
        dialect validation, this raises — a silently degraded query is worse
        than no query, because it produces results nobody knows to distrust.

        Args:
            schema              : Source schema name
            table               : Source table name
            mappings            : Column rule mappings
            source_db_type      : Source database type (mssql, postgresql, athena)
            target_db_type      : Target database type (default: snowflake)
            query_type          : Type of query (data_validation, null_pct, distinct_count)
            has_fivetran_active : Whether to include Fivetran filter

        Returns:
            AIGeneratedQuery with validated SQL.

        Raises:
            AISQLGenerationError: no API key, SDK missing, API unreachable, or
                                  every attempt failed dialect validation.
        """
        if not self._ai_active:
            raise AISQLGenerationError(
                "No AI API key configured — cannot generate validation SQL.\n"
                "  Set one of the following in .env:\n"
                "    DIAL_API_KEY=...    (EPAM DIAL — access to GPT/Claude/Gemini)\n"
                "    CLAUDE_API_KEY=...  (Anthropic direct — no VPN needed)\n"
                "  Or run: python validate_cli.py  →  choose [8] Configure API key"
            )

        system_prompt = self._build_system_prompt(source_db_type, target_db_type)
        user_prompt   = self._build_user_prompt(
            schema, table, mappings, source_db_type, query_type, has_fivetran_active
        )

        print(
            f"  [AISQLGenerator] Backend: "
            f"{'Claude Direct' if self._backend == _BACKEND_CLAUDE else 'EPAM DIAL'}  |  "
            f"Model: '{self.model}'  |  {schema}.{table} ({query_type})"
        )

        attempts: List[str] = []
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            raw    = self._call_ai(messages, f"{schema}.{table} ({query_type})", attempt)
            result = self._parse_response(raw, source_db_type, mappings, query_type)

            if not result.warnings:
                if attempt > 1:
                    print(
                        f"  [AISQLGenerator] {schema}.{table} ({query_type}) "
                        f"passed validation on attempt {attempt}."
                    )
                return result

            attempts.append(f"attempt {attempt}: " + "; ".join(result.warnings))
            print(
                f"  [AISQLGenerator] Attempt {attempt}/{MAX_GENERATION_ATTEMPTS} for "
                f"{schema}.{table} ({query_type}) failed dialect checks: "
                + "; ".join(result.warnings)
            )
            messages = messages + [
                {"role": "assistant", "content": result.query},
                {
                    "role": "user",
                    "content": (
                        "That query failed validation for these reasons:\n"
                        + "\n".join(f"- {w}" for w in result.warnings)
                        + f"\n\nRewrite it as valid {source_db_type.upper()} SQL that "
                        "fixes every issue above. Return ONLY the SQL."
                    ),
                },
            ]

        raise AISQLGenerationError(
            f"AI could not produce valid {source_db_type.upper()} SQL for "
            f"{schema}.{table} ({query_type}) after {MAX_GENERATION_ATTEMPTS} attempts.\n"
            + "\n".join(f"  {a}" for a in attempts)
        )

    def generate_target_validation_query(
        self,
        target_fqn: str,
        mappings: List[ColumnRuleMapping],
        source_db_type: str,
        target_db_type: str = "snowflake",
        has_fivetran_active: bool = False,
    ) -> AIGeneratedQuery:
        """
        Generate the TARGET-side data validation query using AI.

        Generating both sides with AI (rather than AI source + rule-based target)
        is what keeps the two SELECT lists aligned: the model is given the
        source aliases and told they must match exactly, so a column can never
        be compared against the wrong one.

        Args:
            target_fqn          : Fully qualified target table (db.schema.table)
            mappings            : Column rule mappings (active only)
            source_db_type      : Source dialect, for alias context
            target_db_type      : Target dialect (default: snowflake)
            has_fivetran_active : Add the active-record filter

        Raises:
            AISQLGenerationError: same conditions as generate_validation_query.
        """
        if not self._ai_active:
            raise AISQLGenerationError(
                "No AI API key configured — cannot generate target validation SQL.\n"
                "  Set DIAL_API_KEY or CLAUDE_API_KEY in .env, "
                "or run: python validate_cli.py  →  choose [8]"
            )

        active = [m for m in mappings if not m.skip_validation]
        columns_json = [
            {
                "target_column":    m.target_column,
                "target_type":      m.target_type,
                "source_column":    m.source_column,
                "source_type":      m.source_type,
                "required_alias":   f"{m.source_column}_normalized",
                "rule":             m.rule.rule_name,
            }
            for m in active
        ]

        fivetran_note = (
            "\nThe query MUST include: WHERE _FIVETRAN_ACTIVE = TRUE"
            if has_fivetran_active
            else ""
        )

        system_prompt = self._build_system_prompt(source_db_type, target_db_type)
        user_prompt = f"""Generate a {target_db_type.upper()} SQL query for: {target_fqn}

Query Type: SELECT normalized columns for row-by-row comparison against the
{source_db_type.upper()} source. The two result sets are compared positionally
AND by alias, so the aliases below are non-negotiable.

Columns ({len(columns_json)} total, in this exact order):
{json.dumps(columns_json, indent=2)}

Requirements:
1. SELECT the columns in exactly the order listed above.
2. Read from the column named in "target_column".
3. Alias each expression with EXACTLY the string in "required_alias" — same
   spelling and same case. Quote the alias with double quotes so
   {target_db_type.upper()} preserves the case verbatim.
4. Wrap every expression: COALESCE(CAST(expression AS {self._get_text_type(target_db_type)}), '{NULL_PLACEHOLDER}')
5. Timestamps: {self._get_format_function(target_db_type)}
6. Booleans: CASE WHEN col = TRUE THEN '1' WHEN col = FALSE THEN '0' ELSE NULL END
7. Apply the same normalization semantics the source side uses, so equal data
   produces byte-identical strings on both sides.{fivetran_note}

Generate the complete SELECT query now:
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        attempts: List[str] = []

        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            raw    = self._call_ai(messages, f"target:{target_fqn}", attempt)
            result = self._parse_response(raw, target_db_type, active, "data_validation")
            if not result.warnings:
                return result

            attempts.append(f"attempt {attempt}: " + "; ".join(result.warnings))
            print(
                f"  [AISQLGenerator] Target attempt {attempt}/{MAX_GENERATION_ATTEMPTS} "
                f"for {target_fqn} failed checks: " + "; ".join(result.warnings)
            )
            messages = messages + [
                {"role": "assistant", "content": result.query},
                {
                    "role": "user",
                    "content": (
                        "That query failed validation for these reasons:\n"
                        + "\n".join(f"- {w}" for w in result.warnings)
                        + f"\n\nRewrite it as valid {target_db_type.upper()} SQL that "
                        "fixes every issue. Return ONLY the SQL."
                    ),
                },
            ]

        raise AISQLGenerationError(
            f"AI could not produce valid {target_db_type.upper()} SQL for "
            f"{target_fqn} after {MAX_GENERATION_ATTEMPTS} attempts.\n"
            + "\n".join(f"  {a}" for a in attempts)
        )

    def generate_custom_query(
        self,
        user_instruction: str,
        table_fqn: str,
        columns: List[dict],
        db_type: str,
    ) -> AIGeneratedQuery:
        """
        Generate a free-form SQL query from a plain-English request, scoped to
        an explicit, human-reviewed column list for ONE real table.

        Args:
            user_instruction: Plain-English description of the desired query.
            table_fqn: The exact table the query must read FROM (e.g.
                "public.employees" or "MYDB.PUBLIC.EMPLOYEES") — required, so
                the model never has to invent a placeholder table name.
            columns: One dict per available column, each with keys "column"
                and "type" for THIS table/dialect — the caller decides which
                columns are in scope (e.g. only the ones checked in a UI
                grid) and which side's names to use; this method does not
                re-derive that from a full mapping run.
            db_type: Dialect the query should be written in.

        Unlike generate_validation_query, there is no fixed alias contract to
        enforce (this isn't a source-vs-target comparison query), so
        validation is looser: still requires a real SELECT...FROM, still
        rejects wrong-dialect syntax, but does not require the
        '<col>_normalized' alias or the NULL placeholder. Uses the same
        self-correcting retry loop as the rest of this class.

        Raises:
            AISQLGenerationError: no API key configured, or every attempt
                failed the (looser) validation checks.
        """
        if not self._ai_active:
            raise AISQLGenerationError(
                "No AI API key configured — cannot generate custom SQL.\n"
                "  Set DIAL_API_KEY or CLAUDE_API_KEY in .env, "
                "or run: python validate_cli.py  →  choose [8] Configure API key"
            )

        columns_json = columns

        system_prompt = self._build_system_prompt(db_type, db_type)
        user_prompt = f"""Generate a {db_type.upper()} SQL query.

Table: {table_fqn}
The query's FROM clause MUST read exactly "{table_fqn}" — do not invent,
alias away, or use a placeholder table name.

Available columns on THIS table (name, type, and the normalization rule
already assigned to it elsewhere in this project, if any):
{json.dumps(columns_json, indent=2)}

User request:
\"\"\"{user_instruction}\"\"\"

Requirements:
1. Use only the column names listed above — do not invent columns.
2. Write valid {db_type.upper()} SQL syntax (see the dialect rules above).
3. If the request involves comparing, deduplicating, or unioning values,
   apply the same normalization style already used for these columns (trim
   text, cast NULLs to a comparable placeholder) so results stay consistent
   with the rest of this project's validation queries.
4. Return ONLY the SQL — no explanation, no markdown fences.

Generate the query now:
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        attempts: List[str] = []

        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            raw    = self._call_ai(messages, f"custom:{db_type}", attempt)
            result = self._parse_response(raw, db_type, [], query_type="custom")
            if table_fqn.split(".")[-1].strip('"').upper() not in result.query.upper():
                result.warnings.append(
                    f"Query does not reference the required table '{table_fqn}' — "
                    "it may have used a placeholder table name instead."
                )
            if not result.warnings:
                return result

            attempts.append(f"attempt {attempt}: " + "; ".join(result.warnings))
            print(
                f"  [AISQLGenerator] Custom query attempt {attempt}/{MAX_GENERATION_ATTEMPTS} "
                f"failed checks: " + "; ".join(result.warnings)
            )
            messages = messages + [
                {"role": "assistant", "content": result.query},
                {
                    "role": "user",
                    "content": (
                        "That query failed validation for these reasons:\n"
                        + "\n".join(f"- {w}" for w in result.warnings)
                        + f"\n\nRewrite it as valid {db_type.upper()} SQL that fixes every "
                        "issue above. Return ONLY the SQL."
                    ),
                },
            ]

        raise AISQLGenerationError(
            f"AI could not produce valid {db_type.upper()} SQL for this request "
            f"after {MAX_GENERATION_ATTEMPTS} attempts.\n"
            + "\n".join(f"  {a}" for a in attempts)
        )

    # -----------------------------------------------------------------------
    # Backend dispatch helpers
    # -----------------------------------------------------------------------

    def _call_ai(self, messages: list, context: str, attempt: int) -> str:
        """Dispatch to the correct backend (DIAL or Claude direct)."""
        if self._backend == _BACKEND_CLAUDE:
            return self._call_claude(messages, context, attempt)
        return self._call_dial(messages, context, attempt)

    def _call_dial(self, messages: list, context: str, attempt: int) -> str:
        """Call EPAM DIAL via AzureOpenAI SDK."""
        try:
            from openai import AzureOpenAI
        except ImportError as exc:
            raise AISQLGenerationError(
                "The 'openai' package is required for DIAL SQL generation.\n"
                "Install it with: pip install -r requirements.txt"
            ) from exc
        client = AzureOpenAI(
            api_key=self.api_key,
            api_version=self.api_version,
            azure_endpoint=self.api_base,
        )
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                extra_headers={"Api-Key": self.api_key},
            )
            usage = extract_openai_usage(response)
            log_usage(
                backend="dial",
                model=self.model,
                call_type="sql_generation",
                context=context,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
                attempt=attempt,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            raise AISQLGenerationError(
                f"DIAL API call failed for {context} "
                f"on attempt {attempt}/{MAX_GENERATION_ATTEMPTS}: {exc}"
            ) from exc

    def _call_claude(self, messages: list, context: str, attempt: int) -> str:
        """
        Call Anthropic Claude directly.
        Converts OpenAI-style messages list to Anthropic format.
        """
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise AISQLGenerationError(
                "The 'anthropic' package is required for Claude direct SQL generation.\n"
                "Install it with: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)

        # Extract system prompt and conversation messages
        system_content = ""
        conv_messages  = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                conv_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_content,
                messages=conv_messages,
            )
        except Exception as exc:
            raise AISQLGenerationError(
                f"Claude API call failed for {context} "
                f"on attempt {attempt}/{MAX_GENERATION_ATTEMPTS}: {exc}"
            ) from exc

        usage = extract_anthropic_usage(response)
        log_usage(
            backend="claude",
            model=self.model,
            call_type="sql_generation",
            context=context,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            attempt=attempt,
        )

        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw += block.text

        # Strip markdown fences if Claude wrapped SQL in ```sql ... ```
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw   = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()
        return raw

    # -----------------------------------------------------------------------
    # Prompt builders
    # -----------------------------------------------------------------------

    def _build_system_prompt(self, source_db: str, target_db: str) -> str:
        """Build the system prompt with database-specific instructions."""
        return f"""You are an expert SQL Query Generator specializing in data migration validation.

Your task: Generate database-specific SQL queries for validation between {source_db.upper()} (source) and {target_db.upper()} (target).

## Database-Specific Syntax Rules:

### MS SQL Server (MSSQL)
- NO TEXT data type — use VARCHAR(MAX) for text casting
- NO TO_CHAR function — use FORMAT() or CONVERT()
- Integers: CAST(column AS VARCHAR(MAX)) NOT CAST(column AS TEXT)
- Timestamps: FORMAT(column, 'yyyy-MM-dd HH:mm:ss')
- Booleans: Use BIT type (0/1), no true/false keywords
- String trimming: LTRIM(RTRIM(column))
- NULL coalesce: COALESCE(CAST(column AS VARCHAR(MAX)), '<<NULL>>')

### PostgreSQL
- Use TEXT for text casting: CAST(column AS TEXT)
- Use TO_CHAR for formatting: TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS')
- Booleans: true/false keywords supported
- String trimming: TRIM(column)
- NULL coalesce: COALESCE(CAST(column AS TEXT), '<<NULL>>')

### Snowflake
- Use STRING for text casting: CAST(column AS STRING)
- Use TO_VARCHAR for formatting: TO_VARCHAR(column, 'YYYY-MM-DD HH24:MI:SS')
- Booleans: TRUE/FALSE keywords
- String trimming: TRIM(column)
- NULL coalesce: COALESCE(CAST(column AS STRING), '<<NULL>>')

### Athena/Trino/Presto
- Use VARCHAR for text casting: CAST(column AS VARCHAR)
- Use date_format for formatting: date_format(column, '%Y-%m-%d %H:%i:%s')
- String trimming: TRIM(column)
- NULL coalesce: COALESCE(CAST(column AS VARCHAR), '<<NULL>>')

## Critical Requirements:
1. ALWAYS use database-specific syntax — never mix dialects.
2. Treat every source→target type pair as a compatibility decision; choose a cast
    legal in the source dialect that produces a comparable value.
3. Preserve numeric precision and scale before text conversion; use intermediate
    conversions for types that cannot be cast directly.
4. Normalize timezone semantics before formatting timestamps; do not silently drop offsets.
5. Handle NULL, empty strings, booleans, JSON/JSONB, binary, UUID, date, numeric,
    and character padding explicitly according to the source dialect.
6. ALL normalized data-validation columns MUST have COALESCE(..., '<<NULL>>').
7. Text casts MUST match the database type (VARCHAR(MAX) for MSSQL, TEXT for PG, STRING for SF).
8. Format functions MUST match the database (FORMAT for MSSQL, TO_CHAR for PG, TO_VARCHAR for SF).
9. Use commas between every SELECT expression and preserve requested aliases exactly.
10. Include the target active-record filter when requested; never mix source and target syntax.
11. Return ONLY the SQL query — no markdown, no explanation.
12. Mentally validate the complete query as executable SQL before returning it.

## NULL Handling (CRITICAL):
- Source query: COALESCE(CAST(expression AS <database_text_type>), '<<NULL>>')
- Target query: COALESCE(CAST(expression AS STRING), '<<NULL>>')

## Output Format:
Return plain SQL query only. No markdown, no comments, no explanations outside the SQL.
"""

    def _build_user_prompt(
        self,
        schema: str,
        table: str,
        mappings: List[ColumnRuleMapping],
        source_db_type: str,
        query_type: str,
        has_fivetran_active: bool,
    ) -> str:
        """Build the user prompt with column details."""
        columns_json = [
            {
                "source_column": m.source_column,
                "target_column": m.target_column,
                "source_type": m.source_type,
                "target_type": m.target_type,
                "rule": m.rule.rule_name,
            }
            for m in mappings if not m.skip_validation
        ]

        fivetran_note = ""
        if has_fivetran_active and query_type == "data_validation":
            fivetran_note = "\nTarget query MUST include: WHERE _FIVETRAN_ACTIVE = TRUE"

        query_descriptions = {
            "data_validation": "SELECT normalized columns for row-by-row comparison",
            "null_pct": "SELECT NULL percentage per column: ROUND(100.0 * SUM(CASE WHEN col IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2)",
            "distinct_count": "SELECT distinct value count per column: COUNT(DISTINCT col)",
        }

        compatibility = self._build_compatibility_matrix(mappings, source_db_type, "snowflake")
        return f"""Generate {source_db_type.upper()} SQL query for: {schema}.{table}

Query Type: {query_descriptions.get(query_type, query_type)}

Columns to process ({len(columns_json)} total):
{json.dumps(columns_json, indent=2)}

Source → target compatibility decisions:
{compatibility}

Requirements:
1. Use {source_db_type.upper()}-specific syntax
2. Apply proper data type conversions per column rule
3. ALL columns MUST be wrapped: COALESCE(CAST(expression AS {self._get_text_type(source_db_type)}), '<<NULL>>')
4. Integers: CAST(col AS {self._get_text_type(source_db_type)})
5. Timestamps: {self._get_format_function(source_db_type)}
6. Booleans: CASE WHEN col = {self._get_bool_true(source_db_type)} THEN '1' WHEN col = {self._get_bool_false(source_db_type)} THEN '0' ELSE NULL END
7. Each normalized column should have alias: column_name_normalized{fivetran_note}

Generate the complete SELECT query now:
"""

    def _build_compatibility_matrix(
        self,
        mappings: List[ColumnRuleMapping],
        source_db_type: str,
        target_db_type: str,
    ) -> str:
        """Give the model explicit per-column castability context."""
        lines = []
        for mapping in mappings:
            if mapping.skip_validation:
                continue
            lines.append(
                f"- {mapping.source_column} -> {mapping.target_column}: "
                f"{mapping.source_type.upper()} -> {mapping.target_type.upper()}; "
                f"rule={mapping.rule.rule_name}; "
                f"source_text_cast={self._get_text_type(source_db_type)}; "
                f"target_text_cast={self._get_text_type(target_db_type)}"
            )
        return "\n".join(lines) or "- No comparable columns"

    def _get_text_type(self, db_type: str) -> str:
        """Get the text data type for the database."""
        db = db_type.lower()
        if db in ("mssql", "sqlserver", "sql_server"):
            return "VARCHAR(MAX)"
        elif db in ("postgres", "postgresql"):
            return "TEXT"
        elif db in ("snowflake",):
            return "STRING"
        elif db in ("athena", "trino", "presto"):
            return "VARCHAR"
        return "TEXT"

    def _get_format_function(self, db_type: str) -> str:
        """Get the timestamp format function for the database."""
        db = db_type.lower()
        if db in ("mssql", "sqlserver", "sql_server"):
            return "FORMAT(col, 'yyyy-MM-dd HH:mm:ss')"
        elif db in ("postgres", "postgresql"):
            return "TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS')"
        elif db in ("snowflake",):
            return "TO_VARCHAR(col, 'YYYY-MM-DD HH24:MI:SS')"
        elif db in ("athena", "trino", "presto"):
            return "date_format(col, '%Y-%m-%d %H:%i:%s')"
        return "TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS')"

    def _get_bool_true(self, db_type: str) -> str:
        """Get the boolean TRUE value for the database."""
        db = db_type.lower()
        if db in ("mssql", "sqlserver", "sql_server"):
            return "1"
        return "true"

    def _get_bool_false(self, db_type: str) -> str:
        """Get the boolean FALSE value for the database."""
        db = db_type.lower()
        if db in ("mssql", "sqlserver", "sql_server"):
            return "0"
        return "false"

    def _parse_response(
        self,
        raw: str,
        db_type: str,
        mappings: Optional[List[ColumnRuleMapping]] = None,
        query_type: str = "data_validation",
    ) -> AIGeneratedQuery:
        """Parse AI response and extract the query."""
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove first line (```sql or ```) and last line (```)
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[1:-1])
            else:
                cleaned = "\n".join(lines[1:])
            cleaned = cleaned.strip()

        warnings = self._validate_generated_query(cleaned, db_type, mappings or [], query_type)

        return AIGeneratedQuery(
            query=cleaned,
            database_type=db_type,
            explanation=f"AI-generated query for {db_type}",
            confidence=0.95 if not warnings else 0.7,
            warnings=warnings,
        )

    def _validate_generated_query(
        self,
        query: str,
        db_type: str,
        mappings: List[ColumnRuleMapping],
        query_type: str,
    ) -> List[str]:
        """Reject unsafe or incomplete AI output before it reaches SQL files."""
        warnings = []
        upper = query.upper()
        dialect = db_type.lower().replace("server", "")
        if not re.search(r"\bSELECT\b", upper) or not re.search(r"\bFROM\b", upper):
            warnings.append("AI response is not a complete SELECT query")
        if dialect in {"mssql", "sqlserver"}:
            forbidden = {
                r"::\s*[A-Z_]": "PostgreSQL cast operator (::)",
                r"\bTO_CHAR\s*\(": "PostgreSQL TO_CHAR",
                r"\bAS\s+TEXT\b": "PostgreSQL TEXT cast",
                r"\bJSONB\b": "PostgreSQL JSONB",
                r"\bENCODE\s*\(": "PostgreSQL encode()",
            }
            for pattern, label in forbidden.items():
                if re.search(pattern, upper):
                    warnings.append(f"MSSQL query contains {label}")
        if query_type == "data_validation":
            if "<<NULL>>" not in query:
                warnings.append("Data-validation query is missing the NULL placeholder")
            for mapping in mappings:
                if mapping.skip_validation:
                    continue
                alias = f"{mapping.source_column}_normalized".upper()
                if alias not in upper:
                    warnings.append(f"Missing required alias {mapping.source_column}_normalized")
            # Detect adjacent SELECT function expressions without a comma.
            if re.search(r"\)\s+\w+\s*\(", query, re.I):
                warnings.append("SELECT expressions may be missing commas")
        return warnings
