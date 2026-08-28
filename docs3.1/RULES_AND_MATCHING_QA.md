# Migration Validator — Rules & Column Matching: Q&A

Prepared for delivery-manager review. Focused on the two things most likely
to be probed in detail: the **type-normalization rule system** and **column
matching (exact / fuzzy / AI)** — plus how to extend both safely.

---

## 1. Architecture & file layout

**Q: Where does the actual rule logic live?**
A: All rule classes live in one file — `src/rules/postgres_base_rules.py`.
`src/rules/mssql_rules.py`, `athena_rules.py`, and `snowflake_rules.py` are
**pure re-export shims** — they define no classes of their own, they just
`import` the same classes from `postgres_base_rules.py` so callers can write
`from rules.athena_rules import BooleanRule` for readability.

**Q: Why one file instead of one per database?**
A: Every rule already handles all 4 dialects internally — each rule class has
sibling methods `_pg_expression()`, `_ms_expression()`, `_athena_expression()`,
`_sf_expression()`. Since MSSQL/Athena/Snowflake extractors normalize their
native type names to PostgreSQL-compatible names *before* rule lookup happens,
one registry and one set of classes covers all four dialects — there's no
technical reason to fork the class hierarchy per database, and doing so would
mean four places to fix the same bug instead of one.

**Q: Doesn't writing rules in "postgres_base_rules.py" mean MSSQL/Athena support
is an afterthought — couldn't a rule silently skip one of them?**
A: It used to be *possible* to skip one, which is exactly why this was
tightened: `_ms_expression()`/`_athena_expression()` originally had a default
body that silently fell back to `_pg_expression()` if a rule author forgot to
override them. That's now a hard requirement — `BaseValidationRule` declares
all four dialect methods (`_pg_expression`, `_ms_expression`,
`_athena_expression`, `_sf_expression`) as `@abstractmethod`, so Python
refuses to instantiate any rule missing one of them, at class-definition time
— no source database is optional. Tightening this caught a real, previously
silent bug: `IntegerRule` had never overridden `_athena_expression()` and was
silently emitting `CAST(col AS TEXT)` for Athena — which isn't valid
Trino/Presto syntax (Athena uses `VARCHAR`, not `TEXT`) — so any INTEGER/
BIGINT/SERIAL column validated from an Athena source would have failed at
query-execution time. Fixed by adding the missing `_athena_expression()`
override (`CAST(col AS VARCHAR)`), matching every sibling rule's pattern.

**Q: What is the "rule registry"?**
A: `RuleRegistry` (in `postgres_base_rules.py`, assembled in `src/rules/__init__.py`)
maps `(source_type, target_type)` pairs to a rule instance. `RuleRegistry.lookup()`
does a **first-match linear scan** — order matters. `TextRule`'s wildcard
`("*", "*")` trigger pair is always registered **last** as the catch-all
fallback for any type pair nothing else claims.

**Q: What are the actual rule classes?**
A: `BooleanRule`, `IntegerRule`, `NumericRule`, `TimestampTZRule`,
`TimestampNTZRule`, `DateRule`, `TextRule` (wildcard/default), `UUIDRule`,
`JSONRule`, `ByteaRule`, `HStoreRule`, `NullPlaceholderRule` — all extending
the abstract `BaseValidationRule`.

---

## 2. How normalization actually works

**Q: What does a rule actually produce?**
A: A SQL expression string for one column, wrapped so it's comparable across
dialects. Every expression is wrapped in
`COALESCE(CAST(expr AS <dialect-text-type>), '<<NULL>>')` — `TEXT` on
PostgreSQL, `VARCHAR(MAX)` on MSSQL, `VARCHAR` on Athena/Trino, `STRING` on
Snowflake. The `<<NULL>>` sentinel means a NULL on both sides compares equal
instead of silently failing `NULL = NULL` (which is never true in SQL).

