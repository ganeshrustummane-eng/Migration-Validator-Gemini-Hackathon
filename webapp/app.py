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

import difflib
import os
import sys
from pathlib import Path

_WEBAPP_DIR = Path(__file__).parent
_ROOT_DIR   = _WEBAPP_DIR.parent
_SRC_DIR    = _ROOT_DIR / "src"
_PROJECT_DIR = _ROOT_DIR / "Project"

for p in (str(_SRC_DIR), str(_ROOT_DIR), str(_PROJECT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import streamlit as st

from dotenv import load_dotenv
load_dotenv(_ROOT_DIR / ".env")

from setup_wizard import (
    print_connection_registry,
    _discover_postgres_databases, _discover_postgres_schemas,
    _discover_mssql_databases, _discover_mssql_schemas,
    _discover_snowflake_databases, _discover_snowflake_schemas,
)
from validate_cli import (
    _normalize_db_type, _DB_TYPE_LABELS, _apply_database_registry,
    _override_source_env, _make_source_extractor, _get_all_exclusions,
    _save_global_user_exclusion, _remove_global_user_exclusion, _exclusions_path_for, STATIC_EXCLUDE_COLUMNS,
)
from validation_pipeline import ValidationPipeline
from sql_extractor import ExtractorFactory, SnowflakeExtractor
from rule_book import rule_book, RuleEntry, RuleValidationError
from ai_transformation.ai_rule_mapper import AVAILABLE_MODELS, MODEL_DESCRIPTIONS, AIRuleMappingError
from ai_transformation.rule_prompt_parser import RuleTypeParser, RuleParseError
from model_probe import get_working_models
from learning.feedback import FeedbackRecorder, MismatchFeedback
import mapping_store
from generated_queries.ai_sql_generator import AISQLQueryGenerator, AISQLGenerationError
from runner import list_configured_tables, run_validation
import results_store

sys.path.insert(0, str(_ROOT_DIR / "token_usage_analysis"))
from report_token_usage import _load_records as _load_token_records, _load_pricing, _cost_for

st.set_page_config(page_title="Migration Validator", layout="wide")

st.markdown("""
<style>
[data-testid="stTab"] {
    padding: 0.9rem 1.4rem !important;
}
[data-testid="stTab"] p {
    font-size: 1.08rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.01rem;
}
[data-testid="stTab"][aria-selected="true"] p {
    color: #6C5CE7 !important;
}
[data-testid="stTab"][aria-selected="true"] {
    border-bottom: 3px solid #6C5CE7 !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
[data-testid="stTab"] {
    font-weight: 600;
}
[data-testid="stTab"][aria-selected="true"] {
    background: linear-gradient(90deg, #6C5CE7 0%, #A29BFE 100%);
    color: white !important;
    border-radius: 8px 8px 0 0;
}
div[data-testid="stMetric"] {
    background: linear-gradient(135deg, #F0F1FA 0%, #E4E7FB 100%);
    border: 1px solid #D6D8F5;
    border-radius: 10px;
    padding: 12px 16px;
}
div[data-testid="stMetricValue"] {
    color: #6C5CE7;
}
</style>
""", unsafe_allow_html=True)

SOURCE_TYPES = ("postgresql", "mssql", "athena")
_TYPE_MANUAL = "✏️  Type manually…"


# ---------------------------------------------------------------------------
# Flash-message / toast helper
# ---------------------------------------------------------------------------
# st.success(...) immediately followed by st.rerun() never actually shows —
# rerun tears down the current run before the message can render. The fix is
# to stash the message in session_state, rerun, and show it as a toast at the
# very top of the NEXT run (toasts persist briefly and are visible regardless
# of which tab is active, so it's obvious the save actually happened).

def flash(message: str, icon: str = "✅"):
    st.session_state["_flash"] = (message, icon)


def _show_pending_flash():
    pending = st.session_state.pop("_flash", None)
    if pending:
        message, icon = pending
        st.toast(message, icon=icon)


_show_pending_flash()


# ---------------------------------------------------------------------------
# Paginated CSV/DataFrame viewer — used anywhere a validation result set could
# be large (summary CSVs, row-level mismatch diffs) instead of dumping the
# whole thing into one st.dataframe.
# ---------------------------------------------------------------------------

def _style_status(df):
    """Colors a 'status' column (PASS/FAIL) green/red if present — purely cosmetic."""
    if "status" not in df.columns:
        return df
    def _color(v):
        if v == "PASS":
            return "color: #1a7f37; font-weight: 600"
        if v == "FAIL":
            return "color: #c0392b; font-weight: 600"
        return ""
    return df.style.map(_color, subset=["status"])


def render_paginated_df(df, key_prefix: str, page_size_options=(10, 25, 50, 100), style_status: bool = True):
    """Renders a DataFrame with page-size + page-number controls instead of
    one long scrollable table. Returns nothing — renders directly."""
    n_rows = len(df)
    if n_rows == 0:
        st.caption("No rows.")
        return

    pc1, pc2 = st.columns([1, 3])
    with pc1:
        page_size = st.selectbox(
            "Rows per page", page_size_options,
            index=min(1, len(page_size_options) - 1), key=f"{key_prefix}_page_size",
        )
    n_pages = max((n_rows + page_size - 1) // page_size, 1)
    with pc2:
        page = st.number_input(
            f"Page (1–{n_pages})", min_value=1, max_value=n_pages, value=1, step=1,
            key=f"{key_prefix}_page",
        )
    start, end = (page - 1) * page_size, min(page * page_size, n_rows)
    st.caption(f"Showing rows {start + 1}–{end} of {n_rows}")

    page_df = df.iloc[start:end]
    st.dataframe(_style_status(page_df) if style_status else page_df, width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# Cached discovery calls — one live query per (host, creds, ...) combo, not
# re-run on every widget interaction elsewhere on the page.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="Discovering databases…")
def cached_source_databases(db_type, host, port, username, password, auth):
    if db_type == "postgresql":
        return _discover_postgres_databases(host, port, username, password)
    if db_type == "mssql":
        return _discover_mssql_databases(host, port, username, password, auth)
    return []  # Athena: database == fixed Glue database, no server-side listing


@st.cache_data(ttl=300, show_spinner="Discovering schemas…")
def cached_source_schemas(db_type, host, port, database, username, password, auth):
    if db_type == "postgresql":
        return _discover_postgres_schemas(host, port, database, username, password)
    if db_type == "mssql":
        return _discover_mssql_schemas(host, port, database, username, password, auth)
    return []  # Athena: schema == database, no separate listing


@st.cache_data(ttl=300, show_spinner="Loading tables…")
def cached_source_tables(db_type, host, port, database, username, password, auth, s3_output, schema):
    extractor = ExtractorFactory.create(
        db_type, host=host, port=port, database=database,
        username=username, password=password, auth=auth, s3_output=s3_output,
    )
    return extractor.list_tables(schema)


@st.cache_data(ttl=300, show_spinner="Loading columns…")
def cached_source_columns(db_type, host, port, database, username, password, auth, s3_output, schema, table):
    extractor = ExtractorFactory.create(
        db_type, host=host, port=port, database=database,
        username=username, password=password, auth=auth, s3_output=s3_output,
    )
    return [c.column_name for c in extractor.extract_columns(schema, table)]


@st.cache_data(ttl=300, show_spinner="Discovering Snowflake databases…")
def cached_sf_databases(account, username, password, warehouse, role):
    return _discover_snowflake_databases(account, username, password, warehouse, role)


@st.cache_data(ttl=300, show_spinner="Discovering Snowflake schemas…")
def cached_sf_schemas(account, database, username, password, warehouse, role):
    rows = _discover_snowflake_schemas(account, database, username, password, warehouse, role)
    return [r[0] for r in rows]


@st.cache_data(ttl=300, show_spinner="Loading Snowflake tables…")
def cached_sf_tables(database, schema):
    return SnowflakeExtractor(database=database).list_tables(schema)


@st.cache_data(ttl=300, show_spinner="Loading Snowflake columns…")
def cached_sf_columns(database, schema, table):
    return [c.column_name for c in SnowflakeExtractor(database=database).extract_columns(schema, table)]


@st.cache_data(ttl=300, show_spinner=False)
def cached_sf_column_types(database, schema, table):
    """{column_name: data_type} for the Snowflake target — used to fill in the
    target type when persisting a human-corrected column mapping as a learned
    example (see render_mapping_review save button)."""
    return {c.column_name: c.data_type for c in SnowflakeExtractor(database=database).extract_columns(schema, table)}


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def load_registry() -> list:
    """Configured source connections from .env, same as the CLI's picker."""
    try:
        registry = print_connection_registry(_ROOT_DIR / ".env")
    except Exception as exc:
        st.error(f"Could not read .env connections: {exc}")
        return []
    out = []
    for rec in registry:
        rec = dict(rec)
        rec["db_type"] = _normalize_db_type(rec["db_type"])
        rec = _apply_database_registry(rec)
        out.append(rec)
    return out


def connection_label(rec: dict) -> str:
    label = _DB_TYPE_LABELS.get(rec["db_type"], rec["db_type"])
    return f"SRC_{rec['index']}  ·  {label}  ·  {rec['host']}/{rec['database']}.{rec['schema']}"


def select_connection(registry: list, key: str):
    if not registry:
        st.warning("No source connections found in .env. Run the setup wizard first: `python src/validate_cli.py setup`")
        return None
    options = {connection_label(r): r for r in registry}
    chosen = st.selectbox("Source connection", list(options.keys()), key=key)
    return options[chosen]


def select_or_type(label: str, options: list, default: str, key: str, format_func=None) -> str:
    """A dropdown of live-discovered values, with a fallback to type a value
    manually (discovery can fail — driver missing, permissions, brand-new
    table not created yet, etc.) so the picker never becomes a dead end."""
    opts = list(dict.fromkeys(options))  # de-dupe, preserve order
    if default and default not in opts:
        opts = [default] + opts
    display_opts = opts + [_TYPE_MANUAL]
    default_idx = display_opts.index(default) if default in display_opts else 0
    kwargs = {"format_func": format_func} if format_func else {}
    choice = st.selectbox(label, display_opts, index=default_idx, key=f"{key}_sel", **kwargs)
    if choice == _TYPE_MANUAL:
        return st.text_input(f"{label} (type manually)", value=default or "", key=f"{key}_txt")
    return choice


@st.cache_data(ttl=300, show_spinner="Checking which AI models are reachable…")
def available_models_for_ui() -> list:
    """Dynamic model list — probes DIAL for reachability when a key is set,
    otherwise returns the full curated registry so the picker still works."""
    api_key = os.getenv("DIAL_API_KEY", "")
    if not api_key:
        return list(AVAILABLE_MODELS)
    try:
        working = get_working_models(
            AVAILABLE_MODELS, api_key,
            os.getenv("DIAL_API_BASE", ""), os.getenv("DIAL_API_VERSION", ""),
        )
        return working or list(AVAILABLE_MODELS)
    except Exception:
        return list(AVAILABLE_MODELS)


def _model_label(model_id: str) -> str:
    if model_id == _TYPE_MANUAL:
        return model_id
    info = MODEL_DESCRIPTIONS.get(model_id)
    if not info:
        return model_id
    vendor, display_name, description = info
    return f"{display_name}  ·  {vendor} — {description}"


def source_password(rec: dict) -> str:
    return os.getenv(f"{rec['prefix']}PASSWORD", "")


_LAYERS = ("bronze", "silver", "gold")


def pick_layer(key: str) -> tuple:
    """Medallion layer picker — mirrors the CLI's interactive '1) bronze
    2) silver 3) gold' prompt (validate_cli.py), which only ever ran in the
    terminal flow. Returns (layer_name, output_dir) where output_dir is where
    the generated YAML/SQL config files are written."""
    layer = st.selectbox(
        "Medallion layer — where to write the generated config",
        _LAYERS, index=0, key=key,
        help="Controls only the output folder for generated YAML/SQL configs (Project/config/<layer>/), "
             "not which Snowflake database/schema is queried — that's chosen above.",
    )
    return layer, _ROOT_DIR / "Project" / "config" / layer


def snowflake_creds() -> dict:
    return {
        "account":   os.getenv("SNOWFLAKE_ACCOUNT", ""),
        "username":  os.getenv("SNOWFLAKE_USERNAME", ""),
        "password":  os.getenv("SNOWFLAKE_PASSWORD", ""),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
        "role":      os.getenv("SNOWFLAKE_ROLE", ""),
        "database":  os.getenv("SNOWFLAKE_DATABASE", ""),
        "schema":    os.getenv("SNOWFLAKE_SCHEMA", ""),
    }


def pick_source_location(rec: dict, key_prefix: str):
    """Cascading database -> schema -> table picker for a source connection,
    all live-discovered using the credentials already in .env. Returns
    (database, schema, table_options, chosen_table_or_None)."""
    db_type  = rec["db_type"]
    password = source_password(rec)

    if db_type == "athena":
        st.caption(f"Athena database/schema is fixed to the Glue database configured in .env: **{rec['database']}**")
        database = rec["database"]
        schema   = rec["schema"]
    else:
        databases = cached_source_databases(db_type, rec["host"], int(rec.get("port") or 0), rec["username"], password, rec.get("auth", ""))
        c1, c2 = st.columns(2)
        with c1:
            database = select_or_type("Source database", databases, rec["database"], f"{key_prefix}_db")
        schemas = cached_source_schemas(db_type, rec["host"], int(rec.get("port") or 0), database, rec["username"], password, rec.get("auth", ""))
        with c2:
            schema = select_or_type("Source schema", schemas, rec["schema"], f"{key_prefix}_schema")

    try:
        tables = cached_source_tables(
            db_type, rec["host"], int(rec.get("port") or 0), database,
            rec["username"], password, rec.get("auth", ""), rec.get("s3_output", ""), schema,
        )
    except Exception as exc:
        st.error(f"Could not list tables: {exc}")
        tables = []

    return database, schema, tables


def pick_snowflake_target(default_table: str, key_prefix: str, include_table: bool = True):
    """Cascading database -> schema -> table picker for the Snowflake target,
    live-discovered the same way as the source picker.

    `include_table` controls whether a single Snowflake table dropdown is
    shown — used for the Single YAML flow, which maps one table directly.
    The Batch YAML flow maps each source table to its own target in the
    per-table mapping grid instead, so it skips this (set include_table=False)
    to avoid a confusing, unused single-table dropdown."""
    creds = snowflake_creds()
    databases = cached_sf_databases(creds["account"], creds["username"], creds["password"], creds["warehouse"], creds["role"])
    c1, c2 = st.columns(2)
    with c1:
        sf_database = select_or_type("Snowflake database", databases, creds["database"], f"{key_prefix}_sfdb")
    schemas = cached_sf_schemas(creds["account"], sf_database, creds["username"], creds["password"], creds["warehouse"], creds["role"])
    with c2:
        sf_schema = select_or_type("Snowflake schema", schemas, creds["schema"], f"{key_prefix}_sfschema")

    if not include_table:
        return sf_database, sf_schema, None

    try:
        sf_tables = cached_sf_tables(sf_database, sf_schema)
    except Exception as exc:
        st.error(f"Could not list Snowflake tables: {exc}")
        sf_tables = []

    sf_table = select_or_type("Snowflake table", sf_tables, default_table, f"{key_prefix}_sftable")
    return sf_database, sf_schema, sf_table


def render_mapping_review(
    pipeline,
    pg_schema: str, pg_table: str, sf_schema: str, sf_table: str,
    sf_database: str, pg_database: str,
    exclude_columns: list, key_prefix: str,
) -> dict:
    """
    Preview the source→target COLUMN mapping the AI/fuzzy matcher would use,
    and let a human correct it before anything is generated.

    The AI mapper can flag a rename as a mismatch even when it's correct
    (e.g. source 'customer' -> target 'user'), and can just as easily auto-
    accept a wrong guess. This grid shows exactly how every column was
    matched (method + confidence) so a human can fix it either way — for a
    single table or, called once per table, for a batch run.

    Returns:
        {source_column: corrected_target_column} for every row the human
        edited away from the AI/fuzzy suggestion. Empty dict if nothing was
        previewed or nothing was changed — callers pass this straight in as
        explicit_mappings, so an empty dict behaves exactly like "no override".
    """
    rows_key = f"{key_prefix}_mapping_rows"
    sig_key = f"{key_prefix}_mapping_sig"
    sig = (pg_schema, pg_table, sf_schema, sf_table, sf_database, pg_database, tuple(sorted(exclude_columns or [])))

    if st.button("🔍 Preview column mapping", key=f"{key_prefix}_preview_btn"):
        with st.spinner(f"Matching columns for {pg_table} → {sf_table} ..."):
            try:
                st.session_state[rows_key] = pipeline.preview_mapping(
                    pg_schema=pg_schema, pg_table=pg_table,
                    sf_schema=sf_schema, sf_table=sf_table,
                    sf_database=sf_database, pg_database=pg_database,
                    exclude_columns=exclude_columns,
                )
                st.session_state[sig_key] = sig
            except Exception as exc:
                st.error(f"Column mapping preview failed: {exc}")
                st.session_state.pop(rows_key, None)

    rows = st.session_state.get(rows_key)
    if rows is None or st.session_state.get(sig_key) != sig:
        st.caption(
            "Not previewed yet — click above to see how each source column was matched "
            "to a target column, and correct any that are wrong."
        )
        return {}

    try:
        live_tgt_cols = cached_sf_columns(sf_database, sf_schema, sf_table)
    except Exception:
        live_tgt_cols = []

    import pandas as pd
    df = pd.DataFrame([{
        "Source Column": r["source_column"],
        "Source Type": r["source_type"],
        "AI/Fuzzy Target": r["target_column"],
        "Corrected Target": r["target_column"],
        "Matched By": "skip" if r["skip_validation"] else r["match_method"],
        "Confidence": r["confidence"],
    } for r in rows])

    target_options = sorted(set(live_tgt_cols) | {r["target_column"] for r in rows if r["target_column"]})

    edited = st.data_editor(
        df,
        column_config={
            "Source Column": st.column_config.TextColumn(disabled=True),
            "Source Type": st.column_config.TextColumn(disabled=True),
            "AI/Fuzzy Target": st.column_config.TextColumn(
                disabled=True, help="What the AI/fuzzy matcher picked — kept for comparison.",
            ),
            "Corrected Target": st.column_config.SelectboxColumn(
                options=target_options + [""],
                help="Override the mapping if it's wrong in either direction — the mapper "
                     "may have missed a valid rename (e.g. 'customer' -> 'user') or accepted a wrong guess.",
            ),
            "Matched By": st.column_config.TextColumn(disabled=True),
            "Confidence": st.column_config.NumberColumn(disabled=True, format="%.2f"),
        },
        hide_index=True,
        width='stretch',
        key=f"{key_prefix}_mapping_editor",
    )

    unmatched = [r["source_column"] for r in rows if not r["skip_validation"] and not r["target_column"]]
    low_conf = [r["source_column"] for r in rows if not r["skip_validation"] and r["target_column"] and r["confidence"] < 0.75]
    if unmatched:
        st.warning(f"No target match for: {', '.join(unmatched)} — pick one above or it will be skipped from validation.")
    if low_conf:
        st.info(f"Matched below high confidence: {', '.join(low_conf)} — review these; a flagged mismatch can still be correct.")

    overrides = {
        row["Source Column"]: row["Corrected Target"]
        for _, row in edited.iterrows()
        if row["Corrected Target"] and row["Corrected Target"] != row["AI/Fuzzy Target"]
    }
    if overrides:
        st.success(
            f"{len(overrides)} correction(s) will be forced onto the mapping: "
            + ", ".join(f"{k} → {v}" for k, v in overrides.items())
        )
        if st.button("💾 Remember these corrections for future runs", key=f"{key_prefix}_save_corrections"):
            try:
                tgt_types = cached_sf_column_types(sf_database, sf_schema, sf_table)
            except Exception:
                tgt_types = {}
            src_type_by_col = {r["source_column"]: r["source_type"] for r in rows}

            def _rule_id_for(src_type: str, tgt_type: str) -> str:
                from rules import get_rule_for_type
                try:
                    return get_rule_for_type(src_type, tgt_type).rule_name
                except Exception:
                    return "text"

            recorder = FeedbackRecorder()
            saved = recorder.record_batch([
                MismatchFeedback(
                    source_column=src_col,
                    target_column=corrected_tgt,
                    source_type=src_type_by_col.get(src_col, ""),
                    target_type=tgt_types.get(corrected_tgt, ""),
                    correct_rule=_rule_id_for(src_type_by_col.get(src_col, ""), tgt_types.get(corrected_tgt, "")),
                    reason="Corrected via webapp column mapping review",
                    table_name=pg_table,
                    was_ai_decision=True,
                )
                for src_col, corrected_tgt in overrides.items()
            ])
            st.success(f"Saved {saved} correction(s) to rule_book_learned.json — future runs will recognize them.")
    return overrides


def render_custom_sql_section(
    mapping_rows: list,
    src_db_type: str,
    src_table_fqn: str,
    sf_table_fqn: str,
    output_dir,
    default_filename: str,
    key_prefix: str,
) -> None:
    """
    Optional 'ask AI for extra SQL' panel, scoped to one table's already-
    reviewed column mapping (name, type, target counterpart).

    Lives right after the mapping-review grid (both in the single-table flow
    and per-table inside batch) rather than as a standalone screen, because
    it needs a concrete, human-reviewed column list to give the AI accurate
    context — that data only exists once a mapping preview has been run for
    this specific table. A standalone version would just be this same form
    asking the user to type out columns/types by hand instead of reusing
    what's already been reviewed on screen.
    """
    with st.expander("🧪 Generate custom SQL from a prompt (optional)", expanded=False):
        st.caption(
            "Ask for any SQL beyond the standard source/target validation query — "
            "e.g. a dedup check, or a query with an extra business condition. "
            "Scoped to whichever columns you keep checked below."
        )

        import pandas as pd
        from rules import get_rule_for_type

        def _rule_name(r: dict) -> str:
            try:
                return get_rule_for_type(r["source_type"], r["target_type"] or r["source_type"]).rule_name
            except Exception:
                return "text"

        usable_rows = [r for r in mapping_rows if not r["skip_validation"]]
        cols_df = pd.DataFrame([{
            "Use": True,
            "Source column": r["source_column"],
            "Source type": r["source_type"],
            "Target column": r["target_column"] or "(unmatched)",
            "Target type": r["target_type"],
        } for r in usable_rows])

        picked = st.data_editor(
            cols_df,
            column_config={
                "Use": st.column_config.CheckboxColumn(help="Include this column as context for the AI."),
                "Source column": st.column_config.TextColumn(disabled=True),
                "Source type": st.column_config.TextColumn(disabled=True),
                "Target column": st.column_config.TextColumn(disabled=True),
                "Target type": st.column_config.TextColumn(disabled=True),
            },
            hide_index=True, width='stretch', key=f"{key_prefix}_custom_cols_editor",
        )

        source_label = f"Source only ({src_db_type})"
        target_choice = st.radio(
            "Generate SQL for:",
            options=[source_label, "Snowflake only", "Both sides"],
            horizontal=True, key=f"{key_prefix}_custom_target_choice",
            help="Column names/types often differ between source and target — pick 'Both sides' "
                 "to get two separate, correctly-named queries rather than one query with mismatched names.",
        )

        custom_prompt = st.text_area(
            "What should this query do?",
            placeholder="e.g. Find duplicate employee names, ignoring case and whitespace",
            key=f"{key_prefix}_custom_prompt",
        )

        custom_model = select_or_type(
            "AI model for this query", available_models_for_ui(),
            os.getenv("DIAL_MODEL", "gpt-4o"),
            f"{key_prefix}_custom_model", format_func=_model_label,
        )

        sql_key = f"{key_prefix}_custom_sql"

        if st.button("✨ Generate SQL", key=f"{key_prefix}_custom_generate"):
            selected_names = {row["Source column"] for _, row in picked.iterrows() if row["Use"]}
            selected = [r for r in usable_rows if r["source_column"] in selected_names]
            if not custom_prompt.strip():
                st.error("Describe what the query should do first.")
            elif not selected:
                st.error("Select at least one column.")
            else:
                want_source = target_choice != "Snowflake only"
                want_target = target_choice != source_label
                results = {}
                with st.spinner("Generating SQL..."):
                    gen = AISQLQueryGenerator(model=custom_model)
                    try:
                        if want_source:
                            src_cols = [
                                {"column": r["source_column"], "type": r["source_type"], "rule": _rule_name(r)}
                                for r in selected
                            ]
                            results["source"] = gen.generate_custom_query(
                                custom_prompt, src_table_fqn, src_cols, src_db_type,
                            ).query
                        if want_target:
                            tgt_cols = [
                                {"column": r["target_column"], "type": r["target_type"], "rule": _rule_name(r)}
                                for r in selected if r["target_column"]
                            ]
                            if not tgt_cols:
                                raise AISQLGenerationError(
                                    "None of the selected columns have a mapped target column — "
                                    "pick a different set, or review the mapping above first."
                                )
                            results["target"] = gen.generate_custom_query(
                                custom_prompt, sf_table_fqn, tgt_cols, "snowflake",
                            ).query
                        st.session_state[sql_key] = results
                    except AISQLGenerationError as exc:
                        st.error(f"Generation failed: {exc}")
                        st.session_state.pop(sql_key, None)

        results = st.session_state.get(sql_key)
        if results:
            if "source" in results:
                st.markdown(f"**Source SQL ({src_db_type}):**")
                st.code(results["source"], language="sql")
            if "target" in results:
                st.markdown("**Snowflake SQL:**")
                st.code(results["target"], language="sql")
            dl1, dl2 = st.columns(2)
            if dl1.button("🔄 Regenerate", key=f"{key_prefix}_custom_regen"):
                st.session_state.pop(sql_key, None)
                st.rerun()
            if dl2.button("💾 Save to file(s)", key=f"{key_prefix}_custom_save"):
                save_dir = Path(output_dir) if output_dir else Path("output")
                save_dir.mkdir(parents=True, exist_ok=True)
                saved = []
                if "source" in results:
                    fname = save_dir / f"{default_filename}_source_custom_query.sql"
                    fname.write_text(results["source"], encoding="utf-8")
                    saved.append(str(fname))
                if "target" in results:
                    fname = save_dir / f"{default_filename}_snowflake_custom_query.sql"
                    fname.write_text(results["target"], encoding="utf-8")
                    saved.append(str(fname))
                flash(f"Saved: {', '.join(saved)}", icon="💾")


# ---------------------------------------------------------------------------
# Sidebar — environment status, always visible regardless of active tab
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Migration Validator")

    _dial_key = os.getenv("DIAL_API_KEY", "")
    _claude_key = os.getenv("CLAUDE_API_KEY", "")
    if _dial_key:
        st.success(f"AI backend: DIAL ({os.getenv('DIAL_MODEL', 'gpt-4o')})", icon="🤖")
    elif _claude_key:
        st.success(f"AI backend: Claude ({os.getenv('CLAUDE_MODEL', 'claude-3-5-sonnet-20241022')})", icon="🤖")
    else:
        # Column-mapping/SQL-generation AI (DIAL/Claude) is separate from the
        # Gemini Chat widget's own backend — only warn "not configured" if
        # neither is set, so a Vertex-AI-only setup doesn't look broken here.
        from gemini_connector.gemini_agent import is_gemini_configured as _is_gemini_ready
        if _is_gemini_ready():
            st.info("AI mapping backend (DIAL/Claude) not set — Gemini Chat is configured separately.", icon="ℹ️")
        else:
            st.error("No AI backend configured (DIAL_API_KEY / CLAUDE_API_KEY missing)", icon="⚠️")

    _sf_account = os.getenv("SNOWFLAKE_ACCOUNT", "")
    if _sf_account:
        st.info(f"Snowflake: {_sf_account}", icon="❄️")
    else:
        st.warning("Snowflake account not configured", icon="⚠️")

    st.divider()
    st.caption("Live dropdowns (databases/schemas/tables) are cached for 5 minutes.")
    if st.button("🔄 Refresh discovery cache", width='stretch'):
        st.cache_data.clear()
        flash("Discovery cache cleared — dropdowns will re-query live data.", icon="🔄")
        st.rerun()

    st.divider()
    with st.expander("🔌 Connections", expanded=False):
        _sidebar_registry = load_registry()
        if not _sidebar_registry:
            st.caption("No SRC_N_* connections found. Configure `.env` or run `python src/validate_cli.py setup`.")
        else:
            st.dataframe(
                [
                    {
                        "Slot": f"SRC_{r['index']}",
                        "Type": _DB_TYPE_LABELS.get(r["db_type"], r["db_type"]),
                        "Host": r["host"],
                        "Database": r["database"],
                        "Schema": r["schema"],
                    }
                    for r in _sidebar_registry
                ],
                width='stretch', hide_index=True,
            )

        _sf = snowflake_creds()
        st.caption(f"Snowflake target: **{_sf['database'] or 'not set'}**.{_sf['schema'] or 'not set'}")

        if st.button("🔎 Test all connections", width='stretch'):
            results = []
            for rec in _sidebar_registry:
                try:
                    extractor = _make_source_extractor(rec)
                    extractor.list_tables(rec["schema"])
                    results.append((connection_label(rec), True, ""))
                except Exception as exc:
                    results.append((connection_label(rec), False, str(exc)))
            try:
                sf_ext = SnowflakeExtractor(database=_sf["database"])
                sf_ext.list_tables(_sf["schema"])
                results.append(("Snowflake (target)", True, ""))
            except Exception as exc:
                results.append(("Snowflake (target)", False, str(exc)))

            for label, ok, err in results:
                if ok:
                    st.success(f"✓ {label}")
                else:
                    st.error(f"✗ {label} — {err}")

    st.divider()
    st.caption("Token usage & cost for this session:")
    st.code("python token_usage_analysis/report_token_usage.py", language="bash")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("Migration Validator")
st.caption("PostgreSQL / MSSQL / Athena → Snowflake — pick everything from live dropdowns, powered by the credentials already in .env.")

tab_single, tab_batch, tab_execute, tab_history, tab_rules, tab_excl, tab_review, tab_guide = st.tabs(
    ["▶️ Generate Single YAML", "📋 Generate Batch YAML", "🚀 Run Validation", "📈 History & Trends",
     "📖 Rule Book", "🚫 Exclusions", "✅ Review & Approve", "📘 Guide"]
)
# 📊 Usage & Cost lives in the sidebar (see below); 🤖 Gemini Chat is a
# floating widget (see end of file) — Review & Approve is back as a tab.

# =============================================================================
# TAB: Generate — Single YAML
# =============================================================================
with tab_single:
    st.subheader("Single YAML — pick source and target from live dropdowns")
    registry = load_registry()
    rec = select_connection(registry, key="single_conn")

    if rec:
        _override_source_env(rec)
        src_db_type = rec["db_type"]

        with st.container(border=True):
            st.markdown("**① Source**")
            database, schema, table_options = pick_source_location(rec, "single")
            source_table = select_or_type("Source table", table_options, "", "single_table")

        with st.container(border=True):
            st.markdown("**② Target (Snowflake)**")
            suggested_sf_table = source_table.upper() if source_table else ""
            sf_database, sf_schema, sf_table = pick_snowflake_target(suggested_sf_table, "single")

        # ── Exclusions: system auto-exclusions (always applied, not editable
        # here) shown separately from optional user-picked exclusions, with a
        # widget key scoped to the selected table so Streamlit doesn't reuse
        # stale selections from a previously chosen table ──
        auto_excluded = set(_get_all_exclusions(src_db_type))
        col_names = []
        if source_table:
            try:
                col_names = cached_source_columns(
                    src_db_type, rec["host"], int(rec.get("port") or 0), database,
                    rec["username"], source_password(rec), rec.get("auth", ""),
                    rec.get("s3_output", ""), schema, source_table,
                )
            except Exception as exc:
                st.warning(f"Could not load columns for exclusion picker: {exc}")

        static_set = {c.lower() for c in STATIC_EXCLUDE_COLUMNS}
        static_present = sorted(c for c in col_names if c.lower() in static_set)
        user_global_present = sorted(c for c in col_names if c.lower() in auto_excluded and c.lower() not in static_set)
        auto_excluded_present = static_present + user_global_present
        pickable_cols = [c for c in col_names if c.lower() not in auto_excluded]

        st.markdown("**③ Columns to exclude**")
        st.caption(
            f"🔒 Built-in auto-excluded (system default for {_DB_TYPE_LABELS.get(src_db_type, src_db_type)}, "
            f"always applied): {', '.join(static_present) or '(none present in this table)'}"
        )
        st.caption(
            f"🌐 User-defined global exclusions (added via the Exclusions tab, always applied): "
            f"{', '.join(user_global_present) or '(none present in this table)'}"
        )
        user_excluded_cols = st.multiselect(
            "Additional columns to exclude from validation (optional, just for this run)",
            options=pickable_cols,
            default=[],
            key=f"single_excl_{source_table}",
            label_visibility="collapsed",
        )
        excluded_cols = auto_excluded_present + user_excluded_cols

        model = select_or_type(
            "AI model", available_models_for_ui(), os.getenv("DIAL_MODEL", "gpt-4o"),
            "single_model", format_func=_model_label,
        )
        layer, output_dir = pick_layer("single_layer")

        column_overrides = {}
        if source_table and sf_table:
            st.markdown("**④ Column mapping — review before generating**")
            extractor = ExtractorFactory.create(
                src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                database=database, username=rec["username"], password=source_password(rec),
                auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
            )
            preview_pipeline = ValidationPipeline(model=model, source_extractor=extractor)
            column_overrides = render_mapping_review(
                preview_pipeline, schema, source_table, sf_schema, sf_table,
                sf_database, database, excluded_cols, key_prefix="single",
            )

            mapping_rows = st.session_state.get("single_mapping_rows")
            if mapping_rows:
                render_custom_sql_section(
                    mapping_rows, src_db_type,
                    src_table_fqn=f"{schema}.{source_table}",
                    sf_table_fqn=f"{sf_database}.{sf_schema}.{sf_table}" if sf_database else f"{sf_schema}.{sf_table}",
                    output_dir=output_dir,
                    default_filename=source_table,
                    key_prefix="single",
                )

        if st.button("▶️ Generate SQL + YAML", type="primary", key="single_generate"):
            if not source_table or not sf_table:
                st.error("Source table and Snowflake table are required.")
            else:
                with st.spinner(f"Running pipeline for {source_table} → {sf_table} ..."):
                    try:
                        extractor = ExtractorFactory.create(
                            src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                            database=database, username=rec["username"], password=source_password(rec),
                            auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
                        )
                        pipeline = ValidationPipeline(model=model, source_extractor=extractor)
                        result, _plan = pipeline.run_with_plan(
                            pg_schema=schema,
                            pg_table=source_table,
                            sf_schema=sf_schema,
                            sf_table=sf_table,
                            sf_database=sf_database,
                            pg_database=database,
                            explicit_mappings=column_overrides or None,
                            exclude_columns=excluded_cols or None,
                            source_db_type=src_db_type,
                            output_dir=output_dir,
                        )
                        st.success(f"Generated for {result.table_name}")
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Active columns", result.active_columns)
                        m2.metric("Skipped columns", len(result.skipped_columns))
                        m3.metric("Generated by", f"{result.generated_by} ({result.model_used})")
                        if result.coverage_headline:
                            st.info(result.coverage_headline)
                        st.write("**Output files:**")
                        st.code(str(result.yaml_path))
                        if result.count_yaml_path:
                            st.code(str(result.count_yaml_path))
                    except Exception as exc:
                        st.error(f"Generation failed: {exc}")

# =============================================================================
# TAB: Generate — Batch YAML
# =============================================================================
with tab_batch:
    st.subheader("Batch YAML — pick source/target once, map every table explicitly")
    registry = load_registry()
    rec = select_connection(registry, key="batch_conn")

    if rec:
        _override_source_env(rec)
        src_db_type = rec["db_type"]

        with st.container(border=True):
            st.markdown("**① Source**")
            database, schema, table_options = pick_source_location(rec, "batch")
            source_tables = st.multiselect(
                "Tables to validate — select N source tables",
                options=table_options,
                key="batch_tables_select",
            )
            if not table_options:
                st.caption("Table list unavailable — type names manually below (comma-separated).")
                manual_raw = st.text_input("Table names (comma-separated)", key="batch_tables_manual")
                source_tables = [t.strip() for t in manual_raw.split(",") if t.strip()]

        with st.container(border=True):
            st.markdown("**② Target (Snowflake)**")
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
            st.markdown("**③ Map each source table to its Snowflake target — review before generating**")

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

        st.markdown("**④ Columns to exclude (per table)**")
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
            st.markdown("**⑤ Column mapping — review per table before generating**")
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
        if st.button("▶️ Generate All", type="primary", key="batch_generate", disabled=generate_disabled):
            extractor = ExtractorFactory.create(
                src_db_type, host=rec["host"], port=int(rec.get("port") or 0),
                database=database, username=rec["username"], password=source_password(rec),
                auth=rec.get("auth", ""), s3_output=rec.get("s3_output", ""),
            )
            progress = st.progress(0.0, text="Starting...")
            results = []
            pairs = list(target_map.items())
            for i, (src_table, tgt_table) in enumerate(pairs, 1):
                progress.progress(i / len(pairs), text=f"{src_table} → {tgt_table}  ({i}/{len(pairs)})")
                try:
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

# =============================================================================
# TAB: Run Validation — execute the generated YAMLs (Project/main.py) and
# show pass/fail results, without ever displaying raw row values.
# =============================================================================
with tab_execute:
    st.subheader("Run validation — pick the YAML file(s) to execute")
    st.caption(
        "Runs `Project/main.py` against the YAML configs generated in the tabs above, "
        "then reads back the run's summary — counts and pass/fail status only."
    )

    top1, top2 = st.columns(2)
    with top1:
        layer = st.selectbox("Medallion layer", _LAYERS, index=0, key="exec_layer")
    with top2:
        environment = st.selectbox("Environment", ["local", "dev", "uat", "prod"], key="exec_env")

    tables_by_type = list_configured_tables(layer)
    count_tables = tables_by_type["count_validation"]
    data_tables = tables_by_type["data_validation"]

    if not count_tables and not data_tables:
        st.warning(
            f"No YAML configs found for layer '{layer}' yet — generate one first "
            f"in the **Generate Single/Batch YAML** tabs."
        )
    else:
        import pandas as pd

        file_rows = [
            {"Run": True, "Validation type": "count_validation",
             "File": f"config/{layer}/count_validation/{layer}.yaml", "Table": t}
            for t in count_tables
        ] + [
            {"Run": True, "Validation type": "data_validation",
             "File": f"config/{layer}/data_validation/{t}.yaml", "Table": t}
            for t in data_tables
        ]
        select_all = st.checkbox("Select all files", value=True, key="exec_select_all")
        for row in file_rows:
            row["Run"] = select_all
        files_df = pd.DataFrame(file_rows)

        st.caption(f"{len(files_df)} YAML file/table combination(s) for **{layer}** — check the ones to run, one file or many.")
        # Key includes select_all so toggling it forces a fresh grid instead of
        # keeping stale per-row edits from before the toggle (data_editor
        # doesn't allow writing its state via st.session_state directly).
        edited = st.data_editor(
            files_df,
            column_config={
                "Run": st.column_config.CheckboxColumn(help="Include this file/table in the run"),
                "Validation type": st.column_config.TextColumn(disabled=True),
                "File": st.column_config.TextColumn(disabled=True),
                "Table": st.column_config.TextColumn(disabled=True),
            },
            hide_index=True, width='stretch', key=f"exec_file_grid_{select_all}",
        )

        picked_count_tables = edited.loc[
            (edited["Validation type"] == "count_validation") & edited["Run"], "Table"
        ].tolist()
        picked_data_tables = edited.loc[
            (edited["Validation type"] == "data_validation") & edited["Run"], "Table"
        ].tolist()
        do_count = bool(picked_count_tables)
        do_data = bool(picked_data_tables)
        selected_tables = sorted(set(picked_count_tables) | set(picked_data_tables))

        if selected_tables and set(picked_count_tables) != set(picked_data_tables) and do_count and do_data:
            st.info(
                "Count and data validation have different table selections — a table checked for only one "
                "type will be silently skipped for the other (no matching YAML requested for it)."
            )

        if st.button("🚀 Run validation", type="primary", key="exec_run", disabled=not selected_tables):
            with st.spinner(f"Running {layer} validation against '{environment}' — this executes real queries..."):
                try:
                    result = run_validation(layer, environment, selected_tables, do_count, do_data)
                except Exception as exc:
                    st.error(f"Execution failed to start: {exc}")
                    result = None

            if result:
                if result["run_id"] and result["summaries"]:
                    st.success(f"Run complete — run_id `{result['run_id']}`  ·  exit code {result['returncode']}")
                elif result["run_id"]:
                    st.error(
                        f"Run `{result['run_id']}` finished (exit code {result['returncode']}) but produced no "
                        f"summary — every table validation errored before completing. See log below."
                    )
                else:
                    st.error("Run did not produce a run_id — see raw output below.")

                for vtype, df in result["summaries"].items():
                    with st.container(border=True):
                        st.markdown(f"#### {'🔢' if vtype == 'count_validation' else '🧬'} {vtype.replace('_', ' ').title()} summary")
                        n_total = len(df)
                        n_pass = int((df["status"] == "PASS").sum())
                        n_fail = n_total - n_pass
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Tables checked", n_total)
                        m2.metric("Passed", n_pass)
                        m3.metric("Failed", n_fail, delta=-n_fail if n_fail else None, delta_color="inverse")
                        render_paginated_df(df, key_prefix=f"exec_summary_{vtype}")

                if result["diff_files"]:
                    with st.container(border=True):
                        st.markdown("#### 🔍 Mismatch detail — row-level diffs")
                        st.caption("Local files only — never sent to any AI/LLM.")
                        for f in result["diff_files"]:
                            try:
                                diff_df = pd.read_csv(f)
                                n_rows = len(diff_df)
                            except Exception as exc:
                                st.warning(f"Could not read `{f.name}`: {exc}")
                                continue
                            with st.expander(f"`{f.relative_to(_PROJECT_DIR)}` — {n_rows} mismatched row(s)", expanded=False):
                                render_paginated_df(diff_df, key_prefix=f"exec_diff_{f.stem}", style_status=False)

                if not result["summaries"]:
                    with st.expander("Raw stdout/stderr (no summary was produced)", expanded=True):
                        st.code(result["stdout_tail"] or "(empty)")
                        if result["stderr_tail"]:
                            st.code(result["stderr_tail"])
                elif result["returncode"] != 0:
                    with st.expander("Raw stdout/stderr (non-zero exit — some tables may have errored)", expanded=False):
                        st.code(result["stdout_tail"] or "(empty)")
                        if result["stderr_tail"]:
                            st.code(result["stderr_tail"])

# =============================================================================
# TAB: History & Trends — SQLite-backed validation history (results_store.py),
# populated automatically by every run in the Run Validation tab. Replaces
# manually grepping through Project/output/<layer>/validation_<run_id>/ CSVs.
# =============================================================================
with tab_history:
    st.subheader("Validation history")
    st.caption("Every run from the Run Validation tab is recorded here — counts and pass/fail status only.")

    hist_layer_choice = st.selectbox("Layer", ["All"] + list(_LAYERS), index=0, key="hist_layer")
    hist_layer = None if hist_layer_choice == "All" else hist_layer_choice

    runs_df = results_store.query_runs(layer=hist_layer, limit=50)
    if runs_df.empty:
        st.info("No runs recorded yet — run a validation in the **Run Validation** tab first.")
    else:
        total_checks = int(runs_df["checks"].sum())
        total_passed = int(runs_df["passed"].sum())
        total_failed = int(runs_df["failed"].sum())
        pass_rate = round(100 * total_passed / total_checks, 1) if total_checks else 0
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Runs recorded", len(runs_df))
        m2.metric("Total checks", total_checks)
        m3.metric("Passed", total_passed)
        m4.metric("Failed", total_failed, delta=-total_failed if total_failed else None, delta_color="inverse")
        m5.metric("Pass rate", f"{pass_rate}%")

        with st.container(border=True):
            st.markdown("#### 🗂️ Recent runs")
            render_paginated_df(
                runs_df[["run_id", "layer", "environment", "returncode", "recorded_at", "checks", "passed", "failed"]],
                key_prefix="hist_runs", style_status=False,
            )

        st.divider()
        with st.container(border=True):
            st.markdown("#### 📉 Drill down by table")
            tables = results_store.distinct_tables(layer=hist_layer)
            if not tables:
                st.caption("No per-table results yet.")
            else:
                picked_table = st.selectbox("Table", tables, key="hist_table")
                trend_df = results_store.table_trend(picked_table)
                if not trend_df.empty:
                    trend_df = trend_df.sort_values("run_at")
                    c1, c2 = st.columns(2)
                    c1.metric("Runs for this table", len(trend_df))
                    c2.metric("Latest status", trend_df.iloc[-1]["status"])
                    count_trend = trend_df.dropna(subset=["source_count", "target_count"])
                    if not count_trend.empty:
                        st.line_chart(
                            count_trend.set_index("run_at")[["source_count", "target_count"]],
                        )
                    render_paginated_df(
                        trend_df[["run_id", "validation_type", "status", "source_count", "target_count",
                                  "count_difference", "run_at"]],
                        key_prefix="hist_trend",
                    )

        st.divider()
        with st.container(border=True):
            st.markdown("#### 🔎 Filter all results")
            f1, f2, f3 = st.columns(3)
            with f1:
                status_filter = st.selectbox("Status", ["All", "PASS", "FAIL"], key="hist_status")
            with f2:
                vtype_filter = st.selectbox("Validation type", ["All", "count_validation", "data_validation"], key="hist_vtype")
            with f3:
                table_filter = st.selectbox("Table", ["All"] + tables, key="hist_table_filter")

            results_df = results_store.query_results(
                layer=hist_layer,
                status=None if status_filter == "All" else status_filter,
                validation_type=None if vtype_filter == "All" else vtype_filter,
                table=None if table_filter == "All" else table_filter,
            )
            render_paginated_df(results_df, key_prefix="hist_results")

# =============================================================================
# TAB: Rule Book
# =============================================================================
with tab_rules:
    st.subheader("Rule book")
    stats = rule_book.stats()
    m1, m2, m3 = st.columns(3)
    m1.metric("Base rules", stats["base_rules"])
    m2.metric("Learned rules", stats["learned_rules"])
    m3.metric("Total", stats["total_rules"])

    with st.expander("ℹ️ What is a Base rule, a Draft rule, and an Active rule?", expanded=False):
        st.markdown(
            "- **Base rule** — built into the code (`src/rules/postgres_base_rules.py`). Always used first for "
            "any type pair (e.g. `NUMERIC → NUMBER`). It can never be overridden or hidden by anything below.\n"
            "- **Draft rule** — a *learned* rule that has been saved but not yet approved. It's advisory only: "
            "it's shown here and given to the AI as extra context, but it is **never used to generate real SQL**. "
            "Every new rule (from the AI paste tool or the manual form) starts as a draft.\n"
            "- **Active rule** — a draft rule that someone reviewed and clicked **Activate** on. Only then can it "
            "actually be used — and only as a *gap filler*, for a type pair that no base rule already covers. "
            "It can never replace or shadow a base rule.\n\n"
            "**In short:** Base rules always run. Draft rules never run — activate a draft to let it run."
        )

    def rule_rows(entries):
        rows = []
        for e in entries:
            sample_col = "amount" if "numeric" in e.id or "integer" in e.id else "col"
            rows.append({
                "ID": e.id, "Name": e.display_name,
                "Source type": e.source_type, "Target type": e.target_type,
                "Source SQL (example)": e.pg_sql_template.replace("{col}", sample_col) if e.pg_sql_template else "",
                "Snowflake SQL (example)": e.sf_sql_template.replace("{col}", sample_col) if e.sf_sql_template else "",
                "Description": e.description,
            })
        return rows

    st.markdown("**Base rules** (code-defined, `src/rules/postgres_base_rules.py`)")
    st.caption(
        "Always checked first for any type pair — a learned rule below can never shadow or change one of these. "
        "The SQL columns below are the ACTUAL expression run against real data — read those, not just the description, "
        "to confirm a rule does what you expect."
    )
    st.info(
        "**How the key rules work:**\n"
        "- **Numeric / Decimal** — cast to text at full native precision. No rounding is applied, so any real "
        "precision drift between source and target shows up as a mismatch instead of being hidden.\n"
        "- **Timestamp / Timestamp TZ** — formatted to microsecond precision (`.US` / `.FF6` / `.ffffff` depending "
        "on dialect). Timezone-aware values are converted to UTC first, then formatted; fractional seconds are "
        "always compared, never stripped.\n"
        "- **UUID** — cast to text and trimmed only. Case is compared exactly as stored — it is NOT normalized to "
        "upper/lowercase, so a genuine case difference between source and target will surface as a mismatch.\n\n",
        icon="📌",
    )
    st.dataframe(rule_rows(rule_book.base_rules()), width='stretch', hide_index=True)

    st.markdown("**Learned rules** (`src/rule_book_learned.json`)")
    st.caption(
        "Draft = advisory only (AI prompt context), never affects generated SQL. "
        "Active = also used as a gap filler for type pairs no base rule owns — click Activate to promote one."
    )
    learned = rule_book.learned_rules()
    if learned:
        for r in learned:
            lc1, lc2, lc3, lc4, lc5 = st.columns([2, 2, 2, 1, 1])
            lc1.write(f"**{r.id}**")
            lc2.write(f"{r.source_type} → {r.target_type}")
            lc3.write(f"reuses: `{r.reuses_rule}`" if r.reuses_rule else "_advisory only_")
            if r.status == "active":
                lc4.success("active", icon="✅")
            else:
                lc4.info("draft", icon="📝")
            with lc5:
                if r.status == "active":
                    if st.button("Deactivate", key=f"deact_{r.id}"):
                        rule_book.deactivate_learned_rule(r.id)
                        st.rerun()
                elif r.reuses_rule:
                    if st.button("Activate", key=f"act_{r.id}"):
                        rule_book.activate_learned_rule(r.id)
                        flash(f"'{r.id}' is now active — used as a gap filler for {r.source_type} → {r.target_type}.", icon="✅")
                        st.rerun()
                else:
                    st.caption("no base rule to reuse — advisory only")
    else:
        st.caption("No learned rules yet.")

    st.divider()
    st.markdown("**Add rules from a pasted table (AI-assisted)**")
    st.caption(
        "Paste any type-mapping table or free text (e.g. `nvarchar -> TEXT`, or a full "
        "MSSQL/Postgres → Snowflake table). The AI can only ever reuse an EXISTING base "
        "rule's already-tested behavior — it cannot invent new SQL. Rows it can't confidently "
        "match are flagged for you to resolve manually."
    )
    raw_rules_text = st.text_area(
        "Paste type mappings here", height=140, key="rule_paste_text",
        placeholder="bit (0,1)      -> BOOLEAN\nmoney          -> NUMBER\nnvarchar       -> TEXT\ntimestamp      -> BINARY",
    )
    rule_parse_model = select_or_type(
        "AI model for parsing", available_models_for_ui(), os.getenv("DIAL_MODEL", "gpt-4o"),
        "rule_parse_model", format_func=_model_label,
    )

    if st.button("✨ Parse with AI", key="parse_rules_btn"):
        if not raw_rules_text.strip():
            st.error("Paste some type mappings first.")
        else:
            with st.spinner("Parsing pasted rules..."):
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
                "Source type": p.source_type,
                "Target type": p.target_type,
                "Dialect": p.dialect,
                "Reuses rule": p.reuses_rule or "",
                "Confidence": p.confidence,
                "Status": status,
                "Note": p.note,
            })
        df = pd.DataFrame(rows)

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
            hide_index=True, width='stretch', key="rule_proposal_editor",
        )

        if st.button("💾 Save checked rows as draft rules", key="save_rule_proposals"):
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
                flash(f"Saved {saved} rule(s) as draft — activate them above to make them live.", icon="📖")
                st.session_state.pop("rule_proposals", None)
                st.rerun()
            elif skipped:
                st.warning("Nothing saved — check that at least one row has 'Save' checked and a 'Reuses rule' chosen.")

    st.divider()
    st.markdown("**Add a custom (learned) rule manually**")
    st.caption("New rules start as draft (advisory only) — activate them above to make them live gap fillers.")
    with st.form("add_rule_form"):
        c1, c2 = st.columns(2)
        rule_id = c1.text_input("Rule id (snake_case)")
        display_name = c2.text_input("Display name")
        description = st.text_area("Description")
        when_to_apply = st.text_input("When to apply (e.g. 'source=VARCHAR maps to target=STRING')")
        c3, c4 = st.columns(2)
        source_type = c3.text_input("Source type (e.g. VARCHAR)")
        target_type = c4.text_input("Target type (e.g. STRING)")
        c5, c6 = st.columns(2)
        pg_sql_template = c5.text_input("Source SQL template (use {col})")
        sf_sql_template = c6.text_input("Snowflake SQL template (use {col})")
        submitted = st.form_submit_button("Save learned rule")

        if submitted:
            if not rule_id or not display_name:
                st.error("Rule id and display name are required.")
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
                    flash(f"Learned rule '{rule_id}' saved to rule_book_learned.json", icon="📖")
                    st.rerun()
                elif ok is not None:
                    st.error("Could not save — a rule with this id may already exist.")

# =============================================================================
# TAB: Exclusions
# =============================================================================
with tab_excl:
    st.subheader("Per-source exclusion policy")
    st.caption("One file per source type — every entry applies to BOTH that source and the Snowflake target.")

    for db_type in SOURCE_TYPES:
        label = _DB_TYPE_LABELS.get(db_type, db_type)
        path = _exclusions_path_for(db_type)
        with st.expander(f"{label}  —  {path.name}", expanded=False):
            all_excl = _get_all_exclusions(db_type)
            static_set = {c.lower() for c in STATIC_EXCLUDE_COLUMNS}
            user_excl = sorted(c for c in all_excl if c not in static_set)
            st.write("**Static (built-in, cannot be removed here):**", ", ".join(STATIC_EXCLUDE_COLUMNS))
            st.write("**User-saved global exclusions:**")
            if user_excl:
                for col in user_excl:
                    rc1, rc2 = st.columns([5, 1])
                    rc1.write(f"`{col}`")
                    if rc2.button("🗑️ Remove", key=f"remove_excl_{db_type}_{col}"):
                        if _remove_global_user_exclusion(db_type, col):
                            flash(f"Removed '{col}' from {label} exclusions.", icon="🗑️")
                            st.rerun()
                        else:
                            st.error(f"Could not remove '{col}' — it may already be gone.")
            else:
                st.caption("(none)")

    st.divider()
    st.markdown("**Add a new global exclusion**")
    with st.form("add_exclusion_form"):
        col_names = st.text_input("Column name(s), comma-separated")
        reason = st.text_input("Reason", value="User-defined global exclusion")
        targets = st.multiselect(
            "Applies to source type(s)",
            options=[_DB_TYPE_LABELS.get(t, t) for t in SOURCE_TYPES],
            default=[],
        )
        submitted = st.form_submit_button("Save exclusion")

        if submitted:
            cols = [c.strip() for c in col_names.split(",") if c.strip()]
            label_to_type = {_DB_TYPE_LABELS.get(t, t): t for t in SOURCE_TYPES}
            chosen_types = [label_to_type[t] for t in targets]
            if not cols or not chosen_types:
                st.error("Enter at least one column name and pick at least one source type.")
            else:
                for db_type in chosen_types:
                    for col in cols:
                        _save_global_user_exclusion(db_type, col, reason)
                flash(f"Saved {len(cols)} column(s) to exclusions for {', '.join(targets)}", icon="🚫")
                st.rerun()

# =============================================================================
# TAB: Usage & Cost
# =============================================================================
with st.sidebar.expander("📊 Usage & Cost", expanded=False):
    import datetime
    from collections import defaultdict

    st.subheader("AI token usage & estimated cost")
    st.caption(
        "Real token counts from every AI call (column mapping + SQL generation), "
        "logged to token_usage_analysis/logs/token_usage.jsonl. Cost is estimated "
        "from public list prices — see token_usage_analysis/pricing.json."
    )

    records = _load_token_records()
    pricing = _load_pricing()

    if not records:
        st.info("No AI calls logged yet. Run a Single YAML or Batch YAML generation with an AI key configured.")
    else:
        def _record_date(r: dict):
            try:
                return datetime.datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S").date()
            except Exception:
                return None

        today = datetime.date.today()
        last_7 = today - datetime.timedelta(days=6)

        today_records = [r for r in records if _record_date(r) == today]
        week_records = [r for r in records if (d := _record_date(r)) and last_7 <= d <= today]

        def _totals(recs):
            tokens = sum(r.get("total_tokens", 0) for r in recs)
            cost = sum(_cost_for(r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), pricing) for r in recs)
            return tokens, cost

        today_tokens, today_cost = _totals(today_records)
        week_tokens, week_cost = _totals(week_records)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Today — cost", f"${today_cost:.4f}")
        c2.metric("Today — tokens", f"{today_tokens:,}")
        c3.metric("Last 7 days — cost", f"${week_cost:.4f}")
        c4.metric("Last 7 days — tokens", f"{week_tokens:,}")
        c5.metric("Last 7 days — AI calls", f"{len(week_records):,}")

        st.markdown("**Daily cost — last 7 days**")
        daily_cost = defaultdict(float)
        for d_offset in range(6, -1, -1):
            daily_cost[(today - datetime.timedelta(days=d_offset)).isoformat()] = 0.0
        for r in week_records:
            d = _record_date(r)
            if d:
                daily_cost[d.isoformat()] += _cost_for(
                    r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), pricing
                )
        import pandas as pd
        chart_df = pd.DataFrame({"Date": list(daily_cost.keys()), "Cost (USD)": list(daily_cost.values())}).set_index("Date")
        st.bar_chart(chart_df)

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Last 7 days — by model**")
            by_model = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
            for r in week_records:
                m = r.get("model", "unknown")
                by_model[m]["calls"] += 1
                by_model[m]["tokens"] += r.get("total_tokens", 0)
                by_model[m]["cost"] += _cost_for(m, r.get("prompt_tokens", 0), r.get("completion_tokens", 0), pricing)
            st.dataframe(
                [{"Model": m, "Calls": s["calls"], "Tokens": s["tokens"], "Cost (USD)": round(s["cost"], 4)} for m, s in sorted(by_model.items())],
                width='stretch', hide_index=True,
            )
        with col_b:
            st.markdown("**Last 7 days — by call type**")
            by_type = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
            for r in week_records:
                t = r.get("call_type", "unknown")
                by_type[t]["calls"] += 1
                by_type[t]["tokens"] += r.get("total_tokens", 0)
                by_type[t]["cost"] += _cost_for(r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), pricing)
            st.dataframe(
                [{"Call type": t, "Calls": s["calls"], "Tokens": s["tokens"], "Cost (USD)": round(s["cost"], 4)} for t, s in sorted(by_type.items())],
                width='stretch', hide_index=True,
            )

        st.divider()
        all_tokens, all_cost = _totals(records)
        st.caption(f"All-time: {len(records):,} AI calls · {all_tokens:,} tokens · ${all_cost:.4f} estimated cost.")
        st.code("python token_usage_analysis/report_token_usage.py --all", language="bash")

