"""Warm per-product email images (logo + hero) into S3 + nexus_brand_assets.

IDEMPOTENT. A slot already `ready` and fresher than --max-age is skipped with
no network call, so re-running is cheap and safe. Only --force re-extracts.
Manual uploads (source='uploaded') are never overwritten.

S3 layout — one folder per product, one object per slot:

    nexus/brand/{workspace_id}/{product_id}/logo_{sha8}.png
    nexus/brand/{workspace_id}/{product_id}/hero_{sha8}.jpg

{sha8} hashes the NORMALIZED bytes, so an unchanged site re-uploads to the
same key (a no-op overwrite, never a second object). When a site genuinely
changes its image the new key is written and the superseded object for that
product+slot is deleted, so the bucket holds exactly one object per slot.

Usage:
    python scripts/warm_brand_images.py --dry-run
    python scripts/warm_brand_images.py --product 1
    python scripts/warm_brand_images.py --limit 20
    python scripts/warm_brand_images.py --product 1 --force
    python scripts/warm_brand_images.py --audit     # report S3 vs DB only
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

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402
from nexus.services.brand_assets import ensure_product_images  # noqa: E402

THROTTLE_SECONDS = 2.0


def audit(db, clean: bool = False) -> int:
    """Compare what S3 holds against what the DB claims. Surfaces orphans —
    objects with no row — which are the real sign of duplication.

    With `clean`, orphans are deleted. The DB is authoritative: an object with
    no row is unreachable by the renderer, so it is dead weight.
    """
    from core.s3_utils import get_s3_client, S3_BUCKET_NAME
    s3 = get_s3_client()
    if not s3:
        print("S3 not configured")
        return 1

    keys = []
    token = None
    while True:
        kw = {"Bucket": S3_BUCKET_NAME, "Prefix": "nexus/brand/"}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        keys.extend([(o["Key"], o["Size"]) for o in r.get("Contents", []) or []])
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")

    rows = db.execute(
        text("SELECT product_id, asset_kind, s3_key, status FROM nexus_brand_assets")
    ).mappings().all()
    known = {r["s3_key"] for r in rows if r["s3_key"]}

    print(f"S3 objects under nexus/brand/ : {len(keys)}")
    print(f"DB rows in nexus_brand_assets : {len(rows)}")
    total = sum(s for _, s in keys)
    print(f"total bytes                   : {total:,}\n")

    for k, s in sorted(keys):
        print(f"  {'ok ' if k in known else 'ORPHAN'} {s:>8,}B  {k}")
    orphans = [k for k, _ in keys if k not in known]
    missing = [r["s3_key"] for r in rows if r["s3_key"] and r["s3_key"] not in
               {k for k, _ in keys}]
    print(f"\norphaned objects (no DB row): {len(orphans)}")
    print(f"DB rows with missing object : {len(missing)}")
    for m in missing:
        print(f"  MISSING {m}")

    if orphans and clean:
        print("\ndeleting orphans…")
        for k in orphans:
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=k)
            print(f"  deleted {k}")
    elif orphans:
        print("\n(re-run with --clean-orphans to delete them)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", type=int)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--clean-orphans", action="store_true",
                    help="with --audit: delete S3 objects that have no DB row")
    ap.add_argument("--max-age", type=int, default=90, help="days before refresh")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.audit:
            return audit(db, clean=args.clean_orphans)

        where = ["source_url IS NOT NULL", "source_url <> ''"]
        params = {"lim": args.limit}
        if args.product:
            where.append("id = :pid")
            params["pid"] = args.product

        rows = db.execute(
            text(
                "SELECT id, workspace_id, name, source_url FROM nexus_products "
                f"WHERE {' AND '.join(where)} ORDER BY id LIMIT :lim"
            ),
            params,
        ).mappings().all()

        print(f"== warm brand images: {len(rows)} product(s) ==")
        print(f"   force={args.force}  max_age={args.max_age}d\n")

        did = 0
        for r in rows:
            prod = dict(r)
            print(f"[{prod['id']:>5}] ws={prod['workspace_id']:<4} "
                  f"{(prod['name'] or '?')[:24]:<24} {prod['source_url']}")
            if args.dry_run:
                print("         (dry run)")
                continue

            rep = ensure_product_images(db, prod, force=args.force,
                                        max_age_days=args.max_age)
            if rep.get("error"):
                print(f"         [ERROR] {rep['error']}")
            for s in rep.get("skipped", []):
                print(f"         [skip] {s}")
            worked = False
            for slot in ("logo", "hero"):
                info = rep.get(slot)
                if not info:
                    continue
                worked = True
                if info.get("ok"):
                    extra = ""
                    if info.get("plated"):
                        extra += " plated"
                    if info.get("superseded_removed"):
                        extra += f" (-{info['superseded_removed']} old)"
                    print(f"         [{slot}] {info['dims']} via {info['source']}{extra}")
                    print(f"                {info['key']}")
                else:
                    print(f"         [{slot}] rejected: {info.get('error')}")
                    for a in (info.get("attempts") or [])[:3]:
                        print(f"                  - {a.get('source')}: "
                              f"{a.get('reject') or a.get('accept')}")
            if worked:
                did += 1
                time.sleep(THROTTLE_SECONDS)

        print(f"\n{'='*56}\nextracted for {did} product(s); "
              f"{len(rows)-did} needed nothing")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
