---
name: silver-layer-coalesce-validation
description: "Use when validating a Silver-layer Coalesce node against its Bronze source in Snowflake. Covers fetching a Coalesce node's metadata, classifying its columns (passthrough / recomputable expression / macro-skip / non-deterministic-existence-only), resolving surrogate business keys, multisource (multi-table-join) nodes, and turning the result into a CanonicalValidationPlan + SchemaDiff, then emitting Silver's own SQL (Coalesce transform verbatim, wrapped in the Snowflake NULL placeholder, not routed through base_rules.py) and multi-line Silver YAML. Also covers multi-source UNION nodes (ADR 0049), the workbook scope filters as JOINs, and the always-on _FIVETRAN_ACTIVE / IS_CURRENT filters (ADR 0045-0047). Files: coalesce_client.py, coalesce_plan_builder.py, silver_sql_emitter.py. See docs/decisions/0013-0027."
---

# Silver-Layer Validation via Coalesce Metadata

Silver validation is recompute-and-diff, not select-and-diff (ADR 0013):
rebuild the SELECT Coalesce's own metadata says the node runs, execute it
against the Bronze table already in Snowflake, and diff that against a
plain `SELECT *` on the materialized Silver table. Both sides are
Snowflake — there is no source-DB connector involved and no fuzzy/AI column
matching, unlike bronze validation.

## Files

- `src/connector/coalesce_client.py` — thin, read-only Coalesce REST client.
  `get_node(workspace_id: str, node_id: str) -> dict`. Env: `COALESCE_API_TOKEN`,
  `COALESCE_WORKSPACE_ID`, `COALESCE_API_BASE` (optional). Raises
  `CoalesceNotConfiguredError` / `CoalesceError`.
- `src/silver/coalesce_plan_builder.py` — the extractor.
  - `build_plan(node_id: str, workspace_id: Optional[str] = None) -> Tuple[CanonicalValidationPlan, SchemaDiff]`
    is the main entrypoint (signature/return shape unchanged by ADR
    0018/0019 — still single-node, called once per UI interaction). It is a
    thin wrapper: fetch via `coalesce_client.get_node()`, then call
    `build_plan_from_metadata(node: dict, workspace_id=None)` (ADR 0021) —
    the shared post-fetch logic, also the entrypoint for a caller who
    already has the node JSON (e.g. pasted from a manual curl call because
    they lack Snowflake access to run the live fetch/diff yet). Raises
    `UnsupportedNodeShapeError` only for `overrideSQL`, `customSQL`, an empty
    `sourceMapping`, or a `sourceMapping` entry with zero `dependencies`
    (ADR 0014 section 6) — hard-stop, never guess. `isMultisource=true` and
    more than one `sourceMapping`/`dependencies` entry are now **supported**
    (ADR 0019 1) — see "Multisource nodes" below.
  - `SchemaDiff` fields: `only_in_metadata`, `only_in_bronze_live`,
    `only_in_silver_live` (all `List[str]`) — declared metadata columns vs.
    live Snowflake columns on both sides, via
    `sql_extractor.extractors.ExtractorFactory`. For multisource nodes the
    Bronze-live side is the *union* of every dependency table's live columns
    (ADR 0019 5), not just the first one. `unavailable_reason: Optional[str]`
    (ADR 0021) is set instead of raising when the live diff query itself
    fails (e.g. no Snowflake grant yet on the Bronze database) — the three
    lists stay empty in that case; treat that as "not checked", never as
    "confirmed clean".
  - The 4-bucket column classification (ADR 0014 section 3) happens in
    `_classify_column()`: passthrough, recomputable expression, macro-skip
    (`skip_validation=True`), non-deterministic-existence-only
    (`skip_validation=True`, `validation_rules=["null_check"]`).
  - Surrogate-key PK fallback (ADR 0014 section 5, deferred per ADR 0015):
    when the business key is macro-computed, `source_primary_keys`/
    `target_primary_keys` are left empty, `plan.requires_review = True`,
    and the natural-key candidate columns are written to
    `plan.population_scope["natural_key_candidates"]` — not a third return
    value; `CanonicalValidationPlan` already has this free-form dict field.
  - `write_schema_diff_exclusion(db_type: str, column: str, reason: str) -> bool`
    — thin wrapper over `validate_cli.save_global_user_exclusion()`; does
    not duplicate the write logic.
