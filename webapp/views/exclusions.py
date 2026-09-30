"""🚫 Exclusions tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# TAB: Exclusions
# =============================================================================
def render():
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

