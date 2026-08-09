"""Background intent-gate sweep — Agent #10 scoring of discovered leads.

Why this exists:
    Discovery (``lead_discovery.autonomous_discover``) attaches Apollo leads to
    a campaign but, when the intent gate is ON (``NEXUS_INTENT_GATE`` != 0),
    DEFERS enrollment: each new lead is stamped ``signals.intent.status =
    'pending'`` and the campaign is flagged ``icp.intent_pending = true``. The
    leads sit visible-but-unsent until Agent #10 judges them.

    Agent #10 is COMPANY-level and ~100s/company — far too slow to run inside
    ``/analyze``. So this module is the consumer: wired into the same scheduler
    tick as ``discovery_sweep``, it claims flagged campaigns, scores a few
    companies per tick, and:
      - ACCEPTED  (>=1 buying signal AND ICP fit not low) -> floor the icp_score
        at 70 and stamp the verdict. (Enrollment is DEFERRED — see below.)
      - REJECTED  -> stamp the verdict + human reason. The operator can still
        "Add" it from the funnel UI (email already revealed — no Apollo cost).
      - ERROR     -> bump ``intent.attempts``; after MAX_SCORE_ATTEMPTS,
        FAIL OPEN (status 'included_failopen') so a broken agent can never
        permanently strand a campaign's leads — they join the accepted set.

    ENROLLMENT GATE: scoring NO LONGER enrolls. Accepted/approved/fail-open
    leads sit scored-but-unsent until the operator hits "Start Outreach"
    (POST /nexus/campaigns/{id}/start-outreach), which enrolls the whole
    approved set in one go. This is what makes outreach begin only AFTER
    Agent #10 finishes AND the user approves.

    When a campaign has no pending leads left, the ``intent_pending`` flag is
    cleared and the campaign falls out of the claim query (cheap when idle).

Public surface:
    process_pending_intent(db, max_campaigns=1, max_companies=2) -> dict
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from nexus.models_phase3 import NexusCampaign, NexusLead
from nexus.services import intent_agent

logger = logging.getLogger("pipelyt.nexus.intent_sweep")

# Per-tick caps. Companies within a campaign are scored CONCURRENTLY (see
# _SCORE_CONCURRENCY), so a whole campaign's pending companies finish in
# roughly one company's wall-clock — not the sum. The company cap is high
# enough to drain a normal campaign in a single tick.
DEFAULT_MAX_CAMPAIGNS_PER_TICK = 1
DEFAULT_MAX_COMPANIES_PER_TICK = 30

# Max Agent #10 runs in flight at once. Each is one grounded Gemini call, so
# concurrency collapses an N-company campaign from ~N×latency to ~1×latency.
# Default 20 → a typical run's companies all score in a SINGLE wave (~30-50s).
# Tunable via AGENT10_SCORE_CONCURRENCY; lower it if Gemini rate-limits (429s),
# raise it for bigger runs. Each grounded call also counts against the Gemini
# requests-per-minute + Google-Search grounding quota.
_SCORE_CONCURRENCY = int(os.getenv("AGENT10_SCORE_CONCURRENCY", "40") or "40")
# Dedicated thread pool sized to the concurrency. asyncio.to_thread's DEFAULT
# pool is min(32, cpu+4) — only ~16 on a 12-core box, ~6 on a small Lambda — so
# raising the semaphore alone would silently bottleneck there. Agent #10 calls
# are I/O-bound: each thread just WAITS on Gemini's HTTP response (~0 CPU, GIL
# released), so the pool is sized to "calls in flight at once", NOT the core
# count. Works identically on a 1-core Lambda. Created once; threads spawn on
# demand up to the max. Tune via AGENT10_SCORE_CONCURRENCY.
_SCORE_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, _SCORE_CONCURRENCY), thread_name_prefix="agent10-score"
)

# How many pending leads to pull per campaign before grouping by company.
_PENDING_FETCH_CAP = 500


# ---------------------------------------------------------------------------
# Lead-side signal helpers
# ---------------------------------------------------------------------------
def _intent_block(lead: NexusLead) -> Dict[str, Any]:
    sig = lead.signals if isinstance(lead.signals, dict) else {}
    blk = sig.get("intent")
    return dict(blk) if isinstance(blk, dict) else {}


def _write_intent(db: Session, lead: NexusLead, intent: Dict[str, Any]) -> None:
    """Reassign signals (JSONB isn't tracked on in-place mutation)."""
    merged = dict(lead.signals or {})
    merged["intent"] = intent
    lead.signals = merged


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def process_pending_intent(
    db: Session,
    max_campaigns: int = DEFAULT_MAX_CAMPAIGNS_PER_TICK,
    max_companies: int = DEFAULT_MAX_COMPANIES_PER_TICK,
    only_campaign_id: Optional[int] = None,
    auto_start_outreach: bool = True,
    target_accepted: Optional[int] = None,
) -> Dict[str, Any]:
    """Score a few pending companies for flagged campaigns. Returns a summary.

    Idempotent + bounded: claims campaigns with ``icp.intent_pending = true``,
    scores up to ``max_companies`` companies' worth of pending leads, enrolls
    accepted ones, and clears the flag when a campaign is fully scored.

    ``only_campaign_id`` restricts the claim to one campaign — used by the
    post-/analyze BackgroundTask so a fresh launch scores itself immediately
    instead of waiting for the scheduler tick.
    """
    result: Dict[str, Any] = {
        "campaigns": 0, "companies_scored": 0, "google_searches": 0,
        "accepted": 0, "rejected": 0, "failopen": 0, "errors": [],
    }
    if not intent_agent.gate_enabled():
        return result

    try:
        if only_campaign_id is not None:
            rows = db.execute(
                text(
                    """SELECT id FROM nexus_campaigns
                        WHERE id = :cid
                          AND (icp ->> 'intent_pending') = 'true'"""
                ),
                {"cid": only_campaign_id},
            ).fetchall()
        else:
            rows = db.execute(
                text(
                    """SELECT id FROM nexus_campaigns
                        WHERE (icp ->> 'intent_pending') = 'true'
                          AND status IN ('active', 'paused')
                        ORDER BY id ASC
                        LIMIT :cap"""
                ),
                {"cap": max(1, int(max_campaigns))},
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("intent_sweep: claim query failed: %s", e)
        db.rollback()
        return result

    if not rows:
        return result

    for r in rows:
        camp = db.query(NexusCampaign).filter(NexusCampaign.id == r[0]).first()
        if camp is None:
            continue
        result["campaigns"] += 1
        try:
            await _score_campaign(
                db, camp, max_companies, result,
                auto_start_outreach=auto_start_outreach,
                target_accepted=target_accepted,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("intent_sweep: campaign %s scoring raised", camp.id)
            result["errors"].append({"campaign_id": camp.id, "error": str(exc)[:200]})
            db.rollback()

    if result["companies_scored"]:
        _cs = result["companies_scored"]
        _gs = result["google_searches"]
        _avg = (_gs / _cs) if _cs else 0.0
        logger.info(
            "\n"
            "============ AGENT #10 - API CALLS & COST (intent sweep) ============\n"
            "  Campaigns scored                                   : %d\n"
            "  Companies researched (1 grounded Gemini call each) : %d\n"
            "  Google Search queries  (BILLED - the real cost)    : %d\n"
            "  Avg Google searches per company                    : %.1f\n"
            "  Verdicts: %d accepted, %d not-picked, %d fail-open\n"
            "====================================================================",
            result["campaigns"], _cs, _gs, _avg,
            result["accepted"], result["rejected"], result["failopen"],
        )
    return result


def _load_product_context(db: Session, camp: NexusCampaign, campaign_icp: Dict[str, Any]) -> Dict[str, Any]:
    """Product/service info injected into the grounded scoring prompt so the
    agent can look for the buying signal that matters for THIS product (e.g.
    Spenzo = ad-management -> 'is the company spending on ads?')."""
    prow = None
    if getattr(camp, "product_id", None):
        prow = db.execute(
            text(
                "SELECT name, value_proposition, category, key_benefits "
                "FROM nexus_products WHERE id = :p"
            ),
            {"p": camp.product_id},
        ).first()
    benefits = getattr(prow, "key_benefits", None) if prow else None
    if isinstance(benefits, list):
        benefits = ", ".join(str(b) for b in benefits if b)
    return {
        "name": (prow.name if prow else None) or camp.name or "our product",
        "description": (prow.value_proposition if prow else None) or "",
        "category": (prow.category if prow else None) or "",
        "key_benefits": benefits or "",
        "entity_type": (campaign_icp.get("entity_type") if isinstance(campaign_icp, dict) else None) or "product",
    }


async def _score_campaign(
    db: Session, camp: NexusCampaign, max_companies: int, result: Dict[str, Any],
    auto_start_outreach: bool = True,
    target_accepted: Optional[int] = None,
) -> None:
    campaign_icp = camp.icp if isinstance(camp.icp, dict) else {}
    product_context = _load_product_context(db, camp, campaign_icp)

    # Product-specific qualifying signal: derive ONCE per campaign (one Gemini
    # call) from the product's own content, then cache on campaign.icp and reuse
    # for every company this run. Re-derived after /analyze (which rewrites icp).
    qualifying_signal = (campaign_icp.get("qualifying_signal") or "") if isinstance(campaign_icp, dict) else ""
    if not qualifying_signal:
        qualifying_signal = intent_agent.derive_qualifying_signal(product_context)
        if qualifying_signal:
            try:
                icp = dict(camp.icp or {})
                icp["qualifying_signal"] = qualifying_signal
                camp.icp = icp
                db.commit()
                logger.info("intent_sweep: campaign %s derived qualifying signal: %s",
                            camp.id, qualifying_signal[:120])
            except Exception:  # noqa: BLE001
                db.rollback()

    # Pull this campaign's pending leads + their company info, oldest first.
    pend = db.execute(
        text(
            """SELECT l.id AS lead_id, gl.id AS global_lead_id,
                      gl.company_name AS company_name,
                      gl.company_domain AS company_domain,
                      gl.role AS role
                 FROM nexus_leads l
                 JOIN nexus_global_leads gl ON gl.id = l.global_lead_id
                WHERE l.campaign_id = :cid
                  AND (l.signals -> 'intent' ->> 'status') = 'pending'
                ORDER BY l.id ASC
                LIMIT :cap"""
        ),
        {"cid": camp.id, "cap": _PENDING_FETCH_CAP},
    ).mappings().fetchall()

    if not pend:
        _clear_flag_if_drained(db, camp.id)
        return

    # Group pending leads by company domain (one Agent #10 run per company).
    by_domain: Dict[str, List[Any]] = {}
    order: List[str] = []
    for p in pend:
        dom = (p["company_domain"] or "").strip().lower()
        # Leads with no domain can't be company-researched — fail open so
        # they still reach outreach rather than getting stuck pending.
        key = dom or f"__nodomain__:{p['lead_id']}"
        if key not in by_domain:
            by_domain[key] = []
            order.append(key)
        by_domain[key].append(p)

    groups = order[: max(1, int(max_companies))]
    total_n = len(groups)
    logger.info(
        "NEXUS RUN [2/4 SIGNALS] campaign %s — Agent #10 scoring %d compan(ies) "
        "from %d pending lead(s), up to %d in parallel…",
        camp.id, total_n, len(pend), _SCORE_CONCURRENCY,
    )
    sem = asyncio.Semaphore(_SCORE_CONCURRENCY)
    done_n = 0  # incremented only on the event-loop thread (safe)

    async def _score_one(key: str):
        """Score one company in a worker thread. Returns (key, verdict|None).
        Touches NO DB / Session — only the pure Agent #10 call — so it's safe
        to run concurrently. verdict=None signals the no-domain fail-open."""
        nonlocal done_n
        members = by_domain[key]
        first = members[0]
        company = first["company_name"] or first["company_domain"] or "?"
        dom = (first["company_domain"] or "").strip().lower()
        if not dom:
            done_n += 1
            logger.info("intent_sweep: [%d/%d] %s — no domain, including by default",
                        done_n, total_n, company)
            return key, None
        logger.info("intent_sweep: → researching %s (%s)…", company, dom)
        async with sem:
            loop = asyncio.get_running_loop()
            verdict = await loop.run_in_executor(
                _SCORE_EXECUTOR,
                functools.partial(
                    intent_agent.score_company_intent,
                    company_name=first["company_name"] or dom,
                    domain=dom,
                    person_title=first["role"],
                    campaign_icp=campaign_icp,
                    product_context=product_context,
                    qualifying_signal=qualifying_signal,
                ),
            )
        done_n += 1
        sigs = verdict.get("signals") or []
        if verdict.get("accepted"):
            logger.info(
                "intent_sweep: [%d/%d] ✓ ACCEPTED %s (%s) — score=%s, %d signal(s)",
                done_n, total_n, company, dom, verdict.get("intent_score"), len(sigs),
            )
        else:
            logger.info(
                "intent_sweep: [%d/%d] ✗ not picked %s (%s) — %s",
                done_n, total_n, company, dom,
                (verdict.get("reason") or "no buying signals")[:70],
            )
        return key, verdict

    # Fan out all companies concurrently and APPLY each verdict as it lands
    # (as_completed), so we can STOP the instant the campaign has enough
    # qualified leads. SQLAlchemy's Session isn't thread-safe, but the workers
    # touch NO DB — only the pure Agent #10 call — so applying writes here on
    # the single event-loop thread between awaits is safe. Commit PER COMPANY so
    # one bad write rolls back only that company, never the whole batch.
    #
    # Solution 1 (cancel-on-target): when `target_accepted` is set, once the
    # campaign reaches that many qualified leads we CANCEL the remaining in-
    # flight company scorings — a run that already met its target must never
    # wait on a straggler (one hung company was adding ~6 minutes). target=None
    # (the background sweep) scores every company and never breaks early.
    accepted_total = 0
    if target_accepted is not None:
        try:
            accepted_total = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM nexus_leads WHERE campaign_id = :c "
                        "AND COALESCE(signals -> 'intent' ->> 'status', 'accepted') "
                        "IN ('accepted', 'approved', 'included_failopen')"
                    ),
                    {"c": camp.id},
                ).scalar() or 0
            )
        except Exception:  # noqa: BLE001
            accepted_total = 0

    tasks = [asyncio.create_task(_score_one(k)) for k in groups]
    try:
        for fut in asyncio.as_completed(tasks):
            key, verdict = await fut
            members = by_domain[key]
            try:
                if verdict is None:
                    _apply_failopen(db, members, reason="No company domain to research")
                    result["failopen"] += len(members)
                elif verdict.get("gate_decision") == "error":
                    _apply_error(db, members, verdict, result)
                elif verdict.get("accepted"):
                    _apply_accepted(db, camp, members, verdict)
                    result["accepted"] += len(members)
                    accepted_total += len(members)
                else:
                    _apply_rejected(db, members, verdict)
                    result["rejected"] += len(members)
                db.commit()
                result["companies_scored"] += 1
                # Tally billed Google searches (grounded call cost driver).
                result["google_searches"] += int((verdict or {}).get("web_searches") or 0)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "intent_sweep: applying verdict failed for %r (campaign %s) — "
                    "rolled back this company, continuing", key, camp.id,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
            if target_accepted is not None and accepted_total >= target_accepted:
                _pending_cancel = sum(1 for t in tasks if not t.done())
                if _pending_cancel:
                    logger.info(
                        "intent_sweep: campaign %s — %d qualified reached (target %d); "
                        "cancelling %d remaining company scoring(s)",
                        camp.id, accepted_total, target_accepted, _pending_cancel,
                    )
                break
    finally:
        # Cancel any still-running scorings; do NOT block on them — their worker
        # threads run to completion in the background and their results are
        # discarded (the leads stay pending for the background sweep to finish).
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # Auto-start outreach (2026-06-04): the operator-approval gate is REMOVED.
    # Once this campaign is fully scored (no pending leads left), enroll the
    # accepted set and flip the launch flag so outreach begins with no human
    # click. The Apollo sequencer tick then generates content + sends.
    remaining = db.execute(
        text(
            "SELECT COUNT(*) FROM nexus_leads "
            "WHERE campaign_id = :c AND (signals -> 'intent' ->> 'status') = 'pending'"
        ),
        {"c": camp.id},
    ).scalar() or 0
    # 2026-06-09 — `auto_start_outreach=False` is passed when scoring runs
    # INLINE during discovery (B-mode). We must NOT enroll + send emails mid-
    # discovery (before the user sees the leads table). Enrollment + email
    # generation is deferred to a background task kicked AFTER /analyze returns.
    if int(remaining) == 0 and auto_start_outreach:
        _auto_start_outreach(db, camp)

    # Drop the intent_pending flag IFF no pending leads remain. Done as a
    # single conditional UPDATE so a concurrent /analyze run that adds new
    # pending leads can't have the flag cleared out from under it.
    _clear_flag_if_drained(db, camp.id)