# =============================================================================
# FLOATING WIDGET: Gemini Chat — fixed bottom-right bubble, not a tab.
# Toggle button + panel are two separate CSS-fixed containers (identified by
# Streamlit's `key=` -> `.st-key-<key>` class) so the panel's visibility can
# be flipped by injecting different CSS each rerun, without needing to nest
# the body under an extra `if`/indent level.
# =============================================================================
st.markdown("""
<style>
.st-key-gemini_chat_toggle {
    position: fixed !important; bottom: 24px !important; right: 24px !important;
    z-index: 10000 !important; left: auto !important;
    width: 64px !important; height: 64px !important;
}
.st-key-gemini_chat_toggle div[data-testid="stVerticalBlock"] { gap: 0; }
.st-key-gemini_chat_toggle button {
    border-radius: 50% !important; width: 64px !important; height: 64px !important;
    min-width: 64px !important; padding: 0 !important;
    font-size: 1.6rem !important; line-height: 1 !important;
    background: linear-gradient(135deg, #6C5CE7 0%, #A29BFE 100%) !important;
    color: white !important; border: none !important;
    box-shadow: 0 4px 16px rgba(108,92,231,0.45) !important;
    transition: transform 0.15s ease, box-shadow 0.15s ease !important;
}
.st-key-gemini_chat_toggle button:hover {
    transform: scale(1.08) !important;
    box-shadow: 0 6px 20px rgba(108,92,231,0.6) !important;
}
.st-key-gemini_chat_panel {
    position: fixed !important; bottom: 100px !important; right: 24px !important;
    z-index: 9999 !important; left: auto !important;
    width: 400px !important; max-width: 92vw; max-height: 70vh; overflow-y: auto;
    background: var(--background-color, white); border-radius: 16px;
    box-shadow: 0 10px 32px rgba(0,0,0,0.28); padding: 0;
    border: 1px solid rgba(128,128,128,0.2);
}
.gemini-chat-header {
    background: linear-gradient(135deg, #6C5CE7 0%, #A29BFE 100%);
    color: white; padding: 14px 18px; border-radius: 16px 16px 0 0;
    display: flex; align-items: center; gap: 10px;
}
.gemini-chat-header .gemini-logo {
    width: 30px; height: 30px; border-radius: 50%;
    background: rgba(255,255,255,0.2); display: flex; align-items: center;
    justify-content: center; font-size: 1.1rem; flex-shrink: 0;
}
.gemini-chat-header .gemini-title { font-weight: 700; font-size: 1.05rem; }
.gemini-chat-header .gemini-subtitle { font-size: 0.78rem; opacity: 0.9; }
.gemini-chat-body { padding: 14px 18px; }
</style>
""", unsafe_allow_html=True)

