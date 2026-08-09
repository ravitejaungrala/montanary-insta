"""LIVE sweep: Gemini targeting auto-fill for EVERY distinct product/service/gcc
in the workspace, using each entity's real stored description from the DB.

Per entity (one Gemini call each, run in parallel):
  - industries: non-empty, all on the 145 Apollo-native list, none suppressed
    by the auto-fill gate, all resolve to tag IDs, >=1 relevance anchor hit,
    zero absurd labels;
  - technologies: every auto-fill-surviving tech resolves to an Apollo UID
    (post-2026-06-11 gate), and the resolved set is reported for eyeballing.

Reads product rows from the configured DATABASE_URL (read-only). ~8 Gemini
calls, no Apollo credits, no DB writes.

Run from apps/backend:
    venv/Scripts/python.exe -X utf8 scripts/test_all_products_live.py
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.routers.analyze_targeting import (  # noqa: E402
    ALLOWED_INDUSTRIES,
    _SYSTEM,
    _build_user_prompt,
    _mappable_industries,
)
from nexus.services import gemini  # noqa: E402
from nexus.services.apollo_industry_map import industries_to_tag_ids  # noqa: E402
from nexus.services.apollo_technology_map import resolve_technology  # noqa: E402

# Relevance anchors per entity — Gemini must surface AT LEAST ONE of these.
# Forbid set is shared: industries absurd for every entity under test.
ANCHORS = {
    "spenzo.ai":     {"Marketing & Advertising", "Retail", "Consumer Goods",
                      "Internet", "Apparel & Fashion", "Supermarkets",
                      "Computer Software"},
    "pipelyt.ai":    {"Marketing & Advertising", "Computer Software", "Internet",
                      "Information Technology & Services", "Staffing & Recruiting"},
    "zninth.com":    {"Financial Services", "Banking", "Insurance",
                      "Telecommunications", "Transportation/Trucking/Railroad",
                      "Food & Beverages", "Information Technology & Services"},
    "zyntegrate.com": {"Information Technology & Services", "Computer Software",
                       "Financial Services", "Banking", "Manufacturing",
                       "Logistics & Supply Chain", "Retail",
                       "Hospital & Health Care"},
    "aarohanconsulting.com": {"Banking", "Financial Services", "Insurance",
                              "Retail", "Hospital & Health Care",
                              "Information Technology & Services",
                              "Computer Software", "Pharmaceuticals"},
    "neuzenai.com":  {"Retail", "Financial Services", "Utilities", "Banking",
                      "Hospital & Health Care", "Insurance", "Oil & Energy",
                      "Internet"},
    "hartflex.com":  {"Health, Wellness & Fitness", "Medical Practice",
                      "Hospital & Health Care", "Cosmetics",
                      "Alternative Medicine", "Consumer Services"},
    "nadec.com":     {"Retail", "Supermarkets", "Food & Beverages",
                      "Restaurants", "Hospitality", "Consumer Goods",
                      "Wholesale", "Farming", "Dairy", "Food Production"},
}
FORBID = {"Judiciary", "Military", "Shipbuilding", "Performing Arts",
          "Libraries", "Animation", "Fine Art"}
# Nadec IS food/agri — nothing in FORBID collides with any entity's domain.

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load_entities():
    """Latest product row per distinct source domain (read-only)."""
    from core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            """SELECT DISTINCT ON (regexp_replace(lower(source_url),
                          '^https?://(www\\.)?|/+$', '', 'g'))
                      name, source_url, value_proposition, category,
                      COALESCE(icp->>'entity_type','product') AS etype
                 FROM nexus_products
                WHERE COALESCE(value_proposition,'') <> ''
                ORDER BY regexp_replace(lower(source_url),
                          '^https?://(www\\.)?|/+$', '', 'g'), id DESC"""
        )).mappings().fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def anchor_key(url: str):
    for key in ANCHORS:
        if key in (url or "").lower():
            return key
    return None


def run_one(ent):
    summary = f"{ent['name']} — {ent['category'] or ''}. {ent['value_proposition'] or ''}"
    raw = gemini.chat_completion(
        system=_SYSTEM,
        user=_build_user_prompt(ent["name"], summary, ent["etype"]),
        temperature=0.2,
        max_tokens=2000,
        response_format_json=True,
    )
    parsed = gemini.extract_json(raw) or {}
    inds = parsed.get("organization_industries") or []
    if isinstance(inds, dict):
        inds = inds.get("values") or []
    techs = parsed.get("buyer_technologies") or []
    if isinstance(techs, dict):
        techs = techs.get("values") or []
    return ent, inds, techs


def main() -> int:
    entities = load_entities()
    print(f"Testing {len(entities)} distinct entit(ies) live…")
    gate = _mappable_industries()

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(run_one, entities))

    for ent, inds, techs in results:
        key = anchor_key(ent["source_url"])
        print(f"\n── {ent['name']} ({ent['etype']}, {ent['source_url']}) " + "─" * 20)
        print("  industries  :", inds)
        resolved = [(t, resolve_technology(t)) for t in techs]
        print("  technologies:", [f"{t}→{u}" for t, u in resolved])

        nm = ent["name"]
        check(f"[{nm}] industries non-empty + on the 145 list",
              bool(inds) and not [i for i in inds if i not in ALLOWED_INDUSTRIES],
              str([i for i in inds if i not in ALLOWED_INDUSTRIES]))
        check(f"[{nm}] none gate-suppressed",
              not [i for i in inds if i not in gate])
        check(f"[{nm}] industries resolve to Apollo tag IDs",
              len(industries_to_tag_ids(inds)) > 0)
        if key:
            check(f"[{nm}] RELEVANT: anchor industry present",
                  bool(set(inds) & ANCHORS[key]),
                  f"got {sorted(set(inds))[:4]}…")
        check(f"[{nm}] no absurd industry",
              not (set(inds) & FORBID), str(sorted(set(inds) & FORBID)))
        # Post-gate, every SURVIVING tech chip must resolve; unresolvable ones
        # are expected to be filtered by the auto-fill (not an error here, but
        # at least one tech should survive for a tech-adjacent entity).
        surviving = [t for t, u in resolved if u]
        check(f"[{nm}] >=1 Apollo-resolvable technology suggested",
              len(surviving) >= 1, f"raw={techs}")

    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED ({len(FAILURES)}): {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
