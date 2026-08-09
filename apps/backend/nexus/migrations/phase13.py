"""Phase 13 — per-brand demo booking link.

Adds `booking_url` to `nexus_sender_brands` so each sender card (brand) on the
Connectors page can carry its OWN demo booking link (Microsoft Bookings /
Google / Calendly). The outreach CTA (`sequencer.py::_build_sender_ctx_for_product`)
prefers this per-brand value over the generic `NEXUS_DEFAULT_CTA_URL` env default,
so every product's emails link to its own booking page.

Purely additive — nullable column, no data migration. `ADD COLUMN IF NOT EXISTS`
is native Postgres and idempotent, matching the additive-only migration policy.
"""

from __future__ import annotations


MIGRATIONS = [
    "ALTER TABLE nexus_sender_brands ADD COLUMN IF NOT EXISTS booking_url TEXT",
]
