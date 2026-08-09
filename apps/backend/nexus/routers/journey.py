"""Nexus GTM Journey router — full port of the legacy
`apps/nexus-legacy/server/routes/journey.js` Express routes.

Field names and response shapes are kept 1:1 with the legacy contract so
the Mongo->Postgres data migration can copy values unchanged. The only
divergence is identifiers: Postgres BIGINT instead of Mongo ObjectId
(`_id` keys still appear in the JSON for frontend compatibility).

Endpoints (all under /nexus/journey):
  GET  /summary                        — bucket counts (active / hidden / total / eligible_auto_hide)
  GET  /leads?view=active|hidden       — filtered lead list with channel attempt counters
  GET  /leads/{lead_id}/detail         — lead + campaigns + sequences + timeline (7 event types)
  POST /leads/{lead_id}/hide           — set priority_state='hidden'
  POST /leads/{lead_id}/unhide         — set priority_state='active'
  POST /leads/{lead_id}/attempt        — increment attempt counters; auto-hide after 3 attempts
  POST /actions/auto-hide-stale        — bulk auto-hide all stale leads (>= min_attempts)
  GET  /leads/{lead_id}/demo           — demo booking + briefing for demo_scheduled leads
  POST /leads/{lead_id}/demo/regenerate — async re-trigger briefing generation
  GET  /{lead_id}                      — legacy minimal timeline (kept for back-compat)
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_user_id,
    extract_workspace_id,
    get_nexus_user,
    require_current_workspace,
)
from nexus.deps import block_user_writes
from nexus.models_phase6 import DemoBooking, Outreach, VoiceCall
from nexus.schemas_phase6 import JourneyEvent, JourneyOut

logger = logging.getLogger("pipelyt.nexus.journey")

# Router-level: read-only Users blocked from writes (reconcile-outreach, hide/
# unhide, attempt, auto-hide); the journey table + detail GETs stay open.
router = APIRouter(
    prefix="/nexus/journey",
    tags=["nexus-journey"],
    dependencies=[Depends(block_user_writes)],
)

# ─── In-process TTL cache for read-heavy GTM endpoints ───────────────────────
# /filters/schema, /summary, and _owned_campaign_ids run identical aggregations
# on every GTM interaction. A short TTL cache collapses bursts of UI clicks
# into a single backend pass without introducing visible staleness — writes
# (hide / unhide / attempt) call gtm_cache_invalidate(workspace_id) to drop
# the cached entries for that workspace.
_GTM_CACHE: Dict[Tuple, Tuple[float, Any]] = {}
_GTM_CACHE_TTL_SECS = 15.0
_GTM_CACHE_MAX_ENTRIES = 256


def _gtm_cache_get(key: Tuple) -> Optional[Any]:
    entry = _GTM_CACHE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() > expires_at:
        _GTM_CACHE.pop(key, None)
        return None
    return value


def _gtm_cache_set(key: Tuple, value: Any, ttl: float = _GTM_CACHE_TTL_SECS) -> None:
    if len(_GTM_CACHE) >= _GTM_CACHE_MAX_ENTRIES:
        try:
            _GTM_CACHE.pop(next(iter(_GTM_CACHE)), None)
        except StopIteration:
            pass
    _GTM_CACHE[key] = (time.monotonic() + ttl, value)


def gtm_cache_invalidate(workspace_id: Optional[int] = None) -> None:
    if workspace_id is None:
        _GTM_CACHE.clear()
        return
    for k in [k for k in _GTM_CACHE if workspace_id in k]:
        _GTM_CACHE.pop(k, None)



# ─── Helpers (ports of legacy helper functions) ──────────────────────────────


_TERMINAL_STATUSES = {"replied", "demo_scheduled", "bounced", "unsubscribed"}


def _status_rank(status: Optional[str]) -> int:
    """Port of legacy statusRank()."""
    if status == "demo_scheduled":
        return 4
    if status == "replied":
        return 3
    if status == "contacted":
        return 2
    if status == "new":
        return 1
    return 0


def _decode_html_entities(s: str) -> str:
    """Port of legacy decodeHtmlEntities()."""
    if not s:
        return ""
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"&#x([0-9a-f]+);", lambda m: chr(int(m.group(1), 16)), s, flags=re.IGNORECASE)
    return (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def _clean_email_content(raw: Optional[str]) -> str:
    """Port of legacy cleanEmailContent() — strip HTML/CSS/JS, keep the
    conversational body when greeting/sign-off can be detected."""
    html = str(raw or "")
    if not html.strip():
        return ""

    s = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<head[\s\S]*?</head>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<noscript[\s\S]*?</noscript>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<svg[\s\S]*?</svg>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<(br|\/p|\/div|\/li|\/tr|\/h\d)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)

    s = _decode_html_entities(s)
    s = s.replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    lower = s.lower()
    hi_match = re.search(r"\b(hi|hello)\b[^,\n]{0,40},?", lower)
    if hi_match:
        start = hi_match.start()
        tail = lower[start:]
        sign_match = re.search(r"\n\s*(best|regards|thanks|cheers)[,\s]", tail)
        if sign_match and sign_match.start() > 0:
            end = start + sign_match.start() + 120
            return s[start:min(end, len(s))].strip()
        return s[start:min(start + 1800, len(s))].strip()

    return s[:1800].strip()


def _normalize_last_channel(counts: Dict[str, int], stored: Optional[str]) -> Optional[str]:
    """Port of legacy normalizeLastChannel(). Returns the dominant
    channel name when exactly one is non-zero, 'mixed' when multiple,
    else the stored value."""
    active = [c for c in ("email", "linkedin", "voice") if (counts.get(c, 0) or 0) > 0]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        return "mixed"
    return stored or None


def _owned_campaign_ids(db: Session, workspace_id: int) -> List[int]:
    """Return campaigns owned by the workspace that have AT LEAST ONE lead.

    Global rule (added 2026-05-23): a campaign with zero `nexus_leads`
    rows is treated as if it doesn't exist — it must not appear in
    counts, the GTM Journey, the leads list, the auto-hide candidates,
    analytics scoping, or any other downstream query that begins with
    "campaigns this workspace owns". Filtering at this single helper
    is the centralized enforcement point — ~9 downstream call sites
    inherit the behaviour for free.
    """
    if not workspace_id:
        return []

    # Short TTL cache — same result for ~15s of repeated calls from any
    # GTM Journey endpoint. Bust on writes via gtm_cache_invalidate().
    _ck = ("_owned_campaign_ids", int(workspace_id))
    _cached = _gtm_cache_get(_ck)
    if _cached is not None:
        return _cached

    rows = db.execute(
        text(
            """
            SELECT c.id
              FROM nexus_campaigns c
             WHERE c.workspace_id = :wid
               -- Skip archived (soft-deleted duplicate campaigns from
               -- the pre-URL-normalization mess). Their leads stay in
               -- the DB but never surface in any journey / analytics /
               -- report query that uses this canonical helper.
               AND COALESCE(c.status, '') <> 'archived'
               AND EXISTS (
                   SELECT 1 FROM nexus_leads nl
                    WHERE nl.campaign_id = c.id
               )
            """
        ),
        {"wid": workspace_id},
    ).fetchall()
    result = [r[0] for r in rows]
    _gtm_cache_set(_ck, result)
    return result


def slice_campaign_ids(
    db: Session,
    workspace_id: int,
    *,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
) -> List[int]:
    """Owned campaign ids (non-archived, has-leads) narrowed to a product /
    entity slice — the canonical campaign set behind both the dashboard's
    filtered counts and the report's leads table, so they can never drift."""
    owned = _owned_campaign_ids(db, workspace_id)
    if not owned:
        return []
    if product_id is None and entity_type not in ("product", "service", "gcc"):
        return owned
    from nexus.services import analytics_aggregator as agg

    params: Dict[str, Any] = {}
    sel = agg._slice_campaign_ids_sql(
        params, product_id=product_id, entity_type=entity_type
    )
    if not sel:
        return owned
    slice_ids = {int(r[0]) for r in db.execute(text(sel), params).fetchall()}
    return [c for c in owned if c in slice_ids]


