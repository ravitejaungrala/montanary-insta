"""Single-entry discovery for a given NexusCampaign.

Derives ICP + target_domains from the campaign and its parent product, ensures
a default sequence exists, then drives ``lead_discovery.autonomous_discover``:
attach raw Apollo leads, score them (Agent #10), and repeat until enough clear
the intent gate. Used by ``/nexus/analyze``.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from nexus import models_phase2
from nexus.models_phase3 import NexusCampaign, NexusLead
from nexus.models_phase4 import NexusSequence
from nexus.services import intent_agent, lead_discovery
from nexus.services.discovery_apollo import revenue_ranges_from_icp

# Option B (2026-06-09): max Agent #10 company scorings in flight at once
# inside the pre-reveal hook. Mirrors intent_sweep's concurrency (same env
# var, same 40 default) so the in-memory scoring wave finishes in ~one
# company's wall-clock — every candidate scores in parallel, no sub-waves.
_PRE_REVEAL_CONCURRENCY = int(os.getenv("AGENT10_SCORE_CONCURRENCY", "40") or "40")

# Plan #1 (2026-06-09): assumed fraction of attached leads that pass the intent
# gate. Used to OVER-FETCH (fetch round_need ÷ rate raw leads) so a SINGLE
# parallel scoring wave yields enough qualified. 0.5 = pull 2x the requested
# count: ask for X leads -> fetch 2X, score them, surface the accepted.
# Env-tunable.
_EXPECTED_QUALIFY_RATE = float(os.getenv("NEXUS_EXPECTED_QUALIFY_RATE", "0.5") or "0.5")

log = logging.getLogger("nexus.discover_for_campaign")


# ---------------------------------------------------------------------------
# Default 4-step sequence, Day 0 / +3 / +6 / +9. Deterministic fallback
# templates — used only if per-lead AI content generation fails.
# ---------------------------------------------------------------------------
_DEFAULT_STEPS: List[Dict[str, Any]] = [
    {
        "step": 0,
        "delay_days": 0,
        "subject_template": "{first_name}, a quick thought on {company_name}",
        "body_template": (
            "Hi {first_name},\n\n"
            "Saw what you're doing at {company_name} and had a quick thought "
            "tied to {product_name}.\n\n"
            "Open to a 15-min call this week?"
        ),
    },
    {
        "step": 1,
        "delay_days": 3,
        "subject_template": "Re: {first_name}",
        "body_template": (
            "Hi {first_name},\n\n"
            "Floating this back up in case it got buried.\n\n"
            "Worth a quick chat?"
        ),
    },
    {
        "step": 2,
        "delay_days": 3,
        "subject_template": "One more thought for {company_name}",
        "body_template": (
            "Hi {first_name},\n\n"
            "Different angle — most {company_name}-style teams we talk to are "
            "quietly sitting on the exact problem {product_name} solves. "
            "Curious if that resonates, or if the timing's just off?"
        ),
    },
    {
        "step": 3,
        "delay_days": 3,
        "subject_template": "Last note, {first_name}",
        "body_template": (
            "Hi {first_name},\n\n"
            "Last note from me — completely understand if the timing isn't right. "
            "If {product_name} ever becomes relevant for {company_name}, you "
            "know where to find me."
        ),
    },
]


def _ensure_default_sequence(
    db: Session, *, workspace_id: int, campaign_id: int, name: str
) -> NexusSequence:
    seq = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.workspace_id == workspace_id,
            NexusSequence.campaign_id == campaign_id,
        )
        .order_by(NexusSequence.id.asc())
        .first()
    )
    if seq is not None:
        return seq
    seq = NexusSequence(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        name=f"{name} — outbound",
        steps=_DEFAULT_STEPS,
    )
    db.add(seq)
    db.commit()
    db.refresh(seq)
    return seq


# ---------------------------------------------------------------------------
# ICP + target-domain derivation
# ---------------------------------------------------------------------------
_DOMAIN_RE = re.compile(r"^(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = _DOMAIN_RE.match(url.strip())
    if not m:
        return None
    return m.group(1).lower().lstrip("www.") or None


def _resolve_icp(
    db: Session, campaign: NexusCampaign
) -> Dict[str, Any]:
    icp = dict(campaign.icp or {})
    if icp:
        return icp
    # Fall back to the parent product's ICP.
    if campaign.product_id:
        prod = (
            db.query(models_phase2.NexusProduct)
            .filter(models_phase2.NexusProduct.id == campaign.product_id)
            .first()
        )
        if prod and prod.icp:
            return dict(prod.icp)
    return {}


def _resolve_target_domains(
    db: Session, campaign: NexusCampaign
) -> List[str]:
    """Best-effort: pull a domain from the campaign's parent product URL.

    The legacy flow generates a richer domain shortlist via the
    competitor / job-board agents. For the P0 loop we just feed the
    product's own domain so the website + hunter scrapers have a
    starting point.
    """
    if not campaign.product_id:
        return []
    prod = (
        db.query(models_phase2.NexusProduct)
        .filter(models_phase2.NexusProduct.id == campaign.product_id)
        .first()
    )
    if not prod:
        return []
    d = _domain_from_url(prod.source_url)
    return [d] if d else []


# ── B-mode (2026-06-09): count = QUALIFIED (Accepted) leads ──────────────────
# When the intent gate is ON, the user's "Number of leads" is the count of
# QUALIFIED (Agent #10-Accepted, score >= 70) leads, NOT raw matches. So after
# each Apollo pass the loop stamps the new leads pending, scores them inline,
# counts how many cleared the gate, and KEEPS discovering until that count hits
# the requested number (or Apollo is exhausted). Runs synchronously — /analyze
# blocks while the UI shows the "qualifying" loader.
_ACCEPTED_INTENT_STATUSES = ("accepted", "approved", "included_failopen")


def _count_accepted(db: Session, campaign_id: int) -> int:
    """Count this campaign's ACCEPTED (qualified) leads. Leads with no intent
    block (gate off / legacy) count as accepted — already in the set."""
    try:
        n = db.execute(
            text(
                """SELECT count(*) FROM nexus_leads
                    WHERE campaign_id = :cid
                      AND COALESCE(signals->'intent'->>'status','accepted')
                          IN ('accepted','approved','included_failopen')"""
            ),
            {"cid": campaign_id},
        ).scalar()
        return int(n or 0)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _count_accepted_since(db: Session, campaign_id: int, since) -> int:
    """Run-fenced sibling of ``_count_accepted``: ACCEPTED leads this run
    attached, moved in, or reclaimed (created_at is bumped in all three
    cases). This is the EXACT population the New-Run UI's latest_run fence
    shows — so everything the run REPORTS uses it, and the log can never
    disagree with the table again."""
    try:
        n = db.execute(
            text(
                """SELECT count(*) FROM nexus_leads
                    WHERE campaign_id = :cid
                      AND created_at >= :since
                      AND COALESCE(signals->'intent'->>'status','accepted')
                          IN ('accepted','approved','included_failopen')"""
            ),
            {"cid": campaign_id, "since": since},
        ).scalar()
        return int(n or 0)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _stamp_pending_for_run(db: Session, campaign: NexusCampaign, since, total: int) -> None:
    """Stamp this run's freshly-attached, un-enrolled leads as intent 'pending'
    and flag the campaign so the scorer claims it. Idempotent + run-fenced
    (created_at >= since). Extracted so the loop can score after EACH pass."""
    try:
        res = db.execute(
            text(
                """UPDATE nexus_leads AS l
                      SET signals = COALESCE(l.signals, '{}'::jsonb)
                                    || jsonb_build_object('intent',
                                         jsonb_build_object('status','pending','attempts',0))
                    WHERE l.campaign_id = :cid
                      AND l.created_at >= :since
                      AND (l.signals -> 'intent') IS NULL
                      AND NOT EXISTS (
                            SELECT 1 FROM nexus_lead_sequences ls
                             WHERE ls.lead_id = l.global_lead_id
                               AND ls.campaign_id = l.campaign_id
                               AND ls.workspace_id = l.workspace_id)"""
            ),
            {"cid": campaign.id, "since": since},
        )
        stamped = res.rowcount or 0
        if stamped > 0 or total > 0:
            icp_flag = dict(campaign.icp or {})
            icp_flag["intent_pending"] = True
            campaign.icp = icp_flag
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass


def _bank_surplus_pending(db: Session, campaign: NexusCampaign) -> int:
    """Once the qualified TARGET is met, BANK the campaign's leftover un-scored
    ('pending') leads instead of discarding them.

    Banked = intent status 'rejected' (hidden from the GTM Journey and never
    enrolled, exactly like the old 'surplus' drop) with drop_reason 'banked',
    so the NEXT run for this product reclaims them BEFORE paying Apollo for
    new candidates — their emails were already revealed (already paid for).
    We intentionally leave the intent_pending flag alone — with no 'pending'
    leads left, the next sweep finds nothing to score and clears the flag
    itself, while enroll_and_outreach_bg still enrolls the Accepted set."""
    banked = 0
    try:
        res = db.execute(
            text(
                """UPDATE nexus_leads AS l
                      SET signals = COALESCE(l.signals, '{}'::jsonb)
                                    || jsonb_build_object('intent',
                                         jsonb_build_object(
                                           'status', 'rejected',
                                           'drop_reason', 'banked',
                                           'attempts',
                                           COALESCE((l.signals -> 'intent' ->> 'attempts')::int, 0)))
                    WHERE l.campaign_id = :cid
                      AND (l.signals -> 'intent' ->> 'status') = 'pending'"""
            ),
            {"cid": campaign.id},
        )
        banked = res.rowcount or 0
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
    return banked


def _bank_excess_accepted(
    db: Session, campaign: NexusCampaign, since, target: int
) -> int:
    """If scoring overshot the requested count (parallel verdicts can land
    together and push accepted past the target), keep the BEST `target` and
    bank the excess: the verdict is PRESERVED inside the intent block and
    `prev_status` remembers it was accepted, so a later run restores it
    WITHOUT re-scoring. Status flips to 'rejected' (hidden) + drop_reason
    'banked'. Run-fenced (created_at >= since) — leads delivered by earlier
    runs are never demoted."""
    excess = _count_accepted(db, campaign.id) - max(0, int(target))
    if excess <= 0:
        return 0
    try:
        res = db.execute(
            text(
                """UPDATE nexus_leads AS l
                      SET signals = COALESCE(l.signals, '{}'::jsonb)
                                    || jsonb_build_object('intent',
                                         COALESCE(l.signals -> 'intent', '{}'::jsonb)
                                         || jsonb_build_object(
                                              'status', 'rejected',
                                              'drop_reason', 'banked',
                                              'prev_status',
                                              COALESCE(l.signals -> 'intent' ->> 'status',
                                                       'accepted')))
                    WHERE l.id IN (
                          SELECT id FROM nexus_leads
                           WHERE campaign_id = :cid
                             AND created_at >= :since
                             AND COALESCE(signals -> 'intent' ->> 'status', 'accepted')
                                 IN ('accepted','approved','included_failopen')
                           ORDER BY icp_score ASC, id DESC
                           LIMIT :n)"""
            ),
            {"cid": campaign.id, "since": since, "n": excess},
        )
        demoted = res.rowcount or 0
        db.commit()
        return demoted
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _reclaim_banked(
    db: Session, campaign: NexusCampaign, target: int
) -> tuple:
    """Product-level lead bank: BEFORE paying Apollo for new candidates, pull
    back leads banked by earlier runs of the SAME product (excess accepted +
    revealed-but-unscored surplus). Their emails were already revealed, so
    reusing them costs ZERO new Apollo credits.

      - banked with prev_status accepted  -> restored as accepted (verdict
        kept, NO re-scoring), at most `target` of them;
      - banked without a verdict          -> back to 'pending' for Agent #10,
        capped at 2x the still-needed count (mirrors the over-fetch rate);
      - banked on a SIBLING campaign of this product -> a copy is attached to
        THIS campaign and the source is marked 'moved' (never reclaimed twice).

    Returns (restored_accepted, restored_pending). Best-effort: any failure
    rolls back and returns (0, 0) — the run then just buys fresh leads."""
    if not campaign.product_id:
        return (0, 0)
    try:
        rows = (
            db.query(NexusLead)
            .join(NexusCampaign, NexusCampaign.id == NexusLead.campaign_id)
            .filter(
                NexusLead.workspace_id == campaign.workspace_id,
                NexusCampaign.product_id == campaign.product_id,
                NexusLead.signals["intent"]["drop_reason"].astext == "banked",
            )
            .order_by(NexusLead.icp_score.desc(), NexusLead.id.asc())
            .limit(500)
            .all()
        )
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return (0, 0)
    if not rows:
        return (0, 0)

    def _was_accepted(lead) -> bool:
        blk = (lead.signals or {}).get("intent") or {}
        return blk.get("prev_status") in _ACCEPTED_INTENT_STATUSES

    def _restore(lead, new_status: str) -> bool:
        """Restore `lead` into THIS campaign with `new_status`. In place when
        it already belongs to this campaign; otherwise attach a copy and mark
        the source 'moved'. Returns True when this campaign gained the lead."""
        blk = dict((lead.signals or {}).get("intent") or {})
        blk.pop("drop_reason", None)
        blk.pop("prev_status", None)
        blk["status"] = new_status
        if new_status == "pending":
            blk["attempts"] = int(blk.get("attempts") or 0)
        if lead.campaign_id == campaign.id:
            merged = dict(lead.signals or {})
            merged["intent"] = blk
            lead.signals = merged
            # INVARIANT: a reclaimed lead IS this run's lead — bump
            # created_at so every run fence (the New-Run UI's latest_run
            # filter, run-fenced counts, surplus banking) includes it.
            lead.created_at = datetime.utcnow()
            return True
        # Sibling campaign of the same product: copy the lead over, then
        # retire the source so it can't be reclaimed twice.
        gained = False
        exists = (
            db.query(NexusLead.id)
            .filter(
                NexusLead.workspace_id == campaign.workspace_id,
                NexusLead.campaign_id == campaign.id,
                NexusLead.global_lead_id == lead.global_lead_id,
            )
            .first()
        )
        if exists is None:
            merged = dict(lead.signals or {})
            merged["intent"] = blk
            db.add(NexusLead(
                workspace_id=campaign.workspace_id,
                campaign_id=campaign.id,
                product_id=campaign.product_id,
                global_lead_id=lead.global_lead_id,
                icp_score=lead.icp_score or 0,
                signals=merged,
            ))
            gained = True
        src = dict(lead.signals or {})
        src_blk = dict(src.get("intent") or {})
        src_blk["drop_reason"] = "moved"
        src["intent"] = src_blk
        lead.signals = src
        return gained

    n_acc = n_pend = 0
    try:
        # Accepted first (best score first) — capped at the run target.
        for lead in rows:
            if n_acc >= max(0, int(target)):
                break
            if _was_accepted(lead) and _restore(lead, "accepted"):
                n_acc += 1
        # Then unscored ones — capped at 2x what's still needed.
        pend_cap = max(0, 2 * (max(0, int(target)) - n_acc))
        for lead in rows:
            if n_pend >= pend_cap:
                break
            blk = (lead.signals or {}).get("intent") or {}
            if blk.get("drop_reason") != "banked":  # already handled above
                continue
            if not _was_accepted(lead) and _restore(lead, "pending"):
                n_pend += 1
        if n_pend > 0:
            # Flag the campaign so the scorer claims the reclaimed pendings.
            icp_flag = dict(campaign.icp or {})
            icp_flag["intent_pending"] = True
            campaign.icp = icp_flag
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return (0, 0)
    return (n_acc, n_pend)


async def _score_pending_until_done(
    db: Session, campaign: NexusCampaign, target_accepted: Optional[int] = None,
) -> None:
    """Run Agent #10 over the campaign's pending leads until none remain (the
    intent_pending flag clears) or a pass makes no progress. Bounded.

    Solution 1 (cancel-on-target): when ``target_accepted`` is set, stop the
    instant the campaign reaches that many QUALIFIED leads — the remaining
    pending companies don't need scoring this run. They keep the
    intent_pending flag, so the post-/analyze background sweep finishes them
    WITHOUT holding up discovery (which is what made one hung company add 6
    minutes while we already had enough qualified leads)."""
    from nexus.services.intent_sweep import process_pending_intent
    for _ in range(60):  # safety bound vs a stuck campaign
        if (
            target_accepted is not None
            and _count_accepted(db, campaign.id) >= target_accepted
        ):
            break  # enough qualified — leave the rest for the background sweep
        # auto_start_outreach=False — score ONLY here. Enrollment + email
        # generation/sending is deferred to a background task AFTER /analyze
        # returns, so emails never go out mid-discovery (before the table shows).
        res = await process_pending_intent(
            db, only_campaign_id=campaign.id, max_companies=50,
            auto_start_outreach=False, target_accepted=target_accepted,
        )
        try:
            db.refresh(campaign)
        except Exception:
            pass
        icp = campaign.icp if isinstance(campaign.icp, dict) else {}
        if not icp.get("intent_pending"):
            break
        if int(res.get("companies_scored", 0) or 0) == 0:
            break


def _company_cache_key(domain: Optional[str], name: Optional[str]) -> str:
    """Cache key for a company's Agent #10 verdict: the domain when known,
    else the normalized company NAME — Apollo's search response HIDES domains
    (bulk_match backfills them only after the paid reveal), so pre-reveal
    scoring usually only has the name to go on. '' = unidentifiable (caller
    fails open and includes the candidate)."""
    dom = (domain or "").strip().lower()
    if dom:
        return dom
    nm = " ".join((name or "").split()).lower()
    return f"name:{nm}" if nm else ""


def _apply_cached_verdicts(
    db: Session, campaign: NexusCampaign, since, scored_cache: Dict[str, Any]
) -> int:
    """Option B: stamp THIS run's freshly-attached pending leads with the
    verdict we ALREADY computed in the pre-reveal hook — so Apollo winners
    are marked 'accepted' WITHOUT a second (non-deterministic, grounded)
    Agent #10 run that could otherwise flip a paid-for reveal to 'rejected'.

    Only ACCEPTED companies matter: rejected companies were never revealed,
    so they have no attached leads. Cache keys are ``_company_cache_key``
    values — a domain, or 'name:<company name>' when Apollo's search hid the
    domain — so attached rows are matched by BOTH their (bulk_match
    backfilled) domain and their company name. Unidentifiable Apollo leads +
    non-Apollo leads aren't in the cache and are left pending for the normal
    scorer. Returns the number of leads stamped. Reuses intent_sweep's exact
    verdict shape so downstream counts / enrollment read it identically.
    """
    from nexus.services import intent_sweep

    accepted = {
        d: v
        for d, v in (scored_cache or {}).items()
        if isinstance(v, dict) and v.get("accepted")
    }
    if not accepted:
        return 0
    try:
        rows = db.execute(
            text(
                """SELECT l.id AS lead_id,
                          lower(COALESCE(gl.company_domain,'')) AS dom,
                          COALESCE(gl.company_name,'') AS cname
                     FROM nexus_leads l
                     JOIN nexus_global_leads gl ON gl.id = l.global_lead_id
                    WHERE l.campaign_id = :cid
                      AND l.created_at >= :since
                      AND (l.signals -> 'intent' ->> 'status') = 'pending'"""
            ),
            {"cid": campaign.id, "since": since},
        ).mappings().fetchall()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return 0

    stamped = 0
    for r in rows:
        verdict = accepted.get(r["dom"]) or accepted.get(
            _company_cache_key(None, r["cname"])
        )
        if not verdict:
            continue
        lead = db.query(NexusLead).filter(NexusLead.id == r["lead_id"]).first()
        if lead is None:
            continue
        payload = intent_sweep._verdict_payload(verdict, "accepted")
        prev = intent_sweep._intent_block(lead)
        payload["attempts"] = int(prev.get("attempts") or 0)
        intent_sweep._write_intent(db, lead, payload)
        # Mirror intent_sweep._apply_accepted: surface the fit score (floored).
        lead.icp_score = max(
            int(payload.get("score") or 0), intent_agent.INTENT_ACCEPT_FLOOR
        )
        stamped += 1
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    if stamped:
        log.info(
            "discover_for_campaign: campaign %s — applied %d cached pre-reveal "
            "verdict(s), no re-score", campaign.id, stamped,
        )
    return stamped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def discover_for_campaign(
    db: Session,
    campaign: NexusCampaign,
    *,
    max_leads: int = 25,
    # 0 = don't drop leads on fit score (the UI shows the score for ranking only).
    min_icp_score: int = 0,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run a full discovery pass for one campaign; returns a summary dict.
    Drains the SSE generator under the hood (events are logged, not surfaced)."""
    icp = _resolve_icp(db, campaign)
    if not icp:
        log.info(
            "discover_for_campaign: campaign %s has no ICP — skipping",
            campaign.id,
        )
        return {"leads_attached": 0, "by_source": {}, "errors": ["no_icp"]}

    target_domains = _resolve_target_domains(db, campaign)

    _ensure_default_sequence(
        db,
        workspace_id=campaign.workspace_id,
        campaign_id=campaign.id,
        name=campaign.name or "Campaign",
    )

    # Intent gate: when ON, discovery attaches leads as 'pending' and the
    # intent_sweep tick scores + enrolls only the accepted ones. When OFF,
    # leads enroll inline (legacy behaviour). Read once per run.
    gate_on = intent_agent.gate_enabled()

    by_source: Dict[str, int] = {}
    errors: List[str] = []
    total = 0

    import time as _time
    _discovery_started = _time.monotonic()
    # Run-start wall-clock — fences the pending-stamp to ONLY this run's leads,
    # so re-running an exhausted campaign never re-vets old leads.
    _run_started_at = datetime.utcnow()

    # TIME breakdown (seconds), summed across every window + round. Logged as a
    # plain-text block at the end so you can see where the run's time went.
    _timing = {
        "apollo_search": 0.0,   # fetch the filtered rows from Apollo
        "qualify_agent": 0.0,   # Agent #10 company-intent scoring (pre-reveal)
        "email_reveal": 0.0,    # Apollo bulk_match — qualified leads only
        "other_scoring": 0.0,   # post-attach scoring (no-domain / non-Apollo)
    }
    # Agent #10 API-call + cost counters (summed across all scoring waves this
    # run) — surfaced in a plain-text cost log next to the TIME BREAKDOWN.
    _agent10 = {
        "companies_scored": 0,  # grounded Gemini calls (1 per unique company)
        "google_searches": 0,   # total billed Google Search queries
        "derive_calls": 0,      # ungrounded product-signal calls (~1 per run)
    }

    def _fmt_mmss(sec: float) -> str:
        sec = max(0.0, float(sec))
        m = int(sec // 60)
        return f"{m:02d}:{sec - m * 60:05.2f}"

    # Page cursors — each pass advances from here; deepest advance is persisted
    # at the end. 0 for legacy campaigns pre-migration.
    last_apollo_page = int(getattr(campaign, "last_apollo_page", 0) or 0)
    last_apollo_broader_page = int(
        getattr(campaign, "last_apollo_broader_page", 0) or 0
    )

    # MIN_NEW_LEADS_TARGET = how many QUALIFIED (Agent #10-accepted) leads to
    # chase, across all revenue windows = the caller's max_leads. Single-pass:
    # ONE fetch of 2x this target, ONE scoring wave, done.
    MIN_NEW_LEADS_TARGET = max(1, int(max_leads))  # qualified leads to chase
    APOLLO_MIN_LEADS_PER_RUN = max(1, int(max_leads))  # per-pass Apollo floor

    # Plain-language narration so anyone reading the logs can follow the run.
    log.info(
        "LEAD DISCOVERY START — campaign %s (%r): user asked for %d qualified "
        "lead(s). Plan: pull up to %d candidate(s) from Apollo (2x the ask, "
        "since roughly half fail the quality check), have Agent #10 research "
        "each company on the live web, and keep only the ones that qualify.",
        campaign.id, campaign.name or "?", MIN_NEW_LEADS_TARGET,
        min(100, 2 * MIN_NEW_LEADS_TARGET) if gate_on else MIN_NEW_LEADS_TARGET,
    )

    # ── Product-level lead bank: reuse leads PAID FOR by earlier runs ────────
    # Excess leads from past runs of this product (already revealed = already
    # paid) are reclaimed FIRST, so Apollo is only asked for what's still
    # missing. Zero new credits for reclaimed leads.
    reclaimed_acc = reclaimed_pend = 0
    if gate_on:
        reclaimed_acc, reclaimed_pend = _reclaim_banked(
            db, campaign, MIN_NEW_LEADS_TARGET
        )
        if reclaimed_acc or reclaimed_pend:
            log.info(
                "BANK REUSE — campaign %s: found %d saved lead(s) from earlier "
                "runs of this product (%d already qualified, %d to re-check "
                "with Agent #10). These cost NO new Apollo credits.",
                campaign.id, reclaimed_acc + reclaimed_pend,
                reclaimed_acc, reclaimed_pend,
            )

    # UI shortfall reason: apollo_exhausted (no more matches) / credit_error.
    apollo_exhausted = False
    credit_error = False
    out_of_credits = False  # the funding user's Pipelyt credits ran out mid-run

    # Revenue windows: Apollo's revenue filter is one contiguous {min,max} and
    # can't express a gap, so NON-adjacent bands run as separate searches whose
    # leads union into this campaign. Adjacent bands arrive pre-merged (1 window).
    # Capped at 3 (the 6 dropdown bands form at most 3 non-adjacent groups).
    MAX_REVENUE_WINDOWS = 3
    revenue_windows = revenue_ranges_from_icp(icp.get("revenue_range"))
    if len(revenue_windows) > MAX_REVENUE_WINDOWS:
        log.warning(
            "discover_for_campaign: campaign %s — %d revenue windows requested, "
            "capping at %d (extras dropped)",
            campaign.id, len(revenue_windows), MAX_REVENUE_WINDOWS,
        )
        revenue_windows = revenue_windows[:MAX_REVENUE_WINDOWS]
    if len(revenue_windows) >= 2:
        icp_variants = [{**icp, "revenue_range": w} for w in revenue_windows]
    elif len(revenue_windows) == 1:
        icp_variants = [{**icp, "revenue_range": revenue_windows[0]}]
    else:
        icp_variants = [icp]  # no revenue filter — run the ICP as-is

    n_windows = len(icp_variants)

    # Option B — SCORE BEFORE REVEAL. Score each company in-memory first (the
    # raw search gives company_name; domains are HIDDEN until the paid reveal),
    # reveal only the winners. Verdicts cached by _company_cache_key (domain,
    # else 'name:<company name>') so no company is scored twice — neither here
    # across pages/windows nor by the post-attach _apply_cached_verdicts (which
    # replays the cached verdict rather than re-run the non-deterministic
    # grounded call). Wired in only when the gate is ON; OFF = legacy
    # reveal-everything.
    scored_cache: Dict[str, Any] = {}  # company key -> Agent #10 verdict dict
    _scoring_ctx: Dict[str, Any] = {}  # lazily-filled product_context + signal

    def _ensure_scoring_ctx() -> None:
        if _scoring_ctx:
            return
        from nexus.services import intent_sweep

        pc = intent_sweep._load_product_context(db, campaign, icp)
        qs = (icp.get("qualifying_signal") or "") if isinstance(icp, dict) else ""
        if not qs:
            _agent10["derive_calls"] += 1
            qs = intent_agent.derive_qualifying_signal(pc) or ""
            if qs:
                try:
                    _icp = dict(campaign.icp or {})
                    _icp["qualifying_signal"] = qs
                    campaign.icp = _icp
                    db.commit()
                except Exception:  # noqa: BLE001
                    try:
                        db.rollback()
                    except Exception:
                        pass
        _scoring_ctx["product_context"] = pc
        _scoring_ctx["qualifying_signal"] = qs

    async def _pre_reveal_score(
        cands: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Return only the candidates worth revealing: company qualified, or
        nothing to research it by (fail-open → include). Companies are keyed
        by domain when present, else by COMPANY NAME — Apollo's search hides
        domains until the paid reveal, and keying by domain alone made this
        hook a NO-OP (every candidate fail-opened, every email was revealed
        before Agent #10 ever ran). Verdicts cached by key so the same
        company is scored at most once per run."""
        keep: List[Dict[str, Any]] = []
        to_score: Dict[str, List[Dict[str, Any]]] = {}
        for c in cands:
            key = _company_cache_key(
                c.get("company_domain"), c.get("company_name")
            )
            if not key:
                keep.append(c)  # no domain AND no name → include by default
                continue
            if key in scored_cache:
                v = scored_cache[key]
                if not isinstance(v, dict) or v.get("accepted"):
                    if isinstance(v, dict):
                        # Carry the verdict score downstream — duplicate
                        # markers show it in the MATCH pill.
                        c["_agent10_score"] = int(v.get("intent_score") or 0)
                    keep.append(c)
                continue
            to_score.setdefault(key, []).append(c)

        if to_score:
            _ensure_scoring_ctx()
            pc = _scoring_ctx["product_context"]
            qs = _scoring_ctx["qualifying_signal"]
            sem = asyncio.Semaphore(_PRE_REVEAL_CONCURRENCY)
            n_by_name = sum(1 for k in to_score if k.startswith("name:"))
            log.info(
                "STEP 2/3 QUALIFY (before paying) — campaign %s: Agent #10 "
                "researching %d compan(ies) on the web%s. Emails are revealed "
                "(credits spent) ONLY for the ones that qualify.",
                campaign.id, len(to_score),
                (f" ({n_by_name} by company name — Apollo hides domains "
                 "until the reveal)") if n_by_name else "",
            )

            async def _score_dom(key: str, members: List[Dict[str, Any]]):
                first = members[0]
                dom = (first.get("company_domain") or "").strip().lower() or None
                async with sem:
                    # intent_sweep's DEDICATED executor, not asyncio.to_thread:
                    # the default thread pool is min(32, cpu+4) (~12 on a
                    # laptop, ~6 on Lambda), which silently split the "all in
                    # parallel" wave into sub-waves that each waited on their
                    # slowest call. The calls are I/O-bound (threads just wait
                    # on Gemini), so the wide pool is safe on any core count.
                    from nexus.services.intent_sweep import _SCORE_EXECUTOR
                    loop = asyncio.get_running_loop()
                    verdict = await loop.run_in_executor(
                        _SCORE_EXECUTOR,
                        functools.partial(
                            intent_agent.score_company_intent,
                            company_name=first.get("company_name") or (dom or ""),
                            domain=dom,
                            person_title=first.get("role"),
                            campaign_icp=icp,
                            product_context=pc,
                            qualifying_signal=qs,
                        ),
                    )
                return key, verdict

            _tq0 = _time.monotonic()  # TIME: qualify agent (Agent #10)
            results = await asyncio.gather(
                *(_score_dom(d, m) for d, m in to_score.items())
            )
            _timing["qualify_agent"] += _time.monotonic() - _tq0
            # Tally Agent #10 API calls + billed Google searches for the cost log.
            _agent10["companies_scored"] += len(results)
            _agent10["google_searches"] += sum(
                int(v.get("web_searches") or 0)
                for _, v in results if isinstance(v, dict)
            )
            for dom, verdict in results:
                if not isinstance(verdict, dict):
                    continue
                if verdict.get("gate_decision") == "error":
                    # Transient scoring error — DON'T cache (allow a later
                    # retry) and DON'T pay to reveal an unverified company.
                    continue
                scored_cache[dom] = verdict
                if verdict.get("accepted"):
                    for _c in to_score[dom]:
                        # Carry the verdict score downstream — duplicate
                        # markers show it in the MATCH pill.
                        _c["_agent10_score"] = int(verdict.get("intent_score") or 0)
                    keep.extend(to_score[dom])
        return keep

    # Inner driver — full retry loop for ONE icp window, chasing `target` new
    # leads. Walks from the passed-in cursors (so the outer loop can go deeper
    # each round) over this window's OWN Apollo result set. No intent
    # stamping / sequences here — those run once after all windows complete.
    async def _find_leads(icp_for_pass, target, start_strict, start_broader,
                          db_sess: Session):
        nonlocal credit_error, out_of_credits
        local_total = 0
        local_by_source: Dict[str, int] = {}
        local_errors: List[str] = []
        cur_strict = start_strict
        cur_broader = start_broader
        pages_total = 0
        broader_total = 0
        retries = 0
        # Per-window exhaustion — local to THIS window's query and RETURNED to
        # the caller. One drained window must never halt the others: they are
        # distinct Apollo queries with independent ends-of-results.
        window_exhausted = False
        while True:
            # Ask Apollo for exactly the rows still needed this window, capped at
            # Apollo's per_page max (100). `target` is already over-fetched for
            # the qualify rate by the caller, so this is the raw-row count.
            remaining_needed = max(1, target - local_total)
            pass_max_leads = min(100, remaining_needed)
            apollo_stats: Dict[str, Any] = {}
            gen = lead_discovery.autonomous_discover(
                db_sess,
                workspace_id=campaign.workspace_id,
                icp=icp_for_pass,
                campaign_id=campaign.id,
                product_id=campaign.product_id,
                target_domains=target_domains,
                sources=sources,
                max_leads=pass_max_leads,
                min_icp_score=min_icp_score,
                apollo_start_page=cur_strict + 1,
                apollo_stats=apollo_stats,
                apollo_min_leads=APOLLO_MIN_LEADS_PER_RUN,
                apollo_broader_start_page=cur_broader + 1,
                defer_enrollment=gate_on,
                # Option B: score + filter companies BEFORE the email reveal
                # so credits are spent on qualified leads only. Gate-off runs
                # keep the legacy "reveal everything" path (hook = None).
                apollo_pre_reveal_filter=(_pre_reveal_score if gate_on else None),
                # Cap the known-people top-up at the USER's ask, not the
                # over-fetch — the table must never show more "Already in
                # campaign" rows than the user requested.
                user_target=MIN_NEW_LEADS_TARGET,
            )
            async for chunk in gen:
                # chunk is an SSE-formatted "data: {...}\n\n" string.
                try:
                    payload = json.loads(chunk.split("data: ", 1)[1].strip())
                except Exception:  # noqa: BLE001
                    continue
                t = payload.get("type")
                if t == "lead":
                    local_total += 1
                    src = payload.get("source") or "unknown"
                    local_by_source[src] = local_by_source.get(src, 0) + 1
                    if local_total >= target:
                        break
                elif t == "error":
                    local_errors.append(
                        f"{payload.get('source')}: {payload.get('message')}"
                    )
                elif t == "done":
                    break

            pages_walked = int(apollo_stats.get("pages_walked") or 0)
            broader_walked = int(apollo_stats.get("broader_pages_walked") or 0)
            # TIME: accumulate this pass's Apollo-search + reveal time.
            _timing["apollo_search"] += float(apollo_stats.get("t_apollo_search") or 0.0)
            _timing["email_reveal"] += float(apollo_stats.get("t_bulk_match") or 0.0)
            if apollo_stats.get("apollo_credit_error"):
                credit_error = True
            if apollo_stats.get("out_of_credits"):
                out_of_credits = True
            if apollo_stats.get("apollo_exhausted"):
                window_exhausted = True
            cur_strict += pages_walked
            cur_broader += broader_walked
            pages_total += pages_walked
            broader_total += broader_walked

            # Single fetch pass (2026-06-10): ONE search + ONE reveal batch
            # per window per run. The old retry loop here chased `target`
            # ATTACHED leads and paid to reveal REPLACEMENTS for every
            # candidate that didn't attach (no email returned, or person
            # already on this product — invisible before the reveal because
            # Apollo HIDES emails and domains in search rows). That turned a
            # 10-reveal budget into 20+. Reveals per run are now structurally
            # capped at the over-fetch count (2x the ask); a shortfall is
            # reported as 'partial' instead of bought back with more credits.
            break
        return (local_total, local_by_source, local_errors,
                pages_total, broader_total, retries, window_exhausted)

    retry_idx = 0
    # Outer loop: each ROUND runs all ACTIVE revenue windows to attach a batch
    # of raw leads, scores them inline, and counts how many CLEARED the gate.
    # Rounds repeat (walking deeper Apollo pages) until enough QUALIFIED leads
    # exist, every window drains, or the round cap.
    # ACCEPTED leads so far (== total when gate off). Starts at the count the
    # bank restored — those already count toward the user's target.
    qualified = reclaimed_acc
    # Per-window cursors + exhaustion. Each revenue window is a DISTINCT Apollo
    # query with its OWN pagination and end-of-results, so they must not share a
    # cursor or an exhausted flag. Sharing them caused two bugs: (1) one drained
    # window flipped a run-wide flag that halted every other window, and (2) a
    # shallow window's cursor was advanced by the DEEPEST window, skipping the
    # shallow window's unread pages. Indexed by position in icp_variants.
    win_cur_strict = [last_apollo_page] * n_windows
    win_cur_broader = [last_apollo_broader_page] * n_windows
    win_exhausted = [False] * n_windows
    round_idx = 0
    while True:
        _have = qualified if gate_on else total
        if _have >= MIN_NEW_LEADS_TARGET:
            break
        # Every window drained → nothing left for this ICP across all bands.
        if all(win_exhausted):
            apollo_exhausted = True
            break
        round_need = MIN_NEW_LEADS_TARGET - _have
        # Plan #1: OVER-FETCH raw leads so ONE parallel scoring wave yields
        # ~round_need qualified (gate rate < 100%). round_fetch = ceil(round_need
        # ÷ rate), capped at Apollo's per_page max (100). This collapses the old
        # multiple sequential scoring rounds into ~one.
        round_fetch = (
            min(100, max(round_need,
                         -(-round_need * 100 // max(1, int(_EXPECTED_QUALIFY_RATE * 100)))))
            if gate_on else round_need
        )
        # Reclaimed-but-unscored bank leads get scored in THIS round's wave,
        # so they reduce how many NEW candidates Apollo is asked for.
        if gate_on and reclaimed_pend:
            round_fetch = max(0, round_fetch - reclaimed_pend)
        round_attached = 0

        # This round's fetch budget is split across the windows still ACTIVE
        # (not yet drained); each walks its OWN result set from its OWN cursor
        # and advances only itself.
        active_idxs = [i for i in range(n_windows) if not win_exhausted[i]]
        if round_fetch > 0:
            log.info(
                "STEP 1/3 FETCH — campaign %s: need %d qualified lead(s); "
                "pulling up to %d candidate(s) from Apollo (search rows are "
                "free; emails are NOT revealed yet) across %d search "
                "window(s).",
                campaign.id, round_need, round_fetch, len(active_idxs),
            )
        else:
            log.info(
                "STEP 1/3 FETCH — campaign %s: the product's lead bank covers "
                "this run — NO new Apollo pull needed.",
                campaign.id,
            )
        # Windows run CONCURRENTLY (2026-06-11): the expensive stage is Agent
        # #10's pre-reveal research, and running windows back-to-back made a
        # 2-window run pay two sequential scoring waves (5 then 5, ~45s+60s)
        # instead of ONE combined wave (10 at once, ~60s). Each window gets
        # its own DB session (a Session must not be shared by interleaving
        # tasks) and an even ceil-share of the fetch budget. Trade-off vs the
        # old sequential loop: an under-delivering window no longer rolls its
        # unused budget into the next window — single-pass mode reports any
        # shortfall as 'partial' either way. The shared `scored_cache` /
        # `_timing` dicts are only touched in sync segments on the event-loop
        # thread, so no locking is needed.
        async def _run_window(vi: int, win_target: int):
            from core.database import SessionLocal
            sess = SessionLocal()
            try:
                res = await _find_leads(
                    icp_variants[vi], win_target,
                    win_cur_strict[vi], win_cur_broader[vi], sess,
                )
                return vi, win_target, res
            finally:
                sess.close()

        window_results = []
        if round_fetch > 0 and active_idxs:
            win_share = -(-round_fetch // len(active_idxs))  # ceil split
            window_results = await asyncio.gather(
                *(_run_window(vi, win_share) for vi in active_idxs)
            )
        for vi, win_target, (v_total, v_by_source, v_errors, v_pages,
                             v_broader, v_retries, v_exhausted) in window_results:
            total += v_total
            round_attached += v_total
            for k, v in v_by_source.items():
                by_source[k] = by_source.get(k, 0) + v
            errors.extend(v_errors)
            # Advance THIS window's own cursor by what IT walked — never by a
            # sibling's depth — and mark it drained if Apollo had no more.
            win_cur_strict[vi] += v_pages
            win_cur_broader[vi] += v_broader
            if v_exhausted:
                win_exhausted[vi] = True
            retry_idx += v_retries
            if n_windows > 1:
                log.info(
                    "discover_for_campaign: campaign %s - revenue window %d/%d "
                    "(%s) -> %d (win target %d, running total %d/%d%s)",
                    campaign.id, vi + 1, n_windows,
                    icp_variants[vi].get("revenue_range"), v_total, win_target,
                    total, MIN_NEW_LEADS_TARGET,
                    ", drained" if win_exhausted[vi] else "",
                )

        # B-count: score this round's attached leads, recount QUALIFIED.
        if gate_on:
            log.info(
                "STEP 2/3 FINALIZE — campaign %s: %d revealed lead(s) "
                "attached + %d reclaimed from the bank; stamping the Agent "
                "#10 verdicts computed BEFORE the paid reveal, and scoring "
                "anything still unscored (bank reclaims / unidentifiable "
                "candidates).",
                campaign.id, round_attached, reclaimed_pend,
            )
            _stamp_pending_for_run(db, campaign, _run_started_at, total)
            # Option B: the Apollo leads we revealed were ALREADY scored
            # (accepted) in the pre-reveal hook — replay those cached verdicts
            # so they're marked accepted without a second grounded run.
            _apply_cached_verdicts(db, campaign, _run_started_at, scored_cache)
            # Score whatever's still pending the normal way: no-domain Apollo
            # leads (fail-open) + non-Apollo sources (e.g. github) that never
            # went through the reveal hook.
            _ts0 = _time.monotonic()  # TIME: post-attach scoring
            await _score_pending_until_done(
                db, campaign, target_accepted=MIN_NEW_LEADS_TARGET,
            )
            _timing["other_scoring"] += _time.monotonic() - _ts0
            qualified = _count_accepted(db, campaign.id)
            log.info(
                "STEP 2/3 RESULT — campaign %s: %d of the %d requested "
                "lead(s) passed the quality check; %d candidate(s) were "
                "rejected or left unscored.",
                campaign.id, qualified, MIN_NEW_LEADS_TARGET,
                max(0, total - qualified),
            )
        else:
            qualified = total

        log.info(
            "discover_for_campaign: campaign %s - round %d attached=%d "
            "qualified=%d / target=%d",
            campaign.id, round_idx, total, qualified, MIN_NEW_LEADS_TARGET,
        )

        if (round_fetch > 0 and round_attached == 0
                and qualified < MIN_NEW_LEADS_TARGET):
            # We DID ask Apollo and got nothing — no more matches for any band.
            # (round_fetch == 0 means the bank covered the run; not exhaustion.)
            apollo_exhausted = True
        # Single pass (2026-06-10): ONE fetch+score round only — pull 2x the
        # requested count, score it, and surface whatever qualified. No second
        # fetch+score round: the run costs ~one scoring wave (predictable
        # wall-clock) and a shortfall is reported as reason='partial' instead
        # of walking deeper Apollo pages.
        break

    # Exact-count delivery + BANKING: if we MET the qualified target, the
    # surplus is SAVED to the product's lead bank (hidden from the UI) instead
    # of discarded — both leftover un-scored leads (emails already paid for)
    # and any accepted overshoot beyond the requested count. The next run for
    # this product reclaims them before paying Apollo again. If we fell short,
    # leftovers stay pending for the background sweep to keep working.
    if gate_on and qualified >= MIN_NEW_LEADS_TARGET:
        _banked_pend = _bank_surplus_pending(db, campaign)
        _banked_exc = _bank_excess_accepted(
            db, campaign, _run_started_at, MIN_NEW_LEADS_TARGET
        )
        if _banked_exc:
            qualified = _count_accepted(db, campaign.id)
        if _banked_pend or _banked_exc:
            log.info(
                "BANK SAVE — campaign %s: target %d met; saved %d extra "
                "lead(s) to this product's bank for future runs (%d already "
                "qualified, %d not yet scored). Reusing them later costs NO "
                "new Apollo credits. Only the requested count shows in the UI.",
                campaign.id, MIN_NEW_LEADS_TARGET, _banked_pend + _banked_exc,
                _banked_exc, _banked_pend,
            )

    # Run-fenced qualified count — the EXACT number the New-Run UI shows
    # (its latest_run fence keys on created_at, which attaches, campaign
    # moves and bank reclaims all stamp with this run's clock). The loop /
    # target logic above stays campaign-wide (B-mode semantics); everything
    # REPORTED from here down uses this, so the log and the UI can't
    # disagree.
    run_qualified = (
        _count_accepted_since(db, campaign.id, _run_started_at)
        if gate_on else total
    )

    # "Already in campaign" marker rows created by THIS run (run-fenced).
    # Surfaced to /analyze so the UI can say "N already in campaign" instead
    # of a misleading "0 leads found" when the pool is fully mined.
    try:
        run_duplicates = int(db.execute(
            text(
                """SELECT count(*) FROM nexus_leads
                    WHERE campaign_id = :cid
                      AND created_at >= :since
                      AND signals->'intent'->>'drop_reason' = 'duplicate'"""
            ),
            {"cid": campaign.id, "since": _run_started_at},
        ).scalar() or 0)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        run_duplicates = 0

    # Deepest window's net advance = what we persist as the single campaign
    # cursor. The deepest window is the one still surfacing leads; drained
    # shallow windows correctly re-report empty (and self-skip) on the next run.
    total_pages_walked = (
        (max(win_cur_strict) - last_apollo_page) if win_cur_strict else 0
    )
    total_broader_walked = (
        (max(win_cur_broader) - last_apollo_broader_page) if win_cur_broader else 0
    )

    # ── Persist final cursor values ───────────────────────────────────
    # Best-effort. If this commit fails, the next run repeats some
    # work, but no leads are lost (lead inserts already committed via
    # autonomous_discover's per-lead writes).
    if total_pages_walked > 0 or total_broader_walked > 0:
        try:
            campaign.last_apollo_page = last_apollo_page + total_pages_walked
            campaign.last_apollo_broader_page = (
                last_apollo_broader_page + total_broader_walked
            )
            db.commit()
            log.info(
                "discover_for_campaign: advanced campaign %s cursors "
                "strict %d -> %d, broader %d -> %d "
                "(retries=%d, total new leads=%d)",
                campaign.id,
                last_apollo_page, campaign.last_apollo_page,
                last_apollo_broader_page, campaign.last_apollo_broader_page,
                retry_idx, total,
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "discover_for_campaign: failed to persist apollo cursors "
                "for campaign %s — next run will repeat the window",
                campaign.id, exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass

    # Intent scoring is done inline above (per round). Any leads left pending
    # (scorer hit its bound) keep the intent_pending flag, so the background
    # intent_sweep finishes them — nothing stranded. Email generation is NOT
    # awaited here; it runs in a BackgroundTask after /analyze responds so the
    # leads table shows immediately, with the sequencer tick as the safety net.
    log.info(
        "[TIMING] Lead discovery took %s — found %d of %d target leads for campaign %s "
        "(walked %d Apollo pages across %d retries)",
        _fmt_mmss(_time.monotonic() - _discovery_started),
        total, MIN_NEW_LEADS_TARGET, campaign.id,
        total_pages_walked + total_broader_walked, retry_idx,
    )
    log.info(
        "NEXUS RUN [1/4 DISCOVERY] campaign %s — found %d lead(s) from Apollo%s",
        campaign.id, total,
        (" (by source: " + ", ".join(f"{k}={v}" for k, v in (by_source or {}).items()) + ")")
        if by_source else "",
    )
    # Final qualified count (gate off → equals attached `total`).
    if not gate_on:
        qualified = total

    # ── TIME BREAKDOWN (plain text) ───────────────────────────────────────
    # The 4 lines ALWAYS sum to TOTAL: "Other" is computed as the remainder
    # (TOTAL - measured stages), so no time is ever silently missing. All
    # Agent #10 time (pre-reveal hook + post-attach pass) is one "qualify" line.
    _total_s = _time.monotonic() - _discovery_started
    _qualify_s = _timing["qualify_agent"] + _timing["other_scoring"]  # all Agent #10
    _measured_s = _timing["apollo_search"] + _timing["email_reveal"] + _qualify_s
    _other_s = max(0.0, _total_s - _measured_s)

    def _secs(x: float) -> str:
        return f"{float(x):7.1f}s"

    log.info(
        "\n"
        "================ TIME BREAKDOWN - campaign %s ================\n"
        "  1. Lead identification (Apollo search, filtered rows) : %s\n"
        "  2. Qualify Agent #10   (company web research, Google) : %s\n"
        "  3. Email reveal        (Apollo bulk_match, qualified) : %s\n"
        "  4. Other               (dedup, attach, db, overhead)  : %s\n"
        "  -------------------------------------------------------------\n"
        "  TOTAL discovery time                                  : %s  (%s)\n"
        "  Leads: %d revealed  ->  %d qualified   (target %d)\n"
        "=============================================================",
        campaign.id,
        _secs(_timing["apollo_search"]),
        _secs(_qualify_s),
        _secs(_timing["email_reveal"]),
        _secs(_other_s),
        _secs(_total_s), _fmt_mmss(_total_s),
        total, run_qualified, MIN_NEW_LEADS_TARGET,
    )

    # ── AGENT #10 — API CALLS & COST (plain text) ─────────────────────────
    # The cost driver is GOOGLE SEARCHES, not Gemini calls: each company is
    # ONE grounded Gemini call, but that call fires several billed Google
    # searches. This block makes the call/search counts explicit so cost is
    # easy to read off a run.
    _gem_calls = _agent10["companies_scored"] + _agent10["derive_calls"]
    _avg_searches = (
        _agent10["google_searches"] / _agent10["companies_scored"]
        if _agent10["companies_scored"] else 0.0
    )
    log.info(
        "\n"
        "============ AGENT #10 - API CALLS & COST - campaign %s ============\n"
        "  Companies researched (1 grounded Gemini call each) : %d\n"
        "  Product-signal calls (ungrounded, ~1 per run)      : %d\n"
        "  ------------------------------------------------------------\n"
        "  TOTAL Gemini API calls                             : %d\n"
        "  Google Search queries  (BILLED - the real cost)    : %d\n"
        "  Avg Google searches per company                    : %.1f\n"
        "===================================================================",
        campaign.id,
        _agent10["companies_scored"],
        _agent10["derive_calls"],
        _gem_calls,
        _agent10["google_searches"],
        _avg_searches,
    )

    # ── Shortfall reason for the UI — measured on QUALIFIED leads ─────
    #   ok             — found the requested count of qualified leads
    #   credits        — Apollo account out of credits (top up)
    #   no_matches     — 0 qualified (filters match nobody / all rejected/pitched)
    #   partial        — some qualified but fewer than requested; Apollo drained
    if qualified >= MIN_NEW_LEADS_TARGET:
        reason = "ok"
    elif out_of_credits:
        reason = "out_of_credits"
    elif credit_error:
        reason = "credits"
    elif qualified == 0:
        reason = "no_matches"
    else:
        reason = "partial"

    _outcome_text = {
        "ok": "delivered everything the user asked for",
        "out_of_credits": "STOPPED — the user ran out of Pipelyt credits; only "
                          "the leads the remaining credits covered were "
                          "delivered. Buy more credits to continue",
        "credits": "STOPPED — the Apollo account is out of credits; top up "
                   "and run again",
        "no_matches": "found NOTHING — either the filters matched nobody on "
                      "Apollo, or Agent #10 rejected every company it "
                      "researched (filters may be too strict or a poor fit "
                      "for the product)",
        "partial": "fell short — the candidate pool didn't yield enough "
                   "qualified companies (single-pass mode: we don't keep "
                   "re-fetching; what passed is shown)",
    }.get(reason, reason)
    log.info(
        "STEP 3/3 DONE — campaign %s: %d qualified lead(s) added by THIS run "
        "(asked: %d; candidates revealed this run: %d — reveals are what "
        "consume Apollo credits). This number == what the New-Run table "
        "shows. Outcome: %s.",
        campaign.id, run_qualified, MIN_NEW_LEADS_TARGET, total, _outcome_text,
    )

    return {
        # leads_attached reports THIS RUN's QUALIFIED (Accepted) leads —
        # run-fenced exactly like the New-Run UI, so the banner, the log and
        # the table always say the same number.
        "leads_attached": run_qualified,
        "revealed": total,            # raw revealed/attached this run (credits)
        "by_source": by_source,
        "errors": errors,
        "campaign_id": campaign.id,
        "started_at": datetime.utcnow().isoformat(),
        "apollo_pages_walked": total_pages_walked,
        "apollo_broader_pages_walked": total_broader_walked,
        "apollo_next_page": last_apollo_page + total_pages_walked + 1,
        "apollo_next_broader_page": last_apollo_broader_page + total_broader_walked + 1,
        "retries": retry_idx,
        "min_target_hit": qualified >= MIN_NEW_LEADS_TARGET,
        # How many were requested + WHY we stopped short, so /analyze can show
        # a clear UI message.
        "requested": MIN_NEW_LEADS_TARGET,
        "reason": reason,
        "duplicates": run_duplicates,
        "apollo_exhausted": apollo_exhausted,
        "credit_error": credit_error,
        # Pipelyt credits ran out mid-run → UI shows a partial set + Buy Credits.
        "out_of_credits": out_of_credits,
        "delivered_count": run_qualified,
    }


__all__ = ["discover_for_campaign"]
