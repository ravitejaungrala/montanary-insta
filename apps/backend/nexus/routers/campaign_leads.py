"""GET /nexus/campaigns/{id}/leads — table-of-leads endpoint.

Powers the LeadsTable component shown on the New Run "launched" screen
and on the GTM Journey page. Returns one row per lead in the campaign,
joined across `nexus_leads` (for icp_score), `nexus_global_leads` (for
PII), and `nexus_lead_sequences` (for status + activity).

Designed to be CHEAP (~ <200 ms even for 100+ leads) so the frontend
can poll it every 2 s while discovery is in flight without spiking the
backend.

The endpoint also returns a `status` field ("discovering" | "done")
which the frontend uses to decide whether to keep polling or stop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import require_current_workspace
from nexus.deps import block_user_writes

logger = logging.getLogger("pipelyt.nexus.campaign_leads")

# Router-level: read-only Users blocked from writes (start-outreach, approve,
# score-pending); reads stay open.
router = APIRouter(
    prefix="/nexus/campaigns",
    tags=["Nexus — Campaign Leads"],
    dependencies=[Depends(block_user_writes)],
)


# Window we treat as "still discovering" — if the campaign was created
# within this window AND has fewer leads than max_leads, assume the
# background discovery loop is still running. After this window we
# flip to "done" so the frontend stops polling.
_DISCOVERING_WINDOW = timedelta(minutes=15)


# ─────────────────────────────────────────────────────────────────────────────
# Single SQL — JOINs the three lead tables in one round-trip.
#
# Columns mapped to the UI:
#   #              → row index (client-side)
#   Fit Score      → nexus_leads.icp_score
#   First Name     → nexus_global_leads.first_name
#   Last Name      → nexus_global_leads.last_name
#   Title          → nexus_global_leads.role
#   Company Name   → nexus_global_leads.company_name
#   Company URL    → nexus_global_leads.company_domain
#   LinkedIn       → nexus_global_leads.linkedin_url
#   Location       → built from person_city / state / country
#   Industry       → nexus_global_leads.organization_industry
#
# Each row also includes `lead_sequence_id` so the frontend can pass it
# to the right-panel (Content / Flow / Analytics) renderer your other
# developer is building.
# ─────────────────────────────────────────────────────────────────────────────
_LEADS_SQL = text(
    """
    SELECT
        gl.id                          AS global_lead_id,
        l.id                           AS lead_id,
        ls.id                          AS lead_sequence_id,
        COALESCE(l.icp_score, 0)       AS fit_score,
        gl.first_name                  AS first_name,
        gl.last_name                   AS last_name,
        gl.role                        AS title,
        gl.company_name                AS company_name,
        gl.company_domain              AS company_url,
        gl.linkedin_url                AS linkedin_url,
        gl.email                       AS email,
        gl.person_city                 AS person_city,
        gl.person_state                AS person_state,
        gl.person_country              AS person_country,
        gl.organization_industry       AS organization_industry,
        -- Per-workspace value wins; fall back to the shared global row for
        -- legacy rows not yet backfilled (workspace-isolation fix).
        COALESCE(l.phone, gl.phone)    AS phone,
        COALESCE(l.status, gl.status)  AS lead_status,
        l.signals                      AS signals,
        l.created_at                   AS lead_created_at,
        ls.status                      AS sequence_status,
        ls.current_step                AS current_step
    FROM nexus_leads l
    JOIN nexus_global_leads gl ON gl.id = l.global_lead_id
    LEFT JOIN nexus_lead_sequences ls
        ON ls.lead_id = l.global_lead_id
       AND ls.workspace_id = l.workspace_id
       AND ls.campaign_id  = l.campaign_id
    WHERE l.campaign_id = :campaign_id
      AND l.workspace_id = :workspace_id
    ORDER BY l.icp_score DESC NULLS LAST, l.id DESC
    LIMIT :limit
    OFFSET :offset
    """
)


# Safety cap on how many campaign leads we pull before in-Python ICP filtering
# + pagination. Real campaigns are well under this; keeps the strict-filter
# pass O(n) and bounded.
_FETCH_CAP = 2000


# Intent-stage buckets for the discovery funnel. Maps the raw
# signals.intent.status onto the four UI columns.
_ACCEPTED_STATUSES = {"accepted", "approved", "included_failopen"}


def _intent_stage(intent: Optional[Dict[str, Any]]) -> str:
    """Bucket a lead into the funnel: 'scoring' | 'accepted' | 'rejected'.

    A lead with no intent block at all (gate off, or a legacy lead enrolled
    before the gate existed) is treated as 'accepted' — it's already in the
    outreach pipeline, so the funnel shouldn't hide it.
    """
    if not isinstance(intent, dict) or not intent:
        return "accepted"
    status = (intent.get("status") or "").strip()
    if status == "pending":
        return "scoring"
    if status == "rejected":
        return "rejected"
    if status in _ACCEPTED_STATUSES:
        return "accepted"
    return "accepted"


def _intent_for_row(r: Any) -> Dict[str, Any]:
    sig = getattr(r, "signals", None)
    intent = sig.get("intent") if isinstance(sig, dict) else None
    intent = intent if isinstance(intent, dict) else {}
    stage = _intent_stage(intent)
    return {
        "stage":   stage,
        "status":  intent.get("status") or ("accepted" if not intent else stage),
        "score":   int(intent.get("score") or 0),
        "reason":  intent.get("reason") or "",
        "signals": intent.get("signals") or [],
        # 2026-06-11 — surfaced so the New-Run table can render duplicate
        # markers ("Already in <campaign>") which are stored as rejected.
        "drop_reason": intent.get("drop_reason") or "",
        "dup_campaign": intent.get("dup_campaign_name") or "",
        "dup_date": intent.get("dup_contacted_at") or "",
    }


def _row_to_lead(r: Any) -> Dict[str, Any]:
    intent = _intent_for_row(r)
    return {
        "global_lead_id":         r.global_lead_id,
        "lead_id":                r.lead_id,
        "lead_sequence_id":       r.lead_sequence_id,
        "fit_score":              int(r.fit_score or 0),
        "first_name":             r.first_name or "",
        "last_name":              r.last_name or "",
        "title":                  r.title or "",
        "company_name":           r.company_name or "",
        "company_url":            r.company_url or "",
        "linkedin_url":           r.linkedin_url or "",
        "email":                  r.email or "",
        # 2026-06-02: phone returned by Apollo. "" when Apollo didn't
        # return one — frontend renders an em-dash placeholder.
        "phone":                  getattr(r, "phone", None) or "",
        "location":               _join_location(
            r.person_city, r.person_state, r.person_country
        ),
        "organization_industry":  r.organization_industry or "",
        "lead_status":            r.lead_status or "new",
        "sequence_status":        r.sequence_status or "",
        "current_step":           r.current_step or 0,
        # Intent-gate verdict (Agent #10). `intent_stage` drives the funnel
        # tabs; `intent` carries the reason + supporting signals for the UI.
        "intent_stage":           intent["stage"],
        "intent":                 intent,
    }


def latest_launch_at(db: Session, *, campaign_id: int) -> Optional[datetime]:
    """The timestamp of this campaign's most recent /analyze run.

    Used to fence the New-Run leads view to ONLY the leads found in the latest
    run (see filtered_lead_rows' `since` arg). Returns None when the campaign
    has no recorded launch (legacy campaign created before the launches table),
    in which case callers fall back to showing all leads.
    """
    return db.execute(
        text("SELECT MAX(launched_at) FROM nexus_campaign_launches WHERE campaign_id = :c"),
        {"c": campaign_id},
    ).scalar()


def filtered_lead_rows(
    db: Session, *, campaign_id: int, workspace_id: int,
    icp: Optional[Dict[str, Any]] = None,
    since: Optional[datetime] = None,
) -> List[Any]:
    """Fetch the campaign's lead rows, STRICTLY filtered to those matching the
    CURRENT campaign ICP (title + location).

    WHY (2026-06-01): /analyze reuses a campaign across runs and never clears
    prior leads, and Apollo's title filter is fuzzy — so the raw table is the
    union of every targeting iteration plus near-miss titles ("Deputy CEO" for
    "CEO"). Gating the displayed rows on the current ICP means the user only
    ever sees leads that actually match what they entered. Passing icp=None (or
    an ICP with no titles/locations) returns everything, unchanged.

    `since` (2026-06-04): when set, additionally drop leads created BEFORE this
    timestamp — used by the New-Run screen to show only the leads found in the
    latest run (fenced via nexus_campaign_launches.launched_at), not the whole
    campaign's accumulated history. None = no fence (GTM Journey / history view).
    """
    rows = db.execute(
        _LEADS_SQL,
        {
            "campaign_id": campaign_id,
            "workspace_id": workspace_id,
            "limit": _FETCH_CAP,
            "offset": 0,
        },
    ).fetchall()
    if since is not None:
        rows = [
            r for r in rows
            if getattr(r, "lead_created_at", None) is not None
            and r.lead_created_at >= since
        ]
    if not isinstance(icp, dict) or not icp:
        return list(rows)
    # APOLLO PARITY (2026-06-09): display-time re-filter is OFF by default.
    # Leads were already matched by Apollo at search time (and the discovery
    # gate is off too — see discovery_apollo._POST_APOLLO_GATE), so re-filtering
    # here would HIDE attached leads and break "what we found = what we show".
    # Re-enable with NEXUS_POST_APOLLO_GATE=true (same switch as discovery).
    import os as _os
    if _os.getenv("NEXUS_POST_APOLLO_GATE", "false").strip().lower() != "true":
        return list(rows)
    # Only filter if at least one strict dimension is set.
    from nexus.services.icp_match import lead_matches_icp, _values
    if not (
        _values(icp.get("person_titles"))
        or _values(icp.get("person_locations"))
        or _values(icp.get("organization_industries"))
    ):
        return list(rows)
    return [
        r for r in rows
        if lead_matches_icp(
            title=r.title or "",
            city=r.person_city, state=r.person_state, country=r.person_country,
            icp=icp,
            # 2026-06-02: also pass industry so stale prior-run leads from a
            # different industry don't pollute the current campaign's view.
            # Department isn't stored at display time (yet); skip it here.
            industry=r.organization_industry,
        )
    ]


def load_leads_for_campaign(
    db: Session, *, campaign_id: int, workspace_id: int,
    limit: int = 100, offset: int = 0,
    icp: Optional[Dict[str, Any]] = None,
    since: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Shared helper used by both GET /leads and POST /analyze.

    Returns the same row shape both consumers expect — so the /analyze
    response can hand the wizard a ready-to-render lead list without
    needing a separate poll. When `icp` is provided, only leads matching the
    current targeting are returned (see filtered_lead_rows). When `since` is
    provided, only leads from the latest run are returned (run-fenced).
    """
    rows = filtered_lead_rows(
        db, campaign_id=campaign_id, workspace_id=workspace_id, icp=icp,
        since=since,
    )
    return [_row_to_lead(r) for r in rows[offset:offset + limit]]


def _join_location(city: Optional[str], state: Optional[str], country: Optional[str]) -> str:
    """Build a human-readable location string from city/state/country.

    Examples:
      ('Bengaluru', 'Karnataka', 'India')  → 'Bengaluru, India'
      ('San Francisco', 'CA', 'United States') → 'San Francisco, CA, United States'
      ('', '', 'India')                     → 'India'
      (None, None, None)                    → ''

    We drop state when country == India / United Kingdom etc. where the
    state is rarely useful (city + country is what matters). For US we
    keep state because "San Francisco, CA" is the common idiom.
    """
    parts: List[str] = []
    if city:
        parts.append(city.strip())
    # Keep state only for US (matches operator intuition).
    if state and (country or "").strip().lower() in ("united states", "us", "usa"):
        parts.append(state.strip())
    if country:
        parts.append(country.strip())
    return ", ".join(p for p in parts if p)


@router.get("/{campaign_id}/leads")
def get_campaign_leads(
    campaign_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    # 2026-06-04: when true, fence the result (rows + funnel + total) to ONLY
    # the leads found in this campaign's latest run. The New-Run screen sets
    # this so a fresh run never shows the campaign's accumulated history; the
    # GTM Journey / history view leaves it false and sees every lead.
    latest_run: bool = Query(False),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Return the leads table snapshot for a campaign.

    Cheap to call: one JOIN query + a small status calculation. Used by
    the frontend's polling loop (every 2 s while discovery is running).

    Returns:
      {
        "campaign_id": <int>,
        "status": "discovering" | "done",
        "total": <int>,         # total leads on the campaign (all rows)
        "leads": [
          { fit_score, first_name, last_name, title, company_name,
            company_url, linkedin_url, location, organization_industry,
            email, lead_sequence_id, sequence_status, current_step }, …
        ]
      }
    """
    # Verify the campaign belongs to the active workspace. Cheap query
    # — also gives us the campaign.created_at for the status calc.
    campaign_row = db.execute(
        text(
            "SELECT id, workspace_id, created_at, icp "
            "FROM nexus_campaigns "
            "WHERE id = :cid AND workspace_id = :wid"
        ),
        {"cid": campaign_id, "wid": workspace.id},
    ).first()
    if campaign_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found in this workspace",
        )

    # Strict ICP filter (title + location) so the user only sees leads that
    # match the CURRENT targeting — not stale prior-run leads or Apollo's
    # fuzzy near-miss titles. `total` reflects the FILTERED count.
    icp = campaign_row.icp if isinstance(campaign_row.icp, dict) else None
    since = latest_launch_at(db, campaign_id=campaign_id) if latest_run else None
    # 2026-06-04: on the new-run view, DON'T re-apply the strict ICP text filter.
    # This run's leads were already (1) ICP-filtered at Apollo discovery and
    # (2) vetted/accepted by Agent #10 — re-filtering by exact industry/title
    # text here wrongly HID accepted leads (e.g. "media production" not matching
    # "Advertising & Marketing"), so "5 found" showed as 3. The run-fence (since)
    # already scopes to this run. The history/GTM view (latest_run=false) keeps
    # the filter to drop stale prior-run / near-miss rows.
    display_icp = None if latest_run else icp
    rows = filtered_lead_rows(
        db, campaign_id=campaign_id, workspace_id=workspace.id, icp=display_icp,
        since=since,
    )
    total = len(rows)
    leads = [_row_to_lead(r) for r in rows[offset:offset + limit]]

    # ── Funnel summary (Agent #10 intent gate) ────────────────────────
    # Counts span ALL filtered rows (not just this page) so the tabs are
    # accurate while the table is paginated. found = everything Apollo
    # surfaced for this targeting; scoring = awaiting the agent; accepted
    # = enrolled in outreach; rejected = held back (with a reason).
    funnel = {"found": int(total), "scoring": 0, "accepted": 0, "rejected": 0}
    for r in rows:
        funnel[_intent_stage(
            (r.signals.get("intent") if isinstance(getattr(r, "signals", None), dict) else None)
        )] += 1

    # Discovery status — used by the polling effect to know when to
    # stop refreshing. Priority order:
    #   1. icp.discovery_pending == true → still discovering (sweep will
    #      pick it up)
    #   2. total >= 20 → hit the per-run target, done
    #   3. campaign created < 15 min ago AND total == 0 → still recent,
    #      keep polling in case BG enrollment is mid-flight
    #   4. else → done (either we landed <20 but Apollo is exhausted,
    #      or we're past the recency window)
    created_at = campaign_row.created_at
    icp = campaign_row.icp or {}
    is_pending = bool(icp.get("discovery_pending")) if isinstance(icp, dict) else False
    # Keep polling while Agent #10 is still scoring leads — either the
    # campaign carries the intent_pending flag or some rows are still in
    # the 'scoring' bucket.
    is_scoring = (
        (bool(icp.get("intent_pending")) if isinstance(icp, dict) else False)
        or funnel["scoring"] > 0
    )
    is_recent = (
        created_at is not None
        and (datetime.utcnow() - created_at) < _DISCOVERING_WINDOW
    )
    if is_pending or is_scoring:
        discovery_status = "discovering"
    elif total >= 20:
        discovery_status = "done"
    elif is_recent and total == 0:
        # Campaign just created, no leads yet — Apollo is probably
        # still running. Keep polling.
        discovery_status = "discovering"
    else:
        # Apollo finished, landed <20 (e.g. tight ICP exhausted at 8
        # leads). No point making the UI poll for 15 min when there's
        # nothing more coming.
        discovery_status = "done"

    return {
        "campaign_id": campaign_id,
        "status": discovery_status,
        "total": int(total),
        "funnel": funnel,
        # Has the operator hit "Start Outreach" yet? The UI shows the button
        # when scoring is done (funnel.scoring == 0) and this is false.
        "outreach_approved": bool(icp.get("outreach_approved")) if isinstance(icp, dict) else False,
        # Email-draft generation (background) completion — drives the UI toast.
        "drafts_ready": bool(icp.get("drafts_ready")) if isinstance(icp, dict) else False,
        "drafts_count": int(icp.get("drafts_count") or 0) if isinstance(icp, dict) else 0,
        "leads": leads,
    }


def _campaign_launched(db: Session, campaign_id: int, workspace_id: int) -> bool:
    """True once the operator has hit Start-Outreach for this campaign."""
    val = db.execute(
        text(
            "SELECT (icp ->> 'outreach_approved') = 'true' "
            "FROM nexus_campaigns WHERE id = :c AND workspace_id = :w"
        ),
        {"c": campaign_id, "w": workspace_id},
    ).scalar()
    return bool(val)


@router.post("/{campaign_id}/start-outreach")
def start_outreach(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Operator gate — enroll the approved lead set and BEGIN outreach.

    This is the SINGLE point where email + LinkedIn outreach starts. It is
    only valid once Agent #10 has finished scoring (no pending leads and no
    intent_pending flag) — otherwise it 409s, so outreach can't begin before
    the user has the full accept/reject picture to review.

    Enrolls every lead in the campaign's 'accepted' bucket (accepted /
    approved / included_failopen, plus gate-off legacy leads), flips
    icp.outreach_approved=true (un-gates the LinkedIn pass), and kicks email
    generation. Idempotent — enrollment is idempotent and re-running just
    re-affirms the flag.
    """
    from nexus.models_phase3 import NexusCampaign, NexusLead
    from nexus.services.lead_discovery import _enroll_in_sequence

    camp = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if camp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found in this workspace",
        )

    icp = camp.icp if isinstance(camp.icp, dict) else {}

    # Guard: Agent #10 must be done. Block while the campaign is still flagged
    # intent_pending OR any lead is still in the 'pending' (scoring) state.
    pending = db.execute(
        text(
            "SELECT COUNT(*) FROM nexus_leads "
            "WHERE campaign_id = :c AND signals->'intent'->>'status' = 'pending'"
        ),
        {"c": campaign_id},
    ).scalar() or 0
    if icp.get("intent_pending") or int(pending) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Agent #10 is still scoring ({int(pending)} lead(s) pending). "
                "Wait for scoring to finish before starting outreach."
            ),
        )

    # Enroll the accepted set (accepted / approved / included_failopen).
    leads = (
        db.query(NexusLead)
        .filter(
            NexusLead.campaign_id == campaign_id,
            NexusLead.workspace_id == workspace.id,
        )
        .all()
    )
    enrolled = 0
    for lead in leads:
        intent = lead.signals.get("intent") if isinstance(lead.signals, dict) else None
        if _intent_stage(intent if isinstance(intent, dict) else None) != "accepted":
            continue
        try:
            _enroll_in_sequence(
                db,
                workspace_id=lead.workspace_id,
                campaign_id=lead.campaign_id,
                global_lead_id=lead.global_lead_id,
            )
            enrolled += 1
        except Exception:  # noqa: BLE001
            logger.exception("start_outreach: enroll failed for lead %s", lead.id)

    # Flip the launch flag (reassign JSONB — in-place mutation isn't tracked).
    merged_icp = dict(icp)
    merged_icp["outreach_approved"] = True
    merged_icp["outreach_approved_at"] = datetime.utcnow().isoformat()
    # Reset the email-draft completion flag so the UI toast fires once the
    # background pass below finishes (and re-fires on a re-launch).
    merged_icp["drafts_ready"] = False
    merged_icp.pop("drafts_count", None)
    camp.icp = merged_icp
    db.commit()

    # Generate the cadence drafts for the just-enrolled leads in ONE batched
    # pass, off the request thread (N Gemini calls must not block the HTTP
    # response). Drafts appear in the Content tab; the sequencer then sends
    # them on its next pass (~1 min), paced by mailbox availability.
    from nexus.services.sequencer import prime_drafts_for_campaign_bg
    background_tasks.add_task(prime_drafts_for_campaign_bg, campaign_id)

    return {"ok": True, "campaign_id": campaign_id, "enrolled": enrolled}


