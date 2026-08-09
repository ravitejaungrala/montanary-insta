"""Offline test for the bulk_match row-pairing fix (campaign 60's failure).

Simulates the EXACT failure: Apollo's search returns candidates with CONTACT
ids, bulk_match answers with PERSON ids (different!), plus a null row for an
unmatched person. The old id-keyed pairing dropped every row (7 matched, 7
charged, 0 attached); the positional fix must pair them all.

No network, no credits, no DB — Apollo's HTTP call is monkeypatched.

Run from apps/backend:
    python scripts/test_bulk_match_pairing.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")

from nexus.services import discovery_apollo as da  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


# Candidates as the SEARCH returned them: CONTACT ids, no domain (hidden).
CANDIDATES = [
    {"apollo_id": "contact_111", "first_name": "Alice", "company_name": "PayPal"},
    {"apollo_id": "contact_222", "first_name": "Bob", "company_name": "Rippling"},
    {"apollo_id": "contact_333", "first_name": "Carol", "company_name": "Uniphore"},
]

# What bulk_match actually answers: PERSON ids (different from contact ids!),
# order preserved, one null for the person Apollo couldn't match.
FAKE_RESPONSE = {
    "matches": [
        {"id": "person_AAA", "email": "alice@paypal.com", "organization": {"primary_domain": "paypal.com"}},
        None,  # Bob unmatched — Apollo pads with null
        {"id": "person_CCC", "email": "carol@uniphore.com", "organization": {"primary_domain": "uniphore.com"}},
    ]
}


async def fake_http_post(url, key, body, stats=None):
    assert "bulk_match" in url
    return FAKE_RESPONSE


async def main():
    da._http_post = fake_http_post  # no network

    print("\nScenario: contact-id candidates, person-id rows, one null row")
    matched = await da._bulk_match("fake-key", CANDIDATES)

    check("Alice paired under HER candidate id (old code: lost)",
          "contact_111" in matched and matched["contact_111"]["email"] == "alice@paypal.com")
    check("Carol paired under HER candidate id (old code: lost)",
          "contact_333" in matched and matched["contact_333"]["email"] == "carol@uniphore.com")
    check("Bob (null row) safely absent, no crash", "contact_222" not in matched)
    check("person-id fallback keys also present", "person_AAA" in matched)

    # The downstream lookup discover_company_leads_apollo does per candidate:
    paired = sum(
        1 for c in CANDIDATES
        if matched.get(str(c["apollo_id"])) and da._pick_email(matched[str(c["apollo_id"])])
    )
    check("downstream lookup now finds 2/3 with usable emails", paired == 2, f"paired={paired}")
    print("\nWith the OLD id-only keying this scenario paired 0/3 — exactly "
          "campaign 60: 7 matched, 7 charged, 0 attached.")
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