- `src/silver/silver_sql_emitter.py` — Silver's own SQL emitter (ADR 0018,
  amended by 0023 and 0027).
  - `emit_query_set(plan: CanonicalValidationPlan) -> ValidationQuerySet` is
    the only entrypoint. It does **not** call `sql_query_generator.py` /
    `ai_sql_generator.py` / `rules/base_rules.py` — no per-type-pair rule
    dispatch, no dialect switching. It **does** wrap every selected column,
    on both the Bronze-recompute and Silver sides, in the single Snowflake
    placeholder `COALESCE(CAST(<expr> AS STRING), '<<NULL>>')` (ADR 0027 —
    inlined as one constant, not imported from `base_rules.py`). Per column:
    passthrough/recomputable → the verbatim column reference or Coalesce
    transform expression, wrapped, `AS "<target>"`; macro-skip,
    non-deterministic and other skipped columns → **omitted from the SELECT
    with no comment of any kind** (ADR 0023 — `--` comments broke flattened
    SQL, `/* */` was rejected by the user). Macro-skip column names survive
    only in `plan.review_reasons`. The existence/NOT-NULL-only check for
    non-deterministic columns uses the existing
    `skip_validation`/`validation_rules=["null_check"]` mechanism.
  - The returned `ValidationQuerySet` feeds `YAMLConfigWriter.write_from_plan(..., layer="silver")`,
    which keeps Silver SQL **multi-line** (`_to_indented_multiline()`);
    Bronze stays single-line (`_to_single_line()`) — ADR 0023.
  - The generated YAML carries the full `validation_plan:` block, same shape
    as Bronze (ADR 0026) — not Silver-specific scope creep.
  - `resolve_ref_macro(text: str, bronze_schema: str) -> str` — resolves every
    Coalesce `{{ ref('LOCATION', 'NODE') }}` macro to
    `"LOCATION"."bronze_schema"."NODE"`, everything else in `text` passes
    through verbatim. Shared by `coalesce_plan_builder.py` (multisource
    `bronze_join_sql` construction) — implemented once, not twice (ADR 0019).
  - `QueryOutputManager.generate_from_plan(plan, layer="silver")` already
    branches on `layer == "silver"` to call `emit_query_set(plan)` instead of
    `SQLQueryGenerator().generate_from_plan(plan)` (owned by
    `validation-query-yaml-generator`).

## Multisource nodes (ADR 0019)

A Silver node's Bronze side can be a join across 2-3 Bronze tables (from the
same or different source DBs). `sourceMapping` may have N entries, each with
N `dependencies`; alias resolution (`_resolve_reference`) already worked
across every alias without change, it was only ever blocked by the
now-removed shape hard-stop. The Bronze recompute query's `FROM`/`JOIN`
clause is the **verbatim** concatenation of each `sourceMapping[].join.joinCondition`
string (macro-resolved via `resolve_ref_macro`), stored in
`plan.population_scope["bronze_join_sql"]` — no `RelationshipSpec`, no
parsing Coalesce's own join SQL into a structured join spec. Single-source
nodes (one `sourceMapping` entry, one dependency — the common case) don't
set this key at all; `silver_sql_emitter.py` falls back to the plain
single-table `FROM "db"."schema"."table"` it always built.

## Known limitation carried forward, not silently hidden

