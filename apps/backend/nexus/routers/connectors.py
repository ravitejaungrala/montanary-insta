"""Mailbox connectors — let a workspace connect its OWN sending mailbox
(Outlook or Gmail) via OAuth so outreach emails go out FROM the customer's
mailbox instead of the shared Apollo/Resend sender.

Endpoints (under /nexus/connectors):
  GET  /status                     — list this workspace's connected mailboxes
  GET  /{provider}/authorize       — begin OAuth (returns consent URL)
  GET  /{provider}/callback        — provider redirect target (no auth header)
  POST /{account_id}/disconnect    — revoke/forget a connected mailbox

`provider` is one of: outlook, gmail. The callback is hit by the user's
BROWSER (an OAuth redirect), so it carries no auth header. We thread the
workspace id through the OAuth `state` param, persisted in
`pending_oauth_syncs` (the same Lambda-safe trick the Canva flow uses), and
resolve it on the way back.
"""

from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import PendingOAuthSync, User
from nexus.deps import block_user_writes, get_nexus_user, require_current_workspace
from nexus.models_phase4 import NexusConversationAccount, NexusSenderBrand
from nexus.services.connectors import SUPPORTED_PROVIDERS, get_connector
from nexus.services.url_norm import brand_key as _norm_url

logger = logging.getLogger("pipelyt.nexus.connectors")

# Router-level: read-only Users are blocked from all writes (connect/map/
# disconnect/delete); reads stay open.
router = APIRouter(
    prefix="/nexus/connectors",
    tags=["Nexus — Connectors"],
    dependencies=[Depends(block_user_writes)],
)

_STATE_TTL_MINUTES = 15


def _frontend_url() -> str:
    return (os.getenv("FRONTEND_URL") or "http://localhost:5173").rstrip("/")


def _state_key(provider: str, state: str) -> str:
    """Namespaced key stored in pending_oauth_syncs.state_or_token."""
    return f"nexus_{provider}:{state}"


def _serialize(row: NexusConversationAccount) -> Dict[str, Any]:
    connected = bool(
        (row.status or "") == "active"
        and (row.provider in SUPPORTED_PROVIDERS)
        and row.refresh_token
    )
    return {
        "id": row.id,
        "email_address": row.email_address,
        "display_name": row.display_name or "",
        "provider": row.provider,
        "status": row.status,
        "connected": connected,
        "daily_send_limit": row.daily_send_limit,
        "daily_send_count": row.daily_send_count,
        # Which sender brand (card) this mailbox belongs to (None = unassigned).
        "brand_id": row.brand_id,
    }


def _serialize_brand(b: NexusSenderBrand) -> Dict[str, Any]:
    return {
        "id": b.id,
        "name": b.name or "",
        "url": b.url or "",
        "entity_type": b.entity_type or "product",
        "booking_url": b.booking_url or "",
    }


# Allowed demo-booking providers (host suffixes). Kept in one place so it's
# easy to extend. Mirror this list in the frontend (NexusConnectors.jsx).
_BOOKING_HOSTS = (
    # Microsoft Bookings
    "outlook.office.com",
    "outlook.office365.com",
    "bookings.cloud.microsoft",
    "bookings.microsoft.com",
    # Google (Calendar appointment schedules)
    "calendar.google.com",
    "calendar.app.google",
    # Calendly
    "calendly.com",
)

_BOOKING_MSG = "Please enter a valid Microsoft Bookings, Google, or Calendly link."


