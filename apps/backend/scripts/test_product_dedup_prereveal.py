"""Integration test for the pre-reveal product dedup (tier 4, 2026-06-11).

Replays campaign 67's exact failure: a person already attached to the
product is re-fetched from Apollo as a SEARCH row — email locked (None),
no linkedin_url, company_domain hidden. Tiers 1-3 are all blind on such a
row, so the duplicate sailed through to Agent #10 (re-research) and the
paid reveal, only to be dropped at attach -> "0 of 10 found".

Asserts:
  1. the campaign-67 row shape IS dropped now (tier 4: name + company name);
  2. a NEW person at the same company is kept;
  3. the same name at a DIFFERENT company is kept (no over-matching);
  4. candidates with missing names/company are kept (tier 4 never guesses);
  5. rows that tiers 1-3 already caught are still caught (no regression);
  6. other products' leads do NOT block this product (scope unchanged).

Throwaway Docker Postgres on localhost:55432 ONLY (hard-guarded).
NO Apollo, NO Gemini, NO credits.

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python -X utf8 scripts/test_product_dedup_prereveal.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TEST_DB = "postgresql://postgres:leadbanktest@localhost:55432/postgres"
os.environ["DATABASE_URL"] = _TEST_DB
if ":55432" not in os.environ["DATABASE_URL"]:
    raise SystemExit("SAFETY: refusing to run against anything but the throwaway test DB")

from core.database import Base, engine, SessionLocal  # noqa: E402

if ":55432" not in str(engine.url):
    raise SystemExit(f"SAFETY: engine bound to {engine.url!r}, not the test container")

import models  # noqa: E402,F401
from nexus import models as nexus_models  # noqa: E402
from nexus.models_phase3 import NexusCampaign, NexusGlobalLead, NexusLead  # noqa: E402
from nexus.services.discovery_apollo import _filter_against_product_db  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    for _ in range(30):
        try:
            with engine.connect():
                break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("test postgres never became ready")

    import re
    from sqlalchemy.schema import CreateTable
    for tname in ("users", "nexus_workspaces", "nexus_global_leads",
                  "nexus_leads", "nexus_campaigns"):
        src = Base.metadata.tables[tname]
        ddl = str(CreateTable(src).compile(engine))
        lines = [l for l in ddl.splitlines() if "FOREIGN KEY" not in l]
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(re.sub(r",\s*\n\)", "\n)", "\n".join(lines)))
        except Exception as e:  # noqa: BLE001
            if "already exists" not in str(e):
                raise
    db = SessionLocal()

    import sqlalchemy as sa

    def make(Model, **over):
        kw = dict(over)
        for col in Model.__table__.columns:
            if (col.name in kw or col.primary_key or col.nullable
                    or col.default is not None or col.server_default is not None):
                continue
            t = col.type
            if isinstance(t, (sa.String, sa.Text)):
                kw[col.name] = f"x{time.monotonic_ns()}"
            elif isinstance(t, (sa.Integer, sa.BigInteger)):
                kw[col.name] = 0
            elif isinstance(t, sa.DateTime):
                kw[col.name] = datetime.utcnow()
            elif isinstance(t, sa.Boolean):
                kw[col.name] = False
        obj = Model(**kw)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    user = make(models.User)
    ws = make(nexus_models.NexusWorkspace, owner_id=user.id)

    PRODUCT, OTHER_PRODUCT = 510, 511
    camp = make(NexusCampaign, workspace_id=ws.id, product_id=PRODUCT,
                name="dedup-test", status="active", icp={})
    camp_other = make(NexusCampaign, workspace_id=ws.id, product_id=OTHER_PRODUCT,
                      name="other-product", status="active", icp={})

    # The campaign-65/66 state: lead attached to THIS product, with the
    # post-reveal data (email + backfilled domain) stored on the global lead.
    gl = make(NexusGlobalLead, email="gohar@stcbank.sa", first_name="Gohar",
              last_name="Shaikh", company_name="STC Bank",
              company_domain="stcbank.sa")
    make(NexusLead, workspace_id=ws.id, campaign_id=camp.id, product_id=PRODUCT,
         global_lead_id=gl.id, icp_score=80, signals={})

    # A lead on a DIFFERENT product — must never block this product's runs.
    gl2 = make(NexusGlobalLead, email="zara@retailco.sa", first_name="Zara",
               last_name="Khan", company_name="RetailCo",
               company_domain="retailco.sa")
    make(NexusLead, workspace_id=ws.id, campaign_id=camp_other.id,
         product_id=OTHER_PRODUCT, global_lead_id=gl2.id, icp_score=80, signals={})

    def search_row(fn, ln, company, **over):
        """A PRE-REVEAL Apollo search row: email locked, no linkedin,
        domain hidden — exactly what campaign 67's candidates looked like."""
        row = {"first_name": fn, "last_name": ln, "company_name": company,
               "email": None, "linkedin_url": None, "company_domain": None}
        row.update(over)
        return row

    print("\n1) campaign-67 shape: already-pitched person on a bare search row")
    cands = [search_row("Gohar", "Shaikh", "STC Bank")]
    out = _filter_against_product_db(db, cands, PRODUCT)
    check("duplicate DROPPED pre-reveal (tier 4 fires)", out == [], str(out))

    print("\n2) new person at the same company is kept")
    out = _filter_against_product_db(
        db, [search_row("Sander", "Aardoom", "STC Bank")], PRODUCT)
    check("new colleague kept", len(out) == 1)

    print("\n3) same name at a DIFFERENT company is kept")
    out = _filter_against_product_db(
        db, [search_row("Gohar", "Shaikh", "Riyad Capital")], PRODUCT)
    check("same-name different-company kept", len(out) == 1)

    print("\n4) tier 4 never guesses on missing fields")
    out = _filter_against_product_db(
        db, [search_row("", "", ""), search_row("Gohar", "Shaikh", "")], PRODUCT)
    check("blank-field candidates kept (no false drops)", len(out) == 2)

    print("\n5) tiers 1-3 still catch what they always caught")
    out = _filter_against_product_db(
        db, [search_row("X", "Y", "Z", email="gohar@stcbank.sa")], PRODUCT)
    check("tier 1 email match still drops", out == [])
    out = _filter_against_product_db(
        db, [search_row("Gohar", "Shaikh", "Different Name Ltd",
                        company_domain="stcbank.sa")], PRODUCT)
    check("tier 3 domain-triplet still drops", out == [])

    print("\n6) duplicate collection for the known-people top-up")
    pool = []
    out = _filter_against_product_db(
        db, [search_row("Gohar", "Shaikh", "STC Bank"),
             search_row("Brand", "New", "Fresh Co")],
        PRODUCT, collect_duplicates=pool)
    check("fresh candidate kept, dup collected separately",
          len(out) == 1 and len(pool) == 1,
          f"kept={len(out)} collected={len(pool)}")
    check("collected dup carries the KNOWN email (zero-credit re-use)",
          pool and pool[0].get("email") == "gohar@stcbank.sa"
          and pool[0].get("email_locked") is False
          and pool[0].get("_dup_known") is True,
          str(pool[0].get("email") if pool else None))
    check("collect=None (default) keeps the old behaviour",
          _filter_against_product_db(
              db, [search_row("Gohar", "Shaikh", "STC Bank")], PRODUCT) == [])

    print("\n7) output projection preserves the marker carry-keys")
    # REGRESSION (campaign 71): _strip_internal is an allow-list — it ate
    # _agent10_score, so every marker stored score 0 and the MATCH pill
    # showed a dash instead of Agent #10's re-vet verdict.
    from nexus.services.discovery_apollo import _strip_internal
    projected = _strip_internal({
        "email": "x@y.z", "first_name": "A", "last_name": "B",
        "company_name": "C", "_agent10_score": 83, "_dup_known": True,
    })
    check("_agent10_score survives the projection",
          projected.get("_agent10_score") == 83, str(projected.get("_agent10_score")))
    check("_dup_known survives the projection",
          projected.get("_dup_known") is True)

    print("\n8) product scope unchanged")
    out = _filter_against_product_db(
        db, [search_row("Zara", "Khan", "RetailCo")], PRODUCT)
    check("other product's lead does NOT block this product", len(out) == 1)
    out = _filter_against_product_db(
        db, [search_row("Gohar", "Shaikh", "STC Bank")], None)
    check("product_id=None -> filter inert (unchanged)", len(out) == 1)

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
