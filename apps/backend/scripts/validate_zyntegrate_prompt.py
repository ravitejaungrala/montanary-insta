"""Validate the UPDATED email prompt end-to-end (NO send — print only).

Picks one real lead from the ws7 Zyntegrate campaign (the govardhan.y@neuzenai.com
account), generates all 4 cadence emails with the live generate_template_content
(which uses the freshly-edited _SYSTEM prompt), and simulates the production
render (_render_branded_step). Prints everything so we can eyeball quality.

Usage:
    cd apps/backend
    python scripts/validate_zyntegrate_prompt.py
Env:
    ZTEST_CAMPAIGN_ID  (default 76 = Zyntegrate #3, ws7)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _env():
    p = BACKEND_DIR / ".env"
    if not p.exists():
        return
    for l in p.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            k, _, v = l.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_env()

from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from nexus.services.sequencer import _load_product, _render_branded_step  # noqa: E402
from nexus.services.outreach_template import (  # noqa: E402
    get_default_sender, generate_template_content,
)

CID = int(os.getenv("ZTEST_CAMPAIGN_ID", "76"))

# Product description provided by the operator for this test run.
USER_PD = {
    "what_the_company_is": (
        "Zyntegrate — an intelligent integration platform that unifies fragmented "
        "systems and automates complex workflows, from legacy databases to modern "
        "cloud platforms and APIs"
    ),
    "what_they_do": (
        "Uses AI agents and a low-code/no-code visual builder to create and manage "
        "integrations — connecting to third-party APIs, orchestrating data flows "
        "across multiple cloud vendors, with real-time monitoring, alerts, and "
        "enterprise-grade security."
    ),
    "key_capabilities": [
        "Legacy System Integration", "Vendor Agnostic Orchestration",
        "Third-Party API Integration", "AI Agents for Automation",
        "Low-Code / No-Code Integrations", "Prebuilt Connectors",
        "Monitoring & Alerts", "Workflow Automation", "Unified Data Layer",
        "Real-Time Sync", "Enterprise Security", "Analytics & Insights",
        "Cloud & Legacy Support",
    ],
    "who_they_serve": (
        "Modern enterprises that manage hundreds of disconnected applications and "
        "need to connect legacy systems with cloud platforms without complex "
        "rip-and-replace migrations"
    ),
    "target_industries": ["Modern enterprises with large, fragmented application portfolios"],
}
USER_VALUE_PROP = (
    "An intelligent integration platform that unifies fragmented systems and "
    "automates complex workflows — from legacy databases to modern cloud and APIs "
    "— using AI agents and a low-code/no-code builder."
)
USER_ICP_PAIN = (
    "Enterprises managing hundreds of disconnected applications struggle to "
    "connect legacy systems with cloud platforms and automate data flows without "
    "complex rip-and-replace migrations."
)


def main():
    db = SessionLocal()
    try:
        camp = db.execute(text("SELECT id, workspace_id, product_id FROM nexus_campaigns WHERE id=:c"), {"c": CID}).mappings().first()
        ws = camp["workspace_id"]
        prod = _load_product(db, ws, CID) or {}
        # Override with the operator-provided description for this test.
        prod["product_description"] = USER_PD
        prod["value_prop"] = USER_VALUE_PROP
        prod["icp_pain"] = USER_ICP_PAIN
        print("=" * 72)
        print(f"PRODUCT (campaign {CID}, ws {ws}):", prod.get("name"), "[operator-provided description]")
        pd = USER_PD
        print("  WHAT IT IS :", (pd.get("what_the_company_is") or "")[:90])
        print("  WHAT WE DO :", (pd.get("what_they_do") or "")[:90])
        print("  WHO SERVE  :", (pd.get("who_they_serve") or "")[:90])

        # sender context (production 3-layer)
        ctx = dict(get_default_sender())
        ctx["company_name"] = prod.get("name")
        ctx["company_url"] = (prod.get("source_url") or "").replace("https://", "").replace("http://", "").rstrip("/")
        ctx["value_prop"] = prod.get("value_prop") or ""
        ctx["icp_pain"] = prod.get("icp_pain") or ""
        ctx["business_type"] = prod.get("entity_type") or "product"
        ctx["product_description"] = pd
        try:
            from nexus.services.brand_dna import fetch_brand_colors
            dna = fetch_brand_colors(db, prod.get("user_id"), prod.get("source_url")) or {}
            if dna.get("brand_color"):
                ctx["brand_color"] = dna["brand_color"]
            if dna.get("accent_color"):
                ctx["accent_color"] = dna["accent_color"]
        except Exception:
            pass

        # one real lead in this campaign, prefer one with company background
        row = db.execute(text("""
            SELECT gl.first_name, gl.last_name, gl.role, gl.company_name, gl.company_domain,
                   gl.email, gl.linkedin_url, gl.person_city, gl.person_state, gl.person_country,
                   gl.linkedin_headline
              FROM nexus_leads n JOIN nexus_global_leads gl ON gl.id = n.global_lead_id
             WHERE n.campaign_id = :c
             ORDER BY n.id DESC LIMIT 1
        """), {"c": CID}).mappings().first()
        lead = {
            "first_name": row["first_name"] or "", "last_name": row["last_name"] or "",
            "title": row["role"] or "", "company_name": row["company_name"] or "",
            "company_domain": row["company_domain"] or "", "email": row["email"] or "",
            "linkedin_url": row["linkedin_url"] or "", "linkedin_headline": row["linkedin_headline"] or "",
            "person_city": row["person_city"], "person_state": row["person_state"], "person_country": row["person_country"],
        }
        print()
        print("LEAD:", lead["first_name"], lead["last_name"], "|", lead["title"], "@", lead["company_name"], f"({lead['company_domain']})")

        # company background (3rd context source)
        bg = db.execute(text("SELECT meta_description, body_snippet, news_headlines FROM nexus_lead_enrichment WHERE company_domain=:d LIMIT 1"), {"d": lead["company_domain"]}).mappings().first()
        enrichment = {"company_summary": "", "company_news": []}
        if bg:
            enrichment["company_summary"] = str(bg.get("meta_description") or bg.get("body_snippet") or "")[:600]
            nh = bg.get("news_headlines")
            if isinstance(nh, list):
                enrichment["company_news"] = [str(n.get("title") if isinstance(n, dict) else n) for n in nh[:2]]
        print("  background:", "FOUND" if (enrichment["company_summary"] or enrichment["company_news"]) else "none")

        print("\n[Generating with the UPDATED prompt — gemini-3.5-flash, no token cap]...\n")
        c = generate_template_content(lead, ctx, enrichment)

        labels = [
            ("INITIAL  (day 0)", "subject", "personalized_opener", "intro_body", "real_result"),
            ("FOLLOW-UP 1 (+3)", "followup_subject", None, "followup_body", None),
            ("FOLLOW-UP 2 (+6)", "followup2_subject", None, "followup2_body", None),
            ("CLOSING  (+9)", "closing_subject", None, "closing_body", None),
        ]
        for name, sk, ok, bk, rk in labels:
            print("─" * 72)
            print(f"  {name}")
            print("  Subject :", c.get(sk))
            if ok:
                print("  Opener  :", c.get(ok))
            print("  Body    :", c.get(bk))
            if rk:
                print("  Result  :", c.get(rk))
            print()

        # Render + SEND each of the 4 cadence emails via the connected mailbox,
        # so you can see them in your Outlook inbox. Recipient overridden to our
        # test inboxes; content is the operator-description generation above.
        lead_dict = {"first_name": lead["first_name"], "title": lead["title"],
                     "company_name": lead["company_name"], "company_domain": lead["company_domain"]}
        steps = [
            (0, "Initial",  c.get("subject"),          c.get("intro_body"),    c.get("real_result"), c.get("personalized_opener")),
            (1, "Follow-up 1", c.get("followup_subject"),  c.get("followup_body"),  None, None),
            (2, "Follow-up 2", c.get("followup2_subject"), c.get("followup2_body"), None, None),
            (3, "Closing",     c.get("closing_subject"),   c.get("closing_body"),   None, None),
        ]
        # resolve a connected mailbox to send FROM
        from nexus.models_phase4 import NexusConversationAccount
        acct = (
            db.query(NexusConversationAccount)
            .filter(NexusConversationAccount.provider.in_(("outlook", "gmail")),
                    NexusConversationAccount.workspace_id == ws)
            .order_by(NexusConversationAccount.id.asc())
            .first()
        )
        recipients = [e.strip() for e in os.getenv(
            "ZTEST_TO", "govardhan.y@neuzenai.com,yerasigovardhanreedy3965@gmail.com"
        ).split(",") if e.strip()]
        print("\n" + "=" * 72)
        print(f"SENDING 4 emails via {getattr(acct,'email_address','(no mailbox)')} -> {', '.join(recipients)}")
        print("=" * 72)
        from nexus.services.connectors import get_connector
        for step, name, subj, body, rr, opener in steps:
            row = SimpleNamespace(subject=subj, body=body, real_result=rr, opener=opener)
            art = _render_branded_step(db, prod, lead_dict, ctx, row, step)
            if not art:
                print(f"  {name}: render failed — skipped"); continue
            tagged = f"[Zyntegrate TEST · {name}] {art['subject']}"
            for to in recipients:
                if acct and getattr(acct, "provider", "") in ("outlook", "gmail"):
                    res = get_connector(acct.provider).send_for_account(
                        db, acct, to_email=to, subject=tagged,
                        body_html=art["html"], body_text=art["text"])
                    ok = "SENT" if res.get("ok") else f"FAILED ({res.get('error')})"
                else:
                    ok = "NO MAILBOX"
                print(f"  {name:12} -> {to}: {ok}")
        print("\nDone. Check the inbox(es):", ", ".join(recipients))
    finally:
        db.close()


if __name__ == "__main__":
    main()