# ---------------------------------------------------------------------------
# Verdict application
# ---------------------------------------------------------------------------
def _verdict_payload(verdict: Dict[str, Any], status: str) -> Dict[str, Any]:
    return {
        "status": status,
        "score": int(verdict.get("intent_score") or 0),
        "in_market_score": int(verdict.get("in_market_score") or 0),
        "icp_fit_band": verdict.get("icp_fit_band"),
        "reason": verdict.get("reason") or "",
        "signals": verdict.get("signals") or [],
        "scored_at": _now_iso(),
    }


def _apply_accepted(
    db: Session, camp: NexusCampaign, members: List[Any], verdict: Dict[str, Any]
) -> None:
    payload = _verdict_payload(verdict, "accepted")
    for m in members:
        lead = db.query(NexusLead).filter(NexusLead.id == m["lead_id"]).first()
        if lead is None:
            continue
        prev = _intent_block(lead)
        payload_with = dict(payload)
        payload_with["attempts"] = int(prev.get("attempts") or 0)
        _write_intent(db, lead, payload_with)
        # Surface the Agent #10 fit-band score (Excellent=95 … Medium=70) as the
        # lead's score so the GTM UI's MATCH column shows the fit verdict, not
        # the raw Apollo firmographic match. Floored at the accept floor.
        lead.icp_score = max(int(payload.get("score") or 0), intent_agent.INTENT_ACCEPT_FLOOR)
        # Enrollment is auto-started once the campaign is fully scored — see
        # _auto_start_outreach (operator-approval gate removed 2026-06-04).


