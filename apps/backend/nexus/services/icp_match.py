"""Strict ICP membership test — does a concrete lead actually match the
user's targeting filters?

WHY THIS EXISTS (2026-06-01)
Apollo's `person_titles` filter is a FUZZY *contains* match: asking for "CEO"
also returns "Deputy CEO", "Assistant to the CEO", "CEO Office Manager", etc.
And because /analyze reuses a campaign across runs (and never clears prior
leads), the lead table accumulated the UNION of every targeting iteration the
user tried. Net effect the user saw: "the leads don't match what I entered."

This module provides a strict, deterministic membership test used in two places:
  1. discovery_apollo — drop search hits whose title doesn't match BEFORE the
     paid bulk_match enrichment (saves credits + never enrolls/emails them).
  2. campaign_leads.load_leads_for_campaign — show ONLY leads matching the
     CURRENT campaign ICP, so stale prior-run leads disappear from the table.

Design: "better few but correct" (the user's explicit ask). Title matching is
strict — abbreviations are expanded (CEO == "Chief Executive Officer"), but
role-changing qualifiers (Deputy / Assistant / Associate / Interim / …) DISQUALIFY
a hit unless the user asked for them. Location is matched at the granularity the
user picked (country / state+country / city+state+country).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Single-token abbreviation → canonical full form. Applied token-by-token so
# "CEO" and "Chief Executive Officer" canonicalize to the same string.
_ABBREV = {
    "ceo": "chief executive officer", "cfo": "chief financial officer",
    "coo": "chief operating officer", "cto": "chief technology officer",
    "cmo": "chief marketing officer", "cpo": "chief product officer",
    "ciso": "chief information security officer", "cio": "chief information officer",
    "cro": "chief revenue officer", "cdo": "chief data officer",
    "chro": "chief human resources officer", "cco": "chief commercial officer",
    "vp": "vice president", "svp": "senior vice president",
    "evp": "executive vice president", "avp": "assistant vice president",
    "hr": "human resources", "it": "information technology",
    "bd": "business development", "sdr": "sales development representative",
    "ae": "account executive", "pm": "product manager", "gm": "general manager",
    "md": "managing director", "sr": "senior", "jr": "junior",
}

# Tokens that CHANGE the role — a hit is disqualified if it sits BEFORE the
# requested phrase and isn't a benign seniority modifier. ("Deputy CEO" ≠ "CEO".)
_NEGATIVE = {
    "deputy", "assistant", "asst", "associate", "interim", "acting",
    "former", "ex", "aspiring", "retired", "intern", "trainee", "freelance",
}

# Benign modifiers allowed to PRECEDE the requested role without changing it.
# "Senior Vice President, Sales" still matches "VP Sales".
_PREFIX_MOD = {
    "senior", "junior", "sr", "jr", "global", "regional", "principal", "staff",
    "group", "corporate", "national", "international", "divisional", "lead",
}

# Role nouns that, when they appear AFTER the requested phrase, mean a DIFFERENT
# primary role. "CEO Office Manager" → 'manager' after "CEO" ⇒ not a CEO.
_ROLE_NOUNS = {
    "manager", "officer", "president", "director", "head", "chief",
    "executive", "engineer", "analyst", "representative", "specialist",
    "coordinator", "administrator", "assistant", "intern", "consultant",
    "advisor", "adviser", "partner", "owner", "founder", "cofounder",
    "agent", "clerk", "supervisor", "technician", "designer", "developer",
    "architect", "scientist", "accountant", "controller", "treasurer",
    "secretary", "counsel", "attorney", "recruiter", "planner", "buyer",
    "trader", "broker", "banker", "generalist", "strategist", "ambassador",
}

# Filler tokens dropped before comparison so "VP of Sales" == "VP Sales".
_FILLER = {"of", "the", "for", "to", "at", "in", "on", "a", "an", "and"}

_COUNTRY_ALIAS = {
    "usa": "united states", "us": "united states", "u.s.": "united states",
    "u.s.a.": "united states", "america": "united states",
    "united states of america": "united states",
    "uk": "united kingdom", "u.k.": "united kingdom", "britain": "united kingdom",
    "great britain": "united kingdom", "england": "united kingdom",
    "uae": "united arab emirates", "korea": "south korea",
}


def _values(raw: Any) -> List[str]:
    """Pull a list of strings from a plain list OR the {values,...} triple."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, dict):
        raw = raw.get("values")
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if isinstance(v, str) and v.strip()]
    return []


