"""Integration test for the PARALLEL ATTACH in autonomous_discover (2026-06-11).

The per-lead pipeline (upsert -> dedup -> attach) now runs in worker threads
with per-lead sessions, in cap-safe chunks. Must behave exactly like the old
serial loop, just concurrent:

  1. all candidates attach, stamped intent 'pending' (defer_enrollment=True);
  2. genuinely CONCURRENT (overlapping workers, wall-clock << serial sum);
  3. max_leads cap is never overshot (chunks sized to the remaining budget);
  4. product-scope dedup still skips already-attached leads silently;
  5. bad candidates (no email) skip without sinking their chunk.

Throwaway Docker Postgres on localhost:55432 ONLY (hard-guarded).
NO Apollo, NO Gemini.

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python -X utf8 scripts/test_parallel_attach.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
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
from nexus.models_phase3 import NexusLead  # noqa: E402
from nexus.services import lead_discovery  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def payloads(n, tag, bad_at=None):
    out = []
    for i in range(n):
        out.append({
            "email": None if i == bad_at else f"{tag}{i}@parallel-attach.test",
            "first_name": f"F{i}", "last_name": f"L{i}", "role": "CTO",
            "company_name": f"Co {tag}{i}", "company_domain": f"co{tag}{i}.test",
            "source": "apollo", "raw": {},
        })
    return out


async def drive(db, *, ws, campaign, product, payload_list, max_leads):
    """Run autonomous_discover with _run_apollo stubbed; return 'lead' events."""
    async def fake_run_apollo(*a, **k):
        return payload_list, []
    orig = lead_discovery._run_apollo
    lead_discovery._run_apollo = fake_run_apollo
    try:
        events = []
        async for chunk in lead_discovery.autonomous_discover(
            db, workspace_id=ws, icp={}, campaign_id=campaign,
            product_id=product, max_leads=max_leads, min_icp_score=0,
            defer_enrollment=True,
        ):
            ev = json.loads(chunk.split("data: ", 1)[1])
            if ev.get("type") == "lead":
                events.append(ev)
        return events
    finally:
        lead_discovery._run_apollo = orig


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
                  "nexus_leads", "nexus_lead_sequences", "nexus_sequences",
                  "nexus_campaigns"):
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
    from nexus.models_phase3 import NexusCampaign
    camp_a = make(NexusCampaign, workspace_id=ws.id, product_id=71,
                  name="alpha #1", status="active", icp={})
    camp_b = make(NexusCampaign, workspace_id=ws.id, product_id=71,
                  name="alpha #2", status="active", icp={})

    # Instrument the upsert to measure REAL concurrency: each worker sleeps
    # 0.3s inside the wrapper, tracking the max number in flight at once.
    state = {"cur": 0, "peak": 0}
    lock = threading.Lock()
    orig_upsert = lead_discovery._upsert_global_lead

    def slow_upsert(sess, payload):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        try:
            time.sleep(0.3)
            return orig_upsert(sess, payload)
        finally:
            with lock:
                state["cur"] -= 1

    lead_discovery._upsert_global_lead = slow_upsert
    try:
        print("\n1) 10 candidates, cap 20 — all attach, pending, CONCURRENTLY")
        t0 = time.monotonic()
        evs = asyncio.run(drive(db, ws=ws.id, campaign=camp_a.id, product=71,
                                payload_list=payloads(10, "a"), max_leads=20))
        dt = time.monotonic() - t0
        check("10 'lead' events emitted", len(evs) == 10, f"got {len(evs)}")
        rows = db.query(NexusLead).filter(NexusLead.campaign_id == camp_a.id).all()
        check("10 rows attached", len(rows) == 10, f"rows={len(rows)}")
        check("all stamped intent=pending",
              all((r.signals or {}).get("intent", {}).get("status") == "pending"
                  for r in rows))
        check("workers genuinely overlapped", state["peak"] >= 3,
              f"peak concurrency={state['peak']}")
        check("wall-clock ~ parallel, not 10x0.3s serial", dt < 10 * 0.3,
              f"elapsed={dt:.2f}s")

        print("\n2) cap safety — 10 fresh candidates, max_leads=4")
        evs = asyncio.run(drive(db, ws=ws.id, campaign=902, product=72,
                                payload_list=payloads(10, "b"), max_leads=4))
        n902 = db.query(NexusLead).filter(NexusLead.campaign_id == 902).count()
        check("exactly 4 attached (no overshoot)",
              len(evs) == 4 and n902 == 4, f"events={len(evs)} rows={n902}")

        print("\n3) product dedup — same candidates re-discovered on a NEW campaign")
        evs = asyncio.run(drive(
            db, ws=ws.id, campaign=camp_b.id, product=71,
            # simulate the pre-reveal hook's verdict stamp (Agent #10 score)
            payload_list=[{**p, "_agent10_score": 83} for p in payloads(10, "a")],
            max_leads=20))
        rows_b = db.query(NexusLead).filter(NexusLead.campaign_id == camp_b.id).all()
        check("0 'lead' events — duplicates never count as found",
              len(evs) == 0, f"events={len(evs)}")
        # 2026-06-11: duplicates now SURFACE as marker rows on the new
        # campaign ("Already in <campaign>" badge) instead of vanishing.
        check("10 duplicate-marker rows created", len(rows_b) == 10,
              f"rows={len(rows_b)}")
        check("markers are rejected + drop_reason=duplicate (badge data)",
              all((r.signals or {}).get("intent", {}).get("status") == "rejected"
                  and (r.signals or {}).get("intent", {}).get("drop_reason") == "duplicate"
                  for r in rows_b))
        check("markers carry Agent #10's re-vet score (MATCH pill), not the "
              "firmographic 100",
              all(r.icp_score == 83
                  and (r.signals or {}).get("intent", {}).get("score") == 83
                  for r in rows_b),
              str(rows_b[0].icp_score if rows_b else None))
        check("markers carry the ORIGINAL campaign's name for the badge",
              all((r.signals or {}).get("intent", {}).get("dup_campaign_name")
                  == camp_a.name for r in rows_b),
              str((rows_b[0].signals or {}).get("intent", {}).get("dup_campaign_name")
                  if rows_b else None))
        check("markers carry the original contact DATE for the badge",
              all((r.signals or {}).get("intent", {}).get("dup_contacted_at", "")
                  .startswith(str(datetime.utcnow().date())) for r in rows_b),
              str((rows_b[0].signals or {}).get("intent", {}).get("dup_contacted_at")
                  if rows_b else None))
        from nexus.models_phase4 import NexusLeadSequence
        n_seq = db.query(NexusLeadSequence).filter(
            NexusLeadSequence.campaign_id == camp_b.id).count()
        check("NO outreach: zero sequence enrollments for markers", n_seq == 0,
              f"enrollments={n_seq}")
        # idempotent: re-discovering AGAIN must not duplicate the markers
        evs = asyncio.run(drive(db, ws=ws.id, campaign=camp_b.id, product=71,
                                payload_list=payloads(10, "a"), max_leads=20))
        n2 = db.query(NexusLead).filter(NexusLead.campaign_id == camp_b.id).count()
        check("re-run idempotent (still 10 markers, 0 events)",
              len(evs) == 0 and n2 == 10, f"events={len(evs)} rows={n2}")
        # REGRESSION (review finding): a THIRD campaign re-discovering the
        # same people must badge them with the ORIGINAL campaign (camp_a),
        # never with camp_b's marker rows (where nothing was ever sent).
        camp_c = make(NexusCampaign, workspace_id=ws.id, product_id=71,
                      name="alpha #3", status="active", icp={})
        evs = asyncio.run(drive(db, ws=ws.id, campaign=camp_c.id, product=71,
                                payload_list=payloads(10, "a"), max_leads=20))
        rows_c = db.query(NexusLead).filter(NexusLead.campaign_id == camp_c.id).all()
        check("3rd-campaign markers still name the ORIGINAL campaign, not a marker",
              len(rows_c) == 10 and all(
                  (r.signals or {}).get("intent", {}).get("dup_campaign_name")
                  == camp_a.name for r in rows_c),
              str((rows_c[0].signals or {}).get("intent", {}).get("dup_campaign_name")
                  if rows_c else None))

        print("\n4) bad candidate (no email) skips without sinking its chunk")
        evs = asyncio.run(drive(db, ws=ws.id, campaign=904, product=74,
                                payload_list=payloads(6, "c", bad_at=2), max_leads=20))
        check("5 of 6 attach, bad one silently skipped", len(evs) == 5,
              f"events={len(evs)}")
    finally:
        lead_discovery._upsert_global_lead = orig_upsert

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
