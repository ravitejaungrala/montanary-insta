"""Phase 6 unsubscribe router.

Endpoint:
  GET /nexus/unsubscribe?token=...  PUBLIC — token-verified; HTML success page.

On success:
  - Insert into nexus_suppression_list (workspace_id, email)
  - Update GlobalLead.status = 'unsubscribed'
  - Halt all active LeadSequence rows
  - Return brand-styled HTML
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_tokens import decode_unsub_token

logger = logging.getLogger("pipelyt.nexus.unsubscribe")

router = APIRouter(prefix="/nexus/unsubscribe", tags=["nexus-unsubscribe"])


@router.get("")
def unsubscribe(token: str = Query(...), db: Session = Depends(get_db)):
    payload = decode_unsub_token(token)
    if not payload:
        return HTMLResponse(_invalid_html(), status_code=400)

    lead_id = payload["lead_id"]
    workspace_id = payload["workspace_id"]
    now = datetime.utcnow()

    # Look up email
    email = ""
    try:
        row = db.execute(
            text("SELECT email FROM nexus_global_leads WHERE id = :id"),
            {"id": lead_id},
        ).first()
        if row:
            email = (row[0] or "").lower()
    except Exception:
        db.rollback()

    # Add to suppression list (Phase 3 table — graceful skip if absent)
    if email:
        try:
            db.execute(
                text(
                    """INSERT INTO nexus_suppression_list (workspace_id, email, reason, suppressed_at, created_at)
                       VALUES (:ws, :em, 'unsubscribed', :now, :now)
                       ON CONFLICT (workspace_id, email) DO NOTHING"""
                ),
                {"ws": workspace_id, "em": email, "now": now},
            )
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("suppression insert skipped: %s", e)
            db.rollback()

    # Update GlobalLead status
    try:
        db.execute(
            text("UPDATE nexus_global_leads SET status='unsubscribed' WHERE id = :id"),
            {"id": lead_id},
        )
        db.commit()
    except Exception:
        db.rollback()

    # Halt active sequences
    try:
        db.execute(
            text(
                """UPDATE nexus_lead_sequences
                   SET status='unsubscribed', last_action_at = :now
                   WHERE lead_id = :lid AND status IN ('active','paused','replied')"""
            ),
            {"now": now, "lid": lead_id},
        )
        db.commit()
    except Exception:
        db.rollback()

    # Mark any nexus_outreach rows for this lead as unsubscribed
    try:
        db.execute(
            text(
                """UPDATE nexus_outreach SET status='unsubscribed'
                   WHERE lead_id = :lid AND status NOT IN ('unsubscribed','bounced')"""
            ),
            {"lid": lead_id},
        )
        db.commit()
    except Exception:
        db.rollback()

    return HTMLResponse(_success_html(email))


def _success_html(email: str) -> str:
    return f"""<!DOCTYPE html>
<html lang='en'><head><meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>Unsubscribed</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh;
     margin:0;background:#fff;color:#000}}
.card{{background:#fff;border:1px solid rgba(0,0,0,0.1);border-radius:12px;
      padding:48px 40px;max-width:440px;text-align:center}}
h1{{font-size:22px;margin-bottom:12px;color:#000}}
p{{color:rgba(0,0,0,0.6);font-size:15px;line-height:1.6}}
.accent{{color:#ff4500;font-weight:600}}
</style></head><body>
<div class='card'>
<h1>You've been unsubscribed</h1>
<p>{email or 'You'} will no longer receive emails from this campaign.</p>
<p class='accent'>If this was a mistake, reply to the original email to opt back in.</p>
</div></body></html>"""


def _invalid_html() -> str:
    return """<!DOCTYPE html>
<html lang='en'><head><meta charset='UTF-8'><title>Invalid Link</title>
<style>
body{font-family:-apple-system,sans-serif;display:flex;align-items:center;
     justify-content:center;min-height:100vh;margin:0;background:#fff;color:#000}
.card{background:#fff;border:1px solid rgba(0,0,0,0.1);border-radius:12px;
      padding:40px;max-width:440px;text-align:center}
h1{color:#000;margin-bottom:12px}
p{color:rgba(0,0,0,0.6)}
</style></head><body>
<div class='card'><h1>Invalid or Expired Link</h1>
<p>This unsubscribe link is no longer valid.</p></div></body></html>"""
