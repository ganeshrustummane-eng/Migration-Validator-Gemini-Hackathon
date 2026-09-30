"""🚀 Run Validation tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# TAB: Run Validation — execute the generated YAMLs (Project/main.py) and
# show pass/fail results, without ever displaying raw row values.
# =============================================================================
def render():
    st.subheader("Run validation — pick the YAML file(s) to execute")
    st.caption(
        "Runs `Project/main.py` against the YAML configs generated in the tabs above, "
        "then reads back the run's summary — counts and pass/fail status only."
    )

    import pandas as pd

    _exec_top1, _exec_top2 = st.columns(2)
    with _exec_top1:
        layer = st.selectbox("Medallion layer", _LAYERS, index=0, key="exec_layer")
    with _exec_top2:
        environment = st.selectbox("Environment", list(ENVIRONMENTS), key="exec_env")

    # ── Execution mode: historical (default, unchanged) vs incremental ──────
    # Incremental only narrows the row set via a per-table filter_column that
    # must already be configured in that table's YAML
    # (validation_plan.incremental.filter_column) -- never typed by hand here
    # and never defaulted to a guessed column name. See
    # docs/decisions/0030-progressive-decision-report-pack-incremental-sanity-streamlit.md
    # Decision 1.
    exec_mode = st.radio("Execution mode", ["Historical", "Incremental"], horizontal=True, key="exec_mode")
    incremental_from_date = incremental_to_date = None
    if exec_mode == "Incremental":
        # ponytail: date-only precision (no time-of-day component), matching
        # the original fork's granularity. Ceiling: a table whose real
        # boundary needs intra-day precision can't express it here. Upgrade
        # to a datetime input if that's ever needed -- see docs/decisions/
        # 0031-implementation-choices-for-adr-0030-open-questions.md Q3.
        _inc_c1, _inc_c2 = st.columns(2)
        with _inc_c1:
            incremental_from_date = st.date_input("From date", key="exec_incremental_from")
        with _inc_c2:
            incremental_to_date = st.date_input("To date", key="exec_incremental_to")
        if incremental_from_date and incremental_to_date and incremental_from_date > incremental_to_date:
            st.error("From date is after To date.")
        st.caption("The date range applies to data validation only. Count validation still runs full-table in Incremental mode.")

    # ── Build full YAML inventory (all layers + report/) ─────────────────────
    # Each row: {stem, vtype, folder, layer, path}
    # Scans every layer so the folder-filter multiselect below does the narrowing.
    def _build_exec_inventory():
        import yaml as _yi
        rows = []
        for _ly in _LAYERS:
            # Count validation — one YAML per source DB (mssql.yaml, postgres.yaml, …)
            # Fall back to legacy {layer}.yaml for older runs
            _cv_dir = _PROJECT_DIR / "config" / _ly / "count_validation"
            _cv_yamls = sorted(_cv_dir.glob("*.yaml")) if _cv_dir.exists() else []
            for _cv_path in _cv_yamls:
                _source_db = _cv_path.stem  # filename IS the source db name
                _cfg = _yi.safe_load(_cv_path.read_text(encoding="utf-8")) or {}
                for tbl, tbl_block in (_cfg.get("tables") or {}).items():
                    # Also read source field from YAML for legacy files where stem != db type
                    _src = ((tbl_block.get("validations") or {})
                            .get("count_validation", {})
                            .get("source") or _source_db)
                    rows.append({"stem": tbl, "vtype": "count_validation",
                                 "folder": _ly, "layer": _ly, "path": _cv_path,
                                 "source_db": _src.lower()})
            # Data validation — subdir per source DB: data_validation/{source_db}/{table}.yaml
            # Also handles legacy flat layout: data_validation/{table}.yaml
            _dv_dir = _PROJECT_DIR / "config" / _ly / "data_validation"
            if _dv_dir.exists():
                for p in sorted(_dv_dir.rglob("*.yaml")):
                    # subdir layout: p.parent.name = source_db, p.parent.parent.name = "data_validation"
                    # flat layout: p.parent.name = "data_validation"
                    if p.parent.name == "data_validation":
                        _src_db = "unknown"
                    else:
                        _src_db = p.parent.name  # e.g. "mssql", "postgres"
                    rows.append({"stem": p.stem, "vtype": "data_validation",
                                 "folder": _ly, "layer": _ly, "path": p,
                                 "source_db": _src_db.lower()})
        # Report/ data_validation — independent of layer
        _rep_root = _PROJECT_DIR / "config" / "report"
        if _rep_root.exists():
            for p in sorted(_rep_root.rglob("*.yaml")):
                if p.parent.name == "data_validation" or p.parent.parent.name == "data_validation":
                    subfolder = p.parent.parent.name if p.parent.parent.name != "data_validation" else p.parent.parent.parent.name
                    _src_db = p.parent.name if p.parent.parent.name == "data_validation" else "unknown"
                    rows.append({"stem": p.stem, "vtype": "data_validation",
                                 "folder": f"report/{subfolder}", "layer": "bronze", "path": p,
                                 "source_db": _src_db.lower()})
        return rows

    _inventory = _build_exec_inventory()

    if not _inventory:
        st.warning(
            f"No YAML configs found for layer '{layer}' yet — generate one first "
            f"in the **📋 Generate YAMLs** tab."
        )
    else:
        # ── Report pack — discovered from config/report/'s actual subdirectories,
        # never a hardcoded list (there is no fixed enum of pack names anywhere in
        # this codebase; see docs/decisions/0030-progressive-decision-report-pack-
        # incremental-sanity-streamlit.md Decision 3 / Discrepancy #2). Picking one
        # pre-scopes the inventory below to that pack's YAMLs; execution itself is
        # unchanged — report-pack YAMLs already run through the same engine as
        # every other row in this table.
        _report_pack_dir = _PROJECT_DIR / "config" / "report"
        _report_packs = sorted(p.name for p in _report_pack_dir.iterdir() if p.is_dir()) if _report_pack_dir.exists() else []
        _pack_choice = "All"
        if _report_packs:
            _pack_choice = st.selectbox("Report pack", ["All"] + _report_packs, key="exec_report_pack")
            if _pack_choice != "All":
                _inventory = [r for r in _inventory if r["folder"] == f"report/{_pack_choice}"]

        # ── Filters row ──────────────────────────────────────────────────────
        _fc1, _fc2, _fc3, _fc4 = st.columns([2, 2, 2, 3])
        with _fc1:
            _all_folders = sorted({r["folder"] for r in _inventory})
            _sel_folders = st.multiselect(
                "Filter by folder", options=_all_folders,
                default=_all_folders, key="exec_folder_filter",
                help="'bronze/silver/gold' = layer configs. 'report/X' = custom report configs.",
            )
        with _fc2:
            _vtype_opts = sorted({r["vtype"] for r in _inventory})
            _sel_vtypes = st.multiselect(
                "Validation type", options=_vtype_opts,
                default=_vtype_opts, key="exec_vtype_filter",
            )
        with _fc3:
            _all_src_dbs = sorted({r["source_db"] for r in _inventory})
            _sel_src_dbs = st.multiselect(
                "Source DB", options=_all_src_dbs,
                default=_all_src_dbs, key="exec_source_db_filter",
                help="Filter by source database type (mssql, postgres, athena, redshift, …).",
            )
        with _fc4:
            _search = st.text_input(
                "Search tables", placeholder="Type to filter…", key="exec_search",
            )

        _filtered = [
            r for r in _inventory
            if r["folder"] in (_sel_folders or _all_folders)
            and r["vtype"] in (_sel_vtypes or _vtype_opts)
            and r["source_db"] in (_sel_src_dbs or _all_src_dbs)
            and (_search.strip().lower() in r["stem"].lower() if _search.strip() else True)
        ]

        select_all = st.checkbox("Select all", value=True, key="exec_select_all")
        _sel_set_key = f"exec_sel_{layer}_{select_all}"

        file_rows = [
            {"Run": select_all, "Table": r["stem"], "Source DB": r["source_db"],
             "Type": r["vtype"], "Folder": r["folder"],
             "File": str(r["path"].relative_to(_PROJECT_DIR))}
            for r in _filtered
        ]
        files_df = pd.DataFrame(file_rows) if file_rows else pd.DataFrame(
            columns=["Run", "Table", "Type", "Folder", "File"])

        st.caption(f"{len(files_df)} of {len(_inventory)} config(s) shown — check the ones to run.")
        edited = st.data_editor(
            files_df,
            column_config={
                "Run":    st.column_config.CheckboxColumn(help="Include in run"),
                "Table":     st.column_config.TextColumn(disabled=True),
                "Source DB": st.column_config.TextColumn(disabled=True),
                "Type":      st.column_config.TextColumn(disabled=True),
                "Folder":    st.column_config.TextColumn(disabled=True),
                "File":      st.column_config.TextColumn(disabled=True),
            },
            hide_index=True, width="stretch", key=f"exec_file_grid_{_sel_set_key}",
        )

        picked_count_tables = edited.loc[
            (edited["Type"] == "count_validation") & edited["Run"], "Table"
        ].tolist()
        picked_data_tables = edited.loc[
            (edited["Type"] == "data_validation") & edited["Run"], "Table"
        ].tolist()
        do_count = bool(picked_count_tables)
        do_data = bool(picked_data_tables)
        selected_tables = sorted(set(picked_count_tables) | set(picked_data_tables))

        if selected_tables and set(picked_count_tables) != set(picked_data_tables) and do_count and do_data:
            st.info(
                "Count and data validation have different table selections — a table checked for only one "
                "type will be silently skipped for the other (no matching YAML requested for it)."
            )

        # ── Incremental readiness check — resolve each picked data_validation
        # table's configured filter_column (never typed by hand), and drop
        # any table with no validation_plan.incremental.enabled from this run
        # rather than silently running it historical. See ADR 0030 Decision 1.
        #
        # CORRECTNESS NOTE (found in the ADR 0031 audit, docs/decisions/0032):
        # removing a table from picked_data_tables does NOT stop main.py from
        # still validating it if the same table is also in picked_count_tables
        # -- Project/main.py's --tables argument is one flat list shared by
        # both count_validation and data_validation (Project/main.py:173-178),
        # so a table selected for count_validation is still passed through and
        # will be matched against the data_validation YAML too, running it
        # historically and unfiltered. The block below over-selects into this
        # overlap and hard-blocks the run instead of silently proceeding.
        incremental_filter_columns = {}
        incremental_missing_tables = []
        incremental_leak_tables = []
        if exec_mode == "Incremental" and picked_data_tables:
            import yaml as _yinc
            _all_data_yamls = {r["stem"]: r["path"] for r in _inventory if r["vtype"] == "data_validation"}
            for _tbl in picked_data_tables:
                _yp = _all_data_yamls.get(_tbl)
                _filter_col = None
                if _yp and _yp.exists():
                    try:
                        _ydoc = _yinc.safe_load(_yp.read_text(encoding="utf-8")) or {}
                        for _tentry in (_ydoc.get("tables") or {}).values():
                            _inc = ((_tentry.get("validations") or {}).get("validation_plan") or {}).get("incremental") or {}
                            if _inc.get("enabled") and _inc.get("filter_column"):
                                _filter_col = _inc["filter_column"]
                    except Exception:
                        pass
                if _filter_col:
                    incremental_filter_columns[_tbl] = _filter_col
                else:
                    incremental_missing_tables.append(_tbl)

            if incremental_filter_columns:
                st.caption("Incremental filter column per table: " + ", ".join(
                    f"**{t}** → `{c}`" for t, c in incremental_filter_columns.items()))
            if incremental_missing_tables:
                picked_data_tables = [t for t in picked_data_tables if t not in incremental_missing_tables]
                do_data = bool(picked_data_tables)
                selected_tables = sorted(set(picked_count_tables) | set(picked_data_tables))

                incremental_leak_tables = sorted(set(incremental_missing_tables) & set(picked_count_tables))
                if incremental_leak_tables:
                    st.error(
                        "Cannot run: " + ", ".join(incremental_leak_tables) + " have no "
                        "`validation_plan.incremental.filter_column` configured AND are also checked for "
                        "count validation. Project/main.py's --tables argument is shared by both validation "
                        "types, so they would still run data_validation historically and unfiltered despite "
                        "being excluded here. Uncheck them from count validation too, or configure their "
                        "incremental filter column, before running."
                    )
                else:
                    st.warning(
                        "Incremental execution unavailable for: " + ", ".join(incremental_missing_tables) +
                        " — no `validation_plan.incremental.filter_column` configured in the table's YAML. "
                        "These tables are excluded from this run."
                    )

        _thresh_col, _count_thresh_col = st.columns([2, 2])
        with _thresh_col:
            mismatch_threshold = st.number_input(
                "Acceptable mismatch % (0 = exact match required)",
                min_value=0.0, max_value=100.0, value=0.0, step=0.1,
                format="%.2f", key="exec_threshold",
                help="e.g. 0.10 means ≤0.10% mismatched rows = PASS. Written into the YAML before running.",
            )
        with _count_thresh_col:
            count_mismatch_threshold = st.number_input(
                "Acceptable count difference % (0 = exact match required)",
                min_value=0.0, max_value=100.0, value=0.0, step=0.1,
                format="%.2f", key="exec_count_threshold",
                help="For tables under active CDC/replication, a strict row-count match will "
                     "intermittently FAIL for no real reason. e.g. 0.05 means ≤0.05% count "
                     "difference = PASS. Written into the YAML before running.",
            )

        # Derive run layer from the actual inventory rows selected (first match wins)
        _picked_stems = set(selected_tables)
        _run_layer = next(
            (r["layer"] for r in _filtered if r["stem"] in _picked_stems),
            layer,
        )

        @st.fragment
        def _run_validation_panel(selected_tables, do_count, do_data, _run_layer, environment,
                                    mismatch_threshold, count_mismatch_threshold,
                                    picked_data_tables, picked_count_tables, _inventory,
                                    exec_mode, incremental_from_date, incremental_to_date,
                                    incremental_filter_columns, pack_choice, incremental_leak_tables):
            """Isolated as a fragment so the 1s poll loop below only re-runs this
            panel, not the whole multi-thousand-line app.py script -- see
            docs/decisions/0005-run-validation-slow-full-page-rerun-polling.md.
            Without this, every poll tick re-executes every OTHER tab's setup
            code too, turning an 11-second subprocess into a multi-minute wait.
            """
            _exec_proc_key = "exec_running_proc"
            _exec_meta_key = "exec_running_meta"
            result = None
            _incremental_invalid = (
                exec_mode == "Incremental"
                and (not incremental_from_date or not incremental_to_date or incremental_from_date > incremental_to_date)
            ) or bool(incremental_leak_tables)

            if st.session_state.get(_exec_proc_key) is not None:
                _proc = st.session_state[_exec_proc_key]
                _meta = st.session_state[_exec_meta_key]
                st.info(f"⏳ Running {_meta['layer']} validation against '{_meta['environment']}' — this executes real queries...")
                if st.button("⏹ Stop validation", key="exec_stop", type="secondary"):
                    terminate_validation(_proc)
                    result = collect_validation_result(_proc, _meta["layer"], _meta["environment"], cancelled=True)
                    st.session_state[_exec_proc_key] = None
                    st.session_state[_exec_meta_key] = None
                    st.warning("Validation stopped — the connector's `finally: conn.close()` still runs, so source/Snowflake "
                               "connections are released, but tables that hadn't finished are not included below.")
                elif _proc.poll() is None:
                    time.sleep(1)
                    st.rerun()
                else:
                    result = collect_validation_result(_proc, _meta["layer"], _meta["environment"])
                    st.session_state[_exec_proc_key] = None
                    st.session_state[_exec_meta_key] = None

            elif st.button("🚀 Run validation", type="primary", key="exec_run",
                            disabled=not selected_tables or _incremental_invalid):
                # Inject mismatch_threshold_pct into data_validation YAMLs before running
                if mismatch_threshold > 0:
                    import yaml as _yrun
                    # Build stem→path map from inventory directly (covers all layers + report)
                    _all_data_yamls = {r["stem"]: r["path"] for r in _inventory if r["vtype"] == "data_validation"}
                    for _tbl in picked_data_tables:
                        _yp = _all_data_yamls.get(_tbl)
                        if _yp.exists():
                            try:
                                _ydoc = _yrun.safe_load(_yp.read_text(encoding="utf-8")) or {}
                                for _tentry in (_ydoc.get("tables") or {}).values():
                                    _dv = (_tentry.get("validations") or {}).get("data_validation")
                                    if _dv:
                                        _dv["mismatch_threshold_pct"] = mismatch_threshold
                                _yp.write_text(_yrun.dump(_ydoc, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
                            except Exception:
                                pass

                # Inject count_mismatch_threshold_pct into the layer's single count_validation
                # YAML before running — count_validation has no tolerance by default (strict ==),
                # so this is opt-in per run, same pattern as the data_validation threshold above.
                if count_mismatch_threshold > 0 and picked_count_tables:
                    import yaml as _yrun
                    _cv_yaml = _PROJECT_DIR / "config" / layer / "count_validation" / f"{layer}.yaml"
                    if _cv_yaml.exists():
                        try:
                            _cvdoc = _yrun.safe_load(_cv_yaml.read_text(encoding="utf-8")) or {}
                            for _tbl in picked_count_tables:
                                _tentry = (_cvdoc.get("tables") or {}).get(_tbl)
                                _cv = (_tentry.get("validations") or {}).get("count_validation") if _tentry else None
                                if _cv:
                                    _cv["count_mismatch_threshold_pct"] = count_mismatch_threshold
                            _cv_yaml.write_text(_yrun.dump(_cvdoc, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
                        except Exception:
                            pass

                # The picked date range goes to this one run only -- never written
                # into the YAML (docs/decisions/0034-explicit-incremental-execution-
                # mode-contract.md). Historical passes nothing, so it always runs
                # full-table.
                _inc_range = ((str(incremental_from_date), str(incremental_to_date))
                              if exec_mode == "Incremental" else None)
                try:
                    _new_proc = start_validation(_run_layer, environment, selected_tables, do_count, do_data,
                                                 incremental_range=_inc_range)
                except Exception as exc:
                    st.error(f"Execution failed to start: {exc}")
                else:
                    st.session_state[_exec_proc_key] = _new_proc
                    st.session_state[_exec_meta_key] = {"layer": _run_layer, "environment": environment}
                    st.rerun()

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

                # Connector timeouts (large Athena/Postgres/MSSQL/Snowflake tables
                # hitting the connect/statement timeout ceilings) surface as ERROR
                # rows with no reason column in the summary CSV — the actual message
                # only lives in the subprocess log tails. Call it out as a distinct
                # toast so "connection/query timed out" isn't mistaken for a generic
                # data mismatch on tables in the 100M+ row range.
                n_error_rows = sum(int((df["status"] == "ERROR").sum()) for df in result["summaries"].values())
                if n_error_rows:
                    _log_tail = (result.get("stdout_tail", "") + result.get("stderr_tail", "")).lower()
                    _timeout_hit = any(s in _log_tail for s in (
                        "timeouterror", "did not finish within", "timeout expired", "query failed",
                    ))
                    if _timeout_hit:
                        st.toast(
                            f"⏱️ {n_error_rows} table(s) errored — looks like a connection/query timeout "
                            f"(large table?). Check the log below or raise the connector's timeout constant.",
                            icon="⏱️",
                        )
                    else:
                        st.toast(f"⚠️ {n_error_rows} table(s) errored — see log below.", icon="⚠️")

                if pack_choice != "All":
                    _pack_total = sum(len(df) for df in result["summaries"].values())
                    _pack_passed = sum(int((df["status"] == "PASS").sum()) for df in result["summaries"].values())
                    st.info(f"📦 Report pack **{pack_choice}**: {_pack_passed}/{_pack_total} passed")

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
                        show_failed_only = st.checkbox(
                            "Show failed only", value=False, key=f"exec_summary_failed_only_{vtype}"
                        )
                        display_df = df[df["status"] == "FAIL"] if show_failed_only else df
                        render_paginated_df(display_df, key_prefix=f"exec_summary_{vtype}")
                        st.download_button(
                            f"⬇ Download {'failed-only ' if show_failed_only else ''}{vtype} summary CSV",
                            data=display_df.to_csv(index=False).encode("utf-8"),
                            file_name=f"{vtype}_summary{'_failed' if show_failed_only else ''}.csv",
                            mime="text/csv",
                            key=f"dl_summary_{vtype}",
                        )

                if result["diff_files"]:
                    with st.container(border=True):
                        st.markdown("#### 🔍 Data validation — row-level results")
                        st.caption("Every row is shown — green = matched, red = differed. Local files only, never sent to any AI/LLM.")
                        for f in result["diff_files"]:
                            _render_diff_file(f, key_prefix=f"exec_diff_{f.stem}")

                if result.get("failed_files"):
                    with st.container(border=True):
                        st.markdown("#### ❌ Data validation — failed rows only")
                        st.caption("Same rows as above, pre-filtered to mismatches. Download below to share just the failures.")
                        for f in result["failed_files"]:
                            _render_diff_file(f, key_prefix=f"exec_failed_{f.stem}")
                            st.download_button(
                                f"⬇ Download {f.name}",
                                data=f.read_bytes(),
                                file_name=f.name,
                                mime="text/csv",
                                key=f"dl_failed_{f.stem}",
                            )

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

        _run_validation_panel(selected_tables, do_count, do_data, _run_layer, environment,
                               mismatch_threshold, count_mismatch_threshold,
                               picked_data_tables, picked_count_tables, _inventory,
                               exec_mode, incremental_from_date, incremental_to_date,
                               incremental_filter_columns, _pack_choice, incremental_leak_tables)

