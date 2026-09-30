"""📂 Output Files tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

    # (old flat-markdown guide replaced by sub-tabs above)

# =============================================================================
# TAB: Output Files
# =============================================================================
def render():
    import pathlib

    st.markdown("""
    <div style="display:flex;align-items:center;gap:14px;padding:8px 0 16px;">
        <div style="width:44px;height:44px;border-radius:10px;
                    background:linear-gradient(135deg,#0EA5E9,#38BDF8);
                    display:flex;align-items:center;justify-content:center;font-size:1.3rem;">📂</div>
        <div>
            <div style="font-size:1.2rem;font-weight:800;color:#0F172A;">Output Files</div>
            <div style="font-size:0.8rem;color:#64748B;">Browse CSV / Excel files from past validation runs — color-coded PASS / FAIL inline</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    _OUT_ROOT = _ROOT_DIR / "Project" / "output"

    # ── Discover files ────────────────────────────────────────────────────────
    _all_files = sorted(
        [p for p in _OUT_ROOT.rglob("*") if p.suffix in {".csv", ".xlsx"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if _OUT_ROOT.exists() else []

    if not _all_files:
        st.info("No CSV or Excel files found in `Project/output/`. Run a validation first.", icon="📭")
    else:
        # ── Sidebar-style filters in a card ──────────────────────────────────
        with st.container():
            _fc1, _fc2, _fc3 = st.columns([2, 2, 1])
            with _fc1:
                _layer_opts = sorted({p.parts[len(_OUT_ROOT.parts)] for p in _all_files if len(p.parts) > len(_OUT_ROOT.parts)})
                _layer_sel = st.multiselect("Filter by layer", _layer_opts, key="of_layer")
            with _fc2:
                _ext_sel = st.multiselect("File type", [".csv", ".xlsx"], default=[".csv", ".xlsx"], key="of_ext")
            with _fc3:
                _refresh = st.button("🔄 Refresh", use_container_width=True, key="of_refresh")

        _filtered = [
            p for p in _all_files
            if p.suffix in (_ext_sel or {".csv", ".xlsx"})
            and (not _layer_sel or (len(p.parts) > len(_OUT_ROOT.parts) and p.parts[len(_OUT_ROOT.parts)] in _layer_sel))
        ]

        if not _filtered:
            st.warning("No files match the current filters.")
        else:
            # ── Summary metrics ───────────────────────────────────────────────
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("Total files", len(_filtered))
            _m2.metric("CSV", sum(1 for p in _filtered if p.suffix == ".csv"))
            _m3.metric("Excel", sum(1 for p in _filtered if p.suffix == ".xlsx"))

            st.divider()

            # ── File picker (relative paths for readability) ──────────────────
            _rel_labels = {str(p.relative_to(_OUT_ROOT)): p for p in _filtered}
            _selected_label = st.selectbox(
                "Select file",
                list(_rel_labels.keys()),
                key="of_picker",
                help="Files sorted newest first",
            )
            _selected_path = _rel_labels[_selected_label]

            # ── File info bar ─────────────────────────────────────────────────
            import datetime as _dt
            _mtime = _dt.datetime.fromtimestamp(_selected_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            _fsize = _selected_path.stat().st_size
            _size_str = f"{_fsize / 1024:.1f} KB" if _fsize < 1_048_576 else f"{_fsize / 1_048_576:.1f} MB"
            _ic1, _ic2, _ic3 = st.columns(3)
            _ic1.caption(f"**Modified:** {_mtime}")
            _ic2.caption(f"**Size:** {_size_str}")
            _ic3.caption(f"**Type:** {_selected_path.suffix.upper()[1:]}")

            # ── Load & render ─────────────────────────────────────────────────
            try:
                import pandas as pd
                if _selected_path.suffix == ".csv":
                    _df = pd.read_csv(_selected_path)
                else:
                    _df = pd.read_excel(_selected_path)

                # Route: diff-style CSVs (have __source/__target cols) use rich renderer;
                # summary CSVs use paginated styled table.
                _is_diff = any(c.endswith("__source") for c in _df.columns)

                if _is_diff:
                    _render_diff_file(_selected_path, key_prefix=f"of_{_selected_label}")
                else:
                    _tab_view, _tab_raw = st.tabs(["📊 Styled view", "🗃 Raw data"])
                    with _tab_view:
                        render_paginated_df(_df, key_prefix=f"of_pg_{_selected_label}")
                    with _tab_raw:
                        st.dataframe(_df, use_container_width=True, hide_index=True)

                # ── Download button ───────────────────────────────────────────
                with open(_selected_path, "rb") as _fh:
                    st.download_button(
                        label=f"⬇️ Download {_selected_path.name}",
                        data=_fh.read(),
                        file_name=_selected_path.name,
                        mime="text/csv" if _selected_path.suffix == ".csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="of_dl",
                    )
            except Exception as _e:
                st.error(f"Could not load file: {_e}")

