"""Phase 10 — credit metering schema.

Formalizes the credit-system tables (created ad-hoc during the credit feature)
into the migration registry so they exist on every deploy / fresh DB:
  - nexus_user_credits   — per-user (plan-owner) credit balance
  - nexus_credit_grants  — idempotency ledger for one-time credit purchases
  - nexus_campaigns.credits_consumed — running per-campaign spend

All statements idempotent + additive (CREATE TABLE/INDEX IF NOT EXISTS,
ADD COLUMN IF NOT EXISTS) — safe to re-run, and a no-op where they already
exist (e.g. the shared staging/prod DB).
"""

from __future__ import annotations

MIGRATIONS = [
    # Per-user credit balance (mirrors nexus.models_phase6.UserCredits).
    """
    CREATE TABLE IF NOT EXISTS nexus_user_credits (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL UNIQUE,
        plan VARCHAR DEFAULT 'free',
        plan_allotment INTEGER DEFAULT 0,
        purchased_this_cycle INTEGER DEFAULT 0,
        consumed_this_cycle INTEGER DEFAULT 0,
        enterprise_custom_allotment INTEGER,
        cycle_anchor TIMESTAMP,
        last_reset_at TIMESTAMP DEFAULT NOW(),
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_user_credits_user ON nexus_user_credits (user_id)",

    # Idempotency ledger for one-time credit purchases (mirrors CreditGrant).
    """
    CREATE TABLE IF NOT EXISTS nexus_credit_grants (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        stripe_session_id VARCHAR NOT NULL UNIQUE,
        credits INTEGER DEFAULT 0,
        amount_cents INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_credit_grants_user ON nexus_credit_grants (user_id)",

    # Per-campaign running spend (bumped by credits_service.consume()).
    "ALTER TABLE nexus_campaigns ADD COLUMN IF NOT EXISTS credits_consumed INTEGER NOT NULL DEFAULT 0",
]
