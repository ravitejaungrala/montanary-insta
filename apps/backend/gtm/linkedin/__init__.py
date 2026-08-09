"""GTM LinkedIn Agent — standalone LinkedIn outreach automation subsystem.

Fully separate from the email sequencer (`nexus/services/sequencer.py`). Sends
connection requests / messages / InMails through the customer's own LinkedIn
session via Playwright, detects acceptances + replies, branches the sequence,
manages session health, and emits analytics events.

Package map (see C:/Users/govar/.claude/plans/delegated-swinging-blossom.md):
  models.py             — ORM for the gtm_linkedin_* tables
  crypto.py             — Fernet encrypt/decrypt for cookies + login pwd
  migrations.py         — additive DDL (registered via nexus/migrations/__init__.py)
  events.py             — event emit + analytics queries
  auth.py               — headless login + session validation (Phase 1)
  scheduler.py          — randomized slot-gen + dispatch (Phase 2)
  worker.py             — SQS worker / one job per invocation (Phase 2)
  browser.py            — Playwright primitives (Phase 2)
  content_generator.py  — Gemini copy (Phase 2)
  sequence_engine.py    — state machine + branching (Phase 3)
  acceptance_detector.py / reply_detector.py (Phase 3)

Runtime behavior is gated by `core.config.NEXUS_LINKEDIN_ENABLED`. The schema
(gtm_linkedin_* tables) is created additively by `gtm/linkedin/migrations.py`,
registered in `nexus/migrations/__init__.py`, regardless of that flag.
"""
