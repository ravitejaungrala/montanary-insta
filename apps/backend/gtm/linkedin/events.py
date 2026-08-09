"""GTM LinkedIn Agent — event emission + (Phase 4) analytics queries.

`gtm_linkedin_events` is the immutable audit/analytics log: every metric is a
COUNT over this table, and the unique partial index on
(lead_id, event_type) for {connection_accepted, reply_received} makes detection
events idempotent. `record_event` swallows that unique-violation so a repeated
detection is a no-op rather than an error.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gtm.linkedin.models import GtmLinkedInEvent

logger = logging.getLogger("pipelyt.gtm_linkedin.events")

# Detection events that must occur at most once per lead (DB-enforced).
_DEDUP_EVENTS = {"connection_accepted", "reply_received"}


def record_event(
    db: Session,
    *,
    workspace_id: int,
    event_type: str,
    lead_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    linkedin_account_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[GtmLinkedInEvent]:
    """Append one event. Returns the row, or None if it was a duplicate of a
    dedup-protected detection event (treated as a successful no-op)."""
    ev = GtmLinkedInEvent(
        workspace_id=workspace_id,
        lead_id=lead_id,
        campaign_id=campaign_id,
        linkedin_account_id=linkedin_account_id,
        event_type=event_type,
        event_metadata=metadata or {},
    )
    db.add(ev)
    try:
        db.commit()
        return ev
    except IntegrityError:
        db.rollback()
        if event_type in _DEDUP_EVENTS:
            logger.info("gtm_linkedin event %s deduped for lead %s", event_type, lead_id)
            return None
        raise


# ── Phase 4 analytics — windowed COUNTs over gtm_linkedin_events ───────────────
def linkedin_funnel(
    db: Session,
    *,
    workspace_id: int,
    since: Any,
    until: Any = None,
) -> Dict[str, int]:
    """Windowed funnel counts for the LinkedIn dashboard block:
    `connection_sent`, `connection_accepted`, `messages_sent` (message + inmail),
    `replies`. Every dashboard metric is a COUNT over this immutable event log.

    Defensive: returns all-zeros if `gtm_linkedin_events` doesn't exist yet (the
    feature ships dark / pre-migration), so the GTM dashboard never breaks."""
    zeros = {"connection_sent": 0, "connection_accepted": 0, "messages_sent": 0, "replies": 0}
    try:
        rows = db.execute(
            text(
                """
                SELECT event_type, COUNT(*) AS n
                  FROM gtm_linkedin_events
                 WHERE workspace_id = :ws
                   AND event_time >= :since
                   AND (:until IS NULL OR event_time <= :until)
                   AND event_type IN ('connection_sent','connection_accepted',
                                      'message_sent','inmail_sent','reply_received')
                 GROUP BY event_type
                """
            ),
            {"ws": workspace_id, "since": since, "until": until},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — most likely UndefinedTable (pre-migration)
        logger.debug("linkedin_funnel skipped (table missing?): %s", str(exc)[:120])
        try:
            db.rollback()
        except Exception:
            pass
        return zeros
    c = {r[0]: int(r[1] or 0) for r in rows}
    return {
        "connection_sent": c.get("connection_sent", 0),
        "connection_accepted": c.get("connection_accepted", 0),
        "messages_sent": c.get("message_sent", 0) + c.get("inmail_sent", 0),
        "replies": c.get("reply_received", 0),
    }
