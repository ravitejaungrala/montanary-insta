"""Unified discovery interface that fans out to all six sources in parallel.

Used by both ``/nexus/leads/autonomous`` (multi-source SSE) and
``/nexus/leads/accurate`` (single-source synchronous).

Pipeline per source:
    1. Call provider with the ICP filters.
    2. Normalise each candidate to a common shape.
    3. Upsert into ``nexus_global_leads`` (dedupe on email).
    4. Enrich domain (cached) and run ICP scoring.
    5. Attach into ``nexus_leads`` under the calling workspace if score >= threshold.

All progress events are yielded as ``DiscoveryProgressEvent``-shaped dicts so
the router can stream them to the client.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nexus.models_phase3 import (
    NexusGlobalLead,
    NexusIntentSignal,
    NexusLead,
)
from nexus.models_phase4 import NexusLeadSequence, NexusSequence
from nexus.services import (
    discovery_apollo,
    discovery_github,
    discovery_hunter,
    discovery_reddit,
)
from nexus.services.email_verifier import verify_email
from nexus.services.icp_scorer import score_icp_fit
from nexus.services.sequencer import compute_step_schedule

logger = logging.getLogger(__name__)

# Discovery is APOLLO-ONLY (2026-06-09). The other sources were removed:
#   - github  : query degraded to a static "hiring" search → ICP-unrelated leads
#   - reddit  : returns 403 (blocked), no leads
#   - website : scraped the product's own domain, not prospects
#   - hunter  : needs a target-domain list we don't build
#   - linkedin: partner-API gated, always a no-op
# Only Apollo respects the user's 5 ICP filters, so it's the single source of
# truth. The _run_<source> adapters + dispatch branches remain (dead) so a
# caller can still re-enable a source by passing `sources=[...]` explicitly.
DEFAULT_SOURCES: Tuple[str, ...] = ("apollo",)

# Parallel attach (2026-06-11): worker threads per chunk in autonomous_discover.
# Each worker holds ONE pooled connection for ~2-3s; with up to 3 revenue
# windows discovering concurrently the worst case is 3×6=18 connections —
# comfortably inside the engine pool (pool_size=10 + max_overflow=20), leaving
# headroom for the window sessions + the request session. Env-tunable.
import os as _os
_ATTACH_CONCURRENCY = max(1, int(_os.getenv("NEXUS_ATTACH_CONCURRENCY", "6") or "6"))


# ---------------------------------------------------------------------------
# Per-source adapters — each returns ``(leads, intent_signals)``.
# ---------------------------------------------------------------------------
async def _run_apollo(
    db: Session,
    icp: Dict[str, Any],
    max_leads: int,
    workspace_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    # 2026-05-29: product_id added so search_people can pre-dedup
    # candidates against the product's existing lead pool BEFORE paying
    # Apollo credits on bulk_match. Saves credits + latency on re-runs.
    product_id: Optional[int] = None,
    # Optional page-progression + min-yield knobs. Default values keep
    # behaviour identical to pre-page-progression callers; only the
    # discover_for_campaign path opts in. Both forwarded through to
    # discovery_apollo.search_people unchanged.
    start_page: int = 1,
    stats: Optional[Dict[str, Any]] = None,
    min_leads: int = 1,
    broader_start_page: int = 1,
    # Option B (2026-06-09): forwarded straight to search_people. Scores +
    # filters candidates BEFORE the email reveal so credits are spent only
    # on qualified leads. None = reveal everything (legacy).
    pre_reveal_filter: Optional[Any] = None,
    user_target: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    leads = await discovery_apollo.search_people(
        db,
        icp,
        max_leads=max_leads,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        start_page=start_page,
        stats=stats,
        min_leads=min_leads,
        broader_start_page=broader_start_page,
        pre_reveal_filter=pre_reveal_filter,
        user_target=user_target,
    )
    return leads, []


async def _run_hunter(
    db: Session, icp: Dict[str, Any], max_leads: int, target_domains: Optional[List[str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    out: List[Dict[str, Any]] = []
    domains = list(target_domains or [])
    if not domains:
        # Fall back: nothing to enumerate when no domains are known.
        return out, []
    per_domain = max(1, max_leads // max(1, len(domains)))
    for d in domains:
        out.extend(
            await discovery_hunter.enumerate_domain(
                d,
                max_leads=per_domain,
                roles=icp.get("roles"),
                seniority=icp.get("seniority"),
            )
        )
        if len(out) >= max_leads:
            break
    return out[:max_leads], []


async def _run_github(
    db: Session, icp: Dict[str, Any], max_leads: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    leads = await discovery_github.search_users(icp, max_leads=max_leads)
    return leads, []


async def _run_reddit(
    db: Session, icp: Dict[str, Any], max_leads: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    signals = await discovery_reddit.search_pain_points(icp, max_results=max_leads)
    return [], signals


async def _run_linkedin(
    db: Session, icp: Dict[str, Any], max_leads: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    # LinkedIn API access requires partner approval — Phase 3 keeps this as a
    # documented stub. Returning empty preserves the six-source surface.
    logger.info("linkedin discovery is intentionally a no-op (partner API gated)")
    return [], []


async def _run_website(
    db: Session, icp: Dict[str, Any], max_leads: int, target_domains: Optional[List[str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Naive on-site email extractor — scans homepage HTML for mailto links."""
    import re

    import httpx

    out: List[Dict[str, Any]] = []
    if not target_domains:
        return out, []

    pattern = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for domain in target_domains:
            try:
                r = await client.get(f"https://{domain}")
                emails = set(m.lower() for m in pattern.findall(r.text or ""))
            except Exception:  # noqa: BLE001
                continue
            for em in emails:
                out.append(
                    {
                        "email": em,
                        "first_name": None,
                        "last_name": None,
                        "role": None,
                        "company_domain": domain.lower(),
                        "company_name": None,
                        "source": "website",
                        "raw": {},
                    }
                )
                if len(out) >= max_leads:
                    return out, []
    return out, []


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _upsert_global_lead(
    db: Session, payload: Dict[str, Any]
) -> Optional[NexusGlobalLead]:
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return None
    row = db.query(NexusGlobalLead).filter(NexusGlobalLead.email == email).first()
    if row is None:
        row = NexusGlobalLead(email=email)
        db.add(row)
    # Only fill in missing fields — never overwrite human-curated values.
    # 2026-05-29: added person_city / person_state / person_country /
    # organization_industry. These drive the Location + Industry columns
    # on the new leads table.
    _UPSERT_FIELDS = (
        "first_name", "last_name", "role",
        "company_domain", "company_name",
        "source", "linkedin_url",
        "person_city", "person_state", "person_country",
        "organization_industry",
        # 2026-06-02 — phone from Apollo. Same upsert rule: only filled
        # when previously NULL, never overwrites a human-edited value.
        "phone",
        # 2026-06-05 — LinkedIn headline (Apollo response; only filled when NULL).
        "linkedin_headline",
    )
    for col in _UPSERT_FIELDS:
        if not getattr(row, col, None) and payload.get(col):
            setattr(row, col, payload[col])
    # Apollo's stable person id (payload key is 'apollo_id') → canonical dedup
    # anchor. Only filled when previously NULL; never overwrites.
    if not getattr(row, "apollo_person_id", None) and (payload.get("apollo_id") or payload.get("id")):
        row.apollo_person_id = str(payload.get("apollo_id") or payload.get("id"))[:64]
    db.commit()
    db.refresh(row)
    return row


