from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import os
import requests
import json
import base64
import uuid
import secrets
from datetime import datetime
from typing import Optional
import logging
import traceback
import hashlib
from jose import jwt, JWTError  # for _validate_token_qs — popup carries JWT in ?token=
from core.database import get_db
from core.config import (
    LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET,
    LINKEDIN_PERSONAL_CLIENT_ID, LINKEDIN_PERSONAL_CLIENT_SECRET,
    REDIRECT_URI,
    FACEBOOK_APP_ID, FACEBOOK_APP_SECRET, FACEBOOK_REDIRECT_URI,
    TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_REDIRECT_URI,
    TWITTER_CLIENT_ID, TWITTER_CLIENT_SECRET,
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI,
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI,
    PINTEREST_CLIENT_ID, PINTEREST_CLIENT_SECRET, PINTEREST_REDIRECT_URI,
    SECRET_KEY, ALGORITHM, FRONTEND_URL
)
from core.database import get_db, SessionLocal
from models import User, SocialAccount, PendingOAuthSync, ReconnectRequest
from routers.auth import block_user_writes, get_current_user
from routers.analytics import sync_all_user_accounts
from services.plan_service import check_quota

logger = logging.getLogger("pipelyt.social")

# C-3: unified Facebook Graph API version across auth + token + data calls.
FB_GRAPH_VERSION = "v19.0"

# postMessage origin — which origin we send AUTH_SUCCESS/AUTH_ERROR to.
# We need to support MULTIPLE frontend environments (prod, staging, dev) because
# the same backend Lambda can serve both staging.app.pipelyt.ai and
# app.pipelyt.ai. FRONTEND_URL only captures one. The safest approach:
#   - If FRONTEND_URLS is configured (list), send to ANY of them that matches
#     the popup's opener origin by iterating on the frontend side.
#   - On the backend, use '*' for the target origin. That's acceptable because
#     this is an OAuth flow opened from the same app — the popup's `window.opener`
#     is already trusted (it IS our app), and the message payload contains
#     tokens that are ALSO visible server-side via our database.
# This is the pragmatic fix. If we need to lock down later, pass the origin
# via `state` param in the OAuth URL and echo it back in postMessage target.
_PM_ORIGIN = "*"
try:
    from core.config import FRONTEND_URLS as _FE_URLS  # noqa
    logger.info(f"[social.py] postMessage target='*'; configured frontend URLs: {_FE_URLS}")
except Exception:
    pass

router = APIRouter(dependencies=[Depends(block_user_writes)])  # Users blocked from writes

# Separate router for OAuth-start endpoints that the browser opens in a popup
# window. These can ONLY receive the user's JWT as a `?token=` query param
# (popups don't carry Authorization headers cross-window), so we can't gate
# them with `block_user_writes` — that dependency reads the bearer token from
# the header via OAuth2PasswordBearer and 401s when it's missing. The OAuth
# callback URLs (where TikTok/LinkedIn/etc. redirect back) ALSO live here
# because they're navigated to by TikTok's servers, not the Pipelyt SPA, and
# carry no auth either. JWT validation for the start endpoints happens
# manually inside each route via `_require_token_qs`.
open_oauth_router = APIRouter()

@open_oauth_router.get("/auth/linkedin")
async def linkedin_auth(token: str | None = None, source: str | None = None):
    # Popup-friendly auth: JWT comes via ?token= since window.open can't send
    # an Authorization header. See _validate_token_qs for the rationale.
    _validate_token_qs(token)
    # ═══════════════════════════════════════════════════════════════════
    # LinkedIn OAuth now runs against the "Personal" app (LinkedIn's
    # Community Management API app), NOT the legacy Share+OIDC app.
    # LinkedIn hard-forbids Community Management API from coexisting with
    # OpenID Connect / Share on LinkedIn on the same developer app, so we
    # gave up openid/profile/email in exchange for:
    #   - r_member_postAnalytics    → personal post metrics
    #   - r_member_profileAnalytics → personal follower count
    #   - w_member_social_feed      → reply to comments on personal posts
    #   - r_organization_social_feed  → org post engagement (comments/reactions)
    #   - w_organization_social_feed  → reply on org posts
    #   - r_organization_followers    → org followers data
    # r_basicprofile replaces openid/profile for name/photo/URL display.
    # We don't ship a LinkedIn email through — Pipelyt already has the
    # user's email from Pipelyt account signup.
    # ═══════════════════════════════════════════════════════════════════
    scope = (
        # Personal identity + basic profile
        "r_basicprofile "
        "r_1st_connections_size "
        # Personal analytics (Community Management)
        "r_member_profileAnalytics "
        "r_member_postAnalytics "
        # Personal publish + engagement
        "w_member_social "
        "w_member_social_feed "
        # Org management
        "rw_organization_admin "
        "r_organization_followers "
        # Org read
        "r_organization_social "
        "r_organization_social_feed "
        # Org publish + engagement
        "w_organization_social "
        "w_organization_social_feed"
    )
    from urllib.parse import quote
    encoded_scope = quote(scope)
    encoded_state = quote(source or "")
    auth_url = (
        f"https://www.linkedin.com/oauth/v2/authorization?response_type=code&"
        f"client_id={LINKEDIN_PERSONAL_CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"scope={encoded_scope}&"
        f"state={encoded_state}"
    )
    logger.info(
        f"[LINKEDIN_AUTH] Redirecting to LinkedIn consent — "
        f"client_id={LINKEDIN_PERSONAL_CLIENT_ID[:8]}... (Community Management app) "
        f"redirect_uri={REDIRECT_URI} scope_count={len(scope.split())} "
        f"scopes=[{scope}]"
    )
    return RedirectResponse(auth_url)

from requests_oauthlib import OAuth1Session

@open_oauth_router.get("/auth/twitter")
async def twitter_auth(request: Request, token: str | None = None, source: str | None = None, db: Session = Depends(get_db)):
    """Starts Twitter OAuth 1.0a flow, which will chain into 2.0 PKCE.
    Using Database-backed storage to bypass cookie issues.
    """
    _validate_token_qs(token)
    try:
        from requests_oauthlib import OAuth1Session
        oauth = OAuth1Session(TWITTER_API_KEY, client_secret=TWITTER_API_SECRET)
        request_token_url = "https://api.twitter.com/oauth/request_token"
        
        fetch_response = oauth.fetch_request_token(request_token_url, params={"oauth_callback": TWITTER_REDIRECT_URI})
        
        resource_owner_key = fetch_response.get('oauth_token')
        resource_owner_secret = fetch_response.get('oauth_token_secret')
        
        if not resource_owner_key:
            raise HTTPException(status_code=500, detail="Failed to get Twitter 1.0a request token")
            
        # Store 1.0a request secret in database (BYPASS COOKIES)
        db.add(PendingOAuthSync(state_or_token=resource_owner_key, secret_or_verifier=resource_owner_secret))
        db.commit()
            
        base_authorization_url = 'https://api.twitter.com/oauth/authorize'
        authorization_url = oauth.authorization_url(base_authorization_url)
        return RedirectResponse(authorization_url)
    except Exception as e:
        logger.error(f"Twitter 1.0a initiation failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Twitter 1.0a error: {str(e)}")

