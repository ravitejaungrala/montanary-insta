"""Phase 6 AI chat router — Gemini-backed, with session persistence.

Endpoints:
  POST /nexus/chat                        send a new message in a session
  GET  /nexus/chat/sessions               list sessions for current user/workspace
  GET  /nexus/chat/sessions/{id}          fetch session + messages
  POST /nexus/chat/sessions               create a session
  DELETE /nexus/chat/sessions/{id}        delete a session

The chat layer can optionally call lightweight read-only "tools" to fetch
real data (lead counts, top campaign by reply rate, recent demos) — we
detect tool intent by inspecting Gemini's response for a small `[[tool:...]]`
marker, run the tool against the workspace DB, then return the tool result
appended to the assistant message. This keeps tool-use synchronous and
doesn't require the full function-calling round-trip API.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus._phase6_common import get_nexus_user, require_current_workspace, extract_workspace_id
from nexus.models_phase6 import ChatMessage, ChatSession
from nexus.schemas_phase6 import ChatRequest, ChatResponse

logger = logging.getLogger("pipelyt.nexus.chat")

try:
    from core.config import GEMINI_API_KEY  # type: ignore
except Exception:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

from nexus.services.gemini import CHAT_MODEL as GEMINI_MODEL  # single source of truth
GEMINI_BASE = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SYSTEM_PROMPT = (
    "You are Nexus AI, the built-in assistant for the Pipelyt Nexus GTM workspace. "
    "Help sales reps with concise answers about their pipeline, leads, campaigns, "
    "demos, and products. Be brief — one short paragraph or a tight bullet list. "
    "Format numbers clearly (e.g. '42 leads', '12% reply rate'). Never make up "
    "numbers; if data is unavailable, say so plainly.\n\n"
    "You may call ONE of these read-only tools by emitting a single line containing "
    "ONLY `[[tool:name]]` on its own line. The system will run the tool and feed the "
    "result back. Available tools:\n"
    "  [[tool:lead_counts]]      — total + per-status lead counts in this workspace\n"
    "  [[tool:top_campaigns]]    — top 5 campaigns by reply rate\n"
    "  [[tool:recent_demos]]     — upcoming demos in the next 7 days\n"
    "Only emit a tool call when the user explicitly needs that data. Otherwise answer directly."
)

router = APIRouter(prefix="/nexus/chat", tags=["nexus-chat"])


# ---------- Schemas ----------------------------------------------------------


class ChatSessionOut(BaseModel):
    id: int
    workspace_id: int
    user_id: int
    title: str
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatMessageOut(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    tool_name: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatSessionDetail(BaseModel):
    session: ChatSessionOut
    messages: List[ChatMessageOut]


class SendMessageRequest(BaseModel):
    session_id: Optional[int] = None  # if None, a new session is created
    message: str


class SendMessageResponse(BaseModel):
    session_id: int
    message: str
    tool_used: Optional[str] = None


# ---------- Tools (read-only, workspace-scoped) ------------------------------


def _tool_lead_counts(db: Session, workspace_id: int) -> Dict[str, Any]:
    try:
        rows = db.execute(
            text(
                "SELECT status, COUNT(1) FROM nexus_global_leads "
                "WHERE workspace_id = :w GROUP BY status"
            ),
            {"w": workspace_id},
        ).fetchall()
        return {"by_status": {r[0]: int(r[1]) for r in rows}}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _tool_top_campaigns(db: Session, workspace_id: int) -> Dict[str, Any]:
    try:
        rows = db.execute(
            text(
                """
                SELECT c.id, c.name,
                       COUNT(DISTINCT CASE WHEN ls.status='replied' THEN ls.lead_id END) AS replies,
                       COUNT(DISTINCT ls.lead_id) AS leads
                  FROM nexus_campaigns c
                  LEFT JOIN nexus_lead_sequences ls ON ls.campaign_id = c.id
                 WHERE c.workspace_id = :w
                   AND EXISTS (
                       SELECT 1 FROM nexus_leads nl
                        WHERE nl.campaign_id = c.id
                   )
                 GROUP BY c.id, c.name
                 ORDER BY replies DESC
                 LIMIT 5
                """
            ),
            {"w": workspace_id},
        ).fetchall()
        return {
            "campaigns": [
                {
                    "id": int(r[0]),
                    "name": r[1],
                    "replies": int(r[2] or 0),
                    "leads": int(r[3] or 0),
                    "reply_rate_pct": round((int(r[2] or 0) / int(r[3] or 1)) * 100, 1)
                    if int(r[3] or 0)
                    else 0.0,
                }
                for r in rows
            ]
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _tool_recent_demos(db: Session, workspace_id: int) -> Dict[str, Any]:
    try:
        rows = db.execute(
            text(
                "SELECT attendee_name, attendee_email, scheduled_at, status "
                "FROM nexus_demo_bookings "
                "WHERE workspace_id = :w "
                "  AND scheduled_at IS NOT NULL "
                "  AND scheduled_at >= NOW() "
                "  AND scheduled_at <= NOW() + INTERVAL '7 days' "
                "ORDER BY scheduled_at ASC LIMIT 10"
            ),
            {"w": workspace_id},
        ).fetchall()
        return {
            "demos": [
                {
                    "name": r[0] or "",
                    "email": r[1] or "",
                    "at": r[2].isoformat() if r[2] else None,
                    "status": r[3] or "",
                }
                for r in rows
            ]
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


_TOOLS = {
    "lead_counts": _tool_lead_counts,
    "top_campaigns": _tool_top_campaigns,
    "recent_demos": _tool_recent_demos,
}


def _maybe_run_tool(
    db: Session, workspace_id: int, assistant_text: str
) -> Optional[Dict[str, Any]]:
    """If the assistant emitted [[tool:name]], run it and return the result.

    Returns: {'tool': name, 'result': dict} or None.
    """
    import re

    m = re.search(r"\[\[tool:([a-z_]+)\]\]", assistant_text or "")
    if not m:
        return None
    name = m.group(1)
    fn = _TOOLS.get(name)
    if not fn:
        return {"tool": name, "result": {"error": f"unknown tool {name}"}}
    return {"tool": name, "result": fn(db, workspace_id)}


# ---------- Gemini call ------------------------------------------------------


async def _call_gemini(history: List[Dict[str, str]]) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    contents = []
    for m in history[-20:]:
        role = "model" if m["role"] in ("assistant", "tool") else "user"
        contents.append({"role": role, "parts": [{"text": (m.get("content") or "")[:4000]}]})

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1024},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{GEMINI_BASE}?key={GEMINI_API_KEY}",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Gemini HTTP %s: %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(status_code=502, detail="Chat service unavailable") from e
    except Exception as e:  # noqa: BLE001
        logger.warning("Gemini error: %s", e)
        raise HTTPException(status_code=502, detail="Chat service unavailable") from e

    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", []) or []
    text_part = next((p.get("text") for p in parts if p.get("text")), "")
    return text_part or ""


# ---------- Endpoints --------------------------------------------------------


@router.post("", response_model=ChatResponse)
async def nexus_chat_oneshot(
    payload: ChatRequest,
    user: Any = Depends(get_nexus_user),
):
    """Legacy stateless endpoint — does not persist messages. Kept for
    backward compatibility with the existing UI. New UI should use
    POST /nexus/chat/sessions/{id}/message."""
    if not payload.messages:
        raise HTTPException(status_code=400, detail="messages array required")
    history = [
        {"role": m.role, "content": m.content or ""} for m in payload.messages[-20:]
    ]
    text_part = await _call_gemini(history)
    return ChatResponse(message=text_part or "I couldn't generate a response. Please try again.")


@router.get("/sessions", response_model=List[ChatSessionOut])
def list_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace)
    rows = (
        db.query(ChatSession)
        .filter(
            ChatSession.workspace_id == ws_id,
            ChatSession.user_id == user.id,
        )
        .order_by(ChatSession.last_message_at.desc().nullslast())
        .limit(100)
        .all()
    )
    return [ChatSessionOut.model_validate(r) for r in rows]


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
def create_session(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace)
    row = ChatSession(workspace_id=ws_id, user_id=user.id, title="New chat")
    db.add(row)
    db.commit()
    db.refresh(row)
    return ChatSessionOut.model_validate(row)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace)
    sess = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session_id,
            ChatSession.workspace_id == ws_id,
            ChatSession.user_id == user.id,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return ChatSessionDetail(
        session=ChatSessionOut.model_validate(sess),
        messages=[ChatMessageOut.model_validate(m) for m in msgs],
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace)
    sess = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session_id,
            ChatSession.workspace_id == ws_id,
            ChatSession.user_id == user.id,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(sess)
    db.commit()
    return None


@router.post("/sessions/{session_id}/message", response_model=SendMessageResponse)
async def send_message(
    session_id: int,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace)
    sess = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session_id,
            ChatSession.workspace_id == ws_id,
            ChatSession.user_id == user.id,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # Persist user message
    now = datetime.utcnow()
    db.add(ChatMessage(session_id=session_id, role="user", content=payload.message))
    sess.last_message_at = now
    if not sess.title or sess.title == "New chat":
        sess.title = (payload.message or "")[:60] or "New chat"
    db.commit()

    # Build history from DB so persisted context is included.
    history_rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(40)
        .all()
    )
    history = [{"role": r.role, "content": r.content or ""} for r in history_rows]

    # First model pass
    assistant_text = await _call_gemini(history)
    tool_used: Optional[str] = None
    tool_result_payload: Optional[Dict[str, Any]] = None

    tool_call = _maybe_run_tool(db, ws_id, assistant_text)
    if tool_call:
        tool_used = tool_call["tool"]
        tool_result_payload = tool_call["result"]
        # Persist the tool turn
        db.add(
            ChatMessage(
                session_id=session_id,
                role="tool",
                content=json.dumps(tool_result_payload)[:8000],
                tool_name=tool_used,
                tool_result=tool_result_payload,
            )
        )
        db.commit()
        # Second pass: hand the result back to the model for a natural-language answer.
        history.append({"role": "tool", "content": json.dumps(tool_result_payload)[:4000]})
        history.append(
            {
                "role": "user",
                "content": "Given that tool result, answer my question above in plain language. Don't emit another tool call.",
            }
        )
        assistant_text = await _call_gemini(history)

    db.add(
        ChatMessage(
            session_id=session_id,
            role="assistant",
            content=assistant_text or "(no response)",
            tool_name=tool_used,
            tool_result=tool_result_payload,
        )
    )
    sess.last_message_at = datetime.utcnow()
    db.commit()

    return SendMessageResponse(
        session_id=session_id,
        message=assistant_text or "I couldn't generate a response. Please try again.",
        tool_used=tool_used,
    )
