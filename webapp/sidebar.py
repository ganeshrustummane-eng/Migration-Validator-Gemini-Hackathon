"""Sidebar: environment status and the scheduled-runs panel (ADR 0050).
Moved verbatim from webapp/app.py."""
from ui_common import *  # noqa: F401,F403

# ---------------------------------------------------------------------------
# Sidebar — environment status, always visible regardless of active tab
# ---------------------------------------------------------------------------

def render():
    st.markdown("### Migration Validator")

    _dial_key = os.getenv("DIAL_API_KEY", "")
    _claude_key = os.getenv("CLAUDE_API_KEY", "")
    if _dial_key:
        st.success(f"AI backend: DIAL ({os.getenv('DIAL_MODEL', 'gpt-4o')})", icon="🤖")
    elif _claude_key:
        st.success(f"AI backend: Claude ({os.getenv('CLAUDE_MODEL', 'claude-3-5-sonnet-20241022')})", icon="🤖")
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
    _sb_recs = _load_token_records()
    _sb_pricing = _load_pricing()
    if _sb_recs:
        _sb_tokens = sum(r.get("total_tokens", 0) for r in _sb_recs)
        _sb_cost = sum(
            _cost_for(r.get("model", ""), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), _sb_pricing)
            for r in _sb_recs
        )
        st.markdown(
            f"**AI usage (all-time):** {len(_sb_recs):,} calls · "
            f"{_sb_tokens:,} tokens · **${_sb_cost:.4f}**"
        )
    else:
        st.caption("No AI calls logged yet.")


def render_scheduler():
    # =============================================================================
    # TAB: Usage & Cost
    # =============================================================================
    _SCHED_KEY = "sched_job_id"
    _is_sched_running = _SCHED_KEY in st.session_state
    _sched_label = "⏰ Scheduled Runs  🟢 Active" if _is_sched_running else "⏰ Scheduled Runs"

    with st.sidebar.expander(_sched_label, expanded=False):
        if _is_sched_running:
            _sched_info = st.session_state.get("sched_meta", {})
            st.markdown(
                f"**Status:** 🟢 Running\n\n"
                f"**Layer:** `{_sched_info.get('layer','—')}`  "
                f"**Env:** `{_sched_info.get('env','—')}`  \n"
                f"**Interval:** {_sched_info.get('interval','—')}"
            )
            if st.button("⏹ Stop scheduler", key="sched_stop", type="primary", use_container_width=True):
                try:
                    st.session_state[_SCHED_KEY].shutdown(wait=False)
                except Exception:
                    pass
                for _k in (_SCHED_KEY, "sched_meta"):
                    st.session_state.pop(_k, None)
                flash("Scheduler stopped.", icon="⏹")
                st.rerun()
        else:
            st.markdown("**Auto-run validation on a fixed schedule.**  \n*App must stay open.*")
            st.divider()
            _sched_layer = st.selectbox("Layer", _LAYERS, key="sched_layer")
            _sched_env = st.selectbox("Environment", list(ENVIRONMENTS), key="sched_env")
            _sched_interval = st.selectbox(
                "Interval",
                ["Every 1 hour", "Every 6 hours", "Every 12 hours", "Daily (midnight)"],
                key="sched_interval",
            )
            _sched_notify = st.checkbox("🔔 Notify on failure (Slack/email)", value=True, key="sched_notify")

            _interval_map = {
                "Every 1 hour": 3600, "Every 6 hours": 21600,
                "Every 12 hours": 43200, "Daily (midnight)": 86400,
            }

            if st.button("▶ Start scheduler", key="sched_start", type="primary", use_container_width=True):
                try:
                    from apscheduler.schedulers.background import BackgroundScheduler
                    _scheduler = BackgroundScheduler()
                    _secs = _interval_map[_sched_interval]
                    _snap_layer, _snap_env, _snap_notify = _sched_layer, _sched_env, _sched_notify

                    def _scheduled_run():
                        try:
                            _r = run_validation(_snap_layer, _snap_env, ["all"], True, True)
                            if _snap_notify and _r.get("returncode", 0) != 0:
                                from notifier import notify_failure
                                notify_failure(
                                    subject=f"[Scheduled] Validation failure — {_snap_layer}",
                                    body=f"Run ID: {_r.get('run_id','?')}\n{_r.get('stdout_tail','')[-500:]}",
                                )
                        except Exception:
                            pass

                    _scheduler.add_job(_scheduled_run, "interval", seconds=_secs)
                    _scheduler.start()
                    st.session_state[_SCHED_KEY] = _scheduler
                    st.session_state["sched_meta"] = {
                        "layer": _sched_layer, "env": _sched_env, "interval": _sched_interval,
                    }
                    flash(f"Scheduler started — {_sched_interval.lower()}.", icon="⏰")
                    st.rerun()
                except ImportError:
                    st.error("Run `pip install apscheduler` to enable scheduled runs.")