@open_oauth_router.get("/auth/linkedin/callback")
async def linkedin_callback(
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    state: str | None = None,
):
    """LinkedIn OAuth callback — Community Management API app.

    Flow:
      1. Exchange auth code → access_token (uses LINKEDIN_PERSONAL_CLIENT_*)
      2. Fetch personal identity via /v2/me + /v2/me/profilePicture
         (Community Management app does NOT have `openid`, so we can't use
         /v2/userinfo. `r_basicprofile` grants /v2/me instead.)
      3. Fetch admin organizations via /v2/organizationAcls
      4. Return SocialAccount payloads to the popup opener

    When LinkedIn returns ?error=... (e.g. unauthorized_scope_error, user
    denied consent), there is NO `code` param. We handle that up front and
    surface a clean popup-close with the error message.

    Every LinkedIn API boundary logs URL + status + body-snippet so we can
    see exactly what the Community Management app is returning.
    """
    # ── LinkedIn returned an error (no code) — surface it and close cleanly ─
    if error or not code:
        # HTML-entity-decode the description so the popup shows readable text.
        import html
        desc = html.unescape(error_description or "")
        logger.warning(
            f"[LINKEDIN_CALLBACK] LinkedIn returned error — "
            f"error={error!r} description={desc!r} state={state!r}"
        )
        if error == "unauthorized_scope_error":
            logger.warning(
                "[LINKEDIN_CALLBACK] HINT: this means the requested scope is not "
                "granted on the LinkedIn app whose Client ID is in "
                "LINKEDIN_PERSONAL_CLIENT_ID. Confirm the app has the "
                "Community Management API product added, and confirm the .env "
                "has App 1's Client ID (NOT App 2's)."
            )
        safe_msg = ((error or "linkedin_error") +
                    (": " + desc if desc else "")).replace("'", "\\'").replace("`", "\\`")
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'linkedin', error: '{safe_msg}' }}, "
            f"'{_PM_ORIGIN}'); window.close();</script>"
        ))

    try:
        logger.info(
            f"[LINKEDIN_CALLBACK] START — code_len={len(code or '')} "
            f"redirect_uri={REDIRECT_URI} "
            f"client_id={LINKEDIN_PERSONAL_CLIENT_ID[:8]}... (Community Management)"
        )

        # ── 1. Exchange auth code for access token ────────────────────
        token_url = "https://www.linkedin.com/oauth/v2/accessToken"
        data = {
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     LINKEDIN_PERSONAL_CLIENT_ID,
            "client_secret": LINKEDIN_PERSONAL_CLIENT_SECRET,
        }
        tok_resp = requests.post(token_url, data=data, timeout=15)
        logger.info(
            f"[LINKEDIN_CALLBACK] token_exchange status={tok_resp.status_code} "
            f"url={token_url}"
        )
        if tok_resp.status_code != 200:
            logger.error(
                f"[LINKEDIN_CALLBACK] token exchange FAILED — "
                f"body={tok_resp.text[:400]}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to get access token: {tok_resp.text[:400]}",
            )

        tok_payload = tok_resp.json()
        token       = tok_payload.get("access_token")
        expires_in  = tok_payload.get("expires_in")
        scope_str   = tok_payload.get("scope", "(scope not returned)")
        logger.info(
            f"[LINKEDIN_CALLBACK] token OK — expires_in={expires_in}s "
            f"scopes_granted=[{scope_str}] token_len={len(token or '')}"
        )

        headers = {"Authorization": f"Bearer {token}"}
        linkedin_accounts = []

        # ── 2. Personal profile via /v2/me (r_basicprofile) ─────────────
        # Community Management API does NOT grant `openid`, so /v2/userinfo
        # would 401 here. /v2/me is the r_basicprofile-friendly path.
        me_url = (
            "https://api.linkedin.com/v2/me"
            "?projection=(id,localizedFirstName,localizedLastName,"
            "profilePicture(displayImage~:playableStreams),"
            "vanityName)"
        )
        me_res = requests.get(me_url, headers=headers, timeout=15)
        logger.info(
            f"[LINKEDIN_CALLBACK] /v2/me status={me_res.status_code} "
            f"body={me_res.text[:300]}"
        )
        if me_res.status_code == 200:
            me = me_res.json()
            person_id = me.get("id") or ""
            personal_urn = f"urn:li:person:{person_id}" if person_id else ""
            first = me.get("localizedFirstName") or ""
            last  = me.get("localizedLastName")  or ""
            # Profile picture: nested projection returns a list of image
            # elements — grab the largest available. Fallback to blank.
            picture_url = ""
            try:
                elements = (
                    me.get("profilePicture", {})
                      .get("displayImage~", {})
                      .get("elements", [])
                )
                if elements:
                    picture_url = (
                        elements[-1].get("identifiers", [{}])[0].get("identifier", "")
                    )
            except Exception as _pe:
                logger.warning(
                    f"[LINKEDIN_CALLBACK] profilePicture parse failed: {_pe}"
                )
            linkedin_accounts.append({
                "id":                  personal_urn,
                "name":                f"{first} {last}".strip() or "LinkedIn user",
                "profile_picture_url": picture_url,
                "type":                "profile",
                "token":               token,
                "platform":            "linkedin",
            })
            logger.info(
                f"[LINKEDIN_CALLBACK] personal account added — "
                f"urn={personal_urn} name='{first} {last}' picture={'yes' if picture_url else 'no'}"
            )
        else:
            logger.warning(
                f"[LINKEDIN_CALLBACK] /v2/me FAILED "
                f"({me_res.status_code}) — no personal account will be created. "
                f"Check r_basicprofile is on the token."
            )

        # ── 3. Admin organizations via /v2/organizationAcls ─────────────
        org_acls_url = (
            "https://api.linkedin.com/v2/organizationAcls"
            "?q=roleAssignee&role=ADMINISTRATOR&projection=(elements*(organization))"
        )
        org_res = requests.get(org_acls_url, headers=headers, timeout=15)
        logger.info(
            f"[LINKEDIN_CALLBACK] /v2/organizationAcls status={org_res.status_code} "
            f"body={org_res.text[:300]}"
        )
        if org_res.status_code == 200:
            elements = (org_res.json() or {}).get("elements", []) or []
            logger.info(
                f"[LINKEDIN_CALLBACK] found {len(elements)} admin org(s)"
            )
            for el in elements:
                org_urn = el.get("organization") or ""
                org_id_num = org_urn.split(":")[-1] if org_urn else ""
                if not org_id_num:
                    continue
                org_details_url = (
                    f"https://api.linkedin.com/v2/organizations/{org_id_num}"
                    f"?projection=(id,localizedName,logoV2(original~:playableStreams))"
                )
                od_res = requests.get(org_details_url, headers=headers, timeout=15)
                logger.info(
                    f"[LINKEDIN_CALLBACK]   org {org_urn} details "
                    f"status={od_res.status_code}"
                )
                if od_res.status_code != 200:
                    logger.warning(
                        f"[LINKEDIN_CALLBACK]   org details FAILED "
                        f"({od_res.status_code}) body={od_res.text[:200]}"
                    )
                    continue
                od = od_res.json() or {}
                logo_url = ""
                try:
                    logo_url = (
                        od.get("logoV2", {})
                          .get("original~", {})
                          .get("elements", [])[0]
                          .get("identifiers", [])[0]
                          .get("identifier", "")
                    )
                except Exception:
                    pass
                linkedin_accounts.append({
                    "id":                  org_urn,
                    "name":                od.get("localizedName", "LinkedIn Page"),
                    "profile_picture_url": logo_url,
                    "type":                "page",
                    "platform":            "linkedin",
                    "token":               token,
                })
                logger.info(
                    f"[LINKEDIN_CALLBACK]   org added — urn={org_urn} "
                    f"name='{od.get('localizedName', '')}' "
                    f"logo={'yes' if logo_url else 'no'}"
                )
        else:
            logger.warning(
                f"[LINKEDIN_CALLBACK] /v2/organizationAcls FAILED "
                f"({org_res.status_code}) — no org pages will be listed. "
                f"Check rw_organization_admin is on the token."
            )

        logger.info(
            f"[LINKEDIN_CALLBACK] DONE — returning {len(linkedin_accounts)} account(s) "
            f"({sum(1 for a in linkedin_accounts if a['type']=='profile')} personal, "
            f"{sum(1 for a in linkedin_accounts if a['type']=='page')} pages)"
        )

        accounts_json = json.dumps(linkedin_accounts)
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', "
            f"platform: 'linkedin', accounts: {accounts_json} }}, "
            f"'{_PM_ORIGIN}'); window.close();</script>"
        ))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[LINKEDIN_CALLBACK] EXCEPTION — {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"LinkedIn callback error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# YouTube / Google OAuth 2.0
# ─────────────────────────────────────────────────────────────────────────────
#
# Phase 1 — Connect + channel discovery.
# All scopes are requested upfront on the consent screen so we never have to
# force the user through a second consent later when video upload / comments /
# analytics features go live. Without this, each phase would force a new
# Google consent → re-auth dance.
#
# Refresh tokens: Google ONLY returns a refresh_token on the FIRST consent for
# a given (client_id, google_account) pair, UNLESS we pass
# `access_type=offline&prompt=consent` — which we do — to force a refresh
# token on every connect. Without that, the second time a user re-connects we
# get an access token that expires in 1 h and no way to refresh it.
#
# Brand channels: a Google account can own multiple YouTube "Brand Account"
# channels. Google's standard consent screen shows a channel chooser BEFORE
# consent is granted; the issued token is scoped to whichever channel the user
# picked. We just call `channels.list?mine=true` after the callback to read
# back which channel they chose.

_YOUTUBE_SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/youtube.upload",       # Phase 2 — video publish
    "https://www.googleapis.com/auth/youtube.force-ssl",    # Phase 3 — comments + replies
    "https://www.googleapis.com/auth/yt-analytics.readonly",  # Phase 4 — analytics
])


