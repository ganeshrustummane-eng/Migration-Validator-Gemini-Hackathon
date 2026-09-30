"""Shared helpers, clients and constants for the Streamlit UI (ADR 0050).

Moved verbatim from webapp/app.py. Import-time code here runs once per
process (paths, .env, caches); anything that must render on every rerun
lives in app.py, ui_theme.py, sidebar.py or views/.
"""
import difflib
import os
import re
import sys
import time
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="snowflake.connector")
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
import scope_filter
from generated_queries.ai_sql_generator import AISQLQueryGenerator, AISQLGenerationError
from runner import list_configured_tables, run_validation, start_validation, collect_validation_result, terminate_validation
import results_store
from Project.utils.environments import ENVIRONMENTS

sys.path.insert(0, str(_ROOT_DIR / "token_usage_analysis"))
from report_token_usage import _load_records as _load_token_records, _load_pricing, _cost_for

SOURCE_TYPES = ("postgresql", "mssql", "athena", "redshift")
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


def _render_diff_file(f, key_prefix: str):
    """Render one row-level result CSV as a wide table.

    Every row is shown (PASS and FAIL).  The table has:
      col_varchar_normalized (index) | status | col_text (source) | col_text (target) | ...

    PASS rows are green, FAIL/SOURCE_ONLY/TARGET_ONLY rows are red/amber.
    Differing cells are highlighted yellow so they stand out in the wide view.
    """
    import pandas as pd
    import numpy as np

    try:
        df = pd.read_csv(f)
    except Exception as exc:
        st.warning(f"Could not read `{f.name}`: {exc}")
        return

    n_total = len(df)
    n_fail  = int((df["status"] != "PASS").sum()) if n_total else 0
    n_pass  = n_total - n_fail
    label   = f.stem.split("_result_")[0] if "_result_" in f.stem else f.stem

    # Identify data columns from __source/__target pairs
    src_col_keys = [c for c in df.columns if c.endswith("__source")]
    data_cols    = [c[: -len("__source")] for c in src_col_keys]

    # ── Build wide display DataFrame ─────────────────────────────────────────
    # Columns: row_key | status | col1 (source) | col1 (target) | col2 ...
    display_cols = {"row_key": df["row_key"], "status": df["status"]}
    final_col_names = ["row_key", "status"]

    for col in data_cols:
        short = col.removesuffix("_normalized") if col.endswith("_normalized") else col
        src_key = f"{col}__source"
        tgt_key = f"{col}__target"
        display_cols[f"{short} (source)"] = df[src_key].fillna("") if src_key in df.columns else ""
        display_cols[f"{short} (target)"] = df[tgt_key].fillna("") if tgt_key in df.columns else ""
        final_col_names += [f"{short} (source)", f"{short} (target)"]

    wide = pd.DataFrame(display_cols)[final_col_names]

    STATUS_BG = {"PASS": "#d4edda", "FAIL": "#f8d7da",
                 "SOURCE_ONLY": "#fff3cd", "TARGET_ONLY": "#fff3cd"}
    STATUS_FG = {"PASS": "#1a7f37", "FAIL": "#c0392b",
                 "SOURCE_ONLY": "#856404", "TARGET_ONLY": "#856404"}

    def _style_wide(df_in):
        styles = pd.DataFrame("", index=df_in.index, columns=df_in.columns)
        for i, row in df_in.iterrows():
            row_status = row["status"]
            bg = STATUS_BG.get(row_status, "")
            # Colour the whole row with the row-level status background
            styles.loc[i, :] = f"background-color: {bg}"
            # Override status cell text colour
            styles.loc[i, "status"] = (
                f"background-color: {bg}; color: {STATUS_FG.get(row_status, '')}; font-weight: 700"
            )
            # Highlight individual cells that differ (yellow) only for FAIL rows
            if row_status not in ("PASS",):
                for col in data_cols:
                    short = col.removesuffix("_normalized") if col.endswith("_normalized") else col
                    sc, tc = f"{short} (source)", f"{short} (target)"
                    if sc in df_in.columns and tc in df_in.columns:
                        sv = str(row.get(sc, ""))
                        tv = str(row.get(tc, ""))
                        if sv != tv:
                            styles.loc[i, sc] = f"background-color: #fff3cd; color: #856404"
                            styles.loc[i, tc] = f"background-color: #fff3cd; color: #856404"
        return styles

    with st.expander(
        f"**{label}** — {n_fail} row(s) FAIL / {n_pass} PASS out of {n_total}",
        expanded=True,
    ):
        m1, m2, m3 = st.columns(3)
        m1.metric("Rows compared", n_total)
        m2.metric("Passed", n_pass)
        m3.metric("Failed", n_fail, delta=-n_fail if n_fail else None, delta_color="inverse")

        st.caption(
            "Each row shows source (PostgreSQL) and target (Snowflake) values side-by-side. "
            "Green = full row matched · Red = mismatch · Yellow cell = the specific value that differed."
        )
        st.dataframe(
            wide.style.apply(_style_wide, axis=None),
            use_container_width=True,
            hide_index=True,
        )


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
# Scope filters from the test team's filter workbooks (ADR 0045) — shared by
# the Bronze and Silver flows. Parsing/rendering lives in src/scope_filter.py.
# ---------------------------------------------------------------------------