def _already_attached_for_product(
    db: Session,
    *,
    workspace_id: int,
    product_id: Optional[int],
    global_lead_id: int,
) -> bool:
    """Has this global_lead ever been attached to any campaign for this
    product (within this workspace)?

    Scope is intentionally PRODUCT-wide, not campaign-wide. The
    operator's mental model is "one URL = one outreach effort"; if a
    person was already pitched on Spenzo AI via any prior campaign on
    that product, they should not silently get re-pitched on a new run.

    Returns False (don't dedup) when product_id is missing — the caller
    has no product context to scope against. The downstream
    `_attach_workspace_lead` still de-dupes at the (workspace, lead)
    scope, so we're not creating multi-campaign duplicates; we just
    can't enforce the broader product fence without a product_id.
    """
    if product_id is None:
        return False
    return db.query(
        db.query(NexusLead)
        .filter(
            NexusLead.workspace_id == workspace_id,
            NexusLead.product_id == product_id,
            NexusLead.global_lead_id == global_lead_id,
        )
        .exists()
    ).scalar()


def _insert_duplicate_marker(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int],
    product_id: Optional[int],
    global_lead: NexusGlobalLead,
    icp_score: int,
    source: Optional[str],
    agent_score: Optional[int] = None,
) -> None:
    """Visible "already in campaign" marker (2026-06-11).

    An Agent #10-APPROVED candidate who is already attached to this product
    gets a marker row on the NEW campaign so the run's table can SHOW the
    person with an "Already in campaign" badge instead of silently dropping
    them. OUTREACH-PROOF BY CONSTRUCTION:
      - intent status 'rejected'  -> _auto_start_outreach skips it (never
        enrolled, so the sequencer can never email it),
      - drop_reason 'duplicate'   -> the bank reclaim (drop_reason='banked')
        never picks it up, and the GTM Journey (hides 'rejected') never
        shows it. Only the New-Run table renders it, badge-only.
    Best-effort: any failure is swallowed — a missing marker just means the
    old silent-skip behaviour for that one lead."""
    if campaign_id is None:
        return
    try:
        exists = (
            db.query(NexusLead.id)
            .filter(
                NexusLead.workspace_id == workspace_id,
                NexusLead.campaign_id == campaign_id,
                NexusLead.global_lead_id == global_lead.id,
            )
            .first()
        )
        if exists is not None:
            return
        # Which campaign already has this person? Shown in the badge
        # ("Already in z-ninth #65"). Best-effort: None -> generic text.
        # EXCLUDES other duplicate markers: without the coalesce filter a
        # SECOND re-discovery would pick the newest row — the previous
        # run's MARKER — and the badge would name a campaign where nothing
        # was ever sent (with that marker's date). The origin must always
        # be a REAL lead row.
        from sqlalchemy import func as _sa_func
        from nexus.models_phase3 import NexusCampaign
        orig = (
            db.query(NexusLead.campaign_id, NexusCampaign.name,
                     NexusLead.created_at)
            .outerjoin(NexusCampaign, NexusCampaign.id == NexusLead.campaign_id)
            .filter(
                NexusLead.workspace_id == workspace_id,
                NexusLead.product_id == product_id,
                NexusLead.global_lead_id == global_lead.id,
                _sa_func.coalesce(
                    NexusLead.signals["intent"]["drop_reason"].astext, ""
                ) != "duplicate",
            )
            .order_by(NexusLead.id.desc())
            .first()
        )
        dup_campaign_id = orig.campaign_id if orig else None
        dup_campaign_name = (orig.name if orig else None) or ""
        dup_contacted_at = (
            orig.created_at.isoformat() if orig and orig.created_at else ""
        )
        _score = int(agent_score) if agent_score else 0
        db.add(NexusLead(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
            global_lead_id=global_lead.id,
            # MATCH pill: Agent #10's re-vet verdict when we have it; the
            # firmographic icp_score (often a flat 100) is misleading here.
            icp_score=_score if _score else icp_score,
            signals={
                "source": source,
                "intent": {
                    "status": "rejected",
                    "drop_reason": "duplicate",
                    "dup_campaign_id": dup_campaign_id,
                    "dup_campaign_name": dup_campaign_name,
                    "dup_contacted_at": dup_contacted_at,
                    "reason": (
                        f"Already contacted in campaign \"{dup_campaign_name}\""
                        if dup_campaign_name else
                        "Already contacted in an earlier campaign for this product"
                    ),
                    "score": _score,
                    "signals": [],
                    "attempts": 0,
                },
            },
        ))
        db.commit()
    except IntegrityError:
        # Concurrent worker inserted the same marker — fine, it exists.
        db.rollback()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            "duplicate-marker insert failed for gl=%s campaign=%s (non-fatal)",
            global_lead.id, campaign_id, exc_info=True,
        )