def count_slice_distinct_leads(
    db: Session,
    workspace_id: int,
    *,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> int:
    """COUNT of (non-rejected) lead × campaign enrolments in the slice + date
    window — the SAME per-product rows the GTM Journey list now shows, so the
    Total Leads tile matches the table exactly (a person worked across two
    products counts once per product, i.e. as two rows)."""
    cids = slice_campaign_ids(
        db, workspace_id, product_id=product_id, entity_type=entity_type
    )
    if not cids:
        return 0
    params: Dict[str, Any] = {"cids": cids}
    date_clause = ""
    if since is not None:
        params["since"] = since
        date_clause += " AND created_at >= :since"
    if until is not None:
        params["until"] = until
        date_clause += " AND created_at <= :until"
    n = db.execute(
        text(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT global_lead_id, campaign_id FROM nexus_leads "
            "   WHERE campaign_id = ANY(:cids) "
            "     AND COALESCE(signals -> 'intent' ->> 'status', '') <> 'rejected'"
            + date_clause
            + ") x"
        ),
        params,
    ).scalar()
    return int(n or 0)


def _collect_attempt_stats(
    db: Session,
    campaign_ids: List[int],
    lead_ids: Optional[List[int]] = None,
    workspace_id: Optional[int] = None,
) -> Dict[int, Dict[str, Any]]:
    """Port of legacy collectAttemptStats(). Aggregates touchpoint and
    outreach counts per lead, scoped to the given campaign set.

    Returns a dict keyed by global_lead_id → {total, email, linkedin,
    voice, last_at}.
    """
    if not campaign_ids:
        return {}

    lead_filter = ""
    params: Dict[str, Any] = {"cids": tuple(campaign_ids)}
    if lead_ids:
        lead_filter = " AND lead_id = ANY(:lids)"
        params["lids"] = lead_ids

    # Touchpoint aggregation: per-channel counts + most recent sent_at.
    # `channel LIKE 'email%'` covers both the Resend channel ('email') and
    # the Apollo-routed channel ('email_apollo'). Without the wildcard,
    # leads contacted via the Apollo path show as touched-N-times with
    # zero "email" touches in the journey UI (Agent 1 finding #9).
    tp_sql = f"""
        SELECT lead_id,
               COUNT(*) AS total,
               SUM(CASE WHEN channel LIKE 'email%%' THEN 1 ELSE 0 END) AS email_n,
               SUM(CASE WHEN channel = 'linkedin' THEN 1 ELSE 0 END) AS linkedin_n,
               SUM(CASE WHEN channel = 'voice' THEN 1 ELSE 0 END) AS voice_n,
               MAX(sent_at) AS last_touchpoint_at
        FROM nexus_touchpoints
        WHERE campaign_id IN :cids{lead_filter}
              AND lead_id IS NOT NULL
        GROUP BY lead_id
    """
    tp_rows = db.execute(text(tp_sql), params).fetchall()

    # Outreach aggregation: every row counts as one email send.
    or_sql = f"""
        SELECT lead_id,
               COUNT(*) AS total,
               MAX(email_sent_at) AS last_outreach_at
        FROM nexus_outreach
        WHERE campaign_id IN :cids{lead_filter}
              AND lead_id IS NOT NULL
        GROUP BY lead_id
    """
    or_rows = db.execute(text(or_sql), params).fetchall()

    out: Dict[int, Dict[str, Any]] = {}
    for r in tp_rows:
        out[r[0]] = {
            "total": int(r[1] or 0),
            "email": int(r[2] or 0),
            "linkedin": int(r[3] or 0),
            "voice": int(r[4] or 0),
            "last_at": r[5],
        }
    for r in or_rows:
        cur = out.get(r[0], {"total": 0, "email": 0, "linkedin": 0, "voice": 0, "last_at": None})
        cur["total"] = (cur["total"] or 0) + int(r[1] or 0)
        cur["email"] = (cur["email"] or 0) + int(r[1] or 0)
        if r[2] and (not cur["last_at"] or r[2] > cur["last_at"]):
            cur["last_at"] = r[2]
        out[r[0]] = cur

    # Agent auto-replies (our outbound responses to a lead's inbound email)
    # live in nexus_inbound_messages(direction='outbound'), NOT in touchpoints
    # or outreach — but they're real email touches, so fold them into each
    # lead's email + total counts. Mapped lead ← thread ← inbound_lead ← email
    # ← global lead. Scoped to the caller's leads + workspace.
    if lead_ids:
        im_params: Dict[str, Any] = {"lids": lead_ids}
        im_ws = ""
        if workspace_id is not None:
            im_ws = " AND t.workspace_id = :ws"
            im_params["ws"] = workspace_id
        im_sql = f"""
            SELECT gl.id AS lead_id, COUNT(*) AS n
            FROM nexus_inbound_messages m
            JOIN nexus_inbound_threads t ON t.id = m.thread_id
            JOIN nexus_inbound_leads il ON il.id = t.lead_id
            JOIN nexus_global_leads gl ON lower(gl.email) = lower(il.email)
            WHERE m.direction = 'outbound'
                  AND gl.id = ANY(:lids){im_ws}
            GROUP BY gl.id
        """
        try:
            # Each agent reply we SENT counts as an email touch — just add it to
            # the lead's email + total. (No last_at compare here: received_at is
            # tz-aware while touchpoint last_at is naive, and comparing them
            # raises TypeError — which previously aborted this loop after the
            # first lead, so only one lead got its +1. Last-activity is already
            # driven by the touchpoint/LinkedIn timestamps below.)
            for r in db.execute(text(im_sql), im_params).fetchall():
                cur = out.get(r[0], {"total": 0, "email": 0, "linkedin": 0, "voice": 0, "last_at": None})
                cur["email"] = (cur["email"] or 0) + int(r[1] or 0)
                cur["total"] = (cur["total"] or 0) + int(r[1] or 0)
                out[r[0]] = cur
        except Exception as e:  # noqa: BLE001
            logger.debug("agent-reply touch count failed: %s", e)
            db.rollback()

    # LinkedIn messages live in their own table (nexus_linkedin_messages),
    # NOT in nexus_touchpoints, so the channel='linkedin' branch of the
    # touchpoint query above always returned zero. Count outbound DMs +
    # InMails per lead here and fold them into the per-lead totals so the
    # Lead Journey table's LinkedIn column reflects reality.
    if lead_ids:
        # Scope to the caller's workspace so a lead that exists in multiple
        # workspaces (or had prior Apollo-sourced messages elsewhere) doesn't
        # over-count. Without this filter the GTM Journey row was reading
        # the lifetime cross-workspace LinkedIn message count — that's why
        # freshly-uploaded manual leads were showing inflated touch counts
        # (e.g. "4" instead of "2" right after a single sequencer pass).
        li_params: Dict[str, Any] = {"lids": lead_ids}
        ws_filter = ""
        if workspace_id is not None:
            ws_filter = " AND workspace_id = :ws"
            li_params["ws"] = workspace_id
        # Count DISTINCT variants per lead instead of raw rows.
        # 2026-05-29 — the previous COUNT(*) over-counted whenever the
        # sequencer wrote duplicate rows for the same (lead, variant)
        # pair. That happens when:
        #   - two workers run the dedup-then-insert race concurrently
        #   - the InMail retry loop (sequencer.py L1135) re-creates
        #     a row after a partial rollback
        #   - a lead gets re-enrolled into a sequence
        # The detail panel ("InMail/DM" stat tile in NexusJourney.jsx
        # L546-557) counts distinct timeline event types and caps at
        # 2 (linkedin_message + linkedin_inmail). Aligning the table
        # column to the same semantics — DM (variant IS NULL or 'dm')
        # vs InMail (variant = 'inmail') — keeps the two views
        # consistent regardless of duplicate-row noise in DB.
        # NULL/'' variants are treated as 'dm' for backward-compat
        # with rows written before the variant column existed.
        li_sql = f"""
            SELECT lead_id,
                   COUNT(*) AS linkedin_n,
                   MAX(sent_at) AS last_li_at
            FROM nexus_linkedin_messages
            WHERE lead_id = ANY(:lids)
                  AND direction = 'outbound'{ws_filter}
                  -- A touch = a MESSAGE actually delivered: a note (on a
                  -- connection), an InMail, or a DM. Each such send stamps one
                  -- 'gtm-li:' urn → counts. A BARE connection request (no note
                  -- delivered) stamps 'gtm-li-nonote:' and is EXCLUDED — nothing
                  -- was actually said to the prospect. Drafts (urn NULL) excluded.
                  AND linkedin_message_urn IS NOT NULL
                  AND linkedin_message_urn NOT LIKE 'gtm-li-nonote:%'
            GROUP BY lead_id
        """
        li_rows = db.execute(text(li_sql), li_params).fetchall()
        for r in li_rows:
            cur = out.get(r[0], {"total": 0, "email": 0, "linkedin": 0, "voice": 0, "last_at": None})
            li_count = int(r[1] or 0)
            cur["linkedin"] = (cur["linkedin"] or 0) + li_count
            cur["total"] = (cur["total"] or 0) + li_count
            # nexus_linkedin_messages.sent_at is TIMESTAMPTZ (tz-aware)
            # while nexus_touchpoints.sent_at is TIMESTAMP WITHOUT TIME
            # ZONE (naive). The naive columns store WALL-CLOCK UTC by
            # convention, so to put the LinkedIn timestamp on the same
            # axis we must FIRST convert to UTC, then drop tzinfo. A
            # plain `.replace(tzinfo=None)` would bake in whatever
            # session timezone the Postgres driver applied (e.g. IST →
            # values appear ~5h30m off, surfacing as nonsense relative
            # times in the GTM Journey "Last Activity" column).
            from datetime import timezone as _tz
            li_at = r[2]
            if li_at is not None and getattr(li_at, "tzinfo", None) is not None:
                li_at = li_at.astimezone(_tz.utc).replace(tzinfo=None)
            if li_at and (not cur["last_at"] or li_at > cur["last_at"]):
                cur["last_at"] = li_at
            out[r[0]] = cur
    return out


def _touch_counts_per_campaign(
    db: Session,
    campaign_ids: List[int],
    lead_ids: Optional[List[int]] = None,
    workspace_id: Optional[int] = None,
) -> Dict[tuple, Dict[str, Any]]:
    """Like `_collect_attempt_stats` but keyed by (global_lead_id, campaign_id)
    so a lead enrolled in multiple PRODUCT campaigns shows each product's OWN
    touch counts (not a combined total). Every source is scoped to its
    campaign: touchpoints/outreach carry campaign_id directly; LinkedIn and
    agent-reply rows resolve their campaign via `lead_sequence_id → campaign`.
    Used only by the GTM Journey lead list (per-product rows)."""
    if not campaign_ids:
        return {}
    params: Dict[str, Any] = {"cids": tuple(campaign_ids)}
    lead_filter = ""
    if lead_ids:
        lead_filter = " AND lead_id = ANY(:lids)"
        params["lids"] = lead_ids

    def _blank():
        return {"total": 0, "email": 0, "linkedin": 0, "voice": 0, "last_at": None}

    out: Dict[tuple, Dict[str, Any]] = {}
    # 1) touchpoints (email/linkedin/voice) per (lead, campaign)
    for r in db.execute(text(f"""
        SELECT lead_id, campaign_id, COUNT(*) AS total,
               SUM(CASE WHEN channel LIKE 'email%%' THEN 1 ELSE 0 END) AS email_n,
               SUM(CASE WHEN channel = 'linkedin' THEN 1 ELSE 0 END) AS linkedin_n,
               SUM(CASE WHEN channel = 'voice' THEN 1 ELSE 0 END) AS voice_n,
               MAX(sent_at) AS last_at
          FROM nexus_touchpoints
         WHERE campaign_id IN :cids{lead_filter} AND lead_id IS NOT NULL
         GROUP BY lead_id, campaign_id
    """), params).fetchall():
        out[(int(r[0]), int(r[1]))] = {
            "total": int(r[2] or 0), "email": int(r[3] or 0),
            "linkedin": int(r[4] or 0), "voice": int(r[5] or 0), "last_at": r[6],
        }
    # 2) outreach = one email each, per (lead, campaign)
    for r in db.execute(text(f"""
        SELECT lead_id, campaign_id, COUNT(*) AS n, MAX(email_sent_at) AS last_at
          FROM nexus_outreach
         WHERE campaign_id IN :cids{lead_filter} AND lead_id IS NOT NULL
         GROUP BY lead_id, campaign_id
    """), params).fetchall():
        cur = out.get((int(r[0]), int(r[1])), _blank())
        cur["total"] += int(r[2] or 0); cur["email"] += int(r[2] or 0)
        if r[3] and (not cur["last_at"] or r[3] > cur["last_at"]):
            cur["last_at"] = r[3]
        out[(int(r[0]), int(r[1]))] = cur
    if lead_ids:
        im_params: Dict[str, Any] = {"lids": lead_ids, "cids": tuple(campaign_ids)}
        im_ws = ""
        if workspace_id is not None:
            im_ws = " AND t.workspace_id = :ws"; im_params["ws"] = workspace_id
        # 3) agent auto-replies we SENT, per (lead, campaign) via thread→sequence
        try:
            for r in db.execute(text(f"""
                SELECT gl.id, ls.campaign_id, COUNT(*) AS n
                  FROM nexus_inbound_messages m
                  JOIN nexus_inbound_threads t ON t.id = m.thread_id
                  JOIN nexus_lead_sequences ls ON ls.id = t.lead_sequence_id
                  JOIN nexus_inbound_leads il ON il.id = t.lead_id
                  JOIN nexus_global_leads gl ON lower(gl.email) = lower(il.email)
                 WHERE m.direction = 'outbound' AND gl.id = ANY(:lids)
                   AND ls.campaign_id IN :cids{im_ws}
                 GROUP BY gl.id, ls.campaign_id
            """), im_params).fetchall():
                cur = out.get((int(r[0]), int(r[1])), _blank())
                cur["email"] += int(r[2] or 0); cur["total"] += int(r[2] or 0)
                out[(int(r[0]), int(r[1]))] = cur
        except Exception as e:  # noqa: BLE001
            logger.debug("per-campaign agent-reply count failed: %s", e); db.rollback()
        # 4) LinkedIn DMs/InMails per (lead, campaign) via sequence→campaign
        li_params: Dict[str, Any] = {"lids": lead_ids, "cids": tuple(campaign_ids)}
        li_ws = ""
        if workspace_id is not None:
            li_ws = " AND lm.workspace_id = :ws"; li_params["ws"] = workspace_id
        from datetime import timezone as _tz
        for r in db.execute(text(f"""
            SELECT lm.lead_id, ls.campaign_id, COUNT(*) AS n, MAX(lm.sent_at) AS last_at
              FROM nexus_linkedin_messages lm
              JOIN nexus_lead_sequences ls ON ls.id = lm.lead_sequence_id
             WHERE lm.lead_id = ANY(:lids) AND lm.direction = 'outbound'
               AND ls.campaign_id IN :cids{li_ws}
               AND lm.linkedin_message_urn IS NOT NULL
               AND lm.linkedin_message_urn NOT LIKE 'gtm-li-nonote:%%'
             GROUP BY lm.lead_id, ls.campaign_id
        """), li_params).fetchall():
            cur = out.get((int(r[0]), int(r[1])), _blank())
            n = int(r[2] or 0); cur["linkedin"] += n; cur["total"] += n
            li_at = r[3]
            if li_at is not None and getattr(li_at, "tzinfo", None) is not None:
                li_at = li_at.astimezone(_tz.utc).replace(tzinfo=None)
            if li_at and (not cur["last_at"] or li_at > cur["last_at"]):
                cur["last_at"] = li_at
            out[(int(r[0]), int(r[1]))] = cur
    return out


def _with_journey_derived(lead: Dict[str, Any], derived: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Port of legacy withJourneyDerived(). Reconciles stored
    channel_attempts + derived counts (whichever is higher wins)."""
    stored = lead.get("channel_attempts") or {}
    if not isinstance(stored, dict):
        stored = {}
    counts = {
        "email": max((derived or {}).get("email", 0) or 0, stored.get("email", 0) or 0),
        "linkedin": max((derived or {}).get("linkedin", 0) or 0, stored.get("linkedin", 0) or 0),
        "voice": max((derived or {}).get("voice", 0) or 0, stored.get("voice", 0) or 0),
    }
    derived_total = (counts["email"] or 0) + (counts["linkedin"] or 0) + (counts["voice"] or 0)
    attempt_count_total = max(
        (derived or {}).get("total", 0) or 0,
        derived_total,
        lead.get("attempt_count_total", 0) or 0,
    )
    last_attempt_at = (derived or {}).get("last_at") or lead.get("last_attempt_at")
    last_attempt_channel = _normalize_last_channel(counts, lead.get("last_attempt_channel"))

    lead_out = dict(lead)
    lead_out["channel_attempts"] = counts
    lead_out["attempt_count_total"] = attempt_count_total
    lead_out["last_attempt_at"] = last_attempt_at
    lead_out["last_attempt_channel"] = last_attempt_channel
    return lead_out


def _lead_row_to_dict(row) -> Dict[str, Any]:
    """Convert a SELECT * FROM nexus_global_leads row (with named cols)
    to the dict shape the legacy frontend expects. Field names match
    the Mongo schema 1:1."""
    d = dict(row._mapping)
    # Synthesize `name` from first/last if not stored explicitly.
    if not d.get("name"):
        fn = (d.get("first_name") or "").strip()
        ln = (d.get("last_name") or "").strip()
        composed = (fn + " " + ln).strip()
        d["name"] = composed or d.get("email") or ""
    # Mirror company/company_name and job_title/role for the frontend.
    if not d.get("company"):
        d["company"] = d.get("company_name") or ""
    if not d.get("job_title"):
        d["job_title"] = d.get("role") or ""
    if not d.get("priority_state"):
        # Fall back to existing `priority` column or 'active'.
        d["priority_state"] = d.get("priority") or "active"
    # `createdAt` is the Mongo key; alias from created_at for parity.
    d["createdAt"] = d.get("created_at")
    return d


def _campaign_ids_for_lead(
    db: Session, global_lead_id: int, owned_campaign_ids: List[int]
) -> List[int]:
    """The Mongo schema stores `campaign_ids` as an array on the lead.
    In Postgres the relationship lives in nexus_leads. This returns the
    intersection of the lead's campaign enrollments with the workspace's
    owned campaigns."""
    if not owned_campaign_ids:
        return []
    rows = db.execute(
        text(
            """SELECT DISTINCT campaign_id FROM nexus_leads
               WHERE global_lead_id = :gid AND campaign_id = ANY(:cids)"""
        ),
        {"gid": global_lead_id, "cids": owned_campaign_ids},
    ).fetchall()
    return [r[0] for r in rows]


# ─── GET /campaign-send-progress ─────────────────────────────────────────────


@router.get("/campaign-send-progress")
def campaign_send_progress(
    campaign_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Return how many leads in a campaign have had their first email sent +
    LinkedIn DM/InMail drafted. The Upload Leads wizard polls this so it
    can fire a "all emails sent" toast the moment the sequencer finishes.

    Response:
      {
        "campaign_id":      int,
        "total":            int,   # leads enrolled
        "emails_sent":      int,   # leads with >=1 outbound email touchpoint
        "linkedin_drafted": int,   # leads with >=1 outbound LinkedIn message
        "pending_email":    int,   # = total - emails_sent
        "pending_linkedin": int,   # = total - linkedin_drafted
        "all_done":         bool,  # pending_email==0 AND pending_linkedin==0
      }

    Cheap: 3 small COUNT() queries scoped by workspace + campaign. Safe
    to poll every 10-30s.
    """
    wid = extract_workspace_id(workspace) or 0
    # Verify the campaign belongs to the caller's workspace; otherwise
    # we'd leak counts cross-tenant.
    owns = db.execute(
        text(
            "SELECT 1 FROM nexus_campaigns "
            "WHERE id = :cid AND workspace_id = :w LIMIT 1"
        ),
        {"cid": campaign_id, "w": wid},
    ).first()
    if not owns:
        raise HTTPException(status_code=404, detail="Campaign not found")

    total = db.execute(
        text(
            "SELECT COUNT(DISTINCT global_lead_id) FROM nexus_leads "
            "WHERE campaign_id = :cid AND workspace_id = :w"
        ),
        {"cid": campaign_id, "w": wid},
    ).scalar() or 0

    emails_sent = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT nl.global_lead_id)
            FROM nexus_leads nl
            WHERE nl.campaign_id = :cid
              AND nl.workspace_id = :w
              AND EXISTS (
                  SELECT 1 FROM nexus_touchpoints tp
                  WHERE tp.lead_id = nl.global_lead_id
                    AND tp.campaign_id = :cid
                    AND tp.channel LIKE 'email%'
                    AND tp.status = 'sent'
              )
            """
        ),
        {"cid": campaign_id, "w": wid},
    ).scalar() or 0

    linkedin_drafted = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT nl.global_lead_id)
            FROM nexus_leads nl
            WHERE nl.campaign_id = :cid
              AND nl.workspace_id = :w
              AND EXISTS (
                  SELECT 1 FROM nexus_linkedin_messages lim
                  WHERE lim.lead_id = nl.global_lead_id
                    AND lim.workspace_id = :w
                    AND lim.direction = 'outbound'
              )
            """
        ),
        {"cid": campaign_id, "w": wid},
    ).scalar() or 0

    total_i = int(total)
    sent_i = int(emails_sent)
    li_i = int(linkedin_drafted)
    pending_email = max(0, total_i - sent_i)
    pending_linkedin = max(0, total_i - li_i)
    return {
        "campaign_id": campaign_id,
        "total": total_i,
        "emails_sent": sent_i,
        "linkedin_drafted": li_i,
        "pending_email": pending_email,
        "pending_linkedin": pending_linkedin,
        "all_done": total_i > 0 and pending_email == 0 and pending_linkedin == 0,
    }


# ─── GET /summary ────────────────────────────────────────────────────────────