_FILTER_WORKBOOK_DIR = _ROOT_DIR / "docs" / "Excel-Files"


@st.cache_data(show_spinner="Reading filter workbook…")
def _load_scope_workbook(path: str, mtime: float) -> dict:
    return scope_filter.load_workbook(path)


def pick_scope_workbook(key: str) -> dict:
    """Workbook picker -> {table_lower: ScopeFilter}; {} when none is chosen."""
    files = sorted(_FILTER_WORKBOOK_DIR.glob("*.xlsx"))
    upload_label = "⬆️ Upload a new workbook…"
    choice = st.selectbox(
        "Filter workbook", ["(none — no workbook filters)"] + [f.name for f in files] + [upload_label],
        key=f"{key}_wb",
        help=f"Workbooks saved in {_FILTER_WORKBOOK_DIR.relative_to(_ROOT_DIR)}. Each row's filter text "
             "(SQL or plain English) is turned into a filter you can review and edit per table below.",
    )
    path = None
    if choice == upload_label:
        up = st.file_uploader("Filter workbook (.xlsx)", type=["xlsx"], key=f"{key}_wb_upload")
        if not up:
            return {}
        path = _FILTER_WORKBOOK_DIR / Path(up.name).name
        if path.exists():
            st.warning(f"`{path.name}` already exists in the workbook folder — pick it from the list, "
                       "or rename the upload to keep both.")
            return {}
        path.write_bytes(up.getvalue())
        st.success(f"Saved to `{path.relative_to(_ROOT_DIR)}` — it will be in the list next time.")
    elif not choice.startswith("(none"):
        path = _FILTER_WORKBOOK_DIR / choice
    if path is None:
        return {}
    try:
        specs = _load_scope_workbook(str(path), path.stat().st_mtime)
    except Exception as exc:
        st.error(f"Could not read `{path.name}`: {exc}")
        return {}
    n_full = sum(s.mode == "full" for s in specs.values())
    n_bad = sum(s.blocked for s in specs.values())
    st.caption(f"{len(specs)} table(s): {len(specs) - n_full - n_bad} filtered, {n_full} compared in full"
               + (f", **{n_bad} need fixing** before they can be generated" if n_bad else ""))
    return specs


def _ai_backend_label() -> str:
    """Which AI backend AISQLQueryGenerator will pick: DIAL first, Claude when
    only CLAUDE_API_KEY is set (no code change needed to switch)."""
    if os.getenv("DIAL_API_KEY"):
        return "EPAM DIAL"
    return "Claude" if os.getenv("CLAUDE_API_KEY") else ""


