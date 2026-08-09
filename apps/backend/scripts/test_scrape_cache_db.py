"""Integration test for the cross-process scrape cache (nexus_scrape_cache).

Verifies the 2026-06-11 DB layer added to playwright_scraper's bundle cache:
  1. round-trip — a bundle cached by "container A" (memory wiped to simulate
     a different Lambda container) is recovered from the DB byte-equal;
  2. TTL — rows older than 15 min are treated as a miss;
  3. prune — expired rows are deleted on the next write;
  4. resilience — with the table DROPPED, both calls stay safe (no raise)
     and the memory layer still works exactly as before;
  5. no-db calls — behave exactly like the pre-existing memory-only cache.

Throwaway Docker Postgres on localhost:55432 ONLY (hard-guarded).
NO network scraping, NO Gemini, NO Apollo.

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python -X utf8 scripts/test_scrape_cache_db.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TEST_DB = "postgresql://postgres:leadbanktest@localhost:55432/postgres"
os.environ["DATABASE_URL"] = _TEST_DB
if ":55432" not in os.environ["DATABASE_URL"]:
    raise SystemExit("SAFETY: refusing to run against anything but the throwaway test DB")

from core.database import engine, SessionLocal  # noqa: E402

if ":55432" not in str(engine.url):
    raise SystemExit(f"SAFETY: engine bound to {engine.url!r}, not the test container")

from sqlalchemy import text  # noqa: E402

from nexus.services import playwright_scraper as ps  # noqa: E402
from nexus.services.playwright_scraper import (  # noqa: E402
    ScrapeBundle,
    ScrapeResult,
    cache_bundle,
    get_cached_bundle,
)

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def make_bundle(tag: str) -> ScrapeBundle:
    return ScrapeBundle(
        homepage=ScrapeResult(
            url=f"https://{tag}.example", text=f"hello {tag} " * 50,
            title=f"{tag} title", meta_description="md",
            h1s=[f"{tag} h1"], h2s=["a", "b"], char_count=550, source="jina",
        ),
        subpages=[ScrapeResult(url=f"https://{tag}.example/services",
                               text="svc text", source="jina")],
        sources={"/services": "jina", "/about": "empty"},
    )


def main() -> int:
    for _ in range(30):
        try:
            with engine.connect():
                break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("test postgres never became ready")

    # Create the table exactly as the migration does (idempotent DDL).
    from nexus.migrations import phase3
    ddl = next(m for m in phase3.MIGRATIONS if "nexus_scrape_cache" in m)
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
    db = SessionLocal()

    url = "https://cachetest.example/"

    print("\n1) cross-process round-trip (memory wiped between write and read)")
    bundle = make_bundle("alpha")
    cache_bundle(url, bundle, db=db)
    ps._SCRAPE_CACHE.clear()           # simulate a DIFFERENT Lambda container
    got = get_cached_bundle(url, db=db)
    check("DB layer returns a bundle", got is not None)
    check("homepage text survives byte-equal",
          got is not None and got.homepage.text == bundle.homepage.text)
    check("subpages + sources survive",
          got is not None and len(got.subpages) == 1
          and got.sources == bundle.sources)
    check("promoted back into the memory layer",
          ps._url_hash(url) in ps._SCRAPE_CACHE)

    print("\n2) TTL — a 16-minute-old row is a miss")
    db.execute(text("UPDATE nexus_scrape_cache SET fetched_at = NOW() - INTERVAL '16 minutes'"))
    db.commit()
    ps._SCRAPE_CACHE.clear()
    check("expired row treated as miss", get_cached_bundle(url, db=db) is None)

    print("\n3) prune — the expired row is deleted on the next write")
    cache_bundle("https://other.example/", make_bundle("beta"), db=db)
    n = db.execute(text("SELECT count(*) FROM nexus_scrape_cache")).scalar()
    check("only the fresh row remains", n == 1, f"rows={n}")

    print("\n4) resilience — table dropped, calls must not raise")
    db.execute(text("DROP TABLE nexus_scrape_cache"))
    db.commit()
    ps._SCRAPE_CACHE.clear()
    try:
        cache_bundle(url, bundle, db=db)        # DB write fails silently
        miss = get_cached_bundle("https://never-cached.example/", db=db)
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok, miss = False, exc
    check("no exception with table missing", ok, str(miss) if not ok else "")
    check("memory layer still works despite DB failure",
          get_cached_bundle(url, db=db) is not None)
    # restore for cleanliness
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)

    print("\n5) no-db calls — exact pre-existing memory behaviour")
    ps._SCRAPE_CACHE.clear()
    cache_bundle(url, bundle)                   # no db
    check("memory hit without db", get_cached_bundle(url) is not None)
    n = db.execute(text("SELECT count(*) FROM nexus_scrape_cache")).scalar()
    check("nothing written to DB without db", n == 0, f"rows={n}")

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
