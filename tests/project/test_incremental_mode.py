"""ADR 0034: the run's mode comes from the date range the Streamlit UI hands
to runner.start_validation() (passed to the main.py child process only),
never from dates left in the YAML.

Run:  python -m pytest tests/project/test_incremental_mode.py -q
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Project"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import runner  # noqa: E402
from utils.utility import (INCREMENTAL_FROM_ENV, INCREMENTAL_TO_ENV,  # noqa: E402
                           check_hybrid_incremental_conflict, incremental_filter_column,
                           read_incremental_range, should_dispatch_hybrid)

CAPABLE_PLAN = {"execution_strategy": "hybrid_v1",
                "incremental": {"enabled": True, "filter_column": "UPDATED_AT",
                                "from_date": "2020-01-01", "to_date": "2020-01-31"}}  # stale leftovers


# ── startup mode resolution ──────────────────────────────────────────────────
def test_neither_var_is_historical():
    assert read_incremental_range({}) is None


def test_both_vars_is_incremental():
    env = {INCREMENTAL_FROM_ENV: "2026-01-01", INCREMENTAL_TO_ENV: "2026-01-31"}
    assert read_incremental_range(env) == ("2026-01-01", "2026-01-31")


@pytest.mark.parametrize("env", [{INCREMENTAL_FROM_ENV: "2026-01-01"}, {INCREMENTAL_TO_ENV: "2026-01-31"}])
def test_only_one_var_fails(env):
    with pytest.raises(ValueError, match="set together"):
        read_incremental_range(env)


def test_from_after_to_fails():
    with pytest.raises(ValueError, match="after"):
        read_incremental_range({INCREMENTAL_FROM_ENV: "2026-02-01", INCREMENTAL_TO_ENV: "2026-01-01"})


def test_non_iso_date_fails():
    with pytest.raises(ValueError):
        read_incremental_range({INCREMENTAL_FROM_ENV: "2026-01-01' OR 1=1 --", INCREMENTAL_TO_ENV: "2026-01-31"})


# ── per-block decision ───────────────────────────────────────────────────────
def test_historical_ignores_leftover_yaml_dates():
    assert incremental_filter_column("data_validation", CAPABLE_PLAN, run_incremental=False) is None


def test_incremental_configured_table_filters():
    assert incremental_filter_column("data_validation", CAPABLE_PLAN, run_incremental=True) == "UPDATED_AT"


def test_incremental_unconfigured_data_validation_errors():
    with pytest.raises(ValueError, match="no validation_plan.incremental"):
        incremental_filter_column("data_validation", {}, run_incremental=True)


def test_incremental_unconfigured_sibling_block_runs_unfiltered():
    assert incremental_filter_column("transformation_validation", {}, run_incremental=True) is None
    assert incremental_filter_column("count_validation", {}, run_incremental=True) is None


# ── ADR 0033 guard now keyed on this run's apply_incremental ─────────────────
def test_historical_hybrid_with_capability_runs_hybrid():
    apply = bool(incremental_filter_column("data_validation", CAPABLE_PLAN, run_incremental=False))
    check_hybrid_incremental_conflict(True, apply)  # no raise


def test_incremental_hybrid_with_capability_blocked():
    apply = bool(incremental_filter_column("data_validation", CAPABLE_PLAN, run_incremental=True))
    with pytest.raises(ValueError, match="not supported with hybrid_v1"):
        check_hybrid_incremental_conflict(True, apply)


# ── guard is table-scoped: whole table blocked, not just data_validation ─────
_QUERIES = {"source": "postgresql", "sourcequery": "SELECT a FROM t",
            "target": "snowflake", "targetquery": "SELECT a FROM t"}


def _table(plan):
    return {"validations": {"validation_plan": plan,
                            "data_validation": dict(_QUERIES),
                            "row_hash_validation": dict(_QUERIES)}}


def _replay(table_config, run_incremental):
    """Mirror of main.py's per-block decision (lines ~342-367): for each block
    with queries, 'error' if it raises before any query runs, else
    ('hybrid' | 'plain', filter_column). row_hash_validation is skipped
    before any of it, exactly like main.py (ADR 0035)."""
    validations = table_config["validations"]
    plan = validations.get("validation_plan") or {}
    row_hash = validations.get("row_hash_validation") or {}
    out = {}
    for name, cfg in validations.items():
        if name == "row_hash_validation":
            continue
        if not cfg.get("source"):
            continue
        try:
            use_hybrid = should_dispatch_hybrid(name, plan, row_hash)
            col = incremental_filter_column(name, plan, run_incremental)
            check_hybrid_incremental_conflict(
                should_dispatch_hybrid("data_validation", plan, row_hash), bool(col))
        except ValueError as e:
            out[name] = ("error", str(e))
            continue
        out[name] = ("hybrid" if use_hybrid else "plain", col)
    return out


def test_incremental_hybrid_capable_blocks_every_block():
    got = _replay(_table(CAPABLE_PLAN), run_incremental=True)
    assert set(got) == {"data_validation"}  # row_hash_validation skipped -> one ERROR, not two
    status, msg = got["data_validation"]
    assert status == "error" and "not supported with hybrid_v1" in msg  # no query executed


def test_historical_hybrid_capable_runs_hybrid():
    got = _replay(_table(CAPABLE_PLAN), run_incremental=False)
    assert got["data_validation"] == ("hybrid", None)
    assert "row_hash_validation" not in got  # no standalone row-hash run (ADR 0035)


def test_incremental_non_hybrid_capable_filters():
    plan = {"incremental": {"enabled": True, "filter_column": "UPDATED_AT"}}
    got = _replay(_table(plan), run_incremental=True)
    assert got["data_validation"] == ("plain", "UPDATED_AT")


def test_incremental_hybrid_unconfigured_keeps_sibling_behavior():
    got = _replay(_table({"execution_strategy": "hybrid_v1"}), run_incremental=True)
    assert got["data_validation"][0] == "error"
    assert "no validation_plan.incremental" in got["data_validation"][1]
    assert "row_hash_validation" not in got


# ── ADR 0035: row_hash_validation is a Tier-1 helper, never run standalone ────
def test_standard_table_skips_row_hash_helper():
    got = _replay(_table({}), run_incremental=False)
    assert got == {"data_validation": ("plain", None)}


def test_real_sibling_blocks_still_execute():
    table = _table({})
    table["validations"]["transformation_validation"] = dict(_QUERIES)
    table["validations"]["aggregate_validation"] = dict(_QUERIES)
    got = _replay(table, run_incremental=False)
    assert got == {"data_validation": ("plain", None),
                   "transformation_validation": ("plain", None),
                   "aggregate_validation": ("plain", None)}


# ── runner.start_validation child env ────────────────────────────────────────
def _child_env(**kwargs):
    with mock.patch.object(runner.subprocess, "Popen") as popen:
        running = runner.start_validation("bronze", "local", ["t"], False, True, **kwargs)
    for p in (running.stdout_path, running.stderr_path):
        p.unlink(missing_ok=True)
    return popen.call_args.kwargs["env"]


def test_historical_strips_inherited_vars(monkeypatch):
    monkeypatch.setenv(INCREMENTAL_FROM_ENV, "2020-01-01")
    monkeypatch.setenv(INCREMENTAL_TO_ENV, "2020-01-31")
    env = _child_env()
    assert INCREMENTAL_FROM_ENV not in env and INCREMENTAL_TO_ENV not in env
    assert os.environ[INCREMENTAL_FROM_ENV] == "2020-01-01"  # parent untouched


def test_incremental_passes_both_vars(monkeypatch):
    monkeypatch.delenv(INCREMENTAL_FROM_ENV, raising=False)
    env = _child_env(incremental_range=("2026-01-01", "2026-01-31"))
    assert env[INCREMENTAL_FROM_ENV] == "2026-01-01" and env[INCREMENTAL_TO_ENV] == "2026-01-31"
    assert INCREMENTAL_FROM_ENV not in os.environ


# ── D2 summary discovery ─────────────────────────────────────────────────────
def test_only_integrity_check_summaries_collected(tmp_path, monkeypatch):
    run_id = "R1"
    run_dir = tmp_path / "output" / "bronze" / f"validation_{run_id}"
    for sub in (f"count_validation_{run_id}", f"data_validation_{run_id}"):
        (run_dir / sub).mkdir(parents=True)
        pd.DataFrame({"table": [sub]}).to_csv(run_dir / sub / "integrity_check_summary.csv", index=False)
        pd.DataFrame({"x": [1]}).to_csv(run_dir / sub / "transformation_validation_summary.csv", index=False)
    monkeypatch.setattr(runner, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(runner.results_store, "record_run", lambda *a, **k: None)

    out = tmp_path / "stdout.log"
    out.write_text(f"Run ID: {run_id}\n")
    err = tmp_path / "stderr.log"
    err.write_text("")
    proc = mock.Mock(returncode=0)
    result = runner.collect_validation_result(runner.RunningValidation(proc, out, err), "bronze", "local")

    assert set(result["summaries"]) == {"integrity_check"}
    assert len(result["summaries"]["integrity_check"]) == 2