def _apply_rejected(db: Session, members: List[Any], verdict: Dict[str, Any]) -> None:
    payload = _verdict_payload(verdict, "rejected")
    for m in members:
        lead = db.query(NexusLead).filter(NexusLead.id == m["lead_id"]).first()
        if lead is None:
            continue
        prev = _intent_block(lead)
        payload_with = dict(payload)
        payload_with["attempts"] = int(prev.get("attempts") or 0)
        _write_intent(db, lead, payload_with)


def _apply_error(
    db: Session, members: List[Any], verdict: Dict[str, Any], result: Dict[str, Any]
) -> None:
    """Bump attempts; fail open (enroll) once attempts hit the ceiling."""
    for m in members:
        lead = db.query(NexusLead).filter(NexusLead.id == m["lead_id"]).first()
        if lead is None:
            continue
        prev = _intent_block(lead)
        attempts = int(prev.get("attempts") or 0) + 1
        if attempts >= intent_agent.MAX_SCORE_ATTEMPTS:
            _write_intent(db, lead, {
                "status": "included_failopen",
                "score": int(lead.icp_score or 0),
                "reason": "Signal check unavailable — included by default",
                "signals": [],
                "attempts": attempts,
                "scored_at": _now_iso(),
            })
            lead.icp_score = max(int(lead.icp_score or 0), intent_agent.INTENT_ACCEPT_FLOOR)
            # No enrollment — deferred to Start-Outreach (joins the approved set).
            result["failopen"] += 1
        else:
            blk = dict(prev)
            blk["status"] = "pending"
            blk["attempts"] = attempts
            blk["last_error"] = (verdict.get("error") or "")[:200]
            _write_intent(db, lead, blk)


