from fastapi import FastAPI, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import logging
import traceback
from core.database import init_db, get_db
from core.config import FRONTEND_URLS, NEXUS_ENABLED, NEXUS_LINKEDIN_ENABLED
from routers import auth
# Pipelyt (non-Nexus) product routers are imported lazily below, only when
# NEXUS_ONLY is off — so local Nexus/GTM testing can skip their heavy deps.
from models import User
from core.auth import get_password_hash

app = FastAPI(title="Pipelyt API")

# Configure Logging for Production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("pipelyt")

# CORS middleware — wide-open (allow_origins="*") for the NEXUS branch deploy.
# Browsers don't allow `allow_credentials=True` AND `allow_origins=["*"]` at
# the same time per the CORS spec, so credentials are disabled in this mode.
# If you re-enable cookie/credential auth later, switch back to the regex
# pattern with explicit domains.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _cors_headers_for(request: Request) -> dict:
    """Build Access-Control-Allow-* headers that match the request's
    Origin against the same allow-list the CORSMiddleware uses.

    Why this helper exists: Starlette's CORSMiddleware only injects CORS
    headers onto SUCCESSFUL responses. When an unhandled exception is
    caught by `@app.exception_handler(Exception)`, the resulting 500
    JSONResponse bypasses the middleware injection — so the browser sees
    a response with no `Access-Control-Allow-Origin`, blocks the body,
    and reports "CORS missing" in the console. The actual error message
    (RDS timeout, NULL constraint, anything) becomes invisible to the
    operator. Adding these headers manually here means error responses
    are readable in the browser DevTools network tab.

    Mirrors the CORSMiddleware allow-list above (currently `["*"]` with
    credentials disabled). When that wide-open mode is in effect we
    return `Access-Control-Allow-Origin: *` and DO NOT emit a
    credentials header — pairing `*` with `Allow-Credentials: true`
    is forbidden by the CORS spec and would break the browser response.
    If the middleware is ever switched back to an explicit allow-list,
    update this helper to echo the matching origin instead of `*`.
    """
    origin = request.headers.get("origin", "")
    # The middleware above uses allow_origins=["*"] + credentials=False,
    # so we mirror that here. Reflecting `*` on every error response
    # keeps the browser from masking the actual error body with a
    # spurious "CORS missing" complaint.
    if origin:
        return {
            "Access-Control-Allow-Origin": "*",
            "Vary": "Origin",
        }
    return {}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"CRITICAL ERROR: {str(exc)}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "message": str(exc)},
        headers=_cors_headers_for(request),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Same CORS guarantee for HTTPException paths (404, 401, 422 etc.).
    Without this override, FastAPI's default handler also returns a
    response that may bypass the CORS middleware on some Starlette
    versions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            **(getattr(exc, "headers", None) or {}),
            **_cors_headers_for(request),
        },
    )

