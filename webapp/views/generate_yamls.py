"""📋 Generate YAMLs (Bronze + Silver) tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# 📊 Usage & Cost lives in the sidebar (see below).

# =============================================================================
# TAB: Generate — Batch YAML
# =============================================================================
def render():
    st.subheader("Generate validation YAML for multiple tables")
    st.caption("Map each source table to one Snowflake target. Review every mapping, set table scope, then generate all configs together.")
    st.info("Workflow: **1. Select tables**  →  **2. Map targets**  →  **3. Review columns**  →  **4. Set scope**  →  **5. Generate all**", icon="🧭")
    _batch_mode = st.radio(
        "Choose batch workflow",
        ["📋 Standard (table mapping)", "📊 Report Pack (Excel)"],
        horizontal=True,
        key="batch_mode_radio",
        help="Standard creates one validation YAML per source table. Report Pack parses an uploaded Excel "
             "report and shows an editable AI-derived plan (tables/grain/filter) per row before generating.",
    )

    st.divider()
    layer_flow = st.radio(
        "Layer",
        ["Bronze", "Silver"],
        horizontal=True,
        key="batch_layer_flow",
        help="Bronze: match a source DB table to a Snowflake Bronze table (existing flow below). "
             "Silver: build the plan from a Coalesce node metadata transform and diff it against "
             "live Bronze/Silver schemas before generating YAML.",
    )

    if layer_flow == "Bronze":
        registry = load_registry()
        rec = select_connection(registry, key="batch_conn")

        if rec and _batch_mode == "📋 Standard (table mapping)":
            _override_source_env(rec)
            src_db_type = rec["db_type"]

            with st.container(border=True):
                st.markdown("### 1. Select source tables")
                st.caption("Choose multiple source tables from one connection. Each selected table becomes one validation job.")
                database, schema, table_options = pick_source_location(rec, "batch")
                source_tables = st.multiselect(
                    "Source tables to validate",
                    options=table_options,
                    key="batch_tables_select",
                )
                if not table_options:
                    st.caption("Table list unavailable — type names manually below (comma-separated).")
                    manual_raw = st.text_input("Table names (comma-separated)", key="batch_tables_manual")
                    source_tables = [t.strip() for t in manual_raw.split(",") if t.strip()]

            with st.container(border=True):
                st.markdown("### 2. Choose Snowflake target area")
                st.caption("Select target database and schema. You will map each source table below.")
                sf_database, sf_schema, _ = pick_snowflake_target("", "batch", include_table=False)

                sf_tables_live = []
                if sf_database and sf_schema:
                    try:
                        sf_tables_live = cached_sf_tables(sf_database, sf_schema)
                    except Exception as exc:
                        st.warning(f"Could not list Snowflake tables for mapping: {exc}")

            target_map: dict = {}
            ambiguous_tables: set = set()
            if source_tables:
                st.markdown("### 3. Map source tables to Snowflake targets")
                st.caption("Green or confirmed matches are suggestions. Resolve every warning manually before generation.")

                creds = snowflake_creds()
                confirmed_mappings = {}
                if sf_database and sf_schema and creds["account"]:
                    confirmed_mappings = mapping_store.load_confirmed_mappings(
                        creds["account"], creds["username"], creds["password"], sf_database, sf_schema,
                    )

                # Suggest a target the same way the CLI does: exact upper() match,
                # else closest fuzzy match, else the plain upper() guess. A source
                # table is flagged ambiguous when 2+ Snowflake tables are equally
                # plausible (e.g. ADDRESS vs ADDRESSES) — those are left blank so
                # a person has to pick, instead of silently guessing wrong.
                upper_sf = {t.upper(): t for t in sf_tables_live}

                def suggest(src_table: str) -> tuple:
                    """Returns (suggested_target, status) where status is one of:
                    confirmed / exact / ambiguous / not_found / no_data. Only
                    'exact' and 'confirmed' are safe to silently pre-fill — every
                    other status leaves the target blank so a human has to pick,
                    rather than guessing a Snowflake table name that may not
                    exist (the ACCTSOFTWARE-style failure this guards against)."""
                    if src_table in confirmed_mappings:
                        return confirmed_mappings[src_table], "confirmed"
                    exact = upper_sf.get(src_table.upper())
                    if exact:
                        return exact, "exact"
                    if not sf_tables_live:
                        # Live Snowflake table discovery failed entirely — nothing
                        # to compare against, fall back to a plain guess (the
                        # manual-entry dropdown still lets a human override it).
                        return src_table.upper(), "no_data"
                    close = difflib.get_close_matches(src_table.upper(), list(upper_sf.keys()), n=3, cutoff=0.4)
                    if len(close) >= 2:
                        return "", "ambiguous"
                    if len(close) == 1:
                        return upper_sf[close[0]], "fuzzy"
                    return "", "not_found"

                suggestions = {t: suggest(t) for t in source_tables}
                ambiguous_tables = {t for t, (_, status) in suggestions.items() if status == "ambiguous"}
                not_found_tables = {t for t, (_, status) in suggestions.items() if status == "not_found"}
                select_options = sorted(set(sf_tables_live) | {s for s, _ in suggestions.values() if s})

                _STATUS_LABELS = {
                    "confirmed": "✓ Previously confirmed",
                    "exact": "",
                    "fuzzy": "",
                    "ambiguous": "⚠️ Ambiguous — pick manually",
                    "not_found": "⚠️ No close match found — pick manually",
                    "no_data": "⚠️ Could not verify (Snowflake table list unavailable)",
                }

                def status_for(src_table: str) -> str:
                    return _STATUS_LABELS[suggestions[src_table][1]]

                import pandas as pd
                mapping_df = pd.DataFrame({
                    "Source Table": source_tables,
                    "Snowflake Target Table": [suggestions[t][0] for t in source_tables],
                    "Status": [status_for(t) for t in source_tables],
                })

                edited_df = st.data_editor(
                    mapping_df,
                    column_config={
                        "Source Table": st.column_config.TextColumn(disabled=True),
                        "Snowflake Target Table": st.column_config.SelectboxColumn(
                            options=select_options, required=True,
                            help="Auto-suggested via exact/fuzzy match against live Snowflake tables — override if wrong.",
                        ),
                        "Status": st.column_config.TextColumn(disabled=True),
                    },
                    hide_index=True,
                    width='stretch',
                    key="batch_mapping_editor",
                )
                target_map = dict(zip(edited_df["Source Table"], edited_df["Snowflake Target Table"]))

                # ── Quality checks on the mapping before allowing Generate ──────
                empty_targets = [s for s, t in target_map.items() if not t]
                target_counts: dict = {}
                for t in target_map.values():
                    if t:
                        target_counts[t] = target_counts.get(t, 0) + 1
                duplicate_targets = [t for t, n in target_counts.items() if n > 1]

                if ambiguous_tables:
                    st.warning(
                        f"Ambiguous target for: {', '.join(sorted(ambiguous_tables))} — "
                        f"multiple Snowflake tables matched closely, pick the correct one in the grid above."
                    )
                if not_found_tables:
                    st.warning(
                        f"No confident Snowflake match for: {', '.join(sorted(not_found_tables))} — "
                        f"pick the correct target manually in the grid above (nothing was auto-filled to avoid guessing wrong)."
                    )
                if empty_targets:
                    st.error(f"Missing target table for: {', '.join(empty_targets)}")
                if duplicate_targets:
                    st.error(
                        f"Two or more source tables are mapped to the same Snowflake target "
                        f"({', '.join(duplicate_targets)}) — each source table needs a distinct target."
                    )
                mapping_valid = source_tables and not empty_targets and not duplicate_targets
                if mapping_valid:
                    st.success(f"{len(source_tables)} source table(s) mapped to {len(source_tables)} distinct target(s) — ready to generate.")

            st.markdown("### 4. Set column exclusions")
            st.caption("System exclusions apply automatically. Add table-specific exclusions only when needed.")
            auto_excluded = _get_all_exclusions(src_db_type)
            static_set = {c.lower() for c in STATIC_EXCLUDE_COLUMNS}
            user_global_excluded = [c for c in auto_excluded if c not in static_set]
            st.caption(
                f"🔒 Built-in auto-excluded (system default for {_DB_TYPE_LABELS.get(src_db_type, src_db_type)}, always "
                f"applied to every table below, not shown in the pickers): {', '.join(STATIC_EXCLUDE_COLUMNS) or '(none)'}"
            )
            st.caption(
                f"🌐 User-defined global exclusions (added via the Exclusions tab, always applied to every table below, "
                f"not shown in the pickers — manage the list in the Exclusions tab): {', '.join(user_global_excluded) or '(none)'}"
            )

            per_table_excl: dict = {}
            for src_table in source_tables:
                with st.expander(f"Columns to exclude — {src_table}", expanded=False):
                    try:
                        table_cols = cached_source_columns(
                            src_db_type, rec["host"], int(rec.get("port") or 0), database,
                            rec["username"], source_password(rec), rec.get("auth", ""),
                            rec.get("s3_output", ""), schema, src_table,
                        )
                    except Exception as exc:
                        st.warning(f"Could not load columns for {src_table}: {exc}")
                        table_cols = []
                    static_present = sorted(c for c in table_cols if c.lower() in static_set)
                    user_global_present = sorted(c for c in table_cols if c.lower() in set(auto_excluded) and c.lower() not in static_set)
                    pickable_cols = [c for c in table_cols if c.lower() not in set(auto_excluded)]
                    if static_present:
                        st.caption(f"🔒 Already built-in auto-excluded: {', '.join(static_present)}")
                    if user_global_present:
                        st.caption(f"🌐 Already user-defined auto-excluded: {', '.join(user_global_present)}")
                    per_table_excl[src_table] = st.multiselect(
                        f"Additional columns to exclude from {src_table} (optional, just for this run)",
                        options=pickable_cols,
                        default=[],
                        key=f"batch_excl_{src_table}",
                        label_visibility="collapsed",
                    )

            model = select_or_type(
                "AI model", available_models_for_ui(), os.getenv("DIAL_MODEL", "gpt-4o"),
                "batch_model", format_func=_model_label,
            )

            layer, output_dir = pick_layer("batch_layer")

            per_table_col_overrides: dict = {}
            if source_tables and mapping_valid:
                st.markdown("### 5. Review columns for each table")
                st.caption("Expand each table. Confirm matches before continuing.")
                batch_extractor = ExtractorFactory.create(
                    src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                    database=database, username=rec["username"], password=source_password(rec),
                    auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
                )
                preview_pipeline = ValidationPipeline(model=model, source_extractor=batch_extractor)
                for src_table in source_tables:
                    tgt_table = target_map.get(src_table, "")
                    if not tgt_table:
                        continue
                    with st.expander(f"Column mapping — {src_table} → {tgt_table}", expanded=False):
                        per_table_col_overrides[src_table] = render_mapping_review(
                            preview_pipeline, schema, src_table, sf_schema, tgt_table,
                            sf_database, database,
                            (list(auto_excluded) + per_table_excl.get(src_table, [])),
                            key_prefix=f"batch_{src_table}",
                        )
                        batch_mapping_rows = st.session_state.get(f"batch_{src_table}_mapping_rows")
                        if batch_mapping_rows:
                            render_custom_sql_section(
                                batch_mapping_rows, src_db_type,
                                src_table_fqn=f"{schema}.{src_table}",
                                sf_table_fqn=(
                                    f"{sf_database}.{sf_schema}.{tgt_table}" if sf_database
                                    else f"{sf_schema}.{tgt_table}"
                                ),
                                output_dir=output_dir,
                                default_filename=src_table,
                                key_prefix=f"batch_{src_table}",
                            )

            generate_disabled = not source_tables or not target_map or any(not t for t in target_map.values()) or (
                len(set(target_map.values())) != len(target_map)
            )

            # ── Pre-generate approval check (batch) ─────────────────────────────────
            # Aggregate low-confidence and unmatched columns across ALL tables that
            # have been previewed, and surface them before the Generate All button.
            _batch_issues: list = []
            for _bt in (source_tables or []):
                _bt_rows = st.session_state.get(f"batch_{_bt}_mapping_rows") or []
                _bt_corrected = set((per_table_col_overrides.get(_bt) or {}).keys())
                _bt_skipped_low = [
                    r for r in _bt_rows
                    if r.get("skip_validation") and not r.get("target_column")
                    and r["source_column"] not in _bt_corrected
                    and r.get("confidence", 0.0) < 0.75
                ]
                _bt_low = [
                    r for r in _bt_rows
                    if not r.get("skip_validation") and r.get("target_column")
                    and r["source_column"] not in _bt_corrected
                    and r.get("confidence", 1.0) < 0.75
                ]
                _bt_none = [r for r in _bt_rows if not r.get("skip_validation") and not r.get("target_column")]
                if _bt_skipped_low or _bt_low or _bt_none:
                    _batch_issues.append({"table": _bt, "skipped_low": _bt_skipped_low, "low": _bt_low, "none": _bt_none})

            _batch_generate_blocked = False
            if _batch_issues and not generate_disabled:
                with st.container(border=True):
                    st.markdown("##### ⚠️ Review required before generating")
                    for _bi in _batch_issues:
                        st.markdown(f"**{_bi['table']}**")
                        if _bi.get("skipped_low"):
                            _bsk_c1, _bsk_c2 = st.columns([3, 1])
                            with _bsk_c1:
                                st.warning(
                                    f"{len(_bi['skipped_low'])} column(s) skipped — no target match (confidence <75%): "
                                    + ", ".join(
                                        f"`{r['source_column']}` ({int(r.get('confidence',0)*100)}%)"
                                        for r in _bi["skipped_low"]
                                    )
                                )
                            with _bsk_c2:
                                if st.button("🎫 Raise Jira ticket", key=f"batch_skip_jira_{_bi['table']}"):
                                    try:
                                        from connector.jira_client import create_ticket, is_configured
                                        if not is_configured():
                                            st.info("Jira not configured — set env vars in `.env`.")
                                        else:
                                            _t = create_ticket(
                                                summary=f"[Migration Validator] Skipped columns: {_bi['table']}",
                                                description=(
                                                    f"Table: {_bi['table']}\n\nSkipped columns (no target, conf <75%):\n"
                                                    + "\n".join(
                                                        f"  - {r['source_column']} ({int(r.get('confidence',0)*100)}%,"
                                                        f" {r.get('skip_reason','no match')})"
                                                        for r in _bi["skipped_low"]
                                                    )
                                                ),
                                                labels=["migration-validator", "skipped-columns", "needs-review"],
                                            )
                                            st.success(f"[{_t['key']}]({_t['url']})")
                                    except Exception as _bsje:
                                        st.error(f"Jira error: {_bsje}")
                        if _bi["none"]:
                            st.error(
                                f"No target match for: "
                                f"`{'`, `'.join(r['source_column'] for r in _bi['none'])}` — will be skipped."
                            )
                        if _bi["low"]:
                            st.warning(
                                "Low-confidence mappings (<75%): "
                                + ", ".join(
                                    f"`{r['source_column']}` → `{r['target_column']}` ({int(r['confidence']*100)}%)"
                                    for r in _bi["low"]
                                )
                            )
                    _bj_col1, _bj_col2 = st.columns([3, 1])
                    with _bj_col2:
                        if st.button("🎫 Raise Jira tickets", key="batch_jira_btn"):
                            try:
                                from connector.jira_client import create_ticket, is_configured
                                if not is_configured():
                                    st.info("Jira not configured — set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in your `.env`.")
                                else:
                                    _created = []
                                    for _bi in _batch_issues:
                                        if not _bi["low"] and not _bi["none"]:
                                            continue
                                        _bdesc = (
                                            f"Table: {_bi['table']}\n\n"
                                            + (f"Unmatched: {', '.join(r['source_column'] for r in _bi['none'])}\n" if _bi["none"] else "")
                                            + (f"Low-confidence:\n" + "\n".join(f"  - {r['source_column']} → {r['target_column']} ({int(r['confidence']*100)}%)" for r in _bi["low"]) if _bi["low"] else "")
                                        )
                                        _t = create_ticket(
                                            summary=f"[Migration Validator] Low-confidence mappings: {_bi['table']}",
                                            description=_bdesc,
                                            labels=["migration-validator", "needs-review"],
                                        )
                                        _created.append(f"[{_t['key']}]({_t['url']})")
                                    st.success(f"Created {len(_created)} ticket(s): {', '.join(_created)}")
                            except Exception as _bje:
                                st.error(f"Jira error: {_bje}")
                    _batch_confirmed = st.checkbox(
                        "I have reviewed the issues above and want to generate anyway (or have raised Jira tickets)",
                        key="batch_review_confirmed",
                    )
                    if not _batch_confirmed:
                        _batch_generate_blocked = True

            # ── Per-table migration filters + optional JOIN rules ──────────────
            # Each table gets its own filter expander so different tables can have
            # different predicates (e.g. orders: created_at >= '2024-01-01',
            # customers: is_active = true).
            # Previously-used filters for each table are surfaced from plan history
            # as a dropdown — no need to retype the same predicate every run.
            # Each expander also lets you add LEFT JOINs for that table; the join
            # spec and a natural-language prompt are forwarded to AISQLQueryGenerator
            # instead of the default column-mapping pipeline.
            st.markdown("### 6. Define validation scope per table")
            st.caption(
                "**What is this?**  When the migration team moved only a *subset* of rows from a table "
                "(e.g. only the last 2 years of orders, or only active customers), the validator must "
                "apply the same filter on the source side — otherwise it will always report a mismatch "
                "because it is comparing the full source against the partial target.\n\n"
                "**Source filter** is applied to the source database query. "
                "**Target filter** is applied to the Snowflake query (defaults to the source filter when left blank). "
                "Write the predicate *without* the WHERE keyword — e.g. `created_at >= '2024-01-01'` or "
                "`status = 'active' AND tenant_id = 42`.\n\n"
                "**Join rules (optional)** — if this table needs to be validated via a JOIN query (e.g. "
                "orders → customers), configure the LEFT JOINs and provide a short prompt. "
                "AI generates the SQL using the live schema. "
                "Previously-used filters for each table are shown in a dropdown so you can reuse them without retyping."
            )

            _batch_scope_specs = pick_scope_workbook("batch_scope")
            _batch_scope_blocked: list = []
            per_table_filters: dict = {}   # {src_table: (source_filter, target_filter)}
            per_table_joins:   dict = {}   # {src_table: {"joins": [...], "prompt": str, "join_sf_schema": str}}
            _pt_all_table_options = [f"{schema}.{t}" for t in source_tables]
            for src_table in source_tables:
                _history = filter_options_for(src_table)
                _label = f"🔍 Migration filter — {src_table}"
                _has_history = bool(_history)
                with st.expander(_label + (" *(history available)*" if _has_history else ""), expanded=False):
                    if _has_history:
                        _dropdown_labels = [f"{sf}  →  {tf or '(same as source)'}" for sf, tf in _history]
                        _dropdown_labels = ["— Enter a new filter —"] + _dropdown_labels
                        _selected_idx = st.selectbox(
                            "Previously used filters for this table",
                            options=range(len(_dropdown_labels)),
                            format_func=lambda i: _dropdown_labels[i],
                            key=f"batch_filter_history_{src_table}",
                            help="Filters are saved automatically from each successful generation run. "
                                 "Select one to pre-fill the fields below, or choose 'Enter a new filter' to type manually.",
                        )
                        _pre_src = _history[_selected_idx - 1][0] if _selected_idx > 0 else ""
                        _pre_tgt = _history[_selected_idx - 1][1] if _selected_idx > 0 else ""
                    else:
                        _pre_src, _pre_tgt = "", ""

                    _fc1, _fc2 = st.columns(2)
                    with _fc1:
                        _src_f = st.text_input(
                            "Source filter",
                            value=_pre_src,
                            placeholder="e.g.  created_at >= '2024-01-01'",
                            key=f"batch_src_filter_{src_table}",
                            help="WHERE predicate applied to the source database query for this table. "
                                 "No WHERE keyword — just the condition, e.g. `status = 'active'`.",
                        )
                    with _fc2:
                        _tgt_f = st.text_input(
                            "Target filter",
                            value=_pre_tgt,
                            placeholder="Leave blank to mirror source filter",
                            key=f"batch_tgt_filter_{src_table}",
                            help="WHERE predicate applied to the Snowflake query. "
                                 "Column names are usually UPPER_CASE on Snowflake. "
                                 "If blank, the source filter is reused as-is.",
                        )

                    if _src_f:
                        st.info(
                            f"**{src_table}** source: `WHERE {_src_f}`  \n"
                            f"**{src_table}** target: `WHERE {_tgt_f or _src_f}`"
                        )
                    else:
                        st.caption("No filter — full table scan (both sides).")

                    # ── Workbook scope filter (ADR 0045), ANDed with the above ──
                    _wb_spec = scope_filter.lookup(_batch_scope_specs, src_table)
                    if _wb_spec:
                        st.markdown("**📄 Workbook filter**")
                        try:
                            _wb_ai_cols = {src_table: cached_source_columns(
                                src_db_type, rec["host"], int(rec.get("port") or 0), database,
                                rec["username"], source_password(rec), rec.get("auth", ""),
                                rec.get("s3_output", ""), schema, src_table,
                            )}
                        except Exception:
                            _wb_ai_cols = None
                        _wb_spec = edit_scope_filter(_wb_spec, f"batch_scope_{src_table}", _wb_ai_cols)
                        if _wb_spec.blocked:
                            _batch_scope_blocked.append(src_table)
                        elif _wb_spec.mode == "scoped":
                            _wb_tgt_table = target_map.get(src_table, "")
                            _wb_parents = {h.parent.lower(): h.parent for h in _wb_spec.hops}
                            _wb_src_cols = lambda t: cached_source_columns(
                                src_db_type, rec["host"], int(rec.get("port") or 0), database,
                                rec["username"], source_password(rec), rec.get("auth", ""),
                                rec.get("s3_output", ""), schema, t,
                            )
                            _wb_src_live = _live_columns(_wb_src_cols, {"": src_table, **_wb_parents})
                            _wb_tgt_live = _live_columns(
                                lambda t: cached_sf_columns(sf_database, sf_schema, t),
                                {"": _wb_tgt_table, **{k: v.upper() for k, v in _wb_parents.items()}},
                            ) if sf_database and _wb_tgt_table else {}
                            _wb_col_map = {
                                r["source_column"]: r["target_column"]
                                for r in (st.session_state.get(f"batch_{src_table}_mapping_rows") or [])
                                if r.get("target_column")
                            }
                            _wb_src_p, _wb_tgt_p, _wb_missing = scope_filter.render_bronze(
                                _wb_spec, schema, sf_database, sf_schema, _wb_col_map, _wb_src_live, _wb_tgt_live,
                            )
                            if _wb_missing:
                                st.error("Column(s) in this filter don't exist — fix the join path above: "
                                         + "; ".join(_wb_missing))
                                _batch_scope_blocked.append(src_table)
                            else:
                                st.caption("Source query adds:")
                                st.code(_wb_src_p, language="sql")
                                st.caption("Snowflake query adds (`_FIVETRAN_ACTIVE = TRUE` on every table):")
                                st.code(_wb_tgt_p, language="sql")
                                _tgt_f = " AND ".join(p for p in (_tgt_f or _src_f, _wb_tgt_p) if p)
                                _src_f = " AND ".join(p for p in (_src_f, _wb_src_p) if p)
                    elif _batch_scope_specs:
                        st.caption("No row for this table in the workbook.")

                    per_table_filters[src_table] = (_src_f, _tgt_f)

                    # ── Optional per-table JOIN rules ──────────────────────────────
                    st.divider()
                    _pt_use_joins = st.checkbox(
                        "Add JOIN rules for this table (optional)",
                        key=f"pt_use_joins_{src_table}",
                        help="When this table must be validated via a JOIN query, enable this to define "
                             "LEFT JOIN conditions. AI generates the SQL using live schema context.",
                    )
                    if _pt_use_joins:
                        st.caption(
                            "Define LEFT JOINs below. This table is the driving (left) side. "
                            "Select the joined table and write the ON condition without the ON keyword."
                        )
                        _pt_join_count = st.number_input(
                            "Number of LEFT JOINs",
                            min_value=1, max_value=max(1, len(_pt_all_table_options) - 1),
                            value=1, step=1,
                            key=f"pt_join_count_{src_table}",
                        )
                        _pt_join_rules: list = []
                        for _pt_ji in range(int(_pt_join_count)):
                            _ptj1, _ptj2 = st.columns(2)
                            with _ptj1:
                                _pt_right = st.selectbox(
                                    f"LEFT JOIN {_pt_ji + 1} — table",
                                    options=_pt_all_table_options,
                                    key=f"pt_join_right_{src_table}_{_pt_ji}",
                                )
                            with _ptj2:
                                _pt_on = st.text_input(
                                    f"LEFT JOIN {_pt_ji + 1} — ON condition",
                                    placeholder=f"{src_table}.id = {_pt_right.split('.')[-1] if _pt_all_table_options else 'other'}.{src_table}_id",
                                    key=f"pt_join_on_{src_table}_{_pt_ji}",
                                    help="Condition without ON keyword. Use table.column notation.",
                                )
                            if _pt_on.strip():
                                _pt_join_rules.append({"right": _pt_right, "on": _pt_on.strip()})

                        _pt_join_prompt = st.text_area(
                            "Prompt for AI SQL generation",
                            placeholder=f"Validate {src_table} joined to products. "
                                        "Compare row counts and key aggregates after applying the filter above.",
                            key=f"pt_join_prompt_{src_table}",
                            height=80,
                            help="Describe what the joined query should validate. "
                                 "AI uses live PK/FK schema for fully-qualified SQL.",
                        ).strip()

                        _pt_join_sf_schema_input = st.text_input(
                            "Snowflake target schema for joined tables (leave blank to use selected target schema)",
                            key=f"pt_join_sf_schema_{src_table}",
                            placeholder=f"e.g. {sf_schema}",
                        ).strip() or sf_schema

                        if int(_pt_join_count) > len(_pt_join_rules):
                            st.warning("Fill in every ON condition before generating.")

                        if _pt_join_rules and _pt_join_prompt:
                            per_table_joins[src_table] = {
                                "joins": _pt_join_rules,
                                "prompt": _pt_join_prompt,
                                "join_sf_schema": _pt_join_sf_schema_input,
                            }
                            st.info(
                                f"**{src_table}** will be validated with {len(_pt_join_rules)} LEFT JOIN(s). "
                                f"AI prompt: _{_pt_join_prompt[:80]}{'…' if len(_pt_join_prompt) > 80 else ''}_"
                            )

            st.divider()
            if per_table_joins:
                st.info(
                    f"**{len(per_table_joins)} table(s)** have JOIN rules configured and will use "
                    "AI SQL generation instead of the standard column-mapping pipeline."
                )
            if _batch_scope_blocked:
                st.error("Fix the workbook filter for: " + ", ".join(sorted(set(_batch_scope_blocked)))
                         + " — or set it to **Whole table** — before generating.")
            st.caption("Final check: every source table has one unique target, mappings are reviewed, and scope is set.")
            if st.button("Generate all table YAMLs", type="primary", key="batch_generate",
                         disabled=generate_disabled or _batch_generate_blocked or bool(_batch_scope_blocked)):
                extractor = ExtractorFactory.create(
                    src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                    database=database, username=rec["username"], password=source_password(rec),
                    auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
                )
                progress = st.progress(0.0, text="Starting...")
                results = []
                pairs = list(target_map.items())
                # Build AI generator once — reused by all tables that have JOIN rules
                _pt_gen = AISQLQueryGenerator(model=model) if per_table_joins else None
                _pt_sf_creds = snowflake_creds()

                for i, (src_table, tgt_table) in enumerate(pairs, 1):
                    progress.progress(i / len(pairs), text=f"{src_table} → {tgt_table}  ({i}/{len(pairs)})")
                    try:
                        _pt_jinfo = per_table_joins.get(src_table)
                        if _pt_jinfo:
                            # ── JOIN path: use AISQLQueryGenerator ──────────────
                            from excel_batch_loader import _build_schema_context as _pt_bsc
                            _pt_join_rules = _pt_jinfo["joins"]
                            _pt_join_prompt = _pt_jinfo["prompt"]
                            _pt_sf_sch_override = _pt_jinfo.get("join_sf_schema") or sf_schema
                            _pt_src_filter, _pt_tgt_filter = per_table_filters.get(src_table, ("", ""))

                            _pt_rule_lines = [f"Driving table: {schema}.{src_table}"]
                            _pt_rule_lines.extend(
                                f"LEFT JOIN {r['right']} ON {r['on']}" for r in _pt_join_rules
                            )
                            _pt_join_spec = (
                                "\n\nSTRUCTURED VALIDATION RULES (mandatory):\n"
                                + "\n".join(_pt_rule_lines)
                                + f"\nSource WHERE predicate: {_pt_src_filter or '(none)'}"
                                + f"\nSnowflake WHERE predicate: {_pt_tgt_filter or _pt_src_filter or '(none)'}"
                                + "\nUse LEFT JOIN only. Do not use INNER JOIN, RIGHT JOIN, FULL JOIN, CROSS JOIN, or comma joins."
                                + " Apply source predicate only in source SQL and Snowflake predicate only in Snowflake SQL."
                            )

                            _pt_src_ctx = _pt_bsc(
                                extractor, src_db_type, database, schema,
                                [src_table] + [r["right"].split(".")[-1] for r in _pt_join_rules],
                                grain_cols=[],
                            )
                            _pt_tgt_ctx: dict = {}
                            if _pt_sf_creds.get("account") and sf_database:
                                _pt_sf_ext = SnowflakeExtractor(
                                    account=_pt_sf_creds["account"],
                                    database=sf_database, schema=_pt_sf_sch_override,
                                    username=_pt_sf_creds["username"],
                                    password=_pt_sf_creds["password"],
                                )
                                _pt_tgt_ctx = _pt_bsc(
                                    _pt_sf_ext, "snowflake", sf_database, _pt_sf_sch_override,
                                    [tgt_table.upper()] + [r["right"].split(".")[-1].upper() for r in _pt_join_rules],
                                    grain_cols=[],
                                )

                            _pt_src_sql = _pt_gen.generate_schema_aware_query(
                                user_instruction=_pt_join_prompt + _pt_join_spec,
                                schema_context=_pt_src_ctx,
                                db_type=src_db_type,
                                default_schema=schema,
                                normalize=True,
                            ).query
                            _pt_tgt_sql = _pt_gen.generate_schema_aware_query(
                                user_instruction=_pt_join_prompt + _pt_join_spec,
                                schema_context=_pt_tgt_ctx,
                                db_type="snowflake",
                                default_schema=_pt_sf_sch_override,
                                normalize=True,
                            ).query

                            import yaml as _pt_yaml
                            _pt_out_dir = Path(output_dir) / "data_validation"
                            _pt_out_dir.mkdir(parents=True, exist_ok=True)
                            _pt_path = _pt_out_dir / f"{src_table}.yaml"
                            _pt_sql_oneline = lambda s: " ".join(s.split())
                            _pt_doc = {
                                "tables": {
                                    src_table: {
                                        "validations": {
                                            "data_validation": {
                                                "source_table_name": src_table,
                                                "source": src_db_type,
                                                "source_database": database,
                                                "source_schema": schema,
                                                "sourcecolumn": "row_hash",
                                                "sourcequery": _pt_sql_oneline(_pt_src_sql),
                                                "target_table_name": tgt_table,
                                                "target": "snowflake",
                                                "target_database": sf_database,
                                                "target_schema": _pt_sf_sch_override,
                                                "targetcolumn": "row_hash",
                                                "targetquery": _pt_sql_oneline(_pt_tgt_sql),
                                                "source_filter": _pt_src_filter,
                                                "target_filter": _pt_tgt_filter or _pt_src_filter,
                                                "joins": _pt_join_rules,
                                            }
                                        }
                                    }
                                }
                            }
                            with open(_pt_path, "w", encoding="utf-8") as _pt_f:
                                _pt_yaml.dump(_pt_doc, _pt_f, allow_unicode=True,
                                              sort_keys=False, default_flow_style=False)
                            results.append({
                                "Source": src_table, "Target": tgt_table, "Status": "✅ Success (JOIN)",
                                "Detail": f"AI JOIN SQL, {len(_pt_join_rules)} join(s)",
                            })
                        else:
                            # ── Standard column-mapping pipeline ─────────────────
                            pipeline = ValidationPipeline(model=model, source_extractor=extractor)
                            result, _plan = pipeline.run_with_plan(
                                pg_schema=schema,
                                pg_table=src_table,
                                sf_schema=sf_schema,
                                sf_table=tgt_table,
                                sf_database=sf_database,
                                pg_database=database,
                                explicit_mappings=per_table_col_overrides.get(src_table) or None,
                                exclude_columns=(list(auto_excluded) + per_table_excl.get(src_table, [])) or None,
                                source_db_type=src_db_type,
                                output_dir=output_dir,
                                source_filter=per_table_filters.get(src_table, ("", ""))[0],
                                target_filter=per_table_filters.get(src_table, ("", ""))[1],
                            )
                            results.append({
                                "Source": src_table, "Target": tgt_table, "Status": "✅ Success",
                                "Detail": f"{result.active_columns} cols, {result.generated_by}",
                            })
                            creds = snowflake_creds()
                            if creds["account"]:
                                mapping_store.save_mapping(
                                    creds["account"], creds["username"], creds["password"],
                                    sf_database, sf_schema, src_table, tgt_table,
                                    confirmed_by=creds["username"], source_connection=connection_label(rec),
                                )
                    except Exception as exc:
                        results.append({"Source": src_table, "Target": tgt_table, "Status": "❌ Failed", "Detail": str(exc)})
                progress.empty()

                st.dataframe(results, width='stretch', hide_index=True)
                n_ok = sum(1 for r in results if r["Status"].startswith("✅"))
                if n_ok == len(results):
                    st.success(f"Batch complete: {n_ok}/{len(results)} table(s) generated successfully.")
                else:
                    st.warning(f"Batch complete: {n_ok}/{len(results)} table(s) generated successfully — see failures above.")

        elif rec and _batch_mode == "📊 Report Pack (Excel)":
            # =====================================================================
            # REPORT PACK MODE — Excel mapping sheet input; each row is
            # run through derive_row_plan() first (tables/grain/filter, AI-derived)
            # so the user reviews/edits the plan *before* any YAML is generated.
            # Single-table rows generate via run_with_plan() (base/learned rules +
            # AI only for ambiguous columns); join rows keep the existing
            # write_yaml()/_generate_queries() path unchanged — no new yaml.dump().
            # =====================================================================
            import hashlib as _aip_hashlib
            import pandas as _aip_pd
            import tempfile as _aip_tempfile
            from excel_batch_loader import load_excel as _aip_load_excel, derive_row_plan as _aip_derive_row_plan
            from excel_batch_loader import _generate_queries as _aip_generate_queries, write_yaml as _aip_write_yaml

            _override_source_env(rec)
            src_db_type = rec["db_type"]

            with st.container(border=True):
                st.markdown("**① Source location** (same connection selected above)")
                _aip_database, _aip_schema, _ = pick_source_location(rec, "aip")

            with st.container(border=True):
                st.markdown("**② Target (Snowflake)**")
                _aip_sf_database, _aip_sf_schema, _ = pick_snowflake_target("", "aip", include_table=False)

            st.markdown("**③ Upload mapping sheet**")
            st.caption(
                "Expected columns: Report Pack · Yaml-File-name · Report Name · Summary · Grain · "
                "Legacy Query (Redshift/Postgres/…) · Snowflake Query. Headers that don't match the "
                "expected names (e.g. 'Description' instead of 'Summary') are classified by AI on "
                "upload; anything AI can't place is surfaced as an unrecognized column below."
            )

            _aip_c1, _aip_c2, _aip_c3 = st.columns([2, 2, 3])
            with _aip_c1:
                _aip_env = st.text_input("Environment", placeholder="dev / prod / uat …", key="aip_env",
                                          help="Replaces {env} tokens in SQL. Leave blank to keep as placeholder.")
            with _aip_c2:
                _aip_sheet = st.text_input("Sheet name (optional)", placeholder="First sheet if blank", key="aip_sheet")
            with _aip_c3:
                _aip_model = select_or_type(
                    "AI model", available_models_for_ui(), os.getenv("DIAL_MODEL", "gpt-4o"),
                    "aip_model", format_func=_model_label,
                )
            _aip_dry = st.checkbox("Dry run — preview only, no files written", key="aip_dry")

            _aip_file = st.file_uploader("Upload Excel mapping sheet (.xlsx)", type=["xlsx", "xls"], key="aip_excel_upload")

            if _aip_file and _aip_database and _aip_schema:
                _aip_bytes = _aip_file.read()
                # Stable per-upload cache key derived from file content (not a
                # freshly-generated temp path, which changes every rerun and would
                # defeat the plan/extractor caching below).
                _aip_file_key = _aip_hashlib.md5(_aip_bytes).hexdigest()

                _aip_tmp_path_key = f"aip_tmp_path_{_aip_file_key}"
                if _aip_tmp_path_key not in st.session_state:
                    with _aip_tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as _aip_tmp:
                        _aip_tmp.write(_aip_bytes)
                        st.session_state[_aip_tmp_path_key] = _aip_tmp.name
                _aip_tmp_path = st.session_state[_aip_tmp_path_key]

                _aip_unrecognized: list = []
                try:
                    _aip_gen = AISQLQueryGenerator(model=_aip_model)
                    _aip_specs = _aip_load_excel(
                        _aip_tmp_path, sheet=_aip_sheet.strip() or None,
                        unrecognized_out=_aip_unrecognized, ai_generator=_aip_gen,
                    )
                except Exception as _aip_exc:
                    st.error(f"Could not parse sheet: {_aip_exc}")
                    _aip_specs = []
                    _aip_gen = None

                if _aip_unrecognized:
                    st.warning(
                        f"Unrecognized column(s) — neither regex nor AI could place them, ignored: "
                        f"{', '.join(_aip_unrecognized)}"
                    )

                if _aip_specs:
                    # Populate connection context on every spec (same as Report Pack)
                    # and derive each row's plan once per upload, cached in session
                    # state keyed by a hash of the uploaded file's bytes (stable
                    # across reruns) so grid edits/checkbox toggles don't re-trigger
                    # a fresh AI call or a fresh source DB connection.
                    for _s in _aip_specs:
                        _s.source_database = _aip_database
                        _s.source_schema = _aip_schema
                        _s.source_tables = [_s.yaml_file_name]
                        _s.sf_database = _aip_sf_database or ""
                        _s.sf_schema = _aip_sf_schema or ""

                    _aip_extractor_key = f"aip_extractor_{_aip_file_key}"
                    if _aip_extractor_key not in st.session_state:
                        st.session_state[_aip_extractor_key] = ExtractorFactory.create(
                            src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                            database=_aip_database, username=rec["username"],
                            password=source_password(rec),
                            auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
                        )
                    _aip_extractor = st.session_state[_aip_extractor_key]

                    _aip_plans_key = f"aip_plans_{_aip_file_key}"
                    if _aip_plans_key not in st.session_state:
                        with st.spinner("Deriving AI plan for each row…"):
                            _aip_plans = {}
                            for _s in _aip_specs:
                                try:
                                    _aip_plans[_s.row_num] = _aip_derive_row_plan(
                                        _s, source_extractor=_aip_extractor,
                                        ai_generator=_aip_gen, model=_aip_model,
                                    )
                                except Exception as _aip_plan_exc:
                                    _aip_plans[_s.row_num] = {
                                        "tables": _s.source_tables, "grain_columns": [],
                                        "filter_english": "", "filter_sql": "",
                                        "join_needed": len(_s.source_tables) > 1,
                                        "warnings": [f"Plan derivation failed: {_aip_plan_exc}"],
                                    }
                            st.session_state[_aip_plans_key] = _aip_plans
                    _aip_plans = st.session_state[_aip_plans_key]

                    st.success(f"Parsed **{len(_aip_specs)}** row(s). Review the AI-derived plan below, then confirm.")

                    _aip_grid_rows = []
                    for _s in _aip_specs:
                        _p = _aip_plans.get(_s.row_num, {})
                        _aip_grid_rows.append({
                            "Row": _s.row_num,
                            "Report Pack": _s.report_pack,
                            "YAML File": _s.yaml_file_name,
                            "Tables (AI)": ", ".join(_p.get("tables", [])),
                            "Grain (AI)": ", ".join(_p.get("grain_columns", [])),
                            "Filter (AI)": _p.get("filter_english", ""),
                            "Status": "🔗 Join" if _p.get("join_needed") else "📄 Single table",
                        })
                    _aip_edited = st.data_editor(
                        _aip_pd.DataFrame(_aip_grid_rows),
                        column_config={
                            "Row": st.column_config.NumberColumn(disabled=True),
                            "Report Pack": st.column_config.TextColumn(disabled=True),
                            "YAML File": st.column_config.TextColumn(disabled=True),
                            "Tables (AI)": st.column_config.TextColumn(disabled=True),
                            "Grain (AI)": st.column_config.TextColumn(disabled=True),
                            "Filter (AI)": st.column_config.TextColumn(
                                help="Editable plain-English filter — edit here or in the row drill-down below, "
                                     "then re-derive to update the SQL predicate. NOTE: for 🔗 Join rows this is "
                                     "preview-only and is NOT applied to the generated SQL (see drill-down).",
                            ),
                            "Status": st.column_config.TextColumn(disabled=True),
                        },
                        hide_index=True,
                        width='stretch',
                        key="aip_batch_grid",
                    )
                    st.caption(
                        "⚠️ **Filter (AI)** is preview-only for 🔗 Join rows — the join-generation path "
                        "(`_generate_queries()`) does not read this field, so edits here have no effect on the "
                        "generated SQL for those rows. It only takes effect for 📄 Single table rows."
                    )
                    # Push any grid edits to the plain-English filter back into each plan
                    for _row in _aip_edited.to_dict("records"):
                        _p = _aip_plans.get(_row["Row"])
                        if _p is not None:
                            _p["filter_english"] = _row["Filter (AI)"]

                    st.markdown("#### Row drill-down")
                    _aip_col_overrides: dict = {}
                    _aip_review_pipeline = ValidationPipeline(model=_aip_model, source_extractor=_aip_extractor)
                    for _s in _aip_specs:
                        _p = _aip_plans.get(_s.row_num, {})
                        for _w in _p.get("warnings", []):
                            st.warning(f"Row {_s.row_num} ({_s.yaml_file_name}): {_w}")
                        with st.expander(f"Row {_s.row_num} — {_s.yaml_file_name}  ({'JOIN' if _p.get('join_needed') else 'single table'})", expanded=False):
                            if _p.get("join_needed"):
                                st.warning(
                                    "This filter is preview-only for join rows — it is NOT applied to the "
                                    "generated SQL. Join SQL comes entirely from the existing schema-aware "
                                    "generator (`_generate_queries()`), which does not read this description."
                                )
                            _aip_desc = st.text_area(
                                "Plan description (plain English)",
                                value=_p.get("filter_english", ""),
                                key=f"aip_desc_{_s.row_num}",
                                height=80,
                                disabled=_p.get("join_needed", False),
                            )
                            if st.button("🔄 Re-derive from this description", key=f"aip_rederive_{_s.row_num}"):
                                _s.filter_condition = _aip_desc
                                with st.spinner("Re-deriving…"):
                                    try:
                                        _aip_plans[_s.row_num] = _aip_derive_row_plan(
                                            _s, source_extractor=_aip_extractor,
                                            ai_generator=_aip_gen, model=_aip_model,
                                        )
                                        st.session_state[_aip_plans_key] = _aip_plans
                                        st.rerun()
                                    except Exception as _aip_rd_exc:
                                        st.error(f"Re-derive failed: {_aip_rd_exc}")

                            if _p.get("join_needed"):
                                st.caption(
                                    "Multi-table row — full JOIN SQL is generated at YAML-write time via the "
                                    "existing schema-aware generator (unchanged). Column-mapping review does "
                                    "not apply to a raw multi-table query."
                                )
                                st.code(
                                    f"Tables: {', '.join(_p.get('tables', []))}\n"
                                    f"Grain: {', '.join(_p.get('grain_columns', []))}\n"
                                    f"Filter: {_p.get('filter_sql') or '(none)'}\n"
                                    f"Summary: {_s.summary}",
                                    language="sql",
                                )
                            else:
                                _aip_col_overrides[_s.row_num] = render_mapping_review(
                                    _aip_review_pipeline, _aip_schema, _s.yaml_file_name,
                                    _aip_sf_schema, _s.yaml_file_name,
                                    _aip_sf_database, _aip_database,
                                    [], key_prefix=f"aip_{_s.row_num}",
                                )

                    _aip_confirmed = st.checkbox(
                        "✅ I have reviewed the AI-derived plan above and confirm generating YAMLs",
                        key="aip_confirmed",
                    )

                    if st.button("▶️ Generate Report YAMLs", type="primary", key="aip_generate",
                                 disabled=not _aip_confirmed):
                        _aip_env_val = _aip_env.strip() or None
                        _aip_out_dir = _ROOT_DIR / "Project" / "config" / "report"
                        _aip_sf_creds = snowflake_creds()
                        _aip_sf_extractor = SnowflakeExtractor(
                            account=_aip_sf_creds["account"],
                            database=_aip_sf_database,
                            schema=_aip_sf_schema,
                            username=_aip_sf_creds["username"],
                            password=_aip_sf_creds["password"],
                        ) if _aip_sf_creds["account"] and _aip_sf_database else None

                        _aip_progress = st.progress(0.0, text="Starting…")
                        _aip_results: list = []
                        _aip_errors: list = []

                        for _aip_i, _s in enumerate(_aip_specs, 1):
                            _aip_progress.progress(
                                _aip_i / len(_aip_specs),
                                text=f"Processing {_s.yaml_file_name}  ({_aip_i}/{len(_aip_specs)})",
                            )
                            _p = _aip_plans.get(_s.row_num, {})
                            try:
                                if _p.get("join_needed"):
                                    # ── Join rows: existing AI-SQL + write_yaml() path, unchanged ──
                                    _s.filter_condition = _p.get("filter_sql") or _s.filter_condition
                                    _aip_src_q, _aip_tgt_q = _aip_generate_queries(
                                        _s, _aip_model,
                                        source_extractor=_aip_extractor,
                                        sf_extractor=_aip_sf_extractor,
                                    ) if (not _s.legacy_query or not _s.snowflake_query) \
                                      else (_s.legacy_query, _s.snowflake_query)
                                    _aip_out = _aip_write_yaml(
                                        _s, _aip_src_q, _aip_tgt_q, _aip_env_val, _aip_out_dir,
                                        dry_run=_aip_dry,
                                    )
                                    _aip_results.append(str(_aip_out))
                                else:
                                    # ── Single-table rows: run_with_plan() (base/learned rules) ──
                                    _aip_pipeline = ValidationPipeline(model=_aip_model, source_extractor=_aip_extractor)
                                    _aip_filter = _p.get("filter_sql") or ""
                                    _aip_grain = _p.get("grain_columns") or []
                                    _aip_result, _aip_plan = _aip_pipeline.run_with_plan(
                                        pg_schema=_aip_schema,
                                        pg_table=_s.yaml_file_name,
                                        sf_schema=_aip_sf_schema,
                                        sf_table=_s.yaml_file_name,
                                        sf_database=_aip_sf_database,
                                        pg_database=_aip_database,
                                        explicit_mappings=_aip_col_overrides.get(_s.row_num) or None,
                                        exclude_columns=None,
                                        source_db_type=src_db_type,
                                        output_dir=_aip_out_dir,
                                        source_filter=_aip_filter,
                                        target_filter=_aip_filter,
                                        candidate_keys=[_aip_grain] if _aip_grain else None,
                                    )
                                    _aip_results.append(str(_aip_result.yaml_path))
                            except Exception as _aip_row_exc:
                                _aip_errors.append(f"Row {_s.row_num} ({_s.yaml_file_name}): {_aip_row_exc}")

                        _aip_progress.progress(1.0, text="Done.")

                        if _aip_results:
                            st.success(f"✅ {'Would write' if _aip_dry else 'Written'} **{len(_aip_results)}** YAML file(s).")
                            with st.expander("Output files"):
                                for _aip_p in _aip_results:
                                    st.code(_aip_p, language=None)
                        for _aip_e in _aip_errors:
                            st.error(_aip_e)
            elif _aip_file and not (_aip_database and _aip_schema):
                st.warning("Select a source database and schema above before uploading.")
    else:
        st.caption(
            "Builds a validation plan from a Coalesce node declared transform metadata, diffs it "
            "against the live Bronze/Silver Snowflake schemas, and requires every drifted column to "
            "be resolved (excluded or bugged) before YAML can be generated. See "
            "`.claude/skills/silver-layer-coalesce-validation/SKILL.md`."
        )
        from silver.coalesce_plan_builder import (
            build_plan as _silver_build_plan,
            write_schema_diff_exclusion as _silver_write_exclusion,
            UnsupportedNodeShapeError as _SilverUnsupportedNodeShapeError,
        )
        from connector.coalesce_client import (
            CoalesceNotConfiguredError as _CoalesceNotConfiguredError,
            CoalesceError as _CoalesceError,
        )

        # Repeatable node-ID row list -- same session_state-list-of-dicts +
        # Add/Remove-row pattern as the Custom SQL tab's validation entries
        # (_CST_KEY / _cst_blank_entry, see render_custom_sql_section above),
        # per ADR 0019 section 6's instruction to reuse the existing pattern
        # rather than inventing a new one.
        _SILVER_NODES_KEY = "silver_node_rows"
        if _SILVER_NODES_KEY not in st.session_state:
            st.session_state[_SILVER_NODES_KEY] = [{"node_id": "", "pasted_json": "", "mode": "Node ID (fetch live)"}]

        _silver_workspace_id = st.text_input(
            "Workspace ID (optional override)",
            value=os.getenv("COALESCE_WORKSPACE_ID", ""),
            key="silver_workspace_id",
            help="Defaults to COALESCE_WORKSPACE_ID from .env if left blank. Applies to every node row below.",
        )

        st.markdown("**Scope filters**")
        st.caption(
            "Always applied: Bronze side `_FIVETRAN_ACTIVE = TRUE` on every table read, Silver side "
            "`IS_CURRENT = TRUE`. Pick the same workbook used for Bronze to validate Silver on the same rows — "
            "each node is matched by its Bronze table name, and parent tables become `INT_<table>` on the Silver side."
        )
        _silver_scope_specs = pick_scope_workbook("silver_scope")

        st.markdown("**Coalesce node IDs**")
        st.caption(
            "Add one row per Coalesce node to validate in this batch. Each row runs the full "
            "build-plan / schema-diff / key-resolution / generate flow independently -- "
            "batching only saves re-opening this tab per node."
        )
        if st.button("➕ Add node", key="silver_add_node"):
            st.session_state[_SILVER_NODES_KEY].append({"node_id": "", "pasted_json": "", "mode": "Node ID (fetch live)"})
            st.rerun()

        _silver_rows_to_delete = []
        for _row_idx, _row in enumerate(st.session_state[_SILVER_NODES_KEY]):
            with st.container(border=True):
                # ADR 0021: per-row escape hatch for when a user has the node
                # metadata (e.g. from a manual curl) but no Snowflake grant
                # yet to run the live fetch/schema-diff -- paste the same
                # JSON get_node() would have returned instead of a node ID.
                _row["mode"] = st.radio(
                    f"Row #{_row_idx + 1} input", ["Node ID (fetch live)", "Paste metadata JSON"],
                    index=["Node ID (fetch live)", "Paste metadata JSON"].index(_row.get("mode", "Node ID (fetch live)")),
                    key=f"silver_mode_{_row_idx}", horizontal=True,
                )
                if _row["mode"] == "Node ID (fetch live)":
                    _row["node_id"] = st.text_input(
                        f"Coalesce node ID #{_row_idx + 1}",
                        value=_row["node_id"], key=f"silver_node_{_row_idx}",
                    )
                else:
                    _row["pasted_json"] = st.text_area(
                        f"Pasted node metadata JSON #{_row_idx + 1}",
                        value=_row["pasted_json"], key=f"silver_paste_{_row_idx}", height=150,
                        help="Same JSON shape as GET /workspaces/{ws}/nodes/{node_id} returns. "
                             "Skips the live Coalesce fetch entirely -- schema-diff verification "
                             "against live Snowflake will also be attempted, but a failure there "
                             "(e.g. no grant yet on the Bronze database) won't block plan/YAML "
                             "generation, just shows as 'unavailable' below.",
                    )
                if len(st.session_state[_SILVER_NODES_KEY]) > 1 and st.button(
                    "🗑️ Remove row", key=f"silver_node_del_{_row_idx}", type="secondary",
                ):
                    _silver_rows_to_delete.append(_row_idx)
        if _silver_rows_to_delete:
            for _row_idx in sorted(_silver_rows_to_delete, reverse=True):
                st.session_state[_SILVER_NODES_KEY].pop(_row_idx)
            st.rerun()

        # (label, row_idx, node_id_or_None, pasted_json_or_None) -- label is
        # the dict key results get stored under, unique per row either way.
        _silver_entries = []
        for _row_idx, _row in enumerate(st.session_state[_SILVER_NODES_KEY]):
            if _row["mode"] == "Node ID (fetch live)" and _row["node_id"].strip():
                _nid = _row["node_id"].strip()
                _silver_entries.append((_nid, _row_idx, _nid, None))
            elif _row["mode"] == "Paste metadata JSON" and _row["pasted_json"].strip():
                _silver_entries.append((f"pasted-row-{_row_idx + 1}", _row_idx, None, _row["pasted_json"].strip()))

        def _silver_forget(keep_rows: bool = True):
            """Drop built plans and every per-node widget state (natural-key pick,
            scope editor, AI result...), so a newly fetched node never shows the
            previous node's choices. Keeps the workbook pick and workspace ID,
            and the node-row inputs unless keep_rows is False (Clear)."""
            keep = ("silver_scope_wb", "silver_workspace_id", _SILVER_NODES_KEY)
            if keep_rows:
                keep += ("silver_mode_", "silver_node_", "silver_paste_",
                         "silver_add_node", "silver_fetch_btn", "silver_clear_btn")
            for _k in [k for k in st.session_state.keys()
                       if str(k).startswith("silver_") and not str(k).startswith(keep)]:
                del st.session_state[_k]
            st.session_state["silver_results"] = {}

        _fc1, _fc2 = st.columns([3, 1])
        with _fc2:
            if st.button("🧹 Clear all nodes", key="silver_clear_btn",
                         help="Remove every node row and built plan and start fresh."):
                _silver_forget(keep_rows=False)
                st.session_state[_SILVER_NODES_KEY] = [{"node_id": "", "pasted_json": "", "mode": "Node ID (fetch live)"}]
                st.rerun()
        with _fc1:
            _silver_fetch_clicked = st.button("Fetch nodes and build plans", key="silver_fetch_btn",
                                              disabled=not _silver_entries)
        if _silver_fetch_clicked:
            # Start from scratch each fetch: results (and widget state) from a
            # previously fetched node never carry over to the next one.
            _silver_forget()
            _silver_results = {}
            for _label, _row_idx, _nid, _pasted in _silver_entries:
                try:
                    if _nid is not None:
                        _plan, _diff = _silver_build_plan(
                            _nid, workspace_id=(_silver_workspace_id.strip() or None),
                        )
                    else:
                        import json as _json
                        from silver.coalesce_plan_builder import build_plan_from_metadata as _silver_build_plan_from_metadata
                        _node = _json.loads(_pasted)
                        _plan, _diff = _silver_build_plan_from_metadata(
                            _node, workspace_id=(_silver_workspace_id.strip() or None),
                        )
                    _silver_results[_label] = {"plan": _plan, "diff": _diff, "resolution": {}}
                    st.success(f"Plan built for `{_label}` -- {_plan.source_table} to {_plan.target_table}.")
                except ValueError as exc:  # includes json.JSONDecodeError
                    st.error(f"Row #{_row_idx + 1}: could not parse pasted JSON: {exc}")
                except _CoalesceNotConfiguredError as exc:
                    st.error(f"Coalesce is not configured -- set `COALESCE_API_TOKEN` / `COALESCE_WORKSPACE_ID` in `.env`. ({exc})")
                    if _nid is not None:
                        break  # live-fetch rows all depend on the same config -- no point looping further
                except _SilverUnsupportedNodeShapeError as exc:
                    st.error(f"`{_label}`: unsupported Coalesce node shape -- cannot build a plan: {exc}")
                except _CoalesceError as exc:
                    st.error(f"`{_label}`: Coalesce API error: {exc}")
                except Exception as exc:
                    st.error(f"`{_label}`: failed to build plan: {exc}")
            st.session_state["silver_results"] = _silver_results

        # Only show plans for nodes still in the rows above -- a row whose node
        # ID was changed or removed hides its old plan until it is fetched.
        _silver_labels = {e[0] for e in _silver_entries}
        _silver_all_results = {
            k: v for k, v in (st.session_state.get("silver_results") or {}).items() if k in _silver_labels
        }
        _stale = set(st.session_state.get("silver_results") or {}) - _silver_labels
        if _stale:
            st.caption(f"Hidden (node changed or removed, fetch again to rebuild): {', '.join(sorted(_stale))}")
        for _lbl in _silver_labels - set(_silver_all_results):
            st.info(f"`{_lbl}` has no plan yet — click **Fetch nodes and build plans**.")

        for _silver_node_id_built, _silver_node_result in _silver_all_results.items():
          _silver_plan = _silver_node_result["plan"]
          _silver_diff = _silver_node_result["diff"]
          _kp = f"silver_{_silver_node_id_built}"  # per-node widget-key / session-state prefix

          with st.container(border=True):
            st.markdown(f"**Node `{_silver_node_id_built}` -- Plan:** `{_silver_plan.source_table}` (Bronze) -> `{_silver_plan.target_table}` (Silver)")

            if getattr(_silver_diff, "unavailable_reason", None):
                # ADR 0021: live schema-diff couldn't run (e.g. no Snowflake
                # grant on the Bronze database yet) -- distinct from "no
                # drift found". Warn, don't gate YAML generation on it.
                st.warning(
                    "Live schema-diff verification unavailable -- drift was NOT checked, "
                    f"proceeding without it: {_silver_diff.unavailable_reason}"
                )

            _silver_resolution = _silver_node_result["resolution"]
            _silver_drift_groups = [
                ("only_in_metadata", "In Coalesce metadata but not live", _silver_diff.only_in_metadata),
                ("only_in_bronze_live", "In live Bronze table but not metadata", _silver_diff.only_in_bronze_live),
                ("only_in_silver_live", "In live Silver table but not metadata", _silver_diff.only_in_silver_live),
            ]
            with st.expander("Schema drift -- resolve every column before generating", expanded=True):
                _silver_any_drift = False
                for _grp_key, _grp_label, _grp_cols in _silver_drift_groups:
                    if not _grp_cols:
                        continue
                    _silver_any_drift = True
                    st.markdown(f"**{_grp_label}**")
                    for _col in _grp_cols:
                        _res_key = f"{_grp_key}:{_col}"
                        _status = _silver_resolution.get(_res_key)
                        _rc1, _rc2, _rc3 = st.columns([3, 1, 1])
                        with _rc1:
                            _status_txt = {"excluded": " [excluded]", "bugged": " [bug raised]"}.get(_status, "")
                            st.write(f"`{_col}`{_status_txt}")
                        with _rc2:
                            if st.button("Exclude", key=f"{_kp}_excl_{_res_key}"):
                                try:
                                    _silver_write_exclusion(
                                        "silver", _col,
                                        "Coalesce/live schema drift -- excluded via Silver validation UI",
                                    )
                                    # Also skip it in *this* plan's mappings -- the exclusion
                                    # YAML above is persisted for future runs but is not
                                    # re-read by QueryOutputManager.generate_from_plan() or
                                    # Project/main.py, which only look at
                                    # plan.active_mappings/skipped_mappings on the object.
                                    # only_in_bronze_live / only_in_silver_live columns exist
                                    # live but were never in the Coalesce metadata, so they
                                    # have no ColumnMappingEntry to mutate -- nothing to do
                                    # for those beyond the persisted exclusion above.
                                    if _grp_key == "only_in_metadata":
                                        for _m in _silver_plan.mappings:
                                            if _m.target_column == _col or _m.source_column == _col:
                                                _m.skip_validation = True
                                                _m.skip_reason = (
                                                    "Excluded via Silver schema-diff UI -- "
                                                    f"column drift ({_grp_key})"
                                                )
                                                break
                                    _silver_resolution[_res_key] = "excluded"
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Could not write exclusion: {exc}")
                        with _rc3:
                            if st.button("Raise Bug", key=f"{_kp}_bug_{_res_key}"):
                                try:
                                    from connector.jira_client import create_ticket, is_configured as _jira_is_configured
                                    if not _jira_is_configured():
                                        st.info("Jira not configured -- set env vars in `.env`.")
                                    else:
                                        _t = create_ticket(
                                            summary=f"[Migration Validator] Silver schema drift: {_col} ({_silver_plan.target_table})",
                                            description=(
                                                f"Table: {_silver_plan.source_table} -> {_silver_plan.target_table}\n"
                                                f"Column: {_col}\n"
                                                f"Drift type: {_grp_label}\n\n"
                                                "Detected while building a Silver validation plan from Coalesce node metadata."
                                            ),
                                            labels=["migration-validator", "silver-schema-drift"],
                                        )
                                        _silver_resolution[_res_key] = "bugged"
                                        st.success(f"[{_t['key']}]({_t['url']})")
                                        st.rerun()
                                except Exception as exc:
                                    st.error(f"Jira error: {exc}")
                if not _silver_any_drift:
                    if getattr(_silver_diff, "unavailable_reason", None):
                        st.info("Nothing to resolve -- diff itself was unavailable (see warning above), not confirmed clean.")
                    else:
                        st.success("No schema drift detected -- metadata matches live Bronze and Silver schemas.")
            _silver_drift_keys = [
                f"{g}:{c}" for g, _, cols in _silver_drift_groups for c in cols
            ]
            _silver_all_resolved = all(_silver_resolution.get(k) in ("excluded", "bugged") for k in _silver_drift_keys)

            _silver_pk_ready = True
            if _silver_plan.requires_review and any("macro-computed" in r for r in _silver_plan.review_reasons):
                _silver_pk_ready = False
                st.warning(
                    "This table business key is a macro-computed surrogate key -- pick the natural-key "
                    "column(s) to actually join/compare rows on (see ADR 0014 section 5 / ADR 0015)."
                )
                _silver_candidates = _silver_plan.population_scope.get("natural_key_candidates", [])
                _silver_pk_pick = st.selectbox(
                    "Natural key column", options=_silver_candidates, key=f"{_kp}_natural_key_pick",
                ) if _silver_candidates else None
                st.caption(
                    "Assumption: the target (Silver) side uses the same column name as this passthrough "
                    "source column, unless the plan own mapping already gives a different target_column "
                    "for it -- verify before trusting this pick."
                )
                if _silver_pk_pick:
                    _silver_target_name = _silver_pk_pick
                    for _m in _silver_plan.mappings:
                        if _m.source_column == _silver_pk_pick and _m.target_column:
                            _silver_target_name = _m.target_column
                            break
                    _silver_plan.source_primary_keys = [_silver_pk_pick]
                    _silver_plan.target_primary_keys = [_silver_target_name]
                    _silver_pk_ready = True

            # Silver validation is always Snowflake-to-Snowflake (Bronze and Silver
            # both live in Snowflake, per ADR 0013) -- this flow only ever produces
            # a Silver config, so the generic bronze/silver/gold pick_layer()
            # selectbox is a false choice here. Fixed to Project/config/silver.
            # ── Scope filter from the workbook, as JOINs (ADR 0045/0046) ─────
            _silver_scope_ready = True
            _silver_plan.source_filter = _silver_plan.target_filter = ""
            _silver_plan.population_scope.pop("scope_joins", None)
            _sspec = scope_filter.lookup(
                _silver_scope_specs, _silver_plan.source_table,
                re.sub(r"^INT_", "", _silver_plan.target_table, flags=re.I),
            )
            _s_union = _silver_plan.population_scope.get("union_branches")
            if _s_union:
                st.info(
                    f"Multi-source node: {len(_s_union)} Bronze sources "
                    f"({', '.join(b['name'] for b in _s_union)}) are combined with "
                    f"**{_silver_plan.population_scope.get('union_strategy')}** into `{_silver_plan.target_table}`. "
                    "The Bronze query rebuilds each source separately (each with `_FIVETRAN_ACTIVE = TRUE`) and "
                    "combines them the same way. Make sure the key includes a column that tells the sources apart."
                )
            with st.expander("Scope filter", expanded=bool(_sspec)):
                if _s_union:
                    st.warning("Workbook filters aren't supported on multi-source nodes yet — this node is "
                               "validated in full (active/current rows only).")
                    _silver_plan.population_scope["scope_filter"] = {"mode": "none", "reason": "multi-source node"}
                elif _sspec is None:
                    _why = "no workbook row for this table" if _silver_scope_specs else "no filter workbook selected"
                    st.caption(f"{_why.capitalize()} — only active/current rows are filtered.")
                    _silver_plan.population_scope["scope_filter"] = {"mode": "none", "reason": _why}
                else:
                    _s_bronze_cols = [
                        _m.source_column.split(".")[-1].strip('"') for _m in _silver_plan.mappings
                        if re.fullmatch(r'"[^"]+"\."[^"]+"', _m.source_column or "")
                    ]
                    _sspec = edit_scope_filter(_sspec, f"{_kp}_scope", {_silver_plan.source_table: _s_bronze_cols})
                    if _sspec.blocked:
                        _silver_scope_ready = False
                    elif _sspec.mode == "scoped":
                        _s_parents = {h.parent.lower(): h.parent.upper() for h in _sspec.hops}
                        _s_bronze_live = _live_columns(
                            lambda t: cached_sf_columns(_silver_plan.source_database, _silver_plan.source_schema, t),
                            {"": _silver_plan.source_table, **_s_parents},
                        )
                        _s_silver_live = _live_columns(
                            lambda t: cached_sf_columns(_silver_plan.target_database, _silver_plan.target_schema, t),
                            {"": _silver_plan.target_table, **{k: f"INT_{v}" for k, v in _s_parents.items()}},
                        )
                        _s_r = scope_filter.render_silver(_sspec, _silver_plan, _s_bronze_live, _s_silver_live)
                        _s_missing = _s_r["missing"]
                        if _s_missing:
                            # ADR 0043 §3: never filter only one side.
                            st.error("Can't apply this filter on both sides — fix the join path above or "
                                     "choose Whole table: " + "; ".join(_s_missing))
                            _silver_scope_ready = False
                        else:
                            st.caption("Bronze recompute query adds (`_FIVETRAN_ACTIVE = TRUE` on every joined table):")
                            st.code(_s_r["bronze_joins"].strip() + "\nWHERE " + _s_r["bronze_where"], language="sql")
                            st.caption("Silver query adds (`INT_` tables, `IS_CURRENT = TRUE` on every joined table):")
                            st.code(_s_r["silver_joins"].strip() + "\nWHERE " + _s_r["silver_where"], language="sql")
                            _silver_plan.population_scope["scope_joins"] = {
                                "bronze": _s_r["bronze_joins"], "silver": _s_r["silver_joins"],
                            }
                            _silver_plan.source_filter = _s_r["bronze_where"]
                            _silver_plan.target_filter = _s_r["silver_where"]
                    _silver_plan.population_scope["scope_filter"] = _sspec.to_dict()

            _silver_output_dir = _ROOT_DIR / "Project" / "config" / "silver"
            _silver_generate_disabled = not (_silver_all_resolved and _silver_pk_ready and _silver_scope_ready)
            if st.button("Generate YAML", type="primary", key=f"{_kp}_generate_btn", disabled=_silver_generate_disabled):
                try:
                    from generated_queries import QueryOutputManager as _SilverQOM
                    _silver_result = _SilverQOM().generate_from_plan(
                        _silver_plan, output_dir=_silver_output_dir, layer="silver",
                    )
                    st.success(f"Generated: {_silver_result.yaml_path}")
                except Exception as exc:
                    st.error(f"Generation failed: {exc}")
            if _silver_generate_disabled:
                st.caption("Resolve every schema-drift column above (pick the natural key, and fix the scope "
                           "filter, if required) to enable Generate.")

