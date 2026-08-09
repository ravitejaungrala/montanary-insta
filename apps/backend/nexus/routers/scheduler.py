"""EventBridge-triggered Nexus scheduler endpoints.

All endpoints require the `X-Scheduler-Secret` header to match
`core.config.SCHEDULER_SECRET`. They are intended to be poked by the
existing Pipelyt EventBridge -> `lambda_pinger.py` infrastructure.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import SCHEDULER_SECRET
from core.database import get_db

log = logging.getLogger("nexus.scheduler")

# Module-level throttle for MS Bookings polling. EventBridge fires this tick
# every minute, but Graph appointment listings don't need that resolution —
# human-paced demo bookings show up within 5 minutes either way. Persists
# across Lambda invocations on a warm container; resets on cold start (also
# fine — cold start means a fresh sync runs immediately).
_MS_BOOKINGS_SYNC_MIN_INTERVAL_SEC = 5 * 60
_ms_bookings_last_sync_ts: float = 0.0

# MS Outlook inbound-mail polling. Same throttle reasoning as bookings —
# every minute is overkill for human-paced replies; 5 min keeps Graph quotas
# happy on warm Lambdas.
_MS_MAIL_SYNC_MIN_INTERVAL_SEC = 5 * 60
_ms_mail_last_sync_ts: float = 0.0


def _verify_secret(x_scheduler_secret: str = Header(None)):
    if not SCHEDULER_SECRET or x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Manual-upload coverage watchdog ─────────────────────────────────────────
#
# Counts manually-uploaded leads (source='manual_upload') that still don't
# have an outbound email touchpoint OR a LinkedIn DM/InMail draft. Runs on
# every sequencer tick — so for as long as the EventBridge schedule keeps
# firing this Lambda (the whole life of the deploy, not just 25 min), we
# get a per-minute readout of "how many manual leads are still pending."
#
# Why this matters: the user wanted a guarantee that "all 100 leads' email
# + LinkedIn DM + InMail are generated, no one missed." The DB transaction
# guarantees the leads land. The sequencer's source-agnostic candidate
# query (after the company-dedup bypass fix in sequencer.py) guarantees
# every manual lead is eligible. But if something else regresses — a
# Gemini quota outage, a sender misconfig, a future code change that
# accidentally filters them out — this watchdog surfaces it instantly
# instead of leaving the operator to discover it via the GTM Journey UI
# showing "in 0" for hours.
#
# WARNING levels:
#   • 0 pending  → INFO ("manual-upload coverage healthy")
#   • >0 pending → WARNING with the breakdown so on-call can grep logs
def _manual_upload_backlog_health(db: Session) -> dict:
    """Return counts of manual_upload leads still missing email / LinkedIn.

    Best-effort. Any query failure is swallowed so a one-off DB hiccup
    never poisons the sequencer tick — the next tick re-runs the check.
    """
    try:
        # Active manual_upload leads with NO outbound email touchpoint yet.
        # We scope to lead_sequences in 'active'/'queued' status so leads
        # that completed (status='completed') or died (status='dead' for a
        # known reason like suppressed) don't show up as a phantom backlog.
        no_email = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT ls.lead_id)
                FROM nexus_lead_sequences ls
                JOIN nexus_global_leads gl ON gl.id = ls.lead_id
                WHERE COALESCE(gl.source, '') = 'manual_upload'
                  AND ls.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM nexus_touchpoints tp
                      WHERE tp.lead_id = ls.lead_id
                        AND tp.channel LIKE 'email%'
                        AND tp.status = 'sent'
                  )
                """
            )
        ).scalar() or 0

        # Manual_upload leads with NO outbound LinkedIn message (DM or InMail).
        no_linkedin = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT gl.id)
                FROM nexus_global_leads gl
                WHERE COALESCE(gl.source, '') = 'manual_upload'
                  AND EXISTS (
                      SELECT 1 FROM nexus_lead_sequences ls
                      WHERE ls.lead_id = gl.id AND ls.status = 'active'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM nexus_linkedin_messages lim
                      WHERE lim.lead_id = gl.id
                        AND lim.direction = 'outbound'
                  )
                """
            )
        ).scalar() or 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "nexus.scheduler: manual-upload backlog check failed (non-fatal): %s",
            exc,
        )
        return {"pending_email": None, "pending_linkedin": None, "error": str(exc)[:200]}

    pending_email = int(no_email)
    pending_linkedin = int(no_linkedin)
    if pending_email == 0 and pending_linkedin == 0:
        log.info(
            "nexus.scheduler: manual-upload coverage healthy — all uploaded "
            "leads have email + LinkedIn generated."
        )
    else:
        log.warning(
            "nexus.scheduler: manual-upload backlog — pending_email=%d "
            "pending_linkedin=%d. Sequencer will keep draining at "
            "DEFAULT_MAX_ROWS_PER_TICK + LINKEDIN_DRAFTS_PER_TICK per tick. "
            "If these numbers don't drop across consecutive ticks, "
            "investigate sender headroom (no_email) or Gemini quota "
            "(no_linkedin).",
            pending_email, pending_linkedin,
        )
    return {"pending_email": pending_email, "pending_linkedin": pending_linkedin}


router = APIRouter(
    prefix="/nexus/scheduler",
    tags=["Nexus — Scheduler"],
)


@router.post("/sequencer/tick", dependencies=[Depends(_verify_secret)])
async def sequencer_tick(db: Session = Depends(get_db)):
    """Drain the LeadSequence work queue and refresh LinkedIn drafts.

    Three passes per tick (third is throttled to once every ~5 min):
      1. process_due_sequences — send queued emails
      2. process_linkedin_drafts — auto-generate outbound LinkedIn DMs
         for any newly discovered leads that don't have one yet.
         Capped per tick so it doesn't blow the Lambda budget.
      3. sync_ms_bookings — pull MS Bookings appointments via Graph API
         and upsert into nexus_demo_bookings so the calendar reflects them.
         Throttled so warm Lambda containers don't hammer Graph.
    """
    from nexus.services.sequencer import (
        process_due_sequences,
        process_linkedin_drafts,
    )

    # Each top-level pass is isolated so one failure can't abort the whole
    # heartbeat — the downstream sweeps (intent scoring, inbound reply, etc.)
    # and the next tick still run. On error we roll back to keep the shared
    # session usable for the passes that follow.
    try:
        email_result = process_due_sequences(db)
    except Exception as e:  # noqa: BLE001
        log.exception("process_due_sequences failed")
        try:
            db.rollback()
        except Exception:
            pass
        email_result = {"error": str(e)}
    try:
        linkedin_result = process_linkedin_drafts(db)
    except Exception as e:  # noqa: BLE001
        log.exception("process_linkedin_drafts failed")
        try:
            db.rollback()
        except Exception:
            pass
        linkedin_result = {"error": str(e)}

    # GTM LinkedIn Agent — dispatch due connection/message/inmail jobs to SQS.
    # Lightweight (no Playwright); the worker Lambda runs the browser. Gated by
    # NEXUS_LINKEDIN_ENABLED so it's a no-op until LinkedIn is turned on.
    gtm_linkedin_result = None
    try:
        from core.config import NEXUS_LINKEDIN_ENABLED as _LI_ENABLED
        if _LI_ENABLED:
            from gtm.linkedin.scheduler import tick as _gtm_li_tick
            gtm_linkedin_result = _gtm_li_tick(db)
    except Exception as e:  # noqa: BLE001
        log.exception("gtm_linkedin dispatch failed")
        try:
            db.rollback()
        except Exception:
            pass
        gtm_linkedin_result = {"error": str(e)}

    try:
        manual_backlog = _manual_upload_backlog_health(db)
    except Exception as e:  # noqa: BLE001
        log.exception("manual_upload_backlog health failed")
        try:
            db.rollback()
        except Exception:
            pass
        manual_backlog = {"error": str(e)}

    bookings_result = None
    briefings_result = None
    discovery_sweep_result = None
    intent_sweep_result = None
    global _ms_bookings_last_sync_ts
    now_ts = time.time()
    if now_ts - _ms_bookings_last_sync_ts >= _MS_BOOKINGS_SYNC_MIN_INTERVAL_SEC:
        _ms_bookings_last_sync_ts = now_ts
        try:
            from nexus.services.ms_bookings_sync import (
                process_demo_briefings,
                sync_ms_bookings,
            )

            bookings_result = await sync_ms_bookings(db)
            briefings_result = process_demo_briefings(db, max_rows=3)
        except Exception as e:  # noqa: BLE001
            log.exception("ms_bookings sync failed")
            bookings_result = {"error": str(e)}

        # Discovery retry sweep — campaigns that timed out / returned zero
        # leads during /analyze get flagged with icp.discovery_pending=true;
        # this sweep retries them. Cheap when there are no pending rows.
        try:
            from nexus.services.discovery_sweep import process_pending_discoveries
            discovery_sweep_result = await process_pending_discoveries(db, max_rows=2)
        except Exception as e:  # noqa: BLE001
            log.exception("discovery_sweep failed")
            discovery_sweep_result = {"error": str(e)}

    # Intent-gate sweep — score discovered leads with Agent #10 and enroll the
    # accepted ones. Runs on EVERY tick (not gated by the bookings throttle) so
    # a freshly-launched campaign starts scoring on the next tick rather than
    # waiting up to 5 min. Companies within a campaign are scored concurrently,
    # so this drains a normal campaign in ~1-2 min. No-op when the gate is off
    # or nothing is pending (one cheap claim query).
    try:
        from nexus.services.intent_sweep import process_pending_intent
        intent_sweep_result = await process_pending_intent(db)
    except Exception as e:  # noqa: BLE001
        log.exception("intent_sweep failed")
        intent_sweep_result = {"error": str(e)}

    # MS Outlook inbound-mail poll (independent of bookings — different
    # cadence is fine and avoids one feature's failure starving the other).
    mail_result = None
    connector_mail_result = None
    global _ms_mail_last_sync_ts
    if now_ts - _ms_mail_last_sync_ts >= _MS_MAIL_SYNC_MIN_INTERVAL_SEC:
        _ms_mail_last_sync_ts = now_ts
        try:
            from nexus.services.ms_mail_sync import sync_ms_inbound_mail
            mail_result = await sync_ms_inbound_mail(db)
        except Exception as e:  # noqa: BLE001
            log.exception("ms_mail sync failed")
            mail_result = {"error": str(e)}
        # Reply agent over each CONNECTED mailbox (Outlook OAuth). Separate
        # try so one path's failure doesn't starve the other.
        try:
            from nexus.services.connectors.inbound_sync import sync_connected_inbound
            connector_mail_result = sync_connected_inbound(db)
        except Exception as e:  # noqa: BLE001
            log.exception("connector inbound sync failed")
            connector_mail_result = {"error": str(e)}

    return {
        "email": email_result,
        "linkedin": linkedin_result,
        "gtm_linkedin": gtm_linkedin_result,
        "bookings": bookings_result,
        "briefings": briefings_result,
        "discovery_sweep": discovery_sweep_result,
        "intent_sweep": intent_sweep_result,
        "ms_mail": mail_result,
        "connector_mail": connector_mail_result,
        # Manual-upload watchdog snapshot — non-zero pending counts here
        # mean the sequencer still has uploaded leads to drain. Monitoring
        # / dashboards can alert when these stay >0 for multiple ticks.
        "manual_upload_backlog": manual_backlog,
    }


@router.post("/automation/tick", dependencies=[Depends(_verify_secret)])
def automation_tick(db: Session = Depends(get_db)):
    """Fire any due automation rows (one_time + daily)."""
    from nexus.services.automation_runner import process_due_automations

    return process_due_automations(db)


@router.post("/daily-cap-reset", dependencies=[Depends(_verify_secret)])
def daily_cap_reset(db: Session = Depends(get_db)):
    """Reset per-account daily send counters on the IST day boundary.

    IST-based and IDEMPOTENT: only zeroes mailboxes whose counter is from an
    EARLIER India day, using the SAME boundary as the sequencer's lazy reset
    (_lazy_daily_reset) so the two can never disagree. Safe to call repeatedly
    or a few minutes late — each mailbox resets exactly once per IST day and a
    counter the lazy path already reset today is skipped (no double-reset, so
    no mailbox can exceed its 50/day cap).
    """
    try:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        result = db.execute(
            text(
                "UPDATE nexus_conversation_accounts "
                "SET daily_send_count = 0, last_reset_at = :now "
                "WHERE last_reset_at IS NULL "
                "   OR (last_reset_at + interval '5 hours 30 minutes')::date "
                "      < (:now + interval '5 hours 30 minutes')::date"
            ),
            {"now": now_utc},
        )
        db.commit()
        return {"reset_count": result.rowcount or 0}
    except Exception as e:
        db.rollback()
        log.exception("daily_cap_reset failed")
        # If the table isn't there yet (pre-Phase 4 migration), return 0.
        return {"reset_count": 0, "error": str(e)}


@router.post("/daily-pipeline", dependencies=[Depends(_verify_secret)])
async def daily_pipeline(db: Session = Depends(get_db)):
    """Nightly autonomous-discovery + enrichment batch.
    Legacy: services/dailyPipeline.js runDailyPipeline().
    Wired to nexus.services.daily_pipeline.run_daily_pipeline (stub for now)."""
    try:
        from nexus.services.daily_pipeline import run_daily_pipeline
        return await run_daily_pipeline()
    except Exception as e:  # noqa: BLE001
        log.exception("daily_pipeline failed")
        return {"ok": False, "error": str(e)}


@router.post("/daily-report", dependencies=[Depends(_verify_secret)])
async def daily_report(db: Session = Depends(get_db)):
    """Email each opted-in user their previous-day digest.
    Legacy: services/dailyReport.js runDailyReport().
    Wired to nexus.services.daily_report.run_daily_report (stub for now)."""
    try:
        from nexus.services.daily_report import run_daily_report
        return await run_daily_report()
    except Exception as e:  # noqa: BLE001
        log.exception("daily_report failed")
        return {"ok": False, "error": str(e)}


@router.post("/credit-log-cleanup", dependencies=[Depends(_verify_secret)])
def credit_log_cleanup(db: Session = Depends(get_db)):
    """Archive credit-log rows older than 90 days.
    Legacy: weekly cron in services/scheduler.js."""
    try:
        result = db.execute(
            text(
                "DELETE FROM nexus_credit_logs WHERE created_at < NOW() - INTERVAL '90 days'"
            )
        )
        db.commit()
        return {"deleted": result.rowcount or 0}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("credit_log_cleanup failed")
        return {"deleted": 0, "error": str(e)}


@router.post("/monthly-usage-reset", dependencies=[Depends(_verify_secret)])
def monthly_usage_reset(db: Session = Depends(get_db)):
    """Reset workspace.usage counters at the start of each IST month.

    Affects: leads_this_month, emails_this_month, tokens_this_month,
    campaigns_active. Implemented as a JSONB update on
    `nexus_workspaces.usage`.

    IDEMPOTENT: stamps the IST month (YYYY-MM) into usage.reset_month and only
    resets workspaces whose stamp isn't the current IST month. So a MISSED
    midnight tick self-heals — ANY tick that month performs the reset exactly
    once, and repeat calls are cheap no-ops (0 rows). No more "one exact minute
    or the whole month's quota never resets".
    """
    try:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        ist_month = now_ist.strftime("%Y-%m")
        # Reset the four counters + stamp the IST month, leaving the rest of
        # `usage` intact. WHERE-guard makes it reset each workspace once/month.
        sql = text(
            """
            UPDATE nexus_workspaces
            SET usage = COALESCE(usage, '{}'::jsonb)
                || jsonb_build_object(
                    'leads_this_month', 0,
                    'emails_this_month', 0,
                    'tokens_this_month', 0,
                    'campaigns_active', 0,
                    'reset_month', :ism
                )
            WHERE COALESCE(usage->>'reset_month', '') <> :ism
            """
        )
        result = db.execute(sql, {"ism": ist_month})
        db.commit()
        return {"reset_count": result.rowcount or 0}
    except Exception as e:
        db.rollback()
        log.exception("monthly_usage_reset failed")
        return {"reset_count": 0, "error": str(e)}
