"""Diagnose which outreach template the sequencer will use, and why.

Answers the question "why am I still getting the old email?" without guessing.
Checks, in order:

  1. Is the f1 code present on disk at all?
  2. What does _f1_enabled() resolve to for this environment?
  3. Does f1 actually RENDER for a real product (it falls back on error)?
  4. Is the running server younger than the code? A live uvicorn keeps the
     module it imported at boot — editing the file changes nothing until it
     restarts. This is the single most common cause.

Usage:
    python scripts/check_email_template.py
    python scripts/check_email_template.py --product 24
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _load_dotenv() -> None:
    p = BACKEND_DIR / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402

OK, BAD, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", type=int, help="render-test a specific product")
    args = ap.parse_args()

    problems = []
    print("== email template diagnosis ==\n")

    # 1. code present
    tpl = BACKEND_DIR / "nexus" / "templates" / "email" / "outreach_f1.tpl.html"
    print(f"{OK if tpl.exists() else BAD} f1 template file: {tpl.name}")
    if not tpl.exists():
        problems.append("outreach_f1.tpl.html missing")

    # 2. flag resolution
    from nexus.services.sequencer import _f1_enabled
    raw = os.getenv("NEXUS_EMAIL_TEMPLATE")
    enabled = _f1_enabled()
    print(f"{OK if enabled else WARN} NEXUS_EMAIL_TEMPLATE={raw!r} -> f1 enabled: {enabled}")
    if not enabled:
        problems.append(f"f1 explicitly disabled by NEXUS_EMAIL_TEMPLATE={raw!r}")

    # 3. does it actually render?
    db = SessionLocal()
    try:
        from nexus.services.sequencer import (
            _build_sender_ctx_for_product, _render_f1_step,
        )
        q = "SELECT id,workspace_id,user_id,name,source_url,key_benefits,f1_sections,icp FROM nexus_products"
        q += " WHERE id=:p" if args.product else " ORDER BY id DESC LIMIT 1"
        row = db.execute(text(q), {"p": args.product} if args.product else {}).mappings().first()
        if not row:
            print(f"{WARN} no product to render-test")
        else:
            prod = dict(row)
            prod.setdefault("campaign_brand", {})
            prod.setdefault("icp_brand", {})
            ctx = _build_sender_ctx_for_product(db, prod)

            class _Row:
                subject = "Diagnostic subject"
                body = "Diagnostic body paragraph."
                opener = ""
                real_result = ""

            art = _render_f1_step(prod, {"first_name": "Test"}, ctx, _Row(), 0)
            if art is None:
                print(f"{BAD} f1 render returned None for product {prod['id']} "
                      f"— sends would SILENTLY fall back to legacy")
                problems.append("f1 render fails for this product")
            else:
                h = art["html"]
                is_f1 = "dm-shell" in h and 'class="wm ' in h
                print(f"{OK if is_f1 else BAD} f1 renders product {prod['id']} "
                      f"({prod['name']}): {len(h)}B, imgs={h.count('<img')}, "
                      f"brand={ctx.get('brand_color') or 'neutral'}")
                if not is_f1:
                    problems.append("rendered HTML lacks f1 markers")
    finally:
        db.close()

    # 4. stale process — the usual culprit
    seq = BACKEND_DIR / "nexus" / "services" / "sequencer.py"
    code_mtime = datetime.fromtimestamp(seq.stat().st_mtime, timezone.utc)
    print(f"\n     sequencer.py last changed: {code_mtime:%Y-%m-%d %H:%M:%S} UTC")
    db = SessionLocal()
    try:
        last = db.execute(text(
            "SELECT max(sent_at) FROM nexus_touchpoints WHERE channel='email'"
        )).scalar()
    finally:
        db.close()
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        print(f"     last email sent          : {last:%Y-%m-%d %H:%M:%S} UTC")
        if last > code_mtime:
            print(f"\n{WARN} An email was sent AFTER the code changed.")
            print("       If that email used the old template, the running server is")
            print("       serving a STALE import — Python does not hot-reload.")
            print("       >>> RESTART the backend. <<<")
            problems.append("possible stale server process")

    print(f"\n{'='*58}")
    if problems:
        print("ISSUES:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("f1 is on disk, enabled, and renders. Any old-template email after")
    print("a restart would be a real bug — check the 'renderer=' log line.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
