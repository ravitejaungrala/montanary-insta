"""Automation tick processor.

`Automation` rows represent recurring (or one-shot) jobs that trigger
lead discovery (Phase 3). The runner is called by
`/nexus/scheduler/automation/tick`.

DST-safe scheduling is implemented via `zoneinfo`: we compute the next
fire time in the automation's own timezone and convert back to UTC
naive for storage, so a daily 09:00 LA run stays at 09:00 LA across DST
boundaries.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.automation_runner")

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compute_next_run_at(
    last_run: Optional[datetime],
    schedule_type: str,
    tz: str = "UTC",
) -> Optional[datetime]:
    """Compute the next fire time in UTC (naive) for the given schedule.

    - `daily`: same local time tomorrow, in the automation's tz.
    - `one_time`: returns None (one-shot is consumed by setting status).

    `last_run` should be a UTC naive datetime (as stored by SQLAlchemy
    without tzinfo). If None we anchor on "now".
    """
    if schedule_type == "one_time":
        return None
    if schedule_type != "daily":
        # Unknown schedule — default to daily semantics.
        log.info("automation_runner: unknown schedule_type %s — daily fallback", schedule_type)

    anchor_utc = last_run or _utcnow()
    if anchor_utc.tzinfo is None:
        anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)

    if ZoneInfo is None:
        # zoneinfo unavailable — best-effort +24h UTC.
        return (anchor_utc + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")

    local_anchor = anchor_utc.astimezone(zone)
    # Preserve the local time-of-day, shift to tomorrow.
    target_local = (local_anchor + timedelta(days=1)).replace(
        second=0, microsecond=0
    )
    # Re-anchor at the same hour:minute (handles edge cases where
    # local_anchor was, say, an hour stomped by DST):
    target_local = datetime.combine(
        target_local.date(),
        time(hour=local_anchor.hour, minute=local_anchor.minute),
        tzinfo=zone,
    )
    return target_local.astimezone(timezone.utc).replace(tzinfo=None)


def _table_exists(db: Session, name: str) -> bool:
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :n LIMIT 1"
            ),
            {"n": name},
        ).first()
        return row is not None
    except Exception:
        return False


def _trigger_discovery(
    db: Session,
    workspace_id: int,
    campaign_id: Optional[int],
    target_leads: int,
) -> Dict[str, Any]:
    """Lazy-import Phase 3 lead discovery and kick it off."""
    try:
        from nexus.services import lead_discovery  # type: ignore

        fn = getattr(lead_discovery, "discover_leads", None) or getattr(
            lead_discovery, "run_discovery", None
        )
        if fn is None:
            return {"ok": False, "error": "lead_discovery entrypoint not found"}
        out = fn(
            db=db,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            target_leads=target_leads,
        )
        return {"ok": True, "result": out}
    except ImportError:
        return {"ok": False, "error": "phase 3 lead_discovery not yet merged"}
    except Exception as e:
        log.exception("automation_runner: discovery trigger failed")
        return {"ok": False, "error": str(e)}


def process_due_automations(db: Session) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "count": 0,
        "triggered": 0,
        "errors": 0,
        "details": [],
    }

    if not _table_exists(db, "nexus_automations"):
        log.info("automation_runner: nexus_automations absent — skip")
        return result

    from nexus.models_phase4 import NexusAutomation

    now = _utcnow()
    due: List[NexusAutomation] = (
        db.query(NexusAutomation)
        .filter(
            NexusAutomation.status == "active",
            NexusAutomation.next_run_at != None,  # noqa: E711
            NexusAutomation.next_run_at <= now,
        )
        .limit(200)
        .all()
    )
    result["count"] = len(due)

    for auto in due:
        try:
            trigger = _trigger_discovery(
                db,
                workspace_id=auto.workspace_id,
                campaign_id=auto.campaign_id,
                target_leads=auto.target_leads or 100,
            )
            auto.last_run_at = now
            auto.run_count = (auto.run_count or 0) + 1

            # Best-effort lead-count extraction from the discovery service's
            # return shape — different entrypoints use different keys.
            leads_this_run = 0
            inner = trigger.get("result") if isinstance(trigger, dict) else None
            if isinstance(inner, dict):
                for k in ("leads_added", "added", "count", "leads"):
                    v = inner.get(k)
                    if isinstance(v, int):
                        leads_this_run = v
                        break
            auto.leads_generated = (auto.leads_generated or 0) + leads_this_run

            if auto.schedule_type == "one_time":
                auto.status = "completed"
                auto.next_run_at = None
            else:
                auto.next_run_at = compute_next_run_at(
                    last_run=now,
                    schedule_type=auto.schedule_type,
                    tz=auto.tz or "UTC",
                )
            if trigger.get("ok"):
                auto.last_run_status = "ok"
                auto.last_run_summary = f"leads_added={leads_this_run}"
                result["triggered"] += 1
            else:
                auto.last_run_status = "failed"
                auto.last_run_summary = (trigger.get("error") or "")[:480]
                result["errors"] += 1
            result["details"].append(
                {"automation_id": auto.id, "result": trigger}
            )
        except Exception as e:
            log.exception("automation_runner: row failed id=%s", auto.id)
            auto.last_run_status = "failed"
            auto.last_run_summary = str(e)[:480]
            result["errors"] += 1
            result["details"].append({"automation_id": auto.id, "error": str(e)})

    try:
        db.commit()
    except Exception:
        log.exception("automation_runner: commit failed")
        db.rollback()
        result["errors"] += 1

    return result
