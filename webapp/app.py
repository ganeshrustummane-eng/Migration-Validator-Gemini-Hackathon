"""
Migration Validator — Web UI
==============================
A thin Streamlit UI over the existing validate_cli.py logic. Goal: replace
the multi-step interactive terminal flow (pick source -> pick database ->
pick schema -> pick table -> pick Snowflake table -> exclude y/n -> model
y/n -> confirm -> layer choice) with one page per workflow, using live
dropdowns (discovered from the actual database using .env credentials)
instead of sequential prompts.

This file does NOT reimplement any connection/matching/generation logic —
it imports and calls the same functions validate_cli.py and setup_wizard.py
use, so behavior (and correctness fixes made there) stays identical in both
places.

Run with:
    streamlit run webapp/app.py
"""

import streamlit as st

from ui_common import *  # noqa: F401,F403 -- paths, .env, helpers
import sidebar
import ui_theme
from views import generate_yamls, custom_sql, run_validation, history, rule_book, exclusions, jira, usage, guide, output_files

st.set_page_config(
    page_title="Migration Validator · Enterprise",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="expanded",
)

ui_theme.apply()

_show_pending_flash()

with st.sidebar:
    sidebar.render()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.markdown("""
<div style="display:flex;align-items:center;gap:16px;padding:20px 0 8px;">
    <div style="width:48px;height:48px;border-radius:12px;
                background:linear-gradient(135deg,#4F46E5 0%,#818CF8 100%);
                display:flex;align-items:center;justify-content:center;
                font-size:1.5rem;box-shadow:0 4px 12px rgba(79,70,229,.35);flex-shrink:0;">🔷</div>
    <div>
        <div style="font-size:1.55rem;font-weight:800;color:#0F172A;letter-spacing:-0.02em;line-height:1.1;">
            Migration Validator</div>
        <div style="font-size:0.82rem;color:#64748B;margin-top:3px;font-weight:500;">
            PostgreSQL · MSSQL · Athena &nbsp;→&nbsp; Snowflake &nbsp;|&nbsp;
            AI-powered column mapping &nbsp;|&nbsp; Governed approval workflow
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

tab_batch, tab_custom, tab_execute, tab_history, tab_rules, tab_excl, tab_jira, tab_usage, tab_guide, tab_files = st.tabs(
    ["📋 Generate YAMLs",
     "✍️ Custom SQL Validation",
     "🚀 Run Validation", "📈 History & Trends",
     "📖 Rule Book", "🚫 Exclusions", "🎫 My Jira Tickets", "💰 Usage & Cost", "📘 Guide",
     "📂 Output Files"]
)

with tab_batch:
    generate_yamls.render()

with tab_custom:
    custom_sql.render()

with tab_execute:
    run_validation.render()

with tab_history:
    history.render()

with tab_rules:
    rule_book.render()

with tab_excl:
    exclusions.render()

sidebar.render_scheduler()

with tab_jira:
    jira.render()

with tab_usage:
    usage.render()

with tab_guide:
    guide.render()

with tab_files:
    output_files.render()
