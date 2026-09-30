"""📖 Rule Book tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# TAB: Rule Book
# =============================================================================
def render():
    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown("""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
        <div style="width:40px;height:40px;border-radius:10px;
                    background:linear-gradient(135deg,#4F46E5,#818CF8);
                    display:flex;align-items:center;justify-content:center;font-size:1.2rem;">📖</div>
        <div>
            <div style="font-size:1.25rem;font-weight:800;color:#0F172A;">Rule Book</div>
            <div style="font-size:0.8rem;color:#64748B;">Type-mapping normalization rules — base (always on) and learned (activate to enable)</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Stat chips ────────────────────────────────────────────────────────────
    stats = rule_book.stats()
    _rs1, _rs2, _rs3, _rs4 = st.columns(4)
    _rs1.metric("Base rules", stats["base_rules"], help="Code-defined, always run first")
    _rs2.metric("Learned — active", sum(1 for r in rule_book.learned_rules() if r.status == "active"),
                help="Gap fillers — only for type pairs without a base rule")
    _rs3.metric("Learned — draft", sum(1 for r in rule_book.learned_rules() if r.status != "active"),
                help="Advisory only — never affect generated SQL until activated")
    _rs4.metric("Total", stats["total_rules"])

    # ── Concept explainer ─────────────────────────────────────────────────────
    st.markdown("""
    <div style="background:#F0F4FF;border:1px solid #C7D2FE;border-radius:10px;padding:14px 18px;margin:12px 0;">
    <div style="font-weight:700;color:#3730A3;margin-bottom:8px;">📌 How rules work</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:0.82rem;color:#334155;">
        <div style="background:white;border-radius:8px;padding:10px 12px;border:1px solid #E0E7FF;">
            <div style="font-weight:700;color:#059669;margin-bottom:4px;">🔒 Base rule</div>
            Built into <code>base_rules.py</code>. Always runs first for a type pair.
            <b>Nothing can shadow or override it.</b>
        </div>
        <div style="background:white;border-radius:8px;padding:10px 12px;border:1px solid #E0E7FF;">
            <div style="font-weight:700;color:#6366F1;margin-bottom:4px;">📝 Draft rule</div>
            Saved but not active. Fed to AI as context only.
            <b>Never affects real SQL generation.</b>
        </div>
        <div style="background:white;border-radius:8px;padding:10px 12px;border:1px solid #E0E7FF;">
            <div style="font-weight:700;color:#D97706;margin-bottom:4px;">⚡ Active rule</div>
            A reviewed draft you activated. Acts as a <b>gap filler</b> for type pairs
            with no base rule — cannot replace base rules.
        </div>
    </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Sub-tabs ───────────────────────────────────────────────────────────────
    _rb_base_tab, _rb_learned_tab, _rb_add_tab = st.tabs(
        ["🔒 Base Rules", "⚡ Learned Rules", "➕ Add Rule"]
    )

    # ── BASE RULES sub-tab ────────────────────────────────────────────────────
    with _rb_base_tab:
        st.caption(
            "Read-only — always checked first for any type pair. The SQL shown is the **actual expression** "
            "run against real data. Read it, don't just the description."
        )

        st.markdown("""
        <div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 16px;margin-bottom:12px;font-size:0.83rem;color:#78350F;">
        <b>Key rules to know:</b>
        &nbsp;·&nbsp; <b>Numeric/Decimal</b> — cast to text at native precision (no rounding — drift surfaces as FAIL).
        &nbsp;·&nbsp; <b>Timestamp TZ</b> — converted to UTC first, then microsecond-formatted.
        &nbsp;·&nbsp; <b>UUID</b> — UPPER(TRIM()) normalised — genuine case differences still FAIL.
        </div>
        """, unsafe_allow_html=True)

        # Search filter
        _rb_search = st.text_input("🔍 Filter rules", placeholder="e.g. uuid, timestamp, numeric…", key="rb_search", label_visibility="collapsed")

        def rule_rows(entries):
            rows = []
            for e in entries:
                sample_col = "amount" if "numeric" in e.id or "integer" in e.id else "col"
                rows.append({
                    "ID": e.id, "Name": e.display_name,
                    "Source type": e.source_type, "Target type": e.target_type,
                    "Source SQL": e.pg_sql_template.replace("{col}", sample_col) if e.pg_sql_template else "",
                    "Snowflake SQL": e.sf_sql_template.replace("{col}", sample_col) if e.sf_sql_template else "",
                    "Description": e.description,
                })
            return rows

        _base = rule_book.base_rules()
        if _rb_search:
            _q = _rb_search.lower()
            _base = [r for r in _base if _q in r.id.lower() or _q in (r.source_type or "").lower()
                     or _q in (r.display_name or "").lower()]

        st.dataframe(
            rule_rows(_base),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Source SQL":    st.column_config.TextColumn(width="large"),
                "Snowflake SQL": st.column_config.TextColumn(width="large"),
                "Description":   st.column_config.TextColumn(width="medium"),
            },
        )

    # ── LEARNED RULES sub-tab ─────────────────────────────────────────────────
    with _rb_learned_tab:
        learned = rule_book.learned_rules()
        if not learned:
            st.markdown("""
            <div style="text-align:center;padding:48px 24px;color:#94A3B8;">
                <div style="font-size:2rem;margin-bottom:8px;">📭</div>
                <div style="font-weight:600;font-size:1rem;margin-bottom:4px;">No learned rules yet</div>
                <div style="font-size:0.82rem;">Use the <b>Add Rule</b> tab to paste a type-mapping table
                or fill in the form manually.</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            _active_rules  = [r for r in learned if r.status == "active"]
            _draft_rules   = [r for r in learned if r.status != "active"]

            if _active_rules:
                st.markdown("##### ⚡ Active — used as gap fillers")
                for r in _active_rules:
                    with st.container(border=True):
                        _la, _lb, _lc, _ld = st.columns([3, 2, 2, 1])
                        with _la:
                            st.markdown(f"`{r.id}`")
                            st.caption(r.description or "")
                        _lb.markdown(f"**{r.source_type}** → **{r.target_type}**")
                        _lc.markdown(f"Reuses `{r.reuses_rule}`" if r.reuses_rule else "_No base rule_")
                        with _ld:
                            st.markdown('<span class="badge badge-active">ACTIVE</span>', unsafe_allow_html=True)
                            if st.button("Deactivate", key=f"deact_{r.id}", type="secondary"):
                                rule_book.deactivate_learned_rule(r.id)
                                st.rerun()

            if _draft_rules:
                st.markdown("##### 📝 Draft — advisory only (activate to use)")
                for r in _draft_rules:
                    with st.container(border=True):
                        _la, _lb, _lc, _ld = st.columns([3, 2, 2, 1])
                        with _la:
                            st.markdown(f"`{r.id}`")
                            st.caption(r.description or "")
                        _lb.markdown(f"**{r.source_type}** → **{r.target_type}**")
                        _lc.markdown(f"Reuses `{r.reuses_rule}`" if r.reuses_rule else "_No base rule_")
                        with _ld:
                            st.markdown('<span class="badge badge-draft">DRAFT</span>', unsafe_allow_html=True)
                            if r.reuses_rule:
                                if st.button("Activate", key=f"act_{r.id}", type="primary"):
                                    rule_book.activate_learned_rule(r.id)
                                    flash(f"'{r.id}' is now active — gap filler for {r.source_type} → {r.target_type}.", icon="✅")
                                    st.rerun()
                            else:
                                st.caption("advisory only")

    # ── ADD RULE sub-tab ──────────────────────────────────────────────────────
    with _rb_add_tab:
        _add_ai_tab, _add_manual_tab = st.tabs(["✨ AI-assisted paste", "✏️ Manual form"])

        with _add_ai_tab:
            st.markdown("""
            <div style="font-size:0.85rem;color:#475569;margin-bottom:10px;">
            Paste any type-mapping table or free text — e.g. <code>nvarchar → TEXT</code>, or a full
            MSSQL/Postgres → Snowflake mapping list. The AI <b>can only reuse an existing base rule's
            SQL</b> — it cannot invent new SQL. Rows it can't match are flagged for manual resolution.
            </div>
            """, unsafe_allow_html=True)

            raw_rules_text = st.text_area(
                "Paste type mappings", height=130, key="rule_paste_text",
                placeholder="bit (0,1)      -> BOOLEAN\nmoney          -> NUMBER\nnvarchar       -> TEXT\ntimestamp      -> BINARY",
                label_visibility="collapsed",
            )
            _rp_col1, _rp_col2 = st.columns([3, 1])
            with _rp_col1:
                rule_parse_model = select_or_type(
                    "AI model", available_models_for_ui(), os.getenv("DIAL_MODEL", "gpt-4o"),
                    "rule_parse_model", format_func=_model_label,
                )
            with _rp_col2:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                if st.button("✨ Parse with AI", key="parse_rules_btn", type="primary", use_container_width=True):
                    if not raw_rules_text.strip():
                        st.error("Paste some type mappings first.")
                    else:
                        with st.spinner("Parsing pasted rules…"):
                            try:
                                parser = RuleTypeParser(model=rule_parse_model)
                                proposals = parser.parse(raw_rules_text, rule_book.base_rule_ids())
                                st.session_state["rule_proposals"] = proposals
                            except (AIRuleMappingError, RuleParseError) as exc:
                                st.error(f"Could not parse: {exc}")
                                st.session_state.pop("rule_proposals", None)

            proposals = st.session_state.get("rule_proposals")
            if proposals:
                import pandas as pd

                def _covered(source_type: str, target_type: str) -> bool:
                    from rules import get_rule_for_type_specific
                    return get_rule_for_type_specific(source_type, target_type) is not None

                rows = []
                for p in proposals:
                    covered = _covered(p.source_type, p.target_type)
                    status = "already covered" if covered else ("needs review" if p.needs_review else "new — will save")
                    rows.append({
                        "Save": (not covered) and not p.needs_review,
                        "Source type": p.source_type, "Target type": p.target_type,
                        "Dialect": p.dialect, "Reuses rule": p.reuses_rule or "",
                        "Confidence": p.confidence, "Status": status, "Note": p.note,
                    })
                df = pd.DataFrame(rows)
                st.markdown(f"**{len(proposals)} mapping(s) parsed** — check rows to save as draft rules:")
                edited = st.data_editor(
                    df,
                    column_config={
                        "Save": st.column_config.CheckboxColumn(help="Only rows with a matched base rule and no review flag can be saved."),
                        "Source type": st.column_config.TextColumn(disabled=True),
                        "Target type": st.column_config.TextColumn(disabled=True),
                        "Dialect": st.column_config.TextColumn(disabled=True),
                        "Reuses rule": st.column_config.SelectboxColumn(options=[""] + rule_book.base_rule_ids()),
                        "Confidence": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                        "Status": st.column_config.TextColumn(disabled=True),
                        "Note": st.column_config.TextColumn(disabled=True),
                    },
                    hide_index=True, use_container_width=True, key="rule_proposal_editor",
                )

                if st.button("💾 Save checked rows as draft rules", key="save_rule_proposals", type="primary"):
                    import datetime as _dt
                    import re as _re
                    saved, skipped = 0, 0
                    for _, row in edited.iterrows():
                        if not row["Save"]:
                            continue
                        if not row["Reuses rule"]:
                            skipped += 1
                            continue
                        slug = _re.sub(r"[^a-z0-9]+", "_", f"{row['Dialect']}_{row['Source type']}_{row['Target type']}".lower()).strip("_")
                        entry = RuleEntry(
                            id=f"prompt_{slug}",
                            display_name=f"{row['Source type']} -> {row['Target type']} ({row['Dialect']})",
                            description=row["Note"] or f"Reuses '{row['Reuses rule']}' rule via AI-assisted paste.",
                            when_to_apply=f"source_type={row['Source type']}, target_type={row['Target type']}, dialect={row['Dialect']}",
                            pg_sql_template="", sf_sql_template="",
                            source_type=row["Source type"], target_type=row["Target type"],
                            reuses_rule=row["Reuses rule"],
                            learned_at=_dt.date.today().isoformat(),
                        )
                        try:
                            if rule_book.save_learned_rule(entry):
                                saved += 1
                        except RuleValidationError as exc:
                            st.error(f"'{row['Source type']} → {row['Target type']}' rejected: {exc}")
                            skipped += 1
                    if saved:
                        flash(f"Saved {saved} rule(s) as draft — activate them in the Learned Rules tab.", icon="📖")
                        st.session_state.pop("rule_proposals", None)
                        st.rerun()
                    elif skipped:
                        st.warning("Nothing saved — check that at least one row has 'Save' checked and a 'Reuses rule' chosen.")

        with _add_manual_tab:
            st.caption("New rules start as **draft** (advisory only) — go to Learned Rules and click Activate to make them live gap fillers.")
            with st.form("add_rule_form", border=False):
                _mf1, _mf2 = st.columns(2)
                rule_id      = _mf1.text_input("Rule ID (snake_case)", placeholder="mssql_money_to_number")
                display_name = _mf2.text_input("Display name", placeholder="MONEY → NUMBER")
                description  = st.text_area("Description", height=80, placeholder="What this rule does and when to apply it")
                when_to_apply = st.text_input("When to apply", placeholder="source=MONEY, target=NUMBER, dialect=mssql")
                _mf3, _mf4   = st.columns(2)
                source_type  = _mf3.text_input("Source type", placeholder="MONEY")
                target_type  = _mf4.text_input("Target type", placeholder="NUMBER")
                _mf5, _mf6   = st.columns(2)
                pg_sql_template = _mf5.text_input("Source SQL template", placeholder="CAST({col} AS NUMERIC)")
                sf_sql_template = _mf6.text_input("Snowflake SQL template", placeholder="CAST({col} AS NUMBER)")
                submitted = st.form_submit_button("💾 Save as draft rule", type="primary")

                if submitted:
                    if not rule_id or not display_name:
                        st.error("Rule ID and display name are required.")
                    else:
                        import datetime
                        entry = RuleEntry(
                            id=rule_id, display_name=display_name, description=description,
                            when_to_apply=when_to_apply, pg_sql_template=pg_sql_template,
                            sf_sql_template=sf_sql_template, source_type=source_type,
                            target_type=target_type, is_learned=True,
                            learned_at=datetime.date.today().isoformat(),
                        )
                        try:
                            ok = rule_book.save_learned_rule(entry)
                        except RuleValidationError as exc:
                            st.error(f"Rejected: {exc}")
                            ok = None
                        if ok:
                            flash(f"Learned rule '{rule_id}' saved — activate it in the Learned Rules tab.", icon="📖")
                            st.rerun()
                        elif ok is not None:
                            st.error("Could not save — a rule with this ID may already exist.")

