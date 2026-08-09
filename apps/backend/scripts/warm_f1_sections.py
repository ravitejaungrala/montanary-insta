"""Warm `nexus_products.f1_sections` — the f1 email's per-product section copy.

IDEMPOTENT. A product that already has copy is skipped with no Gemini call.
Only --force regenerates.

The send path never generates: `_build_sender_ctx_for_product` reads the column
straight off the product row, so an unwarmed product simply renders the
brand-neutral fallback headings.

Usage:
    python scripts/warm_f1_sections.py --dry-run
    python scripts/warm_f1_sections.py --product 24
    python scripts/warm_f1_sections.py --workspace 7
    python scripts/warm_f1_sections.py --product 24 --force
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
from nexus.services.f1_sections import FIELDS, ensure_f1_sections  # noqa: E402

THROTTLE_SECONDS = 1.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", type=int)
    ap.add_argument("--workspace", type=int)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        where = ["name IS NOT NULL", "name <> ''"]
        params = {"lim": args.limit}
        if args.product:
            where.append("id = :pid")
            params["pid"] = args.product
        if args.workspace:
            where.append("workspace_id = :ws")
            params["ws"] = args.workspace
        if not args.product and not args.force:
            where.append("f1_sections IS NULL")

        rows = db.execute(
            text(
                "SELECT id, workspace_id, name, value_proposition, key_benefits, "
                "       product_description, f1_sections "
                f"FROM nexus_products WHERE {' AND '.join(where)} "
                "ORDER BY id LIMIT :lim"
            ),
            params,
        ).mappings().all()

        print(f"== warm f1 section copy: {len(rows)} product(s) ==")
        print(f"   force={args.force}\n")

        done = skipped = failed = 0
        for r in rows:
            prod = dict(r)
            prod["value_prop"] = prod.get("value_proposition")
            print(f"[{prod['id']:>5}] ws={prod['workspace_id']:<4} {(prod['name'] or '?')[:26]}")
            if args.dry_run:
                print("         (dry run)")
                skipped += 1
                continue

            sections = ensure_f1_sections(db, prod, force=args.force)
            if not sections:
                print("         [--] no copy generated; neutral fallbacks stay in use")
                failed += 1
                continue
            for k in FIELDS:
                v = sections.get(k)
                if v:
                    print(f"         {k:<18} {v}")
            done += 1
            time.sleep(THROTTLE_SECONDS)

        print(f"\n{'='*58}\nwarmed={done}  none={failed}  skipped={skipped}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