def _expand(tokens: List[str]) -> List[str]:
    """Expand abbreviations token-by-token, drop filler."""
    out: List[str] = []
    for tok in tokens:
        for sub in _ABBREV.get(tok, tok).split():
            if sub not in _FILLER:
                out.append(sub)
    return out


def _req_tokens(s: str) -> List[str]:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return _expand([t for t in s.split() if t not in _FILLER])


def _title_clauses(title: str) -> List[List[str]]:
    """Split a title into role CLAUSES so dual titles like 'CEO & Founder'
    or 'CMO, General Manager' are TWO clauses, not one.

    2026-06-02 fix: comma was previously treated as filler (kept inside
    a single clause). That made the "different role-noun AFTER the
    requested title" rejection misfire on real dual titles —
    'CMO, General Manager' (legit CMO) was dropped because 'manager'
    looked like a follow-on role. Comma, semicolon, ampersand, pipe,
    and slash are now ALL clause separators (a "punctuation firewall").
    Dashes are kept inside a clause so 'Co-Founder' stays one word.

    Plain "VP, Sales" still works: clause 1 = ['vp'] matches a 'VP'
    request; clause 2 = ['sales'] is ignored.
    """
    s = (title or "").lower()
    # Clause-separator punctuation → sentinel " and " which the split below
    # uses as the canonical break-point.
    s = re.sub(r"[&/|,;]", " and ", s)
    # Strip any remaining non-alphanum garbage (parens, dots, etc.).
    # Dashes are NOT stripped here — they sit inside words like 'co-founder'
    # which we keep as a single token.
    s = re.sub(r"[^a-z0-9 \-]+", " ", s)
    clauses: List[List[str]] = []
    for part in re.split(r"\band\b", s):
        toks = _expand([t for t in part.split() if t not in _FILLER])
        if toks:
            clauses.append(toks)
    return clauses


def title_matches(title: str, requested: List[str]) -> bool:
    """True iff `title` is a STRICT match for at least one requested role.

    A clause matches a requested role when the role's token sequence appears
    contiguously in the clause AND
      - every token BEFORE it is a benign seniority modifier (Senior/Global/…),
        never a role-changing qualifier (Deputy/Assistant/Former/…), and
      - no token AFTER it is a different role noun (so "CEO" ≠ "CEO Office
        Manager", but "CEO & Founder" / "VP Sales Operations" still match).
    """
    reqs = _values(requested)
    if not reqs:
        return True  # no title filter set → everyone passes
    clauses = _title_clauses(title)
    if not clauses:
        return False
    for req in reqs:
        rt = _req_tokens(req)
        if not rt:
            continue
        n = len(rt)
        for clause in clauses:
            for i in range(0, len(clause) - n + 1):
                if clause[i:i + n] != rt:
                    continue
                before, after = clause[:i], clause[i + n:]
                # Tokens BEFORE the role may be benign seniority modifiers
                # (Senior/Global/…) OR a CO-ROLE the person also holds
                # ("Founder, CEO", "President & CEO" written without a
                # separator). Reject ONLY a role-changing qualifier
                # (Deputy/Assistant/Interim/Former/…).
                if any(b in _NEGATIVE for b in before):
                    continue
                # Tokens after the role may be department/function words OR a
                # RESTATEMENT of the same role (very common: "Chief Executive
                # Officer (CEO)", "CEO, Chief Executive Officer"). Reject only
                # when an after-token is a DIFFERENT role noun / qualifier that
                # isn't part of the requested role itself.
                rt_set = set(rt)
                if any((a in _ROLE_NOUNS or a in _NEGATIVE) and a not in rt_set
                       for a in after):
                    continue
                return True
    return False


def _norm_loc(s: Optional[str]) -> str:
    v = (s or "").strip().lower()
    return _COUNTRY_ALIAS.get(v, v)