def edit_scope_filter(spec, key: str, columns: dict = None):
    """Editable view of one workbook row as JOINs (ADR 0046): conditions on the
    table itself plus one grid row per JOIN. Prose rows can be interpreted by
    AI first; the result is only a starting point for this same editor.
    Returns the (possibly edited) ScopeFilter."""
    import pandas as pd
    st.caption(f"From `{spec.provenance}`" + (f" · size tier **{spec.tier}**" if spec.tier else ""))
    if spec.raw_text:
        st.code(spec.raw_text, language="sql")

    # AI interpretation replaces the starting spec; a new version number gives
    # the widgets below fresh keys so they pick up the AI's values.
    ver_key = f"{key}_ver"
    ai_spec = st.session_state.get(f"{key}_ai_spec")
    if ai_spec is not None:
        spec = ai_spec
    backend = _ai_backend_label()
    _ai_extra = st.text_area(
        "Your instructions for AI (optional)", key=f"{key}_ai_extra", height=70,
        placeholder="e.g. also join companies c on f.company_id = c.id and keep only c.active = true",
        help="Sent with the workbook text and the current filter below, so AI can add joins or "
             "conditions the workbook left out. You can also just edit the grid by hand.",
    )
    _ai_c1, _ai_c2 = st.columns([1, 3])
    with _ai_c1:
        _ai_clicked = st.button("✨ Interpret with AI", key=f"{key}_ai_btn",
                                disabled=not (backend and (spec.raw_text or _ai_extra.strip())),
                                help="Rewrites the filter text (plus your instructions) as JOINs + WHERE for "
                                     "you to review and edit. Uses DIAL_API_KEY, or CLAUDE_API_KEY when DIAL isn't set.")
    with _ai_c2:
        st.caption(f"AI backend: **{backend}**" if backend else "AI unavailable — set DIAL_API_KEY or CLAUDE_API_KEY.")
    if _ai_clicked:
        _gen = AISQLQueryGenerator()
        _note = spec.raw_text
        if spec.mode == "scoped" and (spec.conditions or spec.hops) and not spec.errors:
            _note += f"\n\nCurrent filter (keep it unless the instructions change it):\n{scope_filter.to_sql(spec)}"
        if _ai_extra.strip():
            _note += f"\n\nAdditional instructions from the reviewer:\n{_ai_extra.strip()}"
        with st.spinner("Interpreting filter text…"):
            _res = _gen.interpret_scope_filter(spec.table, _note.strip(), spec.scope_values, columns)
        if _res["sql"]:
            st.session_state[f"{key}_ai_spec"] = scope_filter.from_ai_sql(spec, _res["sql"], _gen.model)
            st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1
            st.rerun()
        for _w in _res["warnings"]:
            st.warning(_w)
    k = f"{key}_v{st.session_state.get(ver_key, 0)}"

    mode = st.radio("Rows to compare", ["scoped", "full"], index=0 if spec.mode == "scoped" else 1,
                    horizontal=True, key=f"{k}_mode",
                    format_func=lambda m: "Only rows matching the filter" if m == "scoped" else "Whole table")
    cond_text = st.text_input(
        f"WHERE conditions on {spec.table}", value=scope_filter.conditions_text(spec.conditions), key=f"{k}_cond",
        disabled=mode == "full", help="Joined with AND, e.g. `created_at >= '2024-08-01'`.",
    )
    hops = pd.DataFrame(
        [{"parent": h.parent, "alias": h.alias, "fk": h.fk, "pk": h.pk,
          "conditions": scope_filter.conditions_text(h.conditions)} for h in spec.hops],
        columns=["parent", "alias", "fk", "pk", "conditions"],
    )
    st.caption(f"JOINs — each row is `JOIN <table> <alias> ON <previous table>.<column> = <alias>.<key>`, "
               f"the first one joining from {spec.table}. Add, remove or change rows as needed.")
    edited = st.data_editor(
        hops, num_rows="dynamic", hide_index=True, width="stretch", key=f"{k}_hops", disabled=mode == "full",
        column_config={
            "parent": st.column_config.TextColumn("JOIN table"),
            "alias": st.column_config.TextColumn("Alias", help="Optional; p1, p2… when blank."),
            "fk": st.column_config.TextColumn("ON: column of previous table"),
            "pk": st.column_config.TextColumn("= column of this table"),
            "conditions": st.column_config.TextColumn("WHERE conditions on this table"),
        },
    )
    new = scope_filter.from_edit(spec, mode, cond_text, edited.fillna("").to_dict("records"))
    if mode == "scoped" and not new.errors:
        st.caption("As one query:")
        st.code(scope_filter.to_sql(new), language="sql")
    for err in (new.errors if mode == "scoped" else []):
        st.error(err)
    for note in new.notes:
        st.caption(f"ℹ️ {note}")
    if mode == "full" and spec.tier.lower() in ("medium", "large"):
        st.warning(f"{spec.tier} table compared in full — expect a long run.")
    return new


