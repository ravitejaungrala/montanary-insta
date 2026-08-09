"""External-webhook router — distinct from /nexus/inbound.

Legacy: apps/nexus-legacy/server/routes/webhooks.js
Routes (legacy — public, signature-verified):
  POST /webhooks/twilio/call-status   — Twilio call status callback
  POST /webhooks/calendly             — Calendly event webhook
  POST /webhooks/ms-bookings          — Microsoft Bookings webhook
  POST /webhooks/booking-confirm      — internal booking-confirm callback
  POST /webhooks/resend               — Resend bounce/click/open

NOTE: /nexus/inbound already handles Resend inbound emails + Gmail
polling. This module is for status/event callbacks that don't fit the
inbound-email shape. Most live as stubs until devs port logic.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Request

router = APIRouter(prefix="/nexus/webhooks", tags=["nexus-webhooks"])


@router.post("/twilio/call-status")
async def twilio_call_status(request: Request) -> Dict[str, Any]:
    """Legacy: POST /webhooks/twilio/call-status.
    Updates nexus_voice_calls.status when Twilio reports
    queued / initiated / ringing / in-progress / completed / busy /
    no-answer / failed / canceled."""
    # Twilio posts as form-urlencoded.
    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    return {"ok": False, "stub": True, "received": form.get("CallStatus")}


@router.post("/calendly")
async def calendly_webhook(request: Request) -> Dict[str, Any]:
    body = await request.json()
    return {"ok": False, "stub": True, "event": body.get("event") if isinstance(body, dict) else None}


@router.post("/ms-bookings")
async def ms_bookings_webhook(request: Request) -> Dict[str, Any]:
    body = await request.json()
    return {"ok": False, "stub": True}


@router.post("/booking-confirm")
async def booking_confirm_webhook(request: Request) -> Dict[str, Any]:
    body = await request.json()
    return {"ok": False, "stub": True}


@router.post("/resend")
async def resend_webhook(request: Request) -> Dict[str, Any]:
    """Resend bounce/click/open passthrough. /nexus/inbound has the
    inbound-email side; this is the engagement-event side."""
    body = await request.json()
    return {"ok": False, "stub": True}