def location_matches(
    city: Optional[str], state: Optional[str], country: Optional[str],
    requested: List[str],
) -> bool:
    """True iff the lead's location satisfies at least one requested location,
    at the granularity the user picked. Requested strings look like
    'Country' | 'State, Country' | 'City, State, Country'."""
    reqs = _values(requested)
    if not reqs:
        return True
    lc, ls, lcountry = _norm_loc(city), _norm_loc(state), _norm_loc(country)
    for req in reqs:
        parts = [_norm_loc(p) for p in req.split(",") if p.strip()]
        if not parts:
            continue
        if len(parts) == 1:                      # country only
            if parts[0] and parts[0] == lcountry:
                return True
        elif len(parts) == 2:                    # state, country
            if parts[1] == lcountry and parts[0] and parts[0] == ls:
                return True
        else:                                    # city, state, country
            if parts[-1] == lcountry and parts[-2] == ls and parts[0] and parts[0] == lc:
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────
# Industry + department strict matchers (2026-06-02)
#
# Added alongside title + country so the post-Apollo gate enforces ALL the
# strict-page-quoted ICP axes, not just title/country. Apollo's industry
# tag-ID filter is usually exact, but the per-lead `organization_industry`
# we store back from Apollo can carry a multi-industry company on a single
# label that doesn't byte-match our requested label (e.g. requested
# "Insurance" but Apollo returned "Financial Services"). For these cases
# we want to drop the lead.
# ─────────────────────────────────────────────────────────────────────────

# Coarse industry aliasing — close-enough labels that should still count as
# a match. Keep this list short; do NOT use it to widen the filter, just to
# bridge byte-level differences between the ICP picker and Apollo's stored
# label set.
_INDUSTRY_ALIAS = {
    "financial services": "finance",
    "bfsi": "banking",
    "fintech": "finance",
    "ecommerce": "e-commerce",
    "e commerce": "e-commerce",
    "saas": "software",
    "info tech": "information technology",
    # NOTE: "it services" → "information technology" was REMOVED 2026-06-02.
    # It rewrote the lead's stored industry into "information technology",
    # which then failed word-boundary match against a request for "IT"
    # (since "information technology" contains no whole "it" word). Without
    # the alias, request "IT" word-boundary-matches "IT Services" directly
    # — which is what the operator expects.
}


def _norm_industry(s: Optional[str]) -> str:
    v = (s or "").strip().lower()
    return _INDUSTRY_ALIAS.get(v, v)


# 2026-06-04 — Compound industry labels carry MULTIPLE sub-industries joined by
# &, /, |, comma, or "and" (e.g. Apollo stores "marketing & advertising" while
# the ICP picker emits "Advertising & Marketing"). Comparing the whole phrase
# byte-for-byte fails on word-order differences, which silently hid every lead
# whose industry was just a reordered form of the requested one. Splitting both
# sides into sub-industry "atoms" and matching on atom overlap fixes that.
_INDUSTRY_SPLIT_RE = re.compile(r"\s*(?:[&/|,]|\band\b)\s*", re.IGNORECASE)


def _industry_atoms(s: Optional[str]) -> List[str]:
    """Split a (possibly compound) industry label into normalized sub-industry
    atoms. 'Advertising & Marketing' → ['advertising', 'marketing']. A label
    with no separator ('information technology') stays a single atom."""
    if not s:
        return []
    return [
        a for a in (_norm_industry(p) for p in _INDUSTRY_SPLIT_RE.split(s))
        if a
    ]


# 2026-06-02 — switched from raw substring (`req in lead`) to WORD-BOUNDARY
# matching for industry + department. Without word boundaries, a 2-letter
# request like "it" would match inside "architecture" / "security"; "hr"
# would match inside "shrubbery"; "pr" would match inside "apparel" or
# "printing". Word boundaries (`\bit\b`) make the gate behave the way an
# operator expects.
def _word_in(needle: str, haystack: str) -> bool:
    """Case-insensitive whole-word substring check. Returns True iff
    `needle` appears as one or more whole tokens inside `haystack`."""
    if not needle or not haystack:
        return False
    # re.escape handles any punctuation in the needle (rare for industry/
    # department but safe). `\b` matches a word boundary on both sides.
    return re.search(rf"\b{re.escape(needle)}\b", haystack, re.IGNORECASE) is not None