@app.get("/")
async def root():
    return {"message": "Pipelyt backend is running! 🚀", "status": "online"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/test-db")
async def test_db(db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        return {"status": "Database connection successful! 📁"}
    except Exception as e:
        return {"status": "Database connection failed! ❌", "error": str(e)}


@app.post("/admin/migrate")
async def admin_migrate(request: Request):
    """One-shot schema migration — hit this after deploying new columns when
    AUTO_SYNC_DB is disabled. Guarded by X-Scheduler-Secret so only the
    operator can trigger it. Idempotent: all ALTER TABLE statements are
    wrapped in `IF NOT EXISTS` / `column_exists` checks inside
    sync_db_schema()."""
    from core.config import SCHEDULER_SECRET
    if not SCHEDULER_SECRET:
        raise HTTPException(status_code=500, detail="SCHEDULER_SECRET not configured")
    provided = request.headers.get("X-Scheduler-Secret")
    if not provided or provided != SCHEDULER_SECRET:
        logger.warning("Unauthorized hit to /admin/migrate.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        sync_db_schema()
        return {"status": "migration complete"}
    except Exception as e:
        logger.error(f"Admin migration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Static files (handle read-only filesystem in Lambda)
static_dir = "static"
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    static_dir = "/tmp/static"
    os.makedirs(os.path.join(static_dir, "uploads"), exist_ok=True)
else:
    os.makedirs("static/uploads", exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include Routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# ── NEXUS_ONLY local-dev shortcut ────────────────────────────────────────────
# When NEXUS_ONLY=true (set it in your LOCAL .env only), ALL Pipelyt
# (non-Nexus) product routers are skipped — their heavy deps are never imported
# and their /canva, /social, /content, /publishing, /video, … endpoints are not
# mounted. Boots much faster for Nexus/GTM development + testing. Auth and the
# /nexus/* routers below stay registered. NEVER set this in staging/prod.
NEXUS_ONLY = os.getenv("NEXUS_ONLY", "false").strip().lower() == "true"
if NEXUS_ONLY:
    # Even in nexus-only mode the shared frontend SHELL still calls /profile and
    # /connections on load to bootstrap the session — without them the app can't
    # sign in (blank page). So keep those two lightweight routers registered and
    # skip only the HEAVY product routers (content, video, canva, publishing,…).
    logger.info(
        "NEXUS_ONLY=true — skipping heavy Pipelyt routers (keeping shell: profile, social)"
    )
    from routers import profile, social
    app.include_router(profile.router, tags=["Profile"])
    app.include_router(social.router, tags=["Social Connections"])
    # OAuth-start endpoints (popup-friendly — JWT via ?token= query param,
    # not header). See social.open_oauth_router for the reasoning.
    app.include_router(social.open_oauth_router, tags=["OAuth Start"])
else:
    from routers import (
        social, content, analytics, drafts, profile, canva, contact,
        payment, publishing, team, templates, reputation, data_deletion, video,
        franchise, weekly_report_scheduler,
    )
    app.include_router(canva.router, tags=["Canva"])
    app.include_router(social.router, tags=["Social Connections"])
    app.include_router(social.open_oauth_router, tags=["OAuth Start"])
    app.include_router(content.router, tags=["AI Content"])
    app.include_router(publishing.router, tags=["Publishing"])
    app.include_router(analytics.router, tags=["Analytics"])
    app.include_router(drafts.router, tags=["Drafts"])
    app.include_router(profile.router, tags=["Profile"])
    app.include_router(contact.router, tags=["Contact Us"])
    app.include_router(payment.router, tags=["Payments"])
    app.include_router(team.router, tags=["Team Members"])
    app.include_router(franchise.router, tags=["Franchise"])
    app.include_router(templates.router, tags=["Templates"])
    app.include_router(reputation.router, tags=["Reputation"])
    # Meta App Review: "User Data Deletion" callback (Facebook signed_request).
    app.include_router(data_deletion.router, tags=["Data Deletion"])
    # Phase 1: motion-graphics video generation (Remotion Lambda).
    app.include_router(video.router, tags=["Video"])
    # Weekly dashboard-stats email digest — EventBridge trigger endpoint.
    app.include_router(weekly_report_scheduler.router, tags=["Reports"])

# ── NEXUS port — all phase routers ───────────────────────────────────────────
# Registered only when NEXUS_ENABLED is on so PIPELYT environments that
# haven't opted into the port don't expose /nexus/* endpoints (which
# would 500 against a Postgres without the nexus_* tables anyway).
#
# Each router module is imported defensively — if a phase module fails
# to import (e.g., missing optional dep like `resend` or `twilio`),
# we log + skip rather than break PIPELYT startup. This keeps the Lambda
# bootable even when NEXUS-only deps aren't installed yet.
if NEXUS_ENABLED:
    logger.info("NEXUS_ENABLED=true — registering /nexus/* routers")
    # (module_path, friendly_label) — order: Phase 1 → Phase 6
    _NEXUS_ROUTER_MODULES = [
        # Phase 1 — auth + workspace + user
        ("nexus.routers.workspace", "Phase 1 — Workspace"),
        ("nexus.routers.user", "Phase 1 — User"),
        # Phase 2 — Product analysis
        ("nexus.routers.analyze", "Phase 2 — Analyze"),
        ("nexus.routers.analyze_from_leads", "Phase 2 — Analyze: BYO leads upload"),
        ("nexus.routers.analyze_targeting", "Phase 2 — Analyze: suggest-targeting"),
        ("nexus.routers.refine_summary", "Phase 2 — Refine summary"),
        ("nexus.routers.kb", "Phase 2 — Knowledge base"),
        ("nexus.routers.kb_extract", "Phase 2 — KB file extract (PDF/DOCX/PPTX)"),
        ("nexus.routers.conversation_accounts", "Phase 4 — Conversation accounts (sender setup)"),
        ("nexus.routers.connectors", "Phase 4 — Mailbox connectors (Outlook/Gmail OAuth)"),
        ("nexus.routers.products", "Phase 2 — Products"),
        # Phase 3 — Leads + discovery
        ("nexus.routers.leads", "Phase 3 — Leads"),
        ("nexus.routers.campaigns", "Phase 3 — Campaigns"),
        ("nexus.routers.campaign_leads", "Phase 3 — Campaign leads table (LeadsTable UI)"),
        # Phase 4 — Sequencer
        ("nexus.routers.sequences", "Phase 4 — Sequences"),
        ("nexus.routers.automations", "Phase 4 — Automations"),
        ("nexus.routers.suppression", "Phase 4 — Suppression"),
        ("nexus.routers.scheduler", "Phase 4 — Scheduler ticks"),
        # Phase 5 — Inbox + intent
        ("nexus.routers.inbound", "Phase 5 — Inbound"),
        ("nexus.routers.conversations", "Phase 5 — Conversations"),
        # Phase 6 — Voice + Bookings + analytics + misc
        ("nexus.routers.voice", "Phase 6 — Voice"),
        ("nexus.routers.bookings", "Phase 6 — Bookings"),
        ("nexus.routers.analytics", "Phase 6 — Analytics"),
        ("nexus.routers.performance", "Phase 6 — Performance"),
        ("nexus.routers.journey", "Phase 6 — Journey"),
        ("nexus.routers.tracker", "Phase 6 — Tracker"),
        ("nexus.routers.credits", "Phase 6 — Credits"),
        ("nexus.routers.chat", "Phase 6 — Chat"),
        ("nexus.routers.dedup", "Phase 6 — Dedup"),
        ("nexus.routers.tracking_pixel", "Phase 6 — Tracking pixel"),
        ("nexus.routers.unsubscribe", "Phase 6 — Unsubscribe"),
        ("nexus.routers.linkedin", "Phase 5 — LinkedIn outreach"),
        ("nexus.routers.system", "System — health, export, deletion"),
        ("nexus.routers.reports", "Reports — CSV/PDF workspace exports"),
        # Phase 7 — GTM Journey parity stubs (legacy-name matching for future
        # Mongo->Postgres migration). Operational logic still being filled in
        # by the team; routers return safe defaults so the frontend can call
        # them without 404s.
        ("nexus.routers.settings_route", "Phase 7 — Settings"),
        ("nexus.routers.team", "Phase 7 — Team"),
        ("nexus.routers.microsite", "Phase 7 — Microsite"),
        ("nexus.routers.scrape_preview", "Phase 7 — Scrape preview"),
        ("nexus.routers.webhooks", "Phase 7 — External webhooks"),
        ("nexus.routers.billing", "Phase 7 — Billing (view-only)"),
    ]
    # GTM LinkedIn Agent — separately gated by NEXUS_LINKEDIN_ENABLED so the
    # connect/automation surface stays dark until LinkedIn is explicitly enabled.
    if NEXUS_LINKEDIN_ENABLED:
        _NEXUS_ROUTER_MODULES.append(
            ("gtm.linkedin.router", "Phase 11 — GTM LinkedIn Agent")
        )
    import importlib
    _nexus_registered = 0
    _nexus_failed = []
    for _mod_path, _label in _NEXUS_ROUTER_MODULES:
        try:
            _mod = importlib.import_module(_mod_path)
            app.include_router(_mod.router)
            _nexus_registered += 1
        except Exception as _e:  # noqa: BLE001
            _nexus_failed.append((_label, str(_e)[:200]))
            logger.warning(f"NEXUS router skipped ({_label}): {_e}")
    logger.info(
        f"NEXUS routers registered: {_nexus_registered}/{len(_NEXUS_ROUTER_MODULES)}"
    )
    if _nexus_failed:
        for _l, _err in _nexus_failed:
            logger.warning(f"  - {_l}: {_err}")

# Root-level fallback for production login (Fixes 404 in frontend build)
from fastapi.security import OAuth2PasswordRequestForm
@app.post("/login", tags=["Authentication"])
async def root_login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return await auth.login(form_data, db)

def seed_user():
    """Create the admin seed user only when BOTH SEED_USER_EMAIL and SEED_USER_PASSWORD
    are explicitly provided. Refuses to run with hard-coded defaults in production."""
    try:
        seed_email = os.getenv("SEED_USER_EMAIL")
        seed_password = os.getenv("SEED_USER_PASSWORD")
        if not seed_email or not seed_password:
            logger.info("Seed user skipped (SEED_USER_EMAIL / SEED_USER_PASSWORD not set).")
            return

        logger.info("Checking seed user status...")
        db = next(get_db())
        user = db.query(User).filter(User.email == seed_email).first()
        if not user:
            logger.info(f"Creating seed user: {seed_email}")
            new_user = User(email=seed_email, hashed_password=get_password_hash(seed_password))
            db.add(new_user)
            db.commit()
        db.close()
        logger.info("Seeding process complete.")
    except Exception as e:
        logger.error(f"Seeding failed: {e}")

def sync_db_schema():
    """
    Handles database initialization and schema migrations.
    Optimized with timeouts and existence checks to prevent deadlocks.
    """
    try:
        logger.info("Initializing database...")
        init_db()

        # Auto-migration for missing columns
        logger.info("Syncing database schema for Business DNA fields...")
        from sqlalchemy import text
        db = next(get_db())
        try:
            # Set a local lock timeout so we don't hang the entire server boot
            db.execute(text("SET lock_timeout = '2s';"))

            # Helper to check if a column exists
            def column_exists(table, column):
                res = db.execute(text(f"""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='{table}' AND column_name='{column}';
                """))
                return res.scalar() is not None

            # 1. Add product_url
            if not column_exists('users', 'product_url'):
                logger.info("Adding 'product_url' column...")
                db.execute(text("ALTER TABLE users ADD COLUMN product_url VARCHAR;"))

            # 2. Add business_url
            if not column_exists('users', 'business_url'):
                logger.info("Adding 'business_url' column...")
                db.execute(text("ALTER TABLE users ADD COLUMN business_url VARCHAR;"))

            # 3. Add business_dna
            if not column_exists('users', 'business_dna'):
                logger.info("Adding 'business_dna' column...")
                db.execute(text("ALTER TABLE users ADD COLUMN business_dna JSONB;"))

            # 4. Add role & team_id
            if not column_exists('users', 'role'):
                logger.info("Adding 'role' column...")
                db.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'admin';"))
            if not column_exists('users', 'team_id'):
                logger.info("Adding 'team_id' column...")
                db.execute(text("ALTER TABLE users ADD COLUMN team_id BIGINT;"))

            # 4b. RBAC: rename legacy role values to the 3-role vocab
            # (master_admin | admin | user). Idempotent + default-permissive
            # (filter by legacy value, never demote). Inline here, NOT the NEXUS
            # registry — the registry is skipped when NEXUS_SKIP_MIGRATIONS=true
            # and is forbidden from touching `users`.
            logger.info("RBAC: renaming legacy role values to master_admin/admin/user...")
            # users.role: owner (admin + no team_owner) -> master_admin; member -> admin
            db.execute(text("UPDATE users SET role='master_admin' WHERE role='admin' AND team_owner_id IS NULL;"))
            db.execute(text("UPDATE users SET role='admin' WHERE role='member';"))

            def _table_exists(t):
                return db.execute(text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
                ), {"t": t}).scalar() is not None

            # nexus_user_profiles.role mirror (admin|member|viewer). Guarded — on
            # a brand-new DB the table is created later by the registry and has
            # no rows to rename yet; on the existing shared DB it runs.
            if _table_exists('nexus_user_profiles'):
                db.execute(text(
                    "UPDATE nexus_user_profiles SET role='master_admin' "
                    "WHERE role='admin' AND user_id IN (SELECT id FROM users WHERE team_owner_id IS NULL);"
                ))
                db.execute(text("UPDATE nexus_user_profiles SET role='admin' WHERE role='member';"))
                db.execute(text("UPDATE nexus_user_profiles SET role='user' WHERE role='viewer';"))

            # 4c. team_invites.role — invites now carry the granted role.
            if not column_exists('team_invites', 'role'):
                logger.info("Adding 'role' column to team_invites...")
                db.execute(text("ALTER TABLE team_invites ADD COLUMN role VARCHAR DEFAULT 'user';"))

            # 4. Baseline follower counts per connected account (A-13)
            if not column_exists('social_accounts', 'initial_follower_count'):
                logger.info("Adding 'initial_follower_count' column to social_accounts...")
                db.execute(text("ALTER TABLE social_accounts ADD COLUMN initial_follower_count INTEGER DEFAULT 0;"))
                # Backfill: use current follower_count as baseline for existing rows
                # so deltas still make sense for users who connected before this migration.
                db.execute(text("UPDATE social_accounts SET initial_follower_count = COALESCE(follower_count, 0) WHERE initial_follower_count IS NULL OR initial_follower_count = 0;"))
            if not column_exists('social_accounts', 'initial_connected_at'):
                logger.info("Adding 'initial_connected_at' column to social_accounts...")
                db.execute(text("ALTER TABLE social_accounts ADD COLUMN initial_connected_at TIMESTAMP;"))
                # Backfill with current time for existing accounts
                db.execute(text("UPDATE social_accounts SET initial_connected_at = CURRENT_TIMESTAMP WHERE initial_connected_at IS NULL;"))

            # 5. media_type column on post tables so the publisher can route
            # video / document / image / text posts to the right platform API.
            # Kept nullable + no backfill — old rows treated as the legacy
            # text-or-image behaviour based on image_url presence.
            for tbl in ('published_posts', 'drafts', 'scheduled_posts'):
                if not column_exists(tbl, 'media_type'):
                    logger.info(f"Adding 'media_type' column to {tbl}...")
                    db.execute(text(f"ALTER TABLE {tbl} ADD COLUMN media_type VARCHAR;"))

            # 7. Add approved_by to scheduled_posts
            if not column_exists('scheduled_posts', 'approved_by'):
                logger.info("Adding 'approved_by' column to scheduled_posts...")
                db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN approved_by BIGINT;"))

            # 7b. Team Members feature (Phase 1): columns on users + content tables.
            if not column_exists('users', 'team_owner_id'):
                logger.info("Adding 'team_owner_id' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN team_owner_id BIGINT REFERENCES users(id);"
                ))
            if not column_exists('users', 'assigned_dna_product_id'):
                logger.info("Adding 'assigned_dna_product_id' column to users...")
                db.execute(text("ALTER TABLE users ADD COLUMN assigned_dna_product_id VARCHAR;"))
            if not column_exists('users', 'assigned_dna_product_ids'):
                logger.info("Adding 'assigned_dna_product_ids' (list) column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN assigned_dna_product_ids JSONB;"
                ))
                # Backfill list from the single-value column so existing members
                # keep their brand assignment after this column lands.
                db.execute(text(
                    "UPDATE users SET assigned_dna_product_ids = "
                    "jsonb_build_array(assigned_dna_product_id) "
                    "WHERE assigned_dna_product_id IS NOT NULL "
                    "AND assigned_dna_product_ids IS NULL;"
                ))
            if not column_exists('users', 'disabled'):
                logger.info("Adding 'disabled' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN disabled BOOLEAN DEFAULT FALSE NOT NULL;"
                ))
            # Weekly dashboard-stats email digest — tracks the last send so
            # the Monday-morning scheduler doesn't double-send if its
            # trigger endpoint fires more than once in the same run window.
            if not column_exists('users', 'last_weekly_report_sent_at'):
                logger.info("Adding 'last_weekly_report_sent_at' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN last_weekly_report_sent_at TIMESTAMP;"
                ))
            # Reputation → Auto-Reply. Page-wide user toggle; when TRUE, the
            # background worker fetches new comments on this user's recent
            # posts every 5 minutes and auto-posts an AI-generated reply.
            if not column_exists('users', 'auto_reply_enabled'):
                logger.info("Adding 'auto_reply_enabled' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN auto_reply_enabled "
                    "BOOLEAN DEFAULT FALSE NOT NULL;"
                ))
            # auto_reply_log table — one row per comment we've auto-replied
            # to, so the worker never double-replies. Idempotency key is
            # (user_id, platform, comment_id).
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS auto_reply_log (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    platform VARCHAR NOT NULL,
                    native_post_id VARCHAR NOT NULL,
                    comment_id VARCHAR NOT NULL,
                    reply_text TEXT,
                    status VARCHAR DEFAULT 'posted' NOT NULL,
                    error_msg TEXT,
                    replied_at TIMESTAMP DEFAULT NOW() NOT NULL,
                    CONSTRAINT uq_auto_reply UNIQUE (user_id, platform, comment_id)
                );
            """))
            db.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_auto_reply_user_platform "
                "ON auto_reply_log(user_id, platform);"
            ))
            if not column_exists('users', 'subscription_cancel_at_period_end'):
                logger.info("Adding 'subscription_cancel_at_period_end' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end "
                    "BOOLEAN DEFAULT FALSE NOT NULL;"
                ))
            # Franchise model columns — independent operator affiliated to a
            # Master brand. Franchisee brings own plan/subscription. Mutually
            # exclusive with team_owner_id.
            if not column_exists('users', 'franchise_of_id'):
                logger.info("Adding 'franchise_of_id' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN franchise_of_id BIGINT REFERENCES users(id);"
                ))
            if not column_exists('users', 'stripe_customer_id'):
                logger.info("Adding 'stripe_customer_id' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR;"
                ))
                # Unique-when-not-null (a single Stripe customer maps to one user).
                db.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_stripe_customer_id "
                    "ON users(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;"
                ))
            # Product-selection column — 'pipelyt' / 'gtm' / 'both'.
            # Onboarding step 4 lets new users pick which surface they want.
            # Existing users (created before this feature) get 'both' so their
            # experience is unchanged. Team members inherit their master's
            # selection; franchisees pick their own.
            if not column_exists('users', 'product_selection'):
                logger.info("Adding 'product_selection' column to users...")
                db.execute(text(
                    "ALTER TABLE users ADD COLUMN product_selection VARCHAR "
                    "DEFAULT 'both' NOT NULL;"
                ))
                # Explicit backfill for any rows already inserted before the
                # default kicked in (belt-and-suspenders — DEFAULT covers new
                # inserts, this covers pre-existing rows).
                db.execute(text(
                    "UPDATE users SET product_selection = 'both' "
                    "WHERE product_selection IS NULL;"
                ))
            if not column_exists('team_invites', 'invite_type'):
                logger.info("Adding 'invite_type' column to team_invites...")
                db.execute(text(
                    "ALTER TABLE team_invites ADD COLUMN invite_type VARCHAR "
                    "DEFAULT 'team' NOT NULL;"
                ))
            for tbl in ('published_posts', 'drafts', 'scheduled_posts'):
                if not column_exists(tbl, 'dna_product_id'):
                    logger.info(f"Adding 'dna_product_id' column to {tbl}...")
                    db.execute(text(f"ALTER TABLE {tbl} ADD COLUMN dna_product_id VARCHAR;"))

            # 7c1. Connection assignment column (admin can route one of their
            #      connected social accounts to a team member).
            if not column_exists('social_accounts', 'assigned_to_user_id'):
                logger.info("Adding 'assigned_to_user_id' column to social_accounts...")
                db.execute(text(
                    "ALTER TABLE social_accounts ADD COLUMN assigned_to_user_id BIGINT REFERENCES users(id);"
                ))

            # 7c2. Reconnect requests (member asks admin to refresh a token).
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS reconnect_requests (
                    id SERIAL PRIMARY KEY,
                    social_account_id BIGINT REFERENCES social_accounts(id) ON DELETE CASCADE,
                    requester_user_id BIGINT REFERENCES users(id),
                    reason VARCHAR,
                    status VARCHAR DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fulfilled_at TIMESTAMP
                );
            """))

            # 7c. Team invites table (Phase 1b — invite flow).
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS team_invites (
                    id SERIAL PRIMARY KEY,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    email VARCHAR NOT NULL,
                    invited_by BIGINT REFERENCES users(id),
                    assigned_dna_product_id VARCHAR,
                    expires_at TIMESTAMP,
                    used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_team_invites_email "
                "ON team_invites(email);"
            ))
            if not column_exists('team_invites', 'assigned_connection_ids'):
                logger.info("Adding 'assigned_connection_ids' column to team_invites...")
                db.execute(text(
                    "ALTER TABLE team_invites ADD COLUMN assigned_connection_ids JSONB;"
                ))
            if not column_exists('team_invites', 'assigned_dna_product_ids'):
                logger.info("Adding 'assigned_dna_product_ids' (list) column to team_invites...")
                db.execute(text(
                    "ALTER TABLE team_invites ADD COLUMN assigned_dna_product_ids JSONB;"
                ))
                db.execute(text(
                    "UPDATE team_invites SET assigned_dna_product_ids = "
                    "jsonb_build_array(assigned_dna_product_id) "
                    "WHERE assigned_dna_product_id IS NOT NULL "
                    "AND assigned_dna_product_ids IS NULL;"
                ))
            if not column_exists('team_invites', 'invited_name'):
                logger.info("Adding 'invited_name' column to team_invites...")
                db.execute(text(
                    "ALTER TABLE team_invites ADD COLUMN invited_name VARCHAR;"
                ))

            # Team member profile fields (Apr 2026) — external company +
            # physical location so admins can filter analytics by region.
            # Mirrored on both users (accepted members) and team_invites
            # (pre-filled at invite time, copied across on acceptance).
            for _tbl in ("users", "team_invites"):
                for _col, _sql in [
                    ("member_company_name", f"ALTER TABLE {_tbl} ADD COLUMN member_company_name VARCHAR;"),
                    ("country",             f"ALTER TABLE {_tbl} ADD COLUMN country VARCHAR;"),
                    ("country_name",        f"ALTER TABLE {_tbl} ADD COLUMN country_name VARCHAR;"),
                    ("state",               f"ALTER TABLE {_tbl} ADD COLUMN state VARCHAR;"),
                    ("state_name",          f"ALTER TABLE {_tbl} ADD COLUMN state_name VARCHAR;"),
                    ("city",                f"ALTER TABLE {_tbl} ADD COLUMN city VARCHAR;"),
                    ("pin_code",            f"ALTER TABLE {_tbl} ADD COLUMN pin_code VARCHAR;"),
                ]:
                    if not column_exists(_tbl, _col):
                        logger.info(f"Adding '{_col}' column to {_tbl}...")
                        db.execute(text(_sql))

            # Add user_otps table if missing
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS user_otps (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR,
                    otp_code VARCHAR,
                    purpose VARCHAR,
                    expires_at TIMESTAMP,
                    is_used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))

            # Agent Post v2: generated_campaigns (gallery source of truth)
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS generated_campaigns (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    campaign_id VARCHAR(32) UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    headline VARCHAR,
                    subheading VARCHAR,
                    cta VARCHAR,
                    primary_color VARCHAR,
                    aspect_ratio VARCHAR,
                    visuals JSONB
                );
            """))
            db.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_generated_campaigns_created_at "
                "ON generated_campaigns(created_at);"
            ))

            # Phase 1: motion-graphics video pipeline. One row per video
            # generation request the Agent Post UI fires off. JSONB stores
            # the storyboard so we can re-render or remix later without
            # re-running the agent.
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS generated_videos (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    campaign_brief TEXT NOT NULL,
                    template VARCHAR DEFAULT 'brand_promo',
                    aspect_ratio VARCHAR DEFAULT '9:16',
                    duration_sec INTEGER DEFAULT 30,
                    dna_product_id VARCHAR,
                    status VARCHAR DEFAULT 'pending',
                    storyboard_json JSONB,
                    video_url VARCHAR,
                    error_message TEXT,
                    render_seconds INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                );
            """))
            db.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_generated_videos_user_created "
                "ON generated_videos(user_id, created_at DESC);"
            ))

            # 9. Create teams table
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS teams (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR,
                    owner_id BIGINT REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))

            # 9b. NEXUS knowledge-base table migration (2026-05-26)
            #
            # Pinecone migration: lightens nexus_product_assets to a
            # metadata row only. New uploads store raw files in S3 +
            # vectors in Pinecone. Adds 7 columns idempotently. The old
            # `raw_text` column is kept (nullable) during the migration
            # window — dropped later by scripts/drop_old_kb_tables.py
            # along with the nexus_knowledge_embeddings table.
            #
            # Wrapped in its own try/except so a single failed ALTER
            # doesn't poison the surrounding sync_db_schema transaction.
            # ── drafts.thumbnail_url (2026-06-26) ───────────────────
            # Store a static PNG preview URL on each draft so the Drafts
            # UI can render a fast, reliable <img> instead of fighting
            # Chrome's flaky PDF iframe viewer for carousel previews.
            # IMPORTANT: commit() immediately so the ALTER persists even
            # if a LATER migration in this block aborts the transaction
            # (e.g. nexus_lead_sequences uq_lead_seq dup). Without this
            # commit the column gets rolled back and the Drafts query
            # 500s with "column drafts.thumbnail_url does not exist".
            try:
                from sqlalchemy import text as _sql_text
                db.execute(_sql_text(
                    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;"
                ))
                db.commit()
                logger.info("drafts.thumbnail_url column synced")
            except Exception as _e:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning("drafts.thumbnail_url sync skipped: %s", _e)

            # Same sync for published_posts so the Published feed can also
            # render carousel previews as native <img> instead of the
            # ugly PDF-icon-plus-UUID-filename tile.
            try:
                from sqlalchemy import text as _sql_text
                db.execute(_sql_text(
                    "ALTER TABLE published_posts ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;"
                ))
                db.commit()
                logger.info("published_posts.thumbnail_url column synced")
            except Exception as _e:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning("published_posts.thumbnail_url sync skipped: %s", _e)

            # slide_thumbnail_urls — JSON-encoded list of EVERY slide's
            # PNG URL so the Drafts modal can navigate slides 1..N as
            # clean <img> tags (no pdf.js worker dependency).
            try:
                from sqlalchemy import text as _sql_text
                db.execute(_sql_text("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS slide_thumbnail_urls TEXT;"))
                db.commit()
                logger.info("drafts.slide_thumbnail_urls column synced")
            except Exception as _e:
                try: db.rollback()
                except Exception: pass
                logger.warning("drafts.slide_thumbnail_urls sync skipped: %s", _e)

            # Same pair of columns for scheduled_posts so the Scheduled tab
            # can render carousel PDF previews as a native <img> instead of
            # the generic "PDF DOCUMENT CAROUSEL" tile. Was missing — Draft
            # and PublishedPost got these on the previous migration but the
            # scheduled_posts table was overlooked.
            try:
                from sqlalchemy import text as _sql_text
                db.execute(_sql_text(
                    "ALTER TABLE scheduled_posts ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;"
                ))
                db.commit()
                logger.info("scheduled_posts.thumbnail_url column synced")
            except Exception as _e:
                try: db.rollback()
                except Exception: pass
                logger.warning("scheduled_posts.thumbnail_url sync skipped: %s", _e)

            try:
                from sqlalchemy import text as _sql_text
                db.execute(_sql_text(
                    "ALTER TABLE scheduled_posts ADD COLUMN IF NOT EXISTS slide_thumbnail_urls TEXT;"
                ))
                db.commit()
                logger.info("scheduled_posts.slide_thumbnail_urls column synced")
            except Exception as _e:
                try: db.rollback()
                except Exception: pass
                logger.warning("scheduled_posts.slide_thumbnail_urls sync skipped: %s", _e)

            try:
                from sqlalchemy import text as _sql_text
                _kb_migrations = [
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS workspace_id BIGINT;",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS s3_key VARCHAR(1024);",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS s3_url VARCHAR(2048);",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'indexed';",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS chunks_indexed INTEGER NOT NULL DEFAULT 0;",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS last_error TEXT;",
                    "ALTER TABLE nexus_product_assets ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMP;",
                    "CREATE INDEX IF NOT EXISTS idx_assets_workspace_product ON nexus_product_assets(workspace_id, product_id);",
                ]
                for stmt in _kb_migrations:
                    try:
                        db.execute(_sql_text(stmt))
                    except Exception as _kb_e:
                        logger.warning("KB migration step skipped: %s -> %s", stmt[:80], _kb_e)
                logger.info("nexus_product_assets KB columns synced")
            except Exception as _e:  # noqa: BLE001
                logger.warning("KB migration block failed (non-fatal): %s", _e)

            # 9c. NEXUS global-leads enrichment columns (2026-05-29)
            #
            # Apollo's search response always returns person.city/state/
            # country and organization.industry, but we never persisted
            # them. The new LeadsTable UI surfaces a Location column
            # ("Bengaluru, India") and an Industry column — those need
            # the columns below. Idempotent ALTER TABLE inside a
            # dedicated try/except so a single failure can't poison the
            # surrounding sync_db_schema work.
            try:
                from sqlalchemy import text as _sql_text  # idempotent reimport
                # 2026-06-04 — Pre-check the catalog (cheap, LOCK-FREE) and only
                # ALTER columns/indexes that are genuinely MISSING. Why:
                # `ADD COLUMN IF NOT EXISTS` still needs a brief ACCESS EXCLUSIVE
                # lock to no-op, and on a busy table (nexus_global_leads is
                # read/written by live discovery) that lock can TIME OUT — and a
                # timed-out statement aborts the whole transaction, cascading
                # "current transaction is aborted" to every following step. By
                # skipping already-present objects, the common case issues NO DDL
                # at all (no lock, no timeout). Genuinely-missing ones still get
                # added, each inside its own SAVEPOINT so one failure (e.g. a
                # transient lock timeout) can't poison the rest.
                _existing_cols = {
                    r[0] for r in db.execute(_sql_text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'nexus_global_leads'"
                    )).fetchall()
                }
                _existing_idx = {
                    r[0] for r in db.execute(_sql_text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename = 'nexus_global_leads'"
                    )).fetchall()
                }
                _gl_cols = [
                    ("person_city", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS person_city VARCHAR(120);"),
                    ("person_state", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS person_state VARCHAR(120);"),
                    ("person_country", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS person_country VARCHAR(120);"),
                    ("organization_industry", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS organization_industry VARCHAR(160);"),
                    # 2026-06-02 — phone number returned by Apollo
                    # /people/bulk_match (NULL when Apollo doesn't return one).
                    ("phone", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS phone VARCHAR(40);"),
                    # 2026-06-05 — LinkedIn headline (Apollo already returns it;
                    # used only to personalize outreach openers).
                    ("linkedin_headline", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS linkedin_headline VARCHAR(512);"),
                    # 2026-06-23 — Apollo stable person id (canonical dedup anchor).
                    ("apollo_person_id", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS apollo_person_id VARCHAR(64);"),
                    # 2026-07-15 — referral provenance. Populated when a reply
                    # names a different point of contact and that person is
                    # auto-created as a new lead (reply-aware follow-up
                    # regeneration feature). NULL for normally-sourced leads.
                    ("source_lead_id", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS source_lead_id BIGINT;"),
                    ("source_message_id", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS source_message_id BIGINT;"),
                    # 2026-07-23 — full list of captured phone numbers (scenario
                    # 9). A reply can carry more than one, and later replies add
                    # more; `phone` above stays the primary. Backfilled below.
                    ("phones", "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS phones JSONB NOT NULL DEFAULT '[]'::jsonb;"),
                ]
                _ran = 0
                _phones_added = "phones" not in _existing_cols
                for _col, _stmt in _gl_cols:
                    if _col in _existing_cols:
                        continue
                    try:
                        with db.begin_nested():  # SAVEPOINT — failure rolls back only this step
                            db.execute(_sql_text(_stmt))
                        _ran += 1
                    except Exception as _gl_e:
                        logger.warning("global_leads migration step skipped: %s -> %s", _stmt[:80], _gl_e)
                # One-time backfill: seed `phones` from the existing primary
                # `phone` so a lead's single number also appears in the list.
                # Only runs the first time the column is added (guarded), and is
                # itself idempotent (WHERE phones is still empty).
                if _phones_added:
                    try:
                        with db.begin_nested():
                            db.execute(_sql_text(
                                "UPDATE nexus_global_leads "
                                "SET phones = jsonb_build_array(phone) "
                                "WHERE phone IS NOT NULL AND phone <> '' "
                                "AND (phones IS NULL OR phones = '[]'::jsonb);"
                            ))
                        _ran += 1
                    except Exception as _gl_e:
                        logger.warning("global_leads phones backfill skipped: %s", _gl_e)
                if "idx_global_leads_industry" not in _existing_idx:
                    _idx_stmt = "CREATE INDEX IF NOT EXISTS idx_global_leads_industry ON nexus_global_leads(organization_industry);"
                    try:
                        with db.begin_nested():
                            db.execute(_sql_text(_idx_stmt))
                        _ran += 1
                    except Exception as _gl_e:
                        logger.warning("global_leads index step skipped: %s -> %s", _idx_stmt[:80], _gl_e)
                logger.info("nexus_global_leads enrichment columns synced (%d new step(s))", _ran)
            except Exception as _e:  # noqa: BLE001
                logger.warning("global_leads migration block failed (non-fatal): %s", _e)

            # 2026-06-04 — OAuth mailbox connector columns on
            # nexus_conversation_accounts. Populated when a user connects
            # their own Outlook/Gmail mailbox so the app can send FROM it
            # (instead of the shared Apollo/Resend sender). Additive +
            # idempotent; isolated try/except so it can't poison sibling
            # schema sync work.
            try:
                from sqlalchemy import text as _sql_text  # idempotent reimport
                _ca_migrations = [
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS access_token TEXT;",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMP;",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS brand_id BIGINT;",
                    # 2026-06-06 — connector send-rotation columns. Present in
                    # the model + added manually in dev; include here so a FRESH
                    # deploy (existing table) creates them too. Additive + idempotent.
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS refresh_token TEXT;",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS daily_send_limit INTEGER DEFAULT 50;",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS daily_send_count INTEGER DEFAULT 0;",
                    "ALTER TABLE nexus_conversation_accounts ADD COLUMN IF NOT EXISTS last_reset_at TIMESTAMP DEFAULT NOW();",
                    """
                    CREATE TABLE IF NOT EXISTS nexus_sender_brands (
                        id BIGSERIAL PRIMARY KEY,
                        workspace_id BIGINT NOT NULL,
                        name VARCHAR(255),
                        url TEXT,
                        url_norm VARCHAR(512),
                        entity_type VARCHAR(16) DEFAULT 'product',
                        created_at TIMESTAMP DEFAULT NOW(),
                        CONSTRAINT uq_sender_brand UNIQUE (workspace_id, url_norm, entity_type)
                    );
                    """,
                    "CREATE INDEX IF NOT EXISTS ix_nexus_sender_brands_ws ON nexus_sender_brands(workspace_id);",
                    # 2026-06-06 — one sequence per (workspace, lead, campaign).
                    # Makes enrollment RACE-PROOF (two schedulers can't create a
                    # duplicate). Skipped by the surrounding try/except if it
                    # already exists or if a legacy table still has duplicates
                    # (dedupe first, then this applies). Enrollment code uses
                    # ON CONFLICT / IntegrityError handling to stay graceful.
                    "ALTER TABLE nexus_lead_sequences ADD CONSTRAINT uq_lead_seq UNIQUE (workspace_id, lead_id, campaign_id);",
                ]
                for stmt in _ca_migrations:
                    try:
                        db.execute(_sql_text(stmt))
                    except Exception as _ca_e:
                        logger.warning("conversation_accounts migration step skipped: %s -> %s", stmt[:80], _ca_e)
                # Per-lead generated emails table (one row per cadence step).
                try:
                    db.execute(_sql_text(
                        """
                        CREATE TABLE IF NOT EXISTS nexus_lead_emails (
                            id BIGSERIAL PRIMARY KEY,
                            workspace_id BIGINT NOT NULL,
                            campaign_id BIGINT,
                            lead_id BIGINT,
                            lead_sequence_id BIGINT NOT NULL
                                REFERENCES nexus_lead_sequences(id) ON DELETE CASCADE,
                            step INTEGER NOT NULL,
                            kind VARCHAR(32),
                            subject TEXT,
                            body TEXT,
                            real_result TEXT,
                            status VARCHAR(16) DEFAULT 'pending',
                            sent_at TIMESTAMP,
                            provider_message_id VARCHAR(255),
                            created_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW(),
                            CONSTRAINT uq_lead_email_seq_step UNIQUE (lead_sequence_id, step)
                        );
                        """
                    ))
                    db.execute(_sql_text(
                        "CREATE INDEX IF NOT EXISTS ix_nexus_lead_emails_seq_step "
                        "ON nexus_lead_emails(lead_sequence_id, step);"
                    ))
                    # Sticky sending mailbox per lead (mailbox rotation).
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS "
                        "conversation_account_id BIGINT;"
                    ))
                    # 2026-06-05 — rich-email columns: per-lead personalized
                    # opener + which role variant rendered the initial email.
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_emails ADD COLUMN IF NOT EXISTS opener TEXT;"
                    ))
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_emails ADD COLUMN IF NOT EXISTS variant_key VARCHAR(80);"
                    ))
                    # 2026-07-15 — reply-aware follow-up regeneration. Tracks
                    # which inbound message a pending step's current content
                    # was last regenerated from, so a fresh reply in the same
                    # thread can trigger regeneration again (see sequencer.py
                    # regenerate_pending_followups). needs_review gates the
                    # FIRST send to a referral lead created from a reply that
                    # named a different point of contact.
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_emails ADD COLUMN IF NOT EXISTS regenerated_from_message_id BIGINT;"
                    ))
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_emails ADD COLUMN IF NOT EXISTS regenerated_at TIMESTAMP;"
                    ))
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS needs_review BOOLEAN DEFAULT FALSE;"
                    ))
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS needs_review_reason TEXT;"
                    ))
                    # 2026-07-21 — fixed cadence dates (email_flow_plan_3.md).
                    # All 4 steps' send dates are computed ONCE at enrollment
                    # (sequencer.py::compute_step_schedule) and stored here,
                    # keyed by step order as a string ("0".."3") since JSONB
                    # object keys are always strings. next_action_at still
                    # drives the actual due-query, but is now sourced FROM
                    # this fixed schedule after a send, instead of being
                    # recomputed as `now + delay_days` (which let a late send
                    # compound delay into every later step). Nullable — rows
                    # enrolled before this column existed keep the old
                    # rolling behavior as a fallback (see sequencer.py).
                    db.execute(_sql_text(
                        "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS step_schedule JSONB;"
                    ))
                    # One-time backfill for rows enrolled before this column
                    # existed — without it, Follow-up 2/Closing showed "Not
                    # yet" in the Flow tab until the frontend's own chained
                    # fallback kicked in (it now does that too, but a real
                    # stored schedule is the correct fix, not just a client-
                    # side guess). Idempotent: only touches step_schedule IS
                    # NULL rows, so this is a fast no-op after the first run.
                    # Anchored on next_action_at (preserves whatever's
                    # already correctly due) rather than enrolled_at, so an
                    # existing real postponement isn't silently discarded.
                    try:
                        import json as _json
                        from datetime import timedelta as _timedelta

                        _backfill_rows = db.execute(_sql_text(
                            """
                            SELECT ls.id, ls.current_step, ls.next_action_at,
                                   ls.enrolled_at, s.steps
                              FROM nexus_lead_sequences ls
                              JOIN nexus_sequences s ON s.id = ls.sequence_id
                             WHERE ls.step_schedule IS NULL
                               AND ls.status IN ('active', 'paused')
                            """
                        )).fetchall()
                        for _bfr in _backfill_rows:
                            _bfr_steps = _bfr[4] or []
                            _bfr_cur = _bfr[1] or 0
                            if _bfr_cur >= len(_bfr_steps):
                                continue
                            _bfr_anchor = _bfr[2] or _bfr[3]
                            if not _bfr_anchor:
                                continue
                            _bfr_schedule = {}
                            _bfr_running = _bfr_anchor
                            for _bfi in range(_bfr_cur, len(_bfr_steps)):
                                if _bfi > _bfr_cur:
                                    _bfr_running = _bfr_running + _timedelta(
                                        days=int(_bfr_steps[_bfi].get("delay_days", 1) or 0)
                                    )
                                _bfr_schedule[str(_bfr_steps[_bfi].get("order", _bfi))] = _bfr_running.isoformat()
                            db.execute(_sql_text(
                                "UPDATE nexus_lead_sequences SET step_schedule = CAST(:s AS jsonb) WHERE id = :id"
                            ), {"s": _json.dumps(_bfr_schedule), "id": _bfr[0]})
                        if _backfill_rows:
                            logger.info("step_schedule backfill: %d row(s) updated", len(_backfill_rows))
                    except Exception as _bf_e:
                        logger.warning("step_schedule backfill skipped: %s", _bf_e)
                    # 2026-07-21 — closes a race condition where two
                    # concurrent inbound-mail poller runs (the sync throttle
                    # in routers/scheduler.py is an in-process timestamp,
                    # not a real cross-process/cross-container lock) could
                    # both pass the _already_ingested() pre-check for the
                    # same message before either had committed, creating
                    # two nexus_inbound_messages rows — in two different
                    # threads — for the identical email (duplicate "Reply
                    # from X" cards in the UI). A real DB constraint is the
                    # only thing that actually closes a check-then-insert
                    # race; the app now catches the resulting IntegrityError
                    # and treats it as "already ingested" (see
                    # routers/inbound.py::_ingest_inbound_message).
                    db.execute(_sql_text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_msg_message_id_header "
                        "ON nexus_inbound_messages(message_id_header) "
                        "WHERE message_id_header IS NOT NULL AND message_id_header != '';"
                    ))
                except Exception as _le_e:
                    logger.warning("nexus_lead_emails migration skipped: %s", _le_e)
                logger.info("nexus_conversation_accounts connector columns synced")
            except Exception as _e:  # noqa: BLE001
                logger.warning("conversation_accounts migration block failed (non-fatal): %s", _e)

            # 10. NEXUS port — registry-driven migrations.
            #
            # Each phase ships its idempotent CREATE TABLE / CREATE INDEX
            # statements as an entry in nexus.migrations.ALL_PHASES. The
            # runner applies them all in order. Phases that ship later
            # only need to append to that list; they do NOT edit this file.
            #
            # Gated by NEXUS_ENABLED so PIPELYT environments that haven't
            # opted into the port pay zero schema cost. Idempotent either
            # way (CREATE TABLE IF NOT EXISTS).
            if NEXUS_ENABLED:
                # Set NEXUS_SKIP_MIGRATIONS=true in env after the first cold
                # start so subsequent boots skip the ~2-min phase loop.
                # Flip to false (or unset) when you change a phase*.py file.
                if os.getenv("NEXUS_SKIP_MIGRATIONS", "").lower() == "true":
                    logger.info("NEXUS_SKIP_MIGRATIONS=true — skipping migrations")
                else:
                    logger.info("NEXUS_ENABLED=true — applying NEXUS migrations registry")
                    from nexus.migrations import apply_all as _apply_nexus_migrations
                    _apply_nexus_migrations(db, logger)

            db.commit()
            logger.info("Database schema sync complete (or skipped if columns exist).")
        except Exception as e:
            # If we hit a lock timeout or other migration error, log it but don't stop the server
            logger.warning(f"Schema sync skipped (likely column already exists or lock timeout): {e}")
            db.rollback()
        finally:
            db.close()

        seed_user()
    except Exception as e:
        logger.error(f"Critical Database initialization failed: {e}")

