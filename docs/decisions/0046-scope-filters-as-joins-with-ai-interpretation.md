# 0046. Scope filters are written as JOINs; AI can read prose rows, and a human always edits the result

**Status:** Accepted — implemented
**Date:** 2026-09-29
**Changes:** [0045](0045-scope-filters-implemented-bronze-and-silver.md) (rendering and editor; the active/current rules are unchanged). Partly reverses [0042](0042-declarative-scope-filter-spec-rendered-per-layer.md) §3 ("a semi-join, never a JOIN") and [0044](0044-ai-for-filter-interpretation-deferred.md) ("no AI now").

## Context

0045 rendered each workbook row as nested `IN (SELECT …)` subqueries. The
generated `CONTACTS.yaml` showed the result: correct rows, but not what the
lead expected. The lead expects the **JOINs** the test team wrote in the
sheet (`FROM contacts j JOIN tenants t ON … JOIN facilities f ON … WHERE …`).

The user also asked for three more things:

- Where a cell is plain English and not SQL, AI should work out the tables,
  columns and join conditions. Use the current key (EPAM DIAL) now and the
  Claude key later.
- The join conditions must stay editable.
- People must be able to add to or finish a filter the workbook leaves
  incomplete.

## Decision

### 1. Silver uses real JOINs on both sides

`scope_filter.render_joins()` turns each join step into
`JOIN <table> <alias> ON <previous>.<col> = <alias>.<key>`, keeping the
sheet's aliases (`t`, `f`). Every joined table's conditions and active-row
predicate go in the `WHERE`.

- `render_silver()` produces JOINs and a WHERE clause for both sides:
  - Bronze side: `<PARENT>` tables, each with `_FIVETRAN_ACTIVE = TRUE`.
  - Silver side: `INT_<PARENT>` tables, each with `IS_CURRENT = TRUE`.
- The JOIN text is stored in `population_scope["scope_joins"]`, so it is
  visible in the YAML. `silver_sql_emitter` appends it after the `FROM`.
- Once a table is joined in, every Silver column is qualified
  (`"INT_CONTACTS"."TENANT_ID"`). Otherwise `INT_TENANTS.TENANT_ID` and
  `IS_CURRENT` would be ambiguous.
- The active filter still goes in `QUALIFY` after the `SYS_VERSION` window
  (0045). The JOINs don't change the window: each base row matches at most
  one active or current parent row.
- If a multisource Coalesce join already ends in a `WHERE`, JOINs can't be
  appended after it. The emitter raises an error for that node rather than
  building invalid SQL.

**Duplicate rows.** A JOIN can duplicate base rows if a parent has more than
one matching row. That's the reason 0042 chose `IN`. It's safe here only
because every joined table is filtered to its single active (Bronze) or
current (Silver) row, which is exactly what the sheet's own Snowflake
queries do. If a Silver parent has several `IS_CURRENT` rows per key, the
run will show duplicate rows (a Uniqueness failure) instead of hiding them.

### 2. Bronze keeps the JOINs inside one subquery

The Bronze data SELECTs are written by the AI generator, with unqualified
column names (`FROM public.contacts`, `id`, `created_at`). Adding
`JOIN tenants t` to that outer `FROM` would make `id` and `created_at`
ambiguous. So on Bronze, the sheet's JOIN chain goes inside one subquery:

```sql
tenant_id IN (SELECT t.id FROM public.tenants t
              JOIN public.facilities f ON t.facility_id = f.id
              WHERE f.company_id IN (…))
```

On the Snowflake side, each joined table also gets
`t._FIVETRAN_ACTIVE = TRUE AND f._FIVETRAN_ACTIVE = TRUE`. It selects the
same rows as the sheet's query, and a JOIN in Bronze's outer `FROM` would
break the queries. Making that possible would mean changing how the AI
generator qualifies columns. That is out of scope here.

### 3. AI for prose rows, only as a starting point for the editor

`AISQLQueryGenerator.interpret_scope_filter(table, text, known_values, columns)`:

- **Backend:** it uses the same `_call_ai()` and the same backend selection
  as everything else. `DIAL_API_KEY` is tried first; when only
  `CLAUDE_API_KEY` is set, it uses Claude with no code change. The UI shows
  which backend will run.
- **What it returns:** exactly one sheet-shaped statement
  (`SELECT COUNT(*) FROM t j JOIN … ON … WHERE …`), or `FULL`, or `UNCLEAR`.
- **What it sees:** the Codes-sheet values and the table's known column
  names, so it doesn't invent columns or values.
- **Safety:** its output goes through the same `parse_filter_text()` as a
  sheet cell. If the AI answers about the wrong table, uses an unknown
  alias, or leaves a quote unbalanced, the row is blocked. The keyword
  guard and the "never raise" behaviour are the same as
  `explain_and_derive_filter()`.
- **Review:** the result only fills the editor, noted as "interpreted by AI
  (model) — review before use". The AI never writes the SQL for each layer.
  Rendering stays deterministic.
- **When it runs:** only when someone clicks **✨ Interpret with AI**, never
  on its own. SQL cells don't need it. The deterministic prose parser still
  handles the SiteLink phrasing without AI.

### 4. Editor

`edit_scope_filter()` has one grid row per JOIN, with these columns:
**JOIN table · Alias · ON: column of previous table · = column of this table
· WHERE conditions on this table**. There is also a WHERE field for the
table itself and a read-only preview of the whole filter as one SQL
statement (`to_sql()`).

- Rows can be added, so an incomplete workbook row can be finished by hand.
- Duplicate aliases, or an alias equal to the base table's name, block the
  row.

## Alternatives considered

- **Keep `IN` subqueries everywhere (0045).** Correct, but not what the lead
  wants to review.
- **Real JOINs in Bronze's outer `FROM`.** Needs every AI-generated column
  qualified. Too big a change for this step.
- **AI writes the per-layer SQL directly.** Rejected, as in 0044: that would
  give no stable spec to review, a different dialect per side, and
  unpredictable SQL in a validator.
- **Run AI on every prose row automatically on upload.** Rejected: 28 calls
  per SiteLink workbook, and most rows don't need it.

## Consequences

- The Silver YAML now reads like the sheet: `FROM … JOIN … ON … WHERE …`.
- `CONTACTS.yaml` was generated with the 0045 `IN` form. Regenerate it to
  get the JOINs.
- The live AI check could not be done this session: the DIAL call timed out
  (DIAL usually needs the EPAM VPN). The AI path is covered by tests of the
  parser-side handling (`test_ai_rewrite_goes_through_the_parser`), not by a
  real model answer yet.