@router.post("/{campaign_id}/leads/{lead_id}/approve")
def approve_lead(
    campaign_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Operator override — ADD an agent-rejected (or still-pending) lead to
    the campaign's approved set despite the verdict.

    Stamps the intent verdict 'approved' and floors the fit score. Enrollment
    is deferred to the same gate as everything else: if the run is NOT yet
    launched, the lead simply joins the approved set and gets enrolled when the
    operator hits "Start Outreach". If the run is ALREADY launched, we enroll
    it immediately so the late addition starts its outreach. No extra Apollo
    cost — the email was revealed at discovery time.
    """
    from datetime import timezone

    from nexus.models_phase3 import NexusLead
    from nexus.services import intent_agent
    from nexus.services.lead_discovery import _enroll_in_sequence

    lead = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id,
            NexusLead.campaign_id == campaign_id,
            NexusLead.workspace_id == workspace.id,
        )
        .first()
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found in this campaign",
        )

    intent = dict(lead.signals.get("intent")) if isinstance(lead.signals, dict) and isinstance(lead.signals.get("intent"), dict) else {}
    intent["status"] = "approved"
    intent["reason"] = ""  # clear the prior "not picked" reason
    intent["approved_at"] = datetime.now(timezone.utc).isoformat()
    merged = dict(lead.signals or {})
    merged["intent"] = intent
    lead.signals = merged
    lead.icp_score = max(int(lead.icp_score or 0), intent_agent.INTENT_ACCEPT_FLOOR)
    db.commit()

    # Enroll NOW only if the run is already launched. Before launch we just
    # add to the approved set; Start-Outreach enrolls the whole set together.
    enrolled_now = False
    if _campaign_launched(db, campaign_id, workspace.id):
        try:
            _enroll_in_sequence(
                db,
                workspace_id=lead.workspace_id,
                campaign_id=lead.campaign_id,
                global_lead_id=lead.global_lead_id,
            )
            enrolled_now = True
        except Exception:  # noqa: BLE001
            logger.exception("approve_lead: enrollment failed for lead %s", lead_id)

    return {"ok": True, "lead_id": lead_id, "intent_stage": "accepted",
            "enrolled": enrolled_now}


@router.post("/{campaign_id}/score-pending")
async def score_pending_leads(
    campaign_id: int,
    max_companies: int = Query(2, ge=1, le=6),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Client-driven intent scoring — the frontend calls this repeatedly while
    leads are 'Checking signals', so scoring ADVANCES even when the
    BackgroundTask / scheduler-tick sweeps aren't available (e.g. serverless,
    where post-response work freezes with the container). Each call scores up
    to `max_companies` companies synchronously (so it runs to completion and
    emits logs within the request) and returns progress; the UI re-polls and
    calls again until `remaining_pending` hits 0.
    """
    owns = db.execute(
        text("SELECT 1 FROM nexus_campaigns WHERE id=:c AND workspace_id=:w"),
        {"c": campaign_id, "w": workspace.id},
    ).first()
    if not owns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found in this workspace",
        )

    from nexus.services.intent_sweep import process_pending_intent
    res = await process_pending_intent(
        db, only_campaign_id=campaign_id, max_companies=max_companies
    )
    remaining = db.execute(
        text(
            "SELECT COUNT(*) FROM nexus_leads "
            "WHERE campaign_id=:c AND signals->'intent'->>'status'='pending'"
        ),
        {"c": campaign_id},
    ).scalar()
    return {**res, "remaining_pending": int(remaining or 0)}
