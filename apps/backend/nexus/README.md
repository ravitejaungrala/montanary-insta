# `apps/backend/nexus/`

Destination package for the NEXUS port. Empty (by design) until features
land per `docs/nexus-migration/PHASE_PLAN.md`.

## Layout when populated

```
apps/backend/nexus/
├── __init__.py          # marker only; no runtime imports
├── config.py            # NEXUS-specific env vars (NEXUS_STRIPE_*, MS_*, etc)
├── models.py            # SQLAlchemy tables (nexus_workspaces, nexus_leads, …)
├── schemas.py           # Pydantic request/response shapes
├── deps.py              # FastAPI dependencies (auth, trial guard, role guard)
├── routers/
│   ├── __init__.py
│   ├── workspace.py
│   ├── analyze.py
│   ├── leads.py
│   ├── campaigns.py
│   ├── sequences.py
│   ├── inbox.py
│   ├── billing.py
│   └── …
└── services/
    ├── __init__.py
    ├── nvidia.py        # NVIDIA NIM client (Llama 3.3-70b, Qwen)
    ├── anthropic.py     # Claude RAG-grounded replies
    ├── openai.py        # Fallback / embeddings
    ├── resend.py        # Outbound mail
    ├── gmail.py         # OAuth polling + thread matching
    ├── ms_graph.py      # MS Bookings
    ├── twilio.py        # Voice
    ├── playwright.py    # Headless browser scrape
    ├── lead_enricher.py
    ├── lead_engine.py
    ├── personalize.py
    ├── sequencer.py
    └── …
```

## How to add a feature

1. Pick a slice from `docs/nexus-migration/PHASE_PLAN.md`.
2. Add models in `models.py`. Add a defensive `CREATE TABLE IF NOT
   EXISTS` block to `apps/backend/main.py::sync_db_schema()`.
3. Add Pydantic schemas in `schemas.py`.
4. Add the FastAPI router in `routers/<feature>.py`.
5. Add any new external clients in `services/`.
6. Register the router in `apps/backend/main.py`, **guarded by the
   `NEXUS_ENABLED` flag**:

   ```python
   from core.config import NEXUS_ENABLED

   if NEXUS_ENABLED:
       from nexus.routers import workspace, analyze  # etc
       app.include_router(workspace.router, prefix="/nexus/workspaces", tags=["Nexus"])
       app.include_router(analyze.router, prefix="/nexus/analyze", tags=["Nexus"])
   ```

7. Tick the row in `docs/nexus-migration/ROUTE_MAP.md` /
   `MODEL_MAP.md`.