@open_oauth_router.get("/auth/youtube")
async def youtube_auth(token: str | None = None, source: str | None = None):
    _validate_token_qs(token)
    """Kick off Google OAuth 2.0 with all YouTube scopes upfront.

    `access_type=offline` + `prompt=consent` ensures Google sends a
    `refresh_token` back on every connect (not just the first one), so we
    can refresh expired access tokens later without forcing the user to
    re-consent.

    `include_granted_scopes=true` lets Google merge any previously granted
    scopes from this client_id + account into the new grant — useful if the
    user re-connects after we add more scopes down the road.
    """
    from urllib.parse import urlencode
    if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="YouTube OAuth is not configured on this server "
                   "(YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing in env).",
        )
    params = {
        "client_id": YOUTUBE_CLIENT_ID,
        "redirect_uri": YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": _YOUTUBE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    logger.info(f"[YOUTUBE_AUTH] redirecting to Google consent ({len(_YOUTUBE_SCOPES.split())} scopes)")
    return RedirectResponse(auth_url)


@open_oauth_router.get("/auth/youtube/callback")
async def youtube_callback(request: Request):
    """Receive Google's auth code, swap it for tokens, fetch the user's
    chosen channel, post the result back to the opener popup.

    The frontend's AccountSelectionModal then shows the channel(s) so the
    user can confirm and click Connect — which POSTs to /connect-accounts
    and creates the SocialAccount row.
    """
    params = dict(request.query_params)
    error = params.get("error")
    code = params.get("code")

    # User declined consent OR Google blocked the app (sensitive scopes,
    # quota, etc). Send an AUTH_ERROR back so the popup closes cleanly.
    if error or not code:
        err_msg = error or "missing_code"
        err_desc = params.get("error_description", "")
        logger.warning(f"[YOUTUBE_CALLBACK] error={err_msg!r} desc={err_desc!r}")
        safe_msg = (err_msg + (": " + err_desc if err_desc else "")).replace("'", "\\'")
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'youtube', error: '{safe_msg}' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    try:
        # ── 1. Exchange code → tokens ──────────────────────────────────
        token_url = "https://oauth2.googleapis.com/token"
        token_resp = requests.post(token_url, data={
            "code": code,
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "redirect_uri": YOUTUBE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=15)
        if token_resp.status_code != 200:
            logger.error(f"[YOUTUBE_CALLBACK] token exchange failed: {token_resp.status_code} {token_resp.text}")
            raise HTTPException(status_code=400, detail=f"Google token exchange failed: {token_resp.text}")

        tok = token_resp.json()
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")  # may be None on re-consent of an already-granted account
        if not access_token:
            raise HTTPException(status_code=400, detail="Google returned no access_token")
        if not refresh_token:
            # We force prompt=consent so this shouldn't happen, but log it
            # — without a refresh token the connection becomes unusable
            # after 1 hour.
            logger.warning("[YOUTUBE_CALLBACK] no refresh_token returned — user may need to revoke + reconnect")

        # ── 2. Fetch the channel the user picked during consent ────────
        # channels.list?mine=true returns ONE channel — whichever the user
        # selected in Google's brand-account chooser before consent.
        ch_resp = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"part": "snippet,statistics,contentDetails", "mine": "true"},
            timeout=15,
        )
        if ch_resp.status_code != 200:
            logger.error(f"[YOUTUBE_CALLBACK] channels.list failed: {ch_resp.status_code} {ch_resp.text}")
            raise HTTPException(status_code=400, detail=f"Failed to fetch YouTube channel: {ch_resp.text}")

        items = (ch_resp.json() or {}).get("items") or []
        if not items:
            # User authenticated via a Google account that has NO YouTube
            # channel (rare — usually means Workspace-only without a YT
            # presence). Surface this as a clear error message.
            return HTMLResponse(content=(
                f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
                f"platform: 'youtube', error: 'No YouTube channel found on this Google account. "
                f"Create a YouTube channel first at https://youtube.com, then try again.' }}, '{_PM_ORIGIN}'); "
                f"window.close();</script>"
            ))

        # Match the minimal payload shape the other connectors send (id, name,
        # type, token, platform — plus refresh_token because Google tokens
        # expire). No channel image, no subscriber count, no extras — those
        # are noise the rest of the app doesn't need, and they made the
        # AccountSelectionModal render a channel avatar that isn't shown for
        # the other platforms.
        youtube_accounts = []
        for item in items:
            snippet = item.get("snippet") or {}
            youtube_accounts.append({
                "id": item.get("id"),
                "name": snippet.get("title") or "YouTube channel",
                "type": "channel",
                "platform": "youtube",
                "token": access_token,
                "refresh_token": refresh_token,
            })

        logger.info(
            f"[YOUTUBE_CALLBACK] fetched {len(youtube_accounts)} channel(s): "
            f"{[c['name'] for c in youtube_accounts]}"
        )

        accounts_json = json.dumps(youtube_accounts)
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', "
            f"platform: 'youtube', accounts: {accounts_json} }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[YOUTUBE_CALLBACK] unexpected error: {e}")
        logger.error(traceback.format_exc())
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'youtube', error: 'Unexpected server error during YouTube connect.' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Pinterest OAuth 2.0
# ─────────────────────────────────────────────────────────────────────────────
#
# Phase 1 — Connect + board discovery + pin publish.
# Pinterest API v5 is the current generation; v1 / v3 / v4 are deprecated.
#
# Auth flow:
#   1. Frontend popup -> /auth/pinterest?token=<jwt>
#   2. Redirect to https://www.pinterest.com/oauth/ with scopes + redirect_uri
#   3. User consents -> Pinterest redirects to /auth/pinterest/callback?code=...
#   4. Exchange code at https://api.pinterest.com/v5/oauth/token (Basic auth)
#   5. Fetch user account at GET /v5/user_account
#   6. Fetch all boards at GET /v5/boards (paginate if >25)
#   7. Send each board back as a separate "account" — matches the Facebook
#      Pages pattern, so users pick which board(s) to save in the
#      AccountSelectionModal. account_id = board_id, token shared across rows.
#
# Tokens: access_token lasts ~30 days, refresh_token lasts ~60 days. We refresh
# lazily — on a 401 from /v5/pins, the publish branch attempts refresh once
# before failing.
_PINTEREST_SCOPES = ",".join([
    "boards:read",
    "boards:write",
    "pins:read",
    "pins:write",
    "user_accounts:read",
])

# Pinterest API base URL — swappable between prod and sandbox.
#   Prod:    https://api.pinterest.com     (default — for Standard-access apps)
#   Sandbox: https://api-sandbox.pinterest.com  (for Trial-access apps testing pins)
# Set PINTEREST_API_BASE env var to override. Once Standard access is granted,
# leave it unset (or set explicitly to prod) — everything routes to prod.
# NOTE: The user-consent URL (www.pinterest.com/oauth/) is the SAME for both
# environments; only the token-exchange + API calls differ.
PINTEREST_API_BASE = os.getenv("PINTEREST_API_BASE", "https://api.pinterest.com").rstrip("/")


@open_oauth_router.get("/auth/pinterest")
async def pinterest_auth(token: str | None = None, source: str | None = None):
    """Kick off Pinterest OAuth 2.0 with all scopes requested upfront."""
    _validate_token_qs(token)
    from urllib.parse import urlencode
    if not PINTEREST_CLIENT_ID or not PINTEREST_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Pinterest OAuth is not configured on this server "
                   "(PINTEREST_CLIENT_ID / PINTEREST_CLIENT_SECRET missing in env).",
        )
    params = {
        "client_id": PINTEREST_CLIENT_ID,
        "redirect_uri": PINTEREST_REDIRECT_URI,
        "response_type": "code",
        "scope": _PINTEREST_SCOPES,
    }
    auth_url = "https://www.pinterest.com/oauth/?" + urlencode(params)
    logger.info(f"[PINTEREST_AUTH] redirecting to Pinterest consent ({len(_PINTEREST_SCOPES.split(','))} scopes)")
    return RedirectResponse(auth_url)


@open_oauth_router.get("/auth/pinterest/callback")
async def pinterest_callback(request: Request):
    """Receive Pinterest's auth code, swap for tokens, fetch user + boards,
    post the result back to the opener popup as one account per board."""
    params = dict(request.query_params)
    error = params.get("error")
    code = params.get("code")

    if error or not code:
        err_msg = error or "missing_code"
        err_desc = params.get("error_description", "")
        logger.warning(f"[PINTEREST_CALLBACK] error={err_msg!r} desc={err_desc!r}")
        safe_msg = (err_msg + (": " + err_desc if err_desc else "")).replace("'", "\\'")
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'pinterest', error: '{safe_msg}' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    try:
        # ── 1. Exchange code → tokens (Basic auth with client_id:secret) ──
        basic = base64.b64encode(
            f"{PINTEREST_CLIENT_ID}:{PINTEREST_CLIENT_SECRET}".encode()
        ).decode()
        logger.info(f"[PINTEREST_CALLBACK] using API base: {PINTEREST_API_BASE}")
        token_resp = requests.post(
            f"{PINTEREST_API_BASE}/v5/oauth/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": PINTEREST_REDIRECT_URI,
            },
            timeout=15,
        )
        if token_resp.status_code != 200:
            logger.error(f"[PINTEREST_CALLBACK] token exchange failed: {token_resp.status_code} {token_resp.text}")
            raise HTTPException(status_code=400, detail=f"Pinterest token exchange failed: {token_resp.text}")

        tok = token_resp.json()
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Pinterest returned no access_token")

        # ── 2. Fetch user account (for the display username) ──────────────
        ua_resp = requests.get(
            f"{PINTEREST_API_BASE}/v5/user_account",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        username = "Pinterest user"
        profile_image = None
        if ua_resp.status_code == 200:
            ua = ua_resp.json() or {}
            username = ua.get("username") or username
            profile_image = ua.get("profile_image")
        else:
            logger.warning(f"[PINTEREST_CALLBACK] user_account fetch failed: {ua_resp.status_code} {ua_resp.text}")

        # ── 3. Fetch boards (paginate up to 100 — most users have <25) ────
        boards = []
        page_size = 25
        bookmark = None
        # Pinterest sandbox has a known quirk: GET /v5/boards without an
        # explicit privacy filter can return an empty list even when the
        # user has boards (user_account.board_count>0 confirms they exist).
        # We fetch PUBLIC + SECRET separately and merge — works on both
        # prod and sandbox, and is safe because Pinterest de-dupes by id.
        for privacy in ("PUBLIC", "SECRET"):
            bookmark = None
            for _ in range(4):  # max 100 boards per privacy tier
                board_params = {"page_size": page_size, "privacy": privacy}
                if bookmark:
                    board_params["bookmark"] = bookmark
                b_resp = requests.get(
                    f"{PINTEREST_API_BASE}/v5/boards",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=board_params,
                    timeout=15,
                )
                if b_resp.status_code != 200:
                    logger.error(f"[PINTEREST_CALLBACK] /v5/boards ({privacy}) failed: {b_resp.status_code} {b_resp.text}")
                    break
                page = b_resp.json() or {}
                boards.extend(page.get("items") or [])
                bookmark = page.get("bookmark")
                if not bookmark:
                    break
        # De-dup by board id (defensive — Pinterest may include a board in
        # both PUBLIC and SECRET results if they change privacy mid-fetch).
        _seen_ids = set()
        _deduped = []
        for _b in boards:
            _bid = _b.get("id")
            if _bid and _bid not in _seen_ids:
                _seen_ids.add(_bid)
                _deduped.append(_b)
        boards = _deduped
        logger.info(f"[PINTEREST_CALLBACK] fetched {len(boards)} boards (base={PINTEREST_API_BASE})")

        if not boards:
            return HTMLResponse(content=(
                f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
                f"platform: 'pinterest', error: 'No Pinterest boards found on this account. "
                f"Create at least one board on pinterest.com, then try again.' }}, '{_PM_ORIGIN}'); "
                f"window.close();</script>"
            ))

        # ── 4. One account row per board (matches Facebook Pages pattern) ──
        # name is JUST the board name — AccountSelectionModal renders it as the
        # title AND derives the subtitle from it (lowercased, spaces stripped,
        # prefixed with @). Stuffing the username into the name produced an
        # ugly "Board (@handle)" title plus a duplicate handle in the subtitle.
        pinterest_accounts = []
        for board in boards:
            pinterest_accounts.append({
                "id": board.get("id"),
                "name": board.get("name") or "Board",
                "type": "board",
                "platform": "pinterest",
                "token": access_token,
                "refresh_token": refresh_token,
                "profile_picture_url": profile_image,
            })

        logger.info(
            f"[PINTEREST_CALLBACK] fetched {len(pinterest_accounts)} board(s) "
            f"for @{username}: {[b['name'] for b in pinterest_accounts]}"
        )

        accounts_json = json.dumps(pinterest_accounts)
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', "
            f"platform: 'pinterest', accounts: {accounts_json} }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PINTEREST_CALLBACK] unexpected error: {e}")
        logger.error(traceback.format_exc())
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'pinterest', error: 'Unexpected server error during Pinterest connect.' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))


