"""Shared retry helper for transient-error resilience on LLM / image calls.

Retries UP TO 3 times (4 attempts total) with exponential backoff on transient
errors only. Non-transient errors (spend cap, auth, 400 bad request) fail fast
so we don't waste calls on things a retry can never fix.

Used by:
  • services/ai_service.py           — REFINER, RESEARCHER, COPYWRITER, CRITIC,
                                        CULTURAL_CALENDAR (Gemini)
  • services/magic_image_pipeline.py — Agent 1 (GPT-5 art director) + Agent 2
                                        (gpt-image-2)

After 3 retries, the underlying exception is re-raised — the caller's existing
fallback path fires (magic → Image Agent v4, image_v4 → gemini-2.5, etc.).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("pipelyt.retry")

T = TypeVar("T")

# Retry policy
MAX_ATTEMPTS = 4        # 1 original + 3 retries
BASE_DELAY_S = 5.0      # first retry after 5s
BACKOFF_MULT = 2.0      # then 10s, 20s (exponential)


def _classify_error(exc: Exception) -> str:
    """Categorize an LLM/image API error to decide whether retry helps.

    Returns:
      "transient" — worth retrying (rate limit, network blip, 5xx, timeout)
      "spend_cap" — Gemini monthly cap hit; retry won't help until cap raised
      "auth"      — API key invalid / permission denied; retry won't help
      "bad_input" — 400 (prompt too long, invalid param); retry won't help
      "unknown"   — treat conservatively as non-transient (fail fast)
    """
    s = str(exc).lower()

    # Quota-exhausted 429 — retrying is pointless because monthly billing
    # cap / project spend cap won't lift on a 5-30s retry. Covers both
    # Gemini ("spending cap") and OpenAI ("insufficient_quota", "exceeded
    # your current quota") phrasings.
    if (
        "spending cap" in s
        or "spend cap" in s
        or "insufficient_quota" in s
        or "exceeded your current quota" in s
        or "check your plan and billing" in s
    ):
        return "spend_cap"

    # Auth / permission — retrying is pointless
    if "invalid_api_key" in s or "permission_denied" in s or "unauthorized" in s or "401" in s or "403" in s:
        return "auth"

    # Bad input — retrying is pointless
    if "invalid_request_error" in s or "invalid_value" in s or "missing required parameter" in s:
        return "bad_input"

    # Transient — worth retrying
    if "429" in s or "resource_exhausted" in s or "rate limit" in s or "quota" in s:
        return "transient"
    if "timeout" in s or "timed out" in s or "connection" in s or "network" in s:
        return "transient"
    if "500" in s or "502" in s or "503" in s or "504" in s or "internal_server_error" in s or "service_unavailable" in s:
        return "transient"

    return "unknown"


def call_with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay_s: float = BASE_DELAY_S,
    backoff_mult: float = BACKOFF_MULT,
) -> T:
    """Invoke `fn()` with retries on transient errors.

    Args:
      fn: zero-arg callable that makes the API request and returns its result
      label: short human-readable identifier for logs (e.g. "REFINER", "Agent 1")
      max_attempts: 1 original + (max_attempts-1) retries. Default 4 = 3 retries.
      base_delay_s: seconds before the FIRST retry
      backoff_mult: multiplier applied to delay per additional retry

    Returns whatever `fn()` returns on the first successful attempt.
    Raises the underlying exception if all attempts exhausted OR on a
    non-transient error (spend cap / auth / bad input).
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            kind = _classify_error(exc)
            remaining = max_attempts - attempt

            # Non-transient: fail fast
            if kind in ("spend_cap", "auth", "bad_input"):
                logger.error(
                    f"[retry] {label}: {kind.upper()} error on attempt {attempt} — "
                    f"NOT retrying (retry won't help) | {exc}"
                )
                raise
            if kind == "unknown":
                logger.error(
                    f"[retry] {label}: UNKNOWN error on attempt {attempt} — "
                    f"NOT retrying (unrecognized failure mode) | {exc}"
                )
                raise

            # Transient: retry if we still have attempts
            if remaining <= 0:
                logger.error(
                    f"[retry] {label}: TRANSIENT error on attempt {attempt}/{max_attempts} "
                    f"— retries exhausted, falling back | {exc}"
                )
                raise

            delay = base_delay_s * (backoff_mult ** (attempt - 1))
            logger.warning(
                f"[retry] {label}: TRANSIENT error on attempt {attempt}/{max_attempts} "
                f"— sleeping {delay:.1f}s then retrying | {exc}"
            )
            time.sleep(delay)

    # Unreachable in practice; keeps type-checker happy.
    assert last_exc is not None
    raise last_exc