**Q: Real example — what does a rule class look like?**
```python
class TimestampTZRule(BaseValidationRule):
    @property
    def trigger_pairs(self):
        return [("TIMESTAMP_TZ", "TIMESTAMP_TZ"),
                ("TIMESTAMP_TZ", "TIMESTAMPTZ"),
                ("TIMESTAMPTZ",  "TIMESTAMP_TZ")]

    def _pg_expression(self, col):
        return f"TO_CHAR({col} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

    def _sf_expression(self, col):
        return f"TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col}), 'YYYY-MM-DD HH24:MI:SS')"

    def _ms_expression(self, col):
        return f"FORMAT({col} AT TIME ZONE 'UTC', 'yyyy-MM-dd HH:mm:ss')"

    def _athena_expression(self, col):
        return f"date_format(at_timezone({col}, 'UTC'), '%Y-%m-%d %H:%i:%s')"
```
Every timestamp, regardless of source dialect, is converted to UTC and
formatted identically before comparison — that's what makes cross-dialect
comparison valid at all.

**Q: What's `rules_catalog.json` for, if the logic is in Python classes?**
A: It's a machine-readable metadata mirror of the same rules (name,
description, example) used to (a) print the rule book via `validate_cli.py rules`
and (b) get injected into AI prompts so the model knows what rules exist. It
does **not** drive SQL generation — that's always the Python registry.

---

## 3. How to add a NEW base rule

**Q: Walk me through adding a brand-new rule (e.g. a new data type).**
A: Two files, in order:

1. **`src/rules/postgres_base_rules.py`** — add a class extending
   `BaseValidationRule`, implementing:
   - `rule_name` (unique snake_case id)
   - `description`
   - `trigger_pairs` — list of `(source_type, target_type)` tuples
   - `_pg_expression(col)` and `_sf_expression(col)` — **required**, abstract
   - `_ms_expression(col)` / `_athena_expression(col)` — optional; default to
     calling `_pg_expression()` if the dialect syntax is actually the same

2. **`src/rules/__init__.py`** — import the class, then
   `_registry.register(YourRule())` **before** `_registry.register(TextRule())`
   (the wildcard must stay last).

3. *(Recommended, not required for SQL generation)* Add a matching entry to
   `src/rules_catalog.json` so it shows up in `validate_cli.py rules` and gets
   included in AI prompt context.

No other wiring needed — `get_rule_for_type()` (the function everything else
calls) reads straight from the registry, so the moment it's registered it's
live for every future `generate`/`batch` run.

**Q: I need a rule specifically for Athena — where does it go?**
A: **Never in `athena_rules.py`** — that file only re-exports, it never
defines classes the registry can see. Two real scenarios:

- **New type-pair on an existing rule** (the common case): just add a tuple to
  that rule's `trigger_pairs` in `postgres_base_rules.py`, e.g.
  `("TINYINT", "NUMBER")` on `IntegerRule`. If `_athena_expression()` is
  already correct for that rule, you're done — no new class needed.
- **Genuinely new Athena-only type** (e.g. Trino `MAP`/`ARRAY`/`STRUCT`, which
  PG/MSSQL/Snowflake have no equivalent for): still a new class in
  `postgres_base_rules.py`, but scope `trigger_pairs` so only Athena's
  normalized type name matches, implement real logic in `_athena_expression()`,
  and the other three dialect methods simply never get triggered for that pair.

---

## 4. Changing an EXISTING base rule

**Q: How do I fix or change what an existing rule generates (e.g. change
rounding from 2 decimal places to 4)?**
A: Edit the class directly in `postgres_base_rules.py`. This is the **only**
mechanism — there is deliberately no runtime override/config path for this,
because `rule_book.py` explicitly documents: *"Base rules are IMMUTABLE — do
not edit at runtime… no custom SQL injection at runtime, to keep queries safe
and auditable."* Since it's a single source of truth, the change should go
through normal code review — it affects every table using that type pair,
across every source database.

**Q: What if I don't want to touch code — is there a lighter-weight override?**
A: Yes, but it's a **different mechanism for a different purpose** — see
next section. It augments the AI's judgement; it does not override the
deterministic SQL the registry produces.

---

## 5. Base rule vs. "learned"/custom rule — when to use which

