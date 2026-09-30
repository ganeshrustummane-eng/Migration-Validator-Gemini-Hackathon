"""✍️ Custom SQL Validation tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# TAB: Custom SQL Validation
# DQE writes their own source + target SQL (any join, grain, aggregation, etc.)
# for N validations, picks PK columns, and we write the YAML directly.
# No AI column-mapping involved — DQE owns the query.
# =============================================================================
def render():
    import yaml as _yaml
    import pandas as pd

    st.markdown("""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
        <div style="width:40px;height:40px;border-radius:10px;
                    background:linear-gradient(135deg,#4F46E5,#818CF8);
                    display:flex;align-items:center;justify-content:center;font-size:1.2rem;">✍️</div>
        <div>
            <div style="font-size:1.25rem;font-weight:800;color:#0F172A;">Custom SQL Validation</div>
            <div style="font-size:0.8rem;color:#64748B;">
                Write your own source + Snowflake SQL for any grain, join, or business logic.
                Add as many validations as you need — each becomes one entry in the generated YAML.
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.info(
        "Use this tab when the standard column-mapping flow isn't enough — e.g. monthly grain aggregations, "
        "multi-table joins, dedup checks, GL line item rollups, or any DQ rule expressed as SQL. "
        "You write both the source and Snowflake queries; we build the YAML.",
        icon="💡",
    )

    # ── Step 1 — Connection ───────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**① Source connection & database/schema**")
        cst_registry = load_registry()
        cst_rec = select_connection(cst_registry, key="cst_conn")

        if cst_rec:
            _override_source_env(cst_rec)
            cst_db_type = cst_rec["db_type"]
            cst_password = source_password(cst_rec)

            if cst_db_type == "athena":
                cst_database = cst_rec["database"]
                cst_schema = cst_rec["schema"]
                st.caption(f"Athena: using Glue database **{cst_database}** (fixed from .env)")
            else:
                cst_dbs = cached_source_databases(
                    cst_db_type, cst_rec["host"], int(cst_rec.get("port") or 0),
                    cst_rec["username"], cst_password, cst_rec.get("auth", ""),
                )
                _cc1, _cc2 = st.columns(2)
                with _cc1:
                    cst_database = select_or_type("Source database", cst_dbs, cst_rec["database"], "cst_db")
                cst_schemas = cached_source_schemas(
                    cst_db_type, cst_rec["host"], int(cst_rec.get("port") or 0),
                    cst_database, cst_rec["username"], cst_password, cst_rec.get("auth", ""),
                )
                with _cc2:
                    cst_schema = select_or_type("Source schema", cst_schemas, cst_rec["schema"], "cst_schema")
        else:
            cst_rec = None
            cst_database = cst_schema = cst_db_type = ""

    # ── Step 2 — Snowflake target connection ──────────────────────────────────
    with st.container(border=True):
        st.markdown("**② Snowflake target database/schema**")
        cst_sf_database, cst_sf_schema, _ = pick_snowflake_target("", "cst_sf", include_table=False)

    # ── Step 3 — Layer ────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**③ Medallion layer**")
        cst_layer, cst_output_dir = pick_layer("cst_layer")

    # Session-state list of validation entries — defined here so both the
    # AI generator (Step 4) and the manual entry grid (Step 5) can access it.
    _CST_KEY = "cst_entries"
    if _CST_KEY not in st.session_state:
        st.session_state[_CST_KEY] = []

    def _cst_blank_entry(idx: int) -> dict:
        return {
            "id": idx,
            "name": f"validation_{idx + 1}",
            "description": "",
            "source_sql": "",
            "target_sql": "",
            "pk_source": "",
            "pk_target": "",
            "validation_type": "data",
        }

    # ── Step 4 — AI SQL Generator (schema-aware) ─────────────────────────────
    st.markdown("**④ AI SQL Generator — describe what you need, AI writes the SQL**")
    st.caption(
        "Select tables from the source schema, describe your query in plain English "
        "(joins across tables, aggregations, grain, DQ checks — anything). "
        "The AI sees the full column schema of every selected table and writes "
        "dialect-correct SQL for your source DB. Generated SQL is added to the "
        "manual entries below for you to review and optionally pair with a Snowflake query."
    )

    with st.container(border=True):
        # Only active once a connection + schema are picked
        _ai_ready = bool(cst_rec and cst_schema)

        if not _ai_ready:
            st.info("Select a source connection and schema above (Steps ① and ②) to enable AI SQL generation.", icon="👆")
        else:
            # ── Table selector ───────────────────────────────────────────────
            try:
                _ai_tables = cached_source_tables(
                    cst_db_type, cst_rec["host"], int(cst_rec.get("port") or 0),
                    cst_database, cst_rec["username"], source_password(cst_rec),
                    cst_rec.get("auth", ""), cst_rec.get("s3_output", ""), cst_schema,
                )
            except Exception as _exc:
                _ai_tables = []
                st.warning(f"Could not list tables: {_exc}")

            # shared model picker above both sections
            ai_model = select_or_type(
                "AI model", available_models_for_ui(),
                os.getenv("DIAL_MODEL", "gpt-4o"),
                "cst_ai_model", format_func=_model_label,
            )

            # shared prompt + validation name
            ai_prompt = st.text_area(
                "Describe the SQL you need (plain English — same description drives both source and target generation)",
                key="cst_ai_prompt",
                height=100,
                placeholder=(
                    "Examples:\n"
                    "• Employees with department name, location, budget and salary (base + total) ordered by salary desc.\n"
                    "• Monthly total sales by region — only active customers, grain = month + region.\n"
                    "• Find duplicate email addresses across the contacts table."
                ),
            )

            _ai_validation_name = st.text_input(
                "Validation name",
                value="ai_generated_check",
                key="cst_ai_val_name",
                placeholder="e.g. employee_salary_check",
            )

            st.markdown("---")

            _ai_normalize = st.radio(
                "Query style",
                options=["Validation query (COALESCE / <<NULL>> normalized)", "Simple query (plain readable SQL)"],
                index=0,
                horizontal=True,
                key="cst_ai_normalize",
                help=(
                    "**Validation query** — adds COALESCE(CAST(...), '<<NULL>>') to every column so "
                    "the comparison engine can detect NULLs vs empty strings. Use this when you will "
                    "paste the result into the YAML and run validation.\n\n"
                    "**Simple query** — clean readable SQL, no normalization wrappers. Use this to "
                    "explore data or hand-write your own normalization."
                ),
            )
            _ai_do_normalize = "Simple" not in _ai_normalize

            # ── Two side-by-side generation sections ─────────────────────────
            _AI_SQL_KEY = "cst_ai_result_sql"
            _SF_SQL_KEY = "cst_ai_sf_result_sql"

            _sec_src, _sec_sf = st.columns(2)

            # ── LEFT: Source SQL generation ───────────────────────────────────
            with _sec_src:
                st.markdown(f"**🗄️ Source SQL — {cst_db_type.upper()}**")
                ai_selected_tables = st.multiselect(
                    "Tables to include",
                    options=_ai_tables,
                    key="cst_ai_tables",
                    placeholder="Pick source tables…",
                )
                # Column preview + filter — only show when exactly one table picked
                _src_col_filter = {}
                if ai_selected_tables:
                    for _pt in ai_selected_tables:
                        try:
                            _all_cols = cached_source_columns(
                                cst_db_type, cst_rec["host"], int(cst_rec.get("port") or 0),
                                cst_database, cst_rec["username"], source_password(cst_rec),
                                cst_rec.get("auth", ""), cst_rec.get("s3_output", ""), cst_schema, _pt,
                            )
                            _picked_cols = st.multiselect(
                                f"Columns from {_pt} (leave blank = all)",
                                options=_all_cols,
                                key=f"cst_src_cols_{_pt}",
                                placeholder="All columns included by default",
                            )
                            _src_col_filter[_pt] = _picked_cols or _all_cols
                        except Exception:
                            pass

                _do_gen_src = st.button(
                    f"✨ Generate {cst_db_type.upper()} SQL",
                    type="primary",
                    key="cst_ai_generate",
                    disabled=not (ai_selected_tables and ai_prompt.strip()),
                )
                if not ai_selected_tables:
                    st.caption("Select at least one table to enable.")

                if _do_gen_src:
                    _schema_ctx = {}
                    with st.spinner("Loading source schema…"):
                        for _tbl in ai_selected_tables:
                            try:
                                _ext = ExtractorFactory.create(
                                    cst_db_type,
                                    host=cst_rec["host"],
                                    port=int(cst_rec.get("port") or 0),
                                    database=cst_database,
                                    username=cst_rec["username"],
                                    password=source_password(cst_rec),
                                    auth=cst_rec.get("auth", ""),
                                    s3_output=cst_rec.get("s3_output", ""),
                                )
                                _col_metas = _ext.extract_columns(cst_schema, _tbl)
                                _allowed = set(_src_col_filter.get(_tbl) or [c.column_name for c in _col_metas])
                                _schema_ctx[f"{cst_schema}.{_tbl}"] = [
                                    {
                                        "column_name": c.column_name,
                                        "data_type": c.data_type,
                                        "is_nullable": c.is_nullable,
                                        "is_primary_key": c.is_primary_key,
                                    }
                                    for c in _col_metas if c.column_name in _allowed
                                ]
                            except Exception as _exc:
                                st.warning(f"Could not load schema for {_tbl}: {_exc}")

                    if _schema_ctx:
                        # Detect PKs for comment + auto-fill
                        _src_pks = [
                            col["column_name"]
                            for tbl_cols in _schema_ctx.values()
                            for col in tbl_cols
                            if col.get("is_primary_key")
                        ]
                        with st.spinner("Generating source SQL…"):
                            try:
                                _gen = AISQLQueryGenerator(model=ai_model)
                                _result = _gen.generate_schema_aware_query(
                                    user_instruction=ai_prompt.strip(),
                                    schema_context=_schema_ctx,
                                    db_type=cst_db_type,
                                    default_schema=cst_schema,
                                    normalize=_ai_do_normalize,
                                )
                                _pk_comment = f"-- PK: {', '.join(_src_pks)}\n" if _src_pks else ""
                                # No PK detected → row-hash SQL only for single-table selections.
                                # Multi-table = join; the FROM can't be inferred, user must alias a PK in SQL.
                                _row_hash_sql = ""
                                if not _src_pks and len(_schema_ctx) == 1:
                                    _single_tbl_cols = next(iter(_schema_ctx.values()))
                                    _single_tbl_fqn  = next(iter(_schema_ctx))
                                    _row_hash_sql = _build_row_hash_sql(_single_tbl_cols, _single_tbl_fqn, cst_db_type)
                                st.session_state[_AI_SQL_KEY] = {
                                    "sql": _pk_comment + _result.query,
                                    "confidence": _result.confidence,
                                    "prompt": ai_prompt.strip(),
                                    "schema_ctx": _schema_ctx,
                                    "pks": _src_pks,
                                    "row_hash_sql": _row_hash_sql,
                                }
                            except AISQLGenerationError as _exc:
                                st.error(f"Source SQL generation failed: {_exc}")
                                st.session_state.pop(_AI_SQL_KEY, None)
                    else:
                        st.error("Could not load column schema — check connection.")

                _ai_result = st.session_state.get(_AI_SQL_KEY)
                if _ai_result:
                    st.markdown(
                        f"<div style='font-size:0.8rem;font-weight:600;color:#4F46E5;margin-top:8px;'>"
                        f"Generated {cst_db_type.upper()} SQL "
                        f"<span style='color:#059669;margin-left:6px;'>confidence {int(_ai_result['confidence']*100)}%</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    _detected_src_pks = _ai_result.get("pks") or []
                    _src_pk_default = ", ".join(_detected_src_pks) if _detected_src_pks else "row_hash"
                    _src_pk_override = st.text_input(
                        "Detected Source PK — edit if needed (comma-separated for composite; 'row_hash' = no-PK mode)",
                        value=_src_pk_default,
                        key="cst_ai_src_pk_override",
                        help="Auto-detected from information_schema. 'row_hash' means no PK was found; a hash-based query is shown below.",
                    )
                    if not _detected_src_pks:
                        if _ai_result.get("row_hash_sql"):
                            st.warning("No primary key detected. Use the row-hash query below as your source SQL — it hashes every column so rows can be compared without a PK.")
                            st.code(_ai_result["row_hash_sql"], language="sql")
                        else:
                            st.warning("No primary key detected and multiple tables selected — row-hash requires a single table. Add an alias column in your SQL to use as PK (e.g. `ROW_NUMBER() OVER (...) AS row_id`).")
                    st.code(_ai_result["sql"], language="sql")
                    # Push the overridden PK back so "Add entry" can read it
                    _ai_result["_pk_override"] = _src_pk_override
                    if st.button("🔄 Regenerate source", key="cst_ai_regen_src"):
                        st.session_state.pop(_AI_SQL_KEY, None)
                        st.rerun()

            # ── RIGHT: Snowflake SQL generation ──────────────────────────────
            with _sec_sf:
                st.markdown("**❄️ Snowflake SQL (target)**")
                _sf_tbl_opts = []
                if cst_sf_database and cst_sf_schema:
                    try:
                        _sf_tbl_opts = cached_sf_tables(cst_sf_database, cst_sf_schema)
                    except Exception:
                        pass
                ai_selected_sf_tables = st.multiselect(
                    "Snowflake tables to include",
                    options=_sf_tbl_opts,
                    key="cst_ai_sf_tables",
                    placeholder="Pick Snowflake tables…" if _sf_tbl_opts else "Select Snowflake schema first (Step ②)",
                    disabled=not _sf_tbl_opts,
                )
                _sf_col_filter = {}
                if ai_selected_sf_tables:
                    for _sft in ai_selected_sf_tables:
                        try:
                            _sf_all_cols = cached_sf_columns(cst_sf_database, cst_sf_schema, _sft)
                            _sf_picked = st.multiselect(
                                f"Columns from {_sft} (leave blank = all)",
                                options=_sf_all_cols,
                                key=f"cst_sf_cols_{_sft}",
                                placeholder="All columns included by default",
                            )
                            _sf_col_filter[_sft] = _sf_picked or _sf_all_cols
                        except Exception:
                            pass

                _do_gen_sf = st.button(
                    "✨ Generate Snowflake SQL",
                    type="primary",
                    key="cst_ai_sf_generate",
                    disabled=not (ai_selected_sf_tables and ai_prompt.strip()),
                )
                if not _sf_tbl_opts:
                    st.caption("Complete Step ② (Snowflake schema) to enable.")
                elif not ai_selected_sf_tables:
                    st.caption("Select at least one Snowflake table to enable.")

                if _do_gen_sf:
                    _sf_schema_ctx = {}
                    with st.spinner("Loading Snowflake schema…"):
                        for _tbl in ai_selected_sf_tables:
                            try:
                                _sf_col_metas = SnowflakeExtractor(database=cst_sf_database).extract_columns(cst_sf_schema, _tbl)
                                _sf_allowed = set(_sf_col_filter.get(_tbl) or [c.column_name for c in _sf_col_metas])
                                _sf_schema_ctx[f"{cst_sf_schema}.{_tbl}"] = [
                                    {
                                        "column_name": c.column_name,
                                        "data_type": c.data_type,
                                        "is_nullable": c.is_nullable,
                                        "is_primary_key": c.is_primary_key,
                                    }
                                    for c in _sf_col_metas if c.column_name in _sf_allowed
                                ]
                            except Exception as _exc:
                                st.warning(f"Could not load Snowflake schema for {_tbl}: {_exc}")

                    if _sf_schema_ctx:
                        _sf_pks = [
                            col["column_name"]
                            for tbl_cols in _sf_schema_ctx.values()
                            for col in tbl_cols
                            if col.get("is_primary_key")
                        ]
                        with st.spinner("Generating Snowflake SQL…"):
                            try:
                                _sf_gen = AISQLQueryGenerator(model=ai_model)
                                _sf_result = _sf_gen.generate_schema_aware_query(
                                    user_instruction=ai_prompt.strip(),
                                    schema_context=_sf_schema_ctx,
                                    db_type="snowflake",
                                    default_schema=cst_sf_schema,
                                    normalize=_ai_do_normalize,
                                )
                                _sf_pk_comment = f"-- PK: {', '.join(_sf_pks)}\n" if _sf_pks else ""
                                _sf_row_hash_sql = ""
                                if not _sf_pks and len(_sf_schema_ctx) == 1:
                                    _sf_single_cols = next(iter(_sf_schema_ctx.values()))
                                    _sf_single_fqn  = next(iter(_sf_schema_ctx))
                                    _sf_row_hash_sql = _build_row_hash_sql(_sf_single_cols, _sf_single_fqn, "snowflake")
                                st.session_state[_SF_SQL_KEY] = {
                                    "sql": _sf_pk_comment + _sf_result.query,
                                    "confidence": _sf_result.confidence,
                                    "pks": _sf_pks,
                                    "row_hash_sql": _sf_row_hash_sql,
                                }
                            except AISQLGenerationError as _exc:
                                st.error(f"Snowflake SQL generation failed: {_exc}")
                                st.session_state.pop(_SF_SQL_KEY, None)
                    else:
                        st.error("Could not load Snowflake column schema — check connection.")

                _sf_ai_result = st.session_state.get(_SF_SQL_KEY)
                if _sf_ai_result:
                    st.markdown(
                        f"<div style='font-size:0.8rem;font-weight:600;color:#059669;margin-top:8px;'>"
                        f"Generated Snowflake SQL "
                        f"<span style='color:#64748B;margin-left:6px;'>confidence {int(_sf_ai_result['confidence']*100)}%</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    _detected_sf_pks = _sf_ai_result.get("pks") or []
                    _sf_pk_default = ", ".join(_detected_sf_pks) if _detected_sf_pks else "row_hash"
                    _sf_pk_override = st.text_input(
                        "Detected Snowflake PK — edit if needed",
                        value=_sf_pk_default,
                        key="cst_ai_sf_pk_override",
                        help="Auto-detected from Snowflake information_schema. 'row_hash' = no-PK hash mode.",
                    )
                    if not _detected_sf_pks:
                        if _sf_ai_result.get("row_hash_sql"):
                            st.warning("No PK detected on Snowflake side. Use the row-hash query below.")
                            st.code(_sf_ai_result["row_hash_sql"], language="sql")
                        else:
                            st.warning("No PK detected and multiple Snowflake tables selected — row-hash requires a single table.")
                    st.code(_sf_ai_result["sql"], language="sql")
                    _sf_ai_result["_pk_override"] = _sf_pk_override
                    if st.button("🔄 Regenerate Snowflake", key="cst_ai_regen_sf"):
                        st.session_state.pop(_SF_SQL_KEY, None)
                        st.rerun()

            st.markdown("---")

            # ── Add as validation entry ───────────────────────────────────────
            _ai_result = st.session_state.get(_AI_SQL_KEY)
            _sf_ai_result = st.session_state.get(_SF_SQL_KEY)
            _can_add = bool(_ai_result or _sf_ai_result)
            if _can_add:
                if st.button("➕ Add as validation entry below", key="cst_ai_add_entry", type="primary"):
                    nxt = len(st.session_state.get(_CST_KEY, []))
                    new_entry = _cst_blank_entry(nxt)
                    new_entry["name"] = _ai_validation_name.strip().replace(" ", "_").lower() or f"ai_check_{nxt+1}"
                    new_entry["description"] = f"AI-generated — {(_ai_result or _sf_ai_result or {}).get('prompt', ai_prompt)[:80]}"
                    if _ai_result:
                        # Use row-hash SQL when no PK was found and the user kept 'row_hash'
                        _src_pk_val = _ai_result.get("_pk_override") or ", ".join(_ai_result.get("pks") or [])
                        if _src_pk_val.strip().lower() == "row_hash" and _ai_result.get("row_hash_sql"):
                            new_entry["source_sql"] = _ai_result["row_hash_sql"]
                        else:
                            new_entry["source_sql"] = _ai_result["sql"]
                        new_entry["pk_source"] = _src_pk_val
                    if _sf_ai_result:
                        _sf_pk_val = _sf_ai_result.get("_pk_override") or ", ".join(_sf_ai_result.get("pks") or [])
                        if _sf_pk_val.strip().lower() == "row_hash" and _sf_ai_result.get("row_hash_sql"):
                            new_entry["target_sql"] = _sf_ai_result["row_hash_sql"]
                        else:
                            new_entry["target_sql"] = _sf_ai_result["sql"]
                        new_entry["pk_target"] = _sf_pk_val
                    if _CST_KEY not in st.session_state:
                        st.session_state[_CST_KEY] = []
                    st.session_state[_CST_KEY].append(new_entry)
                    st.session_state.pop(_AI_SQL_KEY, None)
                    st.session_state.pop(_SF_SQL_KEY, None)
                    _sides = []
                    if _ai_result:
                        _sides.append("source")
                    if _sf_ai_result:
                        _sides.append("Snowflake")
                    flash(f"Added '{new_entry['name']}' ({' + '.join(_sides)} SQL) — review the entry below.", icon="✅")
                    st.rerun()

    # ── Step 5 — Validation entries ───────────────────────────────────────────
    st.markdown("**⑤ Validation entries**")
    st.caption(
        "Add one entry per logical check. Each entry gets its own YAML block. "
        "Source SQL runs against your source DB; Snowflake SQL runs against your Snowflake target. "
        "The PK column(s) are used to align rows for comparison — use the same logical key in both queries."
    )

    # Add / remove buttons
    _btn_c1, _btn_c2 = st.columns([1, 5])
    with _btn_c1:
        if st.button("➕ Add validation", key="cst_add"):
            nxt = len(st.session_state[_CST_KEY])
            st.session_state[_CST_KEY].append(_cst_blank_entry(nxt))
            st.rerun()

    if not st.session_state[_CST_KEY]:
        st.markdown("""
        <div style="background:#F8FAFC;border:2px dashed #CBD5E1;border-radius:12px;
                    padding:32px;text-align:center;color:#94A3B8;margin:16px 0;">
            <div style="font-size:2rem;margin-bottom:8px;">📝</div>
            <div style="font-size:0.95rem;font-weight:600;">No validations yet</div>
            <div style="font-size:0.82rem;margin-top:4px;">
                Click <strong>➕ Add validation</strong> above to write your first SQL check.
            </div>
        </div>
        """, unsafe_allow_html=True)

    to_delete = []
    for idx, entry in enumerate(st.session_state[_CST_KEY]):
        entry_key = f"cst_entry_{idx}"
        with st.expander(
            f"**{entry['name'] or f'Validation {idx+1}'}** — {entry.get('description','') or 'click to expand'}",
            expanded=True,
        ):
            _e1, _e2, _e3 = st.columns([3, 4, 1])
            with _e1:
                entry["name"] = st.text_input(
                    "Validation name (used as YAML key)",
                    value=entry["name"], key=f"{entry_key}_name",
                    placeholder="e.g. monthly_sales_grain",
                )
            with _e2:
                entry["description"] = st.text_input(
                    "Description (optional)",
                    value=entry["description"], key=f"{entry_key}_desc",
                    placeholder="e.g. Monthly sales by region, joined with dim_customer",
                )
            with _e3:
                entry["validation_type"] = st.selectbox(
                    "Type", ["data", "count"], key=f"{entry_key}_vtype",
                    index=0 if entry["validation_type"] == "data" else 1,
                    help="data = row-level diff with PK alignment; count = row count only",
                )

            _s1, _s2 = st.columns(2)
            with _s1:
                st.markdown(
                    f"<div style='font-size:0.8rem;font-weight:600;color:#4F46E5;margin-bottom:4px;'>"
                    f"Source SQL ({_DB_TYPE_LABELS.get(cst_db_type, cst_db_type) or 'source'})"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                _src_hints = {
                    "mssql":      "SELECT\n    FORMAT(order_date,'yyyy-MM') AS month,\n    region,\n    SUM(amount) AS total_sales\nFROM dbo.sales s\nJOIN dbo.dim_customer c ON s.customer_id=c.id\nGROUP BY FORMAT(order_date,'yyyy-MM'), region",
                    "athena":     "SELECT\n    date_trunc('month', order_date) AS month,\n    region,\n    SUM(amount) AS total_sales\nFROM schema.sales s\nJOIN schema.dim_customer c ON s.customer_id=c.id\nGROUP BY 1, 2",
                    "postgresql": "SELECT\n    DATE_TRUNC('month', order_date) AS month,\n    region,\n    SUM(amount)                    AS total_sales\nFROM sales s\nJOIN dim_customer c ON s.customer_id=c.id\nGROUP BY 1, 2",
                }
                entry["source_sql"] = st.text_area(
                    "Source SQL",
                    value=entry["source_sql"], key=f"{entry_key}_src_sql",
                    height=220,
                    placeholder=_src_hints.get(cst_db_type, _src_hints["postgresql"]),
                    label_visibility="collapsed",
                )
            with _s2:
                st.markdown(
                    "<div style='font-size:0.8rem;font-weight:600;color:#059669;margin-bottom:4px;'>"
                    "Snowflake SQL (target)"
                    "</div>",
                    unsafe_allow_html=True,
                )
                entry["target_sql"] = st.text_area(
                    "Snowflake SQL",
                    value=entry["target_sql"], key=f"{entry_key}_tgt_sql",
                    height=220,
                    placeholder=(
                        "SELECT\n"
                        "    DATE_TRUNC('month', ORDER_DATE) AS MONTH,\n"
                        "    REGION,\n"
                        "    SUM(AMOUNT)                     AS TOTAL_SALES\n"
                        "FROM DWH.SALES S\n"
                        "JOIN DWH.DIM_CUSTOMER C ON S.CUSTOMER_ID = C.ID\n"
                        "GROUP BY 1, 2"
                    ),
                    label_visibility="collapsed",
                )

            if entry["validation_type"] == "data":
                _pk1, _pk2 = st.columns(2)
                with _pk1:
                    entry["pk_source"] = st.text_input(
                        "Source PK column(s) — comma-separated if composite",
                        value=entry["pk_source"], key=f"{entry_key}_pk_src",
                        placeholder="e.g. month, region",
                    )
                with _pk2:
                    entry["pk_target"] = st.text_input(
                        "Snowflake PK column(s) — must align with source PK",
                        value=entry["pk_target"], key=f"{entry_key}_pk_tgt",
                        placeholder="e.g. MONTH, REGION",
                    )

            if st.button("🗑️ Remove this validation", key=f"{entry_key}_del", type="secondary"):
                to_delete.append(idx)

    if to_delete:
        for idx in sorted(to_delete, reverse=True):
            st.session_state[_CST_KEY].pop(idx)
        st.rerun()

    # ── Step 6 — YAML preview + save ─────────────────────────────────────────
    st.divider()
    entries = st.session_state.get(_CST_KEY, [])
    valid_entries = [e for e in entries if e.get("source_sql", "").strip() and e.get("target_sql", "").strip() and e.get("name", "").strip()]

    if valid_entries and cst_rec:
        _tables_dict = {}
        for e in valid_entries:
            vname = e["name"].strip().replace(" ", "_")
            block_type = "data_validation" if e["validation_type"] == "data" else "count_validation"
            inner = {
                "source_table_name": vname,
                "source": cst_db_type or "postgresql",
                "source_database": cst_database,
                "source_schema": cst_schema,
                "sourcequery": " ".join(e["source_sql"].split()),
                "target_table_name": vname,
                "target": "snowflake",
                "target_database": cst_sf_database,
                "target_schema": cst_sf_schema,
                "targetquery": " ".join(e["target_sql"].split()),
            }
            if block_type == "data_validation":
                pk_src = [c.strip() for c in e["pk_source"].split(",") if c.strip()]
                pk_tgt = [c.strip() for c in e["pk_target"].split(",") if c.strip()]
                if pk_src:
                    inner["sourcecolumn"] = pk_src if len(pk_src) > 1 else pk_src[0]
                if pk_tgt:
                    inner["targetcolumn"] = pk_tgt if len(pk_tgt) > 1 else pk_tgt[0]
            _tables_dict[vname] = {"validations": {block_type: inner}}

        vtype_folder = "data_validation"
        yaml_stem = valid_entries[0]["name"].strip().replace(" ", "_") if len(valid_entries) == 1 else "custom_validations"
        yaml_payload = {"tables": _tables_dict}

        yaml_str = _yaml.dump(yaml_payload, default_flow_style=False, sort_keys=False, allow_unicode=True, Dumper=_yaml.SafeDumper)

        # ── Inline schema validation (no tempfile — we already have the dict) ─
        from validation.config_schema import ValidationConfigDocument
        from pydantic import ValidationError as _PydanticValidationError
        _yaml_errors = []
        try:
            ValidationConfigDocument.model_validate(yaml_payload)
        except _PydanticValidationError as _ve:
            _yaml_errors = [
                f"{'.'.join(str(p) for p in e['loc'])} — {e['msg']}"
                for e in _ve.errors()
            ]

        with st.expander("📄 YAML preview", expanded=True):
            st.code(yaml_str, language="yaml")
            if _yaml_errors:
                st.error("**Schema errors — fix before saving:**")
                for _msg in _yaml_errors:
                    st.markdown(f"- `{_msg}`")
            else:
                st.success("✓ YAML passes schema validation", icon="✅")

        _save_c1, _save_c2, _save_c3 = st.columns([2, 2, 3])
        with _save_c1:
            custom_yaml_filename = st.text_input(
                "Output filename (without .yaml)",
                value=yaml_stem, key="cst_yaml_filename",
            )
        with _save_c2:
            report_subfolder = st.text_input(
                "Report subfolder (optional)",
                value="", key="cst_report_subfolder",
                placeholder="e.g. emanagement",
                help="If set, saves under config/report/<subfolder>/data_validation/ (independent of layer). Leave blank to save under config/<layer>/data_validation/.",
            )
        with _save_c3:
            st.markdown("<div style='height:28px'/>", unsafe_allow_html=True)
            if st.button("💾 Save YAML to config folder", type="primary", key="cst_save",
                         disabled=bool(_yaml_errors)):
                if report_subfolder.strip():
                    save_dir = _ROOT_DIR / "Project" / "config" / "report" / report_subfolder.strip() / vtype_folder
                else:
                    save_dir = cst_output_dir / vtype_folder
                save_path = save_dir / f"{custom_yaml_filename}.yaml"
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(yaml_str, encoding="utf-8")
                flash(f"Saved: {save_path}", icon="💾")
                st.rerun()

    elif entries and not valid_entries:
        st.warning("Fill in at least a name + source SQL + Snowflake SQL to preview the YAML.")
    elif not entries:
        pass  # empty state already shown above
    elif not cst_rec:
        st.warning("Select a source connection above before saving.")