def _live_columns(fetch, tables: dict) -> dict:
    """{key: columns} for each {key: table} that can be read; unreadable tables
    are left out, so the filter for them is not checked (and says so)."""
    out = {}
    for k, table in tables.items():
        try:
            out[k] = fetch(table)
        except Exception:
            pass
    if len(out) < len(tables):
        st.caption("⚠️ Some column names could not be checked against the live database.")
    return out


# ---------------------------------------------------------------------------
# Filter history — read from existing plan JSONs, no extra storage needed
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def load_filter_history() -> dict:
    """
    Scan all persisted plan JSON files and return previously-used migration
    filters keyed by source table name.

    Returns: {table_name: [(source_filter, target_filter), ...]}
    Only includes entries where at least source_filter is non-empty.
    Deduplicates within a table. Most-recently-used first.
    """
    plans_root = _ROOT_DIR / "output" / "plans"
    seen: dict = {}   # table -> list of (src_filter, tgt_filter), insertion order = newest first
    if not plans_root.exists():
        return {}
    for plan_file in sorted(plans_root.rglob("*.plan.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            import json as _json
            data = _json.loads(plan_file.read_text(encoding="utf-8"))
            src_filter = data.get("source_filter", "").strip()
            tgt_filter = data.get("target_filter", "").strip()
            if not src_filter:
                continue
            table = data.get("source", {}).get("table") or data.get("source_table", "")
            if not table:
                continue
            pair = (src_filter, tgt_filter)
            if table not in seen:
                seen[table] = []
            if pair not in seen[table]:
                seen[table].append(pair)
        except Exception:
            continue
    return seen


def filter_options_for(table: str) -> list:
    """
    Return previously-used (source_filter, target_filter) pairs for a table.
    Empty list means no history exists yet.
    """
    return load_filter_history().get(table, [])


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

    # ADR 0025: reserve the top slot for the schema-validation summary now,
    # fill it in below (after the flag groups are computed) -- Streamlit
    # renders a container at the position it was created, not where it's
    # filled, so this keeps "Schema Validation" visually ABOVE the editable
    # mapping grid while still reusing the grid's own edit state (corrected
    # targets) to decide what's still an open issue.
    _schema_section = st.container()

    st.markdown("**Column mapping (editable)**")
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

    # corrected_targets: columns the user has manually fixed in the grid this session
    corrected_targets = {
        row["Source Column"]
        for _, row in edited.iterrows()
        if row["Corrected Target"] and row["Corrected Target"] != row["AI/Fuzzy Target"]
    }

    # ADR 0024: columns a human already reviewed and marked OK (missing-column
    # or type-mismatch) don't need to be re-flagged on every subsequent run.
    _schema_ok_cols = {c.lower() for c in _get_all_exclusions("bronze_schema")}

    # skipped = skip_validation=True AND no user correction yet
    skipped_low = [
        r for r in rows
        if r.get("skip_validation")
        and not r.get("target_column")
        and r["source_column"] not in corrected_targets
        and r.get("confidence", 0.0) < 0.75
        and r["source_column"].lower() not in _schema_ok_cols
    ]
    # matched but low confidence (not corrected, not a learned rule)
    low_conf_rows = [
        r for r in rows
        if not r.get("skip_validation")
        and r.get("target_column")
        and r["source_column"] not in corrected_targets
        and r.get("confidence", 1.0) < 0.75
    ]

    # ADR 0024: source/target data-type comparison, informational -- the
    # rules_catalog.json compatibility table isn't confirmed as the bar yet,
    # so a mismatch is flagged for human review, not auto-failed.
    def _norm_type(t: str) -> str:
        return re.sub(r"\s*\([^)]*\)", "", (t or "")).strip().upper()

    type_mismatches = [
        r for r in rows
        if r.get("target_column")
        and not r.get("skip_validation")
        and r["source_column"] not in corrected_targets
        and _norm_type(r.get("source_type", "")) != _norm_type(r.get("target_type", ""))
        and r["source_column"].lower() not in _schema_ok_cols
    ]

    # ADR 0025: the other half of "did every column migrate" -- target
    # (Snowflake) columns that exist live but nothing on the source side
    # claims. Fivetran metadata columns are never expected to have a source
    # counterpart, so they're excluded the same way they are everywhere else.
    _fivetran_meta_cols = {c.lower() for c in STATIC_EXCLUDE_COLUMNS}
    _matched_target_cols = {r["target_column"].upper() for r in rows if r.get("target_column")}
    extra_in_target = [
        c for c in live_tgt_cols
        if c.upper() not in _matched_target_cols
        and c.lower() not in _fivetran_meta_cols
        and c.lower() not in _schema_ok_cols
    ]

    _missing_in_target_n = len(skipped_low) + len(
        [r for r in rows if not r.get("skip_validation") and not r.get("target_column")
         and r["source_column"].lower() not in _schema_ok_cols]
    )
    _total_issues = _missing_in_target_n + len(extra_in_target) + len(type_mismatches) + len(low_conf_rows)
    _matched_count = sum(1 for r in rows if r.get("target_column") and not r.get("skip_validation"))

    with _schema_section:
        st.markdown("### 🧬 Schema Validation")
        st.caption(
            f"Source columns: {len(rows)}  |  Target columns: {len(live_tgt_cols)}  |  "
            f"Matched: {_matched_count}  |  Missing in target: {_missing_in_target_n}  |  "
            f"Extra in target: {len(extra_in_target)}  |  Type mismatches: {len(type_mismatches)}"
        )
        if _total_issues:
            st.warning(f"⚠️ Schema validation: {_total_issues} issue(s) need review")
        else:
            st.success("✅ Schema validation: no issues")

        if extra_in_target:
            _et_c1, _et_c2, _et_c3 = st.columns([3, 1, 1])
            with _et_c1:
                st.warning(
                    f"⚠️ **{len(extra_in_target)} column(s) in target with no source match**: "
                    + ", ".join(f"`{c}`" for c in extra_in_target)
                )
            with _et_c2:
                if st.button("🎫 Raise Jira ticket", key=f"{key_prefix}_extra_jira_btn"):
                    try:
                        from connector.jira_client import create_ticket, is_configured
                        if not is_configured():
                            st.info("Jira not configured — set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `.env`.")
                        else:
                            _t = create_ticket(
                                summary=f"[Migration Validator] Extra target columns with no source match: {sf_table}",
                                description=(
                                    f"Table: {pg_table} -> {sf_table}\n\n"
                                    "Target columns with no source column mapping to them:\n"
                                    + "\n".join(f"  - {c}" for c in extra_in_target)
                                ),
                                labels=["migration-validator", "extra-target-columns", "needs-review"],
                            )
                            st.success(f"Jira ticket created: [{_t['key']}]({_t['url']})")
                    except Exception as _ete:
                        st.error(f"Jira error: {_ete}")
            with _et_c3:
                if st.button("✅ Mark OK", key=f"{key_prefix}_extra_ok_btn"):
                    for c in extra_in_target:
                        _save_global_user_exclusion(
                            "bronze_schema", c,
                            f"{sf_table}.{c}: no source column maps to it -- reviewed OK via mapping UI",
                        )
                    st.rerun()

    unmatched = [
        r["source_column"] for r in rows
        if not r.get("skip_validation") and not r.get("target_column")
        and r["source_column"].lower() not in _schema_ok_cols
    ]

    # ADR 0025: the remaining three flag groups render inside the same
    # top-of-page schema-validation section as the extra-in-target group above.
    with _schema_section:
        if skipped_low:
            _sk_cols = ", ".join(
                f"`{r['source_column']}` ({int(r.get('confidence',0)*100)}%)" for r in skipped_low
            )
            _sk_c1, _sk_c2, _sk_c3 = st.columns([3, 1, 1])
            with _sk_c1:
                st.warning(
                    f"⚠️ **{len(skipped_low)} column(s) skipped — no target match found** "
                    f"(confidence below 75%): {_sk_cols}\n\n"
                    "Set a **Corrected Target** below to include them, mark them OK, or raise a Jira ticket."
                )
            with _sk_c2:
                if st.button("🎫 Raise Jira ticket", key=f"{key_prefix}_skip_jira_btn"):
                    try:
                        from connector.jira_client import create_ticket, is_configured
                        if not is_configured():
                            st.info("Jira not configured — set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `.env`.")
                        else:
                            _t = create_ticket(
                                summary=f"[Migration Validator] Skipped columns need mapping: {pg_table}",
                                description=(
                                    f"Table: {pg_table}\n\nColumns skipped (no target match, confidence <75%):\n"
                                    + "\n".join(
                                        f"  - {r['source_column']} (conf {int(r.get('confidence',0)*100)}%,"
                                        f" reason: {r.get('skip_reason','unknown')})"
                                        for r in skipped_low
                                    )
                                ),
                                labels=["migration-validator", "skipped-columns", "needs-review"],
                            )
                            st.success(f"Jira ticket created: [{_t['key']}]({_t['url']})")
                    except Exception as _ske:
                        st.error(f"Jira error: {_ske}")
            with _sk_c3:
                # ADR 0024: human reviewed, decided this missing column is fine --
                # remember it (bronze_schema exclusions) so it stops re-flagging.
                if st.button("✅ Mark OK", key=f"{key_prefix}_skip_ok_btn"):
                    for r in skipped_low:
                        _save_global_user_exclusion(
                            "bronze_schema", r["source_column"],
                            f"No target match for {pg_table}.{r['source_column']} -- reviewed OK via mapping UI",
                        )
                    st.rerun()

        if unmatched:
            _um_c1, _um_c2 = st.columns([3, 1])
            with _um_c1:
                st.warning(f"No target match for: {', '.join(unmatched)} — pick one below, mark OK, or it will be skipped from validation.")
            with _um_c2:
                if st.button("✅ Mark OK", key=f"{key_prefix}_unmatched_ok_btn"):
                    for col in unmatched:
                        _save_global_user_exclusion(
                            "bronze_schema", col,
                            f"No target match for {pg_table}.{col} -- reviewed OK via mapping UI",
                        )
                    st.rerun()

        if type_mismatches:
            _tm_c1, _tm_c2 = st.columns([3, 1])
            with _tm_c1:
                st.info(
                    f"⚠️ **{len(type_mismatches)} type mismatch(es)** (informational — base rule "
                    "compatibility not confirmed yet, review manually): "
                    + ", ".join(
                        f"`{r['source_column']}` ({r['source_type']} → {r['target_type']})"
                        for r in type_mismatches
                    )
                )
            with _tm_c2:
                if st.button("✅ Mark OK", key=f"{key_prefix}_type_ok_btn"):
                    for r in type_mismatches:
                        _save_global_user_exclusion(
                            "bronze_schema", r["source_column"],
                            f"{pg_table}.{r['source_column']}: {r['source_type']} vs {r['target_type']} "
                            "-- type mismatch reviewed OK via mapping UI",
                        )
                    st.rerun()

        if low_conf_rows:
            _lc_col1, _lc_col2 = st.columns([3, 1])
            with _lc_col1:
                st.info(f"Matched below high confidence: {', '.join(r['source_column'] for r in low_conf_rows)} — review these; a flagged mismatch can still be correct.")
            with _lc_col2:
                if st.button("🎫 Raise Jira ticket", key=f"{key_prefix}_inline_jira_btn"):
                    try:
                        from connector.jira_client import create_ticket, is_configured
                        if not is_configured():
                            st.info("Jira not configured — set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `.env`.")
                        else:
                            _t = create_ticket(
                                summary=f"[Migration Validator] Low-confidence mappings: {pg_table}",
                                description=(
                                    f"Table: {pg_table}\n\nLow-confidence column mappings:\n"
                                    + "\n".join(f"  - {r['source_column']} → {r['target_column']} ({int(r['confidence']*100)}%)" for r in low_conf_rows)
                                ),
                                labels=["migration-validator", "needs-review"],
                            )
                            st.success(f"Jira ticket created: [{_t['key']}]({_t['url']})")
                    except Exception as _lce:
                        st.error(f"Jira error: {_lce}")

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

# =============================================================================
# Row-hash SQL builder — used by Custom SQL tab when a table has no PK.
# Concatenates every normalized column with '|' separator and MD5-hashes the
# result.  Column order follows ordinal_position so both sides hash identically.
# ponytail: Phase 1 — pure hash, no dup_count.  Add GROUP BY + COUNT(*) when
#           duplicate-row tables are encountered and set-compare fails.

def _build_row_hash_sql(columns: list, table_fqn: str, db_type: str) -> str:
    """Return a SELECT MD5(<concat of all normalized cols>) AS row_hash FROM table_fqn."""
    sep = " || '|' || "
    parts = []
    for c in columns:
        col = c["column_name"]
        dt  = (c.get("data_type") or "text").lower()
        if db_type in ("postgresql", "postgres"):
            if "timestamp" in dt:
                expr = f"COALESCE(CAST(TO_CHAR({col}, 'YYYY-MM-DD HH24:MI:SS') AS TEXT), '<<NULL>>')"
            elif "date" == dt:
                expr = f"COALESCE(CAST(TO_CHAR({col}, 'YYYY-MM-DD') AS TEXT), '<<NULL>>')"
            elif "bool" in dt:
                expr = f"COALESCE(CAST(CASE WHEN {col} = true THEN '1' WHEN {col} = false THEN '0' ELSE NULL END AS TEXT), '<<NULL>>')"
            else:
                expr = f"COALESCE(CAST(TRIM({col}) AS TEXT), '<<NULL>>')"
        elif db_type in ("mssql", "sqlserver"):
            if "datetime" in dt or "date" == dt:
                expr = f"COALESCE(CAST(FORMAT({col}, 'yyyy-MM-dd HH:mm:ss') AS VARCHAR(MAX)), '<<NULL>>')"
            elif "bit" in dt:
                expr = f"COALESCE(CAST(CASE WHEN {col} = 1 THEN '1' ELSE '0' END AS VARCHAR(MAX)), '<<NULL>>')"
            else:
                expr = f"COALESCE(CAST(LTRIM(RTRIM({col})) AS VARCHAR(MAX)), '<<NULL>>')"
        elif db_type == "snowflake":
            if "timestamp" in dt or "date" == dt:
                expr = f"COALESCE(CAST(TO_VARCHAR({col}, 'YYYY-MM-DD HH24:MI:SS') AS STRING), '<<NULL>>')"
            elif "boolean" in dt:
                expr = f"COALESCE(CAST(CASE WHEN {col} = TRUE THEN '1' WHEN {col} = FALSE THEN '0' ELSE NULL END AS STRING), '<<NULL>>')"
            else:
                expr = f"COALESCE(CAST(TRIM({col}) AS STRING), '<<NULL>>')"
        else:  # athena / trino
            if "timestamp" in dt or "date" == dt:
                expr = f"COALESCE(CAST(date_format({col}, '%Y-%m-%d %H:%i:%s') AS VARCHAR), '<<NULL>>')"
            elif "boolean" in dt:
                expr = f"COALESCE(CAST(CASE WHEN {col} THEN '1' ELSE '0' END AS VARCHAR), '<<NULL>>')"
            else:
                expr = f"COALESCE(CAST(TRIM({col}) AS VARCHAR), '<<NULL>>')"
        parts.append(expr)

    concat_expr = sep.join(parts)
    if db_type in ("postgresql", "postgres"):
        hash_expr = f"MD5({concat_expr})"
    elif db_type in ("mssql", "sqlserver"):
        hash_expr = f"LOWER(CONVERT(VARCHAR(32), HASHBYTES('MD5', {concat_expr}), 2))"
    elif db_type == "snowflake":
        hash_expr = f"MD5({concat_expr})"
    else:
        hash_expr = f"MD5({concat_expr})"

    return f"SELECT {hash_expr} AS row_hash\nFROM {table_fqn}"


# Every view does `from ui_common import *`; export private names too.
__all__ = [_n for _n in list(globals()) if not _n.startswith("__")]
