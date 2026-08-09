"""Apollo.io people-search wrapper with Postgres-backed response caching.

Apollo charges per request, so we hash the normalised request body and cache
responses in ``nexus_apollo_lead_cache``. Cache lifetime is 14 days.

Pipeline:
    1. mixed_people/api_search — replaces the deprecated /mixed_people/search;
       free-tier, returns metadata (cached).
    2. people/bulk_match    — paid, reveals real emails for the candidates
       whose search result came back as ``email_not_unlocked@domain.com``.
    3. Each call writes a NexusCreditLog row so /nexus/credits/usage works.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from nexus.models_phase3 import NexusApolloLeadCache
from nexus.models_phase6 import CreditLog
from nexus.services.apollo_industry_map import industries_to_tag_ids
from nexus.services.apollo_technology_map import technologies_to_uids

logger = logging.getLogger(__name__)

APOLLO_BASE = "https://api.apollo.io/v1"
CACHE_TTL = timedelta(days=14)

# In-DB Apollo response cache (`nexus_apollo_lead_cache`). Default OFF: the
# 14-day cache was making re-runs replay the same page slice and surface "zero
# new leads". Existing rows are left in place; flipping back to True re-enables.
APOLLO_CACHE_ENABLED = (
    os.getenv("APOLLO_CACHE_ENABLED", "false").strip().lower() == "true"
)

# Hard per-run credit ceiling over TOTAL Apollo spend (search + bulk_match).
# Default 0 = UNLIMITED (env APOLLO_MAX_CREDITS_PER_RUN). A positive value caps
# the run: pagination stops once search-side spend crosses it and the bulk_match
# list is trimmed to the remainder. Malformed env → 0 (never crash import).
def _parse_credit_cap() -> int:
    raw = os.getenv("APOLLO_MAX_CREDITS_PER_RUN", "0")
    try:
        v = int(raw)
        return max(0, v)  # 0 = unlimited
    except (TypeError, ValueError):
        logger.warning(
            "APOLLO_MAX_CREDITS_PER_RUN env var malformed (%r) — defaulting to 0 (unlimited)",
            raw,
        )
        return 0

APOLLO_MAX_CREDITS_PER_RUN = _parse_credit_cap()

# STRICT targeting (env NEXUS_STRICT_TARGETING, default True): when True the
# broaden-on-low-yield fallback is DISABLED — discovery never drops the user's
# titles/industries/tech to pad the count. Per user: "only my filters, even if
# that means zero leads." Set False to restore the auto-broaden safety net.
_STRICT_TARGETING = (
    os.getenv("NEXUS_STRICT_TARGETING", "true").strip().lower() != "false"
)

# Post-Apollo re-gate switch (env NEXUS_POST_APOLLO_GATE, default False). OFF
# for Apollo parity: Apollo already enforces titles + industries at search time,
# so re-filtering returned rows only drops people Apollo accepted. Set True to
# re-enable the old client-side title/industry re-check.
_POST_APOLLO_GATE = (
    os.getenv("NEXUS_POST_APOLLO_GATE", "false").strip().lower() == "true"
)


def _api_key() -> Optional[str]:
    try:
        from nexus import config as _cfg  # type: ignore
        v = getattr(_cfg, "APOLLO_API_KEY", None)
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("APOLLO_API_KEY")


def _hash_body(body: Dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _from_cache(db: Session, query_hash: str) -> Optional[Dict[str, Any]]:
    # Feature-flag short-circuit. When caching is disabled we ALWAYS
    # return None so the calling code falls through to a fresh Apollo
    # call. Existing cache rows stay untouched — flipping the flag back
    # to True restores the original behaviour with zero data loss.
    if not APOLLO_CACHE_ENABLED:
        return None
    row = (
        db.query(NexusApolloLeadCache)
        .filter(NexusApolloLeadCache.query_hash == query_hash)
        .first()
    )
    if not row:
        return None
    if datetime.utcnow() - row.fetched_at > CACHE_TTL:
        return None
    return row.response


def _to_cache(db: Session, query_hash: str, response: Dict[str, Any]) -> None:
    # Mirror of _from_cache — when caching is disabled, skip the write
    # so we don't accumulate stale rows the reader won't consult anyway.
    if not APOLLO_CACHE_ENABLED:
        return
    row = (
        db.query(NexusApolloLeadCache)
        .filter(NexusApolloLeadCache.query_hash == query_hash)
        .first()
    )
    if row is None:
        row = NexusApolloLeadCache(query_hash=query_hash)
        db.add(row)
    row.response = response
    row.fetched_at = datetime.utcnow()
    db.commit()


def _log_credits(
    db: Session,
    *,
    workspace_id: Optional[int],
    action_type: str,
    total_requested: int,
    successful_matches: int,
    campaign_id: Optional[int] = None,
) -> None:
    """Write a NexusCreditLog row. Best-effort — never raises."""
    if not workspace_id:
        return
    try:
        db.add(
            CreditLog(
                workspace_id=workspace_id,
                action_type=action_type,
                credits_used=successful_matches,
                successful_matches=successful_matches,
                total_requested=total_requested,
                campaign_id=campaign_id,
                ref_type="apollo",
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("apollo: failed to write credit log (non-fatal)")
        db.rollback()


# ---------------------------------------------------------------------------
# ICP -> Apollo body
# ---------------------------------------------------------------------------
def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [x for x in v if x not in (None, "")]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _first_nonempty(d: Dict[str, Any], *keys: str) -> List[Any]:
    """Return the first non-empty list among the given keys.

    Lets us accept both the legacy NEXUS ICP shape (``industries``,
    ``geography_hints``, ``company_sizes``) and the partial port shape
    (``industry``, ``geographies``, ``company_size``) without losing data.
    """
    for k in keys:
        vals = _as_list(d.get(k))
        if vals:
            return vals
    return []


# Apollo limits we honour to avoid HTTP 422 "Value too long":
#   - q_keywords:           ~250 chars total. We cap MUCH tighter —
#                           just 2 items (~60 chars max) to keep the
#                           search broad. Apollo's keyword match is
#                           an AND-ish behaviour, so fewer keywords
#                           means more leads in the result set.
#   - person_titles / person_locations / organization_industries:
#       Apollo documents NO per-array limit on these OR-union fields (the
#       only documented array cap is q_organization_domains_list = 1000,
#       which we don't use). These fields are OR'd by Apollo — every extra
#       value WIDENS the result set — so capping them DROPS leads the user
#       asked for: a user selecting 12 industries got only 10 searched, and
#       leads in the other 2 were never fetched. The cap below is therefore
#       only a sanity bound far above the largest wizard dropdown (~67
#       industries), not an Apollo requirement. See docs.apollo.io People
#       Search API reference (verified 2026-06).
_MAX_Q_KEYWORDS_CHARS = 60    # only 2 short keywords, well under Apollo's ~250 ceiling
_MAX_KEYWORD_ITEMS = 2        # cap q_keywords at 2 items per user instruction
_MAX_LIST_ITEMS = 100         # sanity bound only — Apollo imposes no array limit here
_MAX_TITLE_CHARS = 60


# Apollo's organization_num_employees_ranges accepts ONLY these literal
# strings. The wizard's ICP gen produces semantic labels like "startup"
# / "SMB" / "enterprise", which we expand here. Anything not in this
# map gets dropped (Apollo would treat unrecognised values as no match
# and silently return 0 leads).
_SIZE_LABEL_TO_RANGES = {
    "startup":     ["1,10", "11,50"],
    "smb":         ["11,50", "51,200", "201,500"],
    "mid_market":  ["201,500", "501,1000", "1001,5000"],
    "mid-market":  ["201,500", "501,1000", "1001,5000"],
    "midmarket":   ["201,500", "501,1000", "1001,5000"],
    "enterprise":  ["1001,5000", "5001,10000", "10001+"],
}


def _normalise_company_sizes(sizes: List[Any]) -> List[str]:
    """Map Gemini-style size labels to Apollo's numeric ranges.

    Passes through any item that already looks like an Apollo range
    (e.g. "11,50" or "10001+") so callers can override with raw values.
    """
    out: List[str] = []
    for s in sizes:
        raw = str(s or "").strip()
        if not raw:
            continue
        # If it already looks like an Apollo range, keep it.
        if "," in raw or raw.endswith("+") or raw[:1].isdigit():
            out.append(raw)
            continue
        expanded = _SIZE_LABEL_TO_RANGES.get(raw.lower())
        if expanded:
            out.extend(expanded)
    # Dedupe while preserving order.
    seen = set()
    deduped: List[str] = []
    for r in out:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped[:_MAX_LIST_ITEMS]


# Apollo's person_locations expects full country names (per docs). The
# wizard sometimes emits abbreviations like "USA"/"UK". We normalise
# the most common ones; anything not in the map passes through.
_LOCATION_ALIAS = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "uae": "United Arab Emirates",
    "korea": "South Korea",
}


def _normalise_locations(items: List[Any]) -> List[str]:
    out: List[str] = []
    for it in items:
        raw = str(it or "").strip()
        if not raw:
            continue
        aliased = _LOCATION_ALIAS.get(raw.lower(), raw)
        # Cap raised 2026-06-01: locations may now be "City, State, Country"
        # strings from the wizard's hierarchical picker (e.g. "San Francisco,
        # California, United States" ≈ 40 chars; long ones can exceed 60).
        # Truncating mid-string would corrupt the Apollo location match.
        out.append(aliased[:120])
    # Dedupe preserving order
    seen = set()
    deduped: List[str] = []
    for r in out:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped[:_MAX_LIST_ITEMS]


def _trim_keywords(items: List[Any]) -> str:
    """Build a space-separated q_keywords string with AT MOST 2 items.

    Apollo's keyword match narrows results, so fewer keywords = more
    leads. We pick the 2 shortest non-sentence tokens (e.g. tech
    names like "Salesforce", "Mulesoft") and drop everything else.
    """
    # Prefer shorter items first — they're more likely real keywords
    # (e.g. "Salesforce") rather than full pain-point sentences.
    ranked = sorted(
        (str(x).strip() for x in items if str(x).strip()),
        key=len,
    )
    tokens: List[str] = []
    used = 0
    for item in ranked:
        if len(item) > 50:
            continue  # sentence — skip
        next_len = used + (1 if tokens else 0) + len(item)
        if next_len > _MAX_Q_KEYWORDS_CHARS:
            break
        tokens.append(item)
        used = next_len
        if len(tokens) >= _MAX_KEYWORD_ITEMS:
            break
    return " ".join(tokens)


# Maps the wizard's revenue dropdown string → Apollo's {min, max} cents-
# precision dollar object. Apollo's `revenue_range` accepts either omitted-
# bound shape; we always emit both for clarity. "Any" / "Pre-revenue" /
# unrecognised values return None — the caller then omits the field
# entirely (Apollo treats absent = no constraint).
_REVENUE_BAND_BOUNDS: Dict[str, Dict[str, int]] = {
    # value strings MUST match the REVENUE_OPTIONS list in analyze_targeting.py.
    "< $1M":           {"min": 0,             "max": 1_000_000},
    "$1M – $10M":      {"min": 1_000_000,     "max": 10_000_000},
    "$10M – $50M":     {"min": 10_000_000,    "max": 50_000_000},
    "$50M – $200M":    {"min": 50_000_000,    "max": 200_000_000},
    "$200M – $1B":     {"min": 200_000_000,   "max": 1_000_000_000},
    "$1B+":            {"min": 1_000_000_000, "max": 1_000_000_000_000},
}


def _coerce_revenue_bounds(raw: Any) -> Optional[Dict[str, int]]:
    """Read an explicit {min, max} dollar object (custom / merged-band range).

    The wizard now sends revenue as `{min, max}` so it can support custom
    values and contiguous multi-band selections collapsed into ONE range
    (Apollo has no array form for revenue — only a single min/max window).
    `min`/`max` may each be missing/None → treated as open-ended (0 / a large
    sentinel). Returns None when neither bound is a usable number, so a stray
    `{}` falls through to the band-label path / omission.
    """
    if not isinstance(raw, dict):
        return None
    if "min" not in raw and "max" not in raw:
        return None  # not a bounds object — likely the {values:...} triple

    def _num(v: Any) -> Optional[int]:
        if isinstance(v, bool):  # guard: bool is an int subclass
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip():
            try:
                return int(float(v.strip()))
            except ValueError:
                return None
        return None

    lo, hi = _num(raw.get("min")), _num(raw.get("max"))
    if lo is None and hi is None:
        return None
    lo = max(0, lo if lo is not None else 0)
    hi = hi if hi is not None else 1_000_000_000_000
    if hi < lo:
        lo, hi = hi, lo
    return {"min": lo, "max": hi}


def revenue_ranges_from_icp(raw: Any) -> List[Dict[str, int]]:
    """Resolve the stored revenue selection to a LIST of {min, max} windows.

    The wizard sends either one window or, when the user picks non-adjacent
    bands, several — each a separate Apollo search (Apollo can't express a gap
    in one query). discover_for_campaign loops over the result, running one
    discovery pass per window and unioning the leads into the same campaign.

    Accepts: a list (of {min,max} objects and/or band-label strings), a single
    {min,max} object, a band-label string, or the {values:[...]} triple.
    Returns [] when nothing resolves (→ no revenue filter).
    """
    out: List[Dict[str, int]] = []
    seen: set = set()

    def _add(b: Optional[Dict[str, int]]) -> None:
        if not b:
            return
        key = (b["min"], b["max"])
        if key in seen:
            return
        seen.add(key)
        out.append(b)

    if isinstance(raw, list):
        for item in raw:
            _add(_coerce_revenue_bounds(item) if isinstance(item, dict)
                 else _revenue_band_to_apollo(item))
    else:
        _add(_revenue_band_to_apollo(raw))
    return out


def _revenue_band_to_apollo(raw: Any) -> Optional[Dict[str, int]]:
    """Convert ONE revenue selection to Apollo's single {min, max} window.

    Resolution order:
      1. explicit {min, max} dollar object (custom / merged contiguous bands)
      2. band label — canonical-triple ({values:[...]}), string, or 1-elem list
      3. a LIST of windows → the SPANNING min/max (defensive fallback for any
         single-value caller; discover_for_campaign uses revenue_ranges_from_icp
         to run them as separate searches instead).
    Returns None for "Any" / "Pre-revenue" / unrecognised values so the caller
    can omit the field (Apollo treats absent = no constraint).
    """
    bounds = _coerce_revenue_bounds(raw)
    if bounds is not None:
        return bounds

    # A list of {min,max} objects → span them (last-resort single-window view).
    if isinstance(raw, list) and any(isinstance(x, dict) for x in raw):
        spans = [_coerce_revenue_bounds(x) for x in raw if isinstance(x, dict)]
        spans = [s for s in spans if s]
        if spans:
            return {"min": min(s["min"] for s in spans),
                    "max": max(s["max"] for s in spans)}

    label: Optional[str] = None
    if isinstance(raw, str):
        label = raw.strip()
    elif isinstance(raw, dict):
        vals = raw.get("values") or []
        if vals and isinstance(vals[0], str):
            label = vals[0].strip()
    elif isinstance(raw, list) and raw and isinstance(raw[0], str):
        label = raw[0].strip()

    if not label or label in ("Any", "Pre-revenue"):
        return None
    return _REVENUE_BAND_BOUNDS.get(label)


# Apollo confidence threshold (2026-06-02). When the ICP field arrives as
# the {values, confidence, evidence} triple from Gemini's analyze_product,
# we only send it to Apollo if confidence >= this number. Plain lists from
# the wizard (user-edited chip values) bypass the gate — manual choices
# are always trusted. Mirrors the wizard-display threshold in
# analyze_targeting.MIN_CONFIDENCE so the user never sees a value that
# isn't also sent to Apollo (and vice-versa).
_APOLLO_CONFIDENCE_MIN = 80

# Legacy string-confidence mapping (kept here too so this module is
# self-contained — `nexus_products.icp` rows written before 2026-06-02
# may still carry "high"/"medium"/"low" strings).
_LEGACY_STRING_CONFIDENCE = {"high": 90, "medium": 60, "low": 20}


def _to_int_confidence(raw: Any) -> int:
    if isinstance(raw, bool):
        return 100 if raw else 0
    if isinstance(raw, (int, float)):
        try:
            return max(0, min(100, int(raw)))
        except Exception:  # noqa: BLE001
            return 0
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return 0
        if s in _LEGACY_STRING_CONFIDENCE:
            return _LEGACY_STRING_CONFIDENCE[s]
        try:
            return max(0, min(100, int(float(s))))
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _icp_field_values(raw: Any, *, require_confidence: bool = True) -> List[str]:
    """Pull a list of string values out of the canonical-triple shape.

    The Gemini analyze_product() output stores each Apollo filter as
    `{"values": [...], "confidence": int 0-100, "evidence": "..."}`. The
    wizard payload sometimes passes the user-edited chip values as a plain
    list (no triple wrapping). This helper handles both.

    2026-06-02: when the input is a triple AND `require_confidence` is True
    (the default), values with confidence < `_APOLLO_CONFIDENCE_MIN` are
    dropped — so Apollo never receives a field the user wouldn't have
    seen either. Plain lists from the wizard always pass through; the user
    typed them by hand and explicitly wants them sent.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, dict):
        if require_confidence:
            conf = _to_int_confidence(raw.get("confidence"))
            if conf < _APOLLO_CONFIDENCE_MIN:
                return []
        vals = raw.get("values") or []
        return [str(v).strip() for v in vals if isinstance(v, str) and v.strip()]
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if isinstance(v, str) and v.strip()]
    return []


