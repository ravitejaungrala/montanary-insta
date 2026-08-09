"""ICP fit scorer for NEXUS leads.

Returns an integer 0..100 representing how well a lead matches the same
6 fields we sent to Apollo to filter the lead in the first place.

2026-05-29 — Scoring now aligns 1:1 with Apollo's filter body.
Previously the scorer included `pain_points` (a scraped-text axis that
had no equivalent in Apollo's filter), which meant leads could pass
Apollo's filter perfectly and still score low because the pain-axis
denominator weighed them down. With pure alignment, a lead that
matches everything Apollo was asked to filter for scores 100.

The 6 dimensions = the 6 Apollo filter fields:

    1. person_titles            (25 pts)  — titles
    2. person_locations         (10 pts)  — country/state
    3. person_departments       (10 pts)  — functional area
    4. organization_industries  (25 pts)  — industry
    5. revenue_range            (15 pts)  — company size
    6. buyer_technologies       (15 pts)  — tech stack

    Total = 100 pts

Missing data on EITHER side (ICP doesn't define the axis, OR Apollo's
response doesn't include the field) reduces the slot's weight rather
than zeroing it out — we cannot penalise a lead for an ICP axis that
wasn't requested.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

_DEFAULT_WEIGHTS: Dict[str, int] = {
    "titles": 25,
    "locations": 10,
    "departments": 10,
    "industries": 25,
    "revenue": 15,
    "technologies": 15,
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


_TITLE_ACRONYMS = {
    "ceo": "chief executive officer",
    "cfo": "chief financial officer",
    "coo": "chief operating officer",
    "cto": "chief technology officer",
    "cio": "chief information officer",
    "cmo": "chief marketing officer",
    "cro": "chief revenue officer",
    "cpo": "chief product officer",
    "chro": "chief human resources officer",
    "cdo": "chief data officer",
    "ciso": "chief information security officer",
    "cso": "chief strategy officer",
    "vp": "vice president",
    "evp": "executive vice president",
    "svp": "senior vice president",
    "avp": "associate vice president",
    "md": "managing director",
    "dir": "director",
    "mgr": "manager",
    "eng": "engineering",
    "ops": "operations",
    "hr": "human resources",
    "biz dev": "business development",
    "bd": "business development",
    "it": "information technology",
}


def _expand_acronyms(text: str) -> str:
    """Return `text` with common B2B title acronyms replaced by full forms,
    so 'CFO' and 'Chief Financial Officer' both normalize to the same
    string for substring matching."""
    if not text:
        return text
    out = text
    for k, v in _TITLE_ACRONYMS.items():
        # Match the acronym as a whole word (basic word-boundary check).
        # Lowercased before this point.
        out = " ".join(
            v if w == k else w for w in out.replace(",", " , ").split()
        )
    return " ".join(out.split())


def _any_match(needles: Optional[Iterable[Any]], haystack: Any) -> bool:
    """True if any needle appears as a substring of haystack (case insensitive),
    with acronym expansion so e.g. "CFO" matches "Chief Financial Officer"."""
    if not needles or haystack is None:
        return False
    hay = _norm(haystack)
    if not hay:
        return False
    hay_expanded = _expand_acronyms(hay)
    for n in needles:
        n_norm = _norm(n)
        if not n_norm:
            continue
        n_expanded = _expand_acronyms(n_norm)
        if (
            n_norm in hay
            or hay in n_norm
            or n_expanded in hay_expanded
            or hay_expanded in n_expanded
        ):
            return True
    return False


def _any_overlap(
    needles: Optional[Iterable[Any]], items: Optional[Iterable[Any]]
) -> bool:
    if not needles or not items:
        return False
    needle_set = {_norm(n) for n in needles if _norm(n)}
    if not needle_set:
        return False
    for it in items:
        v = _norm(it)
        if not v:
            continue
        for n in needle_set:
            if n in v or v in n:
                return True
    return False


def _parse_money(s: Any) -> Optional[int]:
    """Parse '$500M', '500M', '$1.2B', '50000', etc. into a raw number.
    Returns None on failure. Used so company-size matches handle the
    multiple revenue formats Apollo returns."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    t = str(s).strip().lower().replace("$", "").replace(",", "")
    if not t:
        return None
    # Strip suffix
    mult = 1
    if t.endswith("k"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    elif t.endswith("b"):
        mult, t = 1_000_000_000, t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        return None


def _company_size_match(buckets: Optional[Iterable[str]], size_value: Any) -> bool:
    """Match either an explicit bucket label ('11-50') or a numeric headcount.
    Now also handles '$500M' / '$1.2B' style strings via _parse_money."""
    if not buckets:
        return False

    # Try to parse size_value as a number (handles '$500M', 500_000_000, etc.)
    parsed_n = _parse_money(size_value)
    if parsed_n is not None:
        n = parsed_n
        for b in buckets:
            b = str(b).strip()
            if "-" in b:
                lo, hi = b.split("-", 1)
                lo_n = _parse_money(lo)
                hi_n = _parse_money(hi)
                if lo_n is not None and hi_n is not None and lo_n <= n <= hi_n:
                    return True
                # Also try raw integer parse (e.g. headcount buckets '11-50').
                try:
                    if int(lo) <= n <= int(hi):
                        return True
                except ValueError:
                    continue
            elif b.endswith("+"):
                try:
                    if n >= int(b[:-1]):
                        return True
                except ValueError:
                    continue
        return False
    return _any_match(buckets, size_value)


def score_icp_fit(
    lead: Dict[str, Any],
    icp: Optional[Dict[str, Any]],
    weights: Optional[Dict[str, int]] = None,
) -> int:
    """Fit score — currently DISABLED, returns 100 for every lead.

    2026-05-29 — Per user request, the 6-axis Apollo-aligned scorer
    was producing a wide spread (0–100) that often surfaced 0-score
    "Apollo false positives" in the UI. Until we revisit a smarter
    scoring strategy (e.g. soft keyword matching + intent signal),
    the function returns a constant 100 so every Apollo-returned
    lead reads as "fit" in the LeadsTable and GTM Journey.

    The original 6-dimension scoring logic is intentionally kept
    BELOW (after the early return) — commented context retained so
    we can re-enable later without rewriting from scratch.

    Always returns INTEGER 100.
    """
    return 100

    # ─────────────────────────────────────────────────────────────────
    # LEGACY scoring logic — disabled per user request. Kept here for
    # quick re-enable: just remove the `return 100` above.
    # ─────────────────────────────────────────────────────────────────
    if not icp:
        return 50  # neutral when ICP is empty

    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    earned = 0
    available = 0

    def _get(*keys):
        for k in keys:
            v = icp.get(k)
            if isinstance(v, dict) and isinstance(v.get("values"), list):
                v = v["values"]
            if v:
                return v
        return None

    def _has_value(v) -> bool:
        if v is None:
            return False
        if isinstance(v, (list, tuple, set, dict)) and len(v) == 0:
            return False
        if isinstance(v, str) and not v.strip():
            return False
        return True

    # ── 1. Titles (25 pts) ──────────────────────────────────────────
    title_vals = _get("person_titles", "roles", "titles")
    lead_title = lead.get("title") or lead.get("role")
    if title_vals and _has_value(lead_title):
        available += w["titles"]
        if _any_match(title_vals, lead_title):
            earned += w["titles"]

    # ── 2. Industries (25 pts) ──────────────────────────────────────
    industry_vals = _get("organization_industries", "industry", "industries")
    lead_industry = lead.get("organization_industry") or lead.get("industry")
    if industry_vals and _has_value(lead_industry):
        available += w["industries"]
        if _any_match(industry_vals, lead_industry):
            earned += w["industries"]

    # ── 3. Revenue / Company Size (15 pts) ──────────────────────────
    size_vals = _get("revenue_range", "company_size", "headcount_range")
    lead_size = lead.get("revenue") or lead.get("company_size") or lead.get("headcount")
    if size_vals and _has_value(lead_size):
        available += w["revenue"]
        if _company_size_match(size_vals, lead_size):
            earned += w["revenue"]

    # ── 4. Technologies (15 pts) ────────────────────────────────────
    tech_vals = _get("buyer_technologies", "tech_stack", "technologies")
    lead_techs = lead.get("tech_stack") or lead.get("technologies") or []
    if tech_vals and _has_value(lead_techs):
        available += w["technologies"]
        if _any_overlap(tech_vals, lead_techs):
            earned += w["technologies"]

    # ── 5. Locations (10 pts) ───────────────────────────────────────
    location_vals = _get("person_locations", "locations")
    lead_loc = (
        lead.get("person_country") or lead.get("person_state")
        or lead.get("country") or lead.get("location")
    )
    if location_vals and _has_value(lead_loc):
        available += w["locations"]
        if _any_match(location_vals, lead_loc):
            earned += w["locations"]

    # ── 6. Departments (10 pts) ─────────────────────────────────────
    department_vals = _get("person_departments", "departments")
    lead_depts = lead.get("departments") or lead.get("department") or []
    if isinstance(lead_depts, str):
        lead_depts = [lead_depts] if lead_depts.strip() else []
    if department_vals and _has_value(lead_depts):
        available += w["departments"]
        if _any_overlap(department_vals, lead_depts):
            earned += w["departments"]

    if available == 0:
        # ICP provided no axes — treat as neutral pass-through.
        return 50

    # Always return an INTEGER 0..100 — no decimals shown in UI.
    return int(max(0, min(100, round(earned * 100 / available))))


__all__ = ["score_icp_fit"]
