"""💰 Usage & Cost tab (moved verbatim from webapp/app.py, ADR 0050)."""
from ui_common import *  # noqa: F401,F403 -- shared helpers, clients, constants

# =============================================================================
# =============================================================================
# TAB: Usage & Cost
# =============================================================================
def render():
    import datetime as _dt
    from collections import defaultdict as _dd

    st.subheader("💰 AI Usage & Cost")
    st.caption(
        "Real token counts from every AI call (column mapping + SQL generation), "
        "logged to token_usage_analysis/logs/token_usage.jsonl. "
        "Cost estimated from public list prices — see token_usage_analysis/pricing.json."
    )

    _u_records = _load_token_records()
    _u_pricing = _load_pricing()

    if not _u_records:
        st.info("No AI calls logged yet. Run a Single YAML or Batch YAML generation with an AI key configured.")
    else:
        def _u_record_date(r: dict):
            try:
                return _dt.datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S").date()
            except Exception:
                return None

        _u_today = _dt.date.today()
        _u_last7 = _u_today - _dt.timedelta(days=6)
        _u_today_recs = [r for r in _u_records if _u_record_date(r) == _u_today]
        _u_week_recs  = [r for r in _u_records if (d := _u_record_date(r)) and _u_last7 <= d <= _u_today]

        def _u_totals(recs):
            tokens = sum(r.get("total_tokens", 0) for r in recs)
            cost   = sum(_cost_for(r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), _u_pricing) for r in recs)
            return tokens, cost

        _u_today_tok, _u_today_cost = _u_totals(_u_today_recs)
        _u_week_tok,  _u_week_cost  = _u_totals(_u_week_recs)
        _u_all_tok,   _u_all_cost   = _u_totals(_u_records)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Today — cost",         f"${_u_today_cost:.4f}")
        c2.metric("Today — tokens",       f"{_u_today_tok:,}")
        c3.metric("Last 7 days — cost",   f"${_u_week_cost:.4f}")
        c4.metric("Last 7 days — tokens", f"{_u_week_tok:,}")
        c5.metric("Last 7 days — calls",  f"{len(_u_week_recs):,}")

        st.divider()
        st.markdown("**Daily cost — last 7 days**")
        _u_daily = _dd(float)
        for _d_off in range(6, -1, -1):
            _u_daily[(_u_today - _dt.timedelta(days=_d_off)).isoformat()] = 0.0
        for r in _u_week_recs:
            d = _u_record_date(r)
            if d:
                _u_daily[d.isoformat()] += _cost_for(
                    r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), _u_pricing
                )
        import pandas as _pd_usage
        _u_chart = _pd_usage.DataFrame({"Date": list(_u_daily.keys()), "Cost (USD)": list(_u_daily.values())}).set_index("Date")
        st.bar_chart(_u_chart)

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Last 7 days — by model**")
            _u_by_model = _dd(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
            for r in _u_week_recs:
                m = r.get("model", "unknown")
                _u_by_model[m]["calls"]  += 1
                _u_by_model[m]["tokens"] += r.get("total_tokens", 0)
                _u_by_model[m]["cost"]   += _cost_for(m, r.get("prompt_tokens", 0), r.get("completion_tokens", 0), _u_pricing)
            st.dataframe(
                [{"Model": m, "Calls": s["calls"], "Tokens": s["tokens"], "Cost (USD)": round(s["cost"], 4)}
                 for m, s in sorted(_u_by_model.items())],
                use_container_width=True, hide_index=True,
            )
        with col_b:
            st.markdown("**Last 7 days — by call type**")
            _u_by_type = _dd(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
            for r in _u_week_recs:
                t = r.get("call_type", "unknown")
                _u_by_type[t]["calls"]  += 1
                _u_by_type[t]["tokens"] += r.get("total_tokens", 0)
                _u_by_type[t]["cost"]   += _cost_for(r.get("model", "unknown"), r.get("prompt_tokens", 0), r.get("completion_tokens", 0), _u_pricing)
            st.dataframe(
                [{"Call type": t, "Calls": s["calls"], "Tokens": s["tokens"], "Cost (USD)": round(s["cost"], 4)}
                 for t, s in sorted(_u_by_type.items())],
                use_container_width=True, hide_index=True,
            )

        st.divider()
        st.caption(f"All-time: {len(_u_records):,} AI calls · {_u_all_tok:,} tokens · **${_u_all_cost:.4f}** estimated cost.")

