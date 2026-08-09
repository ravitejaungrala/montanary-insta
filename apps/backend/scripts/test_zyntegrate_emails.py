"""End-to-end test of the 4-email Zyntegrate cadence — IDENTICAL to production.

This does NOT hardcode the product or the template. It drives the exact
production path:
  * product context  → sequencer._load_product (real DB row, real
                        product_description + brand + entity_type)
  * sender context   → built the same 3-layer way process_due_sequences does
  * email content    → outreach_template.generate_template_content (the same
                        call _ensure_lead_emails makes — gemini-3.5-flash,
                        no token cap, few-shot prompt)
  * HTML render      → sequencer._render_branded_step (production's PRIMARY
                        branded renderer → email_blocks.render_rich_email),
                        with render_email as the same fallback prod uses
  * send             → the connected mailbox via get_connector (production send)

The ONLY thing overridden for testing is the RECIPIENT address — we pull a
REAL lead from the DB for personalization, then send to our own inbox.

Usage:
    cd apps/backend
    python scripts/test_zyntegrate_emails.py

Env overrides:
    ZTEST_CAMPAIGN_ID  campaign whose product to use (default 40 = Zyntegrate)
    ZTEST_FROM         preferred sending mailbox (default govardhan.y@neuzenai.com)
    ZTEST_TO           comma-separated recipients (default the two test inboxes)
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
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


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

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402
from nexus.services.outreach_template import (  # noqa: E402
    generate_template_content,
    get_default_sender,
    render_email,
)
from nexus.services.sequencer import _load_product, _render_branded_step  # noqa: E402
from nexus.services.resend_client import send_email  # noqa: E402


# ── Test sender / recipients ────────────────────────────────────────────────
CAMPAIGN_ID = int(os.getenv("ZTEST_CAMPAIGN_ID", "40"))  # Zyntegrate campaign
SENDER_FROM = os.getenv("ZTEST_FROM", "govardhan.y@neuzenai.com")
RECIPIENTS = [
    e.strip()
    for e in os.getenv(
        "ZTEST_TO",
        "govardhan.y@neuzenai.com,yerasigovardhanreedy3965@gmail.com",
    ).split(",")
    if e.strip()
]

STEP_LABELS = {
    0: "Initial (day 0)",
    1: "Follow-up 1 (day +3)",
    2: "Follow-up 2 (day +6)",
    3: "Closing (day +9)",
}


def _build_sender_ctx(db, prod):
    """Replicates process_due_sequences' 3-layer sender context build so the
    Gemini prompt + render get EXACTLY the production inputs."""
    sender_ctx = dict(get_default_sender())
    prod_name = (prod.get("name") or "").strip()
    prod_url = (prod.get("source_url") or "").strip()
    clean_url = prod_url.replace("https://", "").replace("http://", "").rstrip("/")
    if prod_name:
        sender_ctx["company_name"] = prod_name
    if clean_url:
        sender_ctx["company_url"] = clean_url

    # Layer 3: per-product brand overrides (icp.brand JSONB).
    brand = prod.get("icp_brand") or {}
    if isinstance(brand, dict):
        for k in (
            "company_name", "company_url", "rep_name", "rep_title",
            "rep_email", "rep_phone", "cta_url", "cta_label",
        ):
            v = brand.get(k)
            if v:
                sender_ctx[k] = str(v)

    sender_ctx["value_prop"] = prod.get("value_prop") or ""
    sender_ctx["icp_pain"] = prod.get("icp_pain") or ""
    sender_ctx["business_type"] = prod.get("entity_type") or "product"
    sender_ctx["product_description"] = prod.get("product_description") or {}

    # Brand colors from Business DNA (same source as prod).
    try:
        from nexus.services.brand_dna import fetch_brand_colors
        dna = fetch_brand_colors(db, prod.get("user_id"), prod.get("source_url")) or {}
        if dna.get("brand_color"):
            sender_ctx["brand_color"] = dna["brand_color"]
        if dna.get("accent_color"):
            sender_ctx["accent_color"] = dna["accent_color"]
    except Exception as exc:
        print(f"   (brand color fetch failed, non-fatal: {str(exc)[:80]})")
    return sender_ctx


def _fetch_real_lead(db):
    """Real lead for personalization. Prefer one WITH scraped company
    background so that context is exercised; fall back to any usable lead."""
    cols = (
        "gl.first_name, gl.last_name, gl.role, gl.company_name, "
        "gl.company_domain, gl.email, gl.linkedin_url, gl.person_city, "
        "gl.person_state, gl.person_country, gl.linkedin_headline"
    )
    queries = [
        f"""SELECT {cols}
              FROM nexus_global_leads gl
              JOIN nexus_lead_enrichment e ON e.company_domain = gl.company_domain
             WHERE COALESCE(gl.role,'') <> '' AND COALESCE(gl.company_name,'') <> ''
             ORDER BY gl.id DESC LIMIT 1""",
        f"""SELECT {cols}
              FROM nexus_global_leads gl
             WHERE COALESCE(gl.role,'') <> '' AND COALESCE(gl.company_name,'') <> ''
               AND COALESCE(gl.company_domain,'') <> ''
             ORDER BY gl.id DESC LIMIT 1""",
    ]
    for q in queries:
        try:
            r = db.execute(text(q)).mappings().first()
            if r:
                return dict(r)
        except Exception as exc:
            print(f"   (lead query fell back: {str(exc)[:90]})")
            break
    r = db.execute(text(
        "SELECT first_name, last_name, role, company_name, company_domain, "
        "email, linkedin_url FROM nexus_global_leads "
        "WHERE COALESCE(role,'')<>'' AND COALESCE(company_name,'')<>'' "
        "ORDER BY id DESC LIMIT 1"
    )).mappings().first()
    return dict(r) if r else None


def _company_background(db, company_domain):
    """Inline copy of sequencer._li_company_background (avoids re-import)."""
    out = {"company_summary": "", "company_news": []}
    if not company_domain:
        return out
    try:
        bg = db.execute(
            text(
                "SELECT meta_description, body_snippet, news_headlines "
                "FROM nexus_lead_enrichment WHERE company_domain = :d LIMIT 1"
            ),
            {"d": company_domain},
        ).mappings().first()
        if bg:
            summary = bg.get("meta_description") or bg.get("body_snippet")
            if summary:
                out["company_summary"] = str(summary)[:600]
            news = bg.get("news_headlines")
            titles = []
            if isinstance(news, list):
                for n in news[:2]:
                    if isinstance(n, dict) and n.get("title"):
                        titles.append(str(n["title"]))
                    elif isinstance(n, str):
                        titles.append(n)
            out["company_news"] = titles
    except Exception as exc:
        print(f"   (company background lookup failed: {str(exc)[:90]})")
    return out


def _resolve_sender_account(db, prefer_email):
    """Connected mailbox to send FROM — same model production uses."""
    try:
        from nexus.models_phase4 import NexusConversationAccount as A
    except Exception as exc:
        print(f"   (mailbox model import failed: {str(exc)[:90]})")
        return None
    try:
        accts = db.query(A).filter(A.provider.in_(("outlook", "gmail"))).all()
    except Exception as exc:
        print(f"   (mailbox query failed: {str(exc)[:90]})")
        return None
    if not accts:
        return None

    def _rank(a):
        match = (getattr(a, "email_address", "") or "").lower() == (prefer_email or "").lower()
        active = (getattr(a, "status", "") or "") == "active"
        return (0 if match else 1, 0 if active else 1, getattr(a, "id", 0))

    accts.sort(key=_rank)
    return accts[0]


def _dispatch(db, account, to, subject, html, body_text):
    """Send via the connected mailbox (production path); Resend fallback."""
    if account is not None and getattr(account, "provider", "") in ("outlook", "gmail"):
        from nexus.services.connectors import get_connector
        conn = get_connector(account.provider)
        res = conn.send_for_account(
            db, account,
            to_email=to, subject=subject, body_html=html, body_text=body_text,
        )
        return {
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "from": getattr(account, "email_address", SENDER_FROM),
        }
    res = send_email(to=to, from_=SENDER_FROM, subject=subject, html=html, text=body_text)
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "from": SENDER_FROM}


def _email_rows_from_content(c):
    """Build the per-step row objects EXACTLY as _ensure_lead_emails stores
    them (subject/body/real_result/opener) — just in memory, no DB write."""
    opener = c.get("personalized_opener") or ""
    plan = [
        (0, c.get("subject"), c.get("intro_body"), c.get("real_result"), opener),
        (1, c.get("followup_subject"), c.get("followup_body"), None, None),
        (2, c.get("followup2_subject"), c.get("followup2_body"), None, None),
        (3, c.get("closing_subject"), c.get("closing_body"), None, None),
    ]
    rows = {}
    for step, subj, body, rr, op in plan:
        rows[step] = SimpleNamespace(
            subject=subj or "", body=body or "", real_result=rr, opener=op,
        )
    return rows


def main() -> None:
    print("== Zyntegrate 4-email cadence test (production-identical) ==\n")

    db = SessionLocal()
    try:
        # 0) Resolve the real product from the campaign (production loader).
        camp = db.execute(
            text("SELECT id, workspace_id, product_id, name FROM nexus_campaigns WHERE id = :c"),
            {"c": CAMPAIGN_ID},
        ).mappings().first()
        if not camp:
            print(f"ERROR: campaign {CAMPAIGN_ID} not found.")
            return
        workspace_id = camp["workspace_id"]
        prod = _load_product(db, workspace_id, CAMPAIGN_ID)
        if not prod:
            print(f"ERROR: no product for campaign {CAMPAIGN_ID}.")
            return
        print(f"[0] Product (real DB row, campaign {CAMPAIGN_ID}, ws {workspace_id}):")
        print(f"    Name        : {prod.get('name')}")
        print(f"    business_type: {prod.get('entity_type')}")
        pd = prod.get("product_description") or {}
        print(f"    product_description sections: {list(pd.keys())}")

        sender_ctx = _build_sender_ctx(db, prod)

        # 1) Real lead.
        row = _fetch_real_lead(db)
        if not row:
            print("ERROR: no usable lead found.")
            return
        lead = {
            "first_name": row.get("first_name") or "",
            "last_name": row.get("last_name") or "",
            "title": row.get("role") or "",
            "company_name": row.get("company_name") or "",
            "company_domain": row.get("company_domain") or "",
            "email": row.get("email") or "",
            "linkedin_url": row.get("linkedin_url") or "",
            "linkedin_headline": row.get("linkedin_headline") or "",
            "person_city": row.get("person_city"),
            "person_state": row.get("person_state"),
            "person_country": row.get("person_country"),
        }
        print("\n[1] Real lead (recipient overridden for testing):")
        print(f"    Name    : {lead['first_name']} {lead['last_name']}".rstrip())
        print(f"    Title   : {lead['title']}")
        print(f"    Company : {lead['company_name']}  ({lead['company_domain']})")

        # 2) Company background (3rd context source).
        bg = _company_background(db, lead["company_domain"])
        enrichment = {
            "company_summary": bg.get("company_summary") or "",
            "company_news": bg.get("company_news") or [],
        }
        if enrichment["company_summary"] or enrichment["company_news"]:
            print(f"    Background: FOUND ({len(enrichment['company_summary'])} chars, "
                  f"{len(enrichment['company_news'])} news)")
        else:
            print("    Background: none on file — opener grounds on role + company")

        # 3) Generate content — the SAME call _ensure_lead_emails makes.
        print("\n[2] Generating 4 emails (gemini-3.5-flash, no token cap)...")
        content = generate_template_content(lead, sender_ctx, enrichment)
        email_rows = _email_rows_from_content(content)

        # 4) Resolve mailbox; mirror prod's rep_email injection.
        account = _resolve_sender_account(db, SENDER_FROM)
        if account is not None:
            if not sender_ctx.get("rep_email") and getattr(account, "email_address", ""):
                sender_ctx["rep_email"] = account.email_address
            print(f"\n    Mailbox: {getattr(account,'email_address','?')} "
                  f"(provider={getattr(account,'provider','?')}, status={getattr(account,'status','?')})")
        else:
            print("\n    No connected mailbox — Resend fallback (needs RESEND_API_KEY).")

        # 5) Render via PRODUCTION renderer + send.
        print("\n[3] Rendering (production _render_branded_step) + sending:\n")
        for step in (0, 1, 2, 3):
            email_row = email_rows[step]
            artefact = _render_branded_step(db, prod, lead, sender_ctx, email_row, step)
            renderer = "render_rich_email (branded)"
            if artefact is None:
                # same fallback production uses
                artefact = render_email(lead, sender_ctx, content, enrichment, step=step)
                renderer = "render_email (fallback)"

            subject = artefact["subject"]
            html = artefact["html"]
            body_text = artefact["text"]

            print("─" * 72)
            print(f"  STEP {step} — {STEP_LABELS[step]}   [{renderer}]")
            print(f"  Subject: {subject}")
            print(f"  (html {len(html)} bytes)")
            print("  Body:")
            for line in body_text.splitlines():
                print(f"    {line}")
            print()

            tagged = f"[Zyntegrate TEST · {STEP_LABELS[step]}] {subject}"
            for to in RECIPIENTS:
                res = _dispatch(db, account, to, tagged, html, body_text)
                status = "SENT" if res.get("ok") else f"FAILED ({res.get('error')})"
                print(f"    send {res.get('from')} -> {to}: {status}")
            print()

        print("─" * 72)
        print("Done. Check the inbox(es):", ", ".join(RECIPIENTS))
    finally:
        db.close()


if __name__ == "__main__":
    main()
