"""Warm `nexus_products.brand_colors` by extracting from each product's website.

This is the ONLY path that makes the live extraction call. The send loop reads
the cache (`resolve_brand_colors(..., allow_extraction=False)`), so a product
with no Business DNA emails in neutral slate until this has run for it.

Each product costs one Microlink fetch + one Gemini vision call, so it is
throttled and skips anything already resolved.

Usage:
    python scripts/warm_brand_colors.py                # all unresolved
    python scripts/warm_brand_colors.py --product 42   # one product
    python scripts/warm_brand_colors.py --force        # re-extract cached
    python scripts/warm_brand_colors.py --dry-run      # report only
    python scripts/warm_brand_colors.py --limit 25
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
    for line in env_path.read_text(encoding="utf-8").splitlines():
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
from nexus.services.brand_assets import (  # noqa: E402
    map_extracted_colors, resolve_brand_colors,
)
from nexus.services.email_palette import derive_palette  # noqa: E402

THROTTLE_SECONDS = 2.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", type=int, help="single product id")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--force", action="store_true", help="re-extract even if cached")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        where = ["source_url IS NOT NULL", "source_url <> ''"]
        params = {"lim": args.limit}
        if args.product:
            where.append("id = :pid")
            params["pid"] = args.product
        elif not args.force:
            where.append("brand_colors IS NULL")

        rows = db.execute(
            text(
                "SELECT id, user_id, name, source_url, brand_colors "
                f"FROM nexus_products WHERE {' AND '.join(where)} "
                "ORDER BY id LIMIT :lim"
            ),
            params,
        ).mappings().all()

        print(f"== warm brand colours: {len(rows)} product(s) ==\n")
        if args.dry_run:
            print("(dry run — no extraction, no writes)\n")

        resolved = skipped = failed = 0
        for r in rows:
            pid = r.get("id")
            name = (r.get("name") or "?")[:28]
            url = r.get("source_url")
            print(f"[{pid:>5}] {name:<28} {url}")

            if args.dry_run:
                skipped += 1
                continue

            prod = {"id": pid, "user_id": r.get("user_id"), "source_url": url}
            if args.force:
                db.execute(
                    text("UPDATE nexus_products SET brand_colors = NULL WHERE id = :id"),
                    {"id": pid},
                )
                db.commit()

            try:
                colors = resolve_brand_colors(db, prod, allow_extraction=True) or {}
            except Exception as e:  # resolve_brand_colors shouldn't raise, but be safe
                print(f"         [ERROR] {e}")
                failed += 1
                continue

            if colors.get("brand_color"):
                pal = derive_palette(colors.get("brand_color"), colors.get("accent_color"))
                print(
                    f"         [OK] brand={colors['brand_color']} "
                    f"accent={colors.get('accent_color') or '-'} "
                    f"-> link={pal['c_link']} counter={pal['c_counter']}"
                )
                resolved += 1
            else:
                print("         [--] no usable colours; email stays neutral slate")
                failed += 1

            time.sleep(THROTTLE_SECONDS)

        print(f"\n{'='*52}")
        print(f"resolved={resolved}  no-colour={failed}  skipped={skipped}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