# ═══════════════════════════════════════════════════════════════════════
# TikTok OAuth (Phase 1 — connect + fetch user info)
#
# TikTok uses OAuth 2.0 with comma-separated scopes (not space-separated like
# Google/LinkedIn). The authorize URL parameter is `client_key` (not
# `client_id`) — a TikTok-specific quirk worth remembering when copy-pasting
# from other providers' code.
#
# Endpoints used in this flow:
#   - https://www.tiktok.com/v2/auth/authorize/  (consent screen)
#   - https://open.tiktokapis.com/v2/oauth/token/  (code → tokens)
#   - https://open.tiktokapis.com/v2/user/info/  (fetch open_id + display name)
#
# Tokens: access_token expires in 24h. refresh_token lasts 365 days. Pipelyt
# stores both — the analytics + publisher services refresh access_token
# proactively before each API call (same pattern as YouTube).
# ═══════════════════════════════════════════════════════════════════════

_TIKTOK_SCOPES = ",".join([
    "user.info.basic",      # display name + avatar + open_id (Login Kit)
    "user.info.stats",      # follower_count + video_count + likes_count
    "video.upload",         # Phase 2 — upload videos as drafts
    "video.publish",        # Phase 2 — publish drafts as public posts (Direct Post)
    "video.list",           # Phase 3 — read user's videos + per-video stats
    # NOTE: user.info.profile (bio_description + profile_web_link) is NOT
    # in the approved scope list for app awthy77ebjq7dnlp - if you re-request
    # approval and TikTok grants it, add it back. comment.list +
    # comment.create are gated for a follow-up review pass.
])