def _attach_workspace_lead(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int],
    product_id: Optional[int],
    global_lead: NexusGlobalLead,
    icp_score: int,
    signals: Optional[Dict[str, Any]] = None,
) -> NexusLead:
    # One attachment per (workspace, global_lead) — latest enrollment
    # wins. If the lead is already attached in this workspace under a
    # different campaign, we MOVE it onto the new campaign rather than
    # creating a second row. Multi-campaign attachments broke demo
    # product attribution (a Pipelyt lead re-enrolled into a Spenzo
    # campaign got Spenzo briefings) — see scripts/dedupe_nexus_leads.py.
    # Skip duplicate-marker rows (2026-06-11): a person can now carry badge
    # markers on later campaigns. Moving/reusing a MARKER here instead of
    # the person's REAL lead row would strand the real row and turn an
    # informational marker into a live lead. Markers are never the
    # attachment of record.
    from sqlalchemy import func as _sa_func
    existing = (
        db.query(NexusLead)
        .filter(
            NexusLead.workspace_id == workspace_id,
            NexusLead.global_lead_id == global_lead.id,
            _sa_func.coalesce(
                NexusLead.signals["intent"]["drop_reason"].astext, ""
            ) != "duplicate",
        )
        .order_by(NexusLead.id.desc())
        .first()
    )
    if existing:
        moved = campaign_id is not None and existing.campaign_id != campaign_id
        if moved:
            existing.campaign_id = campaign_id
            # INVARIANT: a lead MOVED to a new campaign behaves exactly like
            # a freshly attached lead.
            #   1. Bump created_at — every run fence (the New-Run UI's
            #      latest_run filter, pending-stamp, cached-verdict replay,
            #      surplus banking) keys on it. Keeping the OLD timestamp
            #      made the backend log "2 qualified" while the New-Run
            #      table showed only 1 (the moved row was fenced out).
            existing.created_at = datetime.utcnow()
            #   2. Drop the OLD campaign's intent verdict — it was judged in
            #      a different campaign context. This run stamps the row
            #      pending and replays the verdict Agent #10 just computed
            #      pre-reveal, so tonight's research decides, not a stale
            #      verdict. (The no-overwrite rule below still protects
            #      SAME-campaign re-discovery, where the sweep's judgment
            #      must stand.)
            _sig = dict(existing.signals or {})
            if _sig.pop("intent", None) is not None:
                existing.signals = _sig
        if product_id is not None and existing.product_id != product_id:
            existing.product_id = product_id
        existing.icp_score = max(existing.icp_score or 0, icp_score or 0)
        if signals:
            merged = dict(existing.signals or {})
            incoming = dict(signals)
            # Discovery must NEVER set/overwrite the intent block on an
            # EXISTING lead row of the SAME campaign — that's owned by the
            # sweep / approve flow. Re-stamping 'pending' here would re-open
            # a lead the sweep already judged (or re-gate an already-enrolled
            # legacy lead that has no intent block). Only brand-new rows (the
            # insert branch below) and campaign MOVES (cleared above) start
            # fresh.
            incoming.pop("intent", None)
            merged.update(incoming)
            existing.signals = merged
        db.commit()
        db.refresh(existing)
        return existing

    row = NexusLead(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        global_lead_id=global_lead.id,
        icp_score=icp_score,
        signals=signals or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _primary_sequence_for_campaign(
    db: Session, *, workspace_id: int, campaign_id: Optional[int]
) -> Optional[NexusSequence]:
    """Find the campaign's primary sequence; fall back to any workspace sequence."""
    if campaign_id is not None:
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
    return (
        db.query(NexusSequence)
        .filter(
            NexusSequence.workspace_id == workspace_id,
            NexusSequence.campaign_id.is_(None),
        )
        .order_by(NexusSequence.id.asc())
        .first()
    )


def _enroll_in_sequence(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int],
    global_lead_id: int,
) -> Optional[NexusLeadSequence]:
    """Idempotent: enroll a global lead in the campaign's sequence.

    Returns the LeadSequence row (existing or new) or None if no sequence
    is configured for the workspace. The sequencer tick (services/sequencer.py)
    will pick up any active row whose next_action_at <= now.

    Side-effect: flips the global lead's status from 'new' → 'queued'
    so the GTM Journey badge reflects that an email is pending. The
    sequencer flips 'queued' → 'contacted' after the first successful
    send. Leads already past 'queued' (contacted / replied / etc.) are
    left alone.
    """
    seq = _primary_sequence_for_campaign(
        db, workspace_id=workspace_id, campaign_id=campaign_id
    )
    if seq is None:
        return None
    existing = (
        db.query(NexusLeadSequence)
        .filter(
            NexusLeadSequence.workspace_id == workspace_id,
            NexusLeadSequence.sequence_id == seq.id,
            NexusLeadSequence.lead_id == global_lead_id,
        )
        .first()
    )
    if existing is not None:
        return existing
    _start_at = datetime.utcnow()
    row = NexusLeadSequence(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        sequence_id=seq.id,
        lead_id=global_lead_id,
        current_step=0,
        status="active",
        next_action_at=_start_at,
        # Fixed cadence decided once, at enrollment (email_flow_plan_3.md).
        step_schedule=compute_step_schedule(_start_at, seq.steps or []),
    )
    db.add(row)
    # Promote the global lead's status badge from 'new' → 'queued'.
    # Best-effort + raw SQL so a column-missing edge case can't blow up
    # the enrollment write. Idempotent: a re-enroll on a lead already
    # contacted/replied/etc. leaves status untouched.
    try:
        db.execute(
            text(
                """
                UPDATE nexus_global_leads
                   SET status = 'queued'
                 WHERE id = :gid
                   AND (status IS NULL OR status = '' OR status = 'new')
                """
            ),
            {"gid": global_lead_id},
        )
        # Per-workspace status mirror (authoritative for display).
        db.execute(
            text(
                """
                UPDATE nexus_leads
                   SET status = 'queued', updated_at = NOW()
                 WHERE global_lead_id = :gid AND workspace_id = :ws
                   AND (status IS NULL OR status = '' OR status = 'new')
                """
            ),
            {"gid": global_lead_id, "ws": workspace_id},
        )
    except Exception:
        logger.warning(
            "enrollment: status badge bump (gl=%s) failed — non-fatal",
            global_lead_id,
        )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent enroll won the uq_lead_seq race (workspace_id, lead_id,
        # campaign_id) — no duplicate is created; return the existing row.
        db.rollback()
        return (
            db.query(NexusLeadSequence)
            .filter(
                NexusLeadSequence.workspace_id == workspace_id,
                NexusLeadSequence.sequence_id == seq.id,
                NexusLeadSequence.lead_id == global_lead_id,
            )
            .first()
        )
    db.refresh(row)

    # The sequencer tick authors the lead's email rows (subject + body via
    # Gemini) on its next pass (~1 min cadence) and renders/sends them via
    # the own (Resend) scheduler — no preview placeholder is written here.

    return row


def _persist_intent_signals(
    db: Session, lead_id: int, signals: Iterable[Dict[str, Any]]
) -> None:
    for s in signals:
        db.add(
            NexusIntentSignal(
                lead_id=lead_id,
                signal_type=s.get("signal_type", "reddit_pain"),
                payload=s,
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _event(payload: Dict[str, Any]) -> str:
    """Format a single SSE event chunk."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def autonomous_discover(
    db: Session,
    *,
    workspace_id: int,
    icp: Dict[str, Any],
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
    target_domains: Optional[List[str]] = None,
    sources: Optional[Iterable[str]] = None,
    max_leads: int = 50,
    min_icp_score: int = 40,
    # Apollo page-progression knobs (optional). When the caller supplies
    # `apollo_start_page` and `apollo_stats`, the Apollo adapter walks
    # that page window and writes pages_walked back into the stats dict
    # so the caller can persist the new cursor. `apollo_min_leads`
    # raises the broaden-on-empty trigger threshold so the auto-widen
    # retry fires on low yield, not just zero yield. Defaults preserve
    # existing behaviour for callers that don't opt in.
    apollo_start_page: int = 1,
    apollo_stats: Optional[Dict[str, Any]] = None,
    apollo_min_leads: int = 1,
    apollo_broader_start_page: int = 1,
    # Intent gate: when True, newly attached leads are stamped
    # signals.intent.status='pending' and are NOT enrolled here — the
    # intent_sweep tick scores them with Agent #10 and enrolls only the
    # accepted ones. Leaves the SSE 'lead' events + counts unchanged so
    # the wizard still shows what was found. Default False = legacy
    # behaviour (enroll inline).
    defer_enrollment: bool = False,
    # Option B (2026-06-09): async hook handed to the Apollo adapter. Scores
    # each candidate's company BEFORE the email reveal and returns only the
    # qualified ones, so reveal credits are spent on qualified leads only.
    # Apollo-only (other sources don't reveal paid emails). None = legacy.
    apollo_pre_reveal_filter: Optional[Any] = None,
    # The USER's requested lead count (max_leads is the over-fetch budget).
    # Forwarded to the Apollo adapter so the known-people top-up never shows
    # more "Already in campaign" rows than the user asked for.
    user_target: Optional[int] = None,
) -> AsyncIterator[str]:
    """Yield Server-Sent-Event chunks as the discovery run progresses."""
    # Discovery is APOLLO-ONLY — build the single Apollo task directly. The old
    # per-source if/elif dispatch (github/reddit/website/hunter/linkedin) is
    # removed: only Apollo respects the ICP filters. `sources` is still accepted
    # for API compatibility but is ignored.
    yield _event(
        {"type": "started", "message": "Discovery started (Apollo)", "count": 0}
    )

    per_source_cap = max(5, max_leads)
    total_attached = 0

    tasks: Dict[str, asyncio.Task] = {
        "apollo": asyncio.create_task(
            _run_apollo(
                db,
                icp,
                per_source_cap,
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                product_id=product_id,
                start_page=apollo_start_page,
                stats=apollo_stats,
                min_leads=apollo_min_leads,
                broader_start_page=apollo_broader_start_page,
                pre_reveal_filter=apollo_pre_reveal_filter,
                user_target=user_target,
            )
        )
    }

    for src, task in tasks.items():
        yield _event({"type": "source_started", "source": src})
        try:
            leads, intent_signals = await task
        except Exception as exc:  # noqa: BLE001
            yield _event({"type": "error", "source": src, "message": str(exc)})
            continue

        yield _event(
            {
                "type": "source_progress",
                "source": src,
                "count": len(leads),
                "message": f"{src} returned {len(leads)} raw candidates",
            }
        )

        # ── PARALLEL ATTACH (2026-06-11) ─────────────────────────────────
        # The per-lead pipeline (upsert → dedup check → attach [+ legacy
        # enroll]) is ~7 sequential DB round-trips; against a remote RDS
        # that's ~2-3s PER LEAD and it ran one-by-one (15 leads ≈ 43s of
        # pure latency after the emails were already paid for). Leads are
        # independent rows, so each candidate now runs in a worker thread
        # with its OWN session — same per-lead commits (durability
        # unchanged), same dedup/stamp/enroll semantics, just concurrent.
        #
        # Cap safety: candidates run in CHUNKS no larger than the remaining
        # `max_leads` budget, so the run can never attach past the cap (a
        # chunk's dedup-skips just roll the shortfall into the next chunk —
        # exactly like the serial loop). Concurrency is bounded well under
        # the engine pool (pool_size=10 + max_overflow=20) even with up to
        # 3 revenue windows attaching at once.
        def _process_candidate(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """Runs in a worker thread on its own session. Returns the SSE
            'lead' payload for an attached lead, or None when skipped
            (no email / below score floor / already pitched on product)."""
            from core.database import SessionLocal
            sess = SessionLocal()
            try:
                gl = _upsert_global_lead(sess, payload)
                if not gl:
                    return None

                # Per-lead homepage + Google-News enrichment stays REMOVED
                # (2026-06-09) — Agent #10's grounded research supersedes it.
                lead_dict: Dict[str, Any] = {
                    "role": gl.role,
                    "title": payload.get("role"),
                    "industry": payload.get("industry"),
                    "company_size": payload.get("company_size"),
                }
                icp_score = score_icp_fit(lead_dict, icp)

                # Optional email verification — only for sources without
                # confidence. verify_email is async; this thread has no
                # running loop, so a private asyncio.run is safe here.
                if not gl.email_verified and payload.get("source") in {"website", "github"}:
                    verified, vscore = asyncio.run(verify_email(gl.email))
                    gl.email_verified = verified
                    gl.email_verify_score = vscore
                    sess.commit()

                if icp_score < min_icp_score:
                    return None

                # Product-scope dedup. The lead does NOT count toward "N leads
                # found" and is never enrolled — but with the gate on we now
                # SURFACE it (2026-06-11): Agent #10 approved this person, so
                # silently vanishing them made runs look broken ("11 qualified
                # -> 0 delivered", campaign 67). A marker row with intent
                # status 'rejected' + drop_reason 'duplicate' renders in the
                # New-Run table with an "Already in campaign" badge while
                # remaining invisible to outreach, banking and the journey.
                if _already_attached_for_product(
                    sess,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    global_lead_id=gl.id,
                ):
                    if defer_enrollment:
                        _insert_duplicate_marker(
                            sess,
                            workspace_id=workspace_id,
                            campaign_id=campaign_id,
                            product_id=product_id,
                            global_lead=gl,
                            icp_score=icp_score,
                            source=payload.get("source"),
                            # Agent #10's verdict for THIS run's re-vet — the
                            # marker's MATCH pill shows the real score, not
                            # the firmographic 100.
                            agent_score=payload.get("_agent10_score"),
                        )
                    return None

                attach_signals: Dict[str, Any] = {
                    "source": payload.get("source"),
                    "raw": payload.get("raw") or {},
                }
                if defer_enrollment:
                    # Stamp pending so the intent_sweep picks it up. Only set
                    # when no verdict exists yet — never clobber a scored lead.
                    attach_signals["intent"] = {"status": "pending", "attempts": 0}
                _attach_workspace_lead(
                    sess,
                    workspace_id=workspace_id,
                    campaign_id=campaign_id,
                    product_id=product_id,
                    global_lead=gl,
                    icp_score=icp_score,
                    signals=attach_signals,
                )
                # Legacy gate-off path: enroll inline, as before.
                if not defer_enrollment:
                    try:
                        _enroll_in_sequence(
                            sess,
                            workspace_id=workspace_id,
                            campaign_id=campaign_id,
                            global_lead_id=gl.id,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("autonomous_discover: enrollment failed (non-fatal)")
                # Extract plain values BEFORE the session closes — the ORM
                # object expires with it.
                return {
                    "icp_score": icp_score,
                    "lead": {
                        "id": gl.id,
                        "email": gl.email,
                        "first_name": gl.first_name,
                        "last_name": gl.last_name,
                        "role": gl.role,
                        "company_domain": gl.company_domain,
                        "company_name": gl.company_name,
                        "source": gl.source,
                        "status": gl.status,
                        "email_verified": gl.email_verified,
                        "email_verify_score": gl.email_verify_score,
                        "created_at": gl.created_at,
                    },
                }
            finally:
                sess.close()

        idx = 0
        while idx < len(leads):
            remaining = max_leads - total_attached
            if remaining <= 0:
                break
            chunk = leads[idx: idx + min(_ATTACH_CONCURRENCY, remaining)]
            idx += len(chunk)
            results = await asyncio.gather(
                *(asyncio.to_thread(_process_candidate, p) for p in chunk),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    # One bad candidate must not sink the batch — its own
                    # session rolled back independently; the rest committed.
                    logger.exception(
                        "autonomous_discover: candidate attach failed "
                        "(non-fatal, continuing)", exc_info=r,
                    )
                    continue
                if r is None:
                    continue
                total_attached += 1
                yield _event({"type": "lead", "source": src, **r})
            if total_attached >= max_leads:
                yield _event({"type": "done", "count": total_attached})
                return

        # Intent signals: persisted as orphan signals attached to a synthetic
        # bookkeeping lead row if the source produced no email-bearing leads.
        if intent_signals:
            # Best-effort: skip persistence when we have no anchor lead.
            yield _event(
                {
                    "type": "source_progress",
                    "source": src,
                    "count": len(intent_signals),
                    "message": f"{src} produced {len(intent_signals)} intent signals",
                }
            )

        yield _event({"type": "source_done", "source": src, "count": len(leads)})

    yield _event({"type": "done", "count": total_attached})


async def accurate_discover(
    db: Session,
    *,
    workspace_id: int,
    source: str,
    icp: Dict[str, Any],
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
    target_domain: Optional[str] = None,
    max_leads: int = 25,
    min_icp_score: int = 60,
) -> List[Dict[str, Any]]:
    """Synchronous single-source discovery — used by ``/nexus/leads/accurate``."""
    if source == "apollo":
        leads, _ = await _run_apollo(
            db,
            icp,
            max_leads,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
        )
    elif source == "hunter":
        leads, _ = await _run_hunter(
            db, icp, max_leads, [target_domain] if target_domain else None
        )
    elif source == "github":
        leads, _ = await _run_github(db, icp, max_leads)
    elif source == "reddit":
        _, leads = await _run_reddit(db, icp, max_leads)
    elif source == "linkedin":
        leads, _ = await _run_linkedin(db, icp, max_leads)
    elif source == "website":
        leads, _ = await _run_website(
            db, icp, max_leads, [target_domain] if target_domain else None
        )
    else:
        leads = []

    # Intent gate: when ON, DEFER enrollment to the intent_sweep (which enrolls
    # only Agent #10-accepted leads) instead of enrolling inline — mirrors
    # autonomous_discover. /nexus/leads/accurate has no UI caller today, but
    # this guarantees BY CONSTRUCTION that no discovery path can enroll a
    # non-accepted lead, even via a direct API call.
    from nexus.services.intent_agent import gate_enabled
    gate_on = gate_enabled()

    out: List[Dict[str, Any]] = []
    for payload in leads:
        gl = _upsert_global_lead(db, payload)
        if not gl:
            continue
        # Per-lead web enrichment removed (see autonomous_discover above) —
        # unused data + Agent #10 does its own research. No web fetch here.
        lead_dict: Dict[str, Any] = {
            "role": gl.role,
        }
        icp_score = score_icp_fit(lead_dict, icp)
        if icp_score < min_icp_score:
            continue

        # Same product-scope dedup as autonomous_discover. Keeps the
        # two code paths' output sets aligned — operators using the
        # single-source "accurate" endpoint shouldn't see re-pitched
        # leads either.
        if _already_attached_for_product(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            global_lead_id=gl.id,
        ):
            continue

        attach_signals: Dict[str, Any] = {
            "source": payload.get("source"),
            "raw": payload.get("raw") or {},
        }
        if gate_on:
            # Stamp pending so the intent_sweep scores it and enrolls ONLY if
            # Agent #10 accepts. Never clobbers an existing verdict
            # (_attach_workspace_lead drops incoming intent on existing rows).
            attach_signals["intent"] = {"status": "pending", "attempts": 0}
        attached = _attach_workspace_lead(
            db,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
            global_lead=gl,
            icp_score=icp_score,
            signals=attach_signals,
        )
        # Enroll inline ONLY when the gate is off (legacy). When the gate is on,
        # enrollment is deferred to the intent_sweep — accepted leads only.
        if not gate_on:
            try:
                _enroll_in_sequence(
                    db,
                    workspace_id=workspace_id,
                    campaign_id=campaign_id,
                    global_lead_id=gl.id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("accurate_discover: enrollment failed (non-fatal)")
        out.append(
            {
                "lead_id": attached.id,
                "global_lead_id": gl.id,
                "email": gl.email,
                "icp_score": icp_score,
            }
        )

    # When gated, flag the campaign so the intent_sweep claims it and scores the
    # pending leads (enrolling ONLY the accepted ones). Without this the deferred
    # leads would sit pending-but-unscored. Best-effort.
    if gate_on and campaign_id and out:
        try:
            from sqlalchemy import text as _sql_text
            db.execute(
                _sql_text(
                    "UPDATE nexus_campaigns "
                    "SET icp = COALESCE(icp, '{}'::jsonb) "
                    "         || jsonb_build_object('intent_pending', true) "
                    "WHERE id = :cid"
                ),
                {"cid": campaign_id},
            )
            db.commit()
        except Exception:  # noqa: BLE001
            logger.warning(
                "accurate_discover: failed to set intent_pending for campaign %s",
                campaign_id,
            )
            db.rollback()
    return out


__all__ = [
    "DEFAULT_SOURCES",
    "autonomous_discover",
    "accurate_discover",
]
