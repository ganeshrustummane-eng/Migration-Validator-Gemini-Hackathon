"""
Model Probe
===========
Tests which DIAL models are actually reachable with the current API key
and caches the results so the CLI doesn't re-probe on every run.

Cache file: .dial_model_cache.json  (next to .env)
Cache TTL : 24 hours
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CACHE_FILE = Path(__file__).parent.parent / ".dial_model_cache.json"
_CACHE_TTL  = 60 * 60 * 24  # 24 hours


def _test_model(client, api_key: str, model: str) -> Tuple[str, bool, Optional[str]]:
    """Return (model, ok, error_or_None)."""
    try:
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=5,
            extra_headers={"Api-Key": api_key},
        )
        # Some models (e.g. o-series, certain Claude) reject temperature=0
        try:
            kwargs["temperature"] = 0
            client.chat.completions.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower() or "deprecated" in str(e).lower():
                kwargs.pop("temperature", None)
                client.chat.completions.create(**kwargs)
            else:
                raise
        return model, True, None
    except Exception as exc:
        return model, False, str(exc)


def probe_models(
    models: List[str],
    api_key: str,
    api_base: str,
    api_version: str,
    *,
    max_workers: int = 6,
    verbose: bool = False,
) -> Dict[str, bool]:
    """
    Test every model in parallel and return {model: is_working}.
    Results are cached to disk for 24 hours.
    """
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=api_base,
        )
    except ImportError:
        return {m: False for m in models}

    results: Dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_test_model, client, api_key, m): m for m in models}
        for fut in as_completed(futures):
            model, ok, err = fut.result()
            results[model] = ok
            if verbose:
                status = "✓" if ok else "✗"
                print(f"  {status} {model}" + (f"  [{err[:60]}]" if err else ""))

    return results


def load_cache() -> Optional[Dict]:
    """Return cached probe data if fresh, else None."""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text())
        if time.time() - data.get("timestamp", 0) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def save_cache(api_key_prefix: str, results: Dict[str, bool]) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "api_key_prefix": api_key_prefix,
            "results": results,
        }, indent=2))
    except Exception:
        pass


def get_working_models(
    models: List[str],
    api_key: str,
    api_base: str,
    api_version: str,
    *,
    verbose: bool = False,
) -> List[str]:
    """
    Return only the models from `models` that respond successfully.
    Uses a 24-hour disk cache keyed to the API key prefix.
    Falls back to the full list if probing fails entirely.
    """
    if not api_key:
        return models

    key_prefix = api_key[:12]

    cached = load_cache()
    if cached and cached.get("api_key_prefix") == key_prefix:
        results = cached["results"]
        # Only return models we actually know about (list may have changed)
        return [m for m in models if results.get(m, False)]

    # Cache miss — probe now
    if verbose:
        print("  Probing model availability (one-time check, cached 24h)...")

    try:
        results = probe_models(models, api_key, api_base, api_version, verbose=verbose)
        save_cache(key_prefix, results)
        return [m for m in models if results.get(m, False)]
    except Exception:
        # If probing itself fails, return all models unchanged
        return models


def invalidate_cache() -> None:
    """Delete the cache file so the next call re-probes."""
    try:
        _CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