@router.get("/summary")
def journey_summary(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Bucket counts for the GTM Journey header.

    Optional `since`/`until` window the lead set by when each lead was added
    to a campaign (nexus_leads.created_at) — used by the dashboard's FROM/TO
    filter. When omitted (the GTM-Journey header), counts are lifetime."""
    wid = extract_workspace_id(workspace) or 0
    campaign_ids = _owned_campaign_ids(db, wid)
    if not campaign_ids:
        return {"summary": {"active": 0, "hidden": 0, "total": 0, "eligible_auto_hide": 0}}

    # Leads whose enrollment intersects any owned campaign (optional date window).
    _ls_params: Dict[str, Any] = {"cids": campaign_ids}
    _ls_date = ""
    if since is not None:
        _ls_params["since"] = since
        _ls_date += " AND created_at >= :since"
    if until is not None:
        _ls_params["until"] = until
        _ls_date += " AND created_at <= :until"
    # One entry per (lead × campaign) so the header counts MATCH the per-product
    # rows the /leads list returns (a person in two products = two rows = two).
    enroll_pairs = db.execute(
        text(
            "SELECT DISTINCT global_lead_id, campaign_id FROM nexus_leads "
            "WHERE campaign_id = ANY(:cids) "
            # Rejected (not-picked) leads are thrown out of the journey.
            "AND COALESCE(signals -> 'intent' ->> 'status', '') <> 'rejected'"
            + _ls_date
        ),
        _ls_params,
    ).fetchall()
    if not enroll_pairs:
        return {"summary": {"active": 0, "hidden": 0, "total": 0, "eligible_auto_hide": 0}}
    lead_ids = list({int(r[0]) for r in enroll_pairs})

    rows = db.execute(
        text(
            "SELECT id, status, priority_state, attempt_count_total, channel_attempts "
            "FROM nexus_global_leads WHERE id = ANY(:lids)"
        ),
        {"lids": lead_ids},
    ).fetchall()

    derived_map = _collect_attempt_stats(db, campaign_ids, lead_ids, workspace_id=wid)
    # Per-lead active/hidden/eligible verdict (priority_state + attempts are
    # global per person); applied once per enrolment row below.
    info_by_gid: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        lead = _lead_row_to_dict(r)
        info_by_gid[int(lead["id"])] = _with_journey_derived(lead, derived_map.get(lead["id"]))

    active = 0
    hidden = 0
    eligible = 0
    for (gid, _cid) in enroll_pairs:
        merged = info_by_gid.get(int(gid))
        if merged is None:
            continue
        if merged.get("priority_state") == "hidden":
            hidden += 1
        else:
            active += 1
        is_terminal = (merged.get("status") or "") in _TERMINAL_STATUSES
        if (
            not is_terminal
            and merged.get("priority_state") != "hidden"
            and (merged.get("attempt_count_total") or 0) >= 3
        ):
            eligible += 1

    return {
        "summary": {
            "active": active,
            "hidden": hidden,
            "total": len(enroll_pairs),
            "eligible_auto_hide": eligible,
        }
    }


# ─── GET /leads ──────────────────────────────────────────────────────────────


@router.get("/leads")
def journey_leads(
    view: str = Query("active", pattern="^(active|hidden|all)$"),
    campaign_id: Optional[int] = Query(None),
    product_id: Optional[int] = Query(None),
    # ── Dynamic filter/search params (additive, 2026-05-27) ─────────────────
    # All optional → existing callers unaffected. The redesigned GTM Journey
    # workspace uses these to drive its filter sidebar + omnibox search.
    q: Optional[str] = Query(None, description="Normalised partial search across name/email/company/domain/title/campaign/campaign_number/location"),
    status: Optional[List[str]] = Query(None, description="Multi-select lead.status filter"),
    priority_states: Optional[List[str]] = Query(None),
    companies: Optional[List[str]] = Query(None, description="Exact match against company / company_name"),
    job_titles: Optional[List[str]] = Query(None, description="Exact match against job_title / role"),
    product_ids: Optional[List[int]] = Query(None, description="Multi-product filter (use this OR singular product_id)"),
    email_verified: Optional[bool] = Query(None),
    engagement: Optional[List[str]] = Query(
        None,
        description="Derived: replied | demo_scheduled | contacted_no_reply | not_contacted",
    ),
    sort: Optional[str] = Query(
        None,
        pattern="^(activity|name|created|attempts)_(asc|desc)$",
        description="Sort key — defaults to view-specific ranking when omitted",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """List leads in either the active, hidden, or ALL bucket. Optionally
    filter to a single owned campaign.

    `view=all` was added 2026-05-23 — the frontend now shows a single
    merged list with a "(Hidden)" tag next to hidden leads instead of a
    separate Hidden toggle. The `priority_state` field is still returned
    on every row so the UI can render the tag.

    Additional optional query params (added 2026-05-27 for the lead-workspace
    redesign): `q`, `status`, `priority_states`, `companies`, `job_titles`,
    `product_ids`, `email_verified`, `engagement`, `sort`, `limit`, `offset`.
    All are post-filters applied AFTER the existing SQL fetch + derived
    enrichment so the new behaviour is layered on top without disturbing
    the existing query shape. Response gains a `total` field (pre-pagination
    count) only when any of these new params are provided — legacy callers
    that send none still receive the plain `{leads: [...]}` shape.
    """
    wid = extract_workspace_id(workspace) or 0
    campaign_ids = _owned_campaign_ids(db, wid)
    if not campaign_ids:
        return {"leads": []}

    if campaign_id is not None:
        if campaign_id not in campaign_ids:
            raise HTTPException(status_code=404, detail="Campaign not found")
        campaign_ids = [campaign_id]
    elif product_id is not None:
        # Narrow to campaigns under this product (frontend product-filter pills).
        prod_cids = [
            r[0]
            for r in db.execute(
                text(
                    "SELECT id FROM nexus_campaigns "
                    "WHERE workspace_id = :wid AND product_id = :pid"
                ),
                {"wid": wid, "pid": product_id},
            ).fetchall()
        ]
        if not prod_cids:
            return {"leads": []}
        campaign_ids = prod_cids
    elif product_ids:
        # Multi-product filter (added with the workspace redesign). Mirrors
        # the singular product_id branch above but accepts a list.
        #
        # 2026-05-29 — Sibling-product expansion. The sidebar Campaign
        # filter merges duplicates (same name + entity_type) for display
        # and uses the LOWEST product_id of each group as the canonical
        # value. Here we expand each requested product_id back into the
        # full set of sibling product_ids that share its (name,
        # entity_type) within this workspace, so clicking the merged
        # row surfaces leads from ALL underlying campaigns — not just
        # the canonical one.
        expanded_pids = set(int(p) for p in product_ids)
        try:
            sibling_rows = db.execute(
                text(
                    """
                    SELECT DISTINCT p2.id
                      FROM nexus_products p1
                      JOIN nexus_products p2
                        ON LOWER(TRIM(p1.name)) = LOWER(TRIM(p2.name))
                       AND COALESCE(NULLIF(p1.icp->>'entity_type',''),'product')
                         = COALESCE(NULLIF(p2.icp->>'entity_type',''),'product')
                     WHERE p1.id = ANY(:pids)
                       AND p1.workspace_id = :wid
                       AND p2.workspace_id = :wid
                    """
                ),
                {"pids": list(expanded_pids), "wid": wid},
            ).fetchall()
            for r in sibling_rows:
                expanded_pids.add(int(r[0]))
        except Exception:
            # If expansion fails (very rare), fall back to raw input.
            pass

        prod_cids = [
            r[0]
            for r in db.execute(
                text(
                    "SELECT id FROM nexus_campaigns "
                    "WHERE workspace_id = :wid AND product_id = ANY(:pids)"
                ),
                {"wid": wid, "pids": list(expanded_pids)},
            ).fetchall()
        ]
        if not prod_cids:
            return {"leads": []}
        campaign_ids = [c for c in prod_cids if c in campaign_ids]
        if not campaign_ids:
            return {"leads": []}

    # Find unique lead IDs enrolled in any selected campaign.
    # GTM Journey shows the campaign's book EXCEPT rejected (not-picked) leads,
    # which are thrown out of the view. Legacy no-intent + accepted + pending
    # all show; only an explicit 'rejected' verdict is hidden. (Outreach is
    # separately gated at enrollment — accepted-only.)
    enrolled = db.execute(
        text(
            "SELECT DISTINCT global_lead_id, campaign_id FROM nexus_leads "
            "WHERE campaign_id = ANY(:cids) "
            "AND COALESCE(signals -> 'intent' ->> 'status', '') <> 'rejected'"
        ),
        {"cids": campaign_ids},
    ).fetchall()
    # One row per (lead × campaign): a person enrolled in multiple PRODUCT
    # campaigns appears once PER product, not collapsed to the most-recent one.
    # Each product's flow/content is already stored separately (per
    # lead_sequence → campaign → product), so each row opens its own detail.
    enroll_pairs = [(int(r[0]), int(r[1])) for r in enrolled]
    lead_ids = list(dict.fromkeys(gid for gid, _ in enroll_pairs))
    if not lead_ids:
        return {"leads": []}

    # Bucket filter applied at the global lead level.
    if view == "hidden":
        where_priority = "priority_state = 'hidden'"
    elif view == "all":
        where_priority = "TRUE"
    else:  # 'active' (default)
        where_priority = "(priority_state IS NULL OR priority_state <> 'hidden')"
    rows = db.execute(
        text(
            f"SELECT * FROM nexus_global_leads "
            f"WHERE id = ANY(:lids) AND {where_priority} "
            "LIMIT 1000"
        ),
        {"lids": lead_ids},
    ).fetchall()

    # ISOLATION: the shared global row's status/phone can reflect ANOTHER
    # workspace's activity for a lead targeted by two tenants. Load THIS
    # workspace's own status/phone from nexus_leads and override below, so the
    # display is per-workspace (falls back to global only for legacy/unset rows).
    ws_state_by_lead: Dict[int, Dict[str, Any]] = {}
    try:
        _lids_ws = [int(r._mapping["id"]) for r in rows]
        if _lids_ws:
            for sr in db.execute(
                text(
                    "SELECT DISTINCT ON (global_lead_id) global_lead_id, status, phone, phones "
                    "FROM nexus_leads "
                    "WHERE workspace_id = :ws AND global_lead_id = ANY(:lids) "
                    "ORDER BY global_lead_id, updated_at DESC"
                ),
                {"ws": wid, "lids": _lids_ws},
            ).fetchall():
                ws_state_by_lead[int(sr[0])] = {
                    "status": sr[1], "phone": sr[2], "phones": sr[3],
                }
    except Exception:
        logger.warning("journey: per-workspace status/phone lookup failed", exc_info=True)

    # Per-(lead, campaign) so each product row shows its OWN touch counts.
    derived_map = _touch_counts_per_campaign(
        db, campaign_ids, [r._mapping["id"] for r in rows], workspace_id=wid,
    )

    # Per-lead product attribution: which campaign's product does each
    # global_lead belong to? A lead can technically be enrolled in
    # multiple campaigns; we pick the most recently enrolled one as the
    # "owning" product so the lead card shows a stable tag. Single SQL,
    # no N+1.
    # Keyed by (global_lead_id, campaign_id) so each per-product row gets its
    # OWN product tag, match score, and enrolment date (no most-recent collapse).
    product_by_lead: Dict[tuple, Dict[str, Any]] = {}
    try:
        lead_id_list = [int(r._mapping["id"]) for r in rows]
        if lead_id_list:
            attribution_rows = db.execute(
                text(
                    """
                    SELECT nl.global_lead_id, p.id, p.name, p.source_url,
                           COALESCE(NULLIF(p.icp->>'entity_type',''),'product') AS entity_type,
                           nl.created_at AS enrolled_at,
                           nl.icp_score AS icp_score,
                           nl.campaign_id AS campaign_id
                      FROM nexus_leads nl
                      JOIN nexus_campaigns c ON c.id = nl.campaign_id
                      JOIN nexus_products p  ON p.id = c.product_id
                     WHERE nl.global_lead_id = ANY(:lids)
                       AND nl.campaign_id = ANY(:cids)
                    """
                ),
                {"lids": lead_id_list, "cids": campaign_ids},
            ).fetchall()
            for ar in attribution_rows:
                product_by_lead[(int(ar[0]), int(ar[7]))] = {
                    "product_id": int(ar[1]) if ar[1] is not None else None,
                    "product_name": ar[2] or "",
                    "product_source_url": ar[3] or "",
                    "product_entity_type": ar[4] or "product",
                    # 2026-05-23: surface per-campaign enrollment timestamp
                    # so the UI's "Xh ago" reflects when the lead was
                    # enrolled in the CURRENT run, not when it was first
                    # discovered globally (which can be days/weeks earlier
                    # for re-used leads across campaigns).
                    "enrolled_at": ar[5],
                    # 2026-05-29: icp_score from nexus_leads, used by the
                    # new Fit Score column on the GTM Journey table.
                    # None (BYO lead with no supplied score) flows through as
                    # null → the table renders a blank pill, not a "0".
                    "icp_score": None if ar[6] is None else int(ar[6]),
                    # 2026-06-06: the campaign this row is attributed to, so the
                    # frontend can open the detail SCOPED to this campaign
                    # (?campaign_id=) → no product mixing for multi-campaign leads.
                    "campaign_id": int(ar[7]) if ar[7] is not None else None,
                }
    except Exception:
        logger.warning("journey: per-lead product attribution failed", exc_info=True)

    # Per-product campaign number + creation date for the campaigns these leads
    # belong to — shown as the "Campaign" + "Created" columns in the GTM Journey
    # table. `product_campaign_number` restarts at 1 per product (Spenzo → 1,2,3);
    # the global id stays internal. Defensive: if the column isn't there yet
    # (pre-migration) the map stays empty and the UI simply omits the number.
    campaign_meta_by_id: Dict[int, Dict[str, Any]] = {}
    try:
        meta_rows = db.execute(
            text(
                "SELECT id, product_campaign_number, created_at "
                "FROM nexus_campaigns WHERE id = ANY(:cids)"
            ),
            {"cids": campaign_ids},
        ).fetchall()
        for mr in meta_rows:
            campaign_meta_by_id[int(mr[0])] = {
                "campaign_number": int(mr[1]) if mr[1] is not None else None,
                "campaign_created_at": mr[2],
            }
    except Exception:
        logger.warning(
            "journey: campaign number/date lookup failed (column may be pre-migration)",
            exc_info=True,
        )

    # Per-lead "when was the demo actually booked" timestamp. The
    # OutreachFlowPanel previously aliased lead.last_contacted_at as the
    # demo-booked time, which collapsed Email Sent and Demo Booked to
    # the same timestamp. Pull the booking's created_at directly so the
    # timeline shows the real booking moment.
    booking_at_by_lead: Dict[int, Any] = {}
    try:
        if lead_id_list:
            booking_rows = db.execute(
                text(
                    """
                    SELECT DISTINCT ON (lead_id)
                           lead_id, created_at, scheduled_at
                      FROM nexus_demo_bookings
                     WHERE lead_id = ANY(:lids)
                     ORDER BY lead_id, created_at DESC
                    """
                ),
                {"lids": lead_id_list},
            ).fetchall()
            for br in booking_rows:
                booking_at_by_lead[int(br[0])] = {
                    "demo_booked_at": br[1],
                    "demo_scheduled_at": br[2],
                }
    except Exception:
        logger.warning("journey: per-lead demo booking lookup failed", exc_info=True)

    # Base global-lead row per person (those that passed the priority/bucket
    # filter), looked up as we walk the (lead × campaign) enrolment pairs.
    lead_base_by_gid = {int(r._mapping["id"]): r for r in rows}
    response: List[Dict[str, Any]] = []
    for (_gid, _cid) in enroll_pairs:
        base = lead_base_by_gid.get(_gid)
        if base is None:
            continue  # hidden/filtered out at the global-lead level
        lead = _lead_row_to_dict(base)
        merged = _with_journey_derived(lead, derived_map.get((_gid, _cid)))
        # Override the shared-row status/phone with THIS workspace's own value.
        _wss = ws_state_by_lead.get(int(merged["id"]))
        if _wss:
            if _wss.get("status"):
                merged["status"] = _wss["status"]
            if _wss.get("phone"):
                merged["phone"] = _wss["phone"]
            if _wss.get("phones") is not None:
                merged["phones"] = _wss["phones"]
        is_terminal = (merged.get("status") or "") in _TERMINAL_STATUSES
        attribution = product_by_lead.get((_gid, _cid), {})
        response.append(
            {
                "_id": merged["id"],
                # Unique per (lead × campaign) so the same person under two
                # products renders as two distinct rows (React key + selection).
                "row_key": f"{_gid}-{_cid}",
                "email": merged.get("email"),
                "name": merged.get("name") or "",
                "company": merged.get("company") or "",
                "company_domain": merged.get("company_domain") or "",
                "job_title": merged.get("job_title") or "",
                "linkedin_url": merged.get("linkedin_url") or "",
                # Phone — returned by Apollo on the search and/or
                # bulk_match path (see discovery_apollo.py). Empty when
                # Apollo didn't return one (typical on free-tier);
                # frontend renders an em-dash placeholder.
                # NOTE: This was missing before 2026-06-02 — the column
                # was populated in DB but never surfaced in the GTM
                # Journey response, so the LeadRow showed every phone
                # as "—" even when one was on file.
                "phone": merged.get("phone") or "",
                # All captured numbers (scenario 9). The table shows `phone` as
                # the primary click-to-dial and surfaces any extras as a "+N"
                # badge. Empty list when only the primary (or none) is on file.
                "phones": merged.get("phones") or [],
                # Location — joined "City, State, Country" string built
                # from the per-column person_city / person_state /
                # person_country fields Apollo gives us. Empty when
                # Apollo didn't return any location for the lead;
                # frontend renders an em-dash placeholder.
                "location": ", ".join(
                    p for p in (
                        (merged.get("person_city") or "").strip(),
                        (merged.get("person_state") or "").strip(),
                        (merged.get("person_country") or "").strip(),
                    )
                    if p
                ),
                "status": merged.get("status") or "new",
                "priority_state": merged.get("priority_state") or "active",
                "hidden_reason": merged.get("hidden_reason") or "",
                "hidden_at": merged.get("hidden_at"),
                # Prefer the per-campaign enrollment time (from nexus_leads.created_at)
                # over the global lead discovery time. Falls back to global created_at
                # for legacy rows where the join didn't find an enrollment row.
                "enrolled_at": attribution.get("enrolled_at") or merged.get("createdAt"),
                "attempt_count_total": merged.get("attempt_count_total") or 0,
                "channel_attempts": merged.get("channel_attempts")
                or {"email": 0, "linkedin": 0, "voice": 0},
                "last_attempt_at": merged.get("last_attempt_at"),
                "last_attempt_channel": merged.get("last_attempt_channel"),
                "total_emails_sent": merged.get("total_emails_sent") or 0,
                "eligible_for_auto_hide": (
                    not is_terminal
                    and merged.get("priority_state") != "hidden"
                    and (merged.get("attempt_count_total") or 0) >= 3
                ),
                # Product attribution — used by LeadCard to render the
                # campaign-product tag (z-ninth / Spenzo AI / Pipelyt).
                "product_id":   attribution.get("product_id"),
                "product_name": attribution.get("product_name"),
                "product_entity_type": attribution.get("product_entity_type"),
                # Campaign attribution — the campaign this lead is in, its
                # per-product number ("Campaign 1, 2, 3" — restarts per product),
                # and when the campaign was created. Shown in the GTM Journey
                # table. campaign_number is None pre-migration → UI omits it.
                "campaign_id": attribution.get("campaign_id"),
                "campaign_number": campaign_meta_by_id.get(
                    attribution.get("campaign_id") or -1, {}
                ).get("campaign_number"),
                "campaign_created_at": campaign_meta_by_id.get(
                    attribution.get("campaign_id") or -1, {}
                ).get("campaign_created_at"),
                # Fit Score (0-100) — computed by icp_scorer.score_icp_fit
                # at enrollment time, OR the value the user uploaded (BYO).
                # None ⇒ unscored BYO lead → table renders a blank pill.
                "icp_score": attribution.get("icp_score"),
                # Demo booking timestamps — surfaced so OutreachFlowPanel's
                # "Demo Booked" stage shows when the booking row was
                # created, not when the last email went out.
                "demo_booked_at": (
                    booking_at_by_lead.get(int(merged["id"]), {}).get("demo_booked_at")
                ),
                "demo_scheduled_at": (
                    booking_at_by_lead.get(int(merged["id"]), {}).get("demo_scheduled_at")
                ),
                # Lead provenance — surfaced so the frontend can mark
                # user-uploaded leads with a "Manual" tag (vs. Apollo-
                # discovered). Values: 'apollo', 'manual_upload', 'hunter',
                # 'github', etc. Defaults to empty string for legacy rows
                # that pre-date the source column.
                "source": merged.get("source") or "",
            }
        )

    # ── Post-filter pass (additive query params from 2026-05-27 redesign) ──
    # These run in Python on the in-memory `response` list rather than in
    # SQL so we don't disturb the existing query shape — every clause is
    # opt-in and short-circuits when the param is absent. The 1000-row
    # safety cap on the SQL above still bounds total work.
    _new_filter_params = [
        q, status, priority_states, companies, job_titles,
        product_ids, email_verified, engagement, sort,
    ]
    _any_new_filter = any(p not in (None, [], "") for p in _new_filter_params)

    if q:
        # Normalised partial search: strip everything but [a-z0-9] from both the
        # query and each field so "JPMorgan Chase" matches "jpmorganchase",
        # "j.p morgan", etc. Covers the columns the user searches by:
        # name, title (job_title), company, campaign (product_name),
        # campaign id (campaign_number) and location. Email kept for utility.
        def _norm(v):
            return re.sub(r"[^a-z0-9]", "", str(v if v is not None else "").lower())

        def _fuzzy_contains(pattern, text, max_dist):
            # Approximate substring match: the minimum edit distance of
            # `pattern` against ANY substring of `text` (row 0 seeded to 0 so a
            # match may begin anywhere). Lets typos like "hyera" hit
            # "hyderabad" (distance 1). Mirrored client-side in NexusJourney.jsx.
            m, n = len(pattern), len(text)
            if m == 0:
                return True
            if n == 0:
                return m <= max_dist
            prev = [0] * (n + 1)
            for i in range(1, m + 1):
                cur = [i] + [0] * n
                pc = pattern[i - 1]
                for j in range(1, n + 1):
                    cost = 0 if pc == text[j - 1] else 1
                    cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
                prev = cur
            return min(prev) <= max_dist

        q_norm = _norm(q)
        if q_norm:
            _q_fields = (
                "name", "email", "company", "company_domain", "job_title",
                "product_name", "campaign_number", "location",
            )
            # Fuzzy tolerance scales with query length: short queries stay exact
            # (substring only) to avoid noise; 4-6 chars allow 1 typo, 7+ allow 2.
            _max_dist = 0 if len(q_norm) < 4 else (1 if len(q_norm) <= 6 else 2)

            # A purely-numeric query is a Campaign ID lookup — match
            # campaign_number EXACTLY rather than as a substring of emails,
            # domains, names, etc. (typing "1" must not surface a lead whose
            # email merely contains a "1").
            _q_is_numeric = q_norm.isdigit()

            def _match_q(r):
                if _q_is_numeric:
                    return _norm(r.get("campaign_number")) == q_norm
                norms = [nv for nv in (_norm(r.get(f)) for f in _q_fields) if nv]
                # Fast, sharp path first — exact normalised substring.
                if any(q_norm in nv for nv in norms):
                    return True
                # Typo-tolerant fallback (only when the query is long enough).
                if _max_dist:
                    return any(_fuzzy_contains(q_norm, nv, _max_dist) for nv in norms)
                return False

            response = [r for r in response if _match_q(r)]

    if status:
        status_set = {s for s in status if s}
        if status_set:
            response = [r for r in response if (r.get("status") or "") in status_set]

    if priority_states:
        ps_set = {s for s in priority_states if s}
        if ps_set:
            response = [
                r for r in response
                if (r.get("priority_state") or "active") in ps_set
            ]

    if companies:
        comp_set = {c for c in companies if c}
        if comp_set:
            response = [r for r in response if (r.get("company") or "") in comp_set]

    if job_titles:
        title_set = {t for t in job_titles if t}
        if title_set:
            response = [r for r in response if (r.get("job_title") or "") in title_set]

    if email_verified is not None:
        # We exposed email_verified on the source row; pull it now to filter.
        # `_lead_row_to_dict` didn't strip it — it's in the dict already.
        # Coerce so DB NULL counts as "not verified".
        def _ev(r):
            v = r.get("email_verified")
            return bool(v) if v is not None else False
        response = [r for r in response if _ev(r) == bool(email_verified)]

    if engagement:
        # Derived buckets so the UI can ask "show me leads who replied" etc.
        # without needing to know the underlying status taxonomy.
        eng_set = {e for e in engagement if e}
        def _eng_match(r):
            s = (r.get("status") or "").lower()
            attempts = int(r.get("attempt_count_total") or 0)
            if "replied" in eng_set and s == "replied":
                return True
            if "demo_scheduled" in eng_set and s == "demo_scheduled":
                return True
            if "contacted_no_reply" in eng_set and s == "contacted":
                return True
            if "not_contacted" in eng_set and attempts == 0 and s == "new":
                return True
            return False
        if eng_set:
            response = [r for r in response if _eng_match(r)]

    _epoch = datetime(1970, 1, 1)

    def _sort_active(a):
        return (
            -(a.get("enrolled_at") or _epoch).timestamp() if a.get("enrolled_at") else 0,
            -_status_rank(a.get("status")),
            -(a.get("attempt_count_total") or 0),
        )

    def _sort_hidden(a):
        return -(a.get("hidden_at") or _epoch).timestamp() if a.get("hidden_at") else 0

    # Explicit sort overrides the view-specific ranking when provided.
    if sort:
        key, _, direction = sort.partition("_")
        reverse = (direction == "desc")
        def _sort_key(r):
            if key == "activity":
                ts = r.get("last_attempt_at") or r.get("enrolled_at") or _epoch
                return ts.timestamp() if hasattr(ts, "timestamp") else 0
            if key == "name":
                return (r.get("name") or "").lower()
            if key == "created":
                ts = r.get("enrolled_at") or _epoch
                return ts.timestamp() if hasattr(ts, "timestamp") else 0
            if key == "attempts":
                return int(r.get("attempt_count_total") or 0)
            return 0
        response.sort(key=_sort_key, reverse=reverse)
    elif view == "hidden":
        response.sort(key=_sort_hidden)
    elif view == "all":
        # Mixed bucket: active rows first (sorted by activity), then hidden
        # rows at the bottom (sorted by hidden_at desc). Keeps the
        # high-attention items at top while still surfacing hidden ones.
        active_rows = [r for r in response if (r.get("priority_state") or "active") != "hidden"]
        hidden_rows = [r for r in response if (r.get("priority_state") or "active") == "hidden"]
        active_rows.sort(key=_sort_active)
        hidden_rows.sort(key=_sort_hidden)
        response = active_rows + hidden_rows
    else:
        response.sort(key=_sort_active)

    # 2026-06-03 — ALWAYS paginate + return total.
    #
    # Previously pagination only applied when a "new" filter param
    # (status/company/etc.) was present. Without a filter the endpoint
    # returned ALL leads (508+) in one response, so the GTM Journey
    # page rendered every row and bogged down — while WITH a filter it
    # correctly showed 25 + infinite scroll. The frontend always sends
    # `limit` + `offset` now, so pagination should be unconditional.
    # `total` is the pre-pagination count the frontend uses to decide
    # whether more pages remain (hasMore).
    total = len(response)
    response = response[offset : offset + limit]
    return {"leads": response, "total": total, "limit": limit, "offset": offset}


# ─── GET /filters/schema ─────────────────────────────────────────────────────


@router.get("/filters/schema")
def journey_filters_schema(
    view: str = Query("all", pattern="^(active|hidden|all)$"),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Introspect the workspace's lead corpus and return the filter groups
    that the GTM Journey workspace sidebar can render — driven entirely by
    what data actually exists.

    Design rules (per the 2026-05-27 redesign brief):
    - Never hard-code filters. Every group below is only included if at
      least one option has count > 0.
    - Counts are facets over the *unfiltered* workspace-scoped lead set
      (further narrowed only by `view`), so the sidebar tells the user
      what's available before they pick anything.
    - High-cardinality fields (company, job_title) are capped at the top
      20 by count so the sidebar stays light. The omnibox handles search.

    Response shape:
        {
          "total": int,                # total leads matched by `view`
          "filters": [
            {"id": str, "label": str, "type": "multi-select"|"boolean",
             "options": [{"value": ..., "label": str, "count": int}, ...]},
            ...
          ]
        }
    """
    wid = extract_workspace_id(workspace) or 0

    # TTL cache — filter schema is rebuilt on every view toggle and every
    # sidebar open. Same workspace + same view returns identical structure
    # for ~15s.
    _schema_ck = ("filters_schema", int(wid), view)
    _cached_schema = _gtm_cache_get(_schema_ck)
    if _cached_schema is not None:
        return _cached_schema

    campaign_ids = _owned_campaign_ids(db, wid)
    if not campaign_ids:
        return {"total": 0, "filters": []}

    # One entry per (lead × campaign) so the sidebar facet counts MATCH the
    # per-product rows the /leads list returns (a person in two products is
    # counted once per product).
    enroll_pairs = [
        (int(r[0]), int(r[1]))
        for r in db.execute(
            text(
                "SELECT DISTINCT global_lead_id, campaign_id FROM nexus_leads "
                "WHERE campaign_id = ANY(:cids) "
                # Rejected (not-picked) leads are thrown out of the journey.
                "AND COALESCE(signals -> 'intent' ->> 'status', '') <> 'rejected'"
            ),
            {"cids": campaign_ids},
        ).fetchall()
    ]
    lead_ids = list({gid for gid, _ in enroll_pairs})
    if not lead_ids:
        return {"total": 0, "filters": []}

    if view == "hidden":
        where_priority = "priority_state = 'hidden'"
    elif view == "active":
        where_priority = "(priority_state IS NULL OR priority_state <> 'hidden')"
    else:
        where_priority = "TRUE"

    rows = db.execute(
        text(
            f"""SELECT id, status, priority_state, company, company_name,
                       job_title, role, email_verified, attempt_count_total
                  FROM nexus_global_leads
                 WHERE id = ANY(:lids) AND {where_priority}"""
        ),
        {"lids": lead_ids},
    ).fetchall()
    from collections import Counter

    # Global-lead info by id (only those that passed the priority/view filter).
    info_by_gid = {int(r._mapping["id"]): r._mapping for r in rows}
    # Keep only enrolment pairs whose lead passed the view filter.
    pairs = [(g, c) for (g, c) in enroll_pairs if g in info_by_gid]
    total = len(pairs)
    if total == 0:
        return {"total": 0, "filters": []}

    # campaign -> (product_id, name, entity_type) for the per-pair product facet.
    cprod: Dict[int, tuple] = {}
    for pr in db.execute(
        text(
            "SELECT c.id, p.id, p.name, "
            "COALESCE(NULLIF(p.icp->>'entity_type',''),'product') AS et "
            "FROM nexus_campaigns c JOIN nexus_products p ON p.id = c.product_id "
            "WHERE c.id = ANY(:cids)"
        ),
        {"cids": campaign_ids},
    ).fetchall():
        cprod[int(pr[0])] = (int(pr[1]), pr[2] or "", (pr[3] or "product"))

    status_counts: Counter = Counter()
    priority_counts: Counter = Counter()
    company_counts: Counter = Counter()
    title_counts: Counter = Counter()
    verified_counts = {"true": 0, "false": 0}
    engagement_counts = {
        "replied": 0,
        "demo_scheduled": 0,
        "contacted_no_reply": 0,
        "not_contacted": 0,
    }
    product_agg: Dict[int, List[Any]] = {}  # product_id -> [name, entity_type, count]

    # Count once PER (lead × campaign) row so every facet sums to `total`.
    for (gid, cid) in pairs:
        m = info_by_gid[gid]
        s = (m["status"] or "new").strip()
        status_counts[s] += 1
        ps = (m["priority_state"] or "active").strip()
        priority_counts[ps] += 1
        company = (m["company"] or m["company_name"] or "").strip()
        if company:
            company_counts[company] += 1
        title = (m["job_title"] or m["role"] or "").strip()
        if title:
            title_counts[title] += 1
        verified_counts["true" if bool(m["email_verified"]) else "false"] += 1
        attempts = int(m["attempt_count_total"] or 0)
        if s == "replied":
            engagement_counts["replied"] += 1
        elif s == "demo_scheduled":
            engagement_counts["demo_scheduled"] += 1
        elif s == "contacted":
            engagement_counts["contacted_no_reply"] += 1
        elif attempts == 0 and s == "new":
            engagement_counts["not_contacted"] += 1
        pinfo = cprod.get(cid)
        if pinfo:
            _pid, _pname, _pet = pinfo
            e = product_agg.setdefault(_pid, [_pname, _pet, 0])
            e[2] += 1

    # Shape like the legacy product_rows: (product_id, name, entity_type, count).
    product_rows = sorted(
        ((pid, v[0], v[1], v[2]) for pid, v in product_agg.items()),
        key=lambda x: -x[3],
    )

    _STATUS_LABELS = {
        "new": "New",
        "contacted": "Contacted",
        "replied": "Replied",
        "demo_scheduled": "Demo Scheduled",
        "bounced": "Bounced",
        "unsubscribed": "Unsubscribed",
    }

    def _opt(value, label, count):
        return {"value": value, "label": label, "count": int(count)}

    filters: List[Dict[str, Any]] = []

    if status_counts:
        filters.append({
            "id": "status",
            "label": "Status",
            "type": "multi-select",
            "options": [
                _opt(s, _STATUS_LABELS.get(s, s.replace("_", " ").title()), c)
                for s, c in status_counts.most_common()
            ],
        })

    if len(priority_counts) > 1:
        # Only surface when there's a mix — otherwise the filter is noise.
        filters.append({
            "id": "priority_states",
            "label": "Priority",
            "type": "multi-select",
            "options": [
                _opt(ps, ps.replace("_", " ").title(), c)
                for ps, c in priority_counts.most_common()
            ],
        })

    if product_rows:
        # 2026-05-29 — Filter renamed "Product" → "Campaign" + duplicate
        # names MERGED for display per user request: "campaign is
        # different id but name is same then show it as same".
        #
        # DB stays as-is — each New Run still creates its own
        # nexus_products row with a unique product_id. Only the SIDEBAR
        # collapses by (name, entity_type) so the operator sees ONE row
        # per unique name. The `value` is the LOWEST product_id of the
        # group; when the user clicks it, the listing endpoint expands
        # that ID to include all sibling product_ids with the same name
        # (see the expansion block in /journey/leads handler).
        _ETYPE_LABEL = {"product": "Product", "service": "Service", "gcc": "GCC"}
        merged: Dict[tuple, Dict[str, Any]] = {}
        for pr in product_rows:
            pid = int(pr[0])
            name = pr[1] or f"#{pid}"
            etype = (pr[2] or "product").lower()
            cnt = int(pr[3] or 0)
            key = (name.strip().lower(), etype)
            if key not in merged:
                merged[key] = {
                    "value": pid,
                    "label": f"{name} ({_ETYPE_LABEL.get(etype, 'Product')})",
                    "count": 0,
                    "min_pid": pid,
                }
            merged[key]["count"] += cnt
            # Canonical filter value = lowest product_id of the group.
            if pid < merged[key]["min_pid"]:
                merged[key]["min_pid"] = pid
                merged[key]["value"] = pid

        # Sort by combined count desc.
        _options = sorted(
            (
                {"value": m["value"], "label": m["label"], "count": m["count"]}
                for m in merged.values()
            ),
            key=lambda o: -o["count"],
        )
        filters.append({
            "id": "product_ids",
            "label": "Campaign",
            "type": "multi-select",
            "options": _options,
        })

    if company_counts:
        # Cap at 20 — high cardinality lists hurt the sidebar.
        filters.append({
            "id": "companies",
            "label": "Company",
            "type": "multi-select",
            "options": [_opt(c, c, n) for c, n in company_counts.most_common(20)],
        })

    if title_counts:
        filters.append({
            "id": "job_titles",
            "label": "Job title",
            "type": "multi-select",
            "options": [_opt(t, t, n) for t, n in title_counts.most_common(20)],
        })

    # Email verified filter intentionally omitted from the sidebar schema —
    # the underlying column on nexus_global_leads is noisy (most rows are
    # imported pre-verified or never re-verified) so the facet doesn't
    # carry meaningful signal in the GTM Journey UI. `verified_counts`
    # is still computed above in case we want to bring it back later.

    if any(v > 0 for v in engagement_counts.values()):
        filters.append({
            "id": "engagement",
            "label": "Engagement",
            "type": "multi-select",
            "options": [
                _opt(k, k.replace("_", " ").title(), v)
                for k, v in engagement_counts.items() if v > 0
            ],
        })

    payload = {"total": total, "filters": filters}
    _gtm_cache_set(_schema_ck, payload)
    return payload


# ─── GET /leads/{id}/detail ──────────────────────────────────────────────────


@router.get("/leads/{lead_id}/detail")
def journey_lead_detail(
    lead_id: int,
    campaign_id: Optional[int] = Query(
        None,
        description="Scope the detail (campaigns, sequences, timeline) to ONE "
        "campaign. When a lead is enrolled in several campaigns (same person, "
        "different products), this keeps the timeline from mixing products. "
        "Omit to show all of the lead's campaigns (legacy behaviour).",
    ),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Full lead detail with campaigns, sequences, and an interleaved
    timeline of 7 event types. Matches legacy /journey/leads/:id/detail
    shape 1:1."""
    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    if not owned:
        raise HTTPException(status_code=404, detail="Lead not found")

    matching_cids = _campaign_ids_for_lead(db, lead_id, owned)
    if not matching_cids:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Optional single-campaign scope. A lead can be in several campaigns (same
    # person targeted for different products); passing campaign_id shows ONLY
    # that campaign's sequences + timeline so products never mix in the detail.
    if campaign_id is not None:
        if campaign_id not in matching_cids:
            raise HTTPException(status_code=404, detail="Lead not in that campaign")
        matching_cids = [campaign_id]

    lead_row = db.execute(
        text("SELECT * FROM nexus_global_leads WHERE id = :id"),
        {"id": lead_id},
    ).first()
    if not lead_row:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = _lead_row_to_dict(lead_row)

    # Campaigns the lead is enrolled in (intersection with owned).
    campaign_rows = db.execute(
        text(
            """SELECT c.id, c.name, p.source_url, c.created_at
               FROM nexus_campaigns c
               LEFT JOIN nexus_products p ON p.id = c.product_id
               WHERE c.id = ANY(:cids)"""
        ),
        {"cids": matching_cids},
    ).fetchall()
    campaigns = [
        {
            "_id": r[0],
            "name": r[1] or "",
            "product_url": r[2] or "",
            "createdAt": r[3],
        }
        for r in campaign_rows
    ]
    campaign_by_id = {c["_id"]: c for c in campaigns}

    # Lead sequences scoped to the matching campaigns.
    sequences = [
        dict(r._mapping)
        for r in db.execute(
            text(
                """SELECT * FROM nexus_lead_sequences
                   WHERE lead_id = :lid AND campaign_id = ANY(:cids)
                   ORDER BY updated_at DESC"""
            ),
            {"lid": lead_id, "cids": matching_cids},
        ).fetchall()
    ]

    # Attach sequence STEP DEFINITIONS to each enrollment so the
    # frontend can render the actual configured stages (not a hardcoded
    # 2-follow-up slot count). The OutreachFlowPanel used to assume a
    # fixed cadence shape, which silently dropped step 3+ for sequences
    # longer than that (e.g. the "Default 4-step cadence" with a closing
    # email). Each enrollment carries `sequence_id` → nexus_sequences.id;
    # we fetch the `steps` JSONB and inline it as `sequence_steps` on
    # the same dict so the frontend doesn't need a second round-trip.
    seq_def_ids = sorted({
        s.get("sequence_id") for s in sequences
        if s.get("sequence_id") is not None
    })
    sequence_defs_by_id: Dict[int, List[Dict[str, Any]]] = {}
    if seq_def_ids:
        try:
            for r in db.execute(
                text(
                    """SELECT id, name, steps FROM nexus_sequences
                       WHERE id = ANY(:ids)"""
                ),
                {"ids": seq_def_ids},
            ).fetchall():
                rid = r[0]
                raw_steps = r[2] or []
                # Normalise to a stable shape for the frontend:
                # [{order:int, label:str, channel:str}, ...]
                # `label` is derived from subject_template (sans
                # placeholders) or a sensible default per step index.
                normalised: List[Dict[str, Any]] = []
                if isinstance(raw_steps, list):
                    for idx, st in enumerate(raw_steps):
                        if not isinstance(st, dict):
                            continue
                        order = st.get("order", idx)
                        channel = st.get("channel", "email")
                        # Default labels matching the canonical 4-step cadence.
                        # Operators can rename steps via the sequence
                        # editor later; for now we synthesise human-readable
                        # labels so the UI doesn't show "Step 2 / Step 3".
                        if order == 0:
                            label = "Initial Email"
                        elif idx == len(raw_steps) - 1 and len(raw_steps) >= 3:
                            # Final step in a 3+ step sequence is "Closing"
                            label = "Closing Email"
                        else:
                            label = f"Follow-up {order}"
                        # delay_days passed through so the frontend's
                        # OutreachFlowPanel can project ~DD/MM/YYYY dates
                        # for pending steps (FU1 = Initial + delay_days,
                        # FU2 = FU1 + delay_days, etc.). Default to 0 so
                        # older sequence rows without the field still
                        # produce a valid (if collapsed) projection.
                        try:
                            delay_days = int(st.get("delay_days") or 0)
                        except (TypeError, ValueError):
                            delay_days = 0
                        normalised.append(
                            {
                                "order": order,
                                "label": label,
                                "channel": channel,
                                "delay_days": delay_days,
                            }
                        )
                sequence_defs_by_id[rid] = normalised
        except Exception:
            logger.warning("journey: sequence step definitions lookup failed", exc_info=True)
            sequence_defs_by_id = {}

    # Inline the steps onto each enrollment so the frontend has a 1:1
    # mapping from `enrollment.campaign_id` → step labels for that
    # campaign's flow.
    for s in sequences:
        s["sequence_steps"] = sequence_defs_by_id.get(s.get("sequence_id"), [])

    # Outreach rows (legacy "outreaches" — one email send each).
    outreach_rows = db.execute(
        text(
            """SELECT * FROM nexus_outreach
               WHERE lead_id = :lid AND campaign_id = ANY(:cids)
               ORDER BY email_sent_at ASC NULLS LAST, created_at ASC"""
        ),
        {"lid": lead_id, "cids": matching_cids},
    ).fetchall()

    # Touchpoint rows (sequencer-canonical sends, per campaign+step).
    touchpoint_rows = db.execute(
        text(
            """SELECT * FROM nexus_touchpoints
               WHERE lead_id = :lid AND campaign_id = ANY(:cids)
               ORDER BY sent_at ASC NULLS LAST, created_at ASC"""
        ),
        {"lid": lead_id, "cids": matching_cids},
    ).fetchall()

    # Generated-but-not-yet-sent email DRAFTS. The sequencer writes the full
    # 4-email set into nexus_lead_emails as soon as a lead is enrolled — even
    # when the actual send is held (e.g. the mailbox hit its daily cap). We
    # surface those as 'draft' events so the Content tab shows the real copy
    # instead of an empty "No content yet"; a real sent touchpoint for the
    # same step always wins over its draft (deduped below).
    _seq_ids = [s["id"] for s in sequences if s.get("id") is not None]
    lead_email_rows = []
    if _seq_ids:
        lead_email_rows = db.execute(
            text(
                """SELECT lead_sequence_id, campaign_id, step, kind,
                          subject, body, real_result, opener, status, created_at,
                          regenerated_from_message_id
                     FROM nexus_lead_emails
                    WHERE lead_sequence_id = ANY(:sids)
                    ORDER BY step ASC"""
            ),
            {"sids": _seq_ids},
        ).fetchall()

    # Voice calls (cross-campaign for the lead).
    voice_rows = db.execute(
        text(
            "SELECT * FROM nexus_voice_calls WHERE lead_id = :lid ORDER BY created_at ASC"
        ),
        {"lid": lead_id},
    ).fetchall()

    # Most recent LinkedIn message snapshot per variant. We pull the
    # latest DM and the latest InMail separately so both can surface in
    # the lead's timeline (a lead may have one of each — the operator
    # picks which to send based on whether they're a 1st-degree
    # connection). Legacy rows pre-date `variant`; treat NULL as 'dm'.
    #
    # Defensive fallback: the `variant` column is added by phase5's
    # ALTER TABLE step, but on a Lambda cold-start between deploys (or a
    # local backend that hasn't been restarted since the migration was
    # added) the column may not yet exist. In that case the variant-aware
    # query throws UndefinedColumn — we catch it, roll back the aborted
    # transaction, and fall back to the original single-row query that
    # makes no assumptions about the new column. The InMail branch
    # returns no row in that case (there can't be any, since the column
    # doesn't exist yet), and the DM continues to render exactly as it
    # did before this feature was added.
    linkedin_dm_row = None
    linkedin_inmail_row = None
    try:
        linkedin_dm_row = db.execute(
            text(
                """SELECT * FROM nexus_linkedin_messages
                   WHERE lead_id = :lid
                     AND (variant IS NULL OR variant = 'dm')
                   ORDER BY sent_at DESC NULLS LAST, id DESC
                   LIMIT 1"""
            ),
            {"lid": lead_id},
        ).first()
        # InMail is a 4-step cadence (step 0=initial .. 3=closing). Show the
        # ACTIVE draft = the lowest step not yet sent (linkedin_message_urn is
        # set only on a real send). As each step is sent, the next becomes the
        # visible draft. Unsent rows (urn NULL) sort first, then by step.
        linkedin_inmail_row = db.execute(
            text(
                """SELECT * FROM nexus_linkedin_messages
                   WHERE lead_id = :lid
                     AND variant = 'inmail'
                   ORDER BY (linkedin_message_urn IS NOT NULL), step ASC, id ASC
                   LIMIT 1"""
            ),
            {"lid": lead_id},
        ).first()
    except Exception as exc:  # noqa: BLE001
        # Most likely UndefinedColumn — phase5 ALTER not yet applied.
        # Clear the aborted transaction so subsequent queries on this
        # session don't all 500 with "current transaction is aborted".
        logger.warning(
            "linkedin variant query failed (likely pre-migration DB) — "
            "falling back to legacy single-row query: %s", exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        try:
            linkedin_dm_row = db.execute(
                text(
                    """SELECT * FROM nexus_linkedin_messages
                       WHERE lead_id = :lid
                       ORDER BY sent_at DESC NULLS LAST, id DESC
                       LIMIT 1"""
                ),
                {"lid": lead_id},
            ).first()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            linkedin_dm_row = None
        linkedin_inmail_row = None

    # Inbound thread items for the lead's email (best-effort).
    inbound_items: List[Dict[str, Any]] = []
    try:
        email_lower = (lead.get("email") or "").lower()
        if email_lower:
            # `nexus_inbound_leads` is keyed (workspace_id, email) — the same
            # person legitimately has ONE ROW PER WORKSPACE. Matching on email
            # alone and taking .first() picked an arbitrary row, so a lead
            # targeted from two workspaces had its replies read off the wrong
            # one: the threads found belonged to the other workspace's
            # sequences, the campaign scope below then filtered them all out,
            # and Content rendered no replies at all. Scope to this workspace
            # and accept every matching row.
            inb_ids = [
                r[0]
                for r in db.execute(
                    text(
                        "SELECT id FROM nexus_inbound_leads "
                        "WHERE email = :em AND workspace_id = :ws"
                    ),
                    {"em": email_lower, "ws": wid},
                ).fetchall()
            ]
            if inb_ids:
                # Scope the Content (and thus the Flow timeline built from it) to
                # the clicked campaign's sequences so a lead enrolled in several
                # PRODUCT campaigns doesn't mix products in the detail. When no
                # campaign_id was passed (legacy "all campaigns" view), fall back
                # to every thread for the lead.
                _seq_ids = [int(s["id"]) for s in sequences if s.get("id") is not None]
                if campaign_id is not None:
                    threads = db.execute(
                        text(
                            """SELECT * FROM nexus_inbound_threads
                               WHERE lead_id = ANY(:lids)
                                 AND lead_sequence_id = ANY(:sids)
                               ORDER BY last_message_at ASC"""
                        ),
                        {"lids": inb_ids, "sids": _seq_ids},
                    ).fetchall()
                else:
                    threads = db.execute(
                        text(
                            """SELECT * FROM nexus_inbound_threads
                               WHERE lead_id = ANY(:lids)
                               ORDER BY last_message_at ASC"""
                        ),
                        {"lids": inb_ids},
                    ).fetchall()
                if threads:
                    thread_ids = [t._mapping["id"] for t in threads]
                    thread_by_id = {t._mapping["id"]: t._mapping for t in threads}
                    messages = db.execute(
                        text(
                            """SELECT * FROM nexus_inbound_messages
                               WHERE thread_id = ANY(:tids)
                               ORDER BY received_at ASC"""
                        ),
                        {"tids": thread_ids},
                    ).fetchall()
                    for m in messages:
                        mm = m._mapping
                        t = thread_by_id.get(mm["thread_id"]) or {}
                        inbound_items.append(
                            {
                                "thread_id": mm["thread_id"],
                                "thread_subject": (t.get("subject") if t else "") or mm.get("subject") or "",
                                "direction": mm.get("direction"),
                                "intent": mm.get("intent") or "",
                                "subject": mm.get("subject") or "",
                                "body": mm.get("body_text") or "",
                                "occurred_at": mm.get("received_at"),
                                "message_id": mm.get("id"),
                            }
                        )
    except Exception:  # noqa: BLE001
        db.rollback()
        inbound_items = []

    # Referral leads spun off from one of this lead's replies (see
    # create_referral_leads / plan.md §7) — keyed by the inbound message that
    # named them, so the Flow tab can show "-> New lead created for X" on the
    # exact reply that triggered it (Shape 3, flow_tab_dynamic_plan.md §6a).
    referral_by_message_id: Dict[int, Dict[str, Any]] = {}
    try:
        _msg_ids = [i["message_id"] for i in inbound_items if i.get("message_id")]
        if _msg_ids:
            for rl in db.execute(
                text(
                    """SELECT id, first_name, last_name, email, source_message_id
                         FROM nexus_global_leads WHERE source_message_id = ANY(:mids)"""
                ),
                {"mids": _msg_ids},
            ).fetchall():
                rm = rl._mapping
                name = (f"{rm.get('first_name') or ''} {rm.get('last_name') or ''}").strip()
                referral_by_message_id[int(rm["source_message_id"])] = {
                    "lead_id": rm["id"],
                    "name": name or rm.get("email"),
                    "email": rm.get("email"),
                }
    except Exception:  # noqa: BLE001
        db.rollback()
        referral_by_message_id = {}

    # ── Build timeline ────────────────────────────────────────────────────
    timeline: List[Dict[str, Any]] = []

    # Sequencer-sent emails are represented by Touchpoints; if a campaign
    # has any email Touchpoint, skip its Outreach email entries to avoid
    # duplicates (the sequencer writes BOTH for the same send).
    email_tp_campaign_ids = {
        tp._mapping["campaign_id"]
        for tp in touchpoint_rows
        if tp._mapping.get("channel") == "email" and tp._mapping.get("campaign_id") is not None
    }

    # `nexus_outreach.reply_text` is a one-time migration artifact carried
    # over from the legacy pre-Postgres system (backfill_outreach.py /
    # nexus_data_migration.py) — it is never written by the live app. The
    # modern `nexus_inbound_messages` thread is the real, ongoing source of
    # truth. For a lead migrated from the old system, the SAME reply can
    # exist in both places (the legacy text plus the live poller re-seeing
    # the same email), which rendered as two identical "Reply from X" cards.
    # Once a lead has ANY real inbound message in the modern table, the
    # legacy mirror is redundant — show it only as a fallback for
    # pre-migration leads that haven't replied since.
    _has_modern_inbound = any(ii.get("direction") == "inbound" for ii in inbound_items)

    for o in outreach_rows:
        om = o._mapping
        cid = om.get("campaign_id")
        if cid not in email_tp_campaign_ids:
            timeline.append(
                {
                    "type": "email_outreach",
                    "channel": "email",
                    "campaign_id": cid,
                    "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                    "status": om.get("status"),
                    "subject": om.get("subject") or "",
                    "body": _clean_email_content(om.get("email_html")),
                    "reply_text": om.get("reply_text") or "",
                    "occurred_at": om.get("email_sent_at") or om.get("created_at"),
                }
            )
        if om.get("reply_text") and not _has_modern_inbound:
            timeline.append(
                {
                    "type": "email_reply",
                    "channel": "email",
                    "campaign_id": cid,
                    "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                    "status": "replied",
                    "subject": om.get("subject") or "",
                    "body": om.get("reply_text") or "",
                    "occurred_at": om.get("updated_at") or om.get("created_at"),
                }
            )

    # Email touchpoints can have MULTIPLE rows per (campaign, step) when a send
    # FAILED and was later retried — rendering both produces a spurious "Draft"
    # duplicate of the sent email AND double-counts the step ("2 emails" for one
    # initial send). Collapse email touchpoints to ONE row per (campaign, step),
    # preferring a 'sent' attempt over a failed one (then the latest by id).
    # LinkedIn / voice touchpoints are per-touch, so they pass through unchanged.
    _best_email_tp: Dict[Any, Any] = {}
    _passthrough_tps = []
    for tp in touchpoint_rows:
        tm = tp._mapping
        if (tm.get("channel") or "email").lower() in ("linkedin", "voice"):
            _passthrough_tps.append(tp)
            continue
        key = (tm.get("campaign_id"), tm.get("step") or 0)
        cur = _best_email_tp.get(key)
        if cur is None:
            _best_email_tp[key] = tp
            continue
        cur_sent = (cur._mapping.get("status") or "") == "sent"
        new_sent = (tm.get("status") or "") == "sent"
        if (new_sent and not cur_sent) or (
            new_sent == cur_sent and (tm.get("id") or 0) > (cur._mapping.get("id") or 0)
        ):
            _best_email_tp[key] = tp

    for tp in _passthrough_tps + list(_best_email_tp.values()):
        tm = tp._mapping
        channel = (tm.get("channel") or "email").lower()
        if channel == "linkedin":
            t_type = "linkedin_message"
        elif channel == "voice":
            t_type = "voice_call"
        else:
            t_type = "followup_email" if (tm.get("step") or 0) > 0 else "email_outreach"
        cid = tm.get("campaign_id")
        timeline.append(
            {
                "type": t_type,
                "channel": channel,
                "campaign_id": cid,
                "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                "status": tm.get("status") or "sent",
                "subject": tm.get("subject") or "",
                "body": tm.get("body_snapshot") or tm.get("body") or "",
                "error": tm.get("error_msg") or tm.get("error") or "",
                "step": tm.get("step"),
                "occurred_at": tm.get("sent_at") or tm.get("created_at"),
            }
        )

    # The existing touchpoint path adds a `linkedin_message` event when
    # there's a touchpoint row for an actually-sent DM. We keep that
    # behaviour for the DM variant (don't double-add) but always surface
    # an InMail draft if one exists — InMails aren't touchpoint-tracked
    # yet, so there's no duplicate-risk path to guard against.
    has_linkedin_from_tp = any(t["type"] == "linkedin_message" for t in timeline)
    if not has_linkedin_from_tp and linkedin_dm_row is not None:
        lm = linkedin_dm_row._mapping
        # Real send = LinkedIn URN present. Drive BOTH `status` and `sent_at` off
        # it (like the email path) so the Content tab's Draft/Sent badge — which
        # reads `status === 'sent'` / `sent_at` — flips correctly. `sent_at` is
        # gated on the URN because the column server-defaults to row-creation time
        # (every draft has one), so it can't be trusted on its own.
        _lm_urn = lm.get("linkedin_message_urn") or ""
        _lm_sent = bool(_lm_urn)
        # A bare connection (no note) stamps a 'gtm-li-nonote:' marker — the
        # connection IS a touch, but the note text was never delivered, so the
        # Content tab must say "Connection sent (no note)", not "Sent".
        _note_sent = _lm_sent and not _lm_urn.startswith("gtm-li-nonote")
        timeline.append(
            {
                "type": "linkedin_message",
                "channel": "linkedin",
                "campaign_id": None,
                "campaign_name": "",
                "status": "sent" if _note_sent else ("connection_no_note" if _lm_sent else "draft"),
                # `sent`/`sent_at` reflect a COUNTED touch (message delivered).
                # A bare no-note connection is NOT a counted touch, so both stay
                # falsy even though the invite went out (status carries that).
                "note_sent": _note_sent,
                "body": lm.get("body") or "",
                # New fields — frontend uses `variant` to label the event
                # (LinkedIn Message vs LinkedIn InMail) and `subject` to
                # show the InMail's headline. DMs carry an empty subject.
                "variant": lm.get("variant") or "dm",
                "subject": lm.get("subject") or "",
                "occurred_at": lm.get("sent_at"),
                "sent_at": lm.get("sent_at") if _note_sent else None,
                "sent": _note_sent,
            }
        )
    if linkedin_inmail_row is not None:
        im = linkedin_inmail_row._mapping
        _im_sent = bool(im.get("linkedin_message_urn"))
        timeline.append(
            {
                "type": "linkedin_inmail",
                "channel": "linkedin",
                "campaign_id": None,
                "campaign_name": "",
                "status": "sent" if _im_sent else "draft",
                "body": im.get("body") or "",
                "variant": "inmail",
                "subject": im.get("subject") or "",
                "occurred_at": im.get("sent_at"),
                "sent_at": im.get("sent_at") if _im_sent else None,
                "sent": _im_sent,
            }
        )

    for vc in voice_rows:
        vm = vc._mapping
        cid = vm.get("campaign_id")
        timeline.append(
            {
                "type": "voice_call",
                "channel": "voice",
                "campaign_id": cid,
                "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                "status": vm.get("status") or "",
                "outcome": vm.get("outcome") or "",
                "subject": f"Voice call to {vm.get('to_number') or ''}".strip(),
                "body": vm.get("last_speech_result") or "",
                "occurred_at": vm.get("started_at") or vm.get("created_at"),
                "transcript": vm.get("transcript") or [],
            }
        )

    for im in inbound_items:
        _referral = referral_by_message_id.get(im.get("message_id"))
        timeline.append(
            {
                "type": "inbound_message" if im["direction"] == "inbound" else "outbound_message",
                "channel": "email",
                "campaign_id": None,
                "campaign_name": "",
                "status": im.get("intent") or im.get("direction"),
                "subject": im.get("subject") or im.get("thread_subject") or "",
                "body": im.get("body") or "",
                "intent": im.get("intent") or "",
                "occurred_at": im.get("occurred_at"),
                "referral": _referral,
                # Lets the frontend tie a rewritten follow-up back to the exact
                # reply that triggered it (regenerated_from_message_id above),
                # and pair an outbound ack with the reply it answers.
                "message_id": im.get("message_id"),
            }
        )

    # Calendar RSVP outcomes (accepted / declined) as clean journey markers. The
    # RSVP email itself is filtered upstream (never ingested as a reply), so we
    # surface the OUTCOME recorded on the booking instead — a real buying signal.
    try:
        rsvp_rows = db.execute(
            text(
                """SELECT id, status, meta, scheduled_at, updated_at
                     FROM nexus_demo_bookings
                    WHERE lead_id = :lid AND workspace_id = :ws
                      AND meta->>'rsvp' IS NOT NULL"""
            ),
            {"lid": lead_id, "ws": wid},
        ).fetchall()
        for r in rsvp_rows:
            rm = r._mapping
            _meta = rm["meta"] if isinstance(rm["meta"], dict) else {}
            _rsvp = _meta.get("rsvp")
            if _rsvp == "accepted":
                _title = "Prospect accepted the invite ✓"
            elif _rsvp in ("declined", "cancelled"):
                _title = "Prospect declined the invite"
            else:
                _title = "Prospect tentatively accepted the invite"
            timeline.append(
                {
                    "type": "calendar_response",
                    "channel": "email",
                    "campaign_id": None,
                    "campaign_name": "",
                    "status": _rsvp,
                    "subject": _title,
                    "body": "",
                    "occurred_at": rm["updated_at"] or rm["scheduled_at"],
                }
            )
    except Exception:  # noqa: BLE001
        logger.debug("journey: calendar RSVP marker skipped", exc_info=True)

    # Show ONLY the NEXT message in the cadence as a draft — i.e. the email
    # for each sequence's CURRENT step — not the whole future cadence. Steps
    # already sent appear above as their real touchpoints; steps the lead
    # hasn't reached yet stay hidden until they become the next message. A
    # real touchpoint for the current step wins over its draft.
    _sent_email_steps = set()
    for tp in touchpoint_rows:
        tm = tp._mapping
        if (tm.get("channel") or "").lower().startswith("email"):
            _sent_email_steps.add((tm.get("campaign_id"), tm.get("step") or 0))
    _le_by_step = {}
    for le in lead_email_rows:
        lm = le._mapping
        _le_by_step[(lm.get("lead_sequence_id"), lm.get("step") or 0)] = lm

    # Branded-HTML preview: render the email with the SAME production renderer
    # the sequencer sends with — _render_branded_step → render_rich_email
    # (branded header + role centerpiece + CTA + signature) — from the stored
    # generated content, so the UI preview MATCHES the delivered email (not the
    # lighter fallback template). Cached per campaign; never raises.
    _ctx_cache: Dict[int, Any] = {}
    _prod_cache: Dict[int, Any] = {}

    def _load_ctx(_cid):
        if _cid in _ctx_cache:
            return _prod_cache.get(_cid), _ctx_cache[_cid]
        prod = None
        ctx = None
        try:
            # Same builder the SEND path uses, so the preview and the delivered
            # email can never disagree. This was previously a hand-rolled copy
            # that read only the product's brand overrides — so a campaign-level
            # representative showed as "<Company> Team" here even once the send
            # path had it right, which is exactly how the bug stayed hidden.
            from nexus.services.sequencer import (
                _build_sender_ctx_for_product,
                _load_product,
            )
            prod = _load_product(db, wid, _cid) or {}
            ctx = _build_sender_ctx_for_product(db, prod)
        except Exception:
            prod, ctx = None, None
        _prod_cache[_cid] = prod
        _ctx_cache[_cid] = ctx
        return prod, ctx

    def _gemini_for_campaign(_cid):
        g = {}
        for _le in lead_email_rows:
            _m = _le._mapping
            if _m.get("campaign_id") != _cid:
                continue
            _s = _m.get("step") or 0
            if _s == 0:
                g["subject"] = _m.get("subject") or ""
                g["intro_body"] = _m.get("body") or ""
                g["real_result"] = _m.get("real_result") or ""
                g["personalized_opener"] = _m.get("opener") or ""
            elif _s == 1:
                g["followup_subject"] = _m.get("subject") or ""
                g["followup_body"] = _m.get("body") or ""
            elif _s == 2:
                g["followup2_subject"] = _m.get("subject") or ""
                g["followup2_body"] = _m.get("body") or ""
            elif _s == 3:
                g["closing_subject"] = _m.get("subject") or ""
                g["closing_body"] = _m.get("body") or ""
        return g

    def _branded_html(_cid, _step):
        try:
            from types import SimpleNamespace
            from nexus.services.sequencer import (
                _f1_enabled, _render_branded_step, _render_f1_step,
            )
            from nexus.services.outreach_template import render_email
            prod, ctx = _load_ctx(_cid)
            if not ctx:
                return ""
            _first = (
                lead.get("first_name")
                or (lead.get("name") or "").split(" ")[0]
                or "there"
            )
            lead_dict = {
                "first_name": _first,
                "title": lead.get("job_title") or lead.get("role") or "",
                "company_name": lead.get("company") or "",
                "company_domain": lead.get("company_domain") or "",
            }
            # The generated content row for this campaign+step.
            _row = None
            for _le in lead_email_rows:
                _m = _le._mapping
                if _m.get("campaign_id") == _cid and (_m.get("step") or 0) == int(_step):
                    _row = SimpleNamespace(
                        subject=_m.get("subject") or "",
                        body=_m.get("body") or "",
                        real_result=_m.get("real_result"),
                        opener=_m.get("opener"),
                    )
                    break
            # PRIMARY renderer — must mirror process_due_sequences' branch order
            # EXACTLY, or the Content tab shows a different email than the one
            # actually delivered. This preview previously called
            # _render_branded_step directly, so it kept showing the legacy
            # template after the sequencer moved to f1.
            if prod and _row is not None:
                if _f1_enabled():
                    try:
                        art = _render_f1_step(prod, lead_dict, ctx, _row, int(_step))
                        if art and art.get("html"):
                            return art["html"]
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    art = _render_branded_step(db, prod, lead_dict, ctx, _row, int(_step))
                    if art and art.get("html"):
                        return art["html"]
                except Exception:  # noqa: BLE001
                    pass
            art2 = render_email(
                lead=lead_dict, sender=ctx,
                gemini=_gemini_for_campaign(_cid), enrichment=None, step=int(_step),
            )
            return art2.get("html") or ""
        except Exception:  # noqa: BLE001
            return ""

    for s in sequences:
        _sid = s.get("id")
        _cur = s.get("current_step") or 0
        lm = _le_by_step.get((_sid, _cur))
        if not lm:
            continue
        if (lm.get("campaign_id"), _cur) in _sent_email_steps:
            continue  # the next step already went out — its touchpoint shows it
        if not (lm.get("subject") or lm.get("body")):
            continue  # nothing generated for this step yet
        cid = lm.get("campaign_id")
        # Assemble the FULL message exactly as it will be DELIVERED — body +
        # case-study line + CTA + signature — so the Draft preview matches the
        # email that actually goes out, not just the intro paragraph. (The
        # initial email carries the case study; follow-ups/closing don't, and
        # the closing has no CTA — mirror the send template's per-step rules.)
        _is_initial = _cur == 0
        _is_closing = _cur == 3
        _dparts = []
        if (lm.get("body") or "").strip():
            _dparts.append(lm["body"].strip())
        if _is_initial and (lm.get("real_result") or "").strip():
            _dparts.append(lm["real_result"].strip())
        _cta_url = (os.getenv("NEXUS_DEFAULT_CTA_URL", "") or "").strip()
        _cta_label = (os.getenv("NEXUS_DEFAULT_CTA_LABEL", "") or "").strip() or "Book a quick call"
        if _cta_url and not _is_closing:
            _dparts.append(f"{_cta_label}: {_cta_url}")
        _company = ((campaign_by_id.get(cid) or {}).get("name", "") or "").split(" #")[0].strip()
        # Sign the preview with the product's representative so the Content tab
        # matches the delivered email. This block hardcoded "<Company> Team",
        # which stayed wrong even after a rep was configured.
        _sig_prod, _sig_ctx = _load_ctx(cid)
        _sig_name = ((_sig_ctx or {}).get("rep_name") or "").strip()
        _sig_title = ((_sig_ctx or {}).get("rep_title") or "").strip()
        if _sig_name:
            _dparts.append(
                "Best regards,\n" + _sig_name + (f"\n{_sig_title}" if _sig_title else "")
            )
        else:
            _dparts.append("Best regards,\n" + (f"{_company} Team" if _company else "The team"))
        _draft_body = "\n\n".join(_dparts)
        timeline.append(
            {
                "type": "followup_email" if _cur > 0 else "email_outreach",
                "channel": "email",
                "campaign_id": cid,
                "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                "status": "draft",
                "subject": lm.get("subject") or "",
                "body": _draft_body,
                "html": _branded_html(cid, _cur),
                "step": _cur,
                "occurred_at": lm.get("created_at"),
                # True when this step's content was rewritten in response to a
                # reply (regenerate_pending_followups), not the original
                # enrollment-time draft — drives the "adapted" marker in the
                # Flow tab's tail (Shape 1, flow_tab_dynamic_plan.md §6a).
                "regenerated": bool(lm.get("regenerated_from_message_id")),
                # The specific inbound reply that triggered the rewrite, so the
                # UI can draw an explicit link instead of relying on adjacency
                # (email_flow_plan_2.md §3c).
                "regenerated_from_message_id": lm.get("regenerated_from_message_id"),
            }
        )

    # Attach the branded HTML preview to EVERY email event that doesn't have it
    # yet (the sent touchpoints) — rendered from the generated content for that
    # step — so sent emails (e.g. the initial) also show the HTML/CSS preview,
    # not just the upcoming draft.
    for _ev in timeline:
        if (
            _ev.get("type") in ("email_outreach", "followup_email")
            and not _ev.get("html")
        ):
            _ev["html"] = _branded_html(_ev.get("campaign_id"), _ev.get("step") or 0)

    # Placeholder events when no real ones exist.
    email_in_timeline = any(
        t["type"] in ("email_outreach", "followup_email") for t in timeline
    )
    if not email_in_timeline:
        if not lead.get("email"):
            timeline.append(
                {
                    "type": "email_outreach",
                    "channel": "email",
                    "campaign_id": None,
                    "campaign_name": "",
                    "status": "unavailable",
                    "subject": "",
                    "body": "",
                    "occurred_at": lead.get("createdAt"),
                }
            )
        elif sequences:
            active_seq = next(
                (s for s in sequences if (s.get("status") or "") in ("active", "processing")),
                None,
            )
            if active_seq:
                cid = active_seq.get("campaign_id")
                timeline.append(
                    {
                        "type": "email_outreach",
                        "channel": "email",
                        "campaign_id": cid,
                        "campaign_name": (campaign_by_id.get(cid) or {}).get("name", ""),
                        "status": "queued",
                        "subject": "",
                        "body": "",
                        "occurred_at": active_seq.get("next_action_at") or active_seq.get("created_at"),
                    }
                )

    linkedin_in_timeline = any(t["type"] == "linkedin_message" for t in timeline)
    if not linkedin_in_timeline and not lead.get("linkedin_url"):
        timeline.append(
            {
                "type": "linkedin_message",
                "channel": "linkedin",
                "campaign_id": None,
                "campaign_name": "",
                "status": "unavailable",
                "body": "",
                # Schema parity with the real DM / InMail events so the
                # frontend gets a uniform payload regardless of whether
                # the row was found or stubbed.
                "variant": "dm",
                "subject": "",
                "occurred_at": lead.get("createdAt"),
            }
        )

    # Normalize mixed naive/aware datetimes so sort() doesn't crash.
    # Some columns are TIMESTAMP (naive), others TIMESTAMPTZ (aware).
    _epoch = datetime(1970, 1, 1)
    def _sort_key(t):
        v = t.get("occurred_at")
        if v is None:
            return _epoch
        # If aware, strip tzinfo to match the naive default.
        if hasattr(v, "tzinfo") and v.tzinfo is not None:
            return v.replace(tzinfo=None)
        return v
    timeline.sort(key=_sort_key)

    # Total LinkedIn touches actually sent (each sent action = 1 touch), so the
    # detail tile matches the Lead-list TOUCHES column. The timeline only carries
    # the latest DM + InMail snapshot, so this can't be counted client-side.
    linkedin_touches = 0
    try:
        linkedin_touches = int(db.execute(
            text("SELECT COUNT(*) FROM nexus_linkedin_messages "
                 "WHERE lead_id = :lid AND direction = 'outbound' "
                 "AND linkedin_message_urn IS NOT NULL "
                 "AND linkedin_message_urn NOT LIKE 'gtm-li-nonote:%'"),
            {"lid": lead_id},
        ).scalar() or 0)
    except Exception:  # noqa: BLE001 — pre-migration DB / column missing
        linkedin_touches = 0

    # Real "when was the demo actually booked" timestamp — same fix already
    # applied to journey_leads() (see the comment there, :959-963). Without
    # this, OutreachFlowPanel's Demo Booked date falls back to
    # last_contacted_at, which collapses it with the last email-sent time.
    demo_booked_at = None
    demo_scheduled_at = None
    try:
        booking_row = db.execute(
            text(
                """
                SELECT created_at, scheduled_at FROM nexus_demo_bookings
                 WHERE lead_id = :lid ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"lid": lead_id},
        ).first()
        if booking_row:
            demo_booked_at, demo_scheduled_at = booking_row[0], booking_row[1]
    except Exception:  # noqa: BLE001 — pre-migration DB / table missing
        logger.warning("journey detail: demo booking lookup failed", exc_info=True)

    return {
        "linkedin_touches": linkedin_touches,
        "lead": {
            "_id": lead["id"],
            "email": lead.get("email"),
            "name": lead.get("name") or "",
            "company": lead.get("company") or "",
            "company_domain": lead.get("company_domain") or "",
            "job_title": lead.get("job_title") or "",
            "linkedin_url": lead.get("linkedin_url") or "",
            "email_verified": bool(lead.get("email_verified")),
            "email_verify_status": lead.get("email_verify_status") or "",
            "status": lead.get("status") or "new",
            "priority_state": lead.get("priority_state") or "active",
            "attempt_count_total": lead.get("attempt_count_total") or 0,
            "channel_attempts": lead.get("channel_attempts")
            or {"email": 0, "linkedin": 0, "voice": 0},
            "last_contacted_at": lead.get("last_contacted_at"),
            "last_attempt_at": lead.get("last_attempt_at"),
            "last_attempt_channel": lead.get("last_attempt_channel"),
            "created_at": lead.get("createdAt"),
            "demo_booked_at": demo_booked_at,
            "demo_scheduled_at": demo_scheduled_at,
        },
        "campaigns": campaigns,
        "sequences": sequences,
        "timeline": timeline,
    }