if "_gemini_chat_open" not in st.session_state:
    st.session_state["_gemini_chat_open"] = False

with st.container(key="gemini_chat_toggle"):
    _toggle_label = "✕" if st.session_state["_gemini_chat_open"] else "✨"
    if st.button(_toggle_label, key="gemini_chat_toggle_btn", help="Gemini Migration Intelligence chat"):
        st.session_state["_gemini_chat_open"] = not st.session_state["_gemini_chat_open"]
        st.rerun()

if not st.session_state["_gemini_chat_open"]:
    st.markdown('<style>.st-key-gemini_chat_panel { display: none !important; }</style>', unsafe_allow_html=True)

with st.container(key="gemini_chat_panel"):
    st.markdown(
        '<div class="gemini-chat-header">'
        '<div class="gemini-logo">✨</div>'
        '<div><div class="gemini-title">Gemini Migration Intelligence</div>'
        '<div class="gemini-subtitle">AI assistant · 24 tools · human-approved actions</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="gemini-chat-body">', unsafe_allow_html=True)
    st.caption(
        "Gemini orchestrates the 24 Migration Validator tools — it never invents data. "
        "Every write action is audited and requires a human reviewer with the appropriate RBAC role."
    )

    # ── Connector + model status ───────────────────────────────────────────
    sys.path.insert(0, str(_SRC_DIR))
    from gemini_connector.gemini_agent import is_gemini_configured, _vertexai_configured

    _dial_key     = os.getenv("DIAL_API_KEY", "")
    _gemini_key   = is_gemini_configured()
    _auth_mode    = os.getenv("AUTH_MODE", "static").upper()
    _connector_ok = bool(os.getenv("CONNECTOR_API_TOKEN") or _auth_mode == "DEV")

    # Determine active backend (mirrors create_agent() priority)
    if _dial_key:
        _ai_backend = "DIAL · " + os.getenv("DIAL_MODEL", "gpt-4o")
        _ai_icon, _ai_status = "✅", "success"
    elif _gemini_key:
        _mode_label = "Vertex AI" if _vertexai_configured() else "Developer API"
        _ai_backend = f"Gemini ({_mode_label}) · " + os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        _ai_icon, _ai_status = "🤖", "success"
    else:
        _ai_backend = "Offline mode"
        _ai_icon, _ai_status = "⚠️", "warning"

    _s1, _s2, _s3 = st.columns(3)
    with _s1:
        if _ai_status == "success":
            st.success(f"**AI Backend** · {_ai_backend}", icon=_ai_icon)
        else:
            st.warning(f"**AI Backend** · {_ai_backend}", icon=_ai_icon)
    with _s2:
        _auth_icon = "🔐" if _auth_mode == "JWT" else ("🔑" if _auth_mode == "STATIC" else "🧪")
        st.info(f"**Auth mode** · {_auth_mode}", icon=_auth_icon)
    with _s3:
        if _connector_ok:
            st.success("**Connector** · ready", icon="✅")
        else:
            st.warning("**Connector** · token not set", icon="⚠️")

    # ── Enterprise identity panel ──────────────────────────────────────────
    with st.expander("🪪 Session Identity & Permissions", expanded=not st.session_state.get("gemini_actor")):
        st.markdown(
            "All approval and write-back actions are attributed to your identity and written "
            "to the immutable audit trail. Enter your corporate email to proceed."
        )
        _id_col1, _id_col2 = st.columns([2, 1])
        gemini_actor = _id_col1.text_input(
            "Corporate email / user ID",
            value=st.session_state.get("gemini_actor", ""),
            key="gemini_actor_input",
            placeholder="firstname.lastname@company.com",
            label_visibility="visible",
        )
        if gemini_actor:
            st.session_state["gemini_actor"] = gemini_actor

        # Derive and display RBAC role from auth mode
        _role_map = {"JWT": "Derived from JWT claims", "STATIC": os.getenv("CONNECTOR_ROLES", "ADMIN"), "DEV": "ADMIN (dev only)"}
        _role_display = _role_map.get(_auth_mode, os.getenv("CONNECTOR_ROLES", "ADMIN"))
        _id_col2.markdown(f"**RBAC Role**")
        _id_col2.code(_role_display, language=None)

        if gemini_actor:
            st.success(f"Identified as **{gemini_actor}** · role: {_role_display}", icon="🪪")
        else:
            st.warning("Identity required before any approval or write-back action is permitted.", icon="⚠️")

    if not _gemini_key:
        st.info(
            "Running in **offline mode** — tool dispatch is available but conversational AI "
            "requires either `GOOGLE_API_KEY`/`GEMINI_API_KEY` in `.env`, or "
            "`GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` (Vertex AI via ADC, "
            "for orgs that disable personal API key creation).",
            icon="ℹ️",
        )

    # Chat history
    if "gemini_messages" not in st.session_state:
        st.session_state["gemini_messages"] = []

    # Process pending prompt from quick action (must happen before rendering)
    if "gemini_pending_prompt" in st.session_state:
        _pending = st.session_state.pop("gemini_pending_prompt")
        st.session_state["gemini_messages"].append({"role": "user", "content": _pending})
        st.rerun()

    # ── Chat history fills the page ────────────────────────────────────────
    for msg in st.session_state["gemini_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Pending reply is rendered as a continuation of history, ABOVE the
    #    input box below — inside a tab, st.chat_input isn't reliably pinned
    #    to the viewport bottom, so DOM order determines what's on top. ─────
    msgs = st.session_state["gemini_messages"]
    if msgs and msgs[-1]["role"] == "user":
        last_user_msg = msgs[-1]["content"]
        with st.chat_message("assistant"):
            with st.spinner("Gemini is working…"):
                try:
                    sys.path.insert(0, str(_SRC_DIR))
                    from gemini_connector.gemini_agent import create_agent

                    agent_key = "gemini_agent_instance"
                    if agent_key not in st.session_state:
                        st.session_state[agent_key] = create_agent()
                    agent = st.session_state[agent_key]

                    if _dial_key or _gemini_key:
                        result = agent.chat(last_user_msg, actor=st.session_state.get("gemini_actor", ""))
                    else:
                        result = agent.chat_offline(last_user_msg)

                    reply = result.get("text", "")
                    if not reply:
                        reply = "_No text response from Gemini. Check API key and model configuration._"

                    st.markdown(reply)

                    # Show tool calls in expander
                    tool_calls = result.get("tool_calls", [])
                    if tool_calls:
                        with st.expander(f"🔧 Tools used ({len(tool_calls)} calls, {result.get('rounds', 0)} round(s))", expanded=False):
                            for tc in tool_calls:
                                st.markdown(f"**`{tc['name']}`**")
                                col_a, col_b = st.columns(2)
                                col_a.json(tc.get("args", {}))
                                col_b.json(tc.get("result", {}))

                    st.session_state["gemini_messages"].append({"role": "assistant", "content": reply})

                except ImportError as exc:
                    err_msg = f"Gemini connector not available: {exc}. Ensure the gemini_connector package is in src/."
                    st.error(err_msg)
                    st.session_state["gemini_messages"].append({"role": "assistant", "content": err_msg})
                except Exception as exc:
                    err_msg = f"Error: {exc}"
                    st.error(err_msg)
                    st.session_state["gemini_messages"].append({"role": "assistant", "content": err_msg})

    # ── Quick-action expander sits just above the chat input ───────────────
    _quick_actions = [
        ("📋 Migration summary",    "Show me a summary of all migrations and which tables need attention."),
        ("🔍 Pending reviews",      "Show me all column mappings that need my approval."),
        ("📊 Business metrics",     "Show me the automation metrics and ROI for the Migration Intelligence Connector."),
        ("🔌 Discover connections", "What database connections are configured?"),
        ("📉 Coverage below 95%",   "Show me all tables in the bronze layer with validation coverage below 95%."),
        ("🔎 Coverage below 100%",  "Show me all tables across all sources where coverage is below 100%."),
    ]
    with st.expander("⚡ Quick actions", expanded=False):
        qa_row1 = st.columns(3)
        for col, (label, prompt) in zip(qa_row1, _quick_actions[:3]):
            if col.button(label, key=f"qa_{label}", use_container_width=True):
                st.session_state["gemini_pending_prompt"] = prompt
                st.rerun()
        qa_row2 = st.columns(3)
        for col, (label, prompt) in zip(qa_row2, _quick_actions[3:]):
            if col.button(label, key=f"qa_{label}", use_container_width=True):
                st.session_state["gemini_pending_prompt"] = prompt
                st.rerun()
        st.divider()
        if st.button("🗑️ Clear chat history", key="gemini_clear", help="Reset conversation and agent"):
            st.session_state["gemini_messages"] = []
            st.session_state.pop("gemini_agent_instance", None)
            st.rerun()

    # Plain form instead of st.chat_input — chat_input pins itself to the
    # true bottom of the whole app (full width), which breaks out of this
    # fixed-position floating panel. A form stays inside the panel's bounds.
    with st.form("gemini_chat_form", clear_on_submit=True, border=False):
        _fc1, _fc2 = st.columns([5, 1])
        user_input = _fc1.text_input("Ask about your migration…", key="gemini_input", label_visibility="collapsed")
        sent = _fc2.form_submit_button("➤")
    if sent and user_input:
        st.session_state["gemini_messages"].append({"role": "user", "content": user_input})
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


# =============================================================================
# TAB: Review & Approve
# =============================================================================
with tab_review:
    st.subheader("Review & Approve — Human-in-the-Loop Governance")
    st.caption(
        "AI-generated column mappings, rules, and validation plans require human sign-off "
        "before they affect any production query. Every decision is written to an immutable "
        "audit trail with your identity, timestamp, and reason."
    )

    sys.path.insert(0, str(_SRC_DIR))
    try:
        from gemini_connector.approval_store import approval_store, ApprovalStatus
        from gemini_connector.audit import audit_logger
        from gemini_connector.tools import get_migration_summary, get_business_metrics
    except ImportError:
        st.error("Gemini connector package not found. Ensure src/gemini_connector/ exists.")
        st.stop()

    # ── Enterprise identity panel (shared with Gemini Chat via session state) ─
    _rev_auth_mode = os.getenv("AUTH_MODE", "static").upper()
    _rev_role_map  = {"JWT": "Derived from JWT claims", "STATIC": os.getenv("CONNECTOR_ROLES", "ADMIN"), "DEV": "ADMIN (dev only)"}
    _rev_role      = _rev_role_map.get(_rev_auth_mode, os.getenv("CONNECTOR_ROLES", "ADMIN"))

    with st.expander("🪪 Reviewer Identity & RBAC Role", expanded=not st.session_state.get("gemini_actor")):
        st.markdown(
            "Approval actions (approve / reject / modify) require at minimum the **REVIEWER** role. "
            "Rule activation requires **RULE_ADMIN**. Plan approval requires **PLAN_APPROVE** permission. "
            "All actions are attributed to your identity in the audit trail."
        )
        _rc1, _rc2 = st.columns([2, 1])
        review_actor = _rc1.text_input(
            "Corporate email / user ID",
            value=st.session_state.get("gemini_actor", ""),
            key="review_actor_input",
            placeholder="firstname.lastname@company.com",
        )
        if review_actor:
            st.session_state["gemini_actor"] = review_actor

        _rc2.markdown("**Auth mode**")
        _rc2.code(_rev_auth_mode, language=None)
        _rc2.markdown("**RBAC role**")
        _rc2.code(_rev_role, language=None)

        if review_actor:
            st.success(f"Reviewing as **{review_actor}** · {_rev_role}", icon="🪪")
        else:
            st.warning("Identity required. Enter your corporate email above before approving anything.", icon="⚠️")

    # ── Summary metrics row ────────────────────────────────────────────────
    approval_stats = approval_store.stats()
    _pending_count   = approval_stats.get("pending", 0)
    _approved_count  = approval_stats.get("approved", 0) + approval_stats.get("auto_accepted", 0)
    _modified_count  = approval_stats.get("modified", 0)
    _rejected_count  = approval_stats.get("rejected", 0)
    _total_count     = approval_stats.get("total", 0)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total",    _total_count)
    m2.metric("Pending",  _pending_count,  delta=f"{'⚠ Action required' if _pending_count else 'None'}", delta_color="inverse" if _pending_count else "off")
    m3.metric("Approved", _approved_count, delta="incl. auto-accepted", delta_color="off")
    m4.metric("Modified", _modified_count)
    m5.metric("Rejected", _rejected_count)

    if _pending_count:
        st.warning(f"**{_pending_count} item(s) require your review.** Scroll to the sections below.", icon="⚠️")
    else:
        st.success("All items have been reviewed. Nothing pending.", icon="✅")

    st.divider()

    # Layer selector is shared across every sub-tab below. "All" aggregates
    # across bronze/silver/gold in the UI — the underlying connector tools
    # (get_migration_summary, get_coverage, list_plans) each take one real
    # layer, so "All" just calls them once per layer and merges the results
    # here rather than changing their documented single-layer contracts.
    layer_choice = st.selectbox(
        "Medallion layer", ["All", "bronze", "silver", "gold"], key="review_layer",
        help="Filters everything below — pending items, coverage, plans, metrics. "
             "'All' aggregates across every layer.",
    )
    _review_layers = list(_LAYERS) if layer_choice == "All" else [layer_choice]

    _needs_action_tab, _overview_tab, _audit_tab = st.tabs(
        ["🔴 Needs Your Action", "📊 Portfolio & Coverage", "📜 Audit Trail"]
    )

    # =========================================================================
    # SUB-TAB: Needs Your Action — pending mappings + plan approvals
    # =========================================================================
    with _needs_action_tab:
        # ── Pending column mapping reviews ─────────────────────────────────
        _auto_threshold = int(float(os.getenv("CONFIDENCE_AUTO_ACCEPT", "0.95")) * 100)
        st.markdown("### Column Mappings Pending Human Review")
        st.caption(
            f"Mappings below **{_auto_threshold}% confidence** are held for human sign-off. "
            "Each card shows the AI's recommendation and the evidence behind it. "
            "Pick one action below — Modify and Reject ask for a reason before they let you confirm."
        )

        pending = approval_store.pending()
        mapping_pending = [r for r in pending if r.entity_type == "mapping"]

        if not mapping_pending:
            st.success("No column mappings pending review.", icon="✅")
        else:
            # Sort: lowest confidence first (most urgent at top)
            mapping_pending_sorted = sorted(mapping_pending, key=lambda x: x.confidence)

            _filter_col, _count_col = st.columns([3, 1])
            _conf_filter = _filter_col.select_slider(
                "Show mappings with confidence at most",
                options=[50, 60, 70, 75, 80, 85, 90, 95, 100],
                value=100,
                key="conf_filter_slider",
            )
            _count_col.metric("Showing", sum(1 for r in mapping_pending_sorted if int(r.confidence * 100) <= _conf_filter),
                              delta=f"of {len(mapping_pending_sorted)} pending")

            for r in mapping_pending_sorted:
                conf_pct = int(r.confidence * 100)
                if conf_pct > _conf_filter:
                    continue

                _conf_label = "🟢 High" if conf_pct >= 85 else ("🟡 Medium" if conf_pct >= 75 else "🔴 Low")
                _conf_color = "green" if conf_pct >= 85 else ("orange" if conf_pct >= 75 else "red")

                with st.container(border=True):
                    # ── Card header ──
                    h1, h2, h3 = st.columns([4, 2, 2])
                    h1.markdown(f"**{r.table}**  ·  `{r.source_column}` → `{r.target_column}`")
                    h2.markdown(f"Confidence: :{_conf_color}[**{conf_pct}%**]  {_conf_label}")
                    h3.caption(f"ID: `{r.id[:16]}…`")

                    # Confidence progress bar
                    st.progress(conf_pct, text=f"AI confidence: {conf_pct}%  (threshold: {_auto_threshold}%)")

                    # ── Metadata row ──
                    dc1, dc2, dc3 = st.columns(3)
                    dc1.caption(f"**Match method:** {r.match_method or '—'}")
                    dc2.caption(f"**Rule:** {r.transformation_rule or '—'}")
                    dc3.caption(f"**Submitted:** {(r.created_at or '')[:16].replace('T', ' ')}")

                    # ── AI recommendation ──
                    if r.ai_recommendation:
                        st.info(f"**AI recommendation:** {r.ai_recommendation}", icon="🤖")

                    # ── Mapping detail from plan ──
                    try:
                        from core.plan_store import PlanStore
                        plan_store = PlanStore()
                        plan_obj = None
                        for _l in _review_layers:
                            plan_obj = plan_store.load_for_table(r.table, _l)
                            if plan_obj:
                                break
                        if plan_obj:
                            mapping_entry = next(
                                (m for m in plan_obj.mappings if m.source_column == r.source_column), None
                            )
                            if mapping_entry:
                                with st.expander("🔍 Column detail from validation plan", expanded=False):
                                    detail_cols = st.columns(4)
                                    detail_cols[0].metric("Source type", mapping_entry.source_type or "—")
                                    detail_cols[1].metric("Target type", mapping_entry.target_type or "—")
                                    detail_cols[2].metric("AI resolved", "Yes" if mapping_entry.ai_resolved else "No")
                                    detail_cols[3].metric("Primary key", "Yes" if mapping_entry.is_primary_key else "No")
                    except Exception:
                        pass

                    # ── Decision area — one explicit button per action instead of a
                    #    radio + generic "Submit" (which button does what was unclear) ──
                    st.markdown("**Your decision:**")
                    _draft_key = f"draft_action_{r.id}"
                    b_approve, b_modify, b_reject, b_jira = st.columns(4)
                    if b_approve.button("✅ Approve", key=f"approve_btn_{r.id}", use_container_width=True):
                        st.session_state[_draft_key] = "Approve"
                    if b_modify.button("✏️ Modify", key=f"modify_btn_{r.id}", use_container_width=True):
                        st.session_state[_draft_key] = "Modify"
                    if b_reject.button("❌ Reject", key=f"reject_btn_{r.id}", use_container_width=True):
                        st.session_state[_draft_key] = "Reject"
                    if b_jira.button("🎫 Jira", key=f"jira_btn_{r.id}", use_container_width=True,
                                      help="Raise a Jira ticket for this mapping — doesn't change its approval status."):
                        st.session_state[_draft_key] = "Jira"

                    if st.session_state.get(_draft_key) == "Jira":
                        from gemini_connector import jira_client
                        if not jira_client.is_configured():
                            st.warning(
                                "Jira isn't configured — set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, "
                                "JIRA_PROJECT_KEY in .env to enable this.", icon="🎫",
                            )
                        else:
                            _jira_summary = st.text_input(
                                "Ticket summary", key=f"jira_summary_{r.id}",
                                value=f"[{layer_choice if layer_choice != 'All' else 'migration'}] "
                                      f"Review mapping {r.table}.{r.source_column} → {r.target_column}",
                            )
                            _jira_desc = st.text_area(
                                "Ticket description", key=f"jira_desc_{r.id}",
                                value=(
                                    f"Table: {r.table}\nColumn: {r.source_column} -> {r.target_column}\n"
                                    f"Confidence: {int(r.confidence * 100)}%\nMatch method: {r.match_method or '-'}\n"
                                    f"Rule: {r.transformation_rule or '-'}\n"
                                    f"AI recommendation: {r.ai_recommendation or '-'}\n"
                                    f"Raised by: {review_actor or '(not set)'} via Migration Validator Review & Approve."
                                ),
                                height=140,
                            )
                            if st.button("🎫 Create Jira ticket", key=f"jira_create_{r.id}", type="primary"):
                                if not review_actor:
                                    st.error("Enter your corporate email at the top before raising a ticket.", icon="⚠️")
                                else:
                                    try:
                                        ticket = jira_client.create_ticket(
                                            _jira_summary, _jira_desc,
                                            labels=["migration-validator", r.table],
                                        )
                                        st.session_state.pop(_draft_key, None)
                                        flash(f"Jira ticket {ticket['key']} created", icon="🎫")
                                        st.success(f"Created [{ticket['key']}]({ticket['url']})", icon="🎫")
                                    except jira_client.JiraError as exc:
                                        st.error(f"Jira ticket creation failed: {exc}", icon="❌")

                    _action_verb = st.session_state.get(_draft_key)
                    new_target  = r.target_column
                    new_rule    = r.transformation_rule
                    reason_text = ""

                    if _action_verb == "Approve":
                        st.info("You're about to accept the AI's mapping exactly as shown above.", icon="✅")
                    elif _action_verb == "Modify":
                        mc1, mc2 = st.columns(2)
                        new_target = mc1.text_input(
                            "New target column", value=r.target_column,
                            key=f"mod_target_{r.id}",
                            help="Override the AI-suggested target column name",
                        )
                        new_rule = mc2.text_input(
                            "New transformation rule", value=r.transformation_rule,
                            key=f"mod_rule_{r.id}",
                            help="Override the transformation rule applied to this column",
                        )
                        reason_text = st.text_area(
                            "Reason (required — written to audit trail)",
                            key=f"reason_{r.id}",
                            placeholder="Explain why you are modifying this mapping. This is recorded permanently.",
                            height=80,
                        )
                    elif _action_verb == "Reject":
                        reason_text = st.text_area(
                            "Reason (required — written to audit trail)",
                            key=f"reason_{r.id}",
                            placeholder="Explain why you are rejecting this mapping. This is recorded permanently.",
                            height=80,
                        )

                    if _action_verb and _action_verb != "Jira":
                        _submit_label = {
                            "Approve": f"✅ Confirm approval — {r.table}.{r.source_column}",
                            "Modify":  f"✏️ Save modification — {r.table}.{r.source_column}",
                            "Reject":  f"❌ Confirm rejection — {r.table}.{r.source_column}",
                        }[_action_verb]

                        if st.button(_submit_label, key=f"submit_{r.id}", type="primary", use_container_width=True):
                            if not review_actor:
                                st.error("Enter your corporate email at the top of the page before submitting.", icon="⚠️")
                            elif _action_verb in ("Reject", "Modify") and not reason_text.strip():
                                st.error("A reason is required for reject and modify actions.", icon="⚠️")
                            else:
                                from gemini_connector.tools import approve_mapping, reject_mapping, modify_mapping
                                if _action_verb == "Approve":
                                    res = approve_mapping(r.id, review_actor, reason="Approved via Review & Approve UI")
                                elif _action_verb == "Reject":
                                    res = reject_mapping(r.id, review_actor, reason_text.strip())
                                else:
                                    res = modify_mapping(r.id, review_actor, new_target, new_rule, reason_text.strip())

                                if res.get("status") == "ok":
                                    st.session_state.pop(_draft_key, None)
                                    flash(f"Mapping {r.id} {_action_verb.lower()}d by {review_actor}", icon="✅")
                                    st.rerun()
                                else:
                                    st.error(f"Action failed: {res.get('message', 'unknown error')}", icon="❌")
                    elif not _action_verb:
                        st.caption("Choose Approve, Modify, Reject, or Jira above to continue.")

        st.divider()

        # ── Plan approvals ───────────────────────────────────────────────────
        st.markdown("### Validation Plan Approvals")
        st.caption(
            "Each card below is a generated validation plan for one source table. "
            "Approve the plan to authorise it for production validation runs. "
            "Plans with warnings still require approval — the warnings are surfaced here for your review."
        )

        try:
            from core.plan_store import PlanStore
            plan_store_r = PlanStore()
            # (path, actual_layer) pairs — "All" means several real layers, so
            # each plan keeps track of which one it actually came from.
            plan_entries = [
                (path, _l) for _l in _review_layers for path in plan_store_r.list_plans(_l)
            ]

            if not plan_entries:
                st.info(f"No validation plans found in the **{layer_choice}** layer.", icon="ℹ️")
            else:
                for path, _plan_layer in plan_entries:
                    try:
                        plan_obj = plan_store_r.load(path)
                    except Exception:
                        continue

                    plan_id = f"plan/{_plan_layer}/{plan_obj.source_table}"
                    plan_rec = approval_store.get(plan_id)
                    plan_status = plan_rec.status if plan_rec else "not_submitted"
                    excl_s = plan_obj.exclusion_summary()
                    _cov = excl_s["coverage_pct"]

                    _plan_status_icon = {
                        "approved": "✅", "rejected": "❌", "not_submitted": "🕐"
                    }.get(plan_status, "🕐")
                    _plan_status_label = plan_status.replace("_", " ").title()

                    _layer_tag = f" · {_plan_layer}" if layer_choice == "All" else ""
                    with st.expander(
                        f"{_plan_status_icon} **{plan_obj.source_table}** → {plan_obj.target_table}{_layer_tag}  "
                        f"| Coverage: {_cov}%  |  Approval: {_plan_status_label}",
                        expanded=(plan_status == "not_submitted"),
                    ):
                        pc1, pc2, pc3, pc4 = st.columns(4)
                        pc1.metric("Validated cols",   excl_s["validated"])
                        pc2.metric("Excluded cols",    excl_s["excluded_count"])
                        pc3.metric("AI resolved",      len(plan_obj.ai_resolved_matches))
                        pc4.metric("Warnings",         len(plan_obj.warnings),
                                   delta="⚠ review required" if plan_obj.warnings else "none",
                                   delta_color="inverse" if plan_obj.warnings else "off")

                        st.progress(int(_cov), text=f"Column coverage: {_cov}%")

                        if plan_obj.warnings:
                            with st.expander(f"⚠ {len(plan_obj.warnings)} warning(s) — expand to review", expanded=True):
                                for w in plan_obj.warnings:
                                    st.caption(f"• {w}")

                        if plan_status == "approved" and plan_rec:
                            st.success(
                                f"Approved by **{plan_rec.decided_by}** on "
                                f"{(plan_rec.decided_at or '')[:16].replace('T', ' ')}",
                                icon="✅",
                            )
                        else:
                            plan_reason = st.text_input(
                                "Approval note (optional — recorded in audit trail)",
                                key=f"plan_reason_{_plan_layer}_{plan_obj.source_table}",
                                placeholder="e.g. Reviewed DDL, warnings acknowledged, approved for bronze layer.",
                            )
                            if st.button(
                                f"✅ Approve validation plan — {plan_obj.source_table}",
                                key=f"approve_plan_{_plan_layer}_{plan_obj.source_table}",
                                type="primary",
                                use_container_width=True,
                            ):
                                if not review_actor:
                                    st.error("Enter your corporate email at the top before approving.", icon="⚠️")
                                else:
                                    from gemini_connector.tools import approve_plan
                                    res = approve_plan(plan_obj.source_table, _plan_layer, review_actor, plan_reason)
                                    if res.get("status") == "ok":
                                        flash(f"Plan '{plan_obj.source_table}' approved by {review_actor}", icon="✅")
                                        st.rerun()
                                    else:
                                        st.error(f"Could not approve: {res.get('message', '')}", icon="❌")
        except Exception as exc:
            st.error(f"Could not load validation plans: {exc}", icon="❌")

    # =========================================================================
    # SUB-TAB: Portfolio & Coverage — read-only analysis, not action items
    # =========================================================================
    with _overview_tab:
        import pandas as pd

        # ── Portfolio overview — merged across _review_layers when "All" ────
        st.markdown("### Portfolio Overview")
        _summaries = [(l, get_migration_summary(layer=l)) for l in _review_layers]
        _summaries_ok = [(l, s) for l, s in _summaries if s.get("status") == "ok"]
        total = sum(s.get("total_tables", 0) for _, s in _summaries_ok)

        if total == 0:
            st.info(f"No plans found in **{layer_choice}**. Generate plans first using the Generate tabs.", icon="ℹ️")
        else:
            complete = sum(s.get("complete", 0) for _, s in _summaries_ok)
            attention = [
                {**row, "Layer": l} for l, s in _summaries_ok for row in s.get("tables_needing_attention", [])
            ]
            # Weighted average coverage across layers, not a plain mean of means.
            _weighted = sum(s.get("avg_coverage_pct", 0) * s.get("total_tables", 0) for _, s in _summaries_ok)
            _avg_cov = round(_weighted / total, 1) if total else 0

            rc1, rc2, rc3, rc4 = st.columns(4)
            rc1.metric("Total tables",    total)
            rc2.metric("Complete",        complete)
            rc3.metric("Needs attention", len(attention))
            rc4.metric("Avg coverage",    f"{_avg_cov}%", delta="target: 100%", delta_color="off")

            if _avg_cov < 100:
                st.progress(int(_avg_cov), text=f"Coverage {_avg_cov}% across {total} table(s)")

            if attention:
                with st.expander(f"⚠ {len(attention)} table(s) need attention — click to expand", expanded=True):
                    st.dataframe(pd.DataFrame(attention), width='stretch', hide_index=True)

        st.divider()

        # ── Coverage report — merged across _review_layers when "All" ──────
        st.markdown("### Coverage Report")
        cov_col1, cov_col2 = st.columns([2, 1])
        cov_threshold = cov_col1.slider(
            "Flag tables below this coverage %", min_value=50, max_value=100,
            value=95, step=1, key="cov_threshold_slider",
        )
        try:
            from gemini_connector.tools import get_coverage
            _cov_results = [(l, get_coverage(layer=l, threshold=float(cov_threshold))) for l in _review_layers]
            _cov_results_ok = [(l, c) for l, c in _cov_results if c.get("status") == "ok"]

            if not _cov_results_ok:
                st.warning("Coverage query failed for every layer.")
            else:
                total_cov = sum(c.get("total_tables", 0) for _, c in _cov_results_ok)
                below_cov = sum(c.get("below_threshold", 0) for _, c in _cov_results_ok)
                cov_col2.metric(
                    f"Below {cov_threshold}%", below_cov,
                    delta=f"of {total_cov} total",
                    delta_color="inverse",
                )
                cov_rows = [
                    {**r, "layer": l} for l, c in _cov_results_ok for r in c.get("coverage_rows", [])
                ]
                if not cov_rows:
                    st.success(
                        f"All {total_cov} tables in '{layer_choice}' meet or exceed {cov_threshold}% coverage.",
                        icon="✅",
                    )
                else:
                    cov_df = pd.DataFrame([
                        {
                            **({"Layer": r["layer"]} if layer_choice == "All" else {}),
                            "Table":          r["table"],
                            "Source system":  r["source_system"],
                            "Target":         r["target"],
                            "Coverage %":     r["coverage_pct"],
                            "Status":         r["status"],
                            "Failures":       r["failure_count"],
                            "Last run":       (r.get("last_run") or "")[:19].replace("T", " "),
                        }
                        for r in cov_rows
                    ])
                    st.dataframe(cov_df, width="stretch", hide_index=True)
                    if any(c.get("has_more") for _, c in _cov_results_ok):
                        st.caption(f"{below_cov} table(s) total below threshold across {len(_review_layers)} layer(s) — some layers paginated, showing first page each.")
        except Exception as _cov_exc:
            st.caption(f"Coverage tool unavailable: {_cov_exc}")

        st.divider()

        # ── Business metrics ─────────────────────────────────────────────────
        st.markdown("### Business Value Metrics")
        metrics_result = get_business_metrics()
        if metrics_result.get("status") == "ok":
            bm1, bm2, bm3, bm4 = st.columns(4)
            bm1.metric("Tables processed",  metrics_result.get("tables_processed", 0))
            bm2.metric("Columns processed", metrics_result.get("columns_processed", 0))
            bm3.metric("Automation rate",   f"{metrics_result.get('automation_rate_pct', 0)}%")
            bm4.metric("SQL scripts avoided", metrics_result.get("manual_sql_avoided", 0))
            bm5, bm6, bm7, bm8 = st.columns(4)
            bm5.metric("Failures detected",  metrics_result.get("failures_detected", 0))
            bm6.metric("AI tokens used",     f"{metrics_result.get('ai_token_usage', 0):,}")
            bm7.metric("AI calls made",      metrics_result.get("ai_calls_made", 0))
            bm8.metric("Mappings reviewed",  metrics_result.get("mappings_reviewed", 0))

            if metrics_result.get("summary"):
                st.success(metrics_result["summary"], icon="📊")

    # =========================================================================
    # SUB-TAB: Audit Trail
    # =========================================================================
    with _audit_tab:
        st.markdown("### Audit Trail")
        st.caption(
            "Every approval, rejection, and modification is recorded here with the actor identity, "
            "timestamp, and reason. This log is append-only — no record is ever deleted or overwritten."
        )
        _audit_limit_col, _ = st.columns([2, 3])
        _audit_limit = _audit_limit_col.select_slider(
            "Show last N records", options=[10, 20, 30, 50, 100], value=30, key="audit_limit"
        )
        recent_audit = audit_logger.recent(_audit_limit)
        if not recent_audit:
            st.info("No audit records yet. Actions you take in this tab will appear here.", icon="ℹ️")
        else:
            import pandas as pd
            _action_icon = {
                "approved": "✅", "rejected": "❌", "modified": "✏️",
                "auto_accepted": "🤖", "submitted": "📤",
            }
            audit_rows = [
                {
                    "Timestamp":  r.timestamp[:19].replace("T", " "),
                    "Action":     _action_icon.get(r.action, "·") + " " + r.action,
                    "Entity":     r.entity_id,
                    "Actor":      r.actor,
                    "Reason":     (r.reason or "")[:80],
                }
                for r in reversed(recent_audit)
            ]
            st.dataframe(
                pd.DataFrame(audit_rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Timestamp": st.column_config.TextColumn("Timestamp", width="medium"),
                    "Action":    st.column_config.TextColumn("Action",    width="small"),
                    "Entity":    st.column_config.TextColumn("Entity",    width="large"),
                    "Actor":     st.column_config.TextColumn("Actor",     width="medium"),
                    "Reason":    st.column_config.TextColumn("Reason",    width="large"),
                },
            )


# =============================================================================
# TAB: Guide
# =============================================================================
with tab_guide:
    st.subheader("Guide — how to use Migration Validator")
    st.caption(
        "A plain-language walkthrough of every tab. If something in the app is unclear, it should be "
        "explained here — if it isn't, treat that as a gap in this guide, not something you need to "
        "already know."
    )

    st.markdown("### 1. What this app does")
    st.markdown(
        "Migration Validator compares a source table (PostgreSQL / MSSQL / Athena) against its migrated "
        "Snowflake table, column by column, and generates two kinds of output for every table you run:\n\n"
        "- **SQL files** — one query per side (source + Snowflake) that pull back the values to compare.\n"
        "- **A validation YAML** — the config that ties the two SQL queries together, records which "
        "columns were skipped and why, and is what a downstream validation run actually reads.\n\n"
        "There are two ways to generate this output — pick whichever matches how many tables you're doing "
        "right now:"
    )
    st.markdown(
        "- **▶️ Generate Single YAML** — one source table → one Snowflake table. Best when you're setting "
        "up a new table, debugging a mismatch, or want to carefully check one mapping before trusting it.\n"
        "- **📋 Generate Batch YAML** — many source tables in one pass. Best once you've already validated "
        "the pattern works (e.g. via Single YAML) and just need to repeat it across a schema."
    )

    st.divider()
    st.markdown("### 2. Generate Single YAML — step by step")
    st.markdown(
        "1. **① Source** — pick the connection, database, schema, and table you're validating.\n"
        "2. **② Target (Snowflake)** — pick the database, schema, and table it was migrated to.\n"
        "3. **③ Columns to exclude** — see section 4 below, this decides which columns are skipped.\n"
        "4. **④ Column mapping** — click **🔍 Preview column mapping** to see how the AI/fuzzy matcher "
        "paired up every source column with a target column. Fix anything it got wrong directly in the "
        "grid — this is your chance to catch a bad rename match before it's baked into the YAML.\n"
        "5. *(Optional)* **🧪 Generate custom SQL from a prompt** — see section 5 below.\n"
        "6. Click **▶️ Generate SQL + YAML**. The output file paths are shown once it finishes — that's "
        "where the SQL and YAML were written, based on the medallion layer you picked."
    )

    st.divider()
    st.markdown("### 3. Generate Batch YAML — step by step")
    st.markdown(
        "1. **① Source** — pick the connection/database/schema, then select every source table you want "
        "to validate in this run.\n"
        "2. **② Target (Snowflake)** — pick just the database and schema (there's no single Snowflake "
        "table picker here — you map each table individually in the next step).\n"
        "3. **③ Mapping grid** — the app auto-suggests a Snowflake target per source table (exact name "
        "match, or the closest fuzzy match). Rows marked **⚠️ Ambiguous** or **⚠️ No close match found** "
        "are left blank on purpose — pick the correct target yourself rather than trust a guess. You "
        "cannot generate until every row has a distinct target.\n"
        "4. **④ Columns to exclude (per table)** — see section 4 below.\n"
        "5. **⑤ Column mapping** — same idea as Single YAML, but one review grid per table (in a "
        "collapsed expander so the page stays manageable with many tables).\n"
        "6. Click **▶️ Generate All** — it processes every table in sequence and shows a success/failure "
        "row for each one at the end."
    )

    st.divider()
    st.markdown("### 4. Columns to exclude — the 3 categories, explained")
    st.markdown(
        "Every \"Columns to exclude\" section (in both Single YAML and Batch YAML) always shows the "
        "**same table columns split into 3 groups**, so it's never a mystery which columns are being "
        "skipped and why:"
    )
    st.markdown(
        "1. **🔒 Built-in auto-excluded** — hardcoded in the app's code (Fivetran sync/CDC metadata "
        "columns like `_fivetran_synced`, `_fivetran_id`, etc.). Always applied to every table, for every "
        "source type. You cannot remove these from the app — they are not real business columns.\n"
        "2. **🌐 User-defined global exclusions** — columns your team has explicitly added as \"always "
        "skip this, for every table of this source type\" — managed entirely from the **Exclusions** tab "
        "(add there with the form; remove with the 🗑️ Remove button next to each one). Example: your team "
        "decided `uts`/`uuid` should never be compared for PostgreSQL sources.\n"
        "3. **➕ Additional columns to exclude (optional, just for this run)** — the picker box you "
        "interact with directly on the Single YAML / Batch YAML screen. Use this for a one-off skip that "
        "only applies to *this* table, *this* generation run — e.g. a column you know is broken in this "
        "one legacy table. Click into the box to add a column; click the ✕ on a chip to remove it."
    )
    st.markdown(
        "**Why 3 groups instead of one combined list?** Groups 1 and 2 are always applied no matter what "
        "— they're shown as plain, read-only text so you know *why* a column disappeared from the mapping "
        "grid without having to guess. Only group 3 is something you actively choose on this screen. "
        "Before this change, all three were merged into one pre-checked pick-list, which made it easy to "
        "mistake a permanent, team-wide exclusion for something you personally selected (or vice versa)."
    )
    st.info(
        "**Fixed bug:** Single YAML previously failed to reliably apply groups 1 and 2 at all — a Streamlit "
        "widget-state issue meant the auto-excluded columns looked pre-checked but weren't actually excluded "
        "from the generated YAML. This is fixed: groups 1 and 2 are now applied unconditionally, independent "
        "of anything in the picker.",
        icon="🛠️",
    )

    st.divider()
    st.markdown("### 5. Generate custom SQL from a prompt")
    st.markdown(
        "After you preview a table's column mapping (Single YAML or Batch YAML), an optional "
        "**\"🧪 Generate custom SQL from a prompt\"** section appears below the mapping grid. Use it when "
        "the standard column-by-column comparison isn't what you need — for example:\n\n"
        "- *\"Count rows where status = 'active', grouped by region\"* (an aggregate check, not a "
        "row-by-row diff)\n"
        "- *\"Compare only the amount and currency columns for orders placed in the last 30 days\"* "
        "(a filtered subset check)\n"
        "- *\"Row count only, no column comparison\"* (a lightweight sanity check before running the full "
        "validation)"
    )
    st.markdown(
        "How to use it:\n"
        "1. Expand the section and pick which mapped columns should be included in the query.\n"
        "2. Choose the target side: source only, Snowflake only, or both (so you get a matching pair of "
        "queries to diff against each other).\n"
        "3. Type your request in plain English in the prompt box.\n"
        "4. Pick an AI model and click generate. **Always read the generated SQL before trusting it** — "
        "it's a starting point, not a guarantee.\n"
        "5. Save it — it's written to the same output folder as the table's regular SQL/YAML files."
    )

    st.divider()
    st.markdown("### 6. Rule Book — Base / Draft / Active rules")
    st.markdown(
        "The Rule Book decides *how* a source column type gets compared to its Snowflake counterpart "
        "(e.g. does a `TIMESTAMP` get compared to microsecond precision? is a `UUID` comparison case-"
        "sensitive?). Every rule is in exactly one of three states:"
    )
    st.markdown(
        "- **Base rule** — written directly into the code (`src/rules/postgres_base_rules.py`). Always "
        "checked first for a given type pair. Nothing below can ever override, hide, or shadow a base "
        "rule — it's the source of truth.\n"
        "- **Draft rule** — a rule someone (or the AI paste tool) proposed, saved to "
        "`src/rule_book_learned.json`, but not yet reviewed. **A draft never affects real SQL generation "
        "— it's advisory only**, shown here and fed to the AI as extra context, but silently ignored by "
        "the actual query-generation logic. This is the safe default: nothing changes just by proposing "
        "a rule.\n"
        "- **Active rule** — a draft that a person reviewed and clicked **Activate** on. From that point "
        "it *is* used — but strictly as a **gap filler**: only for a type pair that has no base rule at "
        "all. It can never compete with or replace a base rule."
    )
    st.markdown(
        "**Two ways to add a draft rule:**\n"
        "1. **Paste a type-mapping table (AI-assisted)** — paste any text describing type mappings (e.g. "
        "`nvarchar -> TEXT`), and the AI proposes rules. It can only ever *reuse* an existing base rule's "
        "already-tested SQL behavior — it cannot invent brand-new SQL. Review the proposed rows, check "
        "the ones you want, and save — they land as drafts.\n"
        "2. **Add manually** — fill in the form with your own SQL templates. Also starts as a draft.\n\n"
        "Either way, nothing is live until you find it in the **Learned rules** list and click **Activate**."
    )

    st.divider()
    st.markdown("### 7. Exclusions tab — managing the global lists")
    st.markdown(
        "This tab is where the **🌐 user-defined global exclusions** (category 2 in section 4) are "
        "managed — one list per source type (PostgreSQL / MSSQL / Athena), since a column excluded here "
        "applies to every table of that source type going forward.\n\n"
        "- **Add** — enter one or more comma-separated column names, a reason, and which source type(s) "
        "it applies to, then **Save exclusion**.\n"
        "- **Remove** — expand a source type, find the column under **User-saved global exclusions**, "
        "and click **🗑️ Remove** next to it.\n\n"
        "The **🔒 built-in auto-excluded** list (Fivetran metadata columns) is shown here too, for "
        "reference — but it's code-defined and can't be edited from the app."
    )

    st.divider()
    st.markdown("### 8. Typical workflow, end to end")
    st.markdown(
        "1. **Connections** — confirm your source and Snowflake connections are configured (read from "
        "`.env`, nothing to set up in the app itself).\n"
        "2. **Exclusions** — check the global exclusion list for your source type; add anything your team "
        "always wants skipped before you start generating.\n"
        "3. **Generate Single YAML** (for one table, or your first time validating a new pattern) or "
        "**Generate Batch YAML** (once you're confident and have several tables to push through) — pick "
        "table(s), review the column mapping, adjust any run-specific exclusions, optionally add a custom "
        "SQL query, then generate.\n"
        "4. **Rule Book** — if the AI flags a type pair it's unsure about during mapping, come here to "
        "add/paste a rule (starts as a draft), review it carefully, then activate it so future runs use "
        "it automatically.\n"
        "5. **Usage & Cost** — check AI token usage and estimated cost for the session, especially before "
        "a large batch run."
    )

    st.divider()
    st.markdown("### 9. Gemini Migration Intelligence — how the AI chat works")
    st.markdown(
        "The **🤖 Gemini Chat** tab gives you a conversational interface to the entire Migration Validator "
        "platform. It is not a free-form chatbot — Gemini operates strictly within a governed tool loop:"
    )
    st.markdown(
        "1. You send a natural-language request (e.g. *\"Show me all tables in bronze with coverage below 95%\"*).\n"
        "2. Gemini selects one or more of the **24 registered tools** and calls them in sequence (up to 10 rounds).\n"
        "3. Each tool returns real, live data from your source/Snowflake connections or the plan store — "
        "Gemini never fabricates values.\n"
        "4. Gemini synthesises the tool results into a plain-English response.\n"
        "5. Every tool call and its result is visible in the **🔧 Tools used** expander beneath the response."
    )
    st.markdown(
        "**What Gemini can do via the chat:**\n"
        "- Discover connected databases and list available tables\n"
        "- Generate, inspect, and compare validation plans\n"
        "- Surface column mappings that need human review (confidence < 95%)\n"
        "- Show business metrics: automation rate, SQL queries avoided, ROI\n"
        "- Initiate governed approval workflows (writes are audited and role-gated)\n\n"
        "**What Gemini cannot do:**\n"
        "- Invent column names, row counts, or validation results\n"
        "- Approve its own suggestions — a human with the REVIEWER role must sign off\n"
        "- Bypass authentication or ignore RBAC restrictions"
    )
    st.info(
        "**Quick actions** below the status bar send pre-built prompts so you don't have to type. "
        "Each button maps to a specific tool combination — click one, watch the tool calls unfold in the expander.",
        icon="ℹ️",
    )

    st.divider()
    st.markdown("### 10. Authentication — three modes explained")
    st.markdown(
        "The Migration Intelligence Connector (the FastAPI server that backs the Gemini tools) enforces "
        "bearer-token authentication on every request. The mode is controlled by the `AUTH_MODE` env var:"
    )

    _auth_table = {
        "Mode": ["`jwt`", "`static`", "`dev`"],
        "When to use": ["Production / staging", "CI pipelines, hackathon demos", "Local development only"],
        "How it works": [
            "Validates a signed JWT (HS256 or RS256). Roles and permissions are extracted from the token claims (`roles`, `permissions`). Expiry, issuer, and audience are all enforced.",
            "Pre-shared bearer token (`CONNECTOR_API_TOKEN`). Roles configured via `CONNECTOR_ROLES` env var. Simple but no expiry — rotate regularly.",
            "No validation. Every request is accepted with ADMIN role. Never use outside localhost.",
        ],
        "Env vars required": [
            "`JWT_SECRET` (HS256) or `JWT_PUBLIC_KEY` (RS256). Optionally `JWT_ISSUER`, `JWT_AUDIENCE`.",
            "`CONNECTOR_API_TOKEN`, optionally `CONNECTOR_ROLES`.",
            "None.",
        ],
    }
    import pandas as pd
    st.dataframe(pd.DataFrame(_auth_table), hide_index=True, use_container_width=True)

    st.markdown(
        "**Bearer token format** — all requests to the connector must include:\n"
        "```\nAuthorization: Bearer <your-token>\n```\n"
        "A missing or invalid token returns HTTP 401. An expired JWT returns `TOKEN_EXPIRED`."
    )

    st.divider()
    st.markdown("### 11. Role-Based Access Control (RBAC)")
    st.markdown(
        "Every write-back and approval action is gated by a five-level RBAC hierarchy. "
        "Roles are cumulative — each level includes all permissions of the level below it."
    )

    _rbac_table = {
        "Role": ["VIEWER", "REVIEWER", "RULE_ADMIN", "VALIDATION_OPERATOR", "ADMIN"],
        "Permissions": [
            "Read schemas, mappings, rules, validation results",
            "VIEWER + approve / reject / modify column mappings, add comments",
            "REVIEWER + create, update, approve, and activate transformation rules",
            "REVIEWER + trigger validation runs, generate and execute SQL",
            "All permissions across all resources",
        ],
        "Typical user": [
            "Data analyst, read-only stakeholder",
            "Migration engineer, data steward",
            "Platform / rules owner",
            "Validation pipeline operator",
            "Platform administrator",
        ],
    }
    st.dataframe(pd.DataFrame(_rbac_table), hide_index=True, use_container_width=True)

    st.markdown(
        "Fine-grained permissions (e.g. `mapping.approve`, `rule.activate`, `validation.execute`) are "
        "derived from the role list automatically. Explicit `permissions` claims in the JWT override "
        "role-derived defaults, enabling resource-level restrictions (e.g. allow `mapping.approve` "
        "only for a specific schema)."
    )
    st.warning(
        "**Security invariant:** The string `gemini_ai` is rejected as an actor on all write tools — "
        "Gemini can never self-approve. A human identity with the correct role must always confirm.",
        icon="🛡️",
    )

    st.divider()
    st.markdown("### 12. Human-in-the-Loop approval workflow")
    st.markdown(
        "When the AI's confidence in a column mapping falls below the `CONFIDENCE_AUTO_ACCEPT` threshold "
        "(default 95%), the mapping enters **PENDING** state and must be reviewed before it can affect "
        "any generated validation SQL. The **✅ Review & Approve** tab is where this happens."
    )
    st.markdown(
        "**Confidence tiers:**\n"
        "| Confidence | State | Action required |\n"
        "|---|---|---|\n"
        "| ≥ 95% (`CONFIDENCE_AUTO_ACCEPT`) | Auto-accepted | None — proceeds to plan immediately |\n"
        "| 75–95% (`CONFIDENCE_REVIEW`) | Pending human review | Reviewer must approve or reject |\n"
        "| < 75% | Mandatory review | Reviewer must approve, reject, or modify |\n"
    )
    st.markdown(
        "**Three review decisions:**\n"
        "- **Approve** — accepts the AI mapping as-is. Recorded with your identity and timestamp.\n"
        "- **Reject** — discards the mapping. It will not be used. Reason is required and recorded.\n"
        "- **Modify** — accepts the mapping but substitutes a different target column you specify. "
        "Both the original AI suggestion and your override are recorded for auditability."
    )
    st.markdown(
        "All decisions use **optimistic concurrency control** (OCC) — a version token ensures two "
        "reviewers cannot simultaneously approve the same mapping, eliminating race conditions."
    )

    st.divider()
    st.markdown("### 13. Audit trail — what is recorded and where")
    st.markdown(
        "Every write action (approval, rejection, modification, rule activation, plan sign-off) is "
        "appended to an **immutable append-only JSONL audit log** at `output/audit_log.jsonl`. "
        "Records are never modified or deleted."
    )
    st.markdown(
        "Each audit record contains:\n"
        "- `audit_id` — globally unique identifier for the record\n"
        "- `action` — what happened (e.g. `mapping.approve`, `rule.activate`)\n"
        "- `actor` — your corporate email / user ID\n"
        "- `timestamp` — ISO 8601 UTC\n"
        "- `resource_type` / `resource_id` — what was acted on\n"
        "- `reason` — required for rejections; optional for approvals\n"
        "- `before` / `after` — state snapshot (for modify actions)\n\n"
        "**What is never recorded:** passwords, API keys, JWT secrets, or raw bearer tokens."
    )
    st.info(
        "The audit trail is designed for regulatory and compliance use. It can be exported, shipped "
        "to a SIEM, or reviewed by a security team without exposing any credentials.",
        icon="📋",
    )
