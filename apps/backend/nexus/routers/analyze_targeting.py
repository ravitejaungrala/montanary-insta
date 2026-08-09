"""POST /nexus/analyze/suggest-targeting

Dedicated Gemini call used by the NewCampaign wizard to auto-fill the
ICP targeting fields (locations, industries, revenue, roles) from the
product summary the user just approved.

Legacy: apps/nexus-legacy/server/routes/analyze.js → '/suggest-targeting'

Why a separate router file: the main analyze.py owns the heavy
scrape+commit pipeline; this is a lightweight LLM call with allowed-list
validation. Keeping it isolated makes it easy to swap the validation
lists without touching the commit path.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import require_current_workspace
from nexus.services import gemini

logger = logging.getLogger("pipelyt.nexus.analyze_targeting")

router = APIRouter(prefix="/nexus/analyze", tags=["Nexus — Analyze"])


# Allowed values — Gemini's output is filtered against these so the
# frontend MultiSelects always receive recognised values.
ALLOWED_LOCATIONS: List[str] = [
    "United States", "United Kingdom", "Canada", "Australia", "New Zealand",
    "Germany", "France", "Netherlands", "Sweden", "Denmark", "Norway", "Finland",
    "Ireland", "Switzerland", "Belgium", "Austria", "Spain", "Italy", "Portugal",
    "Poland", "Czech Republic", "Luxembourg",
    "Singapore", "India", "Japan", "South Korea", "Taiwan", "Hong Kong",
    "Malaysia", "Indonesia", "Philippines", "Thailand", "Vietnam", "China",
    "United Arab Emirates", "Saudi Arabia", "Israel", "Qatar", "Bahrain",
    "South Africa", "Nigeria", "Kenya", "Egypt",
    "Brazil", "Mexico", "Argentina", "Colombia", "Chile",
]

ALLOWED_INDUSTRIES: List[str] = [
    # 2026-06-11: Apollo's FULL native industry taxonomy (145 labels) — the
    # user asked the dropdown to mirror Apollo 1:1. Each label resolves to a
    # verified tag ID via apollo_industry_map (curated map + native fallback);
    # legacy curated labels (SaaS, FinTech, ...) still resolve as aliases for
    # ICPs stored by older runs.
    "Accounting", "Agriculture", "Airlines/Aviation", "Alternative Dispute Resolution",
    "Alternative Medicine", "Animation", "Apparel & Fashion",
    "Architecture & Planning", "Arts & Crafts", "Automotive", "Aviation & Aerospace",
    "Banking", "Biotechnology", "Broadcast Media", "Building Materials",
    "Business Supplies & Equipment", "Capital Markets", "Chemicals",
    "Civic & Social Organization", "Civil Engineering", "Commercial Real Estate",
    "Computer & Network Security", "Computer Games", "Computer Hardware",
    "Computer Networking", "Computer Software", "Construction", "Consumer Electronics",
    "Consumer Goods", "Consumer Services", "Cosmetics", "Dairy", "Defense & Space",
    "Design", "E-Learning", "Education Management",
    "Electrical/Electronic Manufacturing", "Entertainment", "Environmental Services",
    "Events Services", "Executive Office", "Facilities Services", "Farming",
    "Financial Services", "Fine Art", "Fishery", "Food & Beverages", "Food Production",
    "Fund-Raising", "Furniture", "Gambling & Casinos", "Glass, Ceramics & Concrete",
    "Government Administration", "Government Relations", "Graphic Design",
    "Health, Wellness & Fitness", "Higher Education", "Hospital & Health Care",
    "Hospitality", "Human Resources", "Import & Export",
    "Individual & Family Services", "Industrial Automation", "Information Services",
    "Information Technology & Services", "Insurance",
    "International Trade & Development", "Internet", "Investment Banking",
    "Investment Management", "Judiciary", "Law Enforcement", "Law Practice",
    "Legal Services", "Leisure, Travel & Tourism", "Libraries",
    "Logistics & Supply Chain", "Luxury Goods & Jewelry", "Machinery",
    "Management Consulting", "Maritime", "Market Research", "Marketing & Advertising",
    "Mechanical Or Industrial Engineering", "Media Production", "Medical Devices",
    "Medical Practice", "Mental Health Care", "Military", "Mining & Metals",
    "Motion Pictures & Film", "Music", "Nanotechnology", "Newspapers",
    "Nonprofit Organization Management", "Oil & Energy", "Online Media",
    "Outsourcing/Offshoring", "Package/Freight Delivery", "Packaging & Containers",
    "Paper & Forest Products", "Performing Arts", "Pharmaceuticals", "Philanthropy",
    "Photography", "Plastics", "Political Organization", "Primary/Secondary Education",
    "Printing", "Professional Training & Coaching", "Program Development",
    "Public Policy", "Public Relations & Communications", "Public Safety",
    "Publishing", "Railroad Manufacture", "Ranching", "Real Estate",
    "Recreational Facilities & Services", "Religious Institutions",
    "Renewables & Environment", "Research", "Restaurants", "Retail",
    "Security & Investigations", "Semiconductors", "Shipbuilding", "Sporting Goods",
    "Sports", "Staffing & Recruiting", "Supermarkets", "Telecommunications",
    "Textiles", "Think Tanks", "Tobacco", "Translation & Localization",
    "Transportation/Trucking/Railroad", "Utilities",
    "Venture Capital & Private Equity", "Veterinary", "Warehousing", "Wholesale",
    "Wine & Spirits", "Wireless", "Writing & Editing",
]

ALLOWED_ROLES: List[str] = [
    "CEO", "CFO", "CTO", "COO", "CMO", "CPO", "CISO", "CRO", "CDO",
    "President", "Founder", "Co-Founder", "Managing Director", "General Manager",
    "VP Engineering", "VP Sales", "VP Marketing", "VP Product", "VP Operations",
    "VP Finance", "VP IT", "VP Business Development", "VP Customer Success",
    "Director of Engineering", "Director of Sales", "Director of Marketing",
    "Director of Product", "Director of Operations", "Director of IT",
    "Head of Engineering", "Head of Product", "Head of Sales", "Head of Marketing",
    "Head of Data", "Head of Operations", "Engineering Manager", "Product Manager",
    "Project Manager", "Sales Manager", "Marketing Manager",
    "Senior Software Engineer", "Software Engineer", "Staff Engineer",
    "Principal Engineer", "Data Scientist", "Data Engineer", "ML Engineer",
    "DevOps Engineer", "Platform Engineer", "Site Reliability Engineer",
    "Account Executive", "Business Development Manager",
    "Sales Development Representative", "Growth Manager", "Customer Success Manager",
    "Account Manager", "IT Manager",
]

# 2026-06-02 — "Any" and "Pre-revenue" removed. Both mapped to "no
# filter" in discovery_apollo._revenue_band_to_apollo, so they did
# nothing functionally. The wizard now shows only real revenue bands;
# if the user picks nothing, no revenue_range filter is sent to Apollo.
# MUST stay in sync with REVENUE_OPTIONS in apps/product-page/.../targetingData.js.
REVENUE_OPTIONS: List[str] = [
    "< $1M", "$1M – $10M", "$10M – $50M",
    "$50M – $200M", "$200M – $1B", "$1B+",
]

# NOTE: ALLOWED_SENIORITIES + person_seniorities was REMOVED 2026-06-02.
# Titles (person_titles) already imply seniority and sending both was
# over-narrowing Apollo's match set. The wizard chip row + the gemini
# prompt schema were both updated accordingly.

# NOTE: ALLOWED_DEPARTMENTS + person_departments was REMOVED 2026-06-08.
# Titles (person_titles) already imply department and sending both was
# over-narrowing Apollo's match set — same rationale as the seniority
# removal above. The wizard chip row + the gemini prompt schema were both
# updated accordingly.

# Apollo's `currently_using_any_of_technology_uids` accepts any free-form
# string — Apollo silently no-ops unknown tech UIDs. We list common ones
# only as anchoring examples for the LLM. The frontend allows free-text
# entry on this row.
COMMON_TECHNOLOGIES: List[str] = [
    "Salesforce", "HubSpot", "Mulesoft", "SAP", "Oracle",
    "AWS", "Google Cloud", "Microsoft Azure",
    "Snowflake", "Databricks", "BigQuery", "Redshift",
    "Workday", "NetSuite", "ServiceNow",
    "Slack", "Microsoft Teams", "Zoom",
    "Jira", "GitHub", "GitLab", "Bitbucket",
    "Stripe", "Shopify", "Segment", "MongoDB", "PostgreSQL",
]


# ─────────────────────────────────────────────────────────────────────────────
# 2026-05-28 schema change
#   Was: 4 output keys (locations, industries, revenue, roles).
#   Now: 5 CANONICAL keys (person_titles, person_locations,
#        organization_industries, revenue_range, buyer_technologies).
#
# The new names match Apollo's wire field names (with 2 boundary renames
# documented in nexus/services/apollo_industry_map.py and the Apollo body
# builder). The React state, the /nexus/analyze POST body, the backend ICP
# dict, and the Apollo request body all use the same names — no mental
# translation needed when reading the code.
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are an expert B2B go-to-market strategist. Given a company "
    "summary you identify the ideal customer profile for outbound sales. "
    "Return ONLY a valid JSON object with the FIVE keys: person_titles, "
    "person_locations, organization_industries, "
    "revenue_range, buyer_technologies. "
    "Do not include any prose, markdown, code fences, or comments."
)


def _build_user_prompt(
    product_name: str, product_summary: str, entity_type: str = "product"
) -> str:
    # Frame the task differently depending on which of the THREE company
    # types we're analyzing (see `_ANALYZE_SYSTEM` in
    # nexus/services/gemini.py for the full type taxonomy):
    #   product → target = buyer companies + buyer decision-makers
    #   service → target = client companies + their decision-makers
    #   gcc     → target = the GCC's OWN tech leadership (heads of
    #             engineering / data / IT). organization_industries +
    #             revenue_range inherit from the parent multinational.
    et = entity_type.lower()
    if et == "service":
        framing = (
            "This company is a SERVICE business — they perform work for "
            "client companies. Suggest the geographies and industries of "
            "their IDEAL CLIENT companies, the typical CLIENT revenue band, "
            "the decision-maker titles AT those "
            "client companies who would sign a services engagement, and the "
            "technologies those clients already use (since the service "
            "typically plugs into them)."
        )
        company_label = "Service company"
    elif et == "gcc":
        # 2026-05-29 definition change: GCC = GCC SERVICE PROVIDER (e.g.
        # ANSR Global, Inductus, Globalization Partners, Velocity Global,
        # Multiplier). They help OTHER companies set up overseas
        # operations. The ICP target is the multinational BUYER, not the
        # provider's own staff.
        framing = (
            "This company is a GCC PROVIDER — a firm that helps OTHER "
            "companies set up their global operations / Global Capability "
            "Centers in new countries (office setup, talent acquisition, "
            "Employer-of-Record, regulatory compliance, cross-border "
            "payroll). Examples: ANSR, Inductus, Globalization Partners, "
            "Velocity Global, Multiplier, Remote.com.\n\n"
            "An external sales motion targets MULTINATIONAL BUYERS looking "
            "to expand abroad. Suggest:\n"
            "  - person_titles — decision-makers at the BUYER company "
            "(CHRO / Chief People Officer / COO / VP International "
            "Expansion / VP Global Talent / CFO / Head of People "
            "Operations).\n"
            "  - person_locations — where the BUYER is HEADQUARTERED "
            "(typically United States / United Kingdom / Western Europe), "
            "NOT where the GCC provider operates.\n"
            "  - organization_industries — industries of the BUYER "
            "companies the GCC provider says it serves.\n"
            "  - revenue_range — the BUYER's revenue band (most GCC "
            "clients are $50M-$1B+; small companies don't set up overseas "
            "operations).\n"
            "  - buyer_technologies — usually empty for GCC unless the "
            "page names a specific buyer-required tech stack."
        )
        company_label = "GCC provider"
    else:
        framing = (
            "This company sells a PRODUCT. Suggest the geographies and "
            "industries of the companies that would BUY/USE this product, "
            "the typical buyer revenue band, the decision-maker titles "
            "who would sign off on the purchase, "
            "and the technologies the buyer company is likely to already "
            "use (so Apollo can filter by integration fit)."
        )
        company_label = "Product"

    return (
        f"{framing}\n\n"
        f"{company_label}: {product_name or 'Unknown'}\n\n"
        f"Summary:\n{product_summary[:50000]}\n\n"
        "Pick values ONLY from the lists below, spelled verbatim. For "
        "`buyer_technologies` the COMMON examples are suggestions only — "
        "you may emit any technology name actually relevant to the page "
        "(verbatim from the page if it is named there).\n\n"
        f"ALLOWED person_locations:\n{', '.join(ALLOWED_LOCATIONS)}\n\n"
        f"ALLOWED organization_industries:\n{', '.join(ALLOWED_INDUSTRIES)}\n\n"
        f"ALLOWED revenue_range (pick ONE):\n{', '.join(REVENUE_OPTIONS)}\n\n"
        f"ALLOWED person_titles:\n{', '.join(ALLOWED_ROLES)}\n\n"
        f"COMMON buyer_technologies (examples — not exhaustive):\n"
        f"{', '.join(COMMON_TECHNOLOGIES)}\n\n"
        "Return JSON with this exact shape:\n"
        "{\n"
        '  "person_titles":           ["..."],\n'
        '  "person_locations":        ["..."],\n'
        '  "organization_industries": ["..."],\n'
        '  "revenue_range":           "...",\n'
        '  "buyer_technologies":      ["..."]\n'
        "}\n\n"
        "Guidance: 3-5 titles, 2-4 locations, "
        "3-5 industries, exactly ONE revenue band, "
        "2-5 technologies."
    )


def _extract_values(raw: Any) -> List[str]:
    """Accept either a plain list OR the canonical-triple shape that the
    analyze_product() output uses (`{values, confidence, evidence}`).

    The wizard often passes the analyze-output ICP straight through as a
    summary — when that happens the field is a triple, not a list.
    """
    if isinstance(raw, dict):
        vals = raw.get("values")
        return vals if isinstance(vals, list) else []
    if isinstance(raw, list):
        return raw
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Confidence gating (2026-06-02 — numeric)
#
# "Better an empty field than a wrong field." Confidence is now a single
# integer 0–100 emitted by the page-only Gemini call (see analyze_product in
# gemini.py). One threshold, one path:
#
#     fields with confidence >= MIN_CONFIDENCE → auto-filled
#     fields with confidence  < MIN_CONFIDENCE → blank + `suppressed: true`
#
# The previous high/medium/low string scheme + the Google-Search-grounded
# "deep research" branch were removed in this commit. The user's requirement
# was that every ICP value must trace back to a verbatim quote in the scraped
# pages — no Crunchbase / LinkedIn / news enrichment. The page-only path is
# now the only path.
#
# Backwards-compat: rows in `nexus_products.icp` written before this commit
# may still carry string confidences. _triple() maps them onto the 0-100 axis
# (high=90, medium=60, low=20) so the gate still works for legacy rows.
# Re-run /analyze to upgrade a product's ICP to native integer confidences.
# ─────────────────────────────────────────────────────────────────────────────
# Single auto-fill threshold for ALL targeting fields. 75 (was 80): with the
# model now reading the FULL scraped site (no truncation), its confidence is
# solid, so a slightly lower bar reliably surfaces fields like locations
# without letting through genuinely-weak guesses. Env-tunable.
MIN_CONFIDENCE = int(os.getenv("NEXUS_TARGETING_MIN_CONFIDENCE", "75"))

# Legacy mapping for `nexus_products.icp` rows written before the 2026-06-02
# numeric-confidence switch. Treat these as the centre of each band.
_LEGACY_STRING_CONFIDENCE = {"high": 90, "medium": 60, "low": 20}


def _to_int_confidence(raw: Any) -> int:
    """Coerce ANY confidence value (int 0-100, numeric string, or legacy
    high/medium/low) into an int in [0, 100]. Defaults to 0 when unknown."""
    if isinstance(raw, bool):
        # bools subclass int; treat as 0/100 explicitly so True/False don't
        # silently map to 1/0.
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


def _passes_confidence(
    confidence: Any, min_confidence: int = MIN_CONFIDENCE
) -> bool:
    return _to_int_confidence(confidence) >= int(min_confidence)


def _triple(raw: Any) -> tuple[List[str], int, str]:
    """Unpack a canonical filter field into (values, confidence_int, evidence).

    Tolerates the {values, confidence, evidence} triple (with int OR legacy
    string confidence), a plain list (no confidence → treated as 60), or
    anything else (→ empty / 0).
    """
    if isinstance(raw, dict):
        vals = raw.get("values")
        vals = vals if isinstance(vals, list) else ([vals] if vals else [])
        conf = _to_int_confidence(raw.get("confidence"))
        evidence = str(raw.get("evidence") or "").strip()
        return (
            [str(v).strip() for v in vals if isinstance(v, str) and v.strip()],
            conf,
            evidence,
        )
    if isinstance(raw, list):
        return (
            [str(v).strip() for v in raw if isinstance(v, str) and v.strip()],
            60,
            "",
        )
    return [], 0, ""


def _mappable_industries() -> set:
    """The industry labels Apollo actually filters on (everything else is
    silently dropped at the Apollo boundary — see apollo_industry_map). We
    only auto-fill industries from THIS set so a HIGH-confidence-but-unmapped
    label never looks applied while having zero effect on discovery."""
    try:
        from nexus.services.apollo_industry_map import (
            APOLLO_INDUSTRY_MAP,
            APOLLO_NATIVE_INDUSTRY_TAGS,
        )
        # Curated aliases + Apollo's full native taxonomy — the same two
        # lookups industries_to_tag_ids() resolves against. Without the
        # native set, the auto-fill gate would suppress the 2026-06-11
        # Apollo-native labels (e.g. "Investment Banking") that the prompt
        # now instructs Gemini to emit.
        return set(APOLLO_INDUSTRY_MAP.keys()) | set(APOLLO_NATIVE_INDUSTRY_TAGS.keys())
    except Exception:  # noqa: BLE001
        return set(ALLOWED_INDUSTRIES)


def _autofill_from_icp(
    icp: Dict[str, Any], *, min_confidence: int = MIN_CONFIDENCE
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the gated suggest-targeting response from an ICP triple dict.

    Returns (fields, meta). `fields` is the same 7-key shape the wizard already
    consumes; only values at/above `min_confidence` survive. `meta` carries
    per-field {confidence, evidence, suppressed} so the UI can show a badge +
    an evidence tooltip, and explain WHY a field was left blank.
    """
    fields: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}
    mappable = _mappable_industries()

    def _emit(key: str, raw: Any, *, cleaner=None, cap: int, min_conf: int = None) -> None:
        values, confidence, evidence = _triple(raw)
        passed = _passes_confidence(
            confidence, min_conf if min_conf is not None else min_confidence
        )
        out: List[str] = []
        if passed and values:
            out = (cleaner(values) if cleaner else values)[:cap]
        fields[key] = out
        meta[key] = {
            "confidence": confidence,
            "evidence": evidence,
            # suppressed = the model HAD candidate values but confidence was
            # below threshold, so we deliberately withheld them.
            "suppressed": bool(values) and not passed,
        }

    # person_titles — FREE-FORM (Apollo accepts any title string). No closed-
    # list filter so deep research can surface the real decision-maker title.
    # Cap 8 (was 5): the model returns 10-16 relevant titles and a cap of 5
    # was dropping obvious buyers (e.g. CMO for a marketing tool). Apollo ORs
    # titles, so more relevant titles = broader, better coverage. Apollo's own
    # body builder caps at 10, so 8 stays safely under that.
    _emit("person_titles", icp.get("person_titles"),
          cleaner=lambda vs: [v[:60] for v in vs], cap=8)

    # person_locations — Gemini emits the deepest granularity the page
    # supports (e.g. "Texas, United States" or "United States"). Apollo
    # accepts any granularity in this single field. Gated like the rest at the
    # global threshold — reliability comes from feeding the model the FULL
    # scraped content (no truncation), so its location confidence is solid.
    _emit("person_locations", icp.get("person_locations"),
          cleaner=lambda vs: [v[:60] for v in vs], cap=6)

    # person_seniorities REMOVED 2026-06-02, person_departments REMOVED
    # 2026-06-08 — both over-narrowed Apollo; titles already imply them.

    # organization_industries — restrict to the Apollo-EFFECTIVE set so an
    # auto-filled industry always actually filters discovery.
    _emit("organization_industries", icp.get("organization_industries"),
          cleaner=lambda vs: [v for v in vs if v in mappable], cap=6)

    # buyer_technologies — keep only techs that resolve to a real Apollo UID
    # (resolve_technology understands casual spellings like "AWS"). Mirrors
    # the fallback path below: an auto-filled chip must never look applied
    # while silently doing nothing at the Apollo boundary (e.g. Gemini's
    # "Salesforce Data Cloud" — a real product, but not in Apollo's catalog).
    # User-TYPED chips stay free-form; this gates only the AI auto-fill.
    from nexus.services.apollo_technology_map import resolve_technology
    _emit("buyer_technologies", icp.get("buyer_technologies"),
          cleaner=lambda vs: [v[:60] for v in vs if resolve_technology(v)],
          cap=10)

    # revenue_range — single value. Gated like the rest; default "Any" (= no
    # constraint) when low-confidence or not a recognised band.
    rev_values, rev_conf, rev_evidence = _triple(icp.get("revenue_range"))
    rev_label = rev_values[0] if rev_values else None
    rev_passed = (
        _passes_confidence(rev_conf, min_confidence) and rev_label in REVENUE_OPTIONS
    )
    fields["revenue_range"] = rev_label if rev_passed else "Any"
    meta["revenue_range"] = {
        "confidence": rev_conf,
        "evidence": rev_evidence,
        "suppressed": bool(rev_label) and not rev_passed,
    }

    return fields, meta


