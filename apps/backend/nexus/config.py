"""NEXUS-specific env vars.

Imported only on demand by routers/services inside this package — never
at PIPELYT module load — so this file does not pull NEXUS dependencies
into the cold-start path while NEXUS_ENABLED is False.

See `docs/nexus-migration/ENV_VARS.md` for the conflict resolution
policy. Conflicting names are prefixed with `NEXUS_`; standalone NEXUS
names keep their original spelling for parity with the legacy stack.
"""

import os

# ── Billing — INTENTIONALLY OMITTED ─────────────────────────────────────────
# Per the NEXUS port plan, billing is out-of-scope. PIPELYT's existing
# `/payment` router + Stripe configuration handle all billing for NEXUS
# features. See docs/nexus-migration/PHASE_PLAN.md "Phase 7 — REMOVED".

# ── AI providers ────────────────────────────────────────────────────────────
# NEXUS uses Google Gemini for all AI calls (chat + embeddings). The API key
# lives in core/config.py as GEMINI_API_KEY and is read directly from the
# environment by nexus/services/gemini.py + personalize.py + intent_classifier.py
# + rag_reply.py. No NEXUS-specific AI keys here.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

# ── Email delivery + inbound ────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_READ_API_KEY = os.getenv("RESEND_READ_API_KEY")
RESEND_FROM = os.getenv("RESEND_FROM")
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET")
TEST_REDIRECT_EMAIL = os.getenv("TEST_REDIRECT_EMAIL")

# ── Gmail OAuth (polling for inbound replies) ───────────────────────────────
NEXUS_GMAIL_USER = os.getenv("NEXUS_GMAIL_USER")
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_REDIRECT_URI = os.getenv("GMAIL_REDIRECT_URI")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# ── Lead discovery / enrichment ─────────────────────────────────────────────
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ABSTRACT_API_KEY = os.getenv("ABSTRACT_API_KEY")
REOON_API_KEY = os.getenv("REOON_API_KEY")

# ── Microsoft Graph (Bookings + outbound) ──────────────────────────────────
MS_TENANT_ID = os.getenv("MS_TENANT_ID")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
MS_BOOKING_BUSINESS_ID = os.getenv("MS_BOOKING_BUSINESS_ID")
MS_WEBHOOK_SECRET = os.getenv("MS_WEBHOOK_SECRET")

# ── Calendly ────────────────────────────────────────────────────────────────
CALENDLY_WEBHOOK_SIGNING_KEY = os.getenv("CALENDLY_WEBHOOK_SIGNING_KEY")
BOOKING_URL = os.getenv("BOOKING_URL")
BOOKING_LINK = os.getenv("BOOKING_LINK")

# ── Voice (Twilio + Deepgram) ───────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
VOICE_PROVIDER = os.getenv("VOICE_PROVIDER")

# ── Template service (MJML + Pexels) ────────────────────────────────────────
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
MJML_APP_ID = os.getenv("MJML_APP_ID")
MJML_SECRET_KEY = os.getenv("MJML_SECRET_KEY")

# ── Misc ────────────────────────────────────────────────────────────────────
SENDER_LOOM_URL = os.getenv("SENDER_LOOM_URL")
REP_NAME = os.getenv("REP_NAME")
REP_EMAIL = os.getenv("REP_EMAIL")
REP_PHONE = os.getenv("REP_PHONE")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