def _validate_token_qs(token: str | None) -> None:
    """Validate the JWT passed in the OAuth-start popup URL's `?token=` param.

    Why this exists: the OAuth-start endpoints are mounted on `open_oauth_router`
    (no router-level auth gate) because the popup `window.open()` can't carry
    Authorization headers. The frontend appends `?token=<jwt>` to the start
    URL — we just need to confirm the JWT is valid before kicking off the
    consent flow. If it's missing or bad, 401 — same as the gated router.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing OAuth start token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@open_oauth_router.get("/auth/tiktok")
async def tiktok_auth(token: str | None = None, source: str | None = None, db: Session = Depends(get_db)):
    """Kick off TikTok OAuth 2.0 with PKCE and all scopes Pipelyt needs upfront.

    TikTok REQUIRES PKCE (Proof Key for Code Exchange) for all OAuth 2.0
    flows — sandbox enforces it strictly and rejects the authorize call
    without `code_challenge`. We generate a random `code_verifier`, hash
    it with SHA-256, base64url-encode the hash as `code_challenge`, and
    store the verifier in `PendingOAuthSync` keyed by the `state` param
    so the callback can fish it back out and complete the token exchange.

    TikTok consent is shown in the popup; on Allow, TikTok redirects to
    `/auth/tiktok/callback?code=<auth_code>&state=<state>`. The user does
    not pick an account (TikTok always uses the one currently logged in
    on tiktok.com).
    """
    # Popup URL → ?token=<jwt> carries the caller's identity (since the popup
    # window can't carry an Authorization header). Validate it before doing
    # anything stateful (PKCE row insert) so an unauth'd hit doesn't pollute
    # PendingOAuthSync.
    _validate_token_qs(token)

    from urllib.parse import urlencode
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="TikTok OAuth is not configured on this server "
                   "(TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing in env).",
        )

    # ── PKCE ──────────────────────────────────────────────────────────
    # code_verifier — 43-128 char URL-safe random string we KEEP SECRET.
    # code_challenge — base64url(SHA256(code_verifier)) — we SEND to TikTok.
    # At callback time we send the verifier in the token exchange so TikTok
    # can re-hash and confirm it matches the challenge from the auth step.
    code_verifier = secrets.token_urlsafe(64)  # ~85 chars after b64
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")  # strip padding — TikTok rejects '=' here

    # Random state — protects against CSRF AND serves as the DB lookup key
    # so the callback can find the verifier it needs to complete the swap.
    state = secrets.token_urlsafe(24)

    # Persist the verifier. PendingOAuthSync is the same staging table the
    # Twitter PKCE flow uses — `state_or_token` field holds the state,
    # `secret_or_verifier` holds the code_verifier. Garbage-collected on
    # the callback's happy path; orphan rows clean up after ~24h via a
    # housekeeping job (acceptable — verifier alone is useless without
    # the matching auth code and client_secret).
    db.add(PendingOAuthSync(
        state_or_token=f"tiktok:{state}",
        secret_or_verifier=code_verifier,
    ))
    db.commit()

    params = {
        "client_key": TIKTOK_CLIENT_KEY,        # TikTok-specific param name
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "response_type": "code",
        "scope": _TIKTOK_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    logger.info(
        f"[TIKTOK_AUTH] redirecting to TikTok consent "
        f"({len(_TIKTOK_SCOPES.split(','))} scopes, state={state[:8]}… PKCE=S256)"
    )
    return RedirectResponse(auth_url)


@open_oauth_router.get("/auth/tiktok/callback")
async def tiktok_callback(request: Request, db: Session = Depends(get_db)):
    """Receive TikTok's auth code, swap it for access + refresh tokens,
    fetch the user's profile, post the result back to the opener popup.

    The frontend's AccountSelectionModal then shows the single TikTok
    account so the user can confirm and click Connect — which POSTs to
    /connect-accounts and creates the SocialAccount row.
    """
    params = dict(request.query_params)
    error = params.get("error")
    code = params.get("code")
    state = params.get("state")

    # User declined consent OR TikTok blocked the app (e.g. account in a
    # region where the API is unavailable, or sandbox-only access).
    if error or not code:
        err_msg = error or "missing_code"
        err_desc = params.get("error_description", "")
        logger.warning(f"[TIKTOK_CALLBACK] error={err_msg!r} desc={err_desc!r}")
        safe_msg = (err_msg + (": " + err_desc if err_desc else "")).replace("'", "\\'")
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'tiktok', error: '{safe_msg}' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    try:
        # ── 0. Fetch the PKCE code_verifier we stashed in /auth/tiktok ───
        # TikTok requires the verifier to match the code_challenge it
        # received during authorize — without it the token exchange fails
        # with "invalid_grant".
        pending = None
        if state:
            pending = db.query(PendingOAuthSync).filter(
                PendingOAuthSync.state_or_token == f"tiktok:{state}"
            ).first()
        if not pending:
            logger.error(f"[TIKTOK_CALLBACK] no PKCE verifier found for state={state!r}")
            return HTMLResponse(content=(
                f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
                f"platform: 'tiktok', error: 'Session expired during TikTok connect. Please try again.' }}, '{_PM_ORIGIN}'); "
                f"window.close();</script>"
            ))
        code_verifier = pending.secret_or_verifier
        # Delete the staging row immediately — we have what we need, and
        # leaving it around is a tiny CSRF surface (an attacker who steals
        # the verifier still needs the matching auth code + client_secret,
        # but defense-in-depth costs us nothing).
        db.delete(pending)
        db.commit()

        # ── 1. Exchange code → tokens ──────────────────────────────────────
        # TikTok expects form-encoded body (NOT JSON) at the token endpoint.
        # The Content-Type header must be application/x-www-form-urlencoded.
        token_url = "https://open.tiktokapis.com/v2/oauth/token/"
        token_resp = requests.post(
            token_url,
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": TIKTOK_REDIRECT_URI,
                "code_verifier": code_verifier,  # PKCE — must match the challenge from auth step
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if token_resp.status_code != 200:
            logger.error(
                f"[TIKTOK_CALLBACK] token exchange failed: "
                f"{token_resp.status_code} {token_resp.text}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"TikTok token exchange failed: {token_resp.text}",
            )
        tok = token_resp.json() or {}
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")
        open_id = tok.get("open_id")  # stable per-user-per-app identifier
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail=f"TikTok returned no access_token: {tok}",
            )

        # ── 2. Fetch the user's profile so we can show name/avatar in UI ──
        # TikTok requires the fields list as a comma-separated query param
        # AND every field must map to an authorized scope — asking for a
        # field whose scope was not granted causes TikTok to reject the
        # WHOLE request with 'scope_not_authorized'.
        #
        # Field → scope mapping (must match _TIKTOK_SCOPES above):
        #   open_id, union_id, avatar_url, display_name → user.info.basic ✓
        #   follower_count, following_count, likes_count, video_count
        #                                             → user.info.stats  ✓
        #   bio_description, profile_deep_link, is_verified
        #                                             → user.info.profile ✗
        # We do NOT currently request user.info.profile (not yet approved
        # by TikTok for app awthy77ebjq7dnlp), so we DROP those three
        # fields. They aren't used in the account payload below anyway.
        # If/when user.info.profile is approved and added to _TIKTOK_SCOPES,
        # add those fields back here.
        fields = ",".join([
            "open_id", "union_id", "avatar_url", "display_name",
            "follower_count", "following_count", "likes_count", "video_count",
        ])
        info_resp = requests.get(
            f"https://open.tiktokapis.com/v2/user/info/?fields={fields}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if info_resp.status_code != 200:
            logger.error(
                f"[TIKTOK_CALLBACK] user.info fetch failed: "
                f"{info_resp.status_code} {info_resp.text}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch TikTok user info: {info_resp.text}",
            )
        info = (info_resp.json() or {}).get("data", {}).get("user", {}) or {}

        # Match the minimal payload shape the other connectors send so the
        # frontend's AccountSelectionModal doesn't need a TikTok-specific
        # branch. id/name/platform are required; the rest (avatar, refresh
        # token, follower count) are picked up by the connect handler.
        tiktok_account = {
            "id": open_id or info.get("open_id"),
            "name": info.get("display_name") or "TikTok account",
            "type": "creator",
            "platform": "tiktok",
            "token": access_token,
            "refresh_token": refresh_token,
            "profile_picture_url": info.get("avatar_url"),
            "follower_count": int(info.get("follower_count") or 0),
        }

        logger.info(
            f"[TIKTOK_CALLBACK] connected @{info.get('display_name')!r} "
            f"open_id={open_id!r} followers={tiktok_account['follower_count']}"
        )

        accounts_json = json.dumps([tiktok_account])
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', "
            f"platform: 'tiktok', accounts: {accounts_json} }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TIKTOK_CALLBACK] unexpected error: {e}")
        logger.error(traceback.format_exc())
        return HTMLResponse(content=(
            f"<script>window.opener.postMessage({{ type: 'AUTH_ERROR', "
            f"platform: 'tiktok', error: 'Unexpected server error during TikTok connect.' }}, '{_PM_ORIGIN}'); "
            f"window.close();</script>"
        ))



@open_oauth_router.get("/auth/twitter/callback")
async def twitter_callback(request: Request, db: Session = Depends(get_db)):
    """Handles both Twitter OAuth 1.0a and 2.0 PKCE callbacks sequentially.
    Uses database-backed storage to bypass cookie issues.
    """
    params = dict(request.query_params)
    
    # CASE 1: Transitioning from OAuth 1.0a to OAuth 2.0 PKCE
    if "oauth_token" in params and "oauth_verifier" in params:
        try:
            from requests_oauthlib import OAuth1Session
            # Retrieve 1.0a secret from database (BYPASS COOKIES)
            pending = db.query(PendingOAuthSync).filter(PendingOAuthSync.state_or_token == params["oauth_token"]).first()
            if not pending:
                raise HTTPException(status_code=400, detail="Invalid or expired Twitter 1.0a session (Not found in DB)")
                
            resource_owner_secret = pending.secret_or_verifier
            oauth = OAuth1Session(
                TWITTER_API_KEY,
                client_secret=TWITTER_API_SECRET,
                resource_owner_key=params["oauth_token"],
                resource_owner_secret=resource_owner_secret,
                verifier=params["oauth_verifier"]
            )
            access_token_url = "https://api.twitter.com/oauth/access_token"
            tokens = oauth.fetch_access_token(access_token_url)
            
            # Clean up used 1.0a record
            db.delete(pending)
            
            # Prepare OAuth 2.0 PKCE and store verifier in DB
            code_verifier = secrets.token_urlsafe(100)
            code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().replace('=', '')
            state = secrets.token_urlsafe(16)
            scope = "tweet.read tweet.write users.read offline.access"
            
            # Save 1.0a tokens AND 2.0 verifier in DB under the same state (BYPASS COOKIES)
            # We bundle the 1.0a tokens into the secret field as a JSON string
            bundle = json.dumps({
                "access_1_0a": tokens.get("oauth_token"),
                "secret_1_0a": tokens.get("oauth_token_secret"),
                "verifier_2_0": code_verifier
            })
            db.add(PendingOAuthSync(state_or_token=state, secret_or_verifier=bundle))
            db.commit()
            
            import urllib.parse
            v2_params = urllib.parse.urlencode({
                "response_type": "code",
                "client_id": TWITTER_CLIENT_ID,
                "redirect_uri": TWITTER_REDIRECT_URI,
                "scope": scope,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",  # was lowercase s256 — X requires uppercase
            })
            v2_auth_url = f"https://x.com/i/oauth2/authorize?{v2_params}"

            return RedirectResponse(v2_auth_url)
        except Exception as e:
            logger.error(f"Twitter 1.0a callback failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Twitter 1.0a callback error: {str(e)}")

    # CASE 2: Finalizing OAuth 2.0 PKCE
    code = params.get("code")
    state = params.get("state")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state in Twitter callback")
        
    # Retrieve verifier bundle from database (BYPASS COOKIES)
    pending = db.query(PendingOAuthSync).filter(PendingOAuthSync.state_or_token == state).first()
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired Twitter 2.0 session (Not found in DB)")
    
    try:
        bundle = json.loads(pending.secret_or_verifier)
        stored_verifier = bundle.get("verifier_2_0")
        token_1_0a = bundle.get("access_1_0a")
        secret_1_0a = bundle.get("secret_1_0a")
        
        token_url = "https://api.twitter.com/2/oauth2/token"
        auth_str = f"{TWITTER_CLIENT_ID}:{TWITTER_CLIENT_SECRET}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()
        
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": TWITTER_REDIRECT_URI,
            "code_verifier": stored_verifier
        }
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        res = requests.post(token_url, data=data, headers=headers)
        if res.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to get OAuth 2.0 tokens: {res.text}")
            
        token_data = res.json()
        access_token_v2 = token_data.get("access_token")
        refresh_token_v2 = token_data.get("refresh_token")
        
        # Get profile info using v2 token
        profile_res = requests.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token_v2}"},
            params={"user.fields": "profile_image_url,name"}
        ).json()
        
        user_data = profile_res.get("data", {})
        
        twitter_account = { 
            "id": user_data.get("id"), 
            "name": user_data.get("name"), 
            "profile_picture_url": user_data.get("profile_image_url"), 
            "type": "profile", 
            "platform": "twitter", 
            "token": token_1_0a or access_token_v2,
            "token_secret": secret_1_0a,
            "refresh_token": refresh_token_v2 
        }
        
        # Clean up database
        db.delete(pending)
        db.commit()
        
        accounts_json = json.dumps([twitter_account])
        return HTMLResponse(content=f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', platform: 'twitter', accounts: {accounts_json} }}, '{_PM_ORIGIN}'); window.close();</script>")
    except Exception as e:
        logger.error(f"Twitter callback finalization failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Twitter callback error: {str(e)}")

@open_oauth_router.get("/auth/facebook")
async def facebook_auth(request: Request, source: str = "facebook", token: str | None = None):
    _validate_token_qs(token)
    # =========================================================================
    # Meta OAuth flow selector — April 2026
    #
    # Meta has migrated Business-type apps to **Facebook Login for Business**
    # which uses `config_id=` instead of `scope=`. Consumer-type apps still
    # use the classic `scope=` flow. Business apps calling the classic flow
    # get redirected to `/dialog/oauth/business/cancel/` with the user-facing
    # "It looks like this app isn't available" error.
    #
    # How to know which flow you need:
    #   • Meta Developers → App Settings → Basic → "App type" field
    #   • If "Business" → YOU MUST use FACEBOOK_CONFIG_ID (create a Login for
    #     Business Configuration in the dashboard and paste the ID here)
    #   • If "Consumer" → FACEBOOK_CONFIG_ID can be empty; scope= flow used
    #
    # Env vars:
    #   FACEBOOK_CONFIG_ID    — Business app: set this. Consumer app: leave unset.
    #   FACEBOOK_SCOPE_LEVEL  — Consumer-flow only (minimal/pages/full)
    #   FACEBOOK_FULL_SCOPES  — legacy alias for FACEBOOK_SCOPE_LEVEL=full
    # =========================================================================
    config_id = (os.environ.get("FACEBOOK_CONFIG_ID", "") or "").strip()

    # Scope logic is used ONLY when config_id is not set (i.e. Consumer apps).
    MINIMAL_SCOPES = "public_profile"
    PAGES_SCOPES = "pages_show_list,pages_read_engagement"
    FULL_EXTRA_SCOPES = "instagram_basic,instagram_content_publish,business_management"
    level = (os.environ.get("FACEBOOK_SCOPE_LEVEL", "") or "full").lower()
    if os.environ.get("FACEBOOK_FULL_SCOPES", "").lower() in ("1", "true", "yes", "on"):
        level = "full"
    if level == "full":
        scope = f"{MINIMAL_SCOPES},{PAGES_SCOPES},{FULL_EXTRA_SCOPES}"
    elif level == "pages":
        scope = f"{MINIMAL_SCOPES},{PAGES_SCOPES}"
    else:
        scope = MINIMAL_SCOPES
    # Build the OAuth URL based on flow type
    app_id_preview = (FACEBOOK_APP_ID[:6] + "…" + FACEBOOK_APP_ID[-4:]) if FACEBOOK_APP_ID else "(MISSING)"
    app_secret_set = bool(FACEBOOK_APP_SECRET and len(FACEBOOK_APP_SECRET) > 10)

    if config_id:
        # Facebook Login for Business flow (Business-type apps).
        # Permissions are defined in the Meta dashboard's Configuration — NOT
        # passed as scope here. All scopes your app needs must be pre-selected
        # in the Login for Business Configuration before it'll work.
        flow_type = "login_for_business"
        auth_url = (
            f"https://www.facebook.com/{FB_GRAPH_VERSION}/dialog/oauth?"
            f"client_id={FACEBOOK_APP_ID}&"
            f"redirect_uri={FACEBOOK_REDIRECT_URI}&"
            f"state={source}&"
            f"config_id={config_id}&"
            f"response_type=code"
        )
        logger.info(
            f"[FB_AUTH] flow=LOGIN_FOR_BUSINESS (Business app) — "
            f"source={source!r} client_id={app_id_preview} app_secret_set={app_secret_set} "
            f"redirect_uri={FACEBOOK_REDIRECT_URI!r} config_id={config_id!r} "
            f"request_origin={request.headers.get('origin')!r}"
        )
    else:
        # Classic Facebook Login flow (Consumer-type apps only — Business apps
        # using this flow will be redirected to /oauth/business/cancel/).
        flow_type = "classic_scope"
        auth_url = (
            f"https://www.facebook.com/{FB_GRAPH_VERSION}/dialog/oauth?"
            f"client_id={FACEBOOK_APP_ID}&"
            f"redirect_uri={FACEBOOK_REDIRECT_URI}&"
            f"state={source}&"
            f"scope={scope}&"
            f"response_type=code"
        )
        logger.info(
            f"[FB_AUTH] flow=CLASSIC_SCOPE (Consumer app / no config_id set) — "
            f"source={source!r} client_id={app_id_preview} app_secret_set={app_secret_set} "
            f"redirect_uri={FACEBOOK_REDIRECT_URI!r} scope_level={level or 'minimal'} "
            f"scope={scope!r} request_origin={request.headers.get('origin')!r}"
        )
        # Helpful warning: if the app is Business-type (detected by config errors),
        # this flow will fail at Meta. We can't detect it from here but we can warn
        # in the log if FACEBOOK_CONFIG_ID looks like it should be set.

    logger.info(f"[FB_AUTH] redirecting to: {auth_url[:250]}...")
    return RedirectResponse(auth_url)

@open_oauth_router.get("/auth/facebook/callback")
@router.post("/auth/facebook/callback")  # Meta business flow sometimes uses POST
async def facebook_callback(
    request: Request,
    code: str = None,
    state: str = "facebook",
    error: str = None,
    error_code: str = None,
    error_description: str = None,
    error_reason: str = None,
):
    """Facebook OAuth callback.

    Previously required `code` as mandatory. When Meta returned an error
    (user declined, app in dev mode + unauthorized user, unapproved scopes),
    FastAPI responded 422 Unprocessable Entity and the user saw a blank
    error page → frontend showed generic "cancelled or failed". Now we
    accept Meta's `error*` params and surface them to the frontend via
    postMessage so you can see the actual Meta error in browser devtools.
    """
    # ⚠️ DEBUG CANARY — print() writes directly to Lambda stdout which ALWAYS
    # ends up in CloudWatch, regardless of Python logging config. If we DON'T
    # see this line in CloudWatch after a Facebook OAuth attempt, the callback
    # is NOT hitting this Lambda (FACEBOOK_REDIRECT_URI is misconfigured).
    print(f"### FB_CALLBACK_CANARY hit at {request.url} method={request.method}", flush=True)
    try:
        # Full diagnostic dump — all query + header info
        logger.info(
            f"[FB_CALLBACK] ENTERED — method={request.method} "
            f"url={str(request.url)[:300]} "
            f"origin={request.headers.get('origin')!r} "
            f"referer={request.headers.get('referer')!r} "
            f"user_agent={request.headers.get('user-agent', '')[:80]!r}"
        )
        logger.info(
            f"[FB_CALLBACK] query params — "
            f"code={'present:' + (code[:12] + '...' if code else 'MISSING')} "
            f"state={state!r} "
            f"error={error!r} "
            f"error_code={error_code!r} "
            f"error_reason={error_reason!r} "
            f"error_description={error_description!r}"
        )

        # For POST callbacks (Meta business flow), `code` may arrive in the
        # form body, not query string. FastAPI binds query by default — also
        # try the body if code is missing.
        if not code and request.method == "POST":
            try:
                form = await request.form()
                code = form.get("code") or code
                state = form.get("state") or state
                error = form.get("error") or error
                error_description = form.get("error_description") or error_description
                logger.info(
                    f"[FB_CALLBACK] POST body parsed — "
                    f"code={'present' if code else 'missing'} "
                    f"state={state!r} "
                    f"error={error!r} "
                    f"form_keys={list(form.keys())}"
                )
            except Exception as form_err:
                logger.warning(f"[FB_CALLBACK] could not parse POST form body: {form_err}")

        # --- Meta returned an error (no `code`) ---
        if error or not code:
            err_msg = (
                error_description
                or error_reason
                or error
                or f"Facebook did not return an authorization code (state={state})"
            )
            logger.warning(
                f"Facebook OAuth rejected by Meta: error={error!r} code={error_code!r} "
                f"reason={error_reason!r} desc={error_description!r} state={state!r}"
            )
            # Send a visible error back to the popup opener so the UI can show it.
            safe_msg = json.dumps(err_msg)
            return HTMLResponse(
                content=(
                    f"<script>"
                    f"if (window.opener) {{ window.opener.postMessage({{ "
                    f"type: 'AUTH_ERROR', platform: '{state}', "
                    f"error: {safe_msg} }}, '{_PM_ORIGIN}'); }}"
                    f"document.body.innerText = 'Facebook OAuth error: ' + {safe_msg};"
                    f"setTimeout(() => window.close(), 3000);"
                    f"</script>"
                )
            )

        logger.info(f"[FB_CALLBACK] exchanging code for token (platform={state})")
        token_res = requests.get(
            f"https://graph.facebook.com/{FB_GRAPH_VERSION}/oauth/access_token",
            params={"client_id": FACEBOOK_APP_ID, "client_secret": FACEBOOK_APP_SECRET, "redirect_uri": FACEBOOK_REDIRECT_URI, "code": code},
            timeout=20,
        ).json()
        logger.info(f"[FB_CALLBACK] token exchange response keys: {list(token_res.keys())}")

        # Fetch the permissions actually granted to this token — critical for
        # diagnosing why posting fails (e.g. `pages_manage_posts` missing).
        try:
            _token = token_res.get("access_token")
            if _token:
                perm_res = requests.get(
                    f"https://graph.facebook.com/{FB_GRAPH_VERSION}/me/permissions",
                    params={"access_token": _token},
                    timeout=10,
                ).json()
                granted = [p["permission"] for p in perm_res.get("data", []) if p.get("status") == "granted"]
                declined = [p["permission"] for p in perm_res.get("data", []) if p.get("status") == "declined"]
                logger.info(f"[FB_CALLBACK] token permissions — granted={granted} declined={declined}")
                # Warn if critical posting perms are missing
                missing_critical = []
                for perm in ("pages_manage_posts", "instagram_content_publish"):
                    if perm not in granted:
                        missing_critical.append(perm)
                if missing_critical:
                    logger.warning(
                        f"[FB_CALLBACK] ⚠️ MISSING critical posting permissions: {missing_critical}. "
                        f"Add these to your Meta Login-for-Business Configuration and reconnect."
                    )
                # Warn if comment management perms are missing — these power the
                # Analytics → Post Detail → Comments panel.
                missing_comment_perms = []
                for perm in ("pages_read_engagement", "pages_manage_engagement",
                             "instagram_manage_comments"):
                    if perm not in granted:
                        missing_comment_perms.append(perm)
                if missing_comment_perms:
                    _cfg_id = (os.environ.get("FACEBOOK_CONFIG_ID", "") or "").strip() or "(unset)"
                    logger.warning(
                        f"[FB_CALLBACK] ⚠️ MISSING comment management permissions: {missing_comment_perms}. "
                        f"Comment fetch/reply in the Analytics modal will fail until these are added "
                        f"to the Login-for-Business Configuration (config_id={_cfg_id!r}) and the user reconnects."
                    )
        except Exception as perm_err:
            logger.warning(f"[FB_CALLBACK] could not fetch token permissions: {perm_err}")
        if "error" in token_res:
            err_detail = token_res.get("error", {}).get("message", "Token exchange failed")
            logger.error(f"Facebook token exchange failed: {token_res}")
            safe_msg = json.dumps(f"Token exchange failed: {err_detail}")
            return HTMLResponse(
                content=(
                    f"<script>"
                    f"if (window.opener) {{ window.opener.postMessage({{ "
                    f"type: 'AUTH_ERROR', platform: '{state}', "
                    f"error: {safe_msg} }}, '{_PM_ORIGIN}'); }}"
                    f"document.body.innerText = 'Facebook token exchange error: ' + {safe_msg};"
                    f"setTimeout(() => window.close(), 3000);"
                    f"</script>"
                )
            )
        token = token_res.get("access_token")

        # --- Fetch Pages via TWO paths and merge ---
        # Path A: /me/accounts — returns Pages the user directly manages via
        #         their Personal Timeline (classic Consumer flow).
        # Path B: /me/businesses → /{biz}/owned_pages — returns Pages sitting
        #         inside a Business Portfolio. Meta's Login-for-Business flow
        #         puts Pages here, so Path A alone returns 0 for anyone whose
        #         Pages live in a Business Manager (which is most business
        #         accounts today).
        # Merge + dedupe by page id so both flows work seamlessly.
        pages_by_id = {}
        try:
            path_a = requests.get(
                f"https://graph.facebook.com/{FB_GRAPH_VERSION}/me/accounts",
                params={"fields": "id,name,access_token,picture", "access_token": token},
                timeout=15,
            ).json()
            for p in (path_a.get("data") or []):
                if p.get("id"):
                    pages_by_id[p["id"]] = p
            path_a_err = path_a.get("error")
            if path_a_err:
                logger.warning(f"[FB_CALLBACK] /me/accounts error: {path_a_err}")
        except Exception as _err_a:
            logger.warning(f"[FB_CALLBACK] /me/accounts fetch failed: {_err_a}")

        try:
            biz_list = requests.get(
                f"https://graph.facebook.com/{FB_GRAPH_VERSION}/me/businesses",
                params={"fields": "id,name", "access_token": token},
                timeout=15,
            ).json()
            biz_err = biz_list.get("error")
            if biz_err:
                logger.warning(f"[FB_CALLBACK] /me/businesses error: {biz_err}")
            for biz in (biz_list.get("data") or []):
                bid = biz.get("id")
                if not bid:
                    continue
                # Pages the business OWNS
                owned = requests.get(
                    f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{bid}/owned_pages",
                    params={"fields": "id,name,access_token,picture", "access_token": token},
                    timeout=15,
                ).json()
                for p in (owned.get("data") or []):
                    if p.get("id"):
                        pages_by_id.setdefault(p["id"], p)
                # Pages the business has been GRANTED client access to
                client = requests.get(
                    f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{bid}/client_pages",
                    params={"fields": "id,name,access_token,picture", "access_token": token},
                    timeout=15,
                ).json()
                for p in (client.get("data") or []):
                    if p.get("id"):
                        pages_by_id.setdefault(p["id"], p)
        except Exception as _err_b:
            logger.warning(f"[FB_CALLBACK] Business Portfolio pages fetch failed: {_err_b}")

        pages = list(pages_by_id.values())
        me_res = requests.get(
            f"https://graph.facebook.com/{FB_GRAPH_VERSION}/me",
            params={"fields": "id,name,picture", "access_token": token},
            timeout=15,
        ).json()

        fb_accounts = [{"id": "me", "name": "Personal Timeline", "type": "profile", "token": token, "platform": "facebook", "profile_picture_url": me_res.get("picture", {}).get("data", {}).get("url", "")}] + \
                      [{"id": p["id"], "name": p["name"], "type": "page", "token": p.get("access_token") or token, "platform": "facebook", "profile_picture_url": p.get("picture", {}).get("data", {}).get("url", "")} for p in pages]

        ig_accounts = []
        for p in pages:
            page_tok = p.get("access_token") or token
            ig_data = requests.get(
                f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{p['id']}",
                params={"fields": "instagram_business_account", "access_token": page_tok},
                timeout=15,
            ).json().get("instagram_business_account")
            if ig_data:
                ig_detail = requests.get(
                    f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{ig_data['id']}",
                    params={"fields": "id,name,username,profile_picture_url", "access_token": page_tok},
                    timeout=15,
                ).json()
                ig_accounts.append({"id": ig_data["id"], "name": ig_detail.get("name") or ig_detail.get("username"), "type": "business", "token": token, "platform": "instagram", "profile_picture_url": ig_detail.get("profile_picture_url", "")})

        logger.info(
            f"[FB_CALLBACK] SUCCESS — posting AUTH_SUCCESS to popup opener — "
            f"target_origin={_PM_ORIGIN!r} "
            f"fb_accounts={len(fb_accounts)} "
            f"ig_accounts={len(ig_accounts)} "
            f"pages_total={len(pages)}"
        )
        return HTMLResponse(content=f"<script>window.opener.postMessage({{ type: 'AUTH_SUCCESS', platform: '{state}', fb_accounts: {json.dumps(fb_accounts)}, ig_accounts: {json.dumps(ig_accounts)} }}, '{_PM_ORIGIN}'); window.close();</script>")
    except Exception as e:
        logger.error(f"Facebook/Instagram callback failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Callback error: {str(e)}")

@router.get("/connections")
async def get_connections(
    member_user_ids: str = None,
    # Brand-filter params (same set as /posts/published). Admin-only;
    # when present, narrows the returned accounts to those owned by /
    # assigned to members who match the filter.
    dna_product_ids: str = None,
    filter_company: str = None,
    filter_country: str = None,
    filter_state: str = None,
    filter_city: str = None,
    filter_pin_code: str = None,
    user_type: str = None,   # 'team' | 'franchise' | None (both)
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the social accounts visible to the caller.

    Admin: their own connections (whether or not assigned to a team member),
           with `assigned_to` populated so the admin can see who's using it.
           If `?member_user_ids=` is passed (admin-only), the result is
           narrowed to accounts where user_id IS one of those members OR
           assigned_to_user_id IS one of those members.
    Member: only accounts they own + admin-owned accounts assigned to them.
    """
    from services.team_service import is_team_member, get_plan_owner
    from sqlalchemy import or_

    if is_team_member(current_user):
        # Member view: own accounts + admin-owned accounts assigned to them.
        admin = get_plan_owner(db, current_user)
        accounts = (
            db.query(SocialAccount)
            .filter(
                or_(
                    SocialAccount.user_id == current_user.id,
                    (SocialAccount.user_id == admin.id) & (SocialAccount.assigned_to_user_id == current_user.id),
                )
            )
            .all()
        )
    else:
        # Admin view. Use shared resolver for brand / location / member
        # filters so /connections stays in lockstep with /analytics/summary
        # and the other dashboard endpoints.
        from services.team_service import resolve_filtered_visible_ids
        resolved = set(resolve_filtered_visible_ids(
            db, current_user,
            member_user_ids=member_user_ids, dna_product_ids=dna_product_ids,
            filter_company=filter_company, filter_country=filter_country,
            filter_state=filter_state, filter_city=filter_city,
            filter_pin_code=filter_pin_code,
            user_type=user_type,
        ))
        has_filter = any([
            member_user_ids, dna_product_ids,
            filter_company, filter_country, filter_state, filter_city, filter_pin_code,
        ])
        # Admin's OWN id sent as the sentinel → fall through to "admin only"
        # (the resolver already returned {admin.id}); treat as unfiltered
        # admin view so they see their own connections.
        admin_only_sentinel = (resolved == {int(current_user.id)})
        target_ids = None if (not has_filter or admin_only_sentinel) else (resolved or None)
        if target_ids:
            # Exclude admin — show only accounts owned by or assigned to the
            # selected members.
            accounts = (
                db.query(SocialAccount)
                .filter(
                    or_(
                        SocialAccount.user_id.in_(target_ids),
                        SocialAccount.assigned_to_user_id.in_(target_ids),
                    )
                )
                .all()
            )
        else:
            accounts = (
                db.query(SocialAccount)
                .filter(SocialAccount.user_id == current_user.id)
                .all()
            )

    # Build a member-id → email map for admin's assignment display.
    assigned_user_ids = {a.assigned_to_user_id for a in accounts if a.assigned_to_user_id}
    user_map = {}
    if assigned_user_ids:
        user_map = {
            u.id: {"id": u.id, "email": u.email, "full_name": u.full_name}
            for u in db.query(User).filter(User.id.in_(assigned_user_ids)).all()
        }

    # Any platform key the frontend reads MUST be in this dict or its accounts
    # get silently dropped by the `if acc.platform in result` guard below.
    # Added "youtube" alongside the original 5 because Phase 1 of YouTube is
    # active. Added "pinterest" once the Pinterest OAuth + publish flow shipped.
    result = {"linkedin": [], "facebook": [], "instagram": [], "twitter": [], "reddit": [], "youtube": [], "tiktok": [], "pinterest": []}
    for acc in accounts:
        if acc.platform in result:
            # owner_kind is the caller's relationship to this account:
            #   'self'  — the caller directly owns the row (they connected it)
            #   'admin' — a team member sees an account their admin assigned
            owner_kind = "self" if acc.user_id == current_user.id else "admin"
            result[acc.platform].append({
                "id": acc.account_id,
                "db_id": acc.id,
                "name": acc.name,
                "type": acc.type,
                "profile_picture_url": acc.profile_picture_url,
                "platform": acc.platform,
                "assigned_to_user_id": acc.assigned_to_user_id,
                "assigned_to": user_map.get(acc.assigned_to_user_id) if acc.assigned_to_user_id else None,
                "owner_kind": owner_kind,
            })
    return result

