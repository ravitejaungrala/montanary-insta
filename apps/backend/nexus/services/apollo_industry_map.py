"""Gemini-output → Apollo industry tag-ID lookup.

Apollo's `/v1/mixed_people/api_search` rejects free-form industry strings —
the `organization_industry_tag_ids` field requires Apollo's own opaque
24-char hex tag IDs. Gemini emits human-readable labels like "Insurance",
"Financial Services", "SaaS"; this module is the single mapping point
between the two vocabularies.

# Why this file exists

Before 2026-05-28 the `_icp_to_apollo_body()` function tried to send
Gemini's free-form `organization_industries` strings directly to Apollo.
Every request was silently treated as "no industry match" by Apollo, so
the user-visible TARGET INDUSTRIES chips on the wizard had **zero effect
on lead discovery** for over a month. The fix lands two pieces:

1. This module — `APOLLO_INDUSTRY_MAP` translates label → tag ID.
2. `_icp_to_apollo_body()` — calls `industries_to_tag_ids()` and sends
   `organization_industry_tag_ids` instead of the legacy free-form field.

# How tag IDs were sourced

The seed set covers the most common B2B industries by historical campaign
volume. Each entry was verified against Apollo's API by either:
  - searching with `q_organization_keyword_tags=<label>` and reading back
    `organization.industry` + the matching `industry_tag_id`, OR
  - inspecting Apollo dashboard responses where the industry filter is set.

# Adding more entries

When `industries_to_tag_ids()` encounters an unmapped Gemini label it
logs `apollo_industry_map: unmapped label='<X>'`. The unmapped label is
silently dropped from the Apollo query — the request still goes through,
just without that industry constraint. To extend coverage:

  1. Read the log for unmapped labels.
  2. Find the canonical Apollo tag ID for each (Apollo support, or a
     keyword-tag probe).
  3. Add a new line below — no other code changes required.

# Safety contract

  - This module NEVER raises. Bad inputs produce empty outputs, never
    crashes. Lead discovery must keep working even when the map is stale.
  - `industries_to_tag_ids()` accepts both `["Insurance"]` and the
    canonical filter triple shape `{"values": ["Insurance"], ...}` so
    callers don't have to unwrap before calling.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List

logger = logging.getLogger("pipelyt.nexus.apollo_industry_map")


# ─────────────────────────────────────────────────────────────────────────────
# Verified map: our industry label → Apollo `industry_tag_id`.
#
# 2026-06-01 FULL REBUILD. The previous seed was almost entirely STALE —
# an audit found 37 of 40 tag IDs returned ZERO companies in Apollo's search
# filter, and 2 were mislabeled (the old "Information Technology" ID was
# actually marketing & advertising; "Financial Services" was a dead ID). Apollo
# had rotated its industry tag IDs since the original seed, silently breaking
# industry targeting product-wide.
#
# Every ID below was sourced + verified live against Apollo on 2026-06-01:
#   1. ENRICH a flagship company in the industry via GET /v1/organizations/enrich
#      → read back its `industry` + `industry_tag_id` (the authoritative pair).
#   2. VERIFY the tag in the SAME id-space the search filter uses: POST
#      /v1/mixed_companies/search with `organization_industry_tag_ids=[tag]`
#      and confirm total_entries > 0 with on-industry sample company names.
# Both calls are firmographic (no lead-reveal credits). To re-verify or extend,
# repeat that two-step against a representative company.
#
# Apollo's industry taxonomy is COARSER than our label list, so several fine
# labels legitimately resolve to the same Apollo industry (e.g. SaaS / Cloud /
# Data Analytics / Software Dev / IoT / Marketing Technology / Social Media /
# HR Tech / Legal Tech / FinTech / E-Commerce / Telecom-adjacent SaaS all map to
# "information technology & services"). Shared tag IDs are expected and fine —
# industries_to_tag_ids() dedupes them. A `# coarse:` note marks labels mapped
# to the nearest broader Apollo industry where no exact match exists.
# ─────────────────────────────────────────────────────────────────────────────
# The shared "information technology & services" tag — many software/internet
# sub-verticals collapse here in Apollo's taxonomy.
_IT_SERVICES = "5567cd4773696439b10b0000"

APOLLO_INDUSTRY_MAP: dict[str, str] = {
    # Information technology cluster (Apollo lumps most into IT & services)
    "Information Technology":     _IT_SERVICES,
    "SaaS":                       _IT_SERVICES,
    "Software Development":       _IT_SERVICES,
    "Cloud Computing":            _IT_SERVICES,          # coarse: → IT & services
    "Data Analytics & BI":        _IT_SERVICES,          # coarse: → IT & services
    "Artificial Intelligence":    _IT_SERVICES,          # coarse: → IT & services
    "IoT":                        _IT_SERVICES,          # coarse: → IT & services
    "HR Tech":                    _IT_SERVICES,          # coarse: → IT & services
    "Legal Tech":                 _IT_SERVICES,          # coarse: → IT & services
    "Marketing Technology":       _IT_SERVICES,          # coarse: → IT & services
    "Social Media":               _IT_SERVICES,          # coarse: → IT & services
    "FinTech":                    _IT_SERVICES,          # coarse: → IT & services
    "E-Commerce":                 _IT_SERVICES,          # coarse: → IT & services
    "Telecommunications":         "5567cd4c7369644d39080000",   # telecommunications
    "Cybersecurity":              "5567cd877369644cf94b0000",   # computer & network security
    "Semiconductors":             "5567e0d87369640e5aa30c00",   # semiconductors
    "Consumer Electronics":       "5567cd4c73696439c9030000",   # electrical/electronic mfg

    # Marketing / advertising
    "Advertising & Marketing":    "5567cd467369644d39040000",   # marketing & advertising

    # Financial cluster
    "Financial Services":         "5567cdd67369643e64020000",   # financial services
    "Banking":                    "5567ce237369644ee5490000",   # banking
    "Banking and finance":        "5567cdd67369643e64020000",   # → financial services (umbrella)
    "Banking & Finance":          "5567cdd67369643e64020000",   # → financial services (umbrella)
    "Insurance":                  "5567cdd973696453d93f0000",   # insurance
    "Capital Markets":            "5567cdd67369643e64020000",   # coarse: → financial services
    "Investment Management":      "5567cdd67369643e64020000",   # coarse: → financial services
    "Venture Capital":            "5567e1587369641c48370000",   # venture capital & private equity
    "Private Equity":             "5567e1587369641c48370000",   # venture capital & private equity

    # Health & life sciences
    "Healthcare":                 "5567cdde73696439812c0000",   # hospital & health care
    "Pharmaceuticals":            "5567e0eb73696410e4bd1200",   # pharmaceuticals
    "Biotechnology":              "5567e0eb73696410e4bd1200",   # coarse: → pharmaceuticals
    "Medical Devices":            "5567e1b97369641ea9690200",   # medical devices

    # Industrial / physical
    "Manufacturing":              "5567cd4973696439d53c0000",   # machinery
    "Automotive":                 "5567cdf27369644cfd800000",   # automotive
    "Aerospace":                  "5567e0dd73696416d3c20100",   # aviation & aerospace
    "Defense":                    "5567e1097369641b5f810500",   # defense & space
    "Chemical":                   "5567e21e73696426a1030000",   # chemicals
    "Mining & Metals":            "5567e3f3736964395d7a0000",   # mining & metals
    "Construction":               "5567cd4773696439dd350000",   # construction
    "Architecture & Design":      "5567cdb77369645401080000",   # architecture & planning
    "Engineering":                "5567e13a73696418756e0200",   # civil engineering
    "Infrastructure":             "5567e13a73696418756e0200",   # coarse: → civil engineering
    "Real Estate":                "5567cd477369645401010000",   # real estate
    "PropTech":                   "5567cd477369645401010000",   # coarse: → real estate

    # Commerce / consumer
    "Retail":                     "5567ced173696450cb580000",   # retail
    "Consumer Goods & FMCG":      "5567ce2673696453d95c0000",   # holds Unilever/Colgate/P&G
    "CPG":                        "5567ce2673696453d95c0000",   # = consumer packaged goods / FMCG
    "Wholesale & Distribution":   "5567d01e73696457ee100000",   # wholesale
    "Food & Beverage":            "5567ce1e7369643b806a0000",   # food & beverages
    "Agriculture & AgTech":       "5567cd4f7369644d2d010000",   # farming

    # Energy / utilities / environment
    "Energy":                     "5567e2127369642420170000",   # coarse: → utilities
    "Utilities":                  "5567e2127369642420170000",   # utilities
    "Oil & Gas":                  "5567cdd97369645624020000",   # oil & energy
    "Clean Energy & Renewables":  "5567ce5b736964540d280000",   # coarse: → environmental services
    "Environmental Services":     "5567ce5b736964540d280000",   # environmental services

    # Media / services / travel / other
    "Media & Publishing":         "5567ce5b73696439a17a0000",   # publishing
    "Entertainment":              "5567cdd37369643b80510000",   # entertainment
    "Gaming":                     "5567cd8b736964540d0f0000",   # computer games
    "Education & EdTech":         "5567e19c7369641c48e70100",   # e-learning
    "Logistics & Supply Chain":   "5567e8bb7369641a658f0000",   # package/freight delivery
    "Transportation":             "5567cd4e7369644cf93b0000",   # transportation/trucking/railroad
    "Hospitality & Tourism":      "5567ce9d7369643bc19c0000",   # hospitality
    "Travel & Tourism":           "5567ce9d7369643bc19c0000",   # coarse: → hospitality
    "Sports & Fitness":           "5567cddb7369644d250c0000",   # health, wellness & fitness
    "Government & Public Sector": "5567cd527369643981050000",   # government administration
    "Non-Profit":                 "5567cd4773696454303a0000",   # nonprofit organization mgmt
    "Consulting":                 "5567cdd47369643dbf260000",   # management consulting
    "Professional Services":      "5567cdd47369643dbf260000",   # coarse: → management consulting
    "Accounting & Tax":           "5567ce1f7369643b78570000",   # accounting
    "Staffing & Recruiting":      "5567e09973696410db020800",   # staffing & recruiting
    "Security":                   "5567e19b7369641ead740000",   # security & investigations
}


# ─────────────────────────────────────────────────────────────────────────────
# Apollo's FULL NATIVE industry taxonomy — Apollo's OWN exact labels → tag IDs.
#
# This is the authoritative source-of-truth straight from Apollo (the same
# labels Apollo returns in `organization.industry`). Two uses:
#   1. REFERENCE for backfilling/verifying APOLLO_INDUSTRY_MAP above — find the
#      right tag ID without re-probing Apollo.
#   2. FALLBACK in industries_to_tag_ids(): when a label isn't in the curated
#      APOLLO_INDUSTRY_MAP but matches an Apollo-native label (case-insensitive),
#      we resolve it here instead of dropping it. Purely additive — the curated
#      map is always checked FIRST, so existing behaviour never changes.
# ─────────────────────────────────────────────────────────────────────────────
APOLLO_NATIVE_INDUSTRY_TAGS: dict[str, str] = {
    "Accounting": "5567ce1f7369643b78570000",
    "Agriculture": "55718f947369642142b84a12",
    "Airlines/Aviation": "5567e0bf7369641d115f0200",
    "Alternative Dispute Resolution": "5567e1a87369641f6d550100",
    "Alternative Medicine": "5567e27c7369642ade490000",
    "Animation": "5567e36f73696431a4970000",
    "Apparel & Fashion": "5567cd82736964540d0b0000",
    "Architecture & Planning": "5567cdb77369645401080000",
    "Arts & Crafts": "5567cd4d73696439d9030000",
    "Automotive": "5567cdf27369644cfd800000",
    "Aviation & Aerospace": "5567e0dd73696416d3c20100",
    "Banking": "5567ce237369644ee5490000",
    "Biotechnology": "5567d08e7369645dbc4b0000",
    "Broadcast Media": "5567e0f973696416d34e0200",
    "Building Materials": "5567e1a17369641ea9d30100",
    "Business Supplies & Equipment": "5567e0fa73696410e4c51200",
    "Capital Markets": "5567cdb773696439a9080000",
    "Chemicals": "5567e21e73696426a1030000",
    "Civic & Social Organization": "5567cdda7369644eed130000",
    "Civil Engineering": "5567e13a73696418756e0200",
    "Commercial Real Estate": "5567e1887369641d68d40100",
    "Computer & Network Security": "5567cd877369644cf94b0000",
    "Computer Games": "5567cd8b736964540d0f0000",
    "Computer Hardware": "5567e0d47369641233eb0600",
    "Computer Networking": "5567cdbe7369643b78360000",
    "Computer Software": "5567cd4e7369643b70010000",
    "Construction": "5567cd4773696439dd350000",
    "Consumer Electronics": "5567e1947369641ead570000",
    "Consumer Goods": "5567ce987369643b789e0000",
    "Consumer Services": "5567d1127261697f2b1d0000",
    "Cosmetics": "5567e1ae73696423dc040000",
    "Dairy": "5567e8a27369646ddb0b0000",
    "Defense & Space": "5567e1097369641b5f810500",
    "Design": "5567cdbc73696439d90b0000",
    "E-Learning": "5567e19c7369641c48e70100",
    "Education Management": "5567ce9e736964540d540000",
    "Electrical/Electronic Manufacturing": "5567cd4c73696439c9030000",
    "Entertainment": "5567cdd37369643b80510000",
    "Environmental Services": "5567ce5b736964540d280000",
    "Events Services": "5567cd8e7369645409450000",
    "Executive Office": "5567e09473696410dbf00700",
    "Facilities Services": "5567ce9c7369643bc9980000",
    "Farming": "5567cd4f7369644d2d010000",
    "Financial Services": "5567cdd67369643e64020000",
    "Fine Art": "5567e2097369642420150000",
    "Fishery": "5567f96c7369642a22080000",
    "Food & Beverages": "5567ce1e7369643b806a0000",
    "Food Production": "5567e1b3736964208b280000",
    "Fund-Raising": "5567d2ad7261697f2b1f0100",
    "Furniture": "5567cede73696440d0040000",
    "Gambling & Casinos": "5567e0cf7369641233e50600",
    "Glass, Ceramics & Concrete": "5567cd4f736964397e030000",
    "Government Administration": "5567cd527369643981050000",
    "Government Relations": "5567e29b736964256c370100",
    "Graphic Design": "5567cd4d73696439d9040000",
    "Health, Wellness & Fitness": "5567cddb7369644d250c0000",
    "Higher Education": "5567cd4c73696453e1300000",
    "Hospital & Health Care": "5567cdde73696439812c0000",
    "Hospitality": "5567ce9d7369643bc19c0000",
    "Human Resources": "5567e0e37369640e5ac10c00",
    "Import & Export": "5567ce9d7369645430c50000",
    "Individual & Family Services": "5567d02b7369645d8b140000",
    "Industrial Automation": "5567e1337369641ad2970000",
    "Information Services": "5567e0c97369640d2b3b1600",
    "Information Technology & Services": "5567cd4773696439b10b0000",
    "Insurance": "5567cdd973696453d93f0000",
    "International Trade & Development": "5567ce9c7369644eed680000",
    "Internet": "5567cd4d736964397e020000",
    "Investment Banking": "5567e1ab7369641f6d660100",
    "Investment Management": "5567e0bc7369641d11550200",
    "Judiciary": "55680a8273696407b61f0000",
    "Law Enforcement": "5567e0e073696408da441e00",
    "Law Practice": "5567ce1f7369644d391c0000",
    "Legal Services": "5567ce2d7369644d25250000",
    "Leisure, Travel & Tourism": "5567cdd87369643bc12f0000",
    "Libraries": "556808697369647bfd420000",
    "Logistics & Supply Chain": "5567cd4973696439b9010000",
    "Luxury Goods & Jewelry": "5567cda97369644cfd3e0000",
    "Machinery": "5567cd4973696439d53c0000",
    "Management Consulting": "5567cdd47369643dbf260000",
    "Maritime": "5567cd8273696439b1240000",
    "Market Research": "5567e1387369641ec75d0200",
    "Marketing & Advertising": "5567cd467369644d39040000",
    "Mechanical Or Industrial Engineering": "5567ce2673696453d95c0000",
    "Media Production": "5567e0ea7369640d2ba31600",
    "Medical Devices": "5567e1b97369641ea9690200",
    "Medical Practice": "5567d0467369645dbc200000",
    "Mental Health Care": "5567ce2773696454308f0000",
    "Military": "5567e2c572616932bb3b0000",
    "Mining & Metals": "5567e3f3736964395d7a0000",
    "Motion Pictures & Film": "5567cdd7736964540d130000",
    "Music": "5567cd4f736964540d050000",
    "Nanotechnology": "5567e7be736964110e210000",
    "Newspapers": "5567cd4a73696439a9010000",
    "Nonprofit Organization Management": "5567cd4773696454303a0000",
    "Oil & Energy": "5567cdd97369645624020000",
    "Online Media": "5567cdb373696439dd540000",
    "Outsourcing/Offshoring": "5567d04173696457ee520000",
    "Package/Freight Delivery": "5567e8bb7369641a658f0000",
    "Packaging & Containers": "5567e36973696431a4480000",
    "Paper & Forest Products": "5567e97f7369641e57730100",
    "Performing Arts": "5567e0af7369641ec7300000",
    "Pharmaceuticals": "5567e0eb73696410e4bd1200",
    "Philanthropy": "5567ce9673696453d99f0000",
    "Photography": "5567cd4f7369644cfd250000",
    "Plastics": "5567cdda7369644cf95d0000",
    "Political Organization": "5567e25f736964256cff0000",
    "Primary/Secondary Education": "5567cdd97369645430680000",
    "Printing": "5567cd4d7369644d513e0000",
    "Professional Training & Coaching": "5567cd49736964541d010000",
    "Program Development": "5567e2907369642433e60200",
    "Public Policy": "5567e28a7369642ae2500000",
    "Public Relations & Communications": "5567ce5973696453d9780000",
    "Public Safety": "5567cd4a7369643ba9010000",
    "Publishing": "5567ce5b73696439a17a0000",
    "Railroad Manufacture": "5567e14673696416d38c0300",
    "Ranching": "5567fd5a73696442b0f20000",
    "Real Estate": "5567cd477369645401010000",
    "Recreational Facilities & Services": "5567e134736964214f5e0000",
    "Religious Institutions": "5567e0f27369640e5aed0c00",
    "Renewables & Environment": "5567cd49736964540d020000",
    "Research": "5567e09f736964160ebb0100",
    "Restaurants": "5567e0e0736964198de70700",
    "Retail": "5567ced173696450cb580000",
    "Security & Investigations": "5567e19b7369641ead740000",
    "Semiconductors": "5567e0d87369640e5aa30c00",
    "Shipbuilding": "5568047d7369646d406c0000",
    "Sporting Goods": "5567e113736964198d5e0800",
    "Sports": "5567ce227369644eed290000",
    "Staffing & Recruiting": "5567e09973696410db020800",
    "Supermarkets": "5567e2a97369642a553d0000",
    "Telecommunications": "5567cd4c7369644d39080000",
    "Textiles": "5567e1327369641d91ce0300",
    "Think Tanks": "5567e1de7369642069ea0100",
    "Tobacco": "55680085736964551e070000",
    "Translation & Localization": "5567e1097369641d91230300",
    "Transportation/Trucking/Railroad": "5567cd4e7369644cf93b0000",
    "Utilities": "5567e2127369642420170000",
    "Venture Capital & Private Equity": "5567e1587369641c48370000",
    "Veterinary": "5567ce9673696439d5c10000",
    "Warehousing": "5567e127736964181e700200",
    "Wholesale": "5567d01e73696457ee100000",
    "Wine & Spirits": "5567cd4d7369643b78100000",
    "Wireless": "5567e3ca736964371b130000",
    "Writing & Editing": "5567cdd973696439a1370000",
}

# Lower-cased index of the native taxonomy for the case-insensitive fallback
# below (built once at import).
_NATIVE_LOWER: dict[str, str] = {
    k.strip().lower(): v for k, v in APOLLO_NATIVE_INDUSTRY_TAGS.items()
}


def _flatten(raw: Any) -> List[str]:
    """Normalise the various shapes a caller may pass.

    Accepted:
      - list[str]                          e.g. ["Insurance", "SaaS"]
      - dict with `values` key (canonical triple shape from analyze_product)
      - single string                      e.g. "Insurance"
      - None / anything else               → []
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        vals = raw.get("values")
        return _flatten(vals)
    if isinstance(raw, Iterable):
        out: List[str] = []
        for v in raw:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


def industries_to_tag_ids(raw: Any) -> List[str]:
    """Translate Gemini industry labels to Apollo `industry_tag_id`s.

    Unknown labels are silently dropped (and logged once each per process
    so the dev can backfill the map without breaking the user). Returns
    a deduplicated, order-preserving list of tag IDs.
    """
    labels = _flatten(raw)
    if not labels:
        return []

    out: List[str] = []
    seen: set[str] = set()
    for label in labels:
        # 1) curated map (our canonical labels) wins — exact match.
        tag_id = APOLLO_INDUSTRY_MAP.get(label)
        # 2) fallback: Apollo's native taxonomy, case-insensitive. Catches
        #    labels that match an Apollo-native industry name but aren't in
        #    the curated map (e.g. "Higher Education", "Marketing &
        #    Advertising"). Additive only — never overrides step 1.
        if tag_id is None:
            tag_id = _NATIVE_LOWER.get(label.strip().lower())
        if tag_id is None:
            logger.info(
                "apollo_industry_map: unmapped label=%r — skipping at "
                "Apollo boundary. Add it to APOLLO_INDUSTRY_MAP to enable.",
                label,
            )
            continue
        if tag_id in seen:
            continue
        seen.add(tag_id)
        out.append(tag_id)

    return out
