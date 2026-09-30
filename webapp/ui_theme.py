"""Global CSS theme, injected on every rerun by app.py (ADR 0050)."""
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL ENTERPRISE THEME
# A single, coherent CSS block that covers: tab bar, metrics, buttons, forms,
# expanders, code blocks, badges, and the chat widget.
# Palette: indigo-600 (#4F46E5) primary, slate-900 (#0F172A) heading text,
#          emerald-600 (#059669) success, rose-600 (#E11D48) danger,
#          amber-500 (#F59E0B) warning — all WCAG AA against white.
# ─────────────────────────────────────────────────────────────────────────────

def apply():
    st.markdown("""
    <style>
    /* ── Google Fonts ─────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* ── Root variables ───────────────────────────────────────────── */
    :root {
        --primary:        #4F46E5;
        --primary-light:  #818CF8;
        --primary-xlight: #EEF2FF;
        --success:        #059669;
        --success-bg:     #ECFDF5;
        --danger:         #E11D48;
        --danger-bg:      #FFF1F2;
        --warning:        #D97706;
        --warning-bg:     #FFFBEB;
        --neutral-50:     #F8FAFC;
        --neutral-100:    #F1F5F9;
        --neutral-200:    #E2E8F0;
        --neutral-700:    #334155;
        --neutral-900:    #0F172A;
        --radius-sm:      6px;
        --radius-md:      10px;
        --radius-lg:      16px;
        --shadow-sm:      0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.06);
        --shadow-md:      0 4px 12px rgba(0,0,0,.12);
    }

    /* ── Base font ────────────────────────────────────────────────── */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }

    /* ── App background ───────────────────────────────────────────── */
    [data-testid="stAppViewContainer"] > .main {
        background: #F8FAFC;
    }
    [data-testid="stSidebar"] {
        background: #1E1B4B !important;
        border-right: 1px solid #312E81;
    }
    [data-testid="stSidebar"] * { color: #E0E7FF !important; }

    /* Sidebar code blocks — keep dark background but use readable light text */
    [data-testid="stSidebar"] .stCode,
    [data-testid="stSidebar"] pre,
    [data-testid="stSidebar"] [data-testid="stCode"],
    [data-testid="stSidebar"] [data-testid="stCode"] pre,
    [data-testid="stSidebar"] [data-testid="stCode"] code {
        background: #0F172A !important;
        color: #BAC8FF !important;
        border: 1px solid #312E81 !important;
    }

    /* Main area code blocks — light background, dark text (fixes the original issue) */
    .main .stCode,
    .main pre,
    .main [data-testid="stCode"],
    .main [data-testid="stCode"] pre,
    .main [data-testid="stCode"] code,
    [data-testid="stAppViewContainer"] > .main .stCode,
    [data-testid="stAppViewContainer"] > .main pre {
        background: #F1F5F9 !important;
        color: #1E293B !important;
        border: 1px solid #E2E8F0 !important;
    }
    [data-testid="stSidebar"] .stMarkdown h1,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h3 { color: #C7D2FE !important; }
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background: rgba(255,255,255,0.06) !important;
        border: 1px solid rgba(255,255,255,0.12) !important;
        border-radius: var(--radius-md) !important;
    }

    /* ── Tab bar ──────────────────────────────────────────────────── */
    [data-testid="stTabs"] > div:first-child {
        border-bottom: 2px solid var(--neutral-200) !important;
        gap: 0 !important;
    }
    [data-testid="stTab"] {
        padding: 0.75rem 1.25rem !important;
        border-radius: var(--radius-sm) var(--radius-sm) 0 0 !important;
        border: none !important;
        background: transparent !important;
        transition: background 0.18s ease, color 0.18s ease !important;
    }
    [data-testid="stTab"] p {
        font-size: 0.875rem !important;
        font-weight: 600 !important;
        color: var(--neutral-700) !important;
        letter-spacing: 0.01em;
    }
    [data-testid="stTab"]:hover {
        background: var(--primary-xlight) !important;
    }
    [data-testid="stTab"]:hover p { color: var(--primary) !important; }
    [data-testid="stTab"][aria-selected="true"] {
        background: white !important;
        border-bottom: 2px solid var(--primary) !important;
        margin-bottom: -2px !important;
    }
    [data-testid="stTab"][aria-selected="true"] p { color: var(--primary) !important; }

    /* ── Metric cards ─────────────────────────────────────────────── */
    div[data-testid="stMetric"] {
        background: white !important;
        border: 1px solid var(--neutral-200) !important;
        border-radius: var(--radius-md) !important;
        padding: 16px 20px !important;
        box-shadow: var(--shadow-sm) !important;
    }
    div[data-testid="stMetricValue"] {
        color: var(--primary) !important;
        font-size: 1.75rem !important;
        font-weight: 700 !important;
    }
    div[data-testid="stMetricLabel"] {
        color: var(--neutral-700) !important;
        font-size: 0.8rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    div[data-testid="stMetricDelta"] { font-size: 0.78rem !important; }

    /* ── Buttons ──────────────────────────────────────────────────── */
    button[data-testid="baseButton-primary"] {
        background: linear-gradient(135deg, var(--primary) 0%, #6366F1 100%) !important;
        color: white !important; border: none !important;
        border-radius: var(--radius-sm) !important;
        font-weight: 600 !important; font-size: 0.875rem !important;
        padding: 0.5rem 1.25rem !important;
        box-shadow: 0 1px 4px rgba(79,70,229,.35) !important;
        transition: opacity 0.15s, transform 0.1s !important;
    }
    button[data-testid="baseButton-primary"]:hover {
        opacity: 0.92 !important; transform: translateY(-1px) !important;
        box-shadow: 0 4px 12px rgba(79,70,229,.45) !important;
    }
    button[data-testid="baseButton-secondary"] {
        border: 1.5px solid var(--neutral-200) !important;
        border-radius: var(--radius-sm) !important;
        font-weight: 500 !important; font-size: 0.875rem !important;
        background: white !important; color: var(--neutral-700) !important;
        transition: border-color 0.15s, color 0.15s !important;
    }
    button[data-testid="baseButton-secondary"]:hover {
        border-color: var(--primary) !important; color: var(--primary) !important;
    }

    /* ── Containers / cards ───────────────────────────────────────── */
    [data-testid="stVerticalBlockBorderWrapper"] > div {
        border-radius: var(--radius-md) !important;
        border-color: var(--neutral-200) !important;
        box-shadow: var(--shadow-sm) !important;
        background: white !important;
    }

    /* ── Expanders ────────────────────────────────────────────────── */
    [data-testid="stExpander"] {
        border: 1px solid var(--neutral-200) !important;
        border-radius: var(--radius-md) !important;
        background: white !important;
        box-shadow: var(--shadow-sm) !important;
    }
    [data-testid="stExpander"] summary {
        font-weight: 600 !important;
        color: var(--neutral-900) !important;
    }

    /* ── Data tables ──────────────────────────────────────────────── */
    [data-testid="stDataFrame"] {
        border: 1px solid var(--neutral-200) !important;
        border-radius: var(--radius-md) !important;
        overflow: hidden !important;
    }

    /* ── Code / pre ───────────────────────────────────────────────── */
    .stCode, pre {
        background: var(--neutral-100) !important;
        color: var(--neutral-900) !important;
        border: 1px solid var(--neutral-200) !important;
        border-radius: var(--radius-sm) !important;
        font-size: 0.82rem !important;
    }
    /* Override any Streamlit syntax-highlight container that forces dark BG */
    [data-testid="stCode"] {
        background: var(--neutral-100) !important;
    }
    [data-testid="stCode"] pre,
    [data-testid="stCode"] code {
        background: var(--neutral-100) !important;
        color: #1E293B !important;
    }

    /* ── Alerts ───────────────────────────────────────────────────── */
    [data-testid="stAlert"] {
        border-radius: var(--radius-md) !important;
        border-left-width: 4px !important;
    }

    /* ── Form inputs ──────────────────────────────────────────────── */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stSelectbox"] > div > div {
        border-radius: var(--radius-sm) !important;
        border-color: var(--neutral-200) !important;
        font-size: 0.875rem !important;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus {
        border-color: var(--primary) !important;
        box-shadow: 0 0 0 3px rgba(79,70,229,.15) !important;
    }

    /* ── Section headers inside tabs ──────────────────────────────── */
    .ent-section-header {
        display: flex; align-items: center; gap: 10px;
        margin: 1.5rem 0 0.5rem; border-bottom: 2px solid var(--primary-xlight);
        padding-bottom: 8px;
    }
    .ent-section-header h3 {
        font-size: 1.05rem; font-weight: 700; color: var(--neutral-900); margin: 0;
    }

    /* ── Status badges ────────────────────────────────────────────── */
    .badge {
        display: inline-block; padding: 2px 9px;
        border-radius: 999px; font-size: 0.72rem; font-weight: 700;
        letter-spacing: 0.04em; text-transform: uppercase;
    }
    .badge-active  { background:#D1FAE5; color:#065F46; }
    .badge-draft   { background:#EEF2FF; color:#3730A3; }
    .badge-warning { background:#FEF3C7; color:#92400E; }
    .badge-danger  { background:#FFE4E6; color:#9F1239; }
    .badge-neutral { background:#F1F5F9; color:#475569; }

    /* ── Rule card ────────────────────────────────────────────────── */
    .rule-card {
        background: white; border: 1px solid var(--neutral-200);
        border-radius: var(--radius-md); padding: 14px 18px;
        margin-bottom: 10px; box-shadow: var(--shadow-sm);
        display: flex; align-items: flex-start; gap: 14px;
    }
    .rule-card .rule-icon {
        width: 36px; height: 36px; border-radius: var(--radius-sm);
        background: var(--primary-xlight); display: flex; align-items: center;
        justify-content: center; font-size: 1.1rem; flex-shrink: 0;
    }
    .rule-card .rule-body { flex: 1; min-width: 0; }
    .rule-card .rule-id { font-size: 0.75rem; font-weight: 600; color: var(--primary); font-family: monospace; }
    .rule-card .rule-name { font-size: 0.95rem; font-weight: 700; color: var(--neutral-900); margin: 2px 0 4px; }
    .rule-card .rule-desc { font-size: 0.82rem; color: #64748B; line-height: 1.5; }

    /* ── Step card (guide) ────────────────────────────────────────── */
    .step-card {
        background: white; border: 1px solid var(--neutral-200);
        border-radius: var(--radius-md); padding: 20px 22px 18px;
        box-shadow: var(--shadow-sm); position: relative;
    }
    .step-card .step-num {
        position: absolute; top: -14px; left: 20px;
        background: var(--primary); color: white; border-radius: 999px;
        width: 28px; height: 28px; display: flex; align-items: center;
        justify-content: center; font-size: 0.8rem; font-weight: 700;
    }
    .step-card .step-title { font-size: 1rem; font-weight: 700; color: var(--neutral-900); margin: 4px 0 8px; }
    .step-card .step-body  { font-size: 0.875rem; color: #475569; line-height: 1.65; }

    /* ── Workflow pill ────────────────────────────────────────────── */
    .workflow-row {
        display: flex; align-items: center; gap: 0; flex-wrap: wrap;
        margin: 1.5rem 0;
    }
    .workflow-pill {
        background: var(--primary-xlight); color: var(--primary);
        border: 1.5px solid var(--primary-light);
        border-radius: 999px; padding: 6px 18px;
        font-size: 0.82rem; font-weight: 700; white-space: nowrap;
    }
    .workflow-arrow { color: var(--primary-light); font-size: 1.2rem; padding: 0 6px; }

    /* ── Chat bubbles ─────────────────────────────────────────────── */
    .chat-bubble-user {
        background: var(--primary); color: white;
        border-radius: 18px 18px 4px 18px;
        padding: 10px 14px; font-size: 0.875rem; max-width: 80%;
        margin-left: auto; margin-bottom: 8px; box-shadow: var(--shadow-sm);
    }
    .chat-bubble-assistant {
        background: white; color: var(--neutral-900);
        border: 1px solid var(--neutral-200);
        border-radius: 4px 18px 18px 18px;
        padding: 10px 14px; font-size: 0.875rem; max-width: 88%;
        margin-right: auto; margin-bottom: 8px; box-shadow: var(--shadow-sm);
    }
    .chat-avatar {
        width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.75rem; font-weight: 700;
    }
    .chat-avatar-ai { background: var(--primary); color: white; }
    .chat-avatar-user { background: #E2E8F0; color: var(--neutral-700); }
    .chat-ts { font-size: 0.68rem; color: #94A3B8; margin-top: 3px; }

    /* ── Quick-action chips ───────────────────────────────────────── */
    .qa-chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 12px; }
    .qa-chip {
        background: var(--primary-xlight); color: var(--primary);
        border: 1.5px solid var(--primary-light); border-radius: 999px;
        padding: 5px 14px; font-size: 0.78rem; font-weight: 600;
        cursor: pointer; transition: background 0.15s;
        white-space: nowrap;
    }
    .qa-chip:hover { background: var(--primary); color: white; }

    /* ── Divider upgrade ──────────────────────────────────────────── */
    hr { border-color: var(--neutral-200) !important; margin: 1.5rem 0 !important; }

    /* ── Scrollbar ────────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--neutral-100); }
    ::-webkit-scrollbar-thumb { background: var(--neutral-200); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #CBD5E1; }

    /* ── Sidebar — white background, black text ───────────────────── */
    [data-testid="stSidebar"] { background: #ffffff !important; }
    [data-testid="stSidebar"] * { color: #111111 !important; }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] select,
    [data-testid="stSidebar"] textarea { background: #f8f9fa !important; border-color: #dee2e6 !important; }
    [data-testid="stSidebar"] button { color: #ffffff !important; }
    </style>
    """, unsafe_allow_html=True)

