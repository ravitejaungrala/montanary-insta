"""Behavior test for CONCURRENT revenue windows in discover_for_campaign.

Replays the campaign-62 shape — two non-adjacent revenue bands → two Apollo
windows — with `autonomous_discover` stubbed (no Apollo, no Gemini). Asserts:

  1. both windows ran (2 discover calls, one per band);
  2. they ran CONCURRENTLY (wall-clock ≈ one window, intervals overlap) —
     the old sequential loop cost the SUM of the windows;
  3. each window got its OWN DB session (never the caller's session);
  4. bookkeeping matches the sequential behavior: totals, by_source merge,
     per-window cursor advance, persisted campaign cursor.

Throwaway Docker Postgres on localhost:55432 ONLY (hard-guarded).

Run from apps/backend:
    docker run -d --rm --name leadbank_test_pg -p 55432:5432 \
        -e POSTGRES_PASSWORD=leadbanktest postgres:16-alpine
    python scripts/test_parallel_windows.py
    docker stop leadbank_test_pg
"""
from __future__ import annotations

import asyncio
import json
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
from nexus.models_phase3 import NexusCampaign  # noqa: E402
from nexus.services import discover_for_campaign as dfc  # noqa: E402
from nexus.services import intent_sweep, lead_discovery  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


# Simulated Agent #10 wall-clock per window. Two windows sequentially would
# cost 2x this; concurrently ~1x.
_WINDOW_SECONDS = 1.0

# Per-call record: {"revenue": ..., "db": <session>, "t0": ..., "t1": ...}
CALLS: list = []


async def _fake_autonomous_discover(db, *, icp, apollo_stats=None, **kwargs):
    """Stands in for the Apollo search + Agent #10 wave + reveal. Yields one
    attached lead per window after a fixed 'research' sleep."""
    rec = {"revenue": (icp or {}).get("revenue_range"), "db": db,
           "t0": time.monotonic()}
    await asyncio.sleep(_WINDOW_SECONDS)  # the scoring wave
    rec["t1"] = time.monotonic()
    CALLS.append(rec)
    if apollo_stats is not None:
        apollo_stats["pages_walked"] = 1
        apollo_stats["broader_pages_walked"] = 0
        apollo_stats["t_apollo_search"] = 0.01
        apollo_stats["t_bulk_match"] = 0.01
    yield "data: " + json.dumps({"type": "lead", "source": "apollo"}) + "\n\n"
    yield "data: " + json.dumps({"type": "done"}) + "\n\n"


async def _fake_process_pending_intent(db, **kwargs):
    return {"companies_scored": 0}


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
                  "nexus_leads", "nexus_campaigns", "nexus_sequences",
                  "nexus_lead_sequences"):
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
    campaign = make(
        NexusCampaign, workspace_id=ws.id, name="parallel-windows-test",
        status="active",
        # Two NON-adjacent bands → two Apollo windows (the campaign-62 shape).
        icp={"person_titles": ["CTO"],
             "revenue_range": ["$1M – $10M", "$50M – $200M"]},
    )

    # Stub the expensive externals. The pre-reveal hook never fires (the fake
    # discover doesn't call it), and residual scoring is a no-op.
    lead_discovery.autonomous_discover = _fake_autonomous_discover
    intent_sweep.process_pending_intent = _fake_process_pending_intent
    dfc.lead_discovery.autonomous_discover = _fake_autonomous_discover

    t0 = time.monotonic()
    result = asyncio.run(dfc.discover_for_campaign(db, campaign, max_leads=5))
    elapsed = time.monotonic() - t0

    print("\n1) both windows ran, one per revenue band")
    check("two discover calls", len(CALLS) == 2, f"calls={len(CALLS)}")
    bands = sorted(json.dumps(c["revenue"]) for c in CALLS)
    check("distinct bands", len(set(bands)) == 2, f"bands={bands}")

    print("\n2) windows ran CONCURRENTLY, not back-to-back")
    if len(CALLS) == 2:
        a, b = CALLS
        overlap = min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])
        check("scoring intervals overlap", overlap > 0.5 * _WINDOW_SECONDS,
              f"overlap={overlap:.2f}s of {_WINDOW_SECONDS:.2f}s")
    check("total wall-clock ~= ONE window (was: sum of both)",
          elapsed < 1.7 * _WINDOW_SECONDS, f"elapsed={elapsed:.2f}s")

    print("\n3) session isolation")
    check("each window used its OWN session, not the caller's",
          all(c["db"] is not db for c in CALLS)
          and len(CALLS) == 2 and CALLS[0]["db"] is not CALLS[1]["db"])

    print("\n4) bookkeeping parity with the sequential loop")
    check("revealed = 1 lead per window", result.get("revealed") == 2,
          f"revealed={result.get('revealed')}")
    check("by_source merged", (result.get("by_source") or {}).get("apollo") == 2)
    check("deepest-window cursor advance persisted",
          result.get("apollo_pages_walked") == 1
          and int(campaign.last_apollo_page or 0) == 1,
          f"walked={result.get('apollo_pages_walked')}, "
          f"campaign.last_apollo_page={campaign.last_apollo_page}")
    check("no errors", not result.get("errors"), str(result.get("errors")))

    db.close()
    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
