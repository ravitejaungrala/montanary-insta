"""Offline tests for the 2026-06-11 targeting-vocabulary fixes.

Covers (no network, no DB, no credits):
  1. ALLOWED_INDUSTRIES (Gemini prompt list) == Apollo's full native taxonomy.
  2. Frontend INDUSTRIES dropdown == the same 145 labels.
  3. Every prompt label resolves to a verified Apollo tag ID; legacy curated
     aliases (SaaS, FinTech, ...) still resolve for ICPs stored by old runs.
  4. The auto-fill gate (_mappable_industries) admits every prompt label —
     a Gemini-emitted native label must never be silently suppressed.
  5. Technology auto-fill keeps only labels that resolve to a real Apollo UID
     (the "Salesforce Data Cloud" dead-chip fix) while keeping casual
     spellings like "AWS".
  6. Every frontend technology suggestion (apolloTechnologies.json) resolves.

Run from apps/backend:
    venv/Scripts/python.exe scripts/test_targeting_autofill.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")

from nexus.routers.analyze_targeting import (  # noqa: E402
    ALLOWED_INDUSTRIES,
    _autofill_from_icp,
    _mappable_industries,
)
from nexus.services.apollo_industry_map import (  # noqa: E402
    APOLLO_INDUSTRY_MAP,
    APOLLO_NATIVE_INDUSTRY_TAGS,
    industries_to_tag_ids,
)
from nexus.services.apollo_technology_map import resolve_technology  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\n1) prompt list == Apollo native taxonomy")
    check("145 labels in the Gemini prompt list", len(ALLOWED_INDUSTRIES) == 145,
          f"got {len(ALLOWED_INDUSTRIES)}")
    check("prompt list matches Apollo taxonomy exactly",
          sorted(ALLOWED_INDUSTRIES) == sorted(APOLLO_NATIVE_INDUSTRY_TAGS))

    print("\n2) frontend dropdown == the same labels")
    js = io.open(
        os.path.join(base, "..", "product-page", "src", "pages", "nexus", "targetingData.js"),
        encoding="utf-8",
    ).read()
    body = re.search(r"export const INDUSTRIES = \[(.*?)\];", js, re.DOTALL).group(1)
    body = re.sub(r"//.*", "", body)
    fe = [s.replace("\\'", "'") for s in re.findall(r"'((?:[^'\\]|\\.)*)'", body)]
    check("dropdown has the same 145 labels",
          sorted(fe) == sorted(APOLLO_NATIVE_INDUSTRY_TAGS),
          f"dropdown={len(fe)}")

    print("\n3) every label resolves at the Apollo boundary")
    unmapped = [l for l in ALLOWED_INDUSTRIES if not industries_to_tag_ids([l])]
    check("no unmapped prompt labels", not unmapped, str(unmapped[:5]))
    legacy_bad = [l for l in APOLLO_INDUSTRY_MAP if not industries_to_tag_ids([l])]
    check("legacy curated aliases still resolve (old stored ICPs)",
          not legacy_bad, str(legacy_bad[:5]))

    print("\n4) auto-fill gate admits every prompt label")
    gate = _mappable_industries()
    suppressed = [l for l in ALLOWED_INDUSTRIES if l not in gate]
    check("no native label suppressed by the gate", not suppressed,
          str(suppressed[:5]))
    fields, _ = _autofill_from_icp({
        "organization_industries": {
            "values": ["Investment Banking", "Medical Practice", "Not A Real Industry"],
            "confidence": 90, "evidence": "page",
        },
    })
    check("native labels auto-fill; junk label dropped",
          fields["organization_industries"] == ["Investment Banking", "Medical Practice"],
          str(fields["organization_industries"]))

    print("\n5) technology auto-fill keeps only Apollo-resolvable chips")
    fields, _ = _autofill_from_icp({
        "buyer_technologies": {
            "values": ["Salesforce", "Mulesoft", "Salesforce Data Cloud",
                       "AWS", "Zzyzx Quantum Suite"],
            "confidence": 90, "evidence": "page",
        },
    })
    # 'Salesforce Data Cloud' now RESOLVES via the parent-product fallback
    # (-> salesforce), so it stays a WORKING chip; pure junk is still dropped.
    check("resolvable chips kept (incl. parent-product recovery), junk dropped",
          fields["buyer_technologies"] == ["Salesforce", "Mulesoft",
                                           "Salesforce Data Cloud", "AWS"],
          str(fields["buyer_technologies"]))
    from nexus.services.apollo_technology_map import technologies_to_uids
    uids = technologies_to_uids(fields["buyer_technologies"])
    check("Apollo boundary dedupes the parent overlap",
          uids == ["salesforce", "mulesoft", "amazon_web_services_aws"],
          str(uids))

    print("\n7) resolver near-miss recovery (2026-06-11 layers)")
    check("parent-product: 'Salesforce Data Cloud' -> salesforce",
          resolve_technology("Salesforce Data Cloud") == "salesforce")
    check("parent-product: 'Microsoft Teams Premium' -> microsoft_teams",
          resolve_technology("Microsoft Teams Premium") == "microsoft_teams")
    check("fuzzy typo: 'Salesforcee' -> salesforce",
          resolve_technology("Salesforcee") == "salesforce")
    check("safety: junk with real-looking suffix stays unresolved",
          resolve_technology("Zzyzx Data Cloud") is None)
    check("safety: 'Java' does NOT fuzzy-jump to 'JavaScript'",
          resolve_technology("Java") != resolve_technology("JavaScript"))

    print("\n6) every frontend technology suggestion resolves")
    techs = json.load(io.open(
        os.path.join(base, "..", "product-page", "src", "pages", "nexus", "apolloTechnologies.json"),
        encoding="utf-8",
    ))
    bad = [t for t in techs if not resolve_technology(t)]
    check(f"all {len(techs)} suggestions resolve to an Apollo UID", not bad,
          f"unresolved={len(bad)}")

    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