# Only run auto-sync if explicitly requested or in local development
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("AUTO_SYNC_DB") == "true":
    sync_db_schema()


# ─────────────────────────────────────────────────────────────────────────────
# Local-dev background sequencer loop
#
# Lambda has no built-in "always-on" worker — in production, EventBridge fires
# `lambda_pinger.py` every minute and that's what drains the Nexus sequencer.
# That doesn't run locally, so `python main.py` without this loop means
# campaigns get enrolled but no email/LinkedIn draft ever fires.
#
# This task ticks the sequencer every 60s WHILE running locally — it never
# spins up inside Lambda (EventBridge already handles it there).
#
# Calls into `process_due_sequences` + `process_linkedin_drafts` directly,
# so SCHEDULER_SECRET / HTTP auth are irrelevant here. Each tick gets its
# own DB session and swallows any per-iteration exception so a transient
# DB hiccup doesn't kill the loop.
#
# Gated by NEXUS_AUTO_SEQUENCER env var (default OFF) so the operator can
# decide when to flip the switch — turning it on while leads are already
# enrolled WILL send their first email on the very next tick. TEST_REDIRECT_EMAIL
# is your safety net during dev — it forwards every send to a single inbox.
# ─────────────────────────────────────────────────────────────────────────────


_LOCAL_SEQUENCER_TICK_SEC = 60
# Local-dev cap: bounds the blast radius of any single tick. Even if
# every safety check above fails, at most this many sends fire per minute.
# Production Lambda uses the higher default in process_due_sequences.
# Set to 15 (slightly above the 11-lead z-ninth batch) so a single tick
# drains the planned re-send. Keep small in general — this is a safety
# rail against runaway loops, not a throughput knob.
_LOCAL_SEQUENCER_MAX_ROWS = 15
# MS Bookings polling runs once every N ticks (5 ticks * 60s = ~5 min).
# Polling Graph more aggressively burns the Azure AD token cache and adds
# zero freshness for human-paced demo bookings.
_MS_BOOKINGS_SYNC_EVERY_TICKS = 5
# Auto-Reply worker cadence — every 5 ticks (5 * 60s = 5 min). Any
# faster and we start denting LinkedIn's 500/day Dev-Tier bucket for
# multi-user tenants; any slower and the "instant reply" promise
# starts to feel laggy on active posts.
_AUTO_REPLY_EVERY_TICKS = 5


