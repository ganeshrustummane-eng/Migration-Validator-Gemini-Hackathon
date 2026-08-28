"""
Token Usage & Cost Report
===========================
Reads token_usage_analysis/logs/token_usage.jsonl (written by token_logger.py,
called from the two live AI call sites: src/ai/rule_planner.py and
src/generated_queries/ai_sql_generator.py) and prints a token/cost summary
for one execution of validate_cli.py.

Every logged record is REAL token usage taken from the AI provider's own
response (response.usage), not an estimate — so this reflects what actually
happened during a run, including every self-correction retry.

Usage:
  python token_usage_analysis/report_token_usage.py                # latest session only
  python token_usage_analysis/report_token_usage.py --session <id> # one specific session
  python token_usage_analysis/report_token_usage.py --all          # every session ever logged
  python token_usage_analysis/report_token_usage.py --list         # list known session ids

Note on cost accuracy: EPAM DIAL is a proxy in front of these models and may
bill internally at a different rate than the public list prices in
pricing.json — see that file's _comment field.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the box-drawing
# characters below — force UTF-8 the same way validate_cli.py does.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_HERE      = Path(__file__).parent
_LOG_FILE  = _HERE / "logs" / "token_usage.jsonl"
_PRICING_FILE = _HERE / "pricing.json"


def _load_records() -> list:
    if not _LOG_FILE.exists():
        return []
    records = []
    with open(_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _load_pricing() -> dict:
    with open(_PRICING_FILE, encoding="utf-8") as f:
        return json.load(f)


def _cost_for(model: str, prompt_tokens: int, completion_tokens: int, pricing: dict) -> float:
    rates = pricing["models"].get(model, pricing["default_unknown_model"])
    cost_in  = (prompt_tokens     / 1_000_000) * rates["input_per_million"]
    cost_out = (completion_tokens / 1_000_000) * rates["output_per_million"]
    return cost_in + cost_out


def _summarize(records: list, pricing: dict) -> None:
    if not records:
        print("  No token usage recorded for this selection.")
        return

    total_prompt = total_completion = total_tokens = 0
    total_cost = 0.0
    by_call_type = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})
    by_model     = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})

    for r in records:
        p = r.get("prompt_tokens", 0)
        c = r.get("completion_tokens", 0)
        t = r.get("total_tokens", p + c)
        model = r.get("model", "unknown")
        call_type = r.get("call_type", "unknown")
        cost = _cost_for(model, p, c, pricing)

        total_prompt += p
        total_completion += c
        total_tokens += t
        total_cost += cost

        by_call_type[call_type]["calls"] += 1
        by_call_type[call_type]["prompt"] += p
        by_call_type[call_type]["completion"] += c
        by_call_type[call_type]["cost"] += cost

        by_model[model]["calls"] += 1
        by_model[model]["prompt"] += p
        by_model[model]["completion"] += c
        by_model[model]["cost"] += cost

    print(f"  Total AI calls        : {len(records)}")
    print(f"  Prompt tokens         : {total_prompt:,}")
    print(f"  Completion tokens     : {total_completion:,}")
    print(f"  Total tokens          : {total_tokens:,}")
    print(f"  Estimated cost (USD)  : ${total_cost:.4f}")

    print("\n  By call type:")
    for call_type, s in sorted(by_call_type.items()):
        print(
            f"    {call_type:<16} calls={s['calls']:<4} "
            f"prompt={s['prompt']:>8,} completion={s['completion']:>8,} "
            f"cost=${s['cost']:.4f}"
        )

    print("\n  By model:")
    for model, s in sorted(by_model.items()):
        print(
            f"    {model:<28} calls={s['calls']:<4} "
            f"prompt={s['prompt']:>8,} completion={s['completion']:>8,} "
            f"cost=${s['cost']:.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize real AI token usage and estimated cost.")
    parser.add_argument("--session", default=None, help="Summarize one specific session id")
    parser.add_argument("--all", action="store_true", help="Summarize every session ever logged")
    parser.add_argument("--list", action="store_true", help="List known session ids and exit")
    args = parser.parse_args()

    records = _load_records()
    pricing = _load_pricing()

    if args.list:
        sessions = sorted({r.get("session_id", "") for r in records})
        if not sessions:
            print("  No sessions logged yet.")
        for s in sessions:
            count = sum(1 for r in records if r.get("session_id") == s)
            print(f"    {s}   ({count} AI calls)")
        return

    if args.all:
        print(f"\n  TOKEN USAGE — ALL {len({r.get('session_id') for r in records})} SESSION(S)")
        print("  " + "─" * 64)
        _summarize(records, pricing)
        return

    if args.session:
        selected = [r for r in records if r.get("session_id") == args.session]
        print(f"\n  TOKEN USAGE — SESSION {args.session}")
        print("  " + "─" * 64)
        _summarize(selected, pricing)
        return

    # Default: latest session (highest session_id, since it's timestamp-prefixed)
    sessions = sorted({r.get("session_id", "") for r in records})
    if not sessions:
        print("  No token usage recorded yet. Run a `generate` or `batch` command with an AI key set first.")
        return
    latest = sessions[-1]
    selected = [r for r in records if r.get("session_id") == latest]
    print(f"\n  TOKEN USAGE — LATEST EXECUTION  (session {latest})")
    print("  " + "─" * 64)
    _summarize(selected, pricing)
    print(f"\n  Tip: use --all to see totals across every run, or --list to see all session ids.")


if __name__ == "__main__":
    main()