# Builds the Apollo search body from the ICP's canonical keys. All keys match
# their Apollo wire name except two, renamed at this boundary:
#   organization_industries → organization_industry_tag_ids  (industries_to_tag_ids)
#   buyer_technologies       → currently_using_any_of_technology_uids (technologies_to_uids)
def _icp_to_apollo_body(icp: Dict[str, Any], page: int, per_page: int) -> Dict[str, Any]:
    # reveal_personal_emails/phone are NOT honored on search — it always returns
    # obfuscated previews; the real unlock is /people/bulk_match downstream.
    body: Dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }

    # person_titles — include_similar_titles=False makes Apollo return only
    # strict matches (default is fuzzy: "CEO" also matches "Deputy CEO" etc.).
    titles = _icp_field_values(icp.get("person_titles"))
    if titles:
        clean_titles = [t[:_MAX_TITLE_CHARS] for t in titles][:_MAX_LIST_ITEMS]
        if clean_titles:
            body["person_titles"] = clean_titles
            body["include_similar_titles"] = False

    # person_locations — Apollo's single location field accepts any granularity
    # (country / "state, country" / "city, state, country").
    locations = _icp_field_values(icp.get("person_locations"))
    if locations:
        locs = _normalise_locations(locations)
        if locs:
            body["person_locations"] = locs[:_MAX_LIST_ITEMS]

    # person_seniorities + person_departments intentionally NOT sent — titles
    # already imply both; sending them over-narrowed Apollo's match set.

    # organization_industries → tag IDs. Apollo's industry filter is a closed
    # enum of hex tag IDs; unmapped labels are skipped (can't be sent free-text).
    industries = _icp_field_values(icp.get("organization_industries"))
    if industries:
        tag_ids = industries_to_tag_ids(industries)
        if tag_ids:
            body["organization_industry_tag_ids"] = tag_ids[:_MAX_LIST_ITEMS]

    # revenue_range — UI band / {min,max} → Apollo {min, max}.
    revenue_bounds = _revenue_band_to_apollo(icp.get("revenue_range"))
    if revenue_bounds:
        body["revenue_range"] = revenue_bounds

    # buyer_technologies → tech UIDs (lower_snake). Display names no-op in
    # Apollo, so technologies_to_uids() resolves them; unknowns pass verbatim.
    techs = _icp_field_values(icp.get("buyer_technologies"))
    if techs:
        uids = technologies_to_uids(techs)
        if uids:
            body["currently_using_any_of_technology_uids"] = uids[:_MAX_LIST_ITEMS * 2]

    return body


