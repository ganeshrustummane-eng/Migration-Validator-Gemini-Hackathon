"""📈 History & Trends tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# TAB: History & Trends — SQLite-backed validation history (results_store.py),
# populated automatically by every run in the Run Validation tab. Replaces
# manually grepping through Project/output/<layer>/validation_<run_id>/ CSVs.
# =============================================================================
def render():
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
            st.download_button(
                "⬇ Download filtered results CSV",
                data=results_df.to_csv(index=False).encode("utf-8"),
                file_name=f"validation_history_{status_filter.lower()}.csv",
                mime="text/csv",
                key="dl_hist_results",
            )

