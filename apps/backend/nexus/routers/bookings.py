"""Phase 6 bookings router.

Endpoints:
  GET  /nexus/ms-bookings/setup        auth-gated — show current MS Bookings config
  POST /nexus/ms-bookings/setup        auth-gated — save MS Bookings business ID
  POST /nexus/ms-bookings/subscribe    auth-gated — create Graph change-notification subscription
  POST /nexus/webhooks/calendly        PUBLIC     — Calendly HMAC-verified webhook
  POST /nexus/webhooks/ms-bookings     PUBLIC     — MS Graph notification (handles validationToken)
  GET  /nexus/webhooks/booking-confirm PUBLIC     — rep one-click confirm via token
  GET  /nexus/bookings                 auth-gated — list demo bookings for workspace
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_workspace_id,
    get_nexus_user,
    require_current_workspace,
)
from core.config import SCHEDULER_SECRET
from fastapi import Header
from nexus.models_phase6 import DemoBooking, DemoBriefing, Settings
from nexus.schemas_phase6 import DemoBookingOut, MSBookingsSetupIn
from nexus.services import calendly_client, gemini, ms_graph_client

logger = logging.getLogger("pipelyt.nexus.bookings")

router = APIRouter(tags=["nexus-bookings"])


# ─── Settings: MS Bookings business ID ────────────────────────────────────────


@router.get("/nexus/ms-bookings/setup")
def get_ms_bookings_setup(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace)
    row = db.query(Settings).filter(Settings.workspace_id == workspace_id).first()
    if not row:
        return {"ms_booking_business_id": "", "rep_name": "", "rep_email": "", "rep_phone": ""}
    settings_blob = row.settings or {}
    return {
        "ms_booking_business_id": settings_blob.get("ms_booking_business_id", ""),
        "rep_name": settings_blob.get("rep_name", ""),
        "rep_email": settings_blob.get("rep_email", ""),
        "rep_phone": settings_blob.get("rep_phone", ""),
        "ms_graph_subscription_id": settings_blob.get("ms_graph_subscription_id", ""),
        "ms_subscription_expires_at": settings_blob.get("ms_subscription_expires_at"),
    }


@router.post("/nexus/ms-bookings/setup")
def post_ms_bookings_setup(
    payload: MSBookingsSetupIn,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace)
    row = db.query(Settings).filter(Settings.workspace_id == workspace_id).first()
    if not row:
        row = Settings(workspace_id=workspace_id, settings={})
        db.add(row)

    blob = dict(row.settings or {})
    blob["ms_booking_business_id"] = payload.ms_booking_business_id
    if payload.rep_name:
        blob["rep_name"] = payload.rep_name
    if payload.rep_email:
        blob["rep_email"] = payload.rep_email
    if payload.rep_phone:
        blob["rep_phone"] = payload.rep_phone
    row.settings = blob
    db.commit()
    db.refresh(row)
    return {"ok": True, "ms_booking_business_id": payload.ms_booking_business_id}


@router.post("/nexus/ms-bookings/subscribe")
async def create_ms_subscription(
    notification_url: str = Query(..., description="HTTPS endpoint Graph will POST to"),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace)
    row = db.query(Settings).filter(Settings.workspace_id == workspace_id).first()
    if not row or not (row.settings or {}).get("ms_booking_business_id"):
        raise HTTPException(status_code=400, detail="MS Bookings business not configured")

    business_id = row.settings["ms_booking_business_id"]
    expires = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"

    try:
        sub = await ms_graph_client.create_change_subscription(
            business_id,
            notification_url,
            client_state="nexus-bookings",
            expiration_iso=expires,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Graph subscription failed: {e}") from e

    blob = dict(row.settings or {})
    blob["ms_graph_subscription_id"] = sub.get("id", "")
    blob["ms_subscription_expires_at"] = sub.get("expirationDateTime", expires)
    row.settings = blob
    db.commit()
    return {"ok": True, "subscription": sub}


# ─── Calendly webhook ─────────────────────────────────────────────────────────


@router.post("/nexus/webhooks/calendly")
async def calendly_webhook(request: Request, db: Session = Depends(get_db)):
    """Calendly v2 webhook. PUBLIC — HMAC verified via signing key."""
    raw = await request.body()
    sig_header = request.headers.get("calendly-webhook-signature") or request.headers.get(
        "x-calendly-signature"
    )
    verdict = calendly_client.verify_signature(raw, sig_header)
    if verdict is False:
        raise HTTPException(status_code=401, detail="signature_invalid")
    # verdict is None when signing key not configured — accept with warn.

    try:
        body = await request.json()
    except Exception:
        body = {}

    event_name = body.get("event")
    payload = body.get("payload") or {}

    invitee_email = (payload.get("email") or "").lower().strip()
    invitee_name = payload.get("name") or ""
    scheduled = payload.get("scheduled_event") or {}
    start_time_str = scheduled.get("start_time")
    end_time_str = scheduled.get("end_time")
    event_uri = scheduled.get("uri") or ""

    if event_name == "invitee.created" and invitee_email:
        # Resolve lead + workspace from nexus_global_leads (Phase 3 table)
        lead_id, workspace_id, campaign_id = _resolve_lead_and_workspace(db, invitee_email)
        if not workspace_id:
            workspace_id = _fallback_workspace_id(db)
            if not workspace_id:
                logger.warning(
                    "calendly_webhook: dropping booking for %s — no workspace could be resolved "
                    "(lead unmatched and no fallback workspace configured)",
                    invitee_email,
                )
                return {"received": True, "matched": False, "skipped": True}

        booking = DemoBooking(
            workspace_id=workspace_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            source="calendly",
            booking_id_external=event_uri,
            attendee_email=invitee_email,
            attendee_name=invitee_name,
            scheduled_at=_parse_iso(start_time_str),
            end_time=_parse_iso(end_time_str),
            status="scheduled",
            meta={"event_uri": event_uri, "raw_payload_keys": list(payload.keys())},
        )
        db.add(booking)

        # Propagate the booking across the lead-status surface (global lead
        # badge, sequence halt, outreach conversion). The shared helper
        # supersedes the old inline UPDATE that only touched lead_sequences
        # with status='meeting' (which wasn't recognised by analytics or
        # the GTM Journey badge ranking).
        if lead_id:
            try:
                from nexus.services.ms_bookings_sync import mark_lead_demo_scheduled

                mark_lead_demo_scheduled(
                    db,
                    lead_id=lead_id,
                    workspace_id=workspace_id,
                    campaign_id=campaign_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("calendly lead-status reflection skipped: %s", e)
                db.rollback()

        db.commit()

        # Generate + store the agenda, then email it to the prospect (Phase 2b).
        # Self-service bookings: the external tool (Calendly / MS Bookings) sent
        # the calendar invite, not us — so we can't embed the agenda in it; we
        # send it as a standalone follow-up instead. Gated on KB grounding +
        # idempotent; best-effort.
        try:
            from nexus.services import meeting_agenda_agent, demo_handling_agent

            meeting_agenda_agent.generate_for_booking(db, booking_id=booking.id)
            demo_handling_agent.send_agenda_email(db, booking_id=booking.id)
        except Exception as e:  # noqa: BLE001
            logger.warning("agenda generation/email skipped: %s", e)

        return {"received": True, "matched": bool(lead_id)}

    if event_name == "invitee.canceled" and invitee_email:
        lead_id, _, _ = _resolve_lead_and_workspace(db, invitee_email)
        if lead_id:
            try:
                next_at = datetime.utcnow() + timedelta(days=3)
                db.execute(
                    text(
                        """UPDATE nexus_lead_sequences
                           SET status='active', next_action_at = :next
                           WHERE lead_id = :lid AND status='meeting'
                        """
                    ),
                    {"next": next_at, "lid": lead_id},
                )
                db.commit()
            except Exception:
                db.rollback()
        return {"received": True}

    return {"received": True, "ignored": True}


# ─── MS Bookings webhook ──────────────────────────────────────────────────────


@router.post("/nexus/webhooks/ms-bookings")
async def ms_bookings_webhook(
    request: Request,
    validationToken: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """MS Graph change-notification webhook.

    PUBLIC — Microsoft sends a `validationToken` on subscription creation that
    must be echoed back as text/plain within 10 seconds. Otherwise the body
    contains a list of change notifications.
    """
    if validationToken:
        return PlainTextResponse(content=validationToken, status_code=200)

    try:
        body = await request.json()
    except Exception:
        body = {}

    notifications = body.get("value") or []
    expected_client_state = "nexus-bookings"

    for notif in notifications:
        if notif.get("clientState") and notif["clientState"] != expected_client_state:
            logger.warning("MS Bookings webhook clientState mismatch — skipping")
            continue

        change_type = notif.get("changeType")
        if change_type not in ("created", "updated"):
            continue

        resource = notif.get("resource", "")
        # Format: solutions/bookingBusinesses/{businessId}/appointments/{appointmentId}
        import re

        m = re.search(r"solutions/bookingBusinesses/([^/]+)/appointments/([^/]+)", resource, re.I)
        if not m:
            continue
        business_id = m.group(1)
        appt_id = (notif.get("resourceData") or {}).get("id") or m.group(2)

        try:
            appt = await ms_graph_client.get_appointment(business_id, appt_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Graph get_appointment failed: %s", e)
            continue
        if not appt:
            continue

        attendee_email = ""
        attendee_name = ""
        for c in appt.get("customers", []) or []:
            attendee_email = (c.get("emailAddress") or "").lower().strip()
            attendee_name = c.get("name") or ""
            break

        lead_id, workspace_id, campaign_id = _resolve_lead_and_workspace(db, attendee_email)
        if not workspace_id:
            workspace_id = _fallback_workspace_id(db)
            if not workspace_id:
                logger.warning(
                    "ms_bookings_webhook: dropping booking for %s — no workspace could be resolved "
                    "(lead unmatched and no fallback workspace configured)",
                    attendee_email,
                )
                continue

        confirm_token = secrets.token_urlsafe(32)

        booking = DemoBooking(
            workspace_id=workspace_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            source="ms_bookings",
            booking_id_external=appt_id,
            ms_booking_business_id=business_id,
            attendee_email=attendee_email,
            attendee_name=attendee_name,
            scheduled_at=_parse_iso(appt.get("startDateTime", {}).get("dateTime")),
            end_time=_parse_iso(appt.get("endDateTime", {}).get("dateTime")),
            status="pending_rep_confirm",
            confirm_token=confirm_token,
            confirm_token_expires_at=datetime.utcnow() + timedelta(days=7),
            meta={"appt_keys": list(appt.keys())[:20]},
        )
        db.add(booking)

        # Mirror the polling-sync path: reflect the booking across the lead-
        # status surface so the webhook (real-time) and the poller (5-min
        # fallback) leave the database in identical shape.
        if lead_id:
            try:
                from nexus.services.ms_bookings_sync import mark_lead_demo_scheduled

                mark_lead_demo_scheduled(
                    db,
                    lead_id=lead_id,
                    workspace_id=workspace_id,
                    campaign_id=campaign_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("ms_bookings lead-status reflection skipped: %s", e)
                db.rollback()

    db.commit()

    # Phase 1: store-only prospect-facing meeting agenda (invite untouched).
    try:
        from nexus.services import meeting_agenda_agent

        meeting_agenda_agent.generate_for_booking(db, booking_id=booking.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("agenda generation skipped: %s", e)

    return Response(status_code=202)


# ─── Booking confirm (rep one-click) ──────────────────────────────────────────


@router.get("/nexus/webhooks/booking-confirm")
def booking_confirm(token: str = Query(...), db: Session = Depends(get_db)):
    """PUBLIC — rep clicks the link from their notification email."""
    booking = db.query(DemoBooking).filter(DemoBooking.confirm_token == token).first()
    if not booking:
        return HTMLResponse(
            _error_html("Link expired or invalid. It may have already been used."),
            status_code=400,
        )

    if booking.confirm_token_expires_at and booking.confirm_token_expires_at < datetime.utcnow():
        return HTMLResponse(
            _error_html("This confirmation link has expired."),
            status_code=400,
        )

    booking.status = "confirmed"
    booking.confirmed_at = datetime.utcnow()
    db.commit()

    return HTMLResponse(_success_html(booking))


# ─── Manual MS Bookings sync (on-demand) ─────────────────────────────────────


@router.post("/nexus/ms-bookings/sync")
async def trigger_ms_bookings_sync(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Manually trigger the MS Bookings poll for testing.

    The background loop already runs every ~5 minutes; this endpoint is for
    operators who just configured credentials and want to verify the pull
    works without waiting. Returns the same summary dict the loop logs.
    """
    from nexus.services.ms_bookings_sync import sync_ms_bookings

    try:
        result = await sync_ms_bookings(db)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MS Bookings sync failed: {e}") from e
    return {"ok": True, **result}