@router.post("/connect-accounts")
async def connect_accounts(request: Request, background_tasks: BackgroundTasks, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = await request.json()
    platform, accounts = body.get("platform"), body.get("accounts", [])
    
    # 1. Quota Check
    # We count how many NEW accounts are being added vs existing ones being refreshed
    new_accounts_count = 0
    for acc in accounts:
        existing = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == platform,
            SocialAccount.account_id == acc["id"]
        ).first()
        if not existing:
            new_accounts_count += 1
            
    if new_accounts_count > 0:
        check_quota(db, current_user, "channels", new_accounts_count)

    db_account_ids = []
    for acc in accounts:
        # SEARCH for existing account by user + platform + account_id
        existing = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == platform,
            SocialAccount.account_id == acc["id"]
        ).first()
        
        if existing:
            # UPDATE existing tokens/info — PRESERVE baseline (initial_follower_count,
            # initial_connected_at). This is the reconnect case: user already had
            # this account, just refreshing OAuth token. Baseline stays.
            existing.token = acc["token"]
            existing.token_secret = acc.get("token_secret")
            existing.refresh_token = acc.get("refresh_token")
            existing.name = acc["name"]
            existing.profile_picture_url = acc.get("profile_picture_url")
            existing.type = acc["type"]
            # Defensive: if an old row has no baseline (pre-migration), seed it now
            if not existing.initial_follower_count and existing.follower_count:
                existing.initial_follower_count = existing.follower_count
                existing.initial_connected_at = datetime.utcnow()
            db_account_ids.append(existing.id)
        else:
            # ADD new account. Baseline follower count will be set by the
            # initial analytics sync (below — connect-accounts triggers it),
            # so we stamp initial_connected_at now and let the sync fill
            # initial_follower_count on first-run.
            new_acc = SocialAccount(
                platform=platform,
                account_id=acc["id"],
                name=acc["name"],
                type=acc["type"],
                token=acc["token"],
                token_secret=acc.get("token_secret"),
                refresh_token=acc.get("refresh_token"),
                profile_picture_url=acc.get("profile_picture_url"),
                initial_connected_at=datetime.utcnow(),
                user_id=current_user.id,
            )
            db.add(new_acc)
            db.flush()  # Get the ID before commit
            db_account_ids.append(new_acc.id)

    # Auto-fulfill any pending reconnect requests for the accounts just
    # updated. If an admin runs the OAuth flow for an account a member
    # has a pending request on, we flip those requests to 'fulfilled'.
    if db_account_ids:
        now_ts = datetime.utcnow()
        (
            db.query(ReconnectRequest)
            .filter(
                ReconnectRequest.social_account_id.in_(db_account_ids),
                ReconnectRequest.status == "pending",
            )
            .update(
                {"status": "fulfilled", "fulfilled_at": now_ts},
                synchronize_session=False,
            )
        )

    db.commit()
    
    # Trigger background sync for the newly connected accounts using INTERNAL IDs
    try:
        if db_account_ids:
            background_tasks.add_task(sync_all_user_accounts, current_user.id, db_account_ids)
    except Exception as e:
        logger.error(f"Failed to queue initial sync: {e}")
        
    return {"status": "success"}

