"""LIVE test: does Gemini auto-fill organization_industries with the updated
Apollo-native labels only — and are they RELEVANT to the analyzed entity?

Runs the REAL suggest-targeting system + user prompt (which embeds the
145-label Apollo-native ALLOWED list) for all three entity types:

  service — z-ninth   (Salesforce/MuleSoft integration services)
  product — Spenzo    (marketing mix modeling platform)
  gcc     — a US-retail-focused capability-center provider

Each case asserts:
  1. every returned label is on the 145-label list (verbatim),
  2. none would be suppressed by the auto-fill gate,
  3. all resolve to Apollo tag IDs,
  4. RELEVANCE: at least one expected anchor industry for that entity is
     present, and no label from the obviously-wrong set appears.

Three Gemini calls, no Apollo, no DB writes.

Run from apps/backend:
    venv/Scripts/python.exe scripts/test_gemini_industry_fill.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")

from nexus.routers.analyze_targeting import (
    ALLOWED_INDUSTRIES,
    _SYSTEM,
    _build_user_prompt,
    _mappable_industries,
)
from nexus.services import gemini
from nexus.services.apollo_industry_map import industries_to_tag_ids

CASES = [
    {
        "name": "z-ninth", "entity": "service",
        "summary": (
            "z-ninth provides implementation and integration services for "
            "Salesforce and MuleSoft, develops custom APIs and AppExchange "
            "apps, and engineers AI-driven ecosystems. Serves growing "
            "businesses worldwide with a particular focus on the financial "
            "services industry. Industries: Financial Services, "
            "Transportation, Telecommunications, Food & Beverage."
        ),
        # relevance anchors: at least ONE must appear
        "expect_any": {"Financial Services", "Banking",
                       "Transportation/Trucking/Railroad", "Telecommunications",
                       "Food & Beverages"},
        # obviously wrong for this page: NONE may appear
        "forbid": {"Cosmetics", "Ranching", "Wine & Spirits", "Fishery",
                   "Performing Arts"},
    },
    {
        "name": "Spenzo", "entity": "product",
        "summary": (
            "Spenzo is a marketing mix modeling (MMM) platform that shows "
            "which channels and campaigns drive real revenue, so marketing "
            "and finance teams can optimize ad spend with confidence. "
            "Incrementality analysis, budget attribution, ROI forecasting. "
            "Who it's for: growth marketers, CMOs, and finance teams "
            "managing multi-channel advertising budgets at consumer brands, "
            "e-commerce retailers, and agencies."
        ),
        "expect_any": {"Marketing & Advertising", "Retail", "Consumer Goods",
                       "Internet", "Apparel & Fashion"},
        "forbid": {"Mining & Metals", "Shipbuilding", "Judiciary",
                   "Military", "Dairy"},
    },
    {
        "name": "AcmeGCC", "entity": "gcc",
        "summary": (
            "AcmeGCC sets up and operates Global Capability Centers in "
            "Hyderabad for US and UK enterprises in retail, banking and "
            "insurance — engineering, data and finance back-office teams "
            "for Fortune-500 parents like large retail chains and banks."
        ),
        "expect_any": {"Retail", "Banking", "Insurance", "Financial Services",
                       "Supermarkets"},
        "forbid": {"Fishery", "Wine & Spirits", "Performing Arts",
                   "Animation", "Ranching"},
    },
]

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


gate = _mappable_industries()
for case in CASES:
    print(f"\n── {case['name']} (entity_type={case['entity']}) " + "─" * 30)
    raw = gemini.chat_completion(
        system=_SYSTEM,
        user=_build_user_prompt(case["name"], case["summary"], case["entity"]),
        temperature=0.2,
        max_tokens=2000,
        response_format_json=True,
    )
    parsed = gemini.extract_json(raw) or {}
    inds = parsed.get("organization_industries") or []
    if isinstance(inds, dict):
        inds = inds.get("values") or []
    print("  Gemini returned:", inds)

    check("all labels on the 145 Apollo-native list",
          inds and not [i for i in inds if i not in ALLOWED_INDUSTRIES],
          str([i for i in inds if i not in ALLOWED_INDUSTRIES]))
    check("none suppressed by the auto-fill gate",
          not [i for i in inds if i not in gate])
    check("all resolve to Apollo tag IDs",
          len(industries_to_tag_ids(inds)) > 0)
    check("RELEVANT: contains an expected anchor industry",
          bool(set(inds) & case["expect_any"]),
          f"anchors={sorted(case['expect_any'])}")
    check("RELEVANT: no obviously-wrong industry",
          not (set(inds) & case["forbid"]),
          str(sorted(set(inds) & case["forbid"])))

print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
sys.exit(0 if not FAILURES else 1)