async def _local_sequencer_loop():
    import asyncio as _asyncio

    log = logging.getLogger("pipelyt.nexus.local_sequencer")
    log.info(
        "nexus local sequencer loop started — ticking every %ss",
        _LOCAL_SEQUENCER_TICK_SEC,
    )
    tick_count = 0
    while True:
        try:
            await _asyncio.sleep(_LOCAL_SEQUENCER_TICK_SEC)
            tick_count += 1
            # Lazy-import so a misconfigured Nexus install doesn't break
            # PIPELYT startup. Same pattern the scheduler router uses.
            from nexus.services.sequencer import (
                process_due_sequences,
                process_linkedin_drafts,
            )
            from core.database import SessionLocal

            db = SessionLocal()
            try:
                # Offload the synchronous, Gemini-heavy sequencer passes
                # to a worker thread so they DON'T freeze the FastAPI
                # event loop. Without this, a tick with N leads (each
                # ~3s of blocking Gemini calls) makes every concurrent
                # HTTP request — including /journey/leads — wait until
                # the tick finishes. That's why the GTM Journey was
                # appearing blank in local until the tick completed.
                #
                # On Lambda this code path doesn't run (the if-guard in
                # _maybe_start_local_sequencer skips it for Lambda), so
                # this is a pure local-dev quality-of-life fix.
                email_result = await _asyncio.to_thread(
                    process_due_sequences, db, _LOCAL_SEQUENCER_MAX_ROWS,
                )
                linkedin_result = await _asyncio.to_thread(
                    process_linkedin_drafts, db,
                )
                if email_result.get("count") or linkedin_result.get("generated"):
                    log.info(
                        "NEXUS RUN [4/4 SENDING] this tick — %d email step(s) sent, "
                        "%d LinkedIn draft(s) generated.",
                        email_result.get("count", 0),
                        linkedin_result.get("generated", 0),
                    )

                # 2026-06-03 — LOCAL: the MS Bookings sync hits Microsoft
                # Graph (graph.microsoft.com/.../bookingBusinesses/...).
                # Locally there are usually no Graph creds, so it fails
                # every tick and spams a WARNING. Gate it behind
                # NEXUS_MS_BOOKINGS_SYNC — set it to "true" only where
                # Graph is actually configured (staging / prod). Default
                # OFF keeps the local dev log clean. The email + LinkedIn
                # sequencer above still runs regardless.
                _ms_sync_on = (
                    str(os.environ.get("NEXUS_MS_BOOKINGS_SYNC", "")).strip().lower()
                    == "true"
                )
                if _ms_sync_on and tick_count % _MS_BOOKINGS_SYNC_EVERY_TICKS == 0:
                    try:
                        from nexus.services.ms_bookings_sync import (
                            process_demo_briefings,
                            sync_ms_bookings,
                        )

                        bookings_result = await sync_ms_bookings(db)
                        if (
                            bookings_result.get("created")
                            or bookings_result.get("errors")
                        ):
                            log.info("ms_bookings sync: %s", bookings_result)
                        # After new bookings land, draft pre-call briefings
                        # so reps don't have to wait for an on-demand call.
                        briefing_result = process_demo_briefings(db, max_rows=3)
                        if briefing_result.get("generated"):
                            log.info("demo briefings: %s", briefing_result)
                    except Exception:
                        log.exception("ms_bookings sync failed (continuing)")

                # ─── Reputation Auto-Reply worker ────────────────────
                # Every _AUTO_REPLY_EVERY_TICKS ticks, scan every user
                # with `auto_reply_enabled=True` and post AI replies to
                # any new comments. Runs in a worker thread — the
                # worker does its own DB session management + heavy
                # Gemini calls, and we don't want the FastAPI event
                # loop blocked.
                if tick_count % _AUTO_REPLY_EVERY_TICKS == 0:
                    try:
                        from services.auto_reply_worker import process_auto_replies
                        ar_result = await _asyncio.to_thread(process_auto_replies)
                        if (ar_result.get("replied")
                                or ar_result.get("failed")
                                or ar_result.get("candidates")):
                            log.info(
                                "AUTO_REPLY tick — users=%d candidates=%d "
                                "replied=%d failed=%d",
                                ar_result.get("users_processed", 0),
                                ar_result.get("candidates", 0),
                                ar_result.get("replied", 0),
                                ar_result.get("failed", 0),
                            )
                    except Exception:
                        log.exception("auto_reply worker failed (continuing)")
            finally:
                db.close()
        except _asyncio.CancelledError:
            log.info("nexus local sequencer loop cancelled")
            raise
        except Exception:
            log.exception("nexus local sequencer tick failed (continuing)")


@app.on_event("startup")
async def _maybe_start_local_sequencer():
    """Boot the background sequencer loop when running locally and
    NEXUS_AUTO_SEQUENCER=true. Idempotent — only starts once."""
    import asyncio as _asyncio

    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return  # Lambda — EventBridge drives the tick, not us.
    if not NEXUS_ENABLED:
        return
    if str(os.environ.get("NEXUS_AUTO_SEQUENCER", "")).strip().lower() != "true":
        logger.info(
            "nexus local sequencer disabled (set NEXUS_AUTO_SEQUENCER=true to enable)"
        )
        return
    _asyncio.create_task(_local_sequencer_loop())
    logger.info("nexus local sequencer scheduled")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    return await call_next(request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)