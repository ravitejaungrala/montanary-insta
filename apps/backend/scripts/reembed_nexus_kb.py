"""One-off re-embed script for `nexus_knowledge_embeddings`.

WHY THIS EXISTS
---------------
The 165 chunks for `product_id=1` (Spenzo) were originally indexed with NVIDIA NIM
`nv-embedqa-e5-v5` (1024-dim). The indexer was later swapped to Gemini at 768-dim
without re-indexing, so query vectors and stored vectors live in different
embedding spaces — cosine similarity is meaningless and the reply agent falls
through to keyword search every time.

This script re-embeds every chunk under one or more products using the current
indexer (`nexus.services.gemini.embed_text`), at the indexer's `EMBED_DIMS`.
After it runs, the stored vectors match what the live retrieval path produces,
and semantic ranking becomes possible.

This script does NOT change schema, does NOT touch any product other than those
named on the command line, and does NOT delete any rows — only `UPDATE`s the
`embedding` column.

USAGE
-----
    cd apps/backend
    # Dry run — show what would change, do not touch DB
    python scripts/reembed_nexus_kb.py --product-id 1 --dry-run

    # For real
    python scripts/reembed_nexus_kb.py --product-id 1

    # All products with any chunks
    python scripts/reembed_nexus_kb.py --all

ENV
---
    DATABASE_URL, GEMINI_API_KEY  — from apps/backend/.env (loaded normally).

SAFETY
------
- Wraps each chunk update in its own savepoint; one bad chunk doesn't abort the
  rest.
- Prints progress every 25 chunks.
- Skips chunks whose `chunk_text` is empty.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import List

# Quiet HTTP debug spam from google-genai
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for noisy in ("httpcore", "httpx", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Reconfigure stdout to UTF-8 for Windows consoles
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# Make `apps/backend` importable when invoked as `python scripts/...`
HERE = Path(__file__).resolve().parent
APP_BACKEND = HERE.parent
if str(APP_BACKEND) not in sys.path:
    sys.path.insert(0, str(APP_BACKEND))

os.environ.setdefault("NEXUS_ENABLED", "true")

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402
from nexus.services import gemini  # noqa: E402

log = logging.getLogger("reembed_nexus_kb")


def _list_products(db, product_ids: List[int] | None, all_products: bool) -> List[int]:
    if product_ids:
        return product_ids
    if all_products:
        rows = db.execute(
            text(
                "SELECT DISTINCT product_id FROM nexus_knowledge_embeddings "
                "ORDER BY product_id"
            )
        ).fetchall()
        return [int(r[0]) for r in rows if r[0] is not None]
    return []


def _reembed_product(db, product_id: int, dry_run: bool, batch_log_every: int) -> dict:
    rows = db.execute(
        text(
            "SELECT id, chunk_text "
            "FROM nexus_knowledge_embeddings "
            "WHERE product_id = :pid "
            "ORDER BY id"
        ),
        {"pid": product_id},
    ).fetchall()

    total = len(rows)
    log.info("product_id=%s: %d chunks queued", product_id, total)
    updated = 0
    skipped_empty = 0
    failed = 0
    t0 = time.perf_counter()

    for i, (row_id, chunk_text_val) in enumerate(rows, 1):
        if not chunk_text_val or not chunk_text_val.strip():
            skipped_empty += 1
            continue

        vec = gemini.embed_text(chunk_text_val, input_type="passage")
        if not vec or not any(v != 0.0 for v in vec):
            failed += 1
            log.warning(
                "product_id=%s row_id=%s: embed returned empty/zero vector — skipping",
                product_id, row_id,
            )
            continue

        if dry_run:
            updated += 1
        else:
            try:
                db.execute(
                    text(
                        "UPDATE nexus_knowledge_embeddings "
                        "SET embedding = CAST(:v AS jsonb) "
                        "WHERE id = :id"
                    ),
                    {"v": _to_json(vec), "id": row_id},
                )
                db.commit()
                updated += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                log.warning(
                    "product_id=%s row_id=%s: UPDATE failed: %s",
                    product_id, row_id, exc,
                )

        if i % batch_log_every == 0:
            elapsed = time.perf_counter() - t0
            rate = i / max(elapsed, 0.001)
            log.info(
                "  product_id=%s progress %d/%d (%.1f chunks/s)",
                product_id, i, total, rate,
            )

    elapsed = time.perf_counter() - t0
    return {
        "product_id": product_id,
        "total": total,
        "updated": updated,
        "skipped_empty": skipped_empty,
        "failed": failed,
        "elapsed_sec": round(elapsed, 1),
    }


def _to_json(vec: List[float]) -> str:
    import json as _json
    return _json.dumps(vec)


def main() -> int:
    p = argparse.ArgumentParser(description="Re-embed nexus_knowledge_embeddings chunks.")
    p.add_argument(
        "--product-id", type=int, action="append", default=[],
        help="Target this product_id (repeatable). Mutually exclusive with --all.",
    )
    p.add_argument(
        "--all", action="store_true",
        help="Re-embed every product that has any indexed chunks.",
    )
    p.add_argument("--dry-run", action="store_true", help="Do not write to DB.")
    p.add_argument("--log-every", type=int, default=25)
    args = p.parse_args()

    if not args.product_id and not args.all:
        p.error("Pass --product-id <N> (one or more) OR --all.")
    if args.product_id and args.all:
        p.error("--product-id and --all are mutually exclusive.")

    if not os.getenv("GEMINI_API_KEY"):
        log.error("GEMINI_API_KEY is not set. Cannot embed.")
        return 2

    db = SessionLocal()
    try:
        product_ids = _list_products(db, args.product_id, args.all)
        if not product_ids:
            log.warning("No products to process.")
            return 0

        log.info("Mode: %s. Targets: %s", "DRY RUN" if args.dry_run else "WRITE", product_ids)
        summaries = []
        for pid in product_ids:
            summaries.append(
                _reembed_product(db, pid, args.dry_run, args.log_every)
            )

        print("\n=== Summary ===")
        for s in summaries:
            print(
                f"  product_id={s['product_id']:>3}  total={s['total']:>4}  "
                f"updated={s['updated']:>4}  empty={s['skipped_empty']:>3}  "
                f"failed={s['failed']:>3}  {s['elapsed_sec']}s"
            )
        if args.dry_run:
            print("\n(dry run — no rows were updated)")
        return 0
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
