"""
Token Usage Logger
===================
Shared, best-effort logger for real AI token usage, called from the two live
AI call sites in the codebase:

  - src/ai/rule_planner.py           (column mapping / rule assignment calls)
  - src/generated_queries/ai_sql_generator.py  (SQL generation calls)

Every AI response carries a `usage` object with the actual prompt/completion
token counts billed by the provider. This module appends one JSON line per
AI call to token_usage_analysis/logs/token_usage.jsonl, tagged with a
per-process SESSION_ID so `report_token_usage.py` can summarize token spend
and estimated cost for one execution of validate_cli.py.

Usage (from a call site):
    from token_usage_analysis.token_logger import log_usage
    log_usage(
        backend="dial", model="gpt-4o", call_type="sql_generation",
        context="orders.source", prompt_tokens=800, completion_tokens=300,
    )

Logging never raises — a failure here must never break an actual validation
run, so every call is wrapped in a broad except.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "token_usage.jsonl"
_LOCK     = threading.Lock()

# One session id per process — every AI call made during a single
# `validate_cli.py` invocation (single-table, run-tables, or batch) shares
# this id, so the report script can group usage per execution.
SESSION_ID = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def log_usage(
    backend: str,
    model: str,
    call_type: str,
    context: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: Optional[int] = None,
    attempt: int = 1,
    extra: Optional[dict] = None,
) -> None:
    """
    Append one real AI-call token record to token_usage.jsonl.

    Args:
        backend:            "dial" or "claude"
        model:               model name actually used for the call
        call_type:           "column_mapping" (rule_planner) or "sql_generation" (ai_sql_generator)
        context:              free-text label, e.g. "orders.source" or "customers.target"
        prompt_tokens:        tokens billed for the prompt/input
        completion_tokens:    tokens billed for the completion/output
        total_tokens:         if omitted, computed as prompt + completion
        attempt:              self-correction attempt number (1-3) for sql_generation calls
        extra:                any additional small metadata to store (e.g. run outcome)
    """
    try:
        if total_tokens is None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

        record = {
            "session_id":        SESSION_ID,
            "timestamp":         time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid":                os.getpid(),
            "backend":            backend,
            "model":              model,
            "call_type":          call_type,
            "context":            context,
            "attempt":            attempt,
            "prompt_tokens":      int(prompt_tokens or 0),
            "completion_tokens":  int(completion_tokens or 0),
            "total_tokens":       int(total_tokens or 0),
        }
        if extra:
            record["extra"] = extra

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except Exception:
        # Token logging must never break an actual validation/generation run.
        pass


def extract_openai_usage(response) -> dict:
    """Pull prompt/completion/total tokens out of an OpenAI/AzureOpenAI response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens":     getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens":      getattr(usage, "total_tokens", 0) or 0,
    }


def extract_anthropic_usage(response) -> dict:
    """Pull input/output tokens out of an Anthropic Claude response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens     = getattr(usage, "input_tokens", 0) or 0
    completion_tokens = getattr(usage, "output_tokens", 0) or 0
    return {
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens":      prompt_tokens + completion_tokens,
    }
