"""Diagnostic: test Apollo's contact upsert API directly.

Bypasses Lambda entirely — uses the APOLLO_API_KEY from your local .env
and tries the EXACT same POST /v1/contacts call that the production
backend is making. Whatever error Apollo returns will be printed verbatim
so we know what's actually failing.

Run:
    cd apps/backend
    source venv/Scripts/activate
    PYTHONIOENCODING=utf-8 python scripts/test_apollo_upsert.py
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv


APOLLO_BASE = "https://api.apollo.io/v1"


def main() -> int:
    load_dotenv()
    api_key = os.getenv("APOLLO_API_KEY")
    if not api_key:
        print("ERROR: APOLLO_API_KEY not in .env")
        return 1
    print(f"API key: ****{api_key[-6:]} (length {len(api_key)})\n")

    headers = {
        "X-Api-Key": api_key,
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
    }

    body = {
        "first_name": "Govardhan",
        "last_name": "Reddy",
        "email": "govardhan.y@neuzenai.com",
        "title": "Founder",
        "organization_name": "NeuzenAI",
    }
    print(f"POST {APOLLO_BASE}/contacts")
    print(f"Body: {json.dumps(body, indent=2)}\n")

    try:
        with httpx.Client(timeout=25.0) as c:
            r = c.post(f"{APOLLO_BASE}/contacts", headers=headers, json=body)
    except Exception as e:
        print(f"NETWORK ERROR: {e}")
        return 1

    print(f"Response status: {r.status_code}")
    print(f"Response headers:")
    for k, v in r.headers.items():
        if k.lower() in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "content-type", "x-request-id"):
            print(f"  {k}: {v}")
    print(f"\nResponse body:")
    try:
        rj = r.json()
        print(json.dumps(rj, indent=2)[:2000])
    except Exception:
        print(r.text[:2000])

    print()
    if r.status_code < 400:
        print("=> CONTACT CREATE SUCCEEDED")
    elif r.status_code == 401:
        print("=> 401 UNAUTHORIZED — API key is invalid or expired")
    elif r.status_code == 403:
        print("=> 403 FORBIDDEN — your Apollo plan likely doesn't allow contact creation via API")
        print("   (Apollo restricts /v1/contacts POST to certain paid plans)")
    elif r.status_code == 422:
        print("=> 422 UNPROCESSABLE — Apollo rejected the payload (often 'contact exists' or schema)")
        print("   Falling back to /contacts/search...")
        try:
            with httpx.Client(timeout=25.0) as c:
                sr = c.post(
                    f"{APOLLO_BASE}/contacts/search",
                    headers=headers,
                    json={"q_keywords": body["email"], "page": 1, "per_page": 5},
                )
            print(f"   search status: {sr.status_code}")
            print(f"   search body: {sr.text[:600]}")
        except Exception as e:
            print(f"   search failed: {e}")
    elif r.status_code == 429:
        print("=> 429 RATE LIMITED — Apollo API throttle. Retry-After: " + str(r.headers.get("retry-after")))
    else:
        print(f"=> {r.status_code} — unexpected. See body above.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