def industry_matches(
    lead_industry: Optional[str], requested: List[str],
) -> bool:
    """True iff the lead's stored industry intersects at least one requested
    industry. Uses **word-boundary** matching (not raw substring) so
    short labels like 'IT' don't accidentally match 'architecture'.

    Alias table catches close-enough labels ('Financial Services' ↔
    'Finance', 'BFSI' ↔ 'Banking', 'SaaS' ↔ 'Software').
    """
    reqs = _values(requested)
    if not reqs:
        return True
    if not lead_industry:
        # No industry on the lead → don't hide them. Apollo's tag filter
        # already constrained this at search time.
        return True
    lead_n = _norm_industry(lead_industry)
    lead_atoms = _industry_atoms(lead_industry)
    for req in reqs:
        req_n = _norm_industry(req)
        if not req_n:
            continue
        if req_n == lead_n:
            return True
        # Whole-word match in either direction. "Insurance" matches
        # "Insurance & Reinsurance"; "IT" matches "IT Services" but
        # NOT "Architecture".
        if _word_in(req_n, lead_n) or _word_in(lead_n, req_n):
            return True
        # Sub-industry atom overlap — order-insensitive match for compound
        # labels. "Advertising & Marketing" (ICP) matches "marketing &
        # advertising" (Apollo's stored label) because both contain the
        # 'advertising'/'marketing' atoms.
        for ra in _industry_atoms(req):
            for la in lead_atoms:
                if ra == la or _word_in(ra, la) or _word_in(la, ra):
                    return True
    return False


def department_matches(
    lead_department: Optional[str], requested: List[str],
) -> bool:
    """True iff the lead's department matches at least one requested
    department. Apollo's department enum is closed (engineering / sales /
    marketing / data / it / hr / finance / operations / …); we use
    **word-boundary** matching so 'it' doesn't match 'architecture' and
    'hr' doesn't match a department whose name happens to contain those
    letters.
    """
    reqs = _values(requested)
    if not reqs:
        return True
    if not lead_department:
        return True
    lead_n = (lead_department or "").strip().lower()
    for req in reqs:
        req_n = (req or "").strip().lower()
        if not req_n:
            continue
        if req_n == lead_n:
            return True
        if _word_in(req_n, lead_n) or _word_in(lead_n, req_n):
            return True
    return False


def lead_matches_icp(
    *, title: str, city: Optional[str], state: Optional[str],
    country: Optional[str], icp: Dict[str, Any],
    industry: Optional[str] = None,
    department: Optional[str] = None,
) -> bool:
    """Strict membership: does this lead match the campaign's CURRENT ICP?

    Only the dimensions the user actually set are enforced. Title, location,
    industry, and department are the leakage-prone axes (fuzzy at Apollo's
    side OR brittle byte-level comparison vs Apollo's stored label), so
    those are what we gate on. Revenue + technologies are trusted to Apollo.
    """
    if not isinstance(icp, dict):
        return True
    if not title_matches(title, icp.get("person_titles")):
        return False
    # Location: enforce at COUNTRY granularity only. Apollo already applies the
    # precise city/state/country filter at SEARCH time (where precision
    # matters for WHO is found), so this display-time check just needs to drop
    # STALE prior-run leads from a different country. Re-checking city/state
    # here is fragile — Apollo's stored "CA"/"Bengaluru" may not byte-match the
    # picker's "California"/"Bangalore", which would wrongly hide valid leads.
    # A lead with no stored country is assumed in-region (not hidden).
    req_countries = set()
    for loc in _values(icp.get("person_locations")):
        parts = [p for p in loc.split(",") if p.strip()]
        if parts:
            req_countries.add(_norm_loc(parts[-1]))
    if req_countries and country:
        if _norm_loc(country) not in req_countries:
            return False
    if not industry_matches(industry, icp.get("organization_industries")):
        return False
    if not department_matches(department, icp.get("person_departments")):
        return False
    return True