Recomputable-expression columns (bucket 2) store the raw Coalesce SQL
expression (e.g. a `ROW_NUMBER() OVER (...)` window function) verbatim in
`ColumnMappingEntry.source_column`, and `silver_sql_emitter.py` emits it
verbatim too (ADR 0018) — the previous concern ("`ai_sql_generator.py`'s rule
templates assume a bare identifier") no longer applies to Silver, since
Silver never calls `ai_sql_generator.py` at all now. That concern still
stands for **Bronze**, which does route through `ai_sql_generator.py` — not
this skill's problem to fix.

## Open items (documented, don't silently "fix")

- **History rows are not validated.** Since ADR 0045, Silver compares only
  current rows (Bronze `_FIVETRAN_ACTIVE`, Silver `IS_CURRENT`). A Silver
  table without `IS_CURRENT` gets a `review_reasons` entry, because only its
  Bronze side can be filtered.
- **Macro resolution** (`{{ ids_to_surrogate_key(...) }}`) — still no
  interpreter (ADR 0014 §3, 0023). Macro columns are omitted, not guessed.
- **Silver YAMLs generated before ADR 0022/0023** (e.g. `LEADS.yaml`) have
  truncated/commented SQL and must be regenerated.

## Config naming (ADR 0047)

Silver YAMLs are `INT_<table>.yaml`. The same `INT_<table>` is the `tables:`
key and the count_validation key (`yaml_config_writer.config_table_name()`).
The runner finds a table by file stem and then by key, so never rename just
one of them.

## Scope filters and active-row filters (ADR 0045)

- `emit_query_set()` always filters Bronze `_FIVETRAN_ACTIVE = TRUE` on
  every table read. Multisource joined tables use `COALESCE(.., TRUE)`.
  When the SELECT has a window function (`SYS_VERSION`), that filter goes in
  `QUALIFY`, never in `WHERE`, because a `WHERE` would reset every
  `SYS_VERSION` to 1.
- The Silver side filters `"IS_CURRENT" = TRUE` when the column exists.
- Workbook filters are **JOINs** (ADR 0046):
  `population_scope["scope_joins"] = {bronze, silver}` is appended after
  the FROM. Silver columns are qualified with the target table once joins
  exist. The WHERE parts arrive as
  `plan.target_filter` (Silver), rendered by
  `scope_filter.render_silver(spec, plan)`:
  - Parents are Bronze `<PARENT>` on one side and `INT_<PARENT>` with
    `IS_CURRENT` on the other.
  - Base-table columns come from the plan's passthrough mappings.
  - A parent's `ID` becomes `<SINGULAR>_ID` (`silver_parent_column()`),
    checked against live columns in the UI.
  - A missing column blocks generation (never filter one side only).
- The spec, with its workbook/sheet/row, goes into
  `population_scope["scope_filter"]`. It is `{mode: none}` when no row
  matched.

## Multi-source (UNION) nodes (ADR 0049)

Several `sourceMapping` entries that each start with `FROM` = several Bronze
sources combined into one Silver table. Each mapping is its own branch
(`population_scope["union_branches"]`: name, resolved FROM SQL, per-column
expression from `sources[i]`); `union_strategy` comes from
`config.insertStrategy` (INSERT/UNION ALL → UNION ALL, UNION → UNION; unknown →
UNION ALL + review reason). The emitter builds one SELECT per branch, each
with its own `_FIVETRAN_ACTIVE` filter, joined by the operator; the count is
`COUNT(*)` over the union. A column missing from any branch is skipped, never
guessed. Workbook scope filters are not applied to UNION nodes yet. The old
ADR 0019 shape (first mapping `FROM`, the rest `JOIN` fragments) still
concatenates. Not yet checked against a real multi-source payload.

## Silver sub-flow UI (`webapp/views/generate_yamls.py`, ADR 0050)

Lives inside `with tab_batch:` ("Generate Batch YAML" tab), gated by a
top-level `st.radio("Layer", ["Bronze", "Silver"], key="batch_layer_flow")`
placed before the existing source-table-picking flow. The Silver sub-flow
does **not** call the generic `pick_layer()` output-directory selectbox —
its output dir is fixed to `Project/config/silver` (ADR 0022), matching the
hardcoded `layer="silver"` in its `generate_from_plan()` call.

Bronze branch (`layer_flow == "Bronze"`): the entire pre-existing
Standard/Report-Pack flow, unchanged, just re-indented one level.

Silver branch (`layer_flow == "Silver"`), session-state keys and call sites:

- `silver_node_id` (`st.text_input`), `silver_workspace_id` (`st.text_input`,
  defaults to `COALESCE_WORKSPACE_ID` env var) — inputs for the fetch. Each
  node row also has a `st.radio` (key `silver_mode_{row_idx}`) to switch that
  row from "Node ID (fetch live)" to "Paste metadata JSON" (ADR 0021) — the
  pasted-JSON path calls `coalesce_plan_builder.build_plan_from_metadata()`
  directly instead of `build_plan()`, skipping the Coalesce API round-trip.
- `st.button("Fetch node and build plan", key="silver_fetch_btn")` calls
  `coalesce_plan_builder.build_plan(node_id, workspace_id=...)` and stores the
  result in `st.session_state["silver_plan"]` / `st.session_state["silver_schema_diff"]`
  (also resets `st.session_state["silver_diff_resolution"] = {}`), so the plan
  survives reruns instead of being rebuilt every render. Catches
  `CoalesceNotConfiguredError` and `UnsupportedNodeShapeError` with dedicated
  `st.error()` messages (same UX pattern as the existing
  `JiraNotConfiguredError` handling elsewhere in this file), and a generic
  `CoalesceError`/`Exception` fallback.
- Schema-diff resolution UI: one row per drifted column (from
  `SchemaDiff.only_in_metadata` / `.only_in_bronze_live` / `.only_in_silver_live`)
  inside `st.expander("Schema drift — resolve every column before generating")`.
  Two buttons per column, keyed `silver_excl_{group}:{column}` and
  `silver_bug_{group}:{column}`:
  - "Exclude" calls `write_schema_diff_exclusion(db_type, column, reason)`
    (`db_type` = `plan.source_db_type`, reason is the fixed string
    `"Coalesce/live schema drift — excluded via Silver validation UI"`).
  - "Raise Bug" calls the existing `connector.jira_client.create_ticket()`
    (same function/signature the rest of the UI already uses), pre-filled
    with the column, table, and drift-type.
  - Resolution state tracked in `st.session_state["silver_diff_resolution"]`,
    a `{"{group}:{column}": "excluded"|"bugged"}` dict.
- Macro-computed surrogate key case: when `plan.requires_review` and a
  `"macro-computed"` review reason is present, an `st.selectbox` (key
  `silver_natural_key_pick`) is populated from
  `plan.population_scope["natural_key_candidates"]`. On pick, sets
  `plan.source_primary_keys = [picked]` and `plan.target_primary_keys =
  [target_column or picked]` (uses the plan's own passthrough
  `target_column` for that source column if one exists, else assumes the
  same name — this assumption is surfaced via `st.caption`, not silent).
- When `SchemaDiff.unavailable_reason` is set (ADR 0021), an `st.warning`
  says drift was NOT checked; this does not gate generation.
- `st.button("Generate YAML", key="silver_generate_btn")` is disabled until
  every schema-diff column is resolved and (if applicable) the natural key is
  picked. When enabled, calls
  `generated_queries.QueryOutputManager().generate_from_plan(plan, output_dir=output_dir, layer="silver")`
  directly — the same call `ValidationPipeline.run_with_plan()` already makes
  internally for Bronze once column-matching is done; Silver's plan is
  already fully built by `build_plan()`, so there is no matching step to run
  first.
