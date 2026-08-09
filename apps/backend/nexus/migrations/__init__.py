"""NEXUS schema migrations — registry pattern.

Each phase ships a `phase<N>.py` module exposing a `MIGRATIONS` list of
SQL strings. The list is appended to `ALL_PHASES` here so `apply_all()`
runs every phase's migrations on cold start, in order.

Conventions for migration SQL:
- Every statement must be **idempotent**: `CREATE TABLE IF NOT EXISTS`,
  `CREATE INDEX IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN ...` wrapped
  in an existence check by the runner.
- NEXUS-side tables prefixed `nexus_`.
- Never `ALTER TABLE users` — use a `nexus_user_profiles` side-table
  instead (PIPELYT's `users` row is canonical).
- Never `DROP` anything — additive only.

`apply_all()` is invoked by `main.py::sync_db_schema()` inside the
existing transaction, gated by `NEXUS_ENABLED`.
"""

from typing import List, Tuple
import logging

from . import phase1, phase2, phase3, phase4, phase5, phase6, phase7, phase8, phase10, phase12, phase13, phase14, phase15, phase16, phase17, phase18, phase19
# Phase 11's DDL lives in the GTM package (gtm/linkedin/migrations.py); the
# central runner just registers it here.
from gtm.linkedin import migrations as phase11_gtm_linkedin


# Phase order matters — Phase 1 lays workspace + user_profile; phases 2-6
# reference nexus_workspaces.id via FK so they must run after Phase 1.
# Within Phases 2-6 the order is dependency-driven:
#   Phase 2 (products) -> referenced by Phase 3 (leads/campaigns)
#   Phase 3 (leads/campaigns) -> referenced by Phase 4 (sequences) and 5 (inbox) and 6 (analytics/voice)
#   Phase 4 (sequences) -> referenced by Phase 5 (inbox threads) and 6 (touchpoint roll-ups)
# Each phase's MIGRATIONS list uses bare BIGINT for cross-phase FKs; the
# Postgres FK constraints to nexus_<other_phase>_<table> get attached
# only if those tables exist (defensive via `DO $$ ... $$` guards in some
# phase modules; bare columns otherwise).
ALL_PHASES: List[Tuple[str, List[str]]] = [
    ("phase1 — workspaces + user_profiles", phase1.MIGRATIONS),
    ("phase2 — products + knowledge_embeddings", phase2.MIGRATIONS),
    ("phase3 — leads + campaigns + enrichment", phase3.MIGRATIONS),
    ("phase4 — sequences + automations + suppression", phase4.MIGRATIONS),
    ("phase5 — inbox + intent + threads", phase5.MIGRATIONS),
    ("phase6 — voice + bookings + analytics + misc", phase6.MIGRATIONS),
    ("phase7 — gtm journey fields on global_leads + touchpoints", phase7.MIGRATIONS),
    ("phase8 — legacy-parity aliases (nexus_invitations view)", phase8.MIGRATIONS),
    ("phase10 — credit metering schema (user_credits, credit_grants, credits_consumed)", phase10.MIGRATIONS),
    ("phase11 — GTM LinkedIn Agent (accounts, settings, lead_state, events, jobs)", phase11_gtm_linkedin.MIGRATIONS),
    ("phase12 — Performance Agent v1 (nexus_winning_examples upsert key)", phase12.MIGRATIONS),
    ("phase13 — per-brand demo booking link (nexus_sender_brands.booking_url)", phase13.MIGRATIONS),
    ("phase14 — nullable icp_score (blank match score on BYO uploads)", phase14.MIGRATIONS),
    ("phase15 — per-workspace lead status/phone (isolation fix)", phase15.MIGRATIONS),
    ("phase16 — prospect-facing meeting agendas (nexus_meeting_agendas)", phase16.MIGRATIONS),
    ("phase17 — persisted product_description on nexus_products", phase17.MIGRATIONS),
    ("phase18 — per-product brand_colors on nexus_products", phase18.MIGRATIONS),
    ("phase19 — email brand assets (logo/hero) + f1 section copy", phase19.MIGRATIONS),
]


def apply_all(db, logger: logging.Logger) -> None:
    """Run every registered phase's migrations. Failures in one phase
    are logged but do not stop later phases — every statement is
    idempotent so re-runs on next cold start will retry.

    Each statement is committed independently so a failing one can't
    poison the surrounding transaction (Postgres aborts the whole tx
    on first error). After a failure we rollback to clear the aborted
    state and let SQLAlchemy autobegin a fresh tx on the next execute.
    """
    from sqlalchemy import text as _text

    total_ok = 0
    total_skipped = 0
    for label, statements in ALL_PHASES:
        logger.info(f"NEXUS migration: {label} — {len(statements)} statement(s)")
        phase_ok = 0
        phase_skipped = 0
        for stmt in statements:
            try:
                db.execute(_text(stmt))
                db.commit()
                phase_ok += 1
            except Exception as e:
                # Postgres aborts the current tx on any SQL error. Rollback
                # to clear the aborted state — the next execute() will
                # autobegin a fresh tx. Do NOT call db.begin() explicitly;
                # in SQLAlchemy autobegin mode that raises InvalidRequestError.
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning(
                    f"NEXUS migration statement skipped ({label}): {e}"
                )
                phase_skipped += 1
                continue
        total_ok += phase_ok
        total_skipped += phase_skipped
        logger.info(
            f"NEXUS migration: {label} done — {phase_ok} applied, "
            f"{phase_skipped} skipped"
        )
    logger.info(
        f"NEXUS migrations complete — {total_ok} applied, {total_skipped} skipped"
    )
