"""🎫 My Jira Tickets tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# Usage & Cost moved to tab_usage — see below


# TAB: My Jira Tickets
# =============================================================================
def render():
    st.subheader("My Jira Tickets")
    st.caption("Tickets assigned to you in the configured Jira project — update status or attach a validation result.")

    try:
        from connector.jira_client import (
            is_configured, get_my_tickets, get_ticket,
            transition_ticket, add_comment, JiraError, JiraNotConfiguredError,
        )
    except ImportError:
        get_my_tickets = None

    if get_my_tickets is None or not is_configured():
        st.info("Jira not configured — add `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` to `.env`.")
    else:
        _jql_filter = st.text_input(
            "Extra JQL filter (optional)",
            placeholder='e.g. priority = High AND labels = "data-migration"',
            key="jira_jql_extra",
        )
        _jira_refresh = st.button("🔄 Refresh tickets", key="jira_refresh")

        _JIRA_TICKETS_KEY = "jira_my_tickets"
        if _jira_refresh or _JIRA_TICKETS_KEY not in st.session_state:
            with st.spinner("Fetching your Jira tickets…"):
                try:
                    st.session_state[_JIRA_TICKETS_KEY] = get_my_tickets(_jql_filter.strip())
                except JiraError as _je:
                    st.error(f"Jira error: {_je}")
                    st.session_state[_JIRA_TICKETS_KEY] = []

        _tickets = st.session_state.get(_JIRA_TICKETS_KEY, [])
        if not _tickets:
            st.info("No open tickets assigned to you.")
        else:
            st.caption(f"{len(_tickets)} open ticket(s)")

            _STATUS_COLORS = {
                "To Do": "#94A3B8", "In Progress": "#F59E0B",
                "In Review": "#6366F1", "Done": "#10B981",
            }
            _PRIORITY_ICONS = {
                "Highest": "🔴", "High": "🟠", "Medium": "🟡",
                "Low": "🔵", "Lowest": "⚪",
            }

            for _tk in _tickets:
                _status_color = _STATUS_COLORS.get(_tk["status"], "#94A3B8")
                _priority_icon = _PRIORITY_ICONS.get(_tk["priority"], "")
                with st.expander(
                    f"{_priority_icon} **{_tk['key']}** — {_tk['summary']}",
                    expanded=False,
                ):
                    _tc1, _tc2 = st.columns([3, 1])
                    with _tc1:
                        st.markdown(
                            f"<span style='background:{_status_color};color:#fff;"
                            f"padding:2px 10px;border-radius:12px;font-size:0.78rem;'>"
                            f"{_tk['status']}</span> &nbsp; "
                            f"<a href='{_tk['url']}' target='_blank' style='font-size:0.82rem;'>"
                            f"Open in Jira ↗</a>",
                            unsafe_allow_html=True,
                        )

                    # ── Transition buttons ────────────────────────────────────
                    with _tc2:
                        _trans_target = (
                            "In Progress" if _tk["status"] == "To Do"
                            else "Done" if _tk["status"] == "In Progress"
                            else None
                        )
                        if _trans_target:
                            if st.button(
                                f"→ {_trans_target}",
                                key=f"jira_trans_{_tk['key']}",
                                type="primary",
                            ):
                                try:
                                    transition_ticket(_tk["key"], _trans_target)
                                    st.session_state.pop(_JIRA_TICKETS_KEY, None)
                                    flash(f"{_tk['key']} moved to '{_trans_target}'.", icon="✅")
                                    st.rerun()
                                except JiraError as _je:
                                    st.error(str(_je))

                    # ── Attach validation result as comment ───────────────────
                    with st.expander("📎 Attach validation result to this ticket", expanded=False):
                        _yaml_files = sorted(
                            (p for p in (_PROJECT_DIR / "config").rglob("*.yaml")
                             if p.parent.name in ("data_validation", "count_validation")),
                            key=lambda p: p.stat().st_mtime, reverse=True,
                        )
                        if not _yaml_files:
                            st.caption("No validation YAMLs found yet.")
                        else:
                            _sel_yaml = st.selectbox(
                                "Validation YAML",
                                options=_yaml_files,
                                format_func=lambda p: str(p),
                                key=f"jira_yaml_{_tk['key']}",
                            )
                            _note = st.text_area(
                                "Notes (optional)",
                                key=f"jira_note_{_tk['key']}",
                                height=68,
                            )
                            if st.button("📤 Post comment", key=f"jira_comment_{_tk['key']}"):
                                _comment = (
                                    f"[Migration Validator] Validation result attached: {_sel_yaml}\n"
                                    + (f"\n{_note}" if _note.strip() else "")
                                )
                                try:
                                    add_comment(_tk["key"], _comment)
                                    flash(f"Comment posted on {_tk['key']}.", icon="✅")
                                except JiraError as _je:
                                    st.error(str(_je))

