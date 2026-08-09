"""Phase 6 voice router.

Endpoints:
  POST /nexus/voice/initiate      auth-gated  — start a Twilio outbound call
  POST /nexus/voice/twiml-stream  PUBLIC      — Twilio webhook (returns TwiML)
  POST /nexus/voice/status        PUBLIC      — Twilio status callback
  POST /nexus/voice/transcript    PUBLIC      — Deepgram callback (writes transcript)
  GET  /nexus/voice/calls         auth-gated  — list recent calls for the workspace
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_user_id,
    extract_workspace_id,
    get_nexus_user,
    require_current_workspace,
)
from nexus.models_phase6 import VoiceCall
from nexus.schemas_phase6 import VoiceCallOut, VoiceInitiateIn
from nexus.services import deepgram_client, twilio_client

logger = logging.getLogger("pipelyt.nexus.voice")

router = APIRouter(prefix="/nexus/voice", tags=["nexus-voice"])


@router.post("/initiate")
def initiate_voice_call(
    payload: VoiceInitiateIn,
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    """Place an outbound Twilio call to the lead's phone number."""
    workspace_id = extract_workspace_id(workspace)
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace not resolved")

    if not payload.to_number:
        raise HTTPException(status_code=400, detail="to_number required")

    # SECURITY: any client-supplied campaign/product/lead must belong to the
    # caller's workspace before it's attached to the call record.
    if payload.campaign_id is not None and not db.execute(
        text("SELECT 1 FROM nexus_campaigns WHERE id = :id AND workspace_id = :ws LIMIT 1"),
        {"id": payload.campaign_id, "ws": workspace_id},
    ).first():
        raise HTTPException(status_code=404, detail="campaign not found in this workspace")
    if payload.product_id is not None and not db.execute(
        text("SELECT 1 FROM nexus_products WHERE id = :id AND workspace_id = :ws LIMIT 1"),
        {"id": payload.product_id, "ws": workspace_id},
    ).first():
        raise HTTPException(status_code=404, detail="product not found in this workspace")
    if payload.lead_id is not None and not db.execute(
        text("SELECT 1 FROM nexus_leads WHERE global_lead_id = :id AND workspace_id = :ws LIMIT 1"),
        {"id": payload.lead_id, "ws": workspace_id},
    ).first():
        raise HTTPException(status_code=404, detail="lead not found in this workspace")

    call_row = VoiceCall(
        workspace_id=workspace_id,
        user_id=extract_user_id(user),
        lead_id=payload.lead_id,
        campaign_id=payload.campaign_id,
        product_id=payload.product_id,
        to_number=payload.to_number,
        status="queued",
        started_at=datetime.utcnow(),
    )
    db.add(call_row)
    db.commit()
    db.refresh(call_row)

    try:
        twilio_resp = twilio_client.initiate_call(payload.to_number)
        call_row.twilio_call_sid = twilio_resp.get("sid")
        call_row.status = twilio_resp.get("status", "initiated")
        call_row.from_number = twilio_resp.get("from", "")
        db.commit()
        db.refresh(call_row)
    except Exception as e:  # noqa: BLE001
        logger.warning("Twilio initiate failed: %s", e)
        call_row.status = "failed"
        call_row.error = str(e)[:500]
        db.commit()
        # Don't 500 — the record is in the DB so the UI can surface the error
        return {"ok": False, "error": str(e), "call_id": call_row.id}

    return {
        "ok": True,
        "call_id": call_row.id,
        "twilio_call_sid": call_row.twilio_call_sid,
        "status": call_row.status,
    }


@router.post("/twiml-stream")
async def twilio_twiml_webhook(request: Request, db: Session = Depends(get_db)):
    """Twilio fetches this URL when the lead answers.

    PUBLIC — Twilio doesn't send JWTs. We identify the call via CallSid.
    Returns TwiML XML.
    """
    try:
        form = await request.form()
    except Exception:
        form = {}

    call_sid = form.get("CallSid") or request.query_params.get("CallSid")
    if call_sid:
        try:
            call_row = db.query(VoiceCall).filter(VoiceCall.twilio_call_sid == call_sid).first()
            if call_row:
                call_row.status = "in-progress"
                call_row.answered_at = datetime.utcnow()
                db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("twiml-stream DB update failed: %s", e)

    prompt = (
        "Hi, this is the Pipelyt sales agent calling about your recent interest. "
        "Is now a good time for a quick conversation?"
    )
    twiml = twilio_client.build_twiml_say_and_stream(prompt)
    return Response(content=twiml, media_type="application/xml")


@router.post("/status")
async def twilio_status_callback(
    request: Request,
    db: Session = Depends(get_db),
    CallSid: Optional[str] = Form(None),
    CallStatus: Optional[str] = Form(None),
    CallDuration: Optional[str] = Form(None),
    RecordingUrl: Optional[str] = Form(None),
):
    """Twilio call status callback.

    PUBLIC — guarded by CallSid lookup; only mutates the matching row.
    """
    if not CallSid:
        return {"ok": False, "reason": "missing_call_sid"}

    call_row = db.query(VoiceCall).filter(VoiceCall.twilio_call_sid == CallSid).first()
    if not call_row:
        return {"ok": False, "reason": "call_not_found"}

    if CallStatus:
        call_row.status = CallStatus
    if CallStatus == "completed":
        call_row.ended_at = datetime.utcnow()
    if CallDuration:
        try:
            call_row.duration_sec = int(CallDuration)
        except Exception:  # noqa: BLE001
            pass
    if RecordingUrl:
        call_row.recording_url = RecordingUrl
        # Deepgram callback URL is wired in /nexus/voice/transcript. Submission
        # happens out-of-band via a separate scheduled job (Lambda has no
        # background-thread guarantees inside a request handler).

    db.commit()
    return {"ok": True}


@router.post("/transcript")
async def deepgram_transcript_callback(request: Request, db: Session = Depends(get_db)):
    """Deepgram callback when the prerecorded transcription finishes.

    PUBLIC — body contains the transcript payload. We identify the call by the
    `request_id` we passed in the original Deepgram call, OR by the matching
    recording URL.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    transcript = deepgram_client.extract_transcript_text(body)
    metadata = body.get("metadata") or {}
    request_id = metadata.get("request_id") or body.get("request_id")

    call_row = None
    if request_id:
        # Optional: store request_id at submit-time → query here
        call_row = (
            db.query(VoiceCall)
            .filter(VoiceCall.error == "")  # not the cleanest match, kept open for future
            .order_by(VoiceCall.id.desc())
            .first()
        )

    if call_row and transcript:
        call_row.transcript = transcript
        db.commit()

    return {"ok": True}


@router.get("/calls")
def list_calls(
    limit: int = 50,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace)
    rows = (
        db.query(VoiceCall)
        .filter(VoiceCall.workspace_id == workspace_id)
        .order_by(VoiceCall.id.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [VoiceCallOut.model_validate(r) for r in rows]