def _valid_booking_url(url: str) -> bool:
    """True when `url` is a well-formed http(s) link whose host is one of the
    allowed booking providers. Host match is suffix-based so subdomains like
    `acme.calendly.com` pass."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == h or host.endswith("." + h) for h in _BOOKING_HOSTS)


def _sync_brands_from_products(db: Session, workspace_id: int) -> None:
    """Auto-create a brand card for each DISTINCT (normalized URL, entity_type)
    among the workspace's products — so existing products/companies show up as
    cards (duplicates collapsed). Users can also add brands manually."""
    try:
        prods = db.execute(
            text(
                "SELECT name, COALESCE(source_url,''), "
                "COALESCE(NULLIF(icp->>'entity_type',''),'product') "
                "FROM nexus_products "
                "WHERE workspace_id = :w AND COALESCE(status,'') <> 'archived'"
            ),
            {"w": workspace_id},
        ).fetchall()
    except Exception:
        db.rollback()
        return

    existing = {
        (b.url_norm, b.entity_type)
        for b in db.query(NexusSenderBrand)
        .filter(NexusSenderBrand.workspace_id == workspace_id)
        .all()
    }
    seen: set = set()
    new_rows: List[NexusSenderBrand] = []
    for name, url, etype in prods:
        un = _norm_url(url or "")
        if not un:
            continue
        key = (un, etype)
        if key in existing or key in seen:
            continue
        seen.add(key)
        new_rows.append(
            NexusSenderBrand(
                workspace_id=workspace_id,
                name=name or un,
                url=url or un,
                url_norm=un,
                entity_type=etype,
            )
        )
    if new_rows:
        db.add_all(new_rows)
        try:
            db.commit()
        except Exception:
            db.rollback()


def _list_brands(db: Session, workspace_id: int) -> List[NexusSenderBrand]:
    return (
        db.query(NexusSenderBrand)
        .filter(NexusSenderBrand.workspace_id == workspace_id)
        .order_by(NexusSenderBrand.name.asc())
        .all()
    )


def _wake_brand_leads(db: Session, workspace_id: int, brand: Optional[NexusSenderBrand]) -> None:
    """When a mailbox is connected/mapped to a brand, set that brand's HELD
    leads to due-now so the next heartbeat (minutes) sends them — instead of
    waiting for the next hourly retry. Only touches leads that were held for a
    missing/capped mailbox (last_error mentions 'mailbox')."""
    if brand is None:
        return
    try:
        rows = db.execute(
            text(
                "SELECT c.id, COALESCE(p.source_url,''), "
                "COALESCE(NULLIF(p.icp->>'entity_type',''),'product') "
                "FROM nexus_campaigns c JOIN nexus_products p ON p.id = c.product_id "
                "WHERE c.workspace_id = :w"
            ),
            {"w": workspace_id},
        ).fetchall()
        cids = [
            int(cid)
            for cid, url, et in rows
            if _norm_url(url or "") == (brand.url_norm or "")
            and (et or "product") == (brand.entity_type or "product")
        ]
        if not cids:
            return
        db.execute(
            text(
                "UPDATE nexus_lead_sequences "
                "SET next_action_at = NOW(), locked_until = NULL "
                "WHERE status = 'active' AND campaign_id = ANY(:cids) "
                "AND last_error ILIKE '%mailbox%'"
            ),
            {"cids": cids},
        )
        db.commit()
        logger.info("woke held leads for brand=%s (%d campaigns)", brand.id, len(cids))
    except Exception:
        db.rollback()


# ── Status ───────────────────────────────────────────────────────────────────


@router.get("/status")
def connectors_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """List the OAuth mailboxes connected for this workspace + which
    connectors are configured at the server level."""
    rows: List[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(
            NexusConversationAccount.workspace_id == workspace.id,
            NexusConversationAccount.provider.in_(SUPPORTED_PROVIDERS),
        )
        .order_by(NexusConversationAccount.id.asc())
        .all()
    )
    # Surface existing products/companies as cards (deduped by URL), then
    # list all cards — including any the user added manually.
    _sync_brands_from_products(db, workspace.id)
    brands = _list_brands(db, workspace.id)
    # Display-time dedup: collapse cards that share a domain-name key (brand_key)
    # + entity_type into ONE, preferring the card that has a mailbox mapped. This
    # is purely cosmetic for the response — NO DB writes, underlying rows are
    # left intact (routing already matches by brand_key, so the extra rows are
    # harmless). Resolves duplicate cards without touching the DB.
    by_key: Dict[tuple, NexusSenderBrand] = {}
    for b in brands:
        k = (_norm_url(b.url or b.url_norm or ""), b.entity_type or "product")
        cur = by_key.get(k)
        if cur is None:
            by_key[k] = b
        elif not db.query(NexusConversationAccount).filter_by(brand_id=cur.id).count() \
                and db.query(NexusConversationAccount).filter_by(brand_id=b.id).count():
            by_key[k] = b  # prefer the card that actually has a mailbox
    brands = list(by_key.values())
    outlook = get_connector("outlook")
    gmail = get_connector("gmail")
    return {
        "accounts": [_serialize(r) for r in rows],
        "brands": [_serialize_brand(b) for b in brands],
        "outlook_available": bool(outlook and outlook.is_configured()),
        "gmail_available": bool(gmail and gmail.is_configured()),
    }


@router.get("/brand-status")
def brand_status(
    url: str = Query(""),
    entity_type: str = Query("product"),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Does the sender brand for this URL + type have a CONNECTED mailbox?
    Used by New Run to warn the user that emails will hold until a mailbox is
    connected."""
    un = _norm_url(url or "")
    et = (entity_type or "product").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = "product"
    brand = (
        db.query(NexusSenderBrand)
        .filter(
            NexusSenderBrand.workspace_id == workspace.id,
            NexusSenderBrand.url_norm == un,
            NexusSenderBrand.entity_type == et,
        )
        .first()
    )
    has_mailbox = False
    if brand is not None:
        has_mailbox = (
            db.query(NexusConversationAccount)
            .filter(
                NexusConversationAccount.brand_id == brand.id,
                NexusConversationAccount.status == "active",
                NexusConversationAccount.refresh_token.isnot(None),
            )
            .first()
            is not None
        )
    return {"has_mailbox": has_mailbox, "brand_exists": brand is not None}


