"""GTM LinkedIn Agent — inbound replies: persist, classify, decide.

Three responsibilities, in order:

1. **Persist.** A read thread becomes a `nexus_linkedin_messages` row with
   `direction='inbound'`. That table already had the columns (`direction`,
   `body`, `intent`, `intent_confidence`, `lead_sequence_id`) — nothing had ever
   written them, because the old `detect_reply` returned a bare boolean and threw
   the message away.

2. **Classify.** Via `intent_classifier.classify(body, channel="linkedin")` —
   the same eight intents as email over a LinkedIn-shaped prompt, so the UI,
   analytics and branching stay single-sourced.

3. **Decide.** Map the intent to plan operations (`decide`). Continuing intents
   re-time or reground the cadence; halting intents truncate it.

THE SAFETY POSTURE: every uncertain path resolves to STOP. Unreadable text, low
confidence, an unknown intent, a classifier outage — all land on `truncate`,
which is exactly what the engine did before any of this existed. Dynamic
behaviour is opt-in on confident, readable signal only.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

from sqlalchemy import text as _sql
from sqlalchemy.orm import Session

logger = logging.getLogger("pipelyt.gtm_linkedin.replies")

# Below this, we do not act on the classification — we stop, as before.
# LinkedIn replies are short, so misclassification risk is materially higher than
# on email; the asymmetry is deliberate. Wrongly stopping costs one lead. Wrongly
# CONTINUING messages someone who asked us not to, which risks the account.
MIN_CONFIDENCE = 0.5

# Intents that keep the cadence alive. `INTERESTED` and `QUESTION` are absent on
# purpose: they route to conversation mode (a reply), not to another scheduled
# follow-up. See decide().
CONTINUING = {"NOT_NOW", "OUT_OF_OFFICE", "LEFT_COMPANY"}
# Intents that mean a human should take it from here.
HANDOFF = {"INTERESTED", "QUESTION", "DEMO_SCHEDULED"}
CONVERSATIONAL = {"INTERESTED", "QUESTION"}


def _body_hash(body: str) -> str:
    return hashlib.sha256((body or "").strip().lower().encode("utf-8")).hexdigest()[:32]


def persist_inbound(
    db: Session, *, workspace_id: int, lead_id: int, body: Optional[str],
    variant: str = "dm", intent: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Optional[int]:
    """Store the prospect's message. Returns the row id, or None if we've already
    stored this exact message.

    Dedup matters: the same thread is re-read on every reply gate and every inbox
    sweep, so a naive insert would create a duplicate row per check. The key is
    (lead, body-hash) — the partial unique index on this table covers only
    OUTBOUND rows precisely because repeated inbound messages are legitimate.
    """
    if not body or not body.strip():
        return None
    h = _body_hash(body)
    try:
        dup = db.execute(
            _sql(
                "SELECT id FROM nexus_linkedin_messages "
                " WHERE workspace_id = :w AND lead_id = :l AND direction = 'inbound' "
                "   AND md5(lower(btrim(body))) = md5(lower(btrim(:b))) "
                " LIMIT 1"
            ),
            {"w": workspace_id, "l": lead_id, "b": body},
        ).first()
        if dup:
            logger.debug("replies: inbound already stored for lead %s (hash %s)", lead_id, h)
            return int(dup[0])

        row = db.execute(
            _sql(
                "INSERT INTO nexus_linkedin_messages "
                "  (workspace_id, lead_id, direction, body, variant, step, intent, intent_confidence, sent_at) "
                "VALUES (:w, :l, 'inbound', :b, :v, 0, :i, :c, NOW()) "
                "RETURNING id"
            ),
            {"w": workspace_id, "l": lead_id, "b": body, "v": variant,
             "i": intent, "c": confidence},
        ).first()
        db.commit()
        msg_id = int(row[0]) if row else None
        logger.info("replies: stored inbound message %s for lead %s (%d chars, intent=%s)",
                    msg_id, lead_id, len(body), intent)
        return msg_id
    except Exception:  # noqa: BLE001 — never block the sequence on bookkeeping
        logger.warning("replies: failed to persist inbound for lead %s", lead_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def classify(body: Optional[str]) -> Dict[str, Any]:
    """Classify a LinkedIn reply. Never raises.

    Returns {intent, confidence, reasoning, actionable}. `actionable` is the
    single gate the engine reads: False means "do not branch on this", whatever
    the intent says.
    """
    if not body or not body.strip():
        return {"intent": None, "confidence": 0.0, "reasoning": "empty body", "actionable": False}
    try:
        from nexus.services.intent_classifier import classify as _classify
        res = _classify(body, channel="linkedin")
        conf = float(getattr(res, "confidence", 0.0) or 0.0)
        intent = getattr(res, "intent", None)
        return {
            "intent": intent,
            "confidence": conf,
            "reasoning": getattr(res, "reasoning", None),
            "actionable": bool(intent) and conf >= MIN_CONFIDENCE,
        }
    except Exception:  # noqa: BLE001
        logger.warning("replies: classification failed — treating as unactionable", exc_info=True)
        return {"intent": None, "confidence": 0.0, "reasoning": "classifier error", "actionable": False}


def decide(classification: Dict[str, Any], *, body: Optional[str] = None,
           received_at: Any = None) -> Dict[str, Any]:
    """Intent → plan operations. Pure: no DB, no side effects, fully testable.

    Returns:
        {
          "action":   "stop" | "continue" | "converse",
          "reason":   str,                # audit / UI copy
          "ops":      [...],              # plan operations for the engine
          "suppress": bool,               # add to the suppression list
          "handoff":  bool,               # flag for a human
        }
    """
    if not classification.get("actionable"):
        # The default, and the landing point for every failure mode.
        return {"action": "stop", "reason": "unclassified",
                "ops": ["truncate"], "suppress": False, "handoff": True}

    intent = (classification.get("intent") or "").upper()

    if intent == "UNSUBSCRIBE":
        return {"action": "stop", "reason": "unsubscribe",
                "ops": ["truncate"], "suppress": True, "handoff": False}

    if intent == "NOT_INTERESTED":
        return {"action": "stop", "reason": "not_interested",
                "ops": ["truncate"], "suppress": False, "handoff": False}

    if intent in CONVERSATIONAL:
        # They engaged. Suspend the cadence and answer them — a scheduled
        # follow-up landing on top of a live reply is the bot tell we most want
        # to avoid. The turn cap and hand-off gate live in the engine.
        return {"action": "converse", "reason": intent.lower(),
                "ops": ["truncate", "insert_reply"], "suppress": False, "handoff": False}

    if intent == "DEMO_SCHEDULED":
        return {"action": "stop", "reason": "demo_scheduled",
                "ops": ["truncate"], "suppress": False, "handoff": True}

    if intent == "OUT_OF_OFFICE":
        days = _ooo_days(body, received_at)
        return {"action": "continue", "reason": "out_of_office",
                "ops": [("retime", days), "mark_stale"], "suppress": False, "handoff": False}

    if intent == "NOT_NOW":
        # They didn't say no — they said later. Push the next touch out well
        # past the default gap rather than dropping them.
        return {"action": "continue", "reason": "not_now",
                "ops": [("retime", 30), "mark_stale"], "suppress": False, "handoff": False}

    if intent == "LEFT_COMPANY":
        # Keep the thread alive but stop pitching the company they left; the
        # rewrite regrounds the remaining copy.
        return {"action": "continue", "reason": "left_company",
                "ops": ["mark_stale"], "suppress": False, "handoff": True}

    # A valid-but-unhandled intent. Stop rather than fall through to a default.
    logger.warning("replies: unhandled intent %r — stopping", intent)
    return {"action": "stop", "reason": f"unhandled:{intent}".lower()[:48],
            "ops": ["truncate"], "suppress": False, "handoff": True}


def _ooo_days(body: Optional[str], received_at: Any) -> int:
    """Days to push the next touch for an out-of-office reply.

    Prefers an explicitly stated return date, then a relative duration, then a
    conservative default. Both extractors are pure text functions reused from the
    email side — nothing about parsing "back on the 14th" is channel-specific.
    """
    default = 7
    if not body:
        return default
    try:
        from datetime import datetime
        from nexus.services.intent_classifier import (
            extract_ooo_return_date, extract_ooo_return_delay_days,
        )
        anchor = received_at or datetime.utcnow()
        d = extract_ooo_return_date(body, anchor)
        if d is not None:
            delta = (d - anchor.date()).days + 1   # the morning after they're back
            return max(1, min(120, delta))
        rel = extract_ooo_return_delay_days(body)
        if rel:
            return max(1, min(120, int(rel) + 1))
    except Exception:  # noqa: BLE001
        logger.debug("replies: OOO date extraction failed — using default", exc_info=True)
    return default
