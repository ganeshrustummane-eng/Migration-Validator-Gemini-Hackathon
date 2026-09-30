"""📘 Guide tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# TAB: Guide
# =============================================================================
def render():
    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown("""
    <div style="display:flex;align-items:center;gap:14px;padding:8px 0 16px;">
        <div style="width:44px;height:44px;border-radius:10px;
                    background:linear-gradient(135deg,#4F46E5,#818CF8);
                    display:flex;align-items:center;justify-content:center;font-size:1.3rem;">📘</div>
        <div>
            <div style="font-size:1.2rem;font-weight:800;color:#0F172A;">Documentation &amp; Guide</div>
            <div style="font-size:0.8rem;color:#64748B;">Everything you need to know — from quickstart to RBAC details</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Quick nav sub-tabs ────────────────────────────────────────────────────
    _g1, _g2, _g3 = st.tabs(["🚀 Quickstart", "⚙️ Features", "✅ Review & Approval"])

    with _g1:
        # Workflow diagram
        st.markdown("""
        <div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
            <div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#94A3B8;margin-bottom:12px;">END-TO-END WORKFLOW</div>
            <div style="display:flex;align-items:center;gap:0;flex-wrap:wrap;">
                <div style="background:#EEF2FF;border:1.5px solid #A5B4FC;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#4338CA;">1 · Connect</div>
                <div style="color:#A5B4FC;font-size:1.2rem;padding:0 6px;">→</div>
                <div style="background:#EEF2FF;border:1.5px solid #A5B4FC;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#4338CA;">2 · Exclusions</div>
                <div style="color:#A5B4FC;font-size:1.2rem;padding:0 6px;">→</div>
                <div style="background:#EEF2FF;border:1.5px solid #A5B4FC;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#4338CA;">3 · Generate YAML</div>
                <div style="color:#A5B4FC;font-size:1.2rem;padding:0 6px;">→</div>
                <div style="background:#EEF2FF;border:1.5px solid #A5B4FC;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#4338CA;">4 · Review &amp; Approve</div>
                <div style="color:#A5B4FC;font-size:1.2rem;padding:0 6px;">→</div>
                <div style="background:#EEF2FF;border:1.5px solid #A5B4FC;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#4338CA;">5 · Run Validation</div>
                <div style="color:#A5B4FC;font-size:1.2rem;padding:0 6px;">→</div>
                <div style="background:#ECFDF5;border:1.5px solid #6EE7B7;border-radius:999px;padding:7px 18px;font-size:0.8rem;font-weight:700;color:#065F46;">✅ Results</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Step cards
        _steps = [
            ("Configure connections", ".env holds your source DB and Snowflake credentials. No credentials are entered inside the app itself — it reads from the environment, keeping secrets out of session state."),
            ("Set exclusion policy", "Open the Exclusions tab and add any columns your team always wants skipped (Fivetran metadata, internal audit columns). These apply globally per source type."),
            ("Generate Single YAML", "Pick source table → Snowflake table. Preview the column mapping, fix any wrong suggestions in the grid, then click Generate. The YAML and SQL files are written to the chosen medallion layer folder."),
            ("Batch-generate for a schema", "Once you're confident one table works, switch to Batch YAML. Select many tables at once, map each to its Snowflake target, and generate in one pass."),
            ("Review & Approve", "Any mapping the AI is less than 95% confident about goes to PENDING. A human reviewer must approve, reject, or modify it before it can be used."),
            ("Run Validation", "Execute the Run Validation tab. It calls Project/main.py against the generated YAMLs and shows PASS/FAIL per table with a row-level diff view for failures."),
        ]
        _sc1, _sc2 = st.columns(2)
        for i, (title, body) in enumerate(_steps):
            col = _sc1 if i % 2 == 0 else _sc2
            col.markdown(f"""
            <div class="step-card" style="margin-bottom:22px;margin-top:18px;">
                <div class="step-num">{i+1}</div>
                <div class="step-title">{title}</div>
                <div class="step-body">{body}</div>
            </div>
            """, unsafe_allow_html=True)

        # What it does box
        st.markdown("""
        <div style="background:#F0FDF4;border:1px solid #86EFAC;border-radius:10px;padding:16px 20px;margin-top:8px;">
        <div style="font-weight:700;color:#166534;margin-bottom:8px;">What Migration Validator produces for every table</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:0.83rem;color:#14532D;">
            <div>📄 <b>Source SQL</b> — normalised SELECT query for the source DB (PostgreSQL / MSSQL / Athena)</div>
            <div>🏔️ <b>Snowflake SQL</b> — matching normalised SELECT for the Snowflake target</div>
            <div>📋 <b>Validation YAML</b> — config that ties both queries together with metadata</div>
            <div>📊 <b>Row-level CSV</b> — PASS/FAIL status per row with source vs. target values side-by-side</div>
        </div>
        </div>
        """, unsafe_allow_html=True)

    with _g2:
        # Column exclusions
        st.markdown("""
        <div style="font-size:1rem;font-weight:700;color:#0F172A;margin:4px 0 12px;">Column exclusion — 3 categories</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px;">
            <div style="background:white;border:1px solid #E2E8F0;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
                <div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#64748B;margin-bottom:6px;">🔒 Built-in</div>
                <div style="font-size:0.83rem;color:#334155;">Fivetran metadata columns hardcoded in the app. Always excluded — no UI to change them.</div>
            </div>
            <div style="background:white;border:1px solid #E2E8F0;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
                <div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#64748B;margin-bottom:6px;">🌐 Global user exclusions</div>
                <div style="font-size:0.83rem;color:#334155;">Managed in the <b>Exclusions</b> tab. Apply to every table of that source type.</div>
            </div>
            <div style="background:white;border:1px solid #E2E8F0;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
                <div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#64748B;margin-bottom:6px;">➕ Run-specific</div>
                <div style="font-size:0.83rem;color:#334155;">The picker on Single/Batch YAML. One-off skip for this generation only — not saved anywhere.</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Rule book
        st.markdown("---")
        st.markdown("""
        <div style="font-size:1rem;font-weight:700;color:#0F172A;margin:4px 0 12px;">Rule Book — how types are normalised</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px;">
            <div style="background:#ECFDF5;border:1px solid #86EFAC;border-radius:10px;padding:14px;">
                <div style="font-weight:700;color:#065F46;margin-bottom:5px;">🔒 Base rules</div>
                <div style="font-size:0.82rem;color:#166534;">In <code>base_rules.py</code>. Always run first. Cannot be overridden.</div>
            </div>
            <div style="background:#EEF2FF;border:1px solid #C7D2FE;border-radius:10px;padding:14px;">
                <div style="font-weight:700;color:#3730A3;margin-bottom:5px;">📝 Draft rules</div>
                <div style="font-size:0.82rem;color:#4338CA;">Saved to <code>rule_book_learned.json</code>. Advisory only — never generate real SQL until activated.</div>
            </div>
            <div style="background:#FEF3C7;border:1px solid #FDE68A;border-radius:10px;padding:14px;">
                <div style="font-weight:700;color:#92400E;margin-bottom:5px;">⚡ Active rules</div>
                <div style="font-size:0.82rem;color:#78350F;">Gap fillers — only for type pairs with no base rule. Activate a draft in the Rule Book tab.</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Custom SQL
        st.markdown("---")
        st.markdown("""
        <div style="font-size:1rem;font-weight:700;color:#0F172A;margin:4px 0 8px;">Custom SQL from a prompt</div>
        <div style="font-size:0.85rem;color:#475569;line-height:1.65;">
        After previewing a column mapping in Single or Batch YAML, a <b>🧪 Generate custom SQL from a prompt</b>
        section appears. Use it when you need something the standard column-diff doesn't cover:
        <ul style="margin:8px 0 0 16px;">
            <li>Aggregate checks — <em>"Count rows where status = 'active', grouped by region"</em></li>
            <li>Filtered subsets — <em>"Compare only orders in the last 30 days"</em></li>
            <li>Lightweight sanity checks — <em>"Row count only, no column comparison"</em></li>
        </ul>
        Always review the generated SQL before trusting it — it's a starting point, not a guarantee.
        </div>
        """, unsafe_allow_html=True)

    with _g3:
        # Confidence tiers
        st.markdown("""
        <div style="font-size:1rem;font-weight:700;color:#0F172A;margin:4px 0 8px;">Confidence tiers &amp; approval gates</div>
        """, unsafe_allow_html=True)
        _conf_tiers = [
            ("≥ 95%", "Auto-accepted", "#ECFDF5", "#065F46", "Proceeds to plan immediately — no human action required"),
            ("75–95%", "Pending review", "#FEF3C7", "#92400E", "Reviewer must approve or reject before mapping is used"),
            ("< 75%", "Mandatory review", "#FFF1F2", "#9F1239", "Reviewer must approve, reject, or modify — cannot skip"),
        ]
        for conf, state, bg, fg, desc in _conf_tiers:
            st.markdown(f"""
            <div style="background:{bg};border-radius:8px;padding:12px 16px;margin-bottom:8px;
                        display:flex;align-items:center;gap:16px;">
                <div style="font-size:1rem;font-weight:800;color:{fg};min-width:60px;text-align:center;">{conf}</div>
                <div>
                    <div style="font-weight:700;color:{fg};font-size:0.85rem;">{state}</div>
                    <div style="font-size:0.8rem;color:#334155;">{desc}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("""
        <div style="font-size:1rem;font-weight:700;color:#0F172A;margin:4px 0 8px;">Human-in-the-loop guarantee</div>
        <div style="font-size:0.83rem;color:#475569;line-height:1.65;">
        Three review decisions are available in the <b>✅ Review &amp; Approve</b> tab:
        <ul style="margin:8px 0 0 16px;">
            <li><b>Approve</b> — accepts the AI mapping as-is. Recorded with your identity and timestamp.</li>
            <li><b>Reject</b> — discards the mapping. It will not be used. Reason is required and recorded.</li>
            <li><b>Modify</b> — accepts the mapping but substitutes a different target column. Both the
                original AI suggestion and your override are recorded for auditability.</li>
        </ul>
        All decisions use <b>optimistic concurrency control (OCC)</b> — two reviewers cannot simultaneously
        approve the same mapping, eliminating race conditions.
        </div>
        """, unsafe_allow_html=True)