# ── Brand cards (add / remove) ───────────────────────────────────────────────


@router.post("/brands")
def create_brand(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Add a sender card: { entity_type, url, name? }. Keyed by the
    normalized URL + entity_type (same dedup New Run uses for products)."""
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A website URL is required.")
    entity_type = (body.get("entity_type") or "product").strip().lower()
    if entity_type not in ("product", "service", "gcc"):
        entity_type = "product"
    url_norm = _norm_url(url)
    name = str(body.get("name") or "").strip()
    if not name:
        try:
            host = urlparse(url if "://" in url else f"https://{url}").hostname or url
            name = host.replace("www.", "")
        except Exception:
            name = url

    existing = (
        db.query(NexusSenderBrand)
        .filter(
            NexusSenderBrand.workspace_id == workspace.id,
            NexusSenderBrand.url_norm == url_norm,
            NexusSenderBrand.entity_type == entity_type,
        )
        .first()
    )
    if existing is not None:
        existing.name = name or existing.name
        existing.url = url or existing.url
        db.commit()
        return {"brand": _serialize_brand(existing), "created": False}

    brand = NexusSenderBrand(
        workspace_id=workspace.id,
        name=name,
        url=url,
        url_norm=url_norm,
        entity_type=entity_type,
    )
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return {"brand": _serialize_brand(brand), "created": True}


@router.delete("/brands/{brand_id}")
def delete_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Remove a sender card. Its mailboxes are unassigned (not disconnected)."""
    brand = (
        db.query(NexusSenderBrand)
        .filter(
            NexusSenderBrand.id == brand_id,
            NexusSenderBrand.workspace_id == workspace.id,
        )
        .first()
    )
    if brand is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    db.query(NexusConversationAccount).filter(
        NexusConversationAccount.brand_id == brand_id
    ).update({"brand_id": None})
    db.delete(brand)
    db.commit()
    return {"ok": True, "id": brand_id}


@router.post("/brands/{brand_id}/booking-link")
def update_brand_booking_url(
    brand_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Set (or clear) a brand's demo booking link: { booking_url }. An empty
    string clears it. A non-empty value must be a valid Microsoft Bookings /
    Google / Calendly URL — validated server-side so the UI can't bypass it."""
    brand = (
        db.query(NexusSenderBrand)
        .filter(
            NexusSenderBrand.id == brand_id,
            NexusSenderBrand.workspace_id == workspace.id,
        )
        .first()
    )
    if brand is None:
        raise HTTPException(status_code=404, detail="Card not found.")

    booking_url = str(body.get("booking_url") or "").strip()
    if booking_url and not _valid_booking_url(booking_url):
        raise HTTPException(status_code=400, detail=_BOOKING_MSG)

    brand.booking_url = booking_url or None
    db.commit()
    return {"brand": _serialize_brand(brand)}


# ── OAuth — begin ─────────────────────────────────────────────────────────────


@router.get("/{provider}/authorize")
def oauth_authorize(
    provider: str,
    brand_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Return the provider consent URL. The frontend opens it; after the
    user consents, the provider redirects to /{provider}/callback.

    `brand_id` (optional) maps the newly-connected mailbox to that sender
    card automatically — used when the user clicks Connect from a card.
    """
    connector = get_connector(provider)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {provider}")
    if not connector.is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"The {provider} connector is not configured on the server.",
        )

    state = secrets.token_urlsafe(24)
    # Persist "workspace_id:brand_id" so the (unauthenticated) callback can
    # resolve the workspace AND auto-map the mailbox to the sender card.
    db.add(
        PendingOAuthSync(
            state_or_token=_state_key(provider, state),
            secret_or_verifier=f"{workspace.id}:{brand_id if brand_id else ''}",
        )
    )
    db.commit()

    try:
        auth_url = connector.build_auth_url(state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s authorize: failed to build auth url", provider)
        raise HTTPException(status_code=500, detail=f"Auth URL error: {exc}") from exc

    return {"auth_url": auth_url}


# ── OAuth — callback (browser redirect, NO auth header) ───────────────────────


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Provider redirects here after consent. Exchange the code, store the
    mailbox under the workspace from `state`, then bounce the browser back
    to the app's Connectors page."""
    landing = f"{_frontend_url()}/?nexus_connector={provider}"

    connector = get_connector(provider)
    if connector is None:
        return RedirectResponse(url=f"{landing}&status=error")

    if error:
        logger.warning("%s callback error: %s / %s", provider, error, error_description)
        return RedirectResponse(url=f"{landing}&status=error")
    if not (code and state):
        return RedirectResponse(url=f"{landing}&status=error")

    # Resolve workspace from the persisted state, then consume it.
    pending = (
        db.query(PendingOAuthSync)
        .filter(PendingOAuthSync.state_or_token == _state_key(provider, state))
        .first()
    )
    if pending is None:
        logger.warning("%s callback: unknown/expired state", provider)
        return RedirectResponse(url=f"{landing}&status=error")

    if pending.created_at and pending.created_at < datetime.utcnow() - timedelta(
        minutes=_STATE_TTL_MINUTES
    ):
        db.delete(pending)
        db.commit()
        return RedirectResponse(url=f"{landing}&status=expired")

    # secret_or_verifier = "workspace_id:brand_id" (brand_id may be empty).
    raw_state = pending.secret_or_verifier or ""
    ws_part, _, brand_part = raw_state.partition(":")
    try:
        workspace_id = int(ws_part)
    except (TypeError, ValueError):
        workspace_id = 0
    try:
        mapped_brand_id = int(brand_part) if brand_part else None
    except (TypeError, ValueError):
        mapped_brand_id = None
    db.delete(pending)
    db.commit()

    if not workspace_id:
        return RedirectResponse(url=f"{landing}&status=error")

    try:
        tokens = connector.exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s callback: token exchange failed: %s", provider, exc)
        return RedirectResponse(url=f"{landing}&status=error")

    email = (tokens.get("email") or "").strip().lower()
    if not email:
        logger.warning("%s callback: no email in token response", provider)
        return RedirectResponse(url=f"{landing}&status=error")

    # Upsert by email_address (the column is globally unique).
    existing: Optional[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(NexusConversationAccount.email_address == email)
        .first()
    )
    if existing is not None:
        # "Actively connected" = active status + a live refresh token. Only an
        # ACTIVE connection owned by ANOTHER workspace blocks re-claim. A
        # disconnected / token-less record is free to be re-claimed by the
        # current workspace (falls through; workspace_id is reassigned below).
        already_connected = (existing.status or "") == "active" and bool(existing.refresh_token)
        if (
            existing.workspace_id
            and existing.workspace_id != workspace_id
            and already_connected
        ):
            logger.warning(
                "%s callback: %s actively owned by ws=%s (attempted ws=%s)",
                provider, email, existing.workspace_id, workspace_id,
            )
            return RedirectResponse(url=f"{landing}&status=conflict")
        # Already connected AND mapped to a DIFFERENT card in THIS workspace →
        # refuse to silently move it. The user must disconnect from the other
        # card first. (Re-connecting to the SAME card just refreshes the token.)
        if (
            already_connected
            and existing.brand_id
            and mapped_brand_id
            and existing.brand_id != mapped_brand_id
        ):
            owner = db.execute(
                text("SELECT name FROM nexus_sender_brands WHERE id = :b"),
                {"b": existing.brand_id},
            ).first()
            owner_name = (owner[0] if owner else "another card") or "another card"
            logger.info(
                "%s callback: %s already mapped to brand=%s — refusing re-map",
                provider, email, existing.brand_id,
            )
            return RedirectResponse(
                url=f"{landing}&status=mapped&product={quote(owner_name)}"
            )
        row = existing
    else:
        row = NexusConversationAccount(
            workspace_id=workspace_id,
            email_address=email,
            daily_send_limit=20,
            daily_send_count=0,
        )
        db.add(row)

    row.workspace_id = workspace_id
    row.provider = provider
    row.status = "active"
    row.display_name = tokens.get("display_name") or ""
    row.tenant_id = tokens.get("tenant_id") or ""
    # Drop any brand mapping that belongs to a DIFFERENT workspace (e.g. a
    # re-claimed mailbox that was previously mapped in another workspace).
    if row.brand_id:
        _owns_existing = db.execute(
            text("SELECT 1 FROM nexus_sender_brands WHERE id = :b AND workspace_id = :w"),
            {"b": row.brand_id, "w": workspace_id},
        ).first()
        if not _owns_existing:
            row.brand_id = None
    # Auto-map to the sender card the user connected from (if any). Validated
    # against the workspace so a stale state can't map across workspaces.
    if mapped_brand_id:
        owns = db.execute(
            text("SELECT 1 FROM nexus_sender_brands WHERE id = :b AND workspace_id = :w"),
            {"b": mapped_brand_id, "w": workspace_id},
        ).first()
        if owns:
            row.brand_id = mapped_brand_id
    row.access_token = tokens.get("access_token")
    row.refresh_token = tokens.get("refresh_token")
    row.token_expires_at = tokens.get("expires_at")
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("%s callback: save failed: %s", provider, exc)
        return RedirectResponse(url=f"{landing}&status=error")

    # Wake any held leads for this brand so they resume in minutes.
    if row.brand_id:
        brand = (
            db.query(NexusSenderBrand)
            .filter(NexusSenderBrand.id == row.brand_id)
            .first()
        )
        _wake_brand_leads(db, workspace_id, brand)

    logger.info("%s connected: ws=%s email=%s", provider, workspace_id, email)
    return RedirectResponse(url=f"{landing}&status=connected&email={email}")


# ── Map a mailbox to a sender card ───────────────────────────────────────────


@router.post("/{account_id}/map")
def map_account(
    account_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Assign a connected mailbox to a sender card (or clear it).

    Body: { "brand_id": <int|null> }. A campaign whose product matches that
    card's URL then rotates sends across the card's mailboxes.
    """
    row: Optional[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(
            NexusConversationAccount.id == account_id,
            NexusConversationAccount.workspace_id == workspace.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connected account not found.")

    raw = body.get("brand_id")
    if raw in (None, "", 0, "0"):
        row.brand_id = None
    else:
        try:
            bid = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="brand_id must be an integer.")
        owns = db.execute(
            text("SELECT 1 FROM nexus_sender_brands WHERE id = :b AND workspace_id = :w"),
            {"b": bid, "w": workspace.id},
        ).first()
        if not owns:
            raise HTTPException(status_code=404, detail="Card not found in this workspace.")
        row.brand_id = bid

    db.commit()
    # Wake held leads for the newly-mapped brand so they resume in minutes.
    if row.brand_id:
        brand = (
            db.query(NexusSenderBrand)
            .filter(NexusSenderBrand.id == row.brand_id)
            .first()
        )
        _wake_brand_leads(db, workspace.id, brand)
    logger.info("mailbox %s mapped to brand=%s (ws=%s)", account_id, row.brand_id, workspace.id)
    return {"ok": True, "id": account_id, "brand_id": row.brand_id}


# ── Disconnect ─────────────────────────────────────────────────────────────────


@router.post("/{account_id}/disconnect")
def disconnect(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Forget a connected mailbox: mark inactive and wipe the stored tokens.
    The row is kept (not deleted) so send-history/limits stay intact."""
    row: Optional[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(
            NexusConversationAccount.id == account_id,
            NexusConversationAccount.workspace_id == workspace.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connected account not found.")

    row.status = "disconnected"
    row.access_token = None
    row.refresh_token = None
    row.token_expires_at = None
    db.commit()
    logger.info("disconnected: ws=%s email=%s", workspace.id, row.email_address)
    return {"ok": True, "id": account_id}
