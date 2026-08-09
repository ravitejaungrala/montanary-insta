"""Background discovery retry sweep.

Why this exists:
    The `POST /nexus/analyze` handler runs discovery synchronously, capped
    at 30s. Under Lambda's 30s API-Gateway ceiling that's often not enough,
    especially when Apollo's q_keywords self-heal fires. Before this sweep,
    a timeout left the campaign EMPTY forever (its `discovery_pending` flag
    in icp had no consumer). The campaign then disappeared from GTM Journey
    via the zero-lead filter — operator never saw it broke.

    This module is that consumer. Wired into the sequencer scheduler tick
    (same surface as MS Bookings sync), runs once every ~5 minutes:
      1. find campaigns with `icp.discovery_pending = true`
      2. retry discovery with the same `discover_for_campaign` helper
      3. on success: clear the flag, leads land, campaign re-appears in UI
      4. on still-zero / still-timeout: leave the flag set, log a warning,
         next tick tries again — up to N times before giving up.

Public surface:
    process_pending_discoveries(db, max_rows=2) -> dict
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("pipelyt.nexus.discovery_sweep")

# Per-tick cap on retries. Each retry takes ~20-60s synchronously, so 2 per
# tick keeps us comfortably under the Lambda timeout AND limits Apollo /
# Hunter / GitHub credit burn from misconfigured campaigns.
DEFAULT_MAX_ROWS_PER_TICK = 2

# After N consecutive retry failures, stop trying. Otherwise a permanently-
# broken config (bad URL, dead Apollo credentials, etc.) would burn API
# credits every tick forever.
MAX_CONSECUTIVE_FAILURES = 5

# Same budget as the synchronous /analyze path (raised 60 → 600 in
# the 2026-05-29 dedup + adaptive-yield refactor — see analyze.py).
DISCOVERY_TIMEOUT_SEC = 600.0
# Locked at 20 to match routers/analyze.py:DISCOVERY_MAX_LEADS (2026-05-29).
# The sweep runs on a scheduler tick to top-up campaigns that didn't hit
# the per-run target on the initial Launch — keeping the cap aligned
# avoids one path overshooting the other.
DISCOVERY_MAX_LEADS = 20


async def process_pending_discoveries(
    db: Session, max_rows: int = DEFAULT_MAX_ROWS_PER_TICK
) -> Dict[str, Any]:
    """Find campaigns flagged as `discovery_pending` and retry discovery.

    Returns {claimed, succeeded, failed, errors}.
    """
    result: Dict[str, Any] = {
        "claimed": 0,
        "succeeded": 0,
        "failed": 0,
        "errors": [],
    }

    try:
        rows = db.execute(
            text(
                """SELECT id, workspace_id, product_id, icp, name
                     FROM nexus_campaigns
                    WHERE (icp ->> 'discovery_pending') = 'true'
                      AND status = 'active'
                    ORDER BY id ASC
                    LIMIT :cap"""
            ),
            {"cap": max(1, int(max_rows))},
        ).mappings().fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("discovery_sweep: claim query failed: %s", e)
        db.rollback()
        return result

    if not rows:
        return result

    # Lazy import — circular if loaded at module level (discover_for_campaign
    # itself imports analyze helpers).
    from nexus.models_phase3 import NexusCampaign
    from nexus.services.discover_for_campaign import discover_for_campaign

    result["claimed"] = len(rows)

    for r in rows:
        camp_id = r["id"]
        icp = dict(r.get("icp") or {})
        failure_count = int(icp.get("discovery_failure_count") or 0)

        if failure_count >= MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "discovery_sweep: campaign %s exceeded %s retries — giving up. "
                "Operator can clear `icp.discovery_failure_count` to retry.",
                camp_id,
                MAX_CONSECUTIVE_FAILURES,
            )
            continue

        campaign = (
            db.query(NexusCampaign).filter(NexusCampaign.id == camp_id).first()
        )
        if not campaign:
            continue

        logger.info(
            "BACKGROUND RETRY — campaign %s ended with 0 leads earlier, so "
            "discovery is retried automatically: attempt %d of %d. After %d "
            "failed attempts the system STOPS retrying (no endless credit "
            "spend); an operator can reset `icp.discovery_failure_count` to "
            "try again.",
            camp_id, failure_count + 1, MAX_CONSECUTIVE_FAILURES,
            MAX_CONSECUTIVE_FAILURES,
        )
        try:
            res = await asyncio.wait_for(
                discover_for_campaign(
                    db,
                    campaign,
                    max_leads=DISCOVERY_MAX_LEADS,
                    min_icp_score=0,
                ),
                timeout=DISCOVERY_TIMEOUT_SEC,
            )
            leads_attached = int(res.get("leads_attached", 0))
        except asyncio.TimeoutError:
            logger.info(
                "discovery_sweep: campaign %s still timing out", camp_id
            )
            leads_attached = 0
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "discovery_sweep: campaign %s discovery raised: %s",
                camp_id,
                exc,
            )
            leads_attached = 0
            result["errors"].append({"campaign_id": camp_id, "error": str(exc)[:200]})

        if leads_attached > 0:
            # Clear the pending flag — campaign now has leads.
            try:
                new_icp = dict(campaign.icp or {})
                new_icp.pop("discovery_pending", None)
                new_icp.pop("discovery_pending_reason", None)
                new_icp.pop("discovery_pending_at", None)
                new_icp.pop("discovery_failure_count", None)
                new_icp["discovery_completed_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                campaign.icp = new_icp
                db.commit()
                result["succeeded"] += 1
                logger.info(
                    "discovery_sweep: campaign %s recovered with %s leads",
                    camp_id,
                    leads_attached,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "discovery_sweep: campaign %s clear-flag commit failed: %s",
                    camp_id,
                    exc,
                )
                db.rollback()
        else:
            # Still zero leads — bump the failure counter; sweep will give
            # up after MAX_CONSECUTIVE_FAILURES retries.
            try:
                new_icp = dict(campaign.icp or {})
                new_icp["discovery_failure_count"] = failure_count + 1
                new_icp["discovery_last_attempt_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                campaign.icp = new_icp
                db.commit()
                result["failed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "discovery_sweep: campaign %s bump-failure commit failed: %s",
                    camp_id,
                    exc,
                )
                db.rollback()

    if result["succeeded"] or result["failed"]:
        logger.info(
            "discovery_sweep tick: claimed=%s succeeded=%s failed=%s",
            result["claimed"],
            result["succeeded"],
            result["failed"],
        )
    return result