@router.delete("/disconnect/{platform}")
async def disconnect_platform(platform: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(SocialAccount).filter(SocialAccount.user_id == current_user.id, SocialAccount.platform == platform).delete()
    db.commit()
    return {"status": "success"}

@router.delete("/disconnect/{platform}/{account_id}")
async def disconnect_account(platform: str, account_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(SocialAccount).filter(SocialAccount.user_id == current_user.id, SocialAccount.platform == platform, SocialAccount.account_id == account_id).delete()
    db.commit()
    return {"status": "success"}


# =============================================================================
# Team-aware connection management
#   — admin assigns one of their connected accounts to a team member
#   — member requests the admin to reconnect a stale account
# =============================================================================

from pydantic import BaseModel as _BaseModel


class _AssignBody(_BaseModel):
    user_id: Optional[int] = None  # None → unassign


class _ReconnectRequestBody(_BaseModel):
    reason: Optional[str] = None


@router.put("/connections/{account_db_id}/assign")
async def assign_connection(
    account_db_id: int,
    body: _AssignBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only. Assign a connection to one of their team members, or
    pass `user_id=null` to unassign. Token/row stay on the admin."""
    from services.team_service import is_team_member
    if is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Team members cannot assign connections.")

    acc = db.query(SocialAccount).filter(
        SocialAccount.id == account_db_id,
        SocialAccount.user_id == current_user.id,
    ).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Connection not found.")

    if body.user_id is not None:
        # Confirm target is an active team member of this admin.
        member = db.query(User).filter(
            User.id == body.user_id,
            User.team_owner_id == current_user.id,
            User.disabled.is_(False),
        ).first()
        if not member:
            raise HTTPException(status_code=400, detail="That user is not an active team member.")
        acc.assigned_to_user_id = member.id
    else:
        acc.assigned_to_user_id = None

    db.commit()
    return {"ok": True, "assigned_to_user_id": acc.assigned_to_user_id}


@router.post("/connections/{account_db_id}/reconnect-request")
async def create_reconnect_request(
    account_db_id: int,
    body: _ReconnectRequestBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Member-only. Ask the admin to refresh this account's token."""
    from services.team_service import is_team_member, get_plan_owner
    if not is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Only team members can request reconnects.")

    admin = get_plan_owner(db, current_user)
    acc = db.query(SocialAccount).filter(
        SocialAccount.id == account_db_id,
        SocialAccount.user_id == admin.id,
        SocialAccount.assigned_to_user_id == current_user.id,
    ).first()
    if not acc:
        raise HTTPException(status_code=404, detail="That connection is not assigned to you.")

    # Dedupe: if there's already a pending request for this account by this user,
    # just return it instead of creating duplicates.
    existing = db.query(ReconnectRequest).filter(
        ReconnectRequest.social_account_id == acc.id,
        ReconnectRequest.requester_user_id == current_user.id,
        ReconnectRequest.status == "pending",
    ).first()
    if existing:
        return {"ok": True, "id": existing.id, "status": existing.status, "deduped": True}

    req = ReconnectRequest(
        social_account_id=acc.id,
        requester_user_id=current_user.id,
        reason=body.reason,
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    # Notify admin via email (best-effort; failure doesn't block the request).
    try:
        from services.email_service import email_service
        email_service.send_reconnect_request_email(
            admin_email=admin.email,
            member_name=current_user.full_name or current_user.email,
            platform=acc.platform,
            account_name=acc.name,
            reason=body.reason or "",
        )
    except Exception as email_err:
        logger.warning(f"Reconnect-request email failed: {email_err}")

    return {"ok": True, "id": req.id, "status": req.status}


@router.post("/connections/reconnect-requests/{request_id}/cancel")
async def cancel_reconnect_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = db.query(ReconnectRequest).filter(
        ReconnectRequest.id == request_id,
        ReconnectRequest.requester_user_id == current_user.id,
        ReconnectRequest.status == "pending",
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found or already resolved.")
    req.status = "cancelled"
    db.commit()
    return {"ok": True}


@router.get("/connections/reconnect-requests")
async def list_reconnect_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only. List pending reconnect requests across all their team's
    assigned accounts."""
    from services.team_service import is_team_member
    if is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Members cannot list reconnect requests.")

    # Join ReconnectRequest → SocialAccount (owned by admin) → requester User
    rows = (
        db.query(ReconnectRequest, SocialAccount, User)
        .join(SocialAccount, ReconnectRequest.social_account_id == SocialAccount.id)
        .join(User, ReconnectRequest.requester_user_id == User.id)
        .filter(
            SocialAccount.user_id == current_user.id,
            ReconnectRequest.status == "pending",
        )
        .order_by(ReconnectRequest.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "social_account_db_id": acc.id,
            "platform": acc.platform,
            "account_name": acc.name,
            "requester": {"id": u.id, "email": u.email, "full_name": u.full_name},
            "reason": r.reason,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for (r, acc, u) in rows
    ]
