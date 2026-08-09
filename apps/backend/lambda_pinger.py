import urllib.request
import os
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _post(endpoint: str, headers: dict, timeout: int = 30) -> dict:
    """Fire a POST to `endpoint`. Returns {ok, status, body, error}."""
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps({}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.getcode(), "body": resp.read().decode("utf-8")}
    except Exception as exc:
        return {"ok": False, "status": None, "body": None, "error": str(exc)}


def lambda_handler(event, context):
    """
    Heartbeat Lambda — fires every minute via EventBridge.

    Calls:
      1. /scheduler/process            — PIPELYT post scheduler (existing)
      2. /nexus/scheduler/sequencer/tick     — drain due email sequences
      3. /nexus/scheduler/automation/tick    — fire due automations
      4. /nexus/scheduler/daily-cap-reset    — reset per-account daily counters
                                               (only on the first invocation of each UTC day)
      5. /nexus/scheduler/monthly-usage-reset — reset monthly workspace quotas
                                               (only on the 1st of each UTC month)
    """
    backend_url = os.environ.get("BACKEND_URL")
    if not backend_url:
        logger.error("BACKEND_URL environment variable is NOT SET.")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "BACKEND_URL environment variable is missing"}),
        }

    base = backend_url.rstrip("/")
    scheduler_secret = os.environ.get("SCHEDULER_SECRET", "")
    nexus_enabled = os.environ.get("NEXUS_ENABLED", "false").strip().lower() == "true"
    linkedin_enabled = os.environ.get("NEXUS_LINKEDIN_ENABLED", "false").strip().lower() == "true"

    headers = {
        "Content-Type": "application/json",
        "X-Scheduler-Secret": scheduler_secret,
    }

    now_utc = datetime.now(timezone.utc)
    # Day boundary is INDIA time (IST = UTC+5:30) — used below for every
    # once-a-day/week/month gated job (weekly report, daily cap reset,
    # monthly usage reset).
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    results = {}

    # ── 1. PIPELYT post scheduler (always) ───────────────────────────────────
    r = _post(f"{base}/scheduler/process", headers)
    results["pipelyt_scheduler"] = r
    if r["ok"]:
        logger.info("PIPELYT scheduler ok — status %s", r["status"])
    else:
        logger.error("PIPELYT scheduler failed: %s", r.get("error"))

    # ── 1b. PIPELYT weekly dashboard-stats email report ──────────────────────
    # Monday, first ~10 minutes of the 9am IST hour. Not Nexus-specific, so
    # this must run BEFORE the `if not nexus_enabled: return` below —
    # otherwise Pipelyt-only deployments (NEXUS_ENABLED=false) would never
    # send it. run_weekly_reports() is itself idempotent per-user (skips
    # anyone emailed in the last 6 days via last_weekly_report_sent_at), so
    # firing this endpoint a few times inside the window is safe.
    if now_ist.weekday() == 0 and now_ist.hour == 9 and now_ist.minute < 10:
        r = _post(f"{base}/scheduler/weekly-report", headers)
        results["pipelyt_weekly_report"] = r
        if r["ok"]:
            logger.info("PIPELYT weekly report ok — %s", r["body"])
        else:
            logger.warning("PIPELYT weekly report failed: %s", r.get("error"))

    if not nexus_enabled:
        logger.info("NEXUS_ENABLED=false — skipping NEXUS ticks")
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Heartbeat ok (NEXUS disabled)", "results": results}),
        }

    # ── 2. NEXUS sequencer tick (every minute) ───────────────────────────────
    # This pass does real work synchronously — email sends + LinkedIn drafts
    # (~75s for a full batch of AI copy) + intent/mail/bookings sweeps — so it
    # can run well past the default 30s. Give it a generous read timeout so the
    # heartbeat doesn't log a false "failed" while the backend is still working.
    # NOTE: the Lambda's own function timeout must be >= this (set to >= 240s).
    r = _post(f"{base}/nexus/scheduler/sequencer/tick", headers, timeout=180)
    results["nexus_sequencer"] = r
    if r["ok"]:
        logger.info("NEXUS sequencer tick ok — %s", r["body"])
    else:
        logger.warning("NEXUS sequencer tick failed: %s", r.get("error"))

    # ── 3. NEXUS automation tick (every minute) ──────────────────────────────
    r = _post(f"{base}/nexus/scheduler/automation/tick", headers)
    results["nexus_automations"] = r
    if r["ok"]:
        logger.info("NEXUS automation tick ok — %s", r["body"])
    else:
        logger.warning("NEXUS automation tick failed: %s", r.get("error"))

    # ── 3b. GTM LinkedIn dispatch tick (every minute) ────────────────────────
    # Auto-pause sweep + dispatch due LinkedIn jobs to SQS (the app side does NO
    # browser work — the Playwright worker Lambda drains SQS). Gated separately so
    # it only fires when the LinkedIn agent is enabled.
    if linkedin_enabled:
        r = _post(f"{base}/nexus/linkedin-agent/scheduler/tick", headers)
        results["gtm_linkedin_tick"] = r
        if r["ok"]:
            logger.info("GTM LinkedIn tick ok — %s", r["body"])
        else:
            logger.warning("GTM LinkedIn tick failed: %s", r.get("error"))

    # Day boundary is INDIA time (IST = UTC+5:30) — the same boundary the
    # sequencer uses for the per-mailbox daily cap. Both reset endpoints are
    # IDEMPOTENT (they check the stored IST date/month themselves and only act
    # when it's stale), so we fire them across a small window instead of one
    # exact minute. A few missed minutes self-heal; extra calls are no-ops.
    # (now_ist already computed near the top of this function, before the
    # NEXUS early-return, so the weekly-report check above can use it too.)

    # ── 4. Daily cap reset (first ~10 min of each IST day) ───────────────────
    if now_ist.hour == 0 and now_ist.minute < 10:
        r = _post(f"{base}/nexus/scheduler/daily-cap-reset", headers)
        results["nexus_daily_reset"] = r
        if r["ok"]:
            logger.info("NEXUS daily cap reset ok — %s", r["body"])
        else:
            logger.warning("NEXUS daily cap reset failed: %s", r.get("error"))

        # GTM LinkedIn: enqueue a daily session-validation per active account so a
        # stale cookie is caught + flagged for reconnect BEFORE it breaks a send.
        if linkedin_enabled:
            r = _post(f"{base}/nexus/linkedin-agent/scheduler/validate-sessions", headers)
            results["gtm_linkedin_validate"] = r
            if r["ok"]:
                logger.info("GTM LinkedIn session validation ok — %s", r["body"])
            else:
                logger.warning("GTM LinkedIn session validation failed: %s", r.get("error"))

    # ── 5. Monthly usage reset (first ~10 min of the 1st IST day of month) ───
    if now_ist.day == 1 and now_ist.hour == 0 and now_ist.minute < 10:
        r = _post(f"{base}/nexus/scheduler/monthly-usage-reset", headers)
        results["nexus_monthly_reset"] = r
        if r["ok"]:
            logger.info("NEXUS monthly usage reset ok — %s", r["body"])
        else:
            logger.warning("NEXUS monthly usage reset failed: %s", r.get("error"))

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Heartbeat ok", "results": results}),
    }