# ─── POST /leads/{id}/hide ───────────────────────────────────────────────────


@router.post("/leads/{lead_id}/hide")
def journey_hide_lead(
    lead_id: int,
    body: Optional[Dict[str, Any]] = Body(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    if not owned or not _campaign_ids_for_lead(db, lead_id, owned):
        raise HTTPException(status_code=404, detail="Lead not found")

    reason = (body or {}).get("reason") or "manual_hide"
    reason = str(reason)[:180]
    db.execute(
        text(
            """UPDATE nexus_global_leads
               SET priority_state = 'hidden',
                   hidden_reason = :reason,
                   hidden_at = NOW(),
                   updated_at = NOW()
               WHERE id = :id"""
        ),
        {"id": lead_id, "reason": reason},
    )
    db.commit()
    try:
        gtm_cache_invalidate(extract_workspace_id(workspace))
    except Exception:
        pass
    return {"ok": True, "lead_id": lead_id, "priority_state": "hidden"}


# ─── POST /reconcile-outreach ────────────────────────────────────────────────
# Refresh-triggered "catch up stuck leads": enroll any qualified lead that
# hasn't been started yet + kick draft generation. Lets the GTM Journey Refresh
# button start outreach for leads the auto-path missed, without a separate click.


@router.post("/reconcile-outreach")
def journey_reconcile_outreach(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Enroll every ACCEPTED lead in this workspace that has no sequence yet,
    then generate their cadence drafts in the background. Idempotent — leads
    already started are untouched (no duplicate enrolls/sends). Sending stays
    paced by the sequencer + the mailbox daily cap.
    """
    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    if not owned:
        return {"ok": True, "enrolled": 0, "campaigns": 0}

    from nexus.services.lead_discovery import _enroll_in_sequence
    from nexus.services.sequencer import prime_drafts_for_campaign_bg

    # Accepted leads (incl. approved / fail-open) with NO sequence row yet.
    rows = db.execute(
        text(
            """SELECT n.global_lead_id AS gid, n.campaign_id AS cid
                 FROM nexus_leads n
                WHERE n.campaign_id = ANY(:cids)
                  AND COALESCE(n.signals -> 'intent' ->> 'status', '')
                      IN ('accepted', 'approved', 'included_failopen')
                  AND NOT EXISTS (
                      SELECT 1 FROM nexus_lead_sequences ls
                       WHERE ls.lead_id = n.global_lead_id
                         AND ls.campaign_id = n.campaign_id)"""
        ),
        {"cids": owned},
    ).mappings().all()

    enrolled = 0
    touched: set = set()
    for r in rows:
        try:
            _enroll_in_sequence(
                db, workspace_id=wid,
                campaign_id=r["cid"], global_lead_id=r["gid"],
            )
            enrolled += 1
            touched.add(r["cid"])
        except Exception:  # noqa: BLE001
            logger.exception("reconcile-outreach: enroll failed gid=%s", r["gid"])

    # Draft generation off-thread so Refresh returns immediately; the sequencer
    # then sends on its next pass, paced by mailbox availability.
    for cid in touched:
        background_tasks.add_task(prime_drafts_for_campaign_bg, cid)

    try:
        gtm_cache_invalidate(wid)
    except Exception:
        pass
    return {"ok": True, "enrolled": enrolled, "campaigns": len(touched)}


# ─── POST /leads/{id}/unhide ─────────────────────────────────────────────────


@router.post("/leads/{lead_id}/unhide")
def journey_unhide_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    if not owned or not _campaign_ids_for_lead(db, lead_id, owned):
        raise HTTPException(status_code=404, detail="Lead not found")

    db.execute(
        text(
            """UPDATE nexus_global_leads
               SET priority_state = 'active',
                   hidden_reason = NULL,
                   hidden_at = NULL,
                   updated_at = NOW()
               WHERE id = :id"""
        ),
        {"id": lead_id},
    )
    db.commit()
    try:
        gtm_cache_invalidate(extract_workspace_id(workspace))
    except Exception:
        pass
    return {"ok": True, "lead_id": lead_id, "priority_state": "active"}


# ─── POST /leads/{id}/attempt ────────────────────────────────────────────────


@router.post("/leads/{lead_id}/attempt")
def journey_record_attempt(
    lead_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
    user: Any = Depends(get_nexus_user),
):
    channel = str(body.get("channel") or "").lower()
    if channel not in ("email", "linkedin", "voice"):
        raise HTTPException(
            status_code=400, detail="channel must be 'email', 'linkedin', or 'voice'"
        )

    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    matching = _campaign_ids_for_lead(db, lead_id, owned)
    if not matching:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_row = db.execute(
        text("SELECT * FROM nexus_global_leads WHERE id = :id"),
        {"id": lead_id},
    ).first()
    if not lead_row:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = _lead_row_to_dict(lead_row)

    # Increment attempt counters atomically.
    db.execute(
        text(
            f"""UPDATE nexus_global_leads
                SET attempt_count_total = COALESCE(attempt_count_total, 0) + 1,
                    channel_attempts = jsonb_set(
                        COALESCE(channel_attempts, '{{}}'::jsonb),
                        ARRAY['{channel}'],
                        to_jsonb(COALESCE((channel_attempts->>'{channel}')::int, 0) + 1),
                        true
                    ),
                    last_attempt_at = NOW(),
                    last_attempt_channel = :ch,
                    status = CASE WHEN status = 'new' THEN 'contacted' ELSE status END,
                    updated_at = NOW()
                WHERE id = :id"""
        ),
        {"id": lead_id, "ch": channel},
    )

    # Write a unified Touchpoint row so the timeline reflects the manual
    # attempt across channels.
    primary_cid = matching[0]
    user_id = extract_user_id(user)
    db.execute(
        text(
            """INSERT INTO nexus_touchpoints
                   (workspace_id, user_id, lead_id, campaign_id, step, channel,
                    subject, body_snapshot, status, sent_at, error_msg, created_at)
               VALUES
                   (:wid, :uid, :lid, :cid, 0, :ch,
                    :subj, '', 'sent', NOW(), '', NOW())"""
        ),
        {
            "wid": wid,
            "uid": user_id,
            "lid": lead_id,
            "cid": primary_cid,
            "ch": channel,
            "subj": f"Manual {channel} attempt",
        },
    )

    # Re-read to decide on auto-hide.
    after = db.execute(
        text(
            "SELECT id, status, priority_state, attempt_count_total, channel_attempts "
            "FROM nexus_global_leads WHERE id = :id"
        ),
        {"id": lead_id},
    ).first()._mapping

    auto_hidden = False
    if (
        (after.get("status") or "") in ("new", "contacted")
        and (after.get("priority_state") or "active") != "hidden"
        and (after.get("attempt_count_total") or 0) >= 3
    ):
        db.execute(
            text(
                """UPDATE nexus_global_leads
                   SET priority_state = 'hidden',
                       hidden_reason = 'auto_hidden_after_3_attempts',
                       hidden_at = NOW(),
                       updated_at = NOW()
                   WHERE id = :id"""
            ),
            {"id": lead_id},
        )
        auto_hidden = True
    db.commit()
    try:
        gtm_cache_invalidate(extract_workspace_id(workspace))
    except Exception:
        pass

    final = db.execute(
        text(
            "SELECT priority_state, attempt_count_total, channel_attempts "
            "FROM nexus_global_leads WHERE id = :id"
        ),
        {"id": lead_id},
    ).first()._mapping

    return {
        "ok": True,
        "lead_id": lead_id,
        "channel": channel,
        "attempt_count_total": final.get("attempt_count_total") or 0,
        "channel_attempts": final.get("channel_attempts") or {"email": 0, "linkedin": 0, "voice": 0},
        "priority_state": final.get("priority_state") or "active",
        "auto_hidden": auto_hidden,
    }


# ─── POST /actions/auto-hide-stale ───────────────────────────────────────────


@router.post("/actions/auto-hide-stale")
def journey_auto_hide_stale(
    body: Optional[Dict[str, Any]] = Body(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    min_attempts = int((body or {}).get("min_attempts") or 3)
    min_attempts = max(1, min(20, min_attempts))

    wid = extract_workspace_id(workspace) or 0
    owned = _owned_campaign_ids(db, wid)
    if not owned:
        return {"hidden": 0, "checked": 0, "min_attempts": min_attempts}

    lead_ids = [
        r[0]
        for r in db.execute(
            text(
                "SELECT DISTINCT global_lead_id FROM nexus_leads "
                "WHERE campaign_id = ANY(:cids) "
                # Rejected (not-picked) leads are thrown out of the journey.
                "AND COALESCE(signals -> 'intent' ->> 'status', '') <> 'rejected'"
            ),
            {"cids": owned},
        ).fetchall()
    ]
    if not lead_ids:
        return {"hidden": 0, "checked": 0, "min_attempts": min_attempts}

    candidates = db.execute(
        text(
            """SELECT id, status, priority_state, attempt_count_total, channel_attempts
               FROM nexus_global_leads
               WHERE id = ANY(:lids)
                 AND (priority_state IS NULL OR priority_state <> 'hidden')
                 AND status IN ('new', 'contacted')"""
        ),
        {"lids": lead_ids},
    ).fetchall()
    if not candidates:
        return {"hidden": 0, "checked": 0, "min_attempts": min_attempts}

    derived = _collect_attempt_stats(
        db, owned, [c._mapping["id"] for c in candidates], workspace_id=wid,
    )

    to_hide: List[int] = []
    for c in candidates:
        lead = _lead_row_to_dict(c)
        merged = _with_journey_derived(lead, derived.get(lead["id"]))
        if (merged.get("attempt_count_total") or 0) >= min_attempts:
            to_hide.append(lead["id"])

    if not to_hide:
        return {"hidden": 0, "checked": len(candidates), "min_attempts": min_attempts}

    reason = f"auto_hidden_after_{min_attempts}_attempts"
    db.execute(
        text(
            """UPDATE nexus_global_leads
               SET priority_state = 'hidden',
                   hidden_reason = :reason,
                   hidden_at = NOW(),
                   updated_at = NOW()
               WHERE id = ANY(:ids)"""
        ),
        {"ids": to_hide, "reason": reason},
    )
    db.commit()
    try:
        gtm_cache_invalidate(extract_workspace_id(workspace))
    except Exception:
        pass

    return {
        "hidden": len(to_hide),
        "checked": len(candidates),
        "min_attempts": min_attempts,
    }


# ─── GET /leads/{id}/demo ────────────────────────────────────────────────────


@router.get("/leads/{lead_id}/demo")
def journey_lead_demo(
    lead_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Return the most recent demo booking + its briefing for this lead."""
    # SECURITY: verify the lead belongs to the caller's workspace before
    # returning any booking/briefing — otherwise any authenticated user could
    # read another tenant's demo bookings by enumerating lead ids (IDOR).
    _ws = int(workspace.id if hasattr(workspace, "id") else workspace)
    if not db.execute(
        text("SELECT 1 FROM nexus_leads WHERE global_lead_id = :lid AND workspace_id = :ws LIMIT 1"),
        {"lid": lead_id, "ws": _ws},
    ).first():
        return {"booking": None, "briefing": None}
    booking_row = db.execute(
        text(
            """SELECT * FROM nexus_demo_bookings
               WHERE lead_id = :lid
               ORDER BY created_at DESC LIMIT 1"""
        ),
        {"lid": lead_id},
    ).first()
    if not booking_row:
        return {"booking": None, "briefing": None}
    booking = dict(booking_row._mapping)

    briefing_row = None
    if booking.get("briefing_id"):
        briefing_row = db.execute(
            text("SELECT * FROM nexus_demo_briefings WHERE id = :id"),
            {"id": booking["briefing_id"]},
        ).first()
    if not briefing_row:
        briefing_row = db.execute(
            text(
                """SELECT * FROM nexus_demo_briefings
                   WHERE demo_booking_id = :bid
                   ORDER BY created_at DESC LIMIT 1"""
            ),
            {"bid": booking["id"]},
        ).first()

    briefing = dict(briefing_row._mapping) if briefing_row else None
    if briefing:
        briefing["_id"] = briefing.get("id")
    booking["_id"] = booking.get("id")

    # Additive: include the resolved product_name + value_proposition so the
    # journey UI can show the same orange product chip the Bookings tab
    # renders — keeps lead-product attribution consistent between the two
    # surfaces (Agent 3 lead-product-consistency finding #4). Uses the same
    # 4-layer resolver the Bookings flow uses, so a Pipelyt-campaign lead
    # always returns "Pipelyt" regardless of which booking-detail surface
    # the operator opens.
    try:
        from nexus.services.ms_bookings_sync import _resolve_product_and_company

        product_name, product_value_prop, _ = _resolve_product_and_company(
            db,
            workspace_id=int(workspace.id) if hasattr(workspace, "id") else int(workspace),
            campaign_id=booking.get("campaign_id"),
            lead_id=booking.get("lead_id"),
            attendee_email=booking.get("attendee_email"),
        )
    except Exception:
        product_name, product_value_prop = "", ""

    return {
        "booking": booking,
        "briefing": briefing,
        "product_name": product_name,
        "product_value_proposition": product_value_prop,
    }


# ─── POST /leads/{id}/demo/regenerate ────────────────────────────────────────


@router.post("/leads/{lead_id}/demo/regenerate")
def journey_lead_demo_regenerate(
    lead_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Mark the briefing for re-generation. The actual generation is
    handled by services/briefing_*; we just flip status='generating' so
    the frontend's 3-second poll picks up the new state."""
    # SECURITY: only act on a lead that belongs to the caller's workspace.
    _ws = int(workspace.id if hasattr(workspace, "id") else workspace)
    if not db.execute(
        text("SELECT 1 FROM nexus_leads WHERE global_lead_id = :lid AND workspace_id = :ws LIMIT 1"),
        {"lid": lead_id, "ws": _ws},
    ).first():
        raise HTTPException(status_code=404, detail="No confirmed booking found for this lead")
    booking_row = db.execute(
        text(
            """SELECT * FROM nexus_demo_bookings
               WHERE lead_id = :lid AND status IN ('confirmed', 'scheduled')
               ORDER BY created_at DESC LIMIT 1"""
        ),
        {"lid": lead_id},
    ).first()
    if not booking_row:
        raise HTTPException(
            status_code=404, detail="No confirmed booking found for this lead"
        )

    booking_id = booking_row._mapping["id"]
    # Flip the briefing into 'generating' so the frontend renders the
    # pulse animation. A background worker / regen service is expected
    # to pick this up.
    db.execute(
        text(
            """UPDATE nexus_demo_briefings
               SET status = 'generating',
                   regenerated_at = NOW(),
                   updated_at = NOW()
               WHERE demo_booking_id = :bid"""
        ),
        {"bid": booking_id},
    )
    db.commit()
    try:
        gtm_cache_invalidate(extract_workspace_id(workspace))
    except Exception:
        pass
    return {"ok": True, "message": "Briefing regeneration started"}


# ─── Legacy minimal endpoint kept for back-compat ────────────────────────────


@router.get("/{lead_id}", response_model=JourneyOut)
def journey_for_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Original minimal /journey/{lead_id} endpoint — preserved so any
    caller depending on the old JourneyOut shape keeps working. New code
    should use /journey/leads/{lead_id}/detail."""
    workspace_id = extract_workspace_id(workspace) or 0

    lead_email = ""
    lead_name = ""
    try:
        # SECURITY: only expose the global lead's name/email when the lead is
        # attached to the caller's workspace (via nexus_leads) — the global
        # row itself has no workspace_id, so scope through the junction.
        row = db.execute(
            text(
                "SELECT gl.email, COALESCE(gl.name,'') FROM nexus_global_leads gl "
                "WHERE gl.id = :id AND EXISTS ("
                "  SELECT 1 FROM nexus_leads nl "
                "   WHERE nl.global_lead_id = gl.id AND nl.workspace_id = :ws)"
            ),
            {"id": lead_id, "ws": workspace_id},
        ).first()
        if row:
            lead_email = row[0] or ""
            lead_name = row[1] or ""
    except Exception:
        db.rollback()

    events: List[JourneyEvent] = []
    outreach_rows = (
        db.query(Outreach)
        .filter(Outreach.workspace_id == workspace_id, Outreach.lead_id == lead_id)
        .order_by(Outreach.id.asc())
        .all()
    )
    for o in outreach_rows:
        if o.email_sent_at or o.sent_at:
            events.append(
                JourneyEvent(
                    timestamp=o.email_sent_at or o.sent_at,
                    kind="email_sent",
                    title=f"Email sent — {o.subject or '(no subject)'}",
                    detail=o.to_email or None,
                    meta={"outreach_id": o.id, "status": o.status},
                )
            )
        if o.opened_at:
            events.append(
                JourneyEvent(
                    timestamp=o.opened_at,
                    kind="email_opened",
                    title="Email opened",
                    meta={"outreach_id": o.id},
                )
            )
        if o.clicked_at:
            events.append(
                JourneyEvent(
                    timestamp=o.clicked_at,
                    kind="email_clicked",
                    title="Link clicked",
                    meta={"outreach_id": o.id},
                )
            )
        if o.status == "replied" and o.reply_text:
            events.append(
                JourneyEvent(
                    timestamp=o.updated_at or o.created_at,
                    kind="reply",
                    title="Lead replied",
                    detail=(o.reply_text or "")[:240],
                    meta={"outreach_id": o.id},
                )
            )

    voice_rows = (
        db.query(VoiceCall)
        .filter(VoiceCall.workspace_id == workspace_id, VoiceCall.lead_id == lead_id)
        .order_by(VoiceCall.id.asc())
        .all()
    )
    for v in voice_rows:
        events.append(
            JourneyEvent(
                timestamp=v.started_at or v.created_at,
                kind="voice_call",
                title=f"Voice call — {v.status} ({v.outcome})",
                detail=(v.transcript or "")[:240] if v.transcript else None,
                meta={"call_id": v.id, "duration_sec": v.duration_sec},
            )
        )

    booking_rows = (
        db.query(DemoBooking)
        .filter(DemoBooking.workspace_id == workspace_id, DemoBooking.lead_id == lead_id)
        .all()
    )
    for b in booking_rows:
        events.append(
            JourneyEvent(
                timestamp=b.scheduled_at or b.created_at,
                kind="demo_booked",
                title=f"Demo booked — {b.source}",
                detail=b.attendee_email,
                meta={"booking_id": b.id, "status": b.status},
            )
        )
        # Prospect's RSVP as a distinct signal event (accepted / declined).
        _bmeta = b.meta if isinstance(b.meta, dict) else {}
        _rsvp = _bmeta.get("rsvp")
        if _rsvp:
            if _rsvp == "accepted":
                _kind, _title = "demo_confirmed", "Prospect accepted the invite ✓"
            elif _rsvp in ("declined", "cancelled"):
                _kind, _title = "demo_declined", "Prospect declined the invite"
            else:
                _kind, _title = "demo_tentative", "Prospect tentatively accepted"
            events.append(
                JourneyEvent(
                    timestamp=b.updated_at or b.scheduled_at or b.created_at,
                    kind=_kind,
                    title=_title,
                    detail=b.attendee_email,
                    meta={"booking_id": b.id, "rsvp": _rsvp},
                )
            )

    try:
        sig_rows = db.execute(
            text(
                """SELECT isg.created_at, isg.source, isg.signal_type, isg.score
                   FROM nexus_intent_signals isg
                   WHERE isg.lead_id = :lid
                     AND EXISTS (
                       SELECT 1 FROM nexus_leads nl
                        WHERE nl.global_lead_id = isg.lead_id
                          AND nl.workspace_id = :ws)
                   ORDER BY isg.created_at ASC"""
            ),
            {"lid": lead_id, "ws": workspace_id},
        ).fetchall()
        for s in sig_rows:
            events.append(
                JourneyEvent(
                    timestamp=s[0],
                    kind="signal",
                    title=f"Intent signal — {s[2] or 'unknown'}",
                    detail=s[1] or "",
                    meta={"score": s[3]},
                )
            )
    except Exception:
        db.rollback()

    _epoch = datetime(1970, 1, 1)
    events.sort(key=lambda e: e.timestamp or _epoch)

    return JourneyOut(
        lead_id=lead_id,
        lead_email=lead_email,
        lead_name=lead_name,
        events=events,
    )
