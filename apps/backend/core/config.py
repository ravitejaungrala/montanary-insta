import logging
import os
from dotenv import load_dotenv

load_dotenv()

_config_logger = logging.getLogger("pipelyt.config")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/pipelyt")

# SECRET_KEY MUST be overridden in production (used for JWT signing).
# Running on Lambda with the default value is a security failure, so we warn loudly.
_DEFAULT_SECRET_KEY = "your-secret-key-123"
SECRET_KEY = os.getenv("SECRET_KEY", _DEFAULT_SECRET_KEY)
if SECRET_KEY == _DEFAULT_SECRET_KEY and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    _config_logger.critical(
        "SECRET_KEY is using the built-in fallback value on Lambda. "
        "Set the SECRET_KEY environment variable to a strong random string."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 365 * 10 # Effectively permanent (10 years)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Frontend Config
FRONTEND_URL_RAW = os.getenv("FRONTEND_URL", "http://localhost:5173")
# Support multiple URLs separated by comma for CORS
FRONTEND_URLS = [url.strip().rstrip("/") for url in FRONTEND_URL_RAW.split(",")]
# Primary URL for redirects
FRONTEND_URL = FRONTEND_URLS[0]

# Social API Config
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

TWITTER_CLIENT_ID = os.getenv("TWITTER_CLIENT_ID")
TWITTER_CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET")
TWITTER_REDIRECT_URI = os.getenv("TWITTER_REDIRECT_URI", f"{BASE_URL}/auth/twitter/callback")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

TWITTER_API_KEY = os.getenv("TWITTER_API_KEY") # Consumer Key
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET") # Consumer Secret
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
# NEW — LinkedIn "Personal" app (Community Management API app). Grants the
# r_member_postAnalytics / r_member_profileAnalytics / *_social_feed scopes
# that the OIDC+Share app can't hold (LinkedIn hard-forbids Community
# Management from coexisting with other products on one app). Falls back to
# the legacy LINKEDIN_CLIENT_ID so nothing breaks if this env is unset.
LINKEDIN_PERSONAL_CLIENT_ID = os.getenv("LINKEDIN_PERSONAL_CLIENT_ID") or LINKEDIN_CLIENT_ID
LINKEDIN_PERSONAL_CLIENT_SECRET = os.getenv("LINKEDIN_PERSONAL_CLIENT_SECRET") or LINKEDIN_CLIENT_SECRET
REDIRECT_URI = os.getenv("REDIRECT_URI", f"{BASE_URL}/auth/linkedin/callback")

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_REDIRECT_URI = os.getenv("FACEBOOK_REDIRECT_URI", f"{BASE_URL}/auth/facebook/callback")

# YouTube / Google OAuth — Phase 1 (connect + channel fetch). Same credentials
# work for Phase 2 (video upload), Phase 3 (comments), Phase 4 (analytics) —
# we asked for all scopes upfront on the consent screen.
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", f"{BASE_URL}/auth/youtube/callback")

# TikTok OAuth — Phase 1 (connect + user info). Same credentials cover Phase 2
# (video publish via Content Posting API), Phase 3 (analytics), Phase 4
# (comments — gated, requested in follow-up review). All scopes asked for
# upfront on the consent screen so we don't need to re-prompt.
# Note: TikTok calls these CLIENT_KEY / CLIENT_SECRET (not client_id) — keep
# the variable name aligned with TikTok's docs to avoid copy-paste confusion.
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
TIKTOK_REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", f"{BASE_URL}/auth/tiktok/callback")

# Pinterest OAuth 2.0 — Phase 1 (connect + board fetch + pin publish).
# Trial Access scopes requested upfront: boards:read, boards:write, pins:read,
# pins:write, user_accounts:read. Pinterest access_token lasts 30 days;
# refresh_token lasts 60 days. We refresh lazily on 401 from the API.
PINTEREST_CLIENT_ID = os.getenv("PINTEREST_CLIENT_ID")
PINTEREST_CLIENT_SECRET = os.getenv("PINTEREST_CLIENT_SECRET")
PINTEREST_REDIRECT_URI = os.getenv("PINTEREST_REDIRECT_URI", f"{BASE_URL}/auth/pinterest/callback")

# S3 / AWS Config
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
PUBLIC_URL = os.getenv("PUBLIC_URL", BASE_URL)
# Stripe Config
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Safety guard: refuse to boot the local backend against Stripe LIVE keys.
#
# Why: a developer running `python main.py` with sk_live_... in their .env is
# one typo away from charging a real card real money. Test cards are rejected
# by Stripe in live mode, so there's no upside to pointing local at prod.
# Production (Lambda) is unaffected — AWS_LAMBDA_FUNCTION_NAME is always
# present there, so this branch never runs.
# Escape hatch: set ALLOW_LIVE_STRIPE_IN_DEV=true if you really need it.
_is_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
_allow_live_stripe_local = os.getenv("ALLOW_LIVE_STRIPE_IN_DEV", "").strip().lower() == "true"
if (
    STRIPE_SECRET_KEY
    and STRIPE_SECRET_KEY.startswith("sk_live_")
    and not _is_lambda
    and not _allow_live_stripe_local
):
    raise RuntimeError(
        "\n\n"
        "REFUSING TO START — Stripe LIVE keys detected in local backend.\n"
        "  STRIPE_SECRET_KEY starts with 'sk_live_'. A single real card typed\n"
        "  into the checkout page would charge real money.\n\n"
        "  Use Stripe TEST keys for local development:\n"
        "    - Toggle 'Test mode' in the Stripe Dashboard header.\n"
        "    - Replace STRIPE_SECRET_KEY (sk_test_...),\n"
        "      STRIPE_PUBLISHABLE_KEY (pk_test_...),\n"
        "      STRIPE_WEBHOOK_SECRET (whsec_... from test mode),\n"
        "      and all STRIPE_*_ID price IDs (test-mode prices).\n\n"
        "  If you really need live keys locally (rare), set in .env:\n"
        "      ALLOW_LIVE_STRIPE_IN_DEV=true\n"
    )

# Price IDs
# We accept BOTH the new (Starter/Growth/Agency) and legacy (Basic/Pro)
# env-var names so deployments can migrate without breaking. The new names
# match the public plan names in pricingV2; the legacy names map 1:1
# (Basic = Starter, Pro = Growth) and stay readable from older Lambda envs.
STRIPE_BASIC_MONTHLY_ID = os.getenv("STRIPE_STARTER_MONTHLY_ID") or os.getenv("STRIPE_BASIC_MONTHLY_ID")
STRIPE_BASIC_YEARLY_ID = os.getenv("STRIPE_STARTER_YEARLY_ID") or os.getenv("STRIPE_BASIC_YEARLY_ID")
STRIPE_PRO_MONTHLY_ID = os.getenv("STRIPE_GROWTH_MONTHLY_ID") or os.getenv("STRIPE_PRO_MONTHLY_ID")
STRIPE_PRO_YEARLY_ID = os.getenv("STRIPE_GROWTH_YEARLY_ID") or os.getenv("STRIPE_PRO_YEARLY_ID")
STRIPE_AGENCY_MONTHLY_ID = os.getenv("STRIPE_AGENCY_MONTHLY_ID")
STRIPE_AGENCY_YEARLY_ID = os.getenv("STRIPE_AGENCY_YEARLY_ID")
STRIPE_ENTERPRISE_MONTHLY_ID = os.getenv("STRIPE_ENTERPRISE_MONTHLY_ID")
STRIPE_ENTERPRISE_YEARLY_ID = os.getenv("STRIPE_ENTERPRISE_YEARLY_ID")

CANVA_CLIENT_ID = os.getenv("CANVA_CLIENT_ID")
CANVA_CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET")
CANVA_REDIRECT_URI = os.getenv("CANVA_REDIRECT_URI", f"{BASE_URL}/auth/canva/callback")

# Azure AD / Microsoft Graph Config
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
AZURE_SENDER_EMAIL = os.getenv("AZURE_SENDER_EMAIL", "contact@neuzenai.com")

# Shared secret used to authenticate the EventBridge -> /scheduler/process trigger.
# Set this in Lambda env vars for BOTH the main backend AND the lambda_pinger.
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET")

# ── NEXUS port (in progress) ──────────────────────────────────────────────────
# Feature flag for the ongoing NEXUS → PIPELYT port. While false (default),
# nothing in apps/backend/nexus/ is imported or registered, so PIPELYT
# behavior is identical to the pre-merge state. Flip to "true" on staging
# only after Phase 1 lands a working /nexus/* surface.
# See docs/nexus-migration/PHASE_PLAN.md for the rollout plan.
NEXUS_ENABLED = False
NEXUS_LINKEDIN_ENABLED = False

# 32-byte urlsafe-base64 Fernet key used to encrypt LinkedIn session cookies
# (and the transient login password in the SQS job payload) at rest. MUST be
# set (via env / Secrets Manager) on the backend + worker before LinkedIn auth
# is used; absence raises at encrypt/decrypt time, never silently stores plaintext.
LINKEDIN_COOKIE_KEY = os.getenv("LINKEDIN_COOKIE_KEY", "")

# FIFO SQS queue URL the LinkedIn scheduler publishes jobs to and the worker
# consumes. Unset locally → DB-only mode (jobs run inline via the test-drive
# script); set in AWS to drive the worker Lambda.
GTM_LINKEDIN_SQS_URL = os.getenv("GTM_LINKEDIN_SQS_URL", "")
