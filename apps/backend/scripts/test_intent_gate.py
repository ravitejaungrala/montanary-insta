"""Live + offline verification for the Agent #10 intent gate integration.

The gate (NEXUS_INTENT_GATE, default ON) makes discovery attach Apollo leads
as `signals.intent.status='pending'`; the intent_sweep tick scores them with
Agent #10 and enrolls only the accepted ones. This script lets you verify each
layer WITHOUT launching a full /analyze run.

Run from apps/backend with the backend venv:

  # 1) OFFLINE — deterministic, no keys/network. Verifies the adapter + gate
  #    wiring against Agent #10's bundled fixtures (Acme=hot, Initech=cold).
  AGENT10_USE_FAKES=1 ./venv/Scripts/python.exe scripts/test_intent_gate.py

  # 2) LIVE adapter — real Agent #10 run on real companies (needs GEMINI_API_KEY
  #    + APOLLO_API_KEY in the env / apps/backend/.env). ~2 min/company.
  ./venv/Scripts/python.exe scripts/test_intent_gate.py --live

  # 3) FULL sweep against the real DB — scores the pending leads of a campaign
  #    exactly as the scheduler tick does, and prints the verdict summary.
  ./venv/Scripts/python.exe scripts/test_intent_gate.py --campaign-id 123
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running as `python scripts/test_intent_gate.py` from apps/backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hr(title: str) -> None:
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


SAMPLE_ICP = {
    "organization_industries": ["Software", "Information Technology"],
    "person_titles": ["Chief Revenue Officer", "VP Sales"],
    "person_locations": ["United States"],
    "revenue_range": "$50M – $200M",
}


def check_adapter(companies) -> None:
    """Run score_company_intent for each company and print the verdict.

    `companies` items are (name, domain, title) -> scored against SAMPLE_ICP,
    or (name, domain, title, icp) to override the ICP for that company.
    """
    from nexus.services import intent_agent

    print(f"gate_enabled() = {intent_agent.gate_enabled()}  "
          f"(NEXUS_INTENT_GATE={os.getenv('NEXUS_INTENT_GATE', '<unset:default ON>')})")
    print(f"USE_FAKES = {os.getenv('AGENT10_USE_FAKES', '0')}\n")

    import time as _t
    timings = []
    for item in companies:
        name, domain, title = item[0], item[1], item[2]
        icp = item[3] if len(item) > 3 else SAMPLE_ICP
        _start = _t.monotonic()
        v = intent_agent.score_company_intent(
            company_name=name, domain=domain, person_title=title,
            campaign_icp=icp,
        )
        _elapsed = _t.monotonic() - _start
        timings.append(_elapsed)
        print(f"--- {name}: {_elapsed:.1f}s ---")
        verdict = "ACCEPT" if v["accepted"] else "reject"
        print(f"[{verdict:6}] {name:24} score={v['intent_score']:>3} "
              f"fit={v['icp_fit_band']}  signals={len(v['signals'])}")
        if not v["accepted"]:
            print(f"           reason: {v['reason']}")
        for s in v["signals"][:3]:
            print(f"           • {s['type']}: {s['summary']} ({s.get('date')})")

    if timings:
        print(f"\nPER-COMPANY: {[f'{t:.1f}s' for t in timings]}  "
              f"avg={sum(timings)/len(timings):.1f}s  max={max(timings):.1f}s")
        print("NOTE: a real campaign scores up to 6 companies CONCURRENTLY, so "
              "campaign wall-clock ≈ max(company) per wave, not the sum.")


def run_sweep(campaign_id: int) -> None:
    """Exercise the real intent_sweep against the live DB for one campaign."""
    # Import the full model set so SQLAlchemy's mapper registry is complete
    # (the FastAPI app does this at startup; a bare script must do it too, or
    # FKs like nexus_lead_sequences -> nexus_workspaces can't resolve).
    import models  # noqa: F401  (core User — NexusWorkspace relates to it)
    import nexus.models  # noqa: F401  (NexusWorkspace + workspace tables)
    import nexus.models_phase2  # noqa: F401
    import nexus.models_phase3  # noqa: F401
    import nexus.models_phase4  # noqa: F401

    from core.database import SessionLocal
    from nexus.services.intent_sweep import process_pending_intent

    db = SessionLocal()
    try:
        before = db.execute(
            __import__("sqlalchemy").text(
                "SELECT (signals->'intent'->>'status') AS s, COUNT(*) "
                "FROM nexus_leads WHERE campaign_id=:c GROUP BY 1"
            ),
            {"c": campaign_id},
        ).fetchall()
        print("intent status BEFORE:", {r[0]: r[1] for r in before})

        result = asyncio.run(process_pending_intent(db, max_campaigns=1, max_companies=3))
        print("sweep result:", result)

        after = db.execute(
            __import__("sqlalchemy").text(
                "SELECT (signals->'intent'->>'status') AS s, COUNT(*) "
                "FROM nexus_leads WHERE campaign_id=:c GROUP BY 1"
            ),
            {"c": campaign_id},
        ).fetchall()
        print("intent status AFTER: ", {r[0]: r[1] for r in after})
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="real Agent #10 run on real companies")
    ap.add_argument("--campaign-id", type=int, default=None, help="run the full sweep on this campaign (real DB)")
    args = ap.parse_args()

    if args.campaign_id is not None:
        _hr(f"FULL SWEEP — campaign {args.campaign_id}")
        run_sweep(args.campaign_id)
        return 0

    if args.live:
        _hr("LIVE ADAPTER — real Agent #10 (needs GEMINI + APOLLO keys)")
        check_adapter([
            ("Databricks", "databricks.com", "Chief Revenue Officer"),
            ("Wikimedia Foundation", "wikimedia.org", "Director"),
        ])
        return 0

    # Default: offline deterministic check against Agent #10 fixtures.
    os.environ.setdefault("AGENT10_USE_FAKES", "1")
    _hr("OFFLINE ADAPTER — Agent #10 fixtures (Acme=hot, Initech=cold)")
    check_adapter([
        # Acme has strong signals; with no ICP restriction -> ACCEPT (pure intent).
        ("Acme Corp", "acme.com", "Chief Revenue Officer", {}),
        # Acme again, but a mismatched ICP -> REJECT (fit gates intent).
        ("Acme Corp", "acme.com", "Chief Revenue Officer", SAMPLE_ICP),
        # Cold company, no signals -> REJECT.
        ("Initech", "initech.com", "VP Sales", {}),
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
