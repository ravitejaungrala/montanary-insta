"""Send a single test of the branded Spenzo outreach template to
siddhuhis1@gmail.com so the user can eyeball the layout.

Usage:
    python scripts/send_spenzo_template_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _load_dotenv() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

from nexus.services.lead_enrichment import enrich_lead  # noqa: E402
from nexus.services.outreach_template import (  # noqa: E402
    DEFAULT_SENDER,
    generate_template_content,
    render_email,
)
from nexus.services.resend_client import send_email  # noqa: E402


# --- Test inputs ---------------------------------------------------------
fake_lead = {
    "first_name": "Siddharth",
    "last_name": "His",
    "title": "VP Engineering",
    "company_name": "Acme Manufacturing",
    "company_domain": "acmemanufacturing.com",
    "linkedin_url": "https://linkedin.com/in/siddharth",
}

# Sender = the Spenzo / NeuZenAI brand. Adding product-context fields
# Gemini can paraphrase but cannot fabricate beyond.
sender = {
    **DEFAULT_SENDER,
    "value_prop": (
        "AI marketing analytics that reveals which channels are wasting budget "
        "vs underinvested across paid + organic + branded search."
    ),
    "icp_pain": (
        "Performance marketing teams can't see channel cannibalisation, "
        "branded-search waste, and budget-allocation mistakes in real time."
    ),
    "key_benefits": [
        "Unified channel attribution across paid + organic + branded search",
        "AI flags overinvested vs underinvested channels per campaign",
        "Quantifies brand-vs-non-brand cannibalisation",
        "Plugs into existing analytics stacks (GA4, Looker, BigQuery, etc.)",
    ],
}


def main() -> None:
    print("== Spenzo template send test ==\n")

    # Step 1: enrich the lead (lightweight Gemini call)
    print("[1] Enriching lead via lead_enrichment...")
    enrichment = enrich_lead(
        fake_lead,
        {
            "name": sender["company_name"],
            "value_prop": sender["value_prop"],
            "icp_pain": sender["icp_pain"],
        },
    )
    if enrichment:
        print(f"    common_pain : {enrichment.get('common_pain')}")
        print(f"    talking_pts : {len(enrichment.get('talking_points') or [])} point(s)")
    else:
        print("    (enrichment empty — Gemini failed, falling back)")

    # Step 2: generate body + real_result via Gemini
    print("\n[2] Generating template content (subject, body, real_result)...")
    gemini = generate_template_content(fake_lead, sender, enrichment)
    print(f"    subject    : {gemini.get('subject')!r}")
    print(f"    intro_body : {(gemini.get('intro_body') or '')[:140]}...")
    print(f"    real_result: {(gemini.get('real_result') or '')[:140]}...")

    # Step 3: render full HTML email
    print("\n[3] Rendering HTML template...")
    artefact = render_email(fake_lead, sender, gemini, enrichment)
    print(f"    subject    : {artefact['subject']}")
    print(f"    html bytes : {len(artefact['html'])}")
    print(f"    text bytes : {len(artefact['text'])}")

    # Save HTML to disk for offline preview too
    preview_path = BACKEND_DIR / "scripts" / "_spenzo_template_preview.html"
    preview_path.write_text(artefact["html"], encoding="utf-8")
    print(f"    saved preview to: {preview_path}")

    # Step 4: send via Resend
    print("\n[4] Sending via Resend to siddhuhis1@gmail.com...")
    from_addr = os.getenv("NEXUS_DEFAULT_FROM_EMAIL") or "onboarding@resend.dev"
    result = send_email(
        to="siddhuhis1@gmail.com",
        from_=from_addr,
        subject=artefact["subject"],
        html=artefact["html"],
        text=artefact["text"],
    )
    if result.get("ok"):
        print(f"    [OK] message_id={result.get('id')}  from={from_addr}")
    else:
        print(f"    [FAIL] {result.get('error')}")


if __name__ == "__main__":
    main()