# ─── List bookings ────────────────────────────────────────────────────────────


@router.get("/nexus/bookings")
def list_bookings(
    limit: int = 50,
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """List demo bookings for the workspace.

    Optional slice filter (additive — when neither is set, this endpoint
    behaves identically to the pre-filter version, returning every
    booking for the workspace):

    - `product_id`  → only bookings tied to this product (matched either
      via the booking's own product_id column, or via its campaign's
      product_id, since legacy webhook-captured bookings sometimes
      have NULL product_id but a real campaign_id).
    - `entity_type` → 'product' | 'service'. Restricts to bookings whose
      campaign's product matches the chosen entity type via the
      `nexus_products.icp.entity_type` JSONB key. Missing/empty values
      default to 'product' (same convention as the dashboard's
      per-product endpoint).

    Anything else for entity_type (None, '', 'all', garbage) → no
    filter applied.
    """
    workspace_id = extract_workspace_id(workspace)
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service"):
        et = None

    q = db.query(DemoBooking).filter(DemoBooking.workspace_id == workspace_id)

    if product_id is not None or et is not None:
        # Build the campaign-IDs subquery that defines the slice. Using a
        # `text(...)` fragment is intentional — it keeps the original ORM
        # path untouched for the no-filter case (no JOIN added), and uses
        # the same JSONB predicate as the analytics endpoints so a filter
        # value of 'product' includes legacy rows with NULL icp.entity_type.
        sub_where = []
        sub_params: Dict[str, Any] = {}
        needs_products = et is not None
        if product_id is not None:
            sub_params["_b_pid"] = int(product_id)
            sub_where.append("c.product_id = :_b_pid")
        if needs_products:
            sub_params["_b_et"] = et
            if et == "product":
                sub_where.append(
                    "COALESCE(NULLIF(p.icp->>'entity_type', ''), 'product') = :_b_et"
                )
            else:
                sub_where.append("p.icp->>'entity_type' = :_b_et")
        join_p = " JOIN nexus_products p ON p.id = c.product_id" if needs_products else ""
        slice_sql = (
            f"SELECT c.id FROM nexus_campaigns c{join_p} "
            f"WHERE " + " AND ".join(sub_where)
        )
        slice_subq = text(slice_sql).bindparams(**sub_params)

        # Match bookings whose campaign falls in the slice OR (when a
        # specific product_id was requested) whose own product_id column
        # directly matches — webhook-captured bookings sometimes have
        # product_id set but campaign_id NULL.
        from sqlalchemy import or_
        conds = [DemoBooking.campaign_id.in_(slice_subq)]
        if product_id is not None:
            conds.append(DemoBooking.product_id == int(product_id))
        q = q.filter(or_(*conds))

    rows = (
        q.order_by(DemoBooking.id.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [DemoBookingOut.model_validate(r) for r in rows]


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _fallback_workspace_id(db: Session) -> Optional[int]:
    """Best-effort workspace_id when the booking's attendee email doesn't
    match any lead. Used to keep webhook-captured bookings visible to the
    operator instead of orphaning them with workspace_id=0.

    Resolution order:
      1. `MS_BOOKING_DEFAULT_WORKSPACE_ID` env var (explicit operator pick).
      2. Workspace owned by `SEED_USER_EMAIL` (the operator's own).
      3. None — caller should log + skip the insert.
    """
    import os

    raw = (os.getenv("MS_BOOKING_DEFAULT_WORKSPACE_ID") or "").strip()
    if raw.isdigit():
        return int(raw)

    seed_email = (os.getenv("SEED_USER_EMAIL") or "").strip().lower()
    if not seed_email:
        return None
    try:
        row = db.execute(
            text(
                "SELECT w.id FROM nexus_workspaces w "
                "JOIN users u ON u.id = w.owner_id "
                "WHERE LOWER(u.email) = :em "
                "ORDER BY w.id LIMIT 1"
            ),
            {"em": seed_email},
        ).first()
        if row:
            return int(row[0])
    except Exception:
        db.rollback()
    return None


def _resolve_lead_and_workspace(db: Session, email: str):
    """Look up the lead by email, return (global_lead_id, workspace_id, campaign_id)
    or (None, None, None).

    Workspace + campaign attachment lives in `nexus_leads`, NOT on
    `nexus_global_leads` (which is workspace-agnostic per Phase 3). The
    previous query referenced non-existent columns and silently returned
    nothing for every match — causing every captured booking to land with
    NULL lead_id/campaign_id, which then poisoned the briefing's product
    resolver into using the workspace's first product instead of the
    actual campaign's product.

    Picks the most-recent `nexus_leads` row (by `id DESC`) so an active
    lead's latest campaign attachment wins. If a global lead has no
    workspace attachment yet (rare — booking webhook fired before
    discovery enrolment), still returns the global_lead_id so the
    caller can at least pull the lead's name/company.
    """
    if not email:
        return None, None, None
    try:
        row = db.execute(
            text(
                """SELECT gl.id, nl.workspace_id, nl.campaign_id
                     FROM nexus_global_leads gl
                     LEFT JOIN nexus_leads nl ON nl.global_lead_id = gl.id
                    WHERE LOWER(gl.email) = :em
                    ORDER BY nl.id DESC NULLS LAST
                    LIMIT 1
                """
            ),
            {"em": email.lower()},
        ).first()
    except Exception as e:  # noqa: BLE001
        logger.warning("_resolve_lead_and_workspace query failed: %s", e)
        db.rollback()
        return None, None, None

    if not row:
        return None, None, None
    return (
        int(row[0]),
        int(row[1]) if row[1] is not None else None,
        int(row[2]) if row[2] is not None else None,
    )


def _parse_iso(value: Optional[str]):
    if not value:
        return None
    try:
        # Accept "...Z" by converting to "+00:00"
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _success_html(booking: DemoBooking) -> str:
    when = booking.scheduled_at.strftime("%a %d %b %Y, %H:%M UTC") if booking.scheduled_at else "TBD"
    name = booking.attendee_name or booking.attendee_email or "(unknown)"
    email = booking.attendee_email or ""
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Demo Confirmed</title>
<style>body{{font-family:-apple-system,sans-serif;background:#fff;color:#000;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.card{{border:1px solid rgba(0,0,0,0.1);border-radius:12px;padding:32px;max-width:480px;width:100%}}
h1{{font-size:22px;margin:0 0 12px;color:#000}}
.row{{margin:6px 0;color:rgba(0,0,0,0.7)}}
.accent{{color:#ff4500;font-weight:600}}
</style></head>
<body><div class='card'>
<h1>Demo Confirmed</h1>
<div class='row'><strong>Lead:</strong> {name}</div>
<div class='row'><strong>Email:</strong> {email}</div>
<div class='row'><strong>When:</strong> {when}</div>
<p class='accent'>The lead will receive a confirmation email shortly.</p>
</div></body></html>"""


# ─── Demo briefing — pre-call dossier ─────────────────────────────────────────


def _refine_briefing_markdown(*, current_md: str, instruction: str) -> str:
    """Refine an existing briefing by feeding the current markdown + a
    user instruction back to Gemini. Used by the "make it more detailed",
    "add competitor comparison", "shorten the objections" re-prompt flows.

    Returns the revised markdown, or the original on any failure so a
    misfiring LLM call never destroys work.
    """
    import os
    if not current_md.strip() or not instruction.strip():
        return current_md

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return current_md

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception:
        return current_md

    prompt = (
        "You are revising an existing B2B pre-demo briefing written in Markdown.\n\n"
        "Apply the user's instruction below. Keep the same Markdown structure "
        "(headings, bullet lists) unless the instruction explicitly asks to "
        "change it. Keep all factual content that the instruction doesn't "
        "contradict. Do not invent new facts about the attendee or company — "
        "only elaborate, condense, or reframe what is already there based on "
        "the instruction. In particular, do NOT introduce any number, "
        "percentage, metric, timeframe, price, or customer name that is not "
        "already present in the current briefing, even if the instruction asks "
        "for more detail — add detail by expanding on what is there, never by "
        "inventing figures. Output ONLY the revised Markdown, no commentary, "
        "no 'Here is the revised briefing:' preamble.\n\n"
        "── CURRENT BRIEFING ──\n"
        f"{current_md}\n\n"
        "── USER INSTRUCTION ──\n"
        f"{instruction.strip()}\n\n"
        "── REVISED BRIEFING ──"
    )
    # Disable Gemini's hidden "thinking" so the entire token budget goes to the
    # visible briefing sections (prevents tail-end truncation). Wrapped in
    # try/except so older google-genai SDKs without ThinkingConfig still work.
    _config_kwargs = {"temperature": 0.4, "max_output_tokens": 10000}
    try:
        _config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:  # noqa: BLE001 — SDK too old to support ThinkingConfig
        pass
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=gemini.CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**_config_kwargs),
        )
        revised = (resp.text or "").strip()
        return revised or current_md
    except Exception as exc:  # noqa: BLE001
        logger.warning("briefing refine Gemini call failed: %s", exc)
        return current_md


def build_briefing_grounding(
    db: Session,
    *,
    workspace_id: Optional[int],
    product_id: Optional[int],
    lead_id: Optional[int],
    product_name: str,
    product_value_prop: str,
    company_hint: str,
) -> Dict[str, Any]:
    """Assemble the REAL evidence a briefing is allowed to draw on.

    The briefing used to be generated from five bare strings (names + value
    prop), so every "About the Company" line and every quantified "quick win"
    was model prior — plausible prose with nothing behind it. This gathers the
    same three grounded sources the agenda uses:

      - product-KB chunks (RAG over the indexed product knowledge base),
      - the lead's own company background (`nexus_lead_enrichment`: site
        description + recent news, already scraped at discovery),
      - the prospect's own words from their replies.

    Every field degrades to empty rather than guessing. Never raises — a
    briefing with thin grounding is still generated, but it is *told* its inputs
    are thin so it stays short instead of inventing.
    """
    out: Dict[str, Any] = {
        "kb_snippets": [],
        "company": {},
        "lead": {},
        "voiced_needs": [],
    }

    # 1. Product KB (RAG) — reuses the agenda's angled multi-query retrieval so
    #    both artifacts ground on the same evidence.
    try:
        from nexus.services import meeting_agenda_agent

        ctx: Dict[str, Any] = {
            "product_id": product_id,
            "workspace_id": workspace_id,
            "product": {"name": product_name, "value_proposition": product_value_prop},
            "company": {"name": company_hint},
            "voiced_needs": [],
        }
        meeting_agenda_agent.ground_product_facts(db, ctx)
        out["kb_snippets"] = ctx.get("kb_snippets") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("briefing grounding: KB retrieval skipped: %s", exc)

    # 2. The person, and their own company — via the shared company loader, so
    #    the briefing and the agenda always describe the account identically.
    try:
        from nexus.services.company_context import load_company_context

        out["company"] = load_company_context(
            db, lead_id=lead_id, company_name=company_hint, ensure_fresh=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("briefing grounding: company context skipped: %s", exc)

    if lead_id:
        try:
            r = db.execute(
                text(
                    "SELECT role, linkedin_headline, person_city, person_country "
                    "FROM nexus_global_leads WHERE id = :l"
                ),
                {"l": int(lead_id)},
            ).mappings().first()
            if r:
                out["lead"] = {k: v for k, v in dict(r).items() if v}
        except Exception as exc:  # noqa: BLE001
            logger.debug("briefing grounding: lead lookup skipped: %s", exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass

        # 3. The prospect's own words.
        try:
            rows = db.execute(
                text(
                    "SELECT im.body_text FROM nexus_inbound_messages im "
                    "JOIN nexus_inbound_threads it ON im.thread_id = it.id "
                    "JOIN nexus_lead_sequences ls ON ls.id = it.lead_sequence_id "
                    "WHERE ls.lead_id = :l "
                    "AND COALESCE(TRIM(im.body_text),'') <> '' "
                    "ORDER BY im.received_at DESC LIMIT 5"
                ),
                {"l": int(lead_id)},
            ).fetchall()
            out["voiced_needs"] = [
                str(r[0]).strip().replace("\n", " ")[:400] for r in rows if r[0]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("briefing grounding: reply lookup skipped: %s", exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass

    return out


def _briefing_evidence_block(grounding: Dict[str, Any]) -> str:
    """Render the grounded evidence for the prompt. Sections the data doesn't
    support are explicitly marked '(nothing on file)' so the model is told what
    it does NOT know, rather than left to fill the silence."""
    g = grounding or {}
    lead = g.get("lead") or {}
    lines = ["── EVIDENCE (the ONLY facts you may state) ──", "", "ABOUT THE PERSON:"]
    lines.append(f"  role: {lead.get('role') or '(nothing on file)'}")
    lines.append(f"  linkedin_headline: {lead.get('linkedin_headline') or '(nothing on file)'}")
    loc = ", ".join(x for x in (lead.get("person_city"), lead.get("person_country")) if x)
    lines.append(f"  location: {loc or '(nothing on file)'}")

    lines.append("")
    try:
        from nexus.services.company_context import format_company_block

        lines.append(format_company_block(g.get("company") or {}))
    except Exception:  # noqa: BLE001
        c = g.get("company") or {}
        lines += [
            "THE PROSPECT'S COMPANY:",
            f"  name: {c.get('name') or '(nothing on file)'}",
            f"  industry: {c.get('industry') or '(nothing on file)'}",
        ]

    lines += ["", "WHAT THE PROSPECT ACTUALLY WROTE (their words — quote/paraphrase only these):"]
    voiced = g.get("voiced_needs") or []
    lines += [f"  - {v}" for v in voiced] or ["  (no replies on file)"]

    kb = g.get("kb_snippets") or []
    lines += ["", "OUR PRODUCT — KNOWLEDGE-BASE EXTRACTS (the ONLY source for product capability claims):"]
    lines += [f"  <chunk>{k}</chunk>" for k in kb] or [
        "  (no product knowledge base indexed — do NOT describe specific "
        "capabilities, integrations, or outcomes beyond the value proposition above)"
    ]
    return "\n".join(lines)


def _briefing_provenance(grounding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compact record of what a briefing was grounded on, stored on the row so a
    weak briefing can be traced to its missing inputs."""
    g = grounding or {}
    kb = len(g.get("kb_snippets") or [])
    c = g.get("company") or {}
    try:
        from nexus.services.company_context import has_signal

        company_known = has_signal(c)
    except Exception:  # noqa: BLE001
        company_known = bool(c.get("description"))
    return {
        "kb_chunks": kb,
        "has_company_background": company_known,
        "has_company_profile": bool(c.get("has_profile")),
        "news_items": len(c.get("news") or []),
        "voiced_needs": len(g.get("voiced_needs") or []),
        "grounded": kb > 0,
    }


# Rep-facing, but a rep repeating an invented metric on a live call is worse
# than a thin briefing. Inference is allowed here (unlike the prospect-facing
# agenda) — it just has to be LABELLED as inference rather than stated as fact.
_BRIEFING_RULES = (
    "GROUNDING RULES — these override every style instruction below:\n"
    "1. State as FACT only what appears in the EVIDENCE block. Nothing else.\n"
    "2. NEVER invent a number, percentage, metric, timeline, price, customer "
    "name, funding event, headcount, or tech-stack detail. If a figure is not "
    "in the evidence verbatim, do not write a figure at all.\n"
    "3. You MAY reason about likely priorities — but every such line must open "
    "with an explicit hedge ('Likely:', 'Worth testing:', 'Hypothesis:') so the "
    "rep can tell inference from fact at a glance.\n"
    "4. Product capabilities come ONLY from the knowledge-base extracts. If "
    "there are none, describe the product only at the level of its value "
    "proposition and say capability detail is unavailable.\n"
    "5. NEVER write a line whose content is 'Not on file', 'Unknown', or "
    "'N/A'. OMIT the field entirely instead. A section listing what you don't "
    "know is noise the rep has to read past — the gaps belong in "
    "'Questions to Ask', where they are useful. If a section has NO evidence "
    "at all, write one short line naming what's missing and nothing else.\n"
    "6. Do not pad. Two grounded bullets beat six thin ones. A short honest "
    "briefing beats a padded invented one.\n"
)


def _generate_briefing_markdown(
    *,
    attendee_name: str,
    attendee_email: str,
    product_name: str,
    product_value_prop: str,
    company_hint: str,
    grounding: Optional[Dict[str, Any]] = None,
) -> str:
    """Call Gemini to draft the briefing, grounded in `grounding` (see
    `build_briefing_grounding`). Returns a fallback markdown blob if the API key
    isn't configured or the call fails — we always store *something* so the UI is
    never empty.

    `grounding` is optional only so legacy/no-context callers still work; without
    it the prompt is told it has no evidence and the output stays deliberately
    thin rather than confidently wrong.
    """
    import os
    company_label = company_hint or "their company"
    product_label = product_name or "our product"
    g = grounding or {}
    _kb = g.get("kb_snippets") or []
    _co = g.get("company") or {}
    _voiced = g.get("voiced_needs") or []

    # Fallback: only what we actually know. No invented placeholder outcomes —
    # the old version shipped template metrics ("cut ramp time from 6 weeks to
    # 3") that read like findings.
    _company_line = _co.get("description") or "Not on file — ask on the call"
    _company_facts = "".join(
        f"- {label}: {_co[key]}\n"
        for key, label in (
            ("industry", "Industry"), ("headcount", "Headcount"),
            ("hq", "HQ"), ("revenue", "Revenue"),
        )
        if _co.get(key)
    )
    fallback = (
        f"## About the Person\n- {attendee_name or attendee_email}\n- Email: {attendee_email}\n"
        + (f"- Role: {(g.get('lead') or {}).get('role')}\n" if (g.get("lead") or {}).get("role") else "")
        + f"\n## About the Company\n- {company_label}\n- {_company_line}\n{_company_facts}\n"
        f"## Why This Product\n- {product_label}: {product_value_prop or 'Not on file — ask on the call'}\n\n"
        f"## How {product_label} Solves {company_label}'s Pain\n"
        "- Not on file — no product knowledge base was available to ground this.\n\n"
        f"## Quick-Win Use Cases for {company_label}\n"
        "- Not on file — ask on the call.\n\n"
        "## Talking Points\n"
        + ("".join(f"- They wrote: “{v}”\n" for v in _voiced[:3]) if _voiced
           else "- Not on file — ask on the call.\n")
        + "\n## Likely Objections\n"
        "- Not on file — ask on the call.\n\n"
        "## Questions to Ask\n"
        "- What does success look like 90 days from now?\n"
        "- Who else needs to be in the room?\n"
    )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception:
        return fallback

    prompt = (
        "Generate a pre-demo briefing in Markdown for a B2B sales rep.\n\n"
        + _BRIEFING_RULES
        + "\n"
        f"Attendee: {attendee_name} <{attendee_email}>\n"
        f"Company: {company_label}\n"
        f"Our product: {product_label} - {product_value_prop or '(no value proposition on file)'}\n\n"
        + _briefing_evidence_block(g)
        + "\n\n"
        "You MUST output EXACTLY these 8 H2 sections, in this exact order, "
        "with these exact headings (no synonyms, no variations, no extra "
        "sections, no missing sections):\n\n"
        "## About the Person\n"
        "## About the Company\n"
        "## Why This Product\n"
        f"## How {product_label} Solves {company_label}'s Pain\n"
        f"## Quick-Win Use Cases for {company_label}\n"
        "## Talking Points\n"
        "## Likely Objections\n"
        "## Questions to Ask\n\n"
        "Section content guidance:\n"
        "- 'About the Person' / 'About the Company': ONLY the facts the evidence "
        "actually carries — list those and stop. Do NOT enumerate the fields you "
        "lack (no 'Headcount: not on file' lines); turn those into questions in "
        "the last section instead. Anything you are reasoning toward must be "
        "hedged per rule 3.\n"
        "- 'Questions to Ask': open with the questions that close the biggest "
        "evidence gaps, so the unknowns are actionable rather than listed.\n"
        f"- 'How {product_label} Solves {company_label}'s Pain': 3-5 bullets, "
        "each pairing a capability drawn from the knowledge-base extracts with a "
        "need the prospect actually voiced, or (hedged) a pattern for their "
        "industry. Do not assert a pain they never mentioned as fact.\n"
        f"- 'Quick-Win Use Cases for {company_label}': 2-4 bullets describing "
        "what could be achieved early. Describe the outcome QUALITATIVELY — do "
        "not attach a timeframe, percentage, or any other number unless that "
        "exact figure appears in the evidence.\n"
        "- 'Likely Objections': bullets with one-line counters; hedge each one, "
        "these are predictions, not reported facts.\n"
        "- 'Talking Points': lead with the prospect's own words where present.\n"
        "- 'Questions to Ask': bullet list; prioritise questions that close the "
        "gaps marked '(nothing on file)' in the evidence.\n\n"
        "Style: be specific and concrete. No fluff. No filler adjectives. "
        "Never say 'as an AI', 'as a language model', or anything similar. "
        "No greeting, no closing, no preamble, no 'Here is the briefing:'. "
        "Output ONLY the Markdown body starting with the first H2."
    )
    # Disable Gemini's hidden "thinking" so the entire token budget goes to the
    # visible briefing sections (prevents tail-end truncation). Wrapped in
    # try/except so older google-genai SDKs without ThinkingConfig still work.
    _config_kwargs = {"temperature": 0.4, "max_output_tokens": 10000}
    try:
        _config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:  # noqa: BLE001 — SDK too old to support ThinkingConfig
        pass
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=gemini.CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**_config_kwargs),
        )
        text_out = (resp.text or "").strip()
        return text_out or fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("briefing Gemini call failed: %s", exc)
        return fallback


@router.get("/nexus/bookings/{booking_id}/briefing")
def get_briefing(
    booking_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Return the existing pre-call briefing for a booking, if any.

    Cheap read — does NOT regenerate via Gemini. The frontend modal calls this
    on open. If `status == 'no_briefing'`, the UI can offer a "Generate"
    button that POSTs to /nexus/bookings/{id}/briefing.

    Also returns the product attribution (name + value_proposition) so the
    modal can show which product the lead booked a demo for. Resolved
    via the same helper the briefing generator uses, so the displayed
    name always matches what the LLM was told.
    """
    workspace_id = extract_workspace_id(workspace)
    booking = (
        db.query(DemoBooking)
        .filter(
            DemoBooking.id == booking_id,
            DemoBooking.workspace_id == workspace_id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    from nexus.services.ms_bookings_sync import _resolve_product_and_company

    product_name, product_value_prop, _company_hint = _resolve_product_and_company(
        db,
        workspace_id=workspace_id,
        campaign_id=booking.campaign_id,
        lead_id=booking.lead_id,
        attendee_email=booking.attendee_email,
    )

    briefing = (
        db.query(DemoBriefing)
        .filter(DemoBriefing.demo_booking_id == booking.id)
        .first()
    )
    base = {
        "booking_id": booking.id,
        "product_name": product_name,
        "product_value_proposition": product_value_prop,
    }
    if not briefing:
        return {**base, "status": "no_briefing", "briefing_md": ""}
    return {
        **base,
        "briefing_id": briefing.id,
        "status": briefing.status or "ready",
        "briefing_md": briefing.briefing_md or "",
        "generated_at": briefing.generated_at,
        "regenerated_at": briefing.regenerated_at,
        # What this briefing was grounded on (kb_chunks, company background,
        # replies) — empty for rows generated before grounding was recorded.
        "grounding": briefing.raw_research or {},
    }


@router.post("/nexus/bookings/{booking_id}/briefing")
def generate_briefing(
    booking_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Generate (or regenerate) a pre-call briefing for a booking.

    Idempotent: an existing briefing row is updated in place. The heavy LLM
    call is synchronous (matches the rest of the NEXUS Lambda surface).
    """
    workspace_id = extract_workspace_id(workspace)
    booking = (
        db.query(DemoBooking)
        .filter(
            DemoBooking.id == booking_id,
            DemoBooking.workspace_id == workspace_id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Pull product context — booking's campaign → product is the highest-
    # fidelity source. Falls back to lead's most-recent campaign, then
    # workspace-first-product. Routed via the shared helper so the on-
    # demand POST and the polling auto-gen path always agree on the
    # product attribution for a given booking.
    from nexus.services.ms_bookings_sync import resolve_booking_attribution

    attribution = resolve_booking_attribution(
        db,
        workspace_id=workspace_id,
        campaign_id=booking.campaign_id,
        lead_id=booking.lead_id,
        attendee_email=booking.attendee_email,
    )
    product_name = attribution["product_name"]
    product_value_prop = attribution["product_value_proposition"]
    company_hint = attribution["company_hint"]

    grounding = build_briefing_grounding(
        db,
        workspace_id=workspace_id,
        product_id=booking.product_id or attribution["product_id"],
        lead_id=booking.lead_id or attribution["lead_id"],
        product_name=product_name,
        product_value_prop=product_value_prop,
        company_hint=company_hint,
    )

    briefing_md = _generate_briefing_markdown(
        attendee_name=booking.attendee_name or "",
        attendee_email=booking.attendee_email or "",
        product_name=product_name or "our product",
        product_value_prop=product_value_prop,
        company_hint=company_hint,
        grounding=grounding,
    )

    existing = (
        db.query(DemoBriefing)
        .filter(DemoBriefing.demo_booking_id == booking.id)
        .first()
    )
    now = datetime.utcnow()
    # What this briefing was actually grounded on — so a thin briefing is
    # diagnosable after the fact instead of indistinguishable from a rich one.
    provenance = _briefing_provenance(grounding)
    if existing is None:
        existing = DemoBriefing(
            workspace_id=workspace_id,
            demo_booking_id=booking.id,
            lead_id=booking.lead_id,
            briefing_md=briefing_md,
            status="ready",
            generated_at=now,
            raw_research=provenance,
        )
        db.add(existing)
    else:
        existing.briefing_md = briefing_md
        existing.status = "ready"
        existing.regenerated_at = now
        existing.raw_research = provenance

    db.commit()
    db.refresh(existing)
    return {
        "ok": True,
        "booking_id": booking.id,
        "briefing_id": existing.id,
        "status": existing.status,
        "briefing_md": existing.briefing_md,
        "grounding": provenance,
        # Echo the freshly-resolved product so the UI chip refreshes in
        # place after regenerate. Without this the frontend merges the
        # POST response over its prior state and keeps showing the stale
        # product_name from the initial GET — even when the briefing
        # body itself was regenerated against the correct product.
        "product_name": product_name,
        "product_value_proposition": product_value_prop,
    }


@router.post("/nexus/bookings/{booking_id}/briefing/refine")
def refine_briefing_endpoint(
    booking_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Re-prompt an existing briefing.

    Body: {"instruction": "make this more detailed"}

    Reads the current briefing_md, asks Gemini to revise per the user's
    instruction, writes the revised markdown back in place, and appends an
    entry to `refine_history` so we can audit / undo later. If no briefing
    exists yet, returns 404 — the caller should POST the base endpoint first.
    """
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")

    workspace_id = extract_workspace_id(workspace)
    booking = (
        db.query(DemoBooking)
        .filter(
            DemoBooking.id == booking_id,
            DemoBooking.workspace_id == workspace_id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    briefing = (
        db.query(DemoBriefing)
        .filter(DemoBriefing.demo_booking_id == booking.id)
        .first()
    )
    if not briefing or not (briefing.briefing_md or "").strip():
        raise HTTPException(
            status_code=404,
            detail="No existing briefing to refine — generate one first",
        )

    previous_md = briefing.briefing_md
    revised_md = _refine_briefing_markdown(
        current_md=previous_md,
        instruction=instruction,
    )

    now = datetime.utcnow()
    history = list(briefing.refine_history or [])
    history.append(
        {
            "ts": now.isoformat() + "Z",
            "instruction": instruction,
            "previous_md": previous_md,
        }
    )
    # Trim history to last 10 to keep the row small.
    if len(history) > 10:
        history = history[-10:]

    briefing.briefing_md = revised_md
    briefing.refine_history = history
    briefing.regenerated_at = now
    briefing.status = "ready"
    db.commit()
    db.refresh(briefing)
    return {
        "ok": True,
        "booking_id": booking.id,
        "briefing_id": briefing.id,
        "status": briefing.status,
        "briefing_md": briefing.briefing_md,
        "history_count": len(history),
    }


# ─── Meeting agenda — prospect-facing (Phase 1: view + regenerate) ────────────


@router.get("/nexus/bookings/{booking_id}/agenda")
def get_agenda(
    booking_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Return the stored prospect-facing meeting agenda for a booking. Cheap
    read — never calls the LLM. Phase 1: this agenda is NOT yet embedded in the
    prospect's invite; it's shown to the rep for validation."""
    from nexus.models_phase6 import MeetingAgenda

    workspace_id = extract_workspace_id(workspace)
    booking = (
        db.query(DemoBooking)
        .filter(DemoBooking.id == booking_id, DemoBooking.workspace_id == workspace_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    agenda = (
        db.query(MeetingAgenda)
        .filter(MeetingAgenda.demo_booking_id == booking.id)
        .first()
    )
    base = {"booking_id": booking.id, "in_invite": False}
    if not agenda:
        return {**base, "status": "no_agenda", "agenda_markdown": ""}
    return {
        **base,
        "status": "ready",
        "agenda_id": agenda.id,
        "agenda_markdown": agenda.agenda_markdown or "",
        "agenda_html": agenda.agenda_html or "",
        "model": agenda.model,
        "source": agenda.source,
        "generated_at": (agenda.generated_at.isoformat() + "Z") if agenda.generated_at else None,
        # Grounding provenance — lets the UI say WHY an agenda stayed rep-facing
        # (0 KB chunks retrieved) instead of leaving the rep guessing.
        "grounding": agenda.grounding or {},
        "grounded": bool((agenda.grounding or {}).get("grounded")),
    }


@router.post("/nexus/bookings/{booking_id}/agenda/regenerate")
def regenerate_agenda(
    booking_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """(Re)generate + persist the agenda for a booking. Does NOT resend or modify
    the invite."""
    from nexus.models_phase6 import MeetingAgenda
    from nexus.services import meeting_agenda_agent

    workspace_id = extract_workspace_id(workspace)
    booking = (
        db.query(DemoBooking)
        .filter(DemoBooking.id == booking_id, DemoBooking.workspace_id == workspace_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    res = meeting_agenda_agent.generate_for_booking(
        db, booking_id=booking.id, force=True, source="manual"
    )
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail="Agenda generation failed")
    agenda = (
        db.query(MeetingAgenda)
        .filter(MeetingAgenda.demo_booking_id == booking.id)
        .first()
    )
    return {
        "ok": True,
        "booking_id": booking.id,
        "agenda_id": agenda.id if agenda else None,
        "agenda_markdown": agenda.agenda_markdown if agenda else "",
        "agenda_html": agenda.agenda_html if agenda else "",
        "model": agenda.model if agenda else None,
        "in_invite": False,
        "grounding": (agenda.grounding or {}) if agenda else {},
        "grounded": bool(res.get("grounded")),
    }


# ─── Demo reminders — scheduler-driven sweep ──────────────────────────────────


def _verify_scheduler_secret(x_scheduler_secret: Optional[str] = Header(None)):
    if SCHEDULER_SECRET:
        if not x_scheduler_secret or x_scheduler_secret != SCHEDULER_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")


def _send_reminder_email(
    *, to: str, subject: str, body: str
) -> bool:
    """Best-effort Resend send. Returns False on any failure (caller logs)."""
    try:
        from nexus.services.resend_client import send_email
    except Exception:
        return False
    try:
        # TEMPORARY: same override as sequencer.py — use the Resend sandbox
        # sender until a verified domain (or per-user mailbox) ships.
        # Hardcoded 'reminders@pipelyt.com' was failing because pipelyt.com
        # isn't verified in Resend.
        import os as _os
        fixed_from = (
            (_os.environ.get("NEXUS_DEFAULT_FROM_EMAIL") or "onboarding@resend.dev").strip()
            or "onboarding@resend.dev"
        )
        result = send_email(
            to=to,
            from_=fixed_from,
            subject=subject,
            html="<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>",
            text=body,
        )
        return bool(result.get("ok"))
    except Exception:
        return False


@router.post(
    "/nexus/scheduler/booking-reminders",
    dependencies=[Depends(_verify_scheduler_secret)],
)
def dispatch_booking_reminders(db: Session = Depends(get_db)):
    """SCHEDULER-SECRET endpoint, fired by EventBridge every 15 minutes.

    Two windows:
      - 24h reminder: scheduled_at ∈ (now+23h45m, now+24h15m), 24h flag not yet set
      - 1h reminder:  scheduled_at ∈ (now+45m,    now+75m),    1h  flag not yet set

    Each reminder goes to both the attendee (lead) and the rep (if rep_email
    is on the booking). Failures are swallowed per-row so one bad address
    can't poison the whole tick.
    """
    now = datetime.utcnow()
    sent_24h = 0
    sent_1h = 0
    errors = 0

    # 24-hour window
    rows_24h = (
        db.query(DemoBooking)
        .filter(
            DemoBooking.status.in_(["scheduled", "confirmed", "pending_rep_confirm"]),
            DemoBooking.reminder_24h_sent == False,  # noqa: E712
            DemoBooking.scheduled_at != None,  # noqa: E711
            DemoBooking.scheduled_at >= now + timedelta(hours=23, minutes=45),
            DemoBooking.scheduled_at <= now + timedelta(hours=24, minutes=15),
        )
        .limit(200)
        .all()
    )
    for b in rows_24h:
        ok = True
        when = b.scheduled_at.strftime("%a %d %b %Y, %H:%M UTC") if b.scheduled_at else "TBD"
        if b.attendee_email:
            ok = _send_reminder_email(
                to=b.attendee_email,
                subject="Reminder: our call tomorrow",
                body=(
                    f"Hi {b.attendee_name or 'there'},\n\n"
                    f"Quick reminder that we're scheduled to chat tomorrow at {when}.\n\n"
                    "Looking forward to it — reply here if anything's changed."
                ),
            ) and ok
        if b.rep_email:
            _send_reminder_email(
                to=b.rep_email,
                subject=f"Tomorrow: demo with {b.attendee_name or b.attendee_email}",
                body=f"You have a demo at {when} with {b.attendee_email}.",
            )
        if ok:
            b.reminder_24h_sent = True
            sent_24h += 1
        else:
            errors += 1

    # 1-hour window
    rows_1h = (
        db.query(DemoBooking)
        .filter(
            DemoBooking.status.in_(["scheduled", "confirmed", "pending_rep_confirm"]),
            DemoBooking.reminder_1h_sent == False,  # noqa: E712
            DemoBooking.scheduled_at != None,  # noqa: E711
            DemoBooking.scheduled_at >= now + timedelta(minutes=45),
            DemoBooking.scheduled_at <= now + timedelta(minutes=75),
        )
        .limit(200)
        .all()
    )
    for b in rows_1h:
        ok = True
        when = b.scheduled_at.strftime("%H:%M UTC") if b.scheduled_at else "TBD"
        if b.attendee_email:
            ok = _send_reminder_email(
                to=b.attendee_email,
                subject="We're on in 1 hour",
                body=(
                    f"Hi {b.attendee_name or 'there'},\n\n"
                    f"Just a heads up — our call is at {when}, about an hour from now.\n\n"
                    "Talk soon."
                ),
            ) and ok
        if b.rep_email:
            _send_reminder_email(
                to=b.rep_email,
                subject=f"In 1h: demo with {b.attendee_name or b.attendee_email}",
                body=f"You have a demo at {when} with {b.attendee_email}.",
            )
        if ok:
            b.reminder_1h_sent = True
            sent_1h += 1
        else:
            errors += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        errors += 1

    return {
        "ok": True,
        "sent_24h": sent_24h,
        "sent_1h": sent_1h,
        "errors": errors,
    }


def _error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Link Expired</title>
<style>body{{font-family:-apple-system,sans-serif;background:#fff;color:#000;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.card{{border:1px solid rgba(0,0,0,0.1);border-radius:12px;padding:32px;max-width:480px;width:100%;text-align:center}}
h1{{color:#000;margin:0 0 12px}}
</style></head>
<body><div class='card'><h1>Link Expired or Invalid</h1><p>{message}</p></div></body></html>"""
