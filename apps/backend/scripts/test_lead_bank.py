"""Integration test for the product-level lead bank (bank / reclaim).

Exercises _bank_surplus_pending, _bank_excess_accepted and _reclaim_banked
from nexus.services.discover_for_campaign against a REAL Postgres — but ONLY
the throwaway Docker container on localhost:55432 (hard-guarded below).
NO Apollo calls, NO Gemini calls, NO real database. Fixture data only.

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python scripts/test_lead_bank.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── HARD SAFETY GUARD ────────────────────────────────────────────────────────
# Force the throwaway container BEFORE any backend import. If anything else
# ends up in DATABASE_URL, refuse to run.
_TEST_DB = "postgresql://postgres:leadbanktest@localhost:55432/postgres"
os.environ["DATABASE_URL"] = _TEST_DB
assert os.environ["DATABASE_URL"] == _TEST_DB
if ":55432" not in os.environ["DATABASE_URL"] or "localhost" not in os.environ["DATABASE_URL"]:
    raise SystemExit("SAFETY: refusing to run against anything but the throwaway test DB")

# Imports AFTER the env guard (core.database builds its engine from it).
from core.database import Base, engine, SessionLocal  # noqa: E402

if ":55432" not in str(engine.url):  # second guard, post-import
    raise SystemExit(f"SAFETY: engine bound to {engine.url!r}, not the test container")

import models  # noqa: E402,F401  — core `users` table (FK parent)
from nexus import models as nexus_models  # noqa: E402,F401 — nexus_workspaces
from nexus import models_phase2  # noqa: E402,F401
from nexus import models_phase3  # noqa: E402
from nexus.models_phase3 import NexusCampaign, NexusGlobalLead, NexusLead  # noqa: E402
from nexus.services.discover_for_campaign import (  # noqa: E402
    _bank_excess_accepted,
    _bank_surplus_pending,
    _count_accepted,
    _reclaim_banked,
)

FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def make(db, Model, **over):
    """Create a row, auto-filling NOT NULL columns that have no default."""
    import sqlalchemy as sa
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


def lead_status(db, lead_id: int):
    db.expire_all()
    l = db.query(NexusLead).filter(NexusLead.id == lead_id).one()
    blk = (l.signals or {}).get("intent") or {}
    return blk.get("status"), blk.get("drop_reason"), blk.get("prev_status")


def main() -> int:
    # Wait for the container to accept connections (fresh start).
    for i in range(30):
        try:
            with engine.connect():
                break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("test postgres never became ready")

    # Create ONLY the tables this test needs, with FOREIGN KEY clauses
    # stripped: the core schema has a users<->teams FK cycle that blocks
    # naive per-table creation, and FK *enforcement* isn't what we're
    # testing. Columns + unique constraints (the dup-protection we DO test)
    # are kept exactly as the models define them.
    import re
    from sqlalchemy.schema import CreateTable
    with engine.begin() as conn:
        for tname in ("users", "nexus_workspaces", "nexus_campaigns",
                      "nexus_global_leads", "nexus_leads"):
            src = Base.metadata.tables[tname]
            ddl = str(CreateTable(src).compile(engine))
            lines = [l for l in ddl.splitlines() if "FOREIGN KEY" not in l]
            ddl = re.sub(r",\s*\n\)", "\n)", "\n".join(lines))
            conn.exec_driver_sql(ddl)
    db = SessionLocal()

    user = make(db, models.User)
    ws = make(db, nexus_models.NexusWorkspace, owner_id=user.id)
    c1 = make(db, NexusCampaign, workspace_id=ws.id, product_id=77,
              name="C1", status="active", icp={})
    c2 = make(db, NexusCampaign, workspace_id=ws.id, product_id=77,
              name="C2", status="active", icp={})
    c3 = make(db, NexusCampaign, workspace_id=ws.id, product_id=88,
              name="C3-other-product", status="active", icp={})

    run_start = datetime.utcnow() - timedelta(seconds=60)

    def add_lead(camp, score, status, n=[0]):
        n[0] += 1
        gl = make(db, NexusGlobalLead, email=f"p{n[0]}@test.io",
                  company_domain=f"co{n[0]}.io", company_name=f"Co {n[0]}")
        return make(db, NexusLead, workspace_id=ws.id, campaign_id=camp.id,
                    product_id=camp.product_id, global_lead_id=gl.id,
                    icp_score=score,
                    signals={"intent": {"status": status, "score": score}})

    # RUN-A fixtures on C1: 17 accepted (scores 70..86), 3 pending.
    accepted = [add_lead(c1, 70 + i, "accepted") for i in range(17)]
    pending = [add_lead(c1, 0, "pending") for _ in range(3)]
    # Different product (C3): one banked lead that must NEVER be touched.
    other = add_lead(c3, 90, "rejected")
    sig = dict(other.signals); sig["intent"] = {**sig["intent"], "drop_reason": "banked",
                                                "prev_status": "accepted"}
    other.signals = sig; db.commit()

    print("\n1) target met (15): bank the 3 un-scored leftovers")
    banked_p = _bank_surplus_pending(db, c1)
    check("3 pending banked", banked_p == 3, f"banked={banked_p}")
    st, dr, _ = lead_status(db, pending[0].id)
    check("banked pending hidden like rejected", st == "rejected" and dr == "banked",
          f"status={st}, drop_reason={dr}")

    print("\n2) 17 accepted > 15 asked: bank the 2 worst, show exactly 15")
    banked_e = _bank_excess_accepted(db, c1, run_start, 15)
    check("2 excess accepted banked", banked_e == 2, f"banked={banked_e}")
    check("UI now shows exactly 15", _count_accepted(db, c1.id) == 15,
          f"accepted={_count_accepted(db, c1.id)}")
    st, dr, prev = lead_status(db, accepted[0].id)  # score 70 = worst -> banked
    check("worst-score lead was the one banked",
          st == "rejected" and dr == "banked" and prev == "accepted",
          f"status={st}, drop_reason={dr}, prev={prev}")
    st, _, _ = lead_status(db, accepted[16].id)  # score 86 = best -> stays
    check("best lead still accepted", st == "accepted", f"status={st}")

    print("\n3) next run, same campaign: reclaim instead of paying Apollo")
    acc, pend = _reclaim_banked(db, c1, 15)
    check("2 accepted + 3 pending reclaimed", (acc, pend) == (2, 3),
          f"got ({acc}, {pend})")
    check("accepted count back to 17", _count_accepted(db, c1.id) == 17,
          f"accepted={_count_accepted(db, c1.id)}")
    st, dr, prev = lead_status(db, accepted[0].id)
    check("restored without re-scoring", st == "accepted" and dr is None and prev is None,
          f"status={st}, drop_reason={dr}, prev={prev}")
    st, dr, _ = lead_status(db, pending[0].id)
    check("unscored leftover back to pending", st == "pending" and dr is None,
          f"status={st}")
    db.refresh(c1)
    check("campaign flagged for the scorer", bool((c1.icp or {}).get("intent_pending")))

    print("\n4) re-bank everything, then a SIBLING campaign (same product) reclaims")
    _bank_surplus_pending(db, c1)
    _bank_excess_accepted(db, c1, run_start, 15)
    acc, pend = _reclaim_banked(db, c2, 1)  # tiny target: caps must bind
    check("accepted reclaim capped at target=1", acc == 1, f"acc={acc}")
    check("pending reclaim capped at 2x(1-1)=0", pend == 0, f"pend={pend}")
    check("C2 gained exactly 1 accepted", _count_accepted(db, c2.id) == 1,
          f"accepted={_count_accepted(db, c2.id)}")

    acc, pend = _reclaim_banked(db, c2, 15)  # now take the rest
    check("remaining 1 accepted + 3 pending cloned over", (acc, pend) == (1, 3),
          f"got ({acc}, {pend})")
    check("C2 accepted total = 2", _count_accepted(db, c2.id) == 2,
          f"accepted={_count_accepted(db, c2.id)}")

    print("\n5) bank is empty now: nothing reclaimable, no double-spend")
    acc, pend = _reclaim_banked(db, c2, 15)
    check("second reclaim finds nothing", (acc, pend) == (0, 0), f"got ({acc}, {pend})")
    st, dr, _ = lead_status(db, other.id)
    check("other product's banked lead untouched", st == "rejected" and dr == "banked",
          f"status={st}, drop_reason={dr}")
    n_c2 = db.query(NexusLead).filter(NexusLead.campaign_id == c2.id).count()
    check("no duplicate rows on C2", n_c2 == 5, f"rows={n_c2}")

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
