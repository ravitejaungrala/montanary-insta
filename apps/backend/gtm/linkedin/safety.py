"""GTM LinkedIn Agent — adaptive safety: warm-up ramp + consecutive-failure auto-pause.

Two lightweight controls layered on the existing static caps / cooldown:

- **Warm-up ramp (#1):** a fresh account sends only a fraction of its configured daily cap
  and ramps to full over ~2 weeks — anchored on OUTREACH age (its first real send across any
  action type), NOT the DB row's created_at. An old LinkedIn account connected today still
  starts cold; an account connected long ago but never used does too.
- **Consecutive-failure auto-pause:** a streak of failed jobs (session break / LinkedIn UI
  change / cookie invalidation) pauses the account before it keeps hammering a broken flow.

DB-read only; no Playwright. The deliberately-omitted health-rate layer (acceptance/reply
rates, scoring, dynamic throttle, safe-mode) was dropped as low-signal at our volume — hard
restrictions are already handled by `auth.mark_cooldown` + `cooldown_until`.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from gtm.linkedin.events import record_event

logger = logging.getLogger("pipelyt.gtm_linkedin.safety")

# IST day boundary (matches the email engine + worker). The per-type daily
# counters reset on a new local-calendar day.
_DAY_TZ_OFFSET = timedelta(hours=5, minutes=30)


def reset_daily_counts_if_new_day(db: Session, settings: Any) -> None:
    """Zero the per-type daily counters when the local-calendar day rolls over.
    MUST run on BOTH the dispatch read-path (here) and the worker send-path — if
    only the worker reset, a capped account would never dispatch a job on the new
    day, so the worker (and thus the reset) would never run → the counter would
    stay pinned at the cap forever. Idempotent: a no-op once already reset today."""
    if settings is None:
        return
    today = (datetime.utcnow() + _DAY_TZ_OFFSET).date()
    last = getattr(settings, "last_reset_at", None)
    last_day = (last + _DAY_TZ_OFFSET).date() if last else None
    if last_day is None or last_day < today:
        settings.daily_connection_count = 0
        settings.daily_message_count = 0
        settings.daily_inmail_count = 0
        settings.last_reset_at = datetime.utcnow()
        db.commit()

# Warm-up curve: (max_age_days_inclusive, factor). A fresh account starts at 25% of its
# configured cap and reaches 100% after two weeks of real outreach. One edit to retune.
_RAMP_STEPS = [(2, 0.25), (6, 0.50), (13, 0.75)]  # 14d+ → 1.0 (default below)

# Trailing-jobs window for the consecutive-failure guard: this many failures in a row pause.
_RECENT_FAILURE_WINDOW = 5

# job_type → settings attribute holding that action's configured daily cap / live counter.
_JOB_LIMIT_ATTR = {
    "send_connection_request": "daily_connection_limit",
    # The combined first touch's primary action is the connection request, so it
    # draws from the SAME connection cap (it bumps daily_connection_count).
    "send_connect_and_inmail": "daily_connection_limit",
    "send_message": "daily_message_limit",
    "send_inmail": "daily_inmail_limit",
}
_JOB_COUNT_ATTR = {
    "send_connection_request": "daily_connection_count",
    "send_connect_and_inmail": "daily_connection_count",
    "send_message": "daily_message_count",
    "send_inmail": "daily_inmail_count",
}


def outreach_age_days(db: Session, account_id: int) -> int:
    """Days since the account's FIRST outreach (connection / message / inmail). 0 if it has
    not sent anything yet (just-starting). Anchored on real activity, not the row's age."""
    row = db.execute(
        text(
            """
            SELECT MIN(event_time) FROM gtm_linkedin_events
             WHERE linkedin_account_id = :a
               AND event_type IN ('connection_sent', 'message_sent', 'inmail_sent')
            """
        ),
        {"a": account_id},
    ).first()
    first = row[0] if row else None
    if not first:
        return 0
    if first.tzinfo is not None:
        first = first.replace(tzinfo=None)
    return max(0, (datetime.utcnow() - first).days)


def ramp_factor(age_days: int) -> float:
    """Warm-up multiplier in (0, 1] for an account that's been doing outreach `age_days`."""
    for max_age, factor in _RAMP_STEPS:
        if age_days <= max_age:
            return factor
    return 1.0


def effective_daily_limit(db: Session, account: Any, settings: Any, job_type: str) -> int:
    """The warm-up-ramped daily cap for `job_type`. Returns 0 for non-capped job types
    (check_*, validate_session, login) so callers leave their existing behavior unchanged."""
    attr = _JOB_LIMIT_ATTR.get(job_type)
    if not attr or settings is None or account is None:
        return 0
    base = int(getattr(settings, attr, 0) or 0)
    if base <= 0:
        return 0
    factor = ramp_factor(outreach_age_days(db, account.id))
    return max(1, math.ceil(base * factor))


def remaining_daily(db: Session, account: Any, settings: Any, job_type: str) -> int:
    """How many more `job_type` sends are allowed today under the ramped cap. A large
    sentinel for non-capped job types (so the scheduler never gates them here)."""
    # Roll the daily counters over on a new day BEFORE reading them, so a fully-
    # capped account from yesterday isn't permanently stuck (the worker's own reset
    # only fires when a job actually runs, which can't happen while capped).
    reset_daily_counts_if_new_day(db, settings)
    limit = effective_daily_limit(db, account, settings, job_type)
    if limit <= 0:
        return 1_000_000
    cattr = _JOB_COUNT_ATTR.get(job_type)
    count = int(getattr(settings, cattr, 0) or 0) if cattr else 0
    return limit - count


def consecutive_failures(db: Session, account_id: int) -> int:
    """Length of the leading run of failed/dead jobs in the account's last
    `_RECENT_FAILURE_WINDOW` jobs (newest first). A streak signals a broken session/UI."""
    rows = db.execute(
        text(
            """
            SELECT status FROM gtm_linkedin_jobs
             WHERE linkedin_account_id = :a
             ORDER BY created_at DESC, id DESC
             LIMIT :n
            """
        ),
        {"a": account_id, "n": _RECENT_FAILURE_WINDOW},
    ).fetchall()
    streak = 0
    for r in rows:
        if (r[0] or "") in ("failed", "dead"):
            streak += 1
        else:
            break
    return streak


def evaluate_and_autopause(db: Session, account: Any) -> bool:
    """Pause the account if its recent jobs are a failure streak. Idempotent. Recovery is
    deliberately manual (operator un-pauses via /{id}/pause) — a streak means something
    broke, so silent auto-resume is unsafe. Returns True if it paused now."""
    if getattr(account, "is_paused", False):
        return False
    if consecutive_failures(db, account.id) >= _RECENT_FAILURE_WINDOW:
        account.is_paused = True
        account.updated_at = datetime.utcnow()
        db.commit()
        record_event(
            db, workspace_id=account.workspace_id, linkedin_account_id=account.id,
            event_type="account_auto_paused", metadata={"reason": "consecutive_failures"},
        )
        logger.warning(
            "gtm_linkedin: auto-paused account %s (%d consecutive job failures)",
            account.id, _RECENT_FAILURE_WINDOW,
        )
        return True
    return False