def _apply_failopen(db: Session, members: List[Any], reason: str) -> None:
    for m in members:
        lead = db.query(NexusLead).filter(NexusLead.id == m["lead_id"]).first()
        if lead is None:
            continue
        _write_intent(db, lead, {
            "status": "included_failopen",
            "score": int(lead.icp_score or 0),
            "reason": reason,
            "signals": [],
            "attempts": int(_intent_block(lead).get("attempts") or 0),
            "scored_at": _now_iso(),
        })
        lead.icp_score = max(int(lead.icp_score or 0), intent_agent.INTENT_ACCEPT_FLOOR)
        # No enrollment — deferred to Start-Outreach (joins the approved set).


def _auto_start_outreach(db: Session, camp: NexusCampaign) -> int:
    """Enroll the accepted set and flip the launch flag — no human click.

    Mirrors the (now-vestigial) POST /start-outreach loop: every lead in the
    'accepted' bucket (accepted / approved / included_failopen, plus gate-off
    legacy leads) is enrolled idempotently. Content generation + sending is
    left to the Apollo sequencer tick (the documented safety net). Idempotent:
    re-running just re-affirms enrollments and the flag.
    """
    from nexus.routers.campaign_leads import _intent_stage
    from nexus.services.lead_discovery import _enroll_in_sequence

    # Fence to THIS run's leads only (created at/after the latest launch). The
    # campaign is reused across runs and accumulates an accepted backlog (incl.
    # legacy no-intent leads that default to 'accepted'); without this fence the
    # first auto-start would mass-enroll the WHOLE backlog and blast outreach to
    # everyone (observed: 83 enrolled on a 5-lead run). Each run enrolls only
    # the leads it just found + accepted.
    launched_at = db.execute(
        text("SELECT MAX(launched_at) FROM nexus_campaign_launches WHERE campaign_id = :c"),
        {"c": camp.id},
    ).scalar()
    q = db.query(NexusLead).filter(
        NexusLead.campaign_id == camp.id,
        NexusLead.workspace_id == camp.workspace_id,
    )
    if launched_at is not None:
        q = q.filter(NexusLead.created_at >= launched_at)
    leads = q.all()
    enrolled = 0
    qualified = dropped = 0
    for lead in leads:
        intent = lead.signals.get("intent") if isinstance(lead.signals, dict) else None
        stage = _intent_stage(intent if isinstance(intent, dict) else None)
        if stage == "rejected":
            dropped += 1
            continue
        if stage != "accepted":
            continue
        qualified += 1
        try:
            _enroll_in_sequence(
                db,
                workspace_id=lead.workspace_id,
                campaign_id=lead.campaign_id,
                global_lead_id=lead.global_lead_id,
            )
            enrolled += 1
        except Exception:  # noqa: BLE001
            logger.exception("intent_sweep: auto-enroll failed for lead %s", lead.id)

        # Auto-enroll the SAME lead into LinkedIn too (best-effort, no-op unless the
        # campaign's brand has a connected active LinkedIn account + the lead has a
        # LinkedIn URL) — so accepted leads start on both channels at once, exactly
        # like email. Gated by the feature flag; never breaks the email enroll.
        from core.config import NEXUS_LINKEDIN_ENABLED
        if NEXUS_LINKEDIN_ENABLED:
            try:
                from gtm.linkedin.scheduler import auto_enroll_lead
                auto_enroll_lead(
                    db,
                    workspace_id=lead.workspace_id,
                    campaign_id=lead.campaign_id,
                    lead_id=lead.global_lead_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("intent_sweep: LinkedIn auto-enroll failed for lead %s", lead.id)

    # Flip the launch flag (reassign JSONB — in-place mutation isn't tracked).
    try:
        icp = dict(camp.icp or {})
        if not icp.get("outreach_approved"):
            icp["outreach_approved"] = True
            icp["outreach_approved_at"] = _now_iso()
            camp.icp = icp
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("intent_sweep: failed to flip outreach_approved for %s", camp.id)
        db.rollback()

    logger.info(
        "NEXUS RUN [3/4 OUTREACH] campaign %s — scoring done: %d qualified, %d dropped "
        "→ enrolled %d lead(s); outreach sends on the next tick.",
        camp.id, qualified, dropped, enrolled,
    )
    return enrolled


def _clear_flag_if_drained(db: Session, campaign_id: int) -> None:
    """Atomically drop intent_pending IFF no pending leads remain.

    The WHERE guard makes the recount + clear a single statement, so a
    concurrent discovery run inserting fresh pending leads can't race the
    flag off. No-op if any pending lead still exists.
    """
    try:
        db.execute(
            text(
                """UPDATE nexus_campaigns
                      SET icp = (COALESCE(icp, '{}'::jsonb) - 'intent_pending')
                                || jsonb_build_object('intent_completed_at', :ts)
                    WHERE id = :cid
                      AND (icp ->> 'intent_pending') = 'true'
                      AND NOT EXISTS (
                          SELECT 1 FROM nexus_leads
                           WHERE campaign_id = :cid
                             AND (signals -> 'intent' ->> 'status') = 'pending')"""
            ),
            {"cid": campaign_id, "ts": _now_iso()},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("intent_sweep: clear-flag update failed for %s", campaign_id)
        db.rollback()


def run_intent_sweep_bg(campaign_id: int) -> None:
    """FastAPI BackgroundTask entry — score THIS campaign's pending leads right
    after /analyze, so scoring doesn't wait for the scheduler tick (which may
    not fire in local/dev). Opens its own DB session and loops until the
    campaign is drained (bounded). The scheduler-tick sweep remains the
    fallback for any environment where this background task can't complete
    (e.g. a frozen Lambda container).

    BackgroundTasks runs sync callables in a threadpool, so this is sync and
    drives the async sweep via asyncio.run.
    """
    import asyncio

    if not intent_agent.gate_enabled():
        return
    from core.database import SessionLocal

    logger.info("intent_sweep: BackgroundTask started for campaign %s", campaign_id)
    db = SessionLocal()
    try:
        # Each pass scores up to DEFAULT_MAX_COMPANIES_PER_TICK companies
        # concurrently; loop until nothing pending remains (bounded by a hard
        # cap so a persistent error can't spin forever).
        for _ in range(20):
            res = asyncio.run(
                process_pending_intent(db, only_campaign_id=campaign_id)
            )
            if not res.get("companies_scored"):
                break
    except Exception:  # noqa: BLE001
        logger.exception(
            "run_intent_sweep_bg: failed for campaign %s", campaign_id
        )
    finally:
        db.close()


def enroll_and_outreach_bg(campaign_id: int) -> None:
    """FastAPI BackgroundTask — runs AFTER /analyze returns (i.e. after the user
    sees the qualified-leads table). Under B-mode, inline scoring deliberately
    scored WITHOUT enrolling/sending (auto_start_outreach=False), so no emails
    went out mid-discovery. This task now:
      1. finishes any residual pending scoring (rare — inline hit its bound), and
      2. enrolls the campaign's Accepted leads (idempotent),
    which lets the sequencer tick generate + send emails IN THE BACKGROUND —
    so emails happen AFTER the leads are shown, never before.
    """
    import asyncio

    if not intent_agent.gate_enabled():
        return
    from core.database import SessionLocal

    logger.info(
        "intent_sweep: enroll+outreach BackgroundTask started for campaign %s",
        campaign_id,
    )
    db = SessionLocal()
    try:
        # 1) Finish scoring anything still pending (auto_start=True → enrolls
        #    + auto-starts when it drains). Bounded.
        for _ in range(20):
            res = asyncio.run(process_pending_intent(db, only_campaign_id=campaign_id))
            if not res.get("companies_scored"):
                break
        # 2) Ensure the Accepted set (scored inline with auto_start off) is
        #    enrolled. Idempotent — safe even if step 1 already enrolled them.
        camp = (
            db.query(NexusCampaign).filter(NexusCampaign.id == campaign_id).first()
        )
        if camp is not None:
            _auto_start_outreach(db, camp)
        # 3) Now that ALL qualified leads are scored + enrolled, generate the
        #    cadence drafts in ONE batched pass. Kept OUT of the scoring loop
        #    above so Gemini calls never slow qualification. Drafts appear in
        #    the Content tab; the sequencer sends them paced by mailbox.
        from nexus.services.sequencer import prime_drafts_for_campaign
        prime_drafts_for_campaign(db, campaign_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "enroll_and_outreach_bg: failed for campaign %s", campaign_id
        )
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


__all__ = ["process_pending_intent", "run_intent_sweep_bg", "enroll_and_outreach_bg"]
