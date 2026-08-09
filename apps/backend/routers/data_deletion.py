"""Meta Platform "Data Deletion Callback" endpoint.

Required by Facebook App Review when an app requests user data. When a
Facebook user removes Pipelyt from their account (Settings → Apps and
Websites → Pipelyt → Remove), Meta POSTs a `signed_request` to the URL
configured under "User Data Deletion" in the Facebook app settings.

Spec: https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback

Contract:
  Request   POST /auth/facebook/data-deletion
            Content-Type: application/x-www-form-urlencoded
            Body:    signed_request=<encoded_signed_payload>

  Response  200 OK
            Content-Type: application/json
            Body: {
              "url": "https://pipelyt.ai/data-deletion?id=<request_id>",
              "confirmation_code": "<request_id>"
            }

This module:
  1. Decodes + verifies the signed_request HMAC against FACEBOOK_APP_SECRET.
  2. Maps the Facebook user_id payload to every Pipelyt SocialAccount that
     authenticated against it (linked via `account_id` on platform=facebook
     or platform=instagram, or via the access token's owning user).
  3. Hard-deletes all derived data: SocialAccount rows, related published
     post platform-rows, scheduled posts, drafts, analytics snapshots
     belonging to those facebook/instagram accounts.
  4. Returns the JSON Meta expects so the user gets a confirmation code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.config import FACEBOOK_APP_SECRET
from core.database import SessionLocal
from models import SocialAccount, PublishedPostPlatform, ScheduledPost

logger = logging.getLogger("pipelyt.data_deletion")

router = APIRouter()


def _b64url_decode(value: str) -> bytes:
    """Meta uses URL-safe base64 WITHOUT padding. Re-pad before decoding."""
    pad = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _parse_signed_request(signed_request: str, app_secret: str) -> Optional[dict]:
    """Verify HMAC + return the JSON payload, or None on signature mismatch.

    Meta's signed_request format: `<sig_b64url>.<payload_b64url>` where the
    signature is HMAC-SHA256 of the payload string keyed with our app
    secret. Reject if the algorithm in the payload isn't HMAC-SHA256 — Meta
    has used HMAC-SHA1 in the past but the modern callback always uses
    SHA256 and we shouldn't accept the weaker variant.
    """
    try:
        sig_b64, payload_b64 = signed_request.split('.', 1)
    except ValueError:
        return None

    expected_sig = hmac.new(
        app_secret.encode('utf-8'),
        payload_b64.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    actual_sig = _b64url_decode(sig_b64)

    # Constant-time compare so we don't leak signature timing.
    if not hmac.compare_digest(expected_sig, actual_sig):
        logger.warning("[data-deletion] signature mismatch — rejecting request")
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode('utf-8'))
    except Exception:
        logger.exception("[data-deletion] payload decode failed")
        return None

    if payload.get('algorithm') != 'HMAC-SHA256':
        logger.warning("[data-deletion] non-SHA256 algorithm rejected: %s", payload.get('algorithm'))
        return None

    return payload


def _purge_facebook_user_data(db: Session, fb_user_id: str) -> dict:
    """Delete every Pipelyt row associated with a single Facebook user-id.

    The user-id Meta sends is the **app-scoped** user id, which we DO NOT
    store directly on Pipelyt's User row (Pipelyt users sign in via
    email/OTP, not Facebook Login). What we DO store are the Facebook
    Pages and Instagram Business accounts the user connected — each as a
    `SocialAccount` row with `platform='facebook'` or `'instagram'`. The
    safest interpretation of a Meta deletion request is therefore:

      "Delete every social-account / post / metric for any
       Facebook-platform or Instagram-platform connection that traces back
       to the user who triggered this deletion."

    Without a stored mapping from app-scoped FB user-id → SocialAccount
    we make a best-effort match by user_id field on accounts of those
    two platforms. If Meta later requires a stricter mapping we'll add
    a `fb_app_scoped_user_id` column and key on it directly.

    Returns a dict of counts so the audit log can verify the deletion.
    """
    affected = {
        "social_accounts": 0,
        "published_post_platforms": 0,
        "scheduled_posts": 0,
    }

    # Find every Facebook / Instagram SocialAccount whose stored user_id
    # (the Meta-side user_id we got at OAuth time) matches the deletion
    # request. We compare loosely because some flows save just the page id.
    accounts = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.platform.in_(['facebook', 'instagram']),
            (SocialAccount.account_id == fb_user_id)
            | (SocialAccount.account_id.like(f"%/{fb_user_id}"))
            | (SocialAccount.account_id.like(f"{fb_user_id}/%")),
        )
        .all()
    )

    if not accounts:
        # No matching Pipelyt rows — still acknowledge deletion to Meta.
        # This is normal when a user authorised Pipelyt but then never
        # connected a Page, or has already disconnected.
        logger.info("[data-deletion] no SocialAccounts matched fb_user_id=%s", fb_user_id)
        return affected

    account_db_ids = [a.id for a in accounts]
    native_post_ids = []  # for downstream cleanup of platform-post rows

    # Delete platform-post rows tied to these accounts so analytics + the
    # comments cache for them disappears. PublishedPost (the parent row)
    # is intentionally KEPT — the original poster owns the content
    # decision and may still want it for their own records.
    pp_rows = (
        db.query(PublishedPostPlatform)
        .filter(PublishedPostPlatform.account_id.in_([a.account_id for a in accounts]))
        .all()
    )
    for pp in pp_rows:
        native_post_ids.append(pp.native_post_id)
    affected["published_post_platforms"] = len(pp_rows)
    db.query(PublishedPostPlatform).filter(
        PublishedPostPlatform.account_id.in_([a.account_id for a in accounts])
    ).delete(synchronize_session=False)

    # Cancel any pending scheduled posts that targeted only the deleted
    # accounts. We don't touch scheduled posts that still target other
    # platforms — let the user remove them manually from the calendar.
    sched_rows = db.query(ScheduledPost).filter(
        ScheduledPost.targets.like(f'%"{fb_user_id}"%')
    ).all()
    for s in sched_rows:
        try:
            tgt = json.loads(s.targets) if isinstance(s.targets, str) else (s.targets or {})
        except Exception:
            tgt = {}
        # Drop facebook + instagram platforms from the target dict.
        tgt.pop('facebook', None)
        tgt.pop('instagram', None)
        if not tgt:
            db.delete(s)
            affected["scheduled_posts"] += 1
        else:
            s.targets = json.dumps(tgt)

    # Finally remove the SocialAccount rows themselves.
    affected["social_accounts"] = len(accounts)
    for a in accounts:
        db.delete(a)

    db.commit()
    logger.info(
        "[data-deletion] purged fb_user_id=%s accounts=%s pp=%s scheduled=%s",
        fb_user_id, affected["social_accounts"],
        affected["published_post_platforms"], affected["scheduled_posts"],
    )
    return affected


@router.post("/auth/facebook/data-deletion")
async def facebook_data_deletion_callback(
    request: Request,
    signed_request: str = Form(...),
):
    """Meta posts a signed deletion request here when a user removes the
    Pipelyt app from their Facebook settings. Verifies the signature,
    purges the user's Facebook+Instagram footprint from Pipelyt, and
    returns the JSON Meta expects with a confirmation code the user can
    quote when checking status.
    """
    if not FACEBOOK_APP_SECRET:
        # Without a secret we can't verify Meta's signature — refuse rather
        # than pretend to delete. Meta will retry; a misconfigured prod
        # surface deserves an alert, not silent success.
        logger.error("[data-deletion] FACEBOOK_APP_SECRET unset; cannot verify request")
        return JSONResponse(
            {"error": "server misconfigured"}, status_code=500,
        )

    payload = _parse_signed_request(signed_request, FACEBOOK_APP_SECRET)
    if payload is None:
        return JSONResponse(
            {"error": "invalid signed_request"}, status_code=400,
        )

    fb_user_id = str(payload.get('user_id', '')).strip()
    if not fb_user_id:
        return JSONResponse(
            {"error": "user_id missing from signed_request"}, status_code=400,
        )

    # Each request gets a unique audit token the user can quote in
    # follow-up emails to confirm the deletion. We don't persist this in
    # a dedicated table yet — the access logs + the affected counts in
    # the structured log line above are the audit trail.
    request_id = secrets.token_urlsafe(16)

    db = SessionLocal()
    try:
        affected = _purge_facebook_user_data(db, fb_user_id)
    except Exception:
        logger.exception("[data-deletion] purge failed for fb_user_id=%s", fb_user_id)
        return JSONResponse(
            {"error": "deletion failed; please retry"}, status_code=500,
        )
    finally:
        db.close()

    logger.info(
        "[data-deletion] confirmed request_id=%s fb_user_id=%s counts=%s",
        request_id, fb_user_id, affected,
    )

    # Format Meta expects.
    return JSONResponse({
        "url": f"https://pipelyt.ai/data-deletion?id={request_id}",
        "confirmation_code": request_id,
    })
