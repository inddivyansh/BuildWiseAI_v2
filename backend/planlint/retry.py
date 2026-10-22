"""Retry wrapper for transient LLM provider failures (503 Service Unavailable,
429 Rate Limits, 502/504 Gateway errors).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

TRANSIENT_KEYWORDS = (
    "503",
    "429",
    "unavailable",
    "high demand",
    "spikes in demand",
    "rate limit",
    "resource_exhausted",
    "resource exhausted",
    "temporarily",
    "temporary",
    "overloaded",
    "bad gateway",
    "gateway timeout",
    "502",
    "504",
)

# Keywords that indicate a hard quota / RPM exhaustion (as opposed to a
# transient 503 spike). These warrant a much longer wait (60 s+) because
# Gemini's free-tier minute-window must fully reset before the next call.
_QUOTA_KEYWORDS = (
    "429",
    "rate limit",
    "resource_exhausted",
    "resource exhausted",
    "quota",
)


async def run_agent_with_retry(
    agent: Any,
    prompt: Any,
    *,
    deps: Any = None,
    max_retries: int = 4,
    delays: Sequence[float] = (2.0, 4.0, 8.0, 12.0),
) -> Any:
    """Execute `agent.run(...)` with exponential backoff on transient errors.

    For 429 / quota exhaustion errors the delay is extended to 65 s so the
    Gemini free-tier RPM window (1 minute) fully resets before we retry.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            if deps is not None:
                return await agent.run(prompt, deps=deps)
            return await agent.run(prompt)
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()
            status_code = getattr(exc, "status_code", None)

            is_quota = (
                status_code == 429
                or any(k in exc_str for k in _QUOTA_KEYWORDS)
            )
            is_transient = (
                is_quota
                or status_code in (503, 502, 504)
                or any(k in exc_str for k in TRANSIENT_KEYWORDS)
            )

            if is_transient and attempt < max_retries - 1:
                if is_quota:
                    # Gemini free-tier RPM window is 60 s. Add a small buffer.
                    delay = 65.0
                else:
                    delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    "LLM provider returned transient error (%s). Attempt %d/%d. Retrying in %.1fs...",
                    exc,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            raise last_exc
    raise last_exc
