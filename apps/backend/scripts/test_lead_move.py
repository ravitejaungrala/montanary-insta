"""Integration test for the campaign-move invariant in _attach_workspace_lead.

INVARIANT: a lead MOVED to a new campaign behaves exactly like a freshly
attached lead — created_at bumps (so the New-Run UI's latest_run fence and
every run fence see it) and the old campaign's intent verdict is cleared
(this run's Agent #10 verdict decides, not a stale one). SAME-campaign
re-discovery must keep both untouched.

Throwaway Docker Postgres on localhost:55432 ONLY (hard-guarded).
NO Apollo, NO Gemini, NO real database.

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python scripts/test_lead_move.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

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
from nexus.models_phase3 import NexusGlobalLead, NexusLead  # noqa: E402
from nexus.services.lead_discovery import _attach_workspace_lead  # noqa: E402

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
    for tname in ("users", "nexus_workspaces", "nexus_global_leads", "nexus_leads"):
        src = Base.metadata.tables[tname]
        ddl = str(CreateTable(src).compile(engine))
        lines = [l for l in ddl.splitlines() if "FOREIGN KEY" not in l]
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(re.sub(r",\s*\n\)", "\n)", "\n".join(lines)))
        except Exception as e:  # noqa: BLE001 — idempotent vs a shared container
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
    gl = make(NexusGlobalLead, email="mover@test.io", company_name="Validity Inc.")

    OLD = datetime.utcnow() - timedelta(days=3)
    lead = make(NexusLead, workspace_id=ws.id, campaign_id=101, product_id=7,
                global_lead_id=gl.id, icp_score=100,
                signals={"intent": {"status": "accepted", "score": 100}},
                created_at=OLD)

    print("\n1) re-discovered by a NEW campaign (the campaign-61 scenario)")
    run_start = datetime.utcnow() - timedelta(seconds=5)
    row = _attach_workspace_lead(
        db, workspace_id=ws.id, campaign_id=202, product_id=7,
        global_lead=gl, icp_score=80, signals={"discovered_by": "apollo"},
    )
    check("same row reused (no duplicate)", row.id == lead.id)
    check("moved to the new campaign", row.campaign_id == 202)
    check("created_at bumped -> passes the New-Run UI run fence",
          row.created_at >= run_start, f"created_at={row.created_at}")
    check("old campaign's verdict cleared (fresh judgment this run)",
          (row.signals or {}).get("intent") is None)
    check("non-intent signals still merged",
          (row.signals or {}).get("discovered_by") == "apollo")

    print("\n2) re-discovered by the SAME campaign — judged verdict must stand")
    db.execute(sa.text("UPDATE nexus_leads SET signals = '{\"intent\": {\"status\": \"rejected\"}}'::jsonb, created_at = :old WHERE id = :id"),
               {"old": OLD, "id": row.id})
    db.commit()
    db.expire_all()
    row2 = _attach_workspace_lead(
        db, workspace_id=ws.id, campaign_id=202, product_id=7,
        global_lead=gl, icp_score=90, signals={"intent": {"status": "pending"}},
    )
    check("same row, same campaign", row2.id == lead.id and row2.campaign_id == 202)
    check("created_at NOT bumped on same-campaign touch",
          row2.created_at.replace(microsecond=0) == OLD.replace(microsecond=0),
          f"created_at={row2.created_at}")
    check("sweep's verdict NOT overwritten",
          (row2.signals or {}).get("intent", {}).get("status") == "rejected")

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
