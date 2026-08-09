"""Send the f1 branded template to a real inbox for client-render testing.

Renders the whole 4-step cadence with realistic product data and sends each
step, so you can see the initial pitch and the lighter follow-ups side by side
in a real client.

Safety: goes through `resend_client.send_email`, which honours
TEST_REDIRECT_EMAIL — if that is set, every message is redirected there
regardless of --to, so this can never reach a prospect.

Usage:
    python scripts/send_f1_test.py --to you@example.com
    python scripts/send_f1_test.py --to you@example.com --steps 0
    python scripts/send_f1_test.py --to you@example.com --dry-run
    python scripts/send_f1_test.py --to you@example.com --no-images
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _load_dotenv() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

from nexus.services.email_palette import derive_palette  # noqa: E402
from nexus.services.outreach_template_f1 import render_email_f1  # noqa: E402

LEAD = {
    "first_name": "Nry",
    "last_name": "Reddy",
    "title": "AI Lead",
    "company_name": "NeuZenAI",
}

SENDER = {
    "company_name": "Spenzo",
    "company_url": "spenzo.ai",
    "rep_name": "B. Subba Rami Reddy",
    "rep_title": "AI Delivery Head",
    "cta_url": "https://spenzo.ai/book",
    "cta_label": "Book a quick call",
    "brand_color": "#ff4500",
    "accent_color": "#0f4c5c",
    "key_benefits": [
        "Marketing mix modeling — See how combined marketing effort influences "
        "total revenue, channel by channel.",
        "Budget optimization — Find the best allocation across active channels "
        "before you commit spend.",
        "ROI forecasting — Project returns from current performance, with "
        "confidence ranges attached.",
        "Automated data integration — Every marketing source connected and "
        "modelled, with no manual preparation.",
    ],
    # Real Spenzo proof points. Never model-generated — see the plan §5.2.
    "proof_points": [
        {"value": "3.2x", "label": "Avg. ROAS", "note": "Across live customer accounts."},
        {"value": "$10M+", "label": "Spend optimized", "note": "Reallocated with model guidance."},
        {"value": "92%", "label": "Model accuracy", "note": "Backtested on historic revenue."},
    ],
}

GEMINI = {
    "subject": "Quick thought on your channel mix",
    "headline": "Make marketing decisions with clarity",
    "preheader": "AI-powered marketing mix modeling. See which channels actually drive growth.",
    "intro_body": (
        "I noticed your work as AI Lead at NeuZenAI. Quantifying marketing impact is "
        "likely a frequent topic given the complexity of your data landscape. Spenzo "
        "replaces spreadsheet handoffs and guesswork with AI-powered marketing mix modeling."
    ),
    "benefits_heading": "Four things, end to end",
    "benefits_sub": "From raw channel data to the allocation you should run next quarter.",
    "editorial_heading": "Built for the channels that drive growth",
    "editorial_body": "A clearer view of revenue, and one place to see which spend earns it.",
    "cta_heading": "See it running on your own numbers",
    "cta_subcopy": "A 15-minute walkthrough. No prep needed.",
    "followup_subject": "Following up on marketing mix",
    "followup_body": (
        "Circling back on this. Most teams we talk to already have the channel data — "
        "the gap is turning it into an allocation decision they can defend."
    ),
    "followup2_subject": "One more thought",
    "followup2_body": (
        "Last note from me on this. If budget planning for next quarter is already "
        "underway, this is usually the moment the modelling pays for itself."
    ),
    "closing_subject": "Closing the loop",
    "closing_body": (
        "I will stop reaching out here. If marketing attribution becomes a priority "
        "later, we are easy to find."
    ),
}

STEP_NAMES = {0: "initial", 1: "follow-up 1", 2: "follow-up 2", 3: "closing"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    ap.add_argument("--steps", default="0,1,2,3",
                    help="comma-separated cadence steps (default all)")
    ap.add_argument("--dry-run", action="store_true", help="render only, do not send")
    ap.add_argument("--no-images", action="store_true",
                    help="render without image URLs (blocked-images simulation)")
    args = ap.parse_args()

    steps = [int(s) for s in args.steps.split(",") if s.strip() != ""]

    sender = dict(SENDER)
    if not args.no_images:
        # Only set image URLs that actually resolve. Anything unset simply
        # omits its block — the template is built for that.
        imgs = {}
        for key, env in (
            ("logo_url", "NEXUS_EMAIL_LOGO_URL"),
            ("hero_url", "NEXUS_EMAIL_HERO_URL"),
            ("editorial_url", "NEXUS_EMAIL_EDITORIAL_IMAGE_URL"),
        ):
            v = (os.getenv(env) or "").strip()
            if v:
                imgs[key] = v
        sender["images"] = imgs

    pal = derive_palette(sender.get("brand_color"), sender.get("accent_color"))
    print("== f1 template test send ==\n")
    print(f"  to        : {args.to}")
    print(f"  steps     : {steps}")
    print(f"  brand     : {sender['brand_color']} -> button {pal['c_link']}, "
          f"counter {pal['c_counter']}")
    imgs = sender.get("images") or {}
    print(f"  images    : {', '.join(imgs) if imgs else '(none configured — blocks omitted)'}")

    redirect = os.getenv("TEST_REDIRECT_EMAIL")
    if redirect:
        print(f"  REDIRECT  : TEST_REDIRECT_EMAIL={redirect} — all mail goes there")
    from_addr = os.getenv("NEXUS_DEFAULT_FROM_EMAIL") or "onboarding@resend.dev"
    print(f"  from      : {from_addr}\n")

    if not os.getenv("RESEND_API_KEY") and not args.dry_run:
        print("  [WARN] RESEND_API_KEY is not set — the send will fail cleanly.")
        print("         Rendering + previews still run.\n")

    sent = failed = 0
    for st in steps:
        artefact = render_email_f1(LEAD, sender, GEMINI, step=st)
        html = artefact["html"]
        preview = BACKEND_DIR / "scripts" / f"_f1_send_step{st}.html"
        preview.write_text(html, encoding="utf-8")

        print(f"[step {st}] {STEP_NAMES.get(st, '?'):<12} "
              f"{len(html):>6}B  imgs={html.count('<img')}  "
              f"cards={html.count('border-left:3px solid')}  "
              f"cta={'y' if 'NEXT STEP' in html else 'n'}")
        print(f"          subject: {artefact['subject']}")
        print(f"          preview: {preview.name}")

        if args.dry_run:
            continue

        from nexus.services.resend_client import send_email
        res = send_email(
            to=args.to,
            from_=from_addr,
            subject=artefact["subject"],
            html=html,
            text=artefact["text"],
        )
        if res.get("ok"):
            print(f"          [SENT] id={res.get('id')}")
            sent += 1
        else:
            print(f"          [FAIL] {res.get('error')}")
            failed += 1
        time.sleep(1.0)

    print(f"\n{'='*56}")
    if args.dry_run:
        print("dry run — nothing sent. Open the _f1_send_step*.html previews.")
        return 0
    print(f"sent={sent}  failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