@router.post("/suggest-targeting")
def suggest_targeting(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Return Gemini-suggested targeting filters validated against allowed lists.

    Response schema (2026-05-28 — see module docstring for rationale):
      {
        "person_titles":           [...],
        "person_locations":        [...],
        "organization_industries": [...],
        "revenue_range":           "...",
        "buyer_technologies":      [...]
      }
    """
    product_summary = str(body.get("product_summary") or "").strip()
    product_name = str(body.get("product_name") or "").strip()
    entity_type = str(body.get("entity_type") or "product").strip().lower()
    # 2026-05-28 — accept "gcc" as 3rd valid entity_type. See
    # `_ANALYZE_SYSTEM` in gemini.py for the type taxonomy.
    if entity_type not in ("product", "service", "gcc"):
        entity_type = "product"

    if not product_summary:
        raise HTTPException(status_code=400, detail="product_summary is required")

    url = str(body.get("url") or "").strip() or None
    icp_in = body.get("icp")

    def _has_filter_keys(d: Any) -> bool:
        return isinstance(d, dict) and any(
            isinstance(d.get(k), (dict, list)) for k in (
                "person_titles", "person_locations", "organization_industries",
                "buyer_technologies", "revenue_range",
            )
        )

    # ── Single path: gate the page-analysis ICP the wizard sent ────────────
    # The Google-Search-grounded research branch (and the legacy summary-only
    # re-derivation below) were removed 2026-06-02. The user's requirement is
    # that every ICP value MUST trace back to a verbatim quote in the scraped
    # pages — no Crunchbase / LinkedIn / news enrichment. analyze_product()
    # over the multi-page scrape is the only source of truth.
    if _has_filter_keys(icp_in):
        fields, meta = _autofill_from_icp(icp_in)
        return {**fields, "_meta": meta, "_source": "page"}

    # ── Last-resort fallback: legacy summary-only re-derivation ────────────
    # Kept ONLY for callers that don't forward the scrape-preview ICP triple
    # (older wizard sessions / cache misses). Has no per-field confidence so
    # every value is flagged confidence=0/suppressed=false; the UI shows the
    # values but with no badge. New code paths should always send `icp_in`.
    parsed: Dict[str, Any] = {}
    try:
        raw = gemini.chat_completion(
            system=_SYSTEM,
            user=_build_user_prompt(product_name, product_summary, entity_type),
            temperature=0.2,
            max_tokens=2000,
            response_format_json=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("suggest-targeting Gemini call failed: %s", exc)
        # Fall through with empty parsed — caller can pick filters manually.
        raw = ""

    if raw:
        try:
            extracted = gemini.extract_json(raw)
            if isinstance(extracted, dict):
                parsed = extracted
        except Exception as exc:  # noqa: BLE001
            # JSON parse fail is non-fatal — return empty arrays instead of
            # blowing up the wizard. User can still fill the fields manually.
            logger.warning(
                "suggest-targeting: could not parse Gemini output (%s). "
                "Returning empty defaults so the wizard can continue.",
                exc,
            )

    # Each filter validates against its allowed list. Unknown values are
    # silently dropped (never returned to the wizard) so the React chip
    # rows always receive known-good options.
    person_titles = [
        v for v in _extract_values(parsed.get("person_titles"))
        if isinstance(v, str) and v in ALLOWED_ROLES
    ][:5]

    person_locations = [
        v for v in _extract_values(parsed.get("person_locations"))
        if isinstance(v, str) and v in ALLOWED_LOCATIONS
    ][:6]

    # person_seniorities removed 2026-06-02, person_departments removed
    # 2026-06-08 — both over-narrowed Apollo; titles already imply them.

    organization_industries = [
        v for v in _extract_values(parsed.get("organization_industries"))
        if isinstance(v, str) and v in ALLOWED_INDUSTRIES
    ][:6]

    # The LLM emits technologies FREE-FORM, but Apollo only filters on techs in
    # its own catalog. Keep a suggestion ONLY if it resolves to a real Apollo
    # technology (resolve_technology recognises casual spellings like "AWS" ->
    # "Amazon Web Services (AWS)"); drop anything not in the catalog so the UI
    # never shows a chip that would silently do nothing at discovery time.
    from nexus.services.apollo_technology_map import resolve_technology

    buyer_technologies_raw = _extract_values(parsed.get("buyer_technologies"))
    buyer_technologies = [
        t
        for t in (
            str(v).strip()[:60]
            for v in buyer_technologies_raw
            if isinstance(v, str) and v.strip()
        )
        if resolve_technology(t) is not None
    ][:10]

    # revenue_range — Gemini may emit either a plain string or a single-
    # element list (per the canonical-triple shape). Accept both.
    revenue_raw = parsed.get("revenue_range")
    if isinstance(revenue_raw, dict):
        vals = revenue_raw.get("values") or []
        revenue_raw = vals[0] if vals else None
    elif isinstance(revenue_raw, list):
        revenue_raw = revenue_raw[0] if revenue_raw else None
    revenue_range = revenue_raw if revenue_raw in REVENUE_OPTIONS else "Any"

    # Fallback has no per-field confidence signal — mark every field
    # confidence=0/suppressed=false so the UI knows these aren't gated
    # (vs the page-ICP path which carries real 0-100 confidences).
    _fallback_meta = {
        k: {"confidence": 0, "evidence": "", "suppressed": False}
        for k in (
            "person_titles", "person_locations",
            "organization_industries",
            "revenue_range", "buyer_technologies",
        )
    }
    return {
        "person_titles":           person_titles,
        "person_locations":        person_locations,
        "organization_industries": organization_industries,
        "revenue_range":           revenue_range,
        "buyer_technologies":      buyer_technologies,
        "_meta":                   _fallback_meta,
    }
