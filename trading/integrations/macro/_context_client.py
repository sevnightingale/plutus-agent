"""context.dev client — lazy singleton for structured web extraction.

The macro data points read a single number from a canonical web source
(MarketWatch, BLS, Farside …) via context.dev's ``web.extract`` — AI-driven
structured extraction over a JSON schema, which handles JS-rendered tables a
raw markdown scrape cannot. One client, reused across fetches.

We FAIL LOUDLY: a missing key or an unreadable source raises rather than
returning a fallback or a guessed value — a macro reading must be real or
absent, never fabricated.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_client = None


def get_context_client():
    """Return a cached ``ContextDev`` client, or raise if the key is unset."""
    global _client
    if _client is None:
        api_key = os.getenv("CONTEXT_DEV_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "CONTEXT_DEV_API_KEY not set — macro data points use context.dev "
                "for web extraction. Add it to ~/.plutus-agent/.env."
            )
        from context.dev import ContextDev

        _client = ContextDev(api_key=api_key, timeout=120)
    return _client


def extract_value(
    primary_url: str,
    schema: Dict[str, Any],
    instructions: str,
    *,
    fallback_urls: Optional[List[str]] = None,
    timeout_ms: int = 60000,
) -> Dict[str, Any]:
    """Structured ``web.extract`` over ``primary_url`` then each fallback.

    Returns the extracted ``data`` dict (matching ``schema``) with the winning
    URL added as ``source``. Raises if every source fails — a macro DP that
    can't read its source surfaces the failure; it never guesses.
    """
    cli = get_context_client()
    urls = [primary_url] + list(fallback_urls or [])
    last_err: Optional[Exception] = None
    for url in urls:
        try:
            resp = cli.web.extract(
                url=url,
                schema=schema,
                instructions=instructions,
                timeout_ms=timeout_ms,
            )
            data = resp.data
            data = dict(data) if isinstance(data, dict) else (
                data.model_dump() if hasattr(data, "model_dump") else {}
            )
            if data:
                data["source"] = url
                return data
        except Exception as exc:  # noqa: BLE001 — try the next source
            last_err = exc
            continue
    raise RuntimeError(
        f"context.dev web.extract returned nothing for any source {urls}: {last_err}"
    )


def classify(value: float, buckets: List[tuple]) -> Dict[str, str]:
    """Map ``value`` to its regime bucket. ``buckets`` are ``(lo, hi, label,

    narrative)`` with half-open ``[lo, hi)`` ranges (use ``float('-inf')`` /
    ``float('inf')`` for open ends). Returns ``{label, narrative}``; the last
    bucket is the fallback if nothing matches.
    """
    for lo, hi, label, narrative in buckets:
        if lo <= value < hi:
            return {"label": label, "narrative": narrative}
    lo, hi, label, narrative = buckets[-1]
    return {"label": label, "narrative": narrative}