def icp_filter_fingerprint(icp: Optional[Dict[str, Any]]) -> str:
    """Stable hash of the EFFECTIVE Apollo filter set for an ICP.

    Two ICPs that produce the same Apollo search body share a fingerprint
    (and therefore a page cursor); any change to titles / locations /
    industries / revenue / technologies yields a different one. The
    campaign-creation path uses this to decide whether a new run pages
    FORWARD from a prior run's cursor (filters unchanged) or RESTARTS at
    page 1 (filters changed) — a stale cursor from a broader query lands
    past the end of a narrower result set and returns ~0 leads.

    Built from `_icp_to_apollo_body` (minus pagination) so it reflects what
    Apollo actually receives, including the industry/tech tag-ID mapping —
    not the raw human labels.
    """
    body = _icp_to_apollo_body(icp or {}, page=1, per_page=1)
    body.pop("page", None)
    body.pop("per_page", None)
    return _hash_body(body)


# ---------------------------------------------------------------------------
# Person normaliser — KEEPS unlocked rows so bulk_match can enrich them.
# ---------------------------------------------------------------------------
def _norm_domain(value: Any) -> Optional[str]:
    if not value:
        return None
    return (
        str(value)
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .strip("/")
        or None
    )


def _filter_against_product_db(
    db: Session,
    candidates: List[Dict[str, Any]],
    product_id: Optional[int],
    # 2026-06-11 — when a list is passed, every DROPPED duplicate is appended
    # to it with its KNOWN email backfilled from our own DB (their email was
    # revealed in a past run, so re-using it costs nothing). Powers the
    # zero-fresh-leads fallback: re-vet known people with Agent #10 and show
    # them as "Already in campaign" rows instead of an empty table.
    collect_duplicates: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Drop candidates already attached to ANY campaign of THIS product.

    Why we do this BEFORE bulk_match:
      bulk_match is the EXPENSIVE Apollo call (1 credit per email reveal).
      Every duplicate we let through wastes credits + wall-clock time
      revealing emails we already have. By pre-filtering against our
      product's existing lead pool we cut Apollo cost on re-runs by
      ~80% and on cross-campaign overlaps by ~30%.

    3-tier dedup hierarchy (any tier match = duplicate):
      1. email (when present + unlocked)
      2. linkedin_url (when email is masked)
      3. (first_name + last_name + company_domain) triplet (last-resort)

    Scope = PRODUCT-level (matches the operator's mental model: "one URL
    = one outreach effort"). Different products can each pitch the same
    person — they're different value props. See `_already_attached_for_product`
    in lead_discovery.py for the matching logic at enrollment time.

    Returns the candidates we have NOT seen for this product yet.
    Returns the input unchanged when product_id is None (no scope).
    """
    if product_id is None or not candidates:
        return candidates

    # One round-trip — pull every global_lead currently attached to ANY
    # campaign of this product. The set is small (a few hundred even for
    # active products) so we filter in Python rather than building a
    # complex IN-clause WHERE per-candidate. The SELECT DISTINCT keeps
    # the result row count == unique global_lead count, not per-campaign.
    try:
        from sqlalchemy import text as _sql_text
        rows = db.execute(
            _sql_text(
                """
                SELECT DISTINCT
                    LOWER(COALESCE(gl.email, '')) AS email_lc,
                    gl.linkedin_url,
                    LOWER(COALESCE(gl.first_name, '')) AS fn,
                    LOWER(COALESCE(gl.last_name, '')) AS ln,
                    LOWER(COALESCE(gl.company_domain, '')) AS dom,
                    LOWER(COALESCE(gl.company_name, '')) AS cname
                FROM nexus_global_leads gl
                JOIN nexus_leads l ON l.global_lead_id = gl.id
                JOIN nexus_campaigns c ON c.id = l.campaign_id
                WHERE c.product_id = :pid
                """
            ),
            {"pid": product_id},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apollo: product-dedup query failed for product=%s (%s) — "
            "falling back to post-bulk_match dedup",
            product_id, exc,
        )
        return candidates

    existing_emails = {r.email_lc for r in rows if r.email_lc}
    existing_linkedins = {r.linkedin_url for r in rows if r.linkedin_url}
    existing_triplets = {
        (r.fn, r.ln, r.dom) for r in rows if (r.fn and r.ln and r.dom)
    }
    # match-key -> the person's KNOWN email (for the zero-fresh fallback).
    _lk_email = {r.linkedin_url: r.email_lc for r in rows
                 if r.linkedin_url and r.email_lc}
    _dom_email = {(r.fn, r.ln, r.dom): r.email_lc for r in rows
                  if (r.fn and r.ln and r.dom and r.email_lc)}
    _name_email = {(r.fn, r.ln, r.cname): r.email_lc for r in rows
                   if (r.fn and r.ln and r.cname and r.email_lc)}

    def _collect(c: Dict[str, Any], known_email: Optional[str]) -> None:
        if collect_duplicates is None:
            return
        c2 = dict(c)
        c2["_dup_known"] = True
        if known_email:
            # Their email was revealed in a past run — reuse it so the
            # fallback path needs NO bulk_match (zero Apollo credits).
            c2["email"] = known_email
            c2["email_locked"] = False
        collect_duplicates.append(c2)
    # Tier 4 (2026-06-11) — (first + last + company NAME). The three tiers
    # above are all BLIND at search time: emails come back locked, search
    # rows often carry no linkedin_url, and Apollo HIDES company_domain
    # until the paid reveal — so the filter was a NO-OP pre-reveal and the
    # same people were re-researched by Agent #10 and re-revealed run after
    # run (campaign 67: 8/8 revealed candidates were already-pitched ->
    # 0 leads delivered). Search rows DO always carry the person's name +
    # company name, and so do our stored leads.
    existing_name_triplets = {
        (r.fn, r.ln, r.cname) for r in rows if (r.fn and r.ln and r.cname)
    }

    out: List[Dict[str, Any]] = []
    dropped = 0
    for c in candidates:
        # Tier 1 — email match (when we have an unlocked email)
        email = (c.get("email") or "").strip().lower()
        if email and email in existing_emails:
            dropped += 1
            _collect(c, email)
            continue
        # Tier 2 — linkedin URL match
        lin = (c.get("linkedin_url") or "").strip()
        if lin and lin in existing_linkedins:
            dropped += 1
            _collect(c, _lk_email.get(lin))
            continue
        # Tier 3 — (first + last + company_domain) triplet match
        fn = (c.get("first_name") or "").strip().lower()
        ln = (c.get("last_name") or "").strip().lower()
        dom = (c.get("company_domain") or "").strip().lower()
        if fn and ln and dom and (fn, ln, dom) in existing_triplets:
            dropped += 1
            _collect(c, _dom_email.get((fn, ln, dom)))
            continue
        # Tier 4 — (first + last + company NAME) triplet match: the only
        # tier that can fire on a pre-reveal search row (see note above).
        cname = (c.get("company_name") or "").strip().lower()
        if fn and ln and cname and (fn, ln, cname) in existing_name_triplets:
            dropped += 1
            _collect(c, _name_email.get((fn, ln, cname)))
            continue
        out.append(c)

    if dropped:
        logger.info(
            "apollo: product-dedup dropped %d/%d candidates already attached "
            "to product=%s (kept %d for bulk_match)",
            dropped, len(candidates), product_id, len(out),
        )
    return out


def _normalise_person(p: Dict[str, Any]) -> Dict[str, Any]:
    raw_email = (p.get("email") or "").strip().lower() or None
    # Apollo's free-tier search returns three things in the email field:
    #   (a) a real address — already unlocked
    #   (b) the literal string "email_not_unlocked@<domain>"
    #   (c) NULL / empty string
    # Cases (b) and (c) both mean "you need to bulk_match to unlock it."
    # Previously we only treated (b) as locked, so candidates from (c)
    # got dropped entirely with no bulk_match attempt and never enrolled.
    locked = (not raw_email) or raw_email.startswith("email_not_unlocked")
    email = None if locked else raw_email
    org = p.get("organization") or {}

    # 2026-05-29 — extracted 4 new fields that we previously discarded:
    # person.city / state / country (drive the "Location" column in the
    # leads table) and organization.industry (drives the "Industry" column).
    # Apollo returns these on every search response; we just weren't
    # persisting them.
    city = (p.get("city") or "").strip() or None
    state = (p.get("state") or "").strip() or None
    country = (p.get("country") or "").strip() or None
    org_industry = (org.get("industry") or "").strip() or None

    # Apollo returns `departments` as a list (e.g. ["engineering", "data"]).
    # Flatten into a single comma-separated string so the post-Apollo
    # `department_matches` gate (which does substring / equality on a
    # single string) can match against any of the lead's departments.
    raw_depts = p.get("departments")
    if isinstance(raw_depts, list):
        department = ", ".join(
            str(d).strip().lower()
            for d in raw_depts
            if isinstance(d, str) and d.strip()
        ) or None
    else:
        department = (raw_depts or "").strip().lower() or None

    # Phone — Apollo can return it on `mobile_phone`, `direct_phone`,
    # or as the first entry in `phone_numbers[]`. Pick whatever's
    # present in priority order; None when Apollo didn't return a
    # phone (which is the common case for free-tier search results).
    phone_raw = (
        p.get("mobile_phone")
        or p.get("direct_phone")
        or p.get("corporate_phone")
        or p.get("home_phone")
    )
    if not phone_raw:
        phones = p.get("phone_numbers") or []
        if isinstance(phones, list) and phones:
            first = phones[0]
            if isinstance(first, dict):
                phone_raw = (
                    first.get("sanitized_number")
                    or first.get("raw_number")
                )
            elif isinstance(first, str):
                phone_raw = first
    phone = (str(phone_raw).strip() if phone_raw else "") or None

    return {
        "apollo_id": p.get("id"),
        "email": email,
        "email_locked": locked,
        "first_name": p.get("first_name"),
        "last_name": p.get("last_name"),
        "role": p.get("title"),
        "company_name": org.get("name"),
        "company_domain": _norm_domain(
            org.get("primary_domain") or org.get("website_url")
        ),
        "linkedin_url": p.get("linkedin_url"),
        # Phone — None when Apollo doesn't return one (typical on the
        # search-side response; sometimes filled by /people/bulk_match).
        "phone": phone,
        # ── New 2026-05-29 fields surfaced in the leads table ───────────
        "person_city": city,
        "person_state": state,
        "person_country": country,
        "organization_industry": org_industry,
        # 2026-06-05 — LinkedIn headline (already in the response; promoted
        # from `raw` so it persists. Used only to personalize outreach openers).
        "linkedin_headline": p.get("headline"),
        # 2026-06-02: surfaced for the post-Apollo strict department gate.
        # Not (yet) shown in the UI — it's an internal filter input only.
        "department": department,
        "source": "apollo",
        "raw": {
            "linkedin_url": p.get("linkedin_url"),
            "seniority": p.get("seniority"),
            "departments": p.get("departments"),
            # Extra context not yet persisted — useful for debugging /
            # future "richer profile" view in the right panel.
            "headline": p.get("headline"),
            "photo_url": p.get("photo_url"),
            "estimated_num_employees": org.get("estimated_num_employees"),
        },
    }


# ---------------------------------------------------------------------------
# Apollo HTTP calls
# ---------------------------------------------------------------------------
# Substrings in an Apollo error body that mean the ACCOUNT is out of
# credits / over quota (vs a transient network blip). Used to surface a
# clear "credits exhausted" reason to the UI instead of a generic
# "no leads found".
_CREDIT_ERROR_HINTS = (
    "credit", "insufficient", "quota", "out of credits",
    "upgrade your plan", "payment required", "exceeded",
)


async def _http_post(
    url: str, key: str, body: Dict[str, Any],
    stats: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post(
                url,
                headers={
                    "X-Api-Key": key,
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if r.status_code != 200:
            logger.warning("apollo HTTP %s on %s: %s", r.status_code, url, r.text[:200])
            # Distinguish "account out of credits/quota" from a transient
            # error so the caller can tell the user to top up vs change
            # filters. HTTP 402 = Payment Required; otherwise sniff the body.
            body_l = (r.text or "")[:300].lower()
            if stats is not None and (
                r.status_code == 402
                or any(h in body_l for h in _CREDIT_ERROR_HINTS)
            ):
                stats["apollo_credit_error"] = True
                logger.error(
                    "apollo: ACCOUNT CREDIT/QUOTA error (HTTP %s) — reveals "
                    "will fail until credits are topped up",
                    r.status_code,
                )
            return None
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.exception("apollo request failed on %s: %s", url, exc)
        return None


async def _bulk_match(
    key: str, candidates: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Call /people/bulk_match for the locked candidates.

    Apollo accepts up to 10 details per call. We page through in chunks.
    Returns a dict keyed by ``apollo_id`` -> enriched apollo person dict.
    `stats` (if passed) receives apollo_credit_error=True when Apollo
    rejects the reveal because the account is out of credits.
    """
    if not candidates:
        return {}

    matched: Dict[str, Dict[str, Any]] = {}
    BATCH = 10
    for i in range(0, len(candidates), BATCH):
        chunk = candidates[i : i + BATCH]
        details: List[Dict[str, Any]] = []
        senders: List[Dict[str, Any]] = []  # candidate per detail, same order
        for c in chunk:
            d: Dict[str, Any] = {}
            if c.get("first_name"):
                d["first_name"] = c["first_name"]
            if c.get("last_name"):
                d["last_name"] = c["last_name"]
            if c.get("company_domain"):
                d["domain"] = c["company_domain"]
            if c.get("company_name"):
                d["organization_name"] = c["company_name"]
            if c.get("linkedin_url"):
                d["linkedin_url"] = c["linkedin_url"]
            if c.get("apollo_id"):
                d["id"] = c["apollo_id"]
            if d:
                details.append(d)
                senders.append(c)
        if not details:
            continue

        data = await _http_post(
            f"{APOLLO_BASE}/people/bulk_match",
            key,
            {
                "details": details,
                "reveal_personal_emails": True,
                "reveal_phone_number": False,
            },
            stats=stats,
        )
        if not data:
            continue
        # bulk_match returns either `matches: [...]` or `people: [...]`.
        # Unmatched details come back as null entries, and the array
        # PRESERVES the order of the details we sent.
        rows = data.get("matches") or data.get("people") or []
        # Map each row back to OUR candidate POSITIONALLY when the shape
        # allows it. Keying by the row's own id alone is a trap: people
        # already saved as CONTACTS in the Apollo account come out of
        # search with a CONTACT id but out of bulk_match with their PERSON
        # id — the ids never match and every row silently dropped.
        positional = len(rows) == len(details)
        n_rows = n_email = 0
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue  # null = Apollo couldn't match this detail
            n_rows += 1
            if positional and idx < len(senders):
                cand_id = str(senders[idx].get("apollo_id") or "")
                if cand_id:
                    matched[cand_id] = row
            row_id = row.get("id")
            if row_id:
                matched.setdefault(str(row_id), row)
            if _pick_email(row):
                n_email += 1
        logger.info(
            "apollo: bulk_match batch — sent %d detail(s), got %d match "
            "row(s), %d with a usable email (positional align=%s)",
            len(details), n_rows, n_email, positional,
        )
    return matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def search_people(
    db: Session,
    icp: Dict[str, Any],
    *,
    max_leads: int = 10,
    # The USER's requested lead count (max_leads is the over-fetched candidate
    # budget, ~2x this). Caps the known-people top-up so the run never SHOWS
    # more "Already in campaign" rows than the user asked for. None = legacy
    # caller, top-up capped by max_leads only.
    user_target: Optional[int] = None,
    workspace_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    # When set, pre-dedup candidates against this product's existing lead pool
    # BEFORE the paid bulk_match — skips leads already attached to any campaign
    # of the same product (saves reveal credits). None = post-reveal dedup only.
    product_id: Optional[int] = None,
    # Page cursor so re-runs surface fresh candidates instead of replaying
    # page 1. Pass campaign.last_apollo_page + 1; read pages_walked back via
    # `stats` to persist the new high-water mark.
    start_page: int = 1,
    # Optional out-dict for run-stats (pages_walked, fresh_search_rows,
    # broader_pages_walked, apollo_credit_error, …). None = no stats emitted.
    stats: Optional[Dict[str, Any]] = None,
    # Below this yield, fall through to the broaden-on-low-yield retry (drops
    # the most over-narrowing filters). 1 = retry only on zero.
    min_leads: int = 1,
    # Separate page cursor for the broader-retry pool (its own pagination space).
    broader_start_page: int = 1,
    # Option B — async hook handed the post-gate/dedup/trim candidates BEFORE
    # the paid bulk_match reveal; returns the SUBSET to reveal (e.g. only
    # intent-qualified companies). The raw search already carries company_name +
    # company_domain, all the scorer needs. None = reveal every candidate.
    pre_reveal_filter: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Apollo person search + bulk_match enrichment → normalised lead dicts
    with real emails (enrichment-failed candidates dropped)."""
    key = _api_key()
    if not key:
        logger.info("APOLLO_API_KEY missing — skipping Apollo discovery")
        return []

    per_page = min(max(max_leads, 1), 100)
    # Pages are fetched in parallel batches (gather). _APOLLO_PAGE_SAFETY is a
    # runaway-loop backstop only — the real stop is an empty page or the lead
    # target. _http_post has no 429 retry, so raise the batch size cautiously.
    _APOLLO_PAGE_SAFETY = int(os.getenv("NEXUS_APOLLO_PAGE_SAFETY", "1000000"))
    # Pages per parallel batch. Default = just enough to cover the over-fetch
    # target (max_leads*2 rows) in ONE batch — usually ~2 pages — instead of a
    # blind 10. Env override wins.
    _PARALLEL_BATCH_SIZE = int(os.getenv("NEXUS_APOLLO_PARALLEL_PAGES", "0") or "0")
    if _PARALLEL_BATCH_SIZE <= 0:
        _PARALLEL_BATCH_SIZE = max(1, -(-(max_leads * 2) // per_page))
    people: List[Dict[str, Any]] = []
    seen_apollo_ids: set = set()
    # apollo_id -> source page, so the trim step below can advance the page
    # cursor only through pages we actually COMMIT (kept), never past the
    # over-fetched tail it discards. Without this the cursor jumped past the
    # discarded page and ~half of Apollo's rows were never revealed.
    page_by_apollo_id: Dict[str, int] = {}
    pages_from_cache = 0
    pages_fresh = 0
    fresh_search_rows = 0
    last_body = None
    last_query_hash = None
    start_page = max(1, int(start_page or 1))
    pages_walked = 0
    next_page = start_page

    # Parity diagnostic (2026-06-09): log the EXACT filter set we send to
    # Apollo (minus pagination) so "why fewer leads than the Apollo UI?" is
    # answerable at a glance. Apollo ANDs across fields — every field present
    # here narrows the pool, so this is the first place to compare against
    # what the operator typed into Apollo's own "Find people" screen.
    _filters_sent = {
        k: v
        for k, v in _icp_to_apollo_body(icp, page=start_page, per_page=per_page).items()
        if k not in ("page", "per_page")
    }
    logger.info(
        "apollo: SEARCH FILTERS (AND across %d field(s)) → %s",
        len(_filters_sent), _filters_sent,
    )

    async def _fetch_one_page(page_n: int):
        """Fetch one Apollo page → (page_people, was_cached, body, query_hash,
        row_count, total_entries). `total_entries` is Apollo's TOTAL count
        of rows matching the filters (returned on every response) — lets the
        caller know the full match size up front and stop once it has them all,
        instead of walking pages blindly."""
        body = _icp_to_apollo_body(icp, page=page_n, per_page=per_page)
        query_hash = _hash_body(body)
        cached = _from_cache(db, query_hash)
        if cached is not None:
            return (cached.get("people") or []), True, body, query_hash, 0, cached.get("total_entries")
        data = await _http_post(
            f"{APOLLO_BASE}/mixed_people/api_search", key, body, stats=stats
        )
        if data is None:
            return None, False, body, query_hash, 0, None  # signal error
        page_people = data.get("people") or []
        total_entries = data.get("total_entries")
        _to_cache(db, query_hash, {"people": page_people, "total_entries": total_entries})
        return page_people, False, body, query_hash, len(page_people), total_entries

    # apollo_total_entries: Apollo's reported TOTAL match count for these
    # filters (read from the first response). apollo_exhausted: True once we've
    # fetched every matching row (empty page OR collected >= total_entries) —
    # the clean "Apollo has no more for this ICP" signal, vs blindly paging.
    apollo_total_entries: Optional[int] = None
    apollo_exhausted = False
    _t_apollo_search0 = time.monotonic()  # TIME: Apollo filtered-rows fetch
    while next_page < start_page + _APOLLO_PAGE_SAFETY:
        # Build a batch of pages to fetch in parallel.
        batch_pages = list(range(next_page, next_page + _PARALLEL_BATCH_SIZE))
        results = await asyncio.gather(
            *[_fetch_one_page(p) for p in batch_pages],
            return_exceptions=False,
        )

        # Stitch results back together in PAGE ORDER (gather preserves
        # input order). Stop processing the batch at the first error /
        # empty page so the cursor doesn't get out of sync.
        batch_should_break = False
        for page_n, (page_people, was_cached, body, query_hash, billed, total_entries) in zip(
            batch_pages, results
        ):
            last_body, last_query_hash = body, query_hash
            if page_people is None:
                # Network/API error — bail this batch.
                batch_should_break = True
                break
            if apollo_total_entries is None and total_entries is not None:
                apollo_total_entries = int(total_entries)
            if was_cached:
                pages_from_cache += 1
            else:
                pages_fresh += 1
                fresh_search_rows += billed
            pages_walked += 1
            if not page_people:
                # Empty page = end of Apollo's matches for this ICP. Do NOT
                # count it as walked: advancing the cursor past an empty page
                # is what made re-runs (and repeated retries) start BEYOND the
                # result set and return ~0 leads — each retry pushed the
                # persisted cursor one page deeper into the void. Roll back the
                # increment so the cursor stops at the last page that had data.
                pages_walked -= 1
                apollo_exhausted = True
                batch_should_break = True
                break
            # Cross-page dedup.
            for p in page_people:
                apid = p.get("id")
                if apid and apid in seen_apollo_ids:
                    continue
                if apid:
                    seen_apollo_ids.add(apid)
                    page_by_apollo_id[str(apid)] = page_n
                people.append(p)
            # Apollo told us the full match count — stop the instant we've
            # collected them all (no point requesting empty pages after).
            if apollo_total_entries is not None and len(people) >= apollo_total_entries:
                apollo_exhausted = True
                batch_should_break = True
                break

        next_page += _PARALLEL_BATCH_SIZE

        if batch_should_break:
            break
        # Over-fetch 2x so the gates have room to drop a fraction and still
        # hit max_leads.
        if len(people) >= max_leads * 2:
            break
        # Per-run credit cap (0 = unlimited, the default).
        if APOLLO_MAX_CREDITS_PER_RUN > 0 and fresh_search_rows >= APOLLO_MAX_CREDITS_PER_RUN:
            logger.info(
                "apollo: per-run credit budget hit (%d/%d) — stopping pagination",
                fresh_search_rows, APOLLO_MAX_CREDITS_PER_RUN,
            )
            break

    served_from_cache = pages_fresh == 0
    # NOTE: the people-search endpoint is FREE — it does NOT consume Apollo
    # credits (confirmed in Apollo docs). Credits are spent only on the email
    # reveal (bulk_match) downstream. `search_rows` is just how many raw rows the
    # fresh search pages returned (pre-dedup) — NOT a credit/money figure.
    logger.info(
        "apollo: multi-page fetch — %d candidates from %d cached + %d fresh pages "
        "(search_rows=%d [free], total_matching=%s, exhausted=%s)",
        len(people), pages_from_cache, pages_fresh, fresh_search_rows,
        apollo_total_entries, apollo_exhausted,
    )

    # `body`/`query_hash` point at the LAST page fetched (the broaden retry
    # below needs them).
    body = last_body
    query_hash = last_query_hash

    # Broaden-on-low-yield self-heal: when a pass yields < min_leads, drop the
    # most over-narrowing filters (titles strip ~most, tech ~90%, industry
    # ~80%) and retry once; keep the high-signal fundamentals (locations,
    # revenue). Lives outside the cache-miss branch so a cached empty narrow
    # query still broadens on later ticks.
    broader_pages_walked = 0  # set if the broaden-retry fires + completes
    _BROADEN_STRIP_KEYS = (
        "person_titles",
        "currently_using_any_of_technology_uids",
        "organization_industry_tag_ids",
    )
    if (
        not _STRICT_TARGETING
        and len(people) < min_leads
        and any(body.get(k) for k in _BROADEN_STRIP_KEYS)
    ):
        broader = {
            k: v for k, v in body.items() if k not in _BROADEN_STRIP_KEYS
        }
        broader_page_n = max(1, int(broader_start_page or 1))
        broader["page"] = broader_page_n
        broader_hash = _hash_body(broader)
        broader_cached = _from_cache(db, broader_hash)
        broader_people: List[Dict[str, Any]] = []
        if broader_cached is not None:
            broader_people = broader_cached.get("people") or []
            broader_pages_walked = 1
            logger.info(
                "apollo: broaden-retry served from cache (%d people, strict had %d, "
                "broader page %d)",
                len(broader_people), len(people), broader_page_n,
            )
        else:
            logger.info(
                "apollo: strict pass yielded %d (< min_leads=%d) — retrying without "
                "q_keywords/person_titles (broader page %d)",
                len(people), min_leads, broader_page_n,
            )
            data2 = await _http_post(
                f"{APOLLO_BASE}/mixed_people/api_search", key, broader
            )
            if data2 is not None:
                broader_people = data2.get("people") or []
                _to_cache(db, broader_hash, {"people": broader_people})
                broader_pages_walked = 1
                fresh_search_rows += len(broader_people)
                served_from_cache = False

        # Merge broader candidates into `people` (additive, deduped by id).
        for p in broader_people:
            apid = p.get("id")
            if apid and apid in seen_apollo_ids:
                continue
            if apid:
                seen_apollo_ids.add(apid)
            people.append(p)

    # Bill only fresh Apollo round-trips, never cache hits.
    if not served_from_cache and fresh_search_rows > 0:
        _log_credits(
            db,
            workspace_id=workspace_id,
            action_type="people_match",
            total_requested=fresh_search_rows,
            successful_matches=fresh_search_rows,
            campaign_id=campaign_id,
        )

    # Normalise the whole pool first, then gate, then trim — trimming before
    # gates is what made users see fewer than max_leads.
    # TIME: record how long the Apollo filtered-rows fetch (search + broaden)
    # took, for the end-of-run breakdown in discover_for_campaign.
    if stats is not None:
        stats["t_apollo_search"] = time.monotonic() - _t_apollo_search0

    candidates = [_normalise_person(p) for p in people]

    # Post-Apollo title/industry re-gate. OFF by default (NEXUS_POST_APOLLO_GATE):
    # Apollo already filtered titles (strict) + industries (tag IDs) at search
    # time, so re-checking here only drops rows Apollo returned and breaks parity
    # with Apollo's UI. We TRUST Apollo's search; re-enable as defence-in-depth.
    if _POST_APOLLO_GATE:
        from nexus.services.icp_match import (
            title_matches, industry_matches,
        )
        req_titles = _icp_field_values(
            icp.get("person_titles"), require_confidence=False,
        )
        req_industries = _icp_field_values(
            icp.get("organization_industries"), require_confidence=False,
        )

        if req_titles or req_industries:
            _before_n = len(candidates)
            def _passes_gates(c: Dict[str, Any]) -> bool:
                if req_titles and not title_matches(
                    c.get("role") or c.get("title") or "", req_titles
                ):
                    return False
                if req_industries and not industry_matches(
                    c.get("organization_industry"), req_industries,
                ):
                    return False
                return True
            candidates = [c for c in candidates if _passes_gates(c)]
            if len(candidates) != _before_n:
                logger.info(
                    "apollo: strict gates kept %d/%d candidates "
                    "(titles=%s industries=%s)",
                    len(candidates), _before_n,
                    req_titles, req_industries,
                )

    # Product-level pre-dedup BEFORE the paid bulk_match — drops candidates
    # already attached to any campaign of this product. No-op when product_id
    # is None.
    _dup_pool: List[Dict[str, Any]] = []
    candidates = _filter_against_product_db(
        db, candidates, product_id, collect_duplicates=_dup_pool,
    )
    # ── Known-people TOP-UP (2026-06-11) ─────────────────────────────────
    # When the fetch yields fewer FRESH people than the candidate target
    # (incl. the zero-fresh extreme), fill the remaining slots with people
    # already attached to this product: Agent #10 re-vets their companies
    # (fresh buying signals may have appeared since), and the approved ones
    # surface as "Already in <campaign> · <date>" marker rows — visible,
    # but never re-enrolled and never re-revealed (their emails are
    # backfilled from our own DB, so the top-up costs ZERO Apollo credits).
    # Fresh candidates always keep priority: they are first in the list, so
    # the trim below can only ever cut top-up rows, never fresh ones.
    # Gate-on only: without the pre-reveal scorer the re-vet has no value.
    if _dup_pool and pre_reveal_filter is not None and len(candidates) < max_leads:
        # Only dups whose email we ALREADY hold — guarantees the reveal
        # stage stays at zero credits (no-email dups would re-enter
        # bulk_match as locked rows).
        _known = [c for c in _dup_pool if c.get("email")]
        # The over-fetch (max_leads ~= 2x ask) exists to absorb fresh-lead
        # qualification losses; it makes no sense for the top-up — the user
        # must never SEE more duplicate rows than the count they asked for.
        _cap = min(max_leads, user_target) if user_target else max_leads
        _need = _cap - len(candidates)
        if _known and _need > 0:
            _topup = _known[:_need]
            logger.info(
                "apollo: only %d fresh candidate(s) of the %d targeted — "
                "topping up with %d already-contacted people (re-vetted by "
                "Agent #10; approved ones show as 'Already in campaign'; "
                "zero reveal credits).",
                len(candidates), max_leads, len(_topup),
            )
            candidates = candidates + _topup

    # Trim to max_leads AFTER gates + dedup, so the cap reflects post-filter
    # survivors (trimming earlier under-delivered).
    if len(candidates) > max_leads:
        logger.info(
            "apollo: trimming %d post-gate survivors to user-requested "
            "max_leads=%d", len(candidates), max_leads,
        )
        discarded = candidates[max_leads:]
        candidates = candidates[:max_leads]
        # Cursor accuracy: we only COMMIT the kept candidates this run. The
        # discarded tail was over-fetched (the loop pulls ~2x max_leads so the
        # gates have headroom) — it must be RE-READ next run, not skipped. So
        # advance the page cursor only through the last fully-consumed page:
        # stop just before the first discarded candidate's source page. The
        # next run resumes there; product-level dedup drops anyone already
        # attached, and the previously-discarded rows finally get revealed.
        # Before this, the cursor advanced by every page walked, so the
        # discarded page was jumped over and ~half of Apollo's rows for the
        # query were never surfaced across runs.
        _discarded_pages = [
            page_by_apollo_id.get(str(c.get("apollo_id")))
            for c in discarded
        ]
        _discarded_pages = [p for p in _discarded_pages if p is not None]
        if _discarded_pages and candidates:
            _first_discarded_page = min(_discarded_pages)
            _consumed = _first_discarded_page - start_page
            # Never below 1 (pages_walked==0 is the caller's "Apollo returned
            # nothing" sentinel) and never above what we actually walked.
            pages_walked = min(pages_walked, max(1, _consumed))

    # Option B: score-before-reveal. Let the caller drop candidates that won't
    # qualify BEFORE we pay reveal credits; we reveal only what it returns.
    # Candidates still carry company_name + company_domain (the scorer's input).
    # On error, fail OPEN (reveal all) so a hook bug never zeroes a run.
    if pre_reveal_filter is not None and candidates:
        _pre_n = len(candidates)
        try:
            candidates = await pre_reveal_filter(candidates)
        except Exception:  # noqa: BLE001
            logger.exception(
                "apollo: pre_reveal_filter raised — revealing all %d "
                "candidates (fail-open)", _pre_n,
            )
        else:
            logger.info(
                "apollo: pre-reveal scoring kept %d/%d candidate(s) to reveal",
                len(candidates), _pre_n,
            )

    locked = [c for c in candidates if c.get("email_locked")]
    unlocked = [c for c in candidates if c.get("email") and not c.get("email_locked")]

    # Cap bulk_match to the leftover per-run credit budget (0 = unlimited,
    # the default). Over-budget locked rows ride along without an email and
    # are dropped downstream.
    if locked and APOLLO_MAX_CREDITS_PER_RUN > 0:
        remaining_budget = max(
            0, APOLLO_MAX_CREDITS_PER_RUN - fresh_search_rows
        )
        if len(locked) > remaining_budget:
            logger.info(
                "apollo: trimming bulk_match list from %d to %d to stay "
                "under %d credit budget (search used %d)",
                len(locked), remaining_budget,
                APOLLO_MAX_CREDITS_PER_RUN, fresh_search_rows,
            )
            locked = locked[:remaining_budget]

    # ── Pipelyt credit gate ──────────────────────────────────────────────────
    # Cap the reveal to the funding user's remaining Pipelyt credits so a run
    # never spends beyond their balance. Zero balance → reveal nothing; partial
    # balance → reveal only what's left (the run returns a partial set). This is
    # SEPARATE from Apollo's own credit pool (`apollo_credit_error`).
    _credit_user_id = None
    if locked:
        try:
            from nexus.services.credits_service import (
                available_credits,
                resolve_credit_user_id,
            )
            _credit_user_id = resolve_credit_user_id(db, workspace_id)
            if _credit_user_id:
                _remaining = available_credits(db, _credit_user_id)  # -1 == unlimited
                if _remaining == 0:
                    logger.info(
                        "CREDITS — user %s has 0 Pipelyt credits; revealing nothing.",
                        _credit_user_id,
                    )
                    locked = []
                    if stats is not None:
                        stats["out_of_credits"] = True
                elif 0 < _remaining < len(locked):
                    logger.info(
                        "CREDITS — user %s has %d credits; revealing %d of %d (partial run).",
                        _credit_user_id, _remaining, _remaining, len(locked),
                    )
                    locked = locked[:_remaining]
                    if stats is not None:
                        stats["out_of_credits"] = True
        except Exception:
            logger.exception("apollo: credit gate failed (non-fatal) — proceeding ungated")

    enriched_map: Dict[str, Dict[str, Any]] = {}
    if locked:
        logger.info(
            "EMAIL REVEAL — paying Apollo to reveal %d email address(es) "
            "(THIS IS THE STEP THAT SPENDS APOLLO CREDITS — roughly 1 credit "
            "per revealed contact).",
            len(locked),
        )
        _t_reveal0 = time.monotonic()  # TIME: email reveal (paid bulk_match)
        enriched_map = await _bulk_match(key, locked, stats=stats)
        if stats is not None:
            stats["t_bulk_match"] = time.monotonic() - _t_reveal0
        if enriched_map:
            _revealed = len(enriched_map)
            # Debit Pipelyt credits by the number actually revealed (never for
            # Apollo no-matches). consume() also writes the CreditLog audit row
            # and bumps the campaign's running total — one atomic transaction.
            _debited = False
            if _credit_user_id:
                try:
                    from nexus.services.credits_service import consume
                    _granted = consume(
                        db, _credit_user_id, workspace_id, campaign_id,
                        "email_reveal", _revealed,
                    )
                    _debited = True
                    if _granted < _revealed and stats is not None:
                        stats["out_of_credits"] = True
                except Exception:
                    logger.exception("apollo: credit debit failed (non-fatal)")
            if not _debited:
                _log_credits(
                    db,
                    workspace_id=workspace_id,
                    action_type="bulk_people_match",
                    total_requested=len(locked),
                    successful_matches=_revealed,
                    campaign_id=campaign_id,
                )

    out: List[Dict[str, Any]] = []
    out.extend(_strip_internal(c) for c in unlocked)

    _no_match = _no_email = 0
    _locked_hint = False  # saw an email_not_unlocked@ placeholder
    for c in locked:
        apollo_id = c.get("apollo_id")
        match = enriched_map.get(str(apollo_id)) if apollo_id is not None else None
        if not match:
            _no_match += 1
            continue
        # _pick_email checks all the fields Apollo may put the email in
        # (email / work_email_address / personal_emails / …), not just `email`.
        em = _pick_email(match)
        if not em:
            # no email anywhere → drop, never enrol emailless. A raw value of
            # "email_not_unlocked@…" means Apollo REFUSED to spend the credit
            # — the account is out of export credits (or the plan can't
            # export) — so surface that instead of silently losing the lead.
            _no_email += 1
            if str(match.get("email") or "").startswith("email_not_unlocked"):
                _locked_hint = True
            continue
        merged = dict(c)
        merged["email"] = em
        merged["email_locked"] = False
        if not merged.get("linkedin_url") and match.get("linkedin_url"):
            merged["linkedin_url"] = match["linkedin_url"]
        # Backfill the company domain bulk_match returns (search hides it).
        if not merged.get("company_domain"):
            org = match.get("organization") or {}
            merged["company_domain"] = _norm_domain(
                org.get("primary_domain") or org.get("website_url")
            )
        # Backfill last_name too (Apollo's search returns first_name
        # only; bulk_match has the full name).
        if not merged.get("last_name") and match.get("last_name"):
            merged["last_name"] = match["last_name"]
        # Backfill phone — bulk_match often returns it when search didn't.
        if not merged.get("phone"):
            ph = (
                match.get("mobile_phone")
                or match.get("direct_phone")
                or match.get("corporate_phone")
                or match.get("home_phone")
            )
            if not ph:
                phones = match.get("phone_numbers") or []
                if isinstance(phones, list) and phones:
                    f = phones[0]
                    if isinstance(f, dict):
                        ph = f.get("sanitized_number") or f.get("raw_number")
                    elif isinstance(f, str):
                        ph = f
            if ph:
                merged["phone"] = str(ph).strip() or None
        # Backfill city/state/country/industry — the search obfuscates these
        # (like the email); only bulk_match returns them.
        if not merged.get("person_city") and match.get("city"):
            merged["person_city"] = str(match["city"]).strip() or None
        if not merged.get("person_state") and match.get("state"):
            merged["person_state"] = str(match["state"]).strip() or None
        if not merged.get("person_country") and match.get("country"):
            merged["person_country"] = str(match["country"]).strip() or None
        if not merged.get("organization_industry"):
            org_m = match.get("organization") or {}
            ind = (org_m.get("industry") or "").strip()
            if ind:
                merged["organization_industry"] = ind
        out.append(_strip_internal(merged))

    # Plain-language reveal outcome — these drops were SILENT before, which
    # made "paid 7 reveals -> 0 leads delivered" undiagnosable from the logs.
    if locked:
        _usable = len(out) - len(unlocked)
        if _usable < len(locked):
            logger.warning(
                "EMAIL REVEAL RESULT — only %d/%d revealed candidate(s) came "
                "back usable: %d had no bulk_match row, %d had no usable "
                "email%s.",
                _usable, len(locked), _no_match, _no_email,
                (" — Apollo returned LOCKED email placeholders: the account "
                 "looks OUT OF EXPORT CREDITS (top up / check "
                 "app.apollo.io -> Settings -> Usage)") if _locked_hint else "",
            )
            if _locked_hint and stats is not None:
                # Drives the UI's 'credits' shortfall reason instead of the
                # misleading 'no_matches'.
                stats["apollo_credit_error"] = True
        else:
            logger.info(
                "EMAIL REVEAL RESULT — all %d candidate(s) revealed with "
                "usable emails.", len(locked),
            )

    # Stats out — lets the caller advance its page cursors by what was walked.
    if stats is not None:
        stats["pages_walked"] = pages_walked
        stats["start_page"] = start_page
        stats["end_page"] = start_page + pages_walked - 1 if pages_walked else start_page - 1
        stats["fresh_search_rows"] = fresh_search_rows
        stats["candidates_returned"] = len(out)
        stats["broader_pages_walked"] = broader_pages_walked
        stats["broader_start_page"] = broader_start_page
        # Plan #1: total matching rows Apollo reported + whether we drained them.
        stats["apollo_total_entries"] = apollo_total_entries
        stats["apollo_exhausted"] = apollo_exhausted

    return out


def _pick_email(match: Dict[str, Any]) -> Optional[str]:
    """Pick the first real email from a bulk_match row, checking every field
    Apollo may use (email / work_email_address / personal_emails / primary_email)
    — not just `email`, which is often null. Lowercased, or None if none usable."""
    def _clean(v: Any) -> Optional[str]:
        s = (str(v or "")).strip().lower()
        if not s or s.startswith("email_not_unlocked") or "@" not in s:
            return None
        return s

    # Try direct fields first
    for key in ("email", "work_email_address", "primary_email"):
        em = _clean(match.get(key))
        if em:
            return em
    # Then check the personal_emails list
    personals = match.get("personal_emails") or []
    if isinstance(personals, list):
        for p in personals:
            em = _clean(p)
            if em:
                return em
    return None


def _strip_internal(c: Dict[str, Any]) -> Dict[str, Any]:
    """Project a candidate down to the lead_discovery output contract. Every
    key here must be in the allow-list or it silently never reaches the DB."""
    return {
        "email": c.get("email"),
        "first_name": c.get("first_name"),
        "last_name": c.get("last_name"),
        "role": c.get("role"),
        "company_name": c.get("company_name"),
        "company_domain": c.get("company_domain"),
        "linkedin_url": c.get("linkedin_url"),
        "person_city": c.get("person_city"),
        "person_state": c.get("person_state"),
        "person_country": c.get("person_country"),
        "organization_industry": c.get("organization_industry"),
        "phone": c.get("phone"),
        "source": c.get("source") or "apollo",
        "raw": c.get("raw") or {},
        # 2026-06-11 — carried through for the duplicate "Already in
        # campaign" markers: Agent #10's re-vet score (drives the MATCH
        # pill) and the known-duplicate flag. This allow-list silently ATE
        # them at first (campaign 71's markers all stored score 0), so:
        # any new internal key MUST be added here or it never reaches the
        # attach loop.
        "_agent10_score": c.get("_agent10_score"),
        "_dup_known": c.get("_dup_known"),
    }


__all__ = ["search_people"]