**Q: What's `add-rule` (menu `[6]`) actually for?**
A: It appends a `RuleEntry` to `src/rule_book_learned.json` — free-text
metadata (`display_name`, `description`, `when_to_apply`, SQL template
snippets, source/target type). It requires **no code change and no PR** —
just run `python validate_cli.py add-rule` interactively.

**Q: Does a learned rule change generated SQL like a base rule does?**
A: No. It's injected into AI prompt context (so the model is *told about* the
convention) but the deterministic SQL generator still only ever calls the
matching built-in Python rule from the registry. This is a deliberate safety
boundary — `rule_book.py` states user-entered rule text is never used to
inject SQL at runtime.

**Q: So when do I use which?**

| Situation | Use |
|---|---|
| A transformation must be **guaranteed** to run identically for every table matching a type pair, forever | Base rule — code + PR |
| A one-off, table/column-specific business convention discovered mid-migration (e.g. "this phone column needs dash-stripping before compare") that should bias the AI's judgement without a deploy | Learned/custom rule — `add-rule`, no code change |

---

## 6. Column matching — exact, fuzzy, AI

**Q: Walk me through how a column gets matched, end to end.**
A: Three deterministic stages run first, in `src/matching/`, before any AI
call:
1. **`ExactMatcher`** — case-insensitive + normalized-name exact match.
   `normalize_column_name()` lowercases and strips all non-alphanumeric
   characters, so `created_at` / `CREATED_AT` / `createdAt` all normalize to
   `createdat` and match.
2. **`FuzzyMatcher`** — for anything not exactly matched, returns the top 5
   candidate target columns using RapidFuzz string similarity (with a
   `difflib` fallback if RapidFuzz isn't installed).
3. **`ConfidenceScorer`** — combines name similarity, type compatibility,
   column position proximity, and a bonus for previously "learned" match
   examples into one score.

**Q: What decides whether AI gets involved?**
A: The confidence score, with three bands:
- **≥ 0.95** → auto-accepted, no AI call.
- **0.75 – 0.95** → sent to AI (`ai_needed`).
- **< 0.75** → left unmatched (reported, not silently dropped).

**Q: Why send only *some* columns to AI instead of the whole schema?**
A: Deliberate token-efficiency design, stated directly in
`rule_planner.py`'s own docstring: *"Receives ONLY the columns with
status='ai_needed'… Sends ONE column per AI call… AI receives only top N
candidates — never the entire target schema."* Exact and confidently-fuzzy
matches never cost a single AI token.

**Q: Does AI ever see real data?**
A: No. Confirmed directly in code comments/docstrings across `rule_planner.py`,
`prompt_builder.py`, and `ai_sql_generator.py`: only column **names, types,
and positions** are sent — never row values, never credentials. The AI's job
is column-name disambiguation and SQL *text* generation, not data inspection.

**Q: What if AI is unavailable (no API key)?**
A: For SQL generation, there is **no fallback** — `AISQLGenerationError` is
raised deliberately, because a silently degraded query "looks authoritative
but was never trustworthy" (direct quote from `ai_sql_generator.py`). For
column mapping specifically, `RulePlanner` falls back to accepting the best
fuzzy candidate with a logged warning, rather than blocking entirely.

---

## 7. Anticipated follow-ups

**Q: What happens if two columns are equally good fuzzy matches?**
A: `ConfidenceScorer` breaks ties using type compatibility and column
position proximity as secondary signals; if still ambiguous, it falls into
the AI band rather than guessing.

**Q: Can the AI ever choose a target column that doesn't exist?**
A: No — `RulePlanner` validates the AI's chosen column name against the
candidate list it was given; if the AI names something outside that list,
it's treated as an error and the code falls back to best fuzzy, with the
incident logged.

**Q: Is fuzzy matching case-sensitive or does it care about underscores vs
camelCase?**
A: No — the same `normalize_column_name()` used for exact matching is applied
before fuzzy comparison too, so naming-convention differences alone don't
lower the match confidence.

**Q: How do exclusions interact with matching/rules?**
A: Exclusions are applied **before** matching starts (Step 2 of the
pipeline), so excluded columns never reach the matcher or a rule at all —
they're removed from consideration and reported separately with a reason,
alongside a coverage percentage on every run.
