# Token Usage & Cost Analysis

This folder measures **real** AI token usage and estimated cost for validate_cli.py
runs — not an estimate from prompt sizes, but the actual `usage` object returned
by the AI provider on every live call.

## How it's wired in

Two files in `src/` were instrumented to log after every successful AI call:

- `src/ai/rule_planner.py` — one call per ambiguous ("ai_needed") column during
  column mapping. Logged as `call_type="column_mapping"`.
- `src/generated_queries/ai_sql_generator.py` — one call per SQL generation
  attempt (source query + target query, each up to 3 self-correction attempts).
  Logged as `call_type="sql_generation"`, covering both the DIAL/OpenAI path
  and the direct Anthropic Claude path.

Each call appends one JSON line to `token_usage_analysis/logs/token_usage.jsonl`
with the real `prompt_tokens`/`completion_tokens`/`total_tokens` from the
provider's response, tagged with a per-process `session_id` so every AI call
made during a single `validate_cli.py` invocation groups together.

Logging is best-effort and wrapped in broad `except` blocks — it can never
break an actual validation run, even if the log file/folder is missing or
unwritable.

## Running a report

```bash
# Latest execution only (default)
python token_usage_analysis/report_token_usage.py

# One specific session
python token_usage_analysis/report_token_usage.py --session 20260819_143012_a1b2c3d4

# Every session ever logged
python token_usage_analysis/report_token_usage.py --all

# List all known session ids
python token_usage_analysis/report_token_usage.py --list
```

Each report breaks totals down by call type (`column_mapping` vs
`sql_generation`) and by model, alongside a total estimated cost.

## Files

| File | Purpose |
|---|---|
| `token_logger.py` | Shared logger imported by the two AI call sites; also extracts usage from OpenAI/Anthropic response objects. |
| `pricing.json` | Per-model $/1M token rates used to estimate cost. |
| `report_token_usage.py` | CLI report — token totals + estimated cost, by session/call-type/model. |
| `logs/token_usage.jsonl` | Append-only log of every real AI call (created on first AI call after this change). |

## Important caveat on cost accuracy

`pricing.json` uses each provider's **public list price**. EPAM DIAL is a
proxy in front of these models and may bill internally at a different rate
than the public list price (DIAL adds its own routing/metering on top) — so
treat the cost figures here as a **reasonable approximation anchored to
public pricing**, not an exact invoice number. Update `pricing.json` if EPAM
shares the actual internal DIAL rate card.

## What this does *not* cover

- Column mapping calls that never happened because the column matched
  exactly or via fuzzy matching alone (by design — those never reach the AI,
  see the "token-efficiency design" note in `rule_planner.py`).
- Any AI calls made outside these two instrumented call sites (e.g. the
  separate, currently-unwired `src/profiling/ai_recommendation.py` module).
