#!/usr/bin/env python3
# =============================================================================
#  NEXUS  —  MongoDB Atlas  →  PostgreSQL  Data Migration
#  Version : 1.0
#  Author  : Pipelyt / Neuzen AI
# =============================================================================
#
#  WHAT THIS SCRIPT DOES
#  ─────────────────────
#  Copies every NEXUS collection from MongoDB Atlas into the nexus_* tables
#  inside the PostgreSQL database that Pipelyt already uses.
#

#
#  HOW TO USE  (follow every step, do not skip any)
#  ─────────────────────────────────────────────────
#  STEP 1 : Install Python dependencies
#             pip install pymongo psycopg2-binary
#
#  STEP 2 : Fill in the six credential values below  (CREDENTIALS section)
#
#  STEP 3 : Make sure your computer's IP is whitelisted in MongoDB Atlas
#             Atlas dashboard → Network Access → Add IP Address
#
#  STEP 4 : Run a DRY RUN first — this prints counts but writes NOTHING
#             python nexus_data_migration.py --dry-run
#
#  STEP 5 : Review the output. If all counts look right, run for real
#             python nexus_data_migration.py
#
#  OPTIONAL : Migrate only one phase at a time (useful for debugging)
#             python nexus_data_migration.py --only global_leads
#             python nexus_data_migration.py --only campaigns
#
#  NOTES
#  ─────
#  •  ONE-SHOT MIGRATION — no schema changes:
#       This script writes ONLY INSERTs into the existing nexus_* tables. It
#       does NOT run any ALTER TABLE / ADD COLUMN / CREATE INDEX against the
#       prod schema. Schema is owned by the deployed NEXUS backend's
#       migrations (`apps/backend/nexus/migrations/phase*.py`); this script
#       just funnels Mongo data into the tables the backend already created.
#
#       Implications:
#       - Run this ONCE per environment. Running a phase twice creates
#         duplicate rows for tables that have no natural unique key (most
#         notably workspaces, products, campaigns, sequences, lead_sequences,
#         inbound_threads, demo_bookings, intent_signals, unmatched_replies).
#       - If a phase crashes mid-run, the whole phase is rolled back by the
#         per-phase transaction below, so you can retry that one phase with
#         `--only PHASE` without duplicating earlier successful phases.
#       - Tables with natural unique keys (global_leads.email, apollo_cache
#         query_hash, conversation_accounts.email_address, inbound_leads
#         (workspace_id,email), suppression (workspace_id,email_lower),
#         personalization_cache composite PK, voice_calls.twilio_call_sid,
#         outreach (lead_id,campaign_id), settings.workspace_id, leads
#         scope-UNIQUE) ARE idempotent — they use ON CONFLICT DO NOTHING.
#  •  Phase 24 (inbox) and Phase 26 (processed_gmail) are SKIPPED — the
#     Postgres tables model different things than the Mongo collections and
#     cannot be 1:1 transferred. They populate organically from new traffic
#     on the live NEXUS deployment.
#  •  Failures in one phase are logged and skipped; other phases continue.
#  •  Per-phase skipped-row counters are summarized at the end so silent data
#     drops (missing FKs, NOT NULL violations, empty keys) are visible.
#  •  The script must be run AFTER the Pipelyt backend has been deployed with
#     NEXUS_ENABLED=true and has had at least one cold-start (so the nexus_*
#     tables already exist in PostgreSQL, including the Phase 6 ALTER columns
#     `priority` on nexus_global_leads and the user-profile preference fields).
#
# =============================================================================


# =============================================================================
#  ██████╗ ██████╗ ███████╗██████╗ ███████╗███╗   ██╗████████╗██╗ █████╗ ██╗
#  ██╔════╝██╔══██╗██╔════╝██╔══██╗██╔════╝████╗  ██║╚══██╔══╝██║██╔══██╗██║
#  ██║     ██████╔╝█████╗  ██║  ██║█████╗  ██╔██╗ ██║   ██║   ██║███████║██║
#  ██║     ██╔══██╗██╔══╝  ██║  ██║██╔══╝  ██║╚██╗██║   ██║   ██║██╔══██║██║
#  ╚██████╗██║  ██║███████╗██████╔╝███████╗██║ ╚████║   ██║   ██║██║  ██║███████╗
#   ╚═════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═╝╚══════╝
#
#  FILL IN ALL SIX VALUES BELOW BEFORE RUNNING
# =============================================================================

# Python future imports MUST be at the very top (after only comments/docstring).
from __future__ import annotations

# ── MongoDB Atlas (SOURCE — read-only) ───────────────────────────────────────
# FILL THIS IN: Get it from the NEXUS legacy server environment.
# It was stored as MONGO_URI in the nexus-legacy/server/.env file.
# Format: mongodb+srv://USERNAME:PASSWORD@cluster.mongodb.net/DATABASE
# Also add your IP to MongoDB Atlas → Network Access before running.
MONGO_URI = "mongodb+srv://saisidd07:nexus123@cluster0.xiwxvnk.mongodb.net/?appName=Cluster0"

# The MongoDB database name — confirmed: cluster0 / spenzo
MONGO_DB_NAME = "spenzo"

# ── PostgreSQL / AWS RDS (DESTINATION — nexus_* tables only) ─────────────────
# Pre-filled from apps/backend/.env (DATABASE_URL). Do NOT commit this file.
PG_HOST     = "pipelyt-db.cmdi2a884dca.us-east-1.rds.amazonaws.com"
PG_PORT     = 5432
PG_DATABASE = "postgres"
PG_USER     = "postgres"
PG_PASSWORD = "NeuZenAI"

# =============================================================================
#  END OF CREDENTIALS — do not edit below unless you know what you're doing
# =============================================================================


# =============================================================================
#  USER EMAIL REMAP — map Mongo emails to PIPELYT user emails
# =============================================================================
# When a Mongo user's email doesn't exist in PIPELYT, the migration would
# normally skip them and their data cascades to skip too. This dict reroutes
# the Mongo email to an existing PIPELYT user.
#
# Multiple Mongo users CAN map to the same PIPELYT user — all their data
# ends up under that one PIPELYT account (workspaces stay separate; the
# nexus_workspaces.owner_id column allows duplicates).
USER_EMAIL_REMAP = {
    # Real Mongo users → real PIPELYT account
    "admin@gmail.com":                  "contact@neuzenai.com",
    "siddharth@gmail.com":              "contact@neuzenai.com",
    # Test Mongo users → PIPELYT test account (cleanup later with one
    # `DELETE FROM nexus_workspaces WHERE owner_id = <test_user_id>;`)
    "test1@gmail.com":                  "test@pipelyt.com",
    "test@localhost.local":             "test@pipelyt.com",
    "e2e_1777541576388@nexustest.dev":  "test@pipelyt.com",
    "e2e_1777541651612@nexustest.dev":  "test@pipelyt.com",
}

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── Dependency check ──────────────────────────────────────────────────────────
_missing = []
try:
    import pymongo
    from pymongo import ReadPreference
    from bson import ObjectId
except ImportError:
    _missing.append("pymongo")

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    _missing.append("psycopg2-binary")

if _missing:
    print("\n  ERROR: Missing Python packages. Run:\n")
    for pkg in _missing:
        print(f"    pip install {pkg}")
    print()
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nexus_migration")

BATCH_SIZE = 200  # rows per INSERT batch

# ── ID maps : mongo ObjectId string  →  postgres integer id ──────────────────
# These are populated as each phase runs so later phases can resolve FKs.
_map: Dict[str, Dict[str, int]] = {
    "user":           {},
    "workspace":      {},
    "product":        {},
    "global_lead":    {},
    "campaign":       {},
    "sequence":       {},
    "lead_sequence":  {},
    "inbound_lead":   {},
    "inbound_thread": {},
    "demo_booking":   {},
}

# ── Full set of nexus_* tables the script writes to (used by preflight) ─────
REQUIRED_NEXUS_TABLES = [
    "nexus_user_profiles",
    "nexus_workspaces",
    "nexus_workspace_invites",
    "nexus_products",
    "nexus_product_assets",
    "nexus_knowledge_embeddings",
    "nexus_global_leads",
    "nexus_campaigns",
    "nexus_leads",
    "nexus_lead_enrichment",
    "nexus_intent_signals",
    "nexus_apollo_lead_cache",
    "nexus_sequences",
    "nexus_lead_sequences",
    "nexus_touchpoints",
    "nexus_personalization_cache",
    "nexus_send_logs",
    "nexus_conversation_accounts",
    "nexus_suppression_list",
    "nexus_automations",
    "nexus_inbound_leads",
    "nexus_inbound_threads",
    "nexus_inbound_messages",
    "nexus_unmatched_replies",
    "nexus_linkedin_messages",
    "nexus_voice_calls",
    "nexus_demo_bookings",
    "nexus_demo_briefings",
    "nexus_performance_insights",
    "nexus_conversion_snapshots",
    "nexus_credit_logs",
    "nexus_token_usage",
    "nexus_winning_examples",
    "nexus_outreach",
    "nexus_settings",
]

# Per-phase counters for rows that were silently dropped (missing FK,
# unresolvable workspace, empty required field, …). Reported in the main
# summary so partial data loss is visible instead of hidden behind a `continue`.
_drop_counters: Dict[str, Dict[str, int]] = {}


def _skip(phase: str, reason: str = "default", n: int = 1) -> None:
    """Record N skipped rows in `phase` under `reason`. Cheap; logged at end."""
    _drop_counters.setdefault(phase, {})
    _drop_counters[phase][reason] = _drop_counters[phase].get(reason, 0) + n


# =============================================================================
#  Utility helpers
# =============================================================================

def _oid(doc: dict, field: str) -> Optional[str]:
    v = doc.get(field)
    return str(v) if v else None

def _pgid(map_name: str, mongo_oid: Optional[str]) -> Optional[int]:
    return _map[map_name].get(mongo_oid) if mongo_oid else None

def _ts(doc: dict, *fields) -> Optional[str]:
    for f in fields:
        v = doc.get(f)
        if v:
            return v.isoformat() if isinstance(v, datetime) else str(v)
    return None

def _now() -> str:
    return datetime.utcnow().isoformat()

def _fn(full: str) -> str:
    parts = (full or "").strip().split(" ", 1)
    return parts[0] if parts else ""

def _ln(full: str) -> str:
    parts = (full or "").strip().split(" ", 1)
    return parts[1] if len(parts) > 1 else ""

def _j(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return json.dumps(v, default=str)
    except Exception:
        return None

def _safe_status(val: str, allowed: tuple, default: str) -> str:
    return val if val in allowed else default


def _batch_insert(cur, table: str, columns: List[str],
                  rows: List[Tuple], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        log.info(f"  [DRY-RUN] {table}: would insert {len(rows)} rows")
        return len(rows)
    col_str = ", ".join(columns)
    ph = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({ph}) ON CONFLICT DO NOTHING"
    for i in range(0, len(rows), BATCH_SIZE):
        psycopg2.extras.execute_batch(cur, sql, rows[i : i + BATCH_SIZE])
    log.info(f"  {table}: {len(rows)} rows processed")
    return len(rows)


def _insert_returning(cur, table: str, columns: List[str], rows: List[Tuple],
                      conflict_col, id_map_name: str,
                      mongo_ids: List[str], dry_run: bool) -> None:
    """
    INSERT each row and capture the resulting id in `_map[id_map_name]`.

    `conflict_col` controls dedup behavior:
      • None  → plain INSERT … RETURNING id (no ON CONFLICT). Re-running the
                phase WILL create duplicate rows; use --only PHASE to retry a
                single failed phase ONLY before any of its rows succeeded.
      • str   → single-column ON CONFLICT (col) DO NOTHING RETURNING id; on
                conflict, re-resolve id via SELECT id WHERE col=value.
      • list/tuple → composite ON CONFLICT (col1, col2, …) DO NOTHING with the
                same SELECT-based id re-resolution.
    """
    if not rows:
        return
    if dry_run:
        log.info(f"  [DRY-RUN] {table}: would insert {len(rows)} rows (with id capture)")
        return
    col_str = ", ".join(columns)
    ph = ", ".join(["%s"] * len(columns))

    if conflict_col is None:
        # No idempotency — fire-and-capture-id. Re-runs duplicate.
        sql = f"INSERT INTO {table} ({col_str}) VALUES ({ph}) RETURNING id"
        ok = 0
        for mongo_oid, row in zip(mongo_ids, rows):
            cur.execute(sql, row)
            result = cur.fetchone()
            if result:
                _map[id_map_name][mongo_oid] = result[0]
                ok += 1
        log.info(f"  {table}: {ok} new (one-shot; re-running this phase will duplicate)")
        return

    conflict_cols = list(conflict_col) if isinstance(conflict_col, (list, tuple)) else [conflict_col]
    conflict_spec = ", ".join(conflict_cols)
    sql = (f"INSERT INTO {table} ({col_str}) VALUES ({ph}) "
           f"ON CONFLICT ({conflict_spec}) DO NOTHING RETURNING id")
    where = " AND ".join(f"{c} = %s" for c in conflict_cols)
    select_sql = f"SELECT id FROM {table} WHERE {where} LIMIT 1"
    ok = 0
    for mongo_oid, row in zip(mongo_ids, rows):
        cur.execute(sql, row)
        result = cur.fetchone()
        if result:
            _map[id_map_name][mongo_oid] = result[0]
            ok += 1
        else:
            values = tuple(row[columns.index(c)] for c in conflict_cols)
            cur.execute(select_sql, values)
            existing = cur.fetchone()
            if existing:
                _map[id_map_name][mongo_oid] = existing[0]
    log.info(f"  {table}: {ok} new, {len(rows)-ok} already existed (IDs mapped)")


def _ws_from_user(cur, user_pg: Optional[int]) -> Optional[int]:
    if not user_pg:
        return None
    cur.execute(
        "SELECT default_workspace_id FROM nexus_user_profiles WHERE user_id=%s LIMIT 1",
        (user_pg,),
    )
    r = cur.fetchone()
    return r[0] if r else None


# Cache: PIPELYT user_id → workspace_id (default or auto-created).
_user_default_ws: Dict[int, int] = {}

def _get_or_create_workspace_for_user(
    cur, user_pg: Optional[int], dry_run: bool, name: str = "NEXUS — Migrated"
) -> Optional[int]:
    """
    Critical fix: lots of Mongo docs (products, campaigns, automations, …)
    have `workspace_id=null` because the legacy app didn't always require
    a workspace. PIPELYT enforces NOT NULL, so without this helper those
    rows would all be skipped, cascading to ~95% data loss.

    Lookup order:
      1) memoized result for user_pg
      2) nexus_user_profiles.default_workspace_id
      3) first nexus_workspaces row owned by user_pg
      4) create a new workspace "NEXUS — Migrated" owned by user_pg
    """
    if not user_pg:
        return None
    if user_pg in _user_default_ws:
        return _user_default_ws[user_pg]

    # (2) profile pointer
    cur.execute(
        "SELECT default_workspace_id FROM nexus_user_profiles WHERE user_id=%s LIMIT 1",
        (user_pg,),
    )
    r = cur.fetchone()
    if r and r[0]:
        _user_default_ws[user_pg] = r[0]
        return r[0]

    # (3) any owned workspace
    cur.execute(
        "SELECT id FROM nexus_workspaces WHERE owner_id=%s ORDER BY id ASC LIMIT 1",
        (user_pg,),
    )
    r = cur.fetchone()
    if r:
        _user_default_ws[user_pg] = r[0]
        # backfill profile pointer
        if not dry_run:
            cur.execute(
                "UPDATE nexus_user_profiles SET default_workspace_id=%s "
                "WHERE user_id=%s AND default_workspace_id IS NULL",
                (r[0], user_pg),
            )
        return r[0]

    # (4) create
    if dry_run:
        log.info(f"  [DRY-RUN] would auto-create workspace '{name}' for user_id={user_pg}")
        # In dry-run, return a sentinel so downstream rows aren't skipped
        # (they won't actually insert anyway). Use a negative pseudo-id.
        _user_default_ws[user_pg] = -user_pg
        return -user_pg

    cur.execute(
        """INSERT INTO nexus_workspaces
            (owner_id, name, status, plan, is_trial, trial_ends_at, limits, usage, created_at)
           VALUES (%s, %s, 'active', 'starter', 'false', NULL, NULL, NULL, NOW())
           RETURNING id""",
        (user_pg, name),
    )
    new_id = cur.fetchone()[0]
    _user_default_ws[user_pg] = new_id
    cur.execute(
        "UPDATE nexus_user_profiles SET default_workspace_id=%s "
        "WHERE user_id=%s AND default_workspace_id IS NULL",
        (new_id, user_pg),
    )
    log.info(f"  Auto-created workspace '{name}' (id={new_id}) for PIPELYT user_id={user_pg}")
    return new_id


# =============================================================================
#  Connection
# =============================================================================

def connect_mongo():
    if "<username>" in MONGO_URI or "<password>" in MONGO_URI:
        log.error("MONGO_URI still has placeholder values — fill in your credentials first.")
        sys.exit(1)
    client = pymongo.MongoClient(
        MONGO_URI,
        read_preference=ReadPreference.SECONDARY_PREFERRED,
        serverSelectionTimeoutMS=15_000,
    )
    db = client[MONGO_DB_NAME]
    db.command("ping")
    log.info(f"MongoDB Atlas connected  (db={MONGO_DB_NAME}, read-only preference)")
    return db


def connect_pg():
    if "<rds-endpoint>" in PG_HOST or "<db-username>" in PG_USER or not PG_PASSWORD:
        log.error("PostgreSQL credentials still have placeholder values — fill them in first.")
        sys.exit(1)
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
        connect_timeout=15,
    )
    conn.autocommit = False
    log.info(f"PostgreSQL connected  (host={PG_HOST}, db={PG_DATABASE})")
    return conn


def preflight_check(cur) -> None:
    """
    Verifies the target PostgreSQL DB has every nexus_* table the script writes
    to. Aborts if any are missing (means NEXUS_ENABLED=true cold-start hasn't
    run yet, or the deployed schema is older than this script). Also confirms
    the pipelyt `users` table we read from exists.
    """
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'nexus_%'"
    )
    existing = {r[0] for r in cur.fetchall()}
    missing = [t for t in REQUIRED_NEXUS_TABLES if t not in existing]
    if missing:
        log.error(
            "The following nexus_* tables are missing from PostgreSQL:\n  "
            + "\n  ".join(missing)
            + "\n\nPlease deploy the Pipelyt backend with NEXUS_ENABLED=true "
            "and let one Lambda cold-start run to create the tables first."
        )
        sys.exit(1)

    cur.execute("SELECT 1 FROM pg_tables WHERE tablename='users' AND schemaname='public'")
    if not cur.fetchone():
        log.error("The pipelyt 'users' table was not found. Wrong database?")
        sys.exit(1)

    log.info(
        f"Pre-flight OK — all {len(REQUIRED_NEXUS_TABLES)} required nexus_* tables "
        f"present (of {len(existing)} total nexus_* tables in DB)"
    )


# =============================================================================
#  PHASE 1 — Users  →  nexus_user_profiles
# =============================================================================

def migrate_users(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: users → nexus_user_profiles ───")
    docs = list(mdb["users"].find({}))
    log.info(f"  Found {len(docs)} users in MongoDB")

    profile_rows, profile_oids = [], []
    for doc in docs:
        email = (doc.get("email") or "").lower().strip()
        if not email:
            continue
        # USER_EMAIL_REMAP: reroute Mongo emails that don't exist in PIPELYT
        # to existing PIPELYT users. Lookups are case-insensitive.
        lookup_email = USER_EMAIL_REMAP.get(email, email).lower().strip()
        cur.execute("SELECT id FROM users WHERE lower(email)=%s LIMIT 1", (lookup_email,))
        row = cur.fetchone()
        if not row:
            log.warning(
                f"  No Pipelyt account found for '{email}'"
                + (f" (remapped → '{lookup_email}')" if lookup_email != email else "")
                + " — skipping"
            )
            continue
        if lookup_email != email:
            log.info(f"  Remap: '{email}' → '{lookup_email}' (PIPELYT user id {row[0]})")
        pg_uid = row[0]
        _map["user"][str(doc["_id"])] = pg_uid

        role = {"admin": "admin", "manager": "member", "user": "member"}.get(
            doc.get("role", "admin"), "member"
        )
        profile_rows.append((
            pg_uid,
            role,
            doc.get("timezone", "UTC"),
            doc.get("daily_report_time", "08:00"),
            doc.get("daily_pipeline_time", "06:00"),
            bool(doc.get("auto_reply_enabled", False)),
            bool(doc.get("founder_mode", False)),
            _ts(doc, "createdAt") or _now(),
        ))
        profile_oids.append(str(doc["_id"]))

    if dry_run:
        log.info(f"  [DRY-RUN] nexus_user_profiles: would upsert {len(profile_rows)} rows")
        return

    sql = """
        INSERT INTO nexus_user_profiles
          (user_id, role, timezone, daily_report_time, daily_pipeline_time,
           auto_reply_enabled, founder_mode, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_id) DO UPDATE SET
          timezone            = EXCLUDED.timezone,
          daily_report_time   = EXCLUDED.daily_report_time,
          daily_pipeline_time = EXCLUDED.daily_pipeline_time,
          auto_reply_enabled  = EXCLUDED.auto_reply_enabled,
          founder_mode        = EXCLUDED.founder_mode
    """
    if profile_rows:
        psycopg2.extras.execute_batch(cur, sql, profile_rows)
    log.info(f"  nexus_user_profiles: {len(profile_rows)} upserted")


# =============================================================================
#  PHASE 2 — Workspaces  →  nexus_workspaces
# =============================================================================

def migrate_workspaces(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: workspaces → nexus_workspaces ───")
    docs = list(mdb["workspaces"].find({}))
    log.info(f"  Found {len(docs)} workspaces in MongoDB")
    log.info("  (Mongo fields dropped: stripe_customer_id, stripe_subscription_id — "
             "PIPELYT handles billing on the User row, no DDL home in nexus_workspaces)")

    rows, mongo_ids = [], []
    for doc in docs:
        owner_pg = _pgid("user", _oid(doc, "owner_id"))
        if not owner_pg:
            log.warning(f"  Workspace '{doc.get('name')}': owner not in Pipelyt users — skipping")
            _skip("workspaces", "owner_not_in_pipelyt")
            continue
        plan = {"starter": "starter", "pro": "growth", "enterprise": "enterprise"}.get(
            doc.get("plan", "starter"), "starter"
        )
        rows.append((
            owner_pg,
            doc.get("name", "My Workspace"),
            doc.get("status", "active"),
            plan,
            str(doc.get("is_trial", False)).lower(),  # DDL stores VARCHAR 'true'|'false'
            _ts(doc, "trial_ends_at"),
            _j(doc.get("limits")),
            _j(doc.get("usage")),
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_workspaces",
        ["owner_id", "name", "status", "plan", "is_trial", "trial_ends_at",
         "limits", "usage", "created_at"],
        rows, None, "workspace", mongo_ids, dry_run,
    )

    if not dry_run:
        for doc in docs:
            ws_pg = _map["workspace"].get(str(doc["_id"]))
            owner_pg = _pgid("user", _oid(doc, "owner_id"))
            if ws_pg and owner_pg:
                cur.execute(
                    "UPDATE nexus_user_profiles SET default_workspace_id=%s WHERE user_id=%s",
                    (ws_pg, owner_pg),
                )
        log.info("  nexus_user_profiles.default_workspace_id set")


# =============================================================================
#  PHASE 3 — Workspace Invites  →  nexus_workspace_invites
# =============================================================================

def migrate_invitations(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: invitations → nexus_workspace_invites ───")
    docs = list(mdb["invitations"].find({}))
    log.info(f"  Found {len(docs)} invitations in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        inv_pg = _pgid("user", _oid(doc, "inviter_id"))
        if not ws_pg or not inv_pg:
            _skip("invitations", "missing_workspace_or_inviter")
            continue
        rows.append((
            ws_pg, inv_pg,
            (doc.get("email") or "").lower(),
            doc.get("role", "member"),
            doc.get("token", ""),
            doc.get("status", "pending"),
            _ts(doc, "expires_at"),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_workspace_invites",
                  ["workspace_id", "invited_by_user_id", "email_lower", "role",
                   "token", "status", "expires_at", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 4 — Products  →  nexus_products
# =============================================================================

def migrate_products(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: products → nexus_products ───")
    docs = list(mdb["products"].find({}))
    log.info(f"  Found {len(docs)} products in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: product_description, "
             "knowledge_base, logo_url, brand_colors, active. "
             "product_summary blob is decomposed into category/value_proposition/"
             "key_benefits/pricing_tier/industry_relevance where present.)")

    rows, mongo_ids = [], []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        user_pg = _pgid("user", _oid(doc, "user_id"))
        if not user_pg:
            _skip("products", "missing_user")
            continue
        if not ws_pg:
            # Mongo workspace_id is null — fall back to the user's
            # default workspace (auto-creating if needed).
            ws_pg = _get_or_create_workspace_for_user(cur, user_pg, dry_run)
        if not ws_pg:
            _skip("products", "no_workspace_resolvable")
            continue

        # The Mongo `product_summary` is a JSON blob with structured fields. The
        # nexus_products DDL stores those fields as individual columns rather than
        # as one blob. Extract what we can; leave the rest blank.
        summary = doc.get("product_summary") or {}
        name = doc.get("name") or summary.get("name") or doc.get("product_url", "")
        category = summary.get("category") or ""
        value_prop = summary.get("value_proposition") or doc.get("product_description", "") or ""
        key_benefits = summary.get("key_benefits") or []
        pricing_tier = summary.get("pricing_tier") or ""
        industry = summary.get("industry_relevance") or ""

        rows.append((
            ws_pg,
            user_pg,
            name,
            category,
            value_prop,
            _j(key_benefits),
            pricing_tier,
            industry,
            _j(doc.get("icp_default") or {}),
            doc.get("product_url", ""),
            "ready" if doc.get("active", True) else "archived",
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_products",
        ["workspace_id", "user_id", "name", "category", "value_proposition",
         "key_benefits", "pricing_tier", "industry_relevance", "icp",
         "source_url", "status", "created_at"],
        rows, None, "product", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 5 — Product Assets  →  nexus_product_assets
# =============================================================================

def migrate_product_assets(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: product assets → nexus_product_assets ───")
    docs = list(mdb["productassets"].find({}))
    log.info(f"  Found {len(docs)} product assets in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: workspace_id, user_id, "
             "chunks, active. title is concatenated into `source` alongside "
             "source_url; source_text → raw_text; char_count computed from raw_text.)")

    rows = []
    for doc in docs:
        prod_pg = _pgid("product", _oid(doc, "product_id"))
        if not prod_pg:
            _skip("product_assets", "missing_product")
            continue
        title = (doc.get("title") or "").strip()
        url = (doc.get("source_url") or "").strip()
        # `source` in the DDL is a single VARCHAR(1024) — pack title + url so
        # neither is lost.
        if title and url:
            source = f"{title} <{url}>"[:1024]
        else:
            source = (title or url)[:1024]
        raw_text = doc.get("source_text") or ""
        rows.append((
            prod_pg,
            doc.get("asset_type", "text"),
            source,
            raw_text,
            len(raw_text),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_product_assets",
                  ["product_id", "asset_type", "source", "raw_text",
                   "char_count", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 6 — Knowledge Embeddings  →  nexus_knowledge_embeddings
# =============================================================================

def migrate_knowledge_embeddings(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: knowledge embeddings → nexus_knowledge_embeddings ───")
    docs = list(mdb["knowledgeembeddings"].find({}))
    log.info(f"  Found {len(docs)} embeddings in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: user_id, section. "
             "q_num → chunk_index, text → chunk_text. "
             "embedding stays JSONB; pgvector index is created by phase5 DDL when available.)")

    rows = []
    for doc in docs:
        prod_pg = _pgid("product", _oid(doc, "product_id"))
        if not prod_pg:
            _skip("knowledge_embeddings", "missing_product")
            continue
        chunk_text = doc.get("text") or ""
        if not chunk_text:
            _skip("knowledge_embeddings", "empty_chunk_text")
            continue
        try:
            chunk_index = int(doc.get("q_num") or 0)
        except (TypeError, ValueError):
            chunk_index = 0
        # DDL embedding column is JSONB — store the list as JSON, no pgvector
        # cast needed. The phase5 migration creates the ivfflat index on the
        # column when pgvector is installed; otherwise queries fall back to
        # JSONB cosine in Python.
        emb = doc.get("embedding")
        rows.append((
            prod_pg,
            chunk_index,
            chunk_text,
            _j(emb) if emb else None,
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_knowledge_embeddings",
                  ["product_id", "chunk_index", "chunk_text", "embedding",
                   "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 7 — Global Leads  →  nexus_global_leads
# =============================================================================

def migrate_global_leads(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: global leads → nexus_global_leads ───")
    docs = list(mdb["globalleads"].find({}))
    log.info(f"  Found {len(docs)} global leads in MongoDB")

    # Keep `demo_scheduled` as a real status — the Postgres column has no CHECK
    # constraint that would reject it (see migrations/phase3.py:21). Collapsing
    # into `replied` loses information about which leads booked a demo.
    status_map = {
        "new": "new", "contacted": "contacted", "replied": "replied",
        "demo_scheduled": "demo_scheduled", "bounced": "bounced",
        "unsubscribed": "unsubscribed",
    }
    rows, mongo_ids = [], []
    for doc in docs:
        email = (doc.get("email") or "").lower().strip()
        if not email:
            continue
        full = doc.get("name", "")
        priority = doc.get("priority_state", "active")
        if priority not in ("active", "low_priority", "hidden"):
            priority = "active"
        # Phase 7 columns (added in nexus/migrations/phase7.py). Backfill
        # the denormalized mirrors so GTM Journey UI sees the full picture.
        channel_attempts = doc.get("channel_attempts") or {"email": 0, "linkedin": 0, "voice": 0}
        # Make sure all 3 keys exist (Mongo sometimes only has the ones used).
        channel_attempts = {
            "email":    int(channel_attempts.get("email", 0) or 0),
            "linkedin": int(channel_attempts.get("linkedin", 0) or 0),
            "voice":    int(channel_attempts.get("voice", 0) or 0),
        }
        rows.append((
            email,
            _fn(full), _ln(full),
            doc.get("job_title", ""),
            doc.get("company_domain", ""),
            doc.get("company", ""),
            status_map.get(doc.get("status", "new"), "new"),
            priority,
            bool(doc.get("email_verified", False)),
            int(doc.get("email_verify_score", 0)),
            doc.get("source", "apollo"),
            _ts(doc, "createdAt") or _now(),
            # ── Phase 7 denormalized fields ─────────────────────────
            full,                                          # name (full string)
            doc.get("company", ""),                        # company mirror
            doc.get("job_title", ""),                      # job_title mirror
            doc.get("linkedin_url", ""),                   # linkedin_url
            doc.get("email_verify_status", ""),            # email_verify_status
            _ts(doc, "last_contacted_at"),                 # last_contacted_at
            priority,                                       # priority_state mirror
            doc.get("hidden_reason"),                      # hidden_reason
            _ts(doc, "hidden_at"),                         # hidden_at
            int(doc.get("attempt_count_total", 0) or 0),   # attempt_count_total
            _j(channel_attempts),                          # channel_attempts JSONB
            _ts(doc, "last_attempt_at"),                   # last_attempt_at
            doc.get("last_attempt_channel"),               # last_attempt_channel
            int(doc.get("total_emails_sent", 0) or 0),     # total_emails_sent
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_global_leads",
        ["email", "first_name", "last_name", "role", "company_domain",
         "company_name", "status", "priority", "email_verified",
         "email_verify_score", "source", "created_at",
         # Phase 7 columns
         "name", "company", "job_title", "linkedin_url",
         "email_verify_status", "last_contacted_at",
         "priority_state", "hidden_reason", "hidden_at",
         "attempt_count_total", "channel_attempts",
         "last_attempt_at", "last_attempt_channel", "total_emails_sent"],
        rows, "email", "global_lead", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 8 — Campaigns  →  nexus_campaigns
# =============================================================================

def migrate_campaigns(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: campaigns → nexus_campaigns ───")
    docs = list(mdb["campaigns"].find({}))
    log.info(f"  Found {len(docs)} campaigns in MongoDB")
    log.info("  (Mongo field dropped — no DDL home: user_id. "
             "Campaign ownership lives on the workspace, not per-campaign.)")

    status_map = {
        "analyzing": "draft", "generating_leads": "active",
        "personalizing": "active", "ready": "active",
        "canceled": "paused", "completed": "archived",
    }
    rows, mongo_ids = [], []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            user_pg = _pgid("user", _oid(doc, "user_id"))
            ws_pg = _get_or_create_workspace_for_user(cur, user_pg, dry_run)
        if not ws_pg:
            _skip("campaigns", "no_workspace_resolvable")
            continue
        ps = doc.get("product_summary") or {}
        name = ps.get("name") or doc.get("product_url") or "Campaign"
        rows.append((
            ws_pg,
            _pgid("product", _oid(doc, "product_id")),
            name,
            _j(doc.get("icp") or {}),
            status_map.get(doc.get("status", "ready"), "active"),
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_campaigns",
        ["workspace_id", "product_id", "name", "icp", "status", "created_at"],
        rows, None, "campaign", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 9 — Leads (company)  →  nexus_leads  (junction table)
# =============================================================================

def migrate_leads(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: leads → nexus_leads ───")
    docs = list(mdb["leads"].find({}))
    log.info(f"  Found {len(docs)} company leads in MongoDB")

    insert_rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        campaign_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        if not ws_pg and campaign_pg and not dry_run:
            # Mongo leads.workspace_id is sometimes null. Derive from campaign.
            cur.execute("SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1", (campaign_pg,))
            r = cur.fetchone()
            ws_pg = r[0] if r else None
        if not ws_pg or not campaign_pg:
            _skip("leads", "missing_workspace_or_campaign")
            continue
        for emp in (doc.get("employees") or []):
            email = (emp.get("email") or "").lower().strip()
            if not email:
                _skip("leads", "employee_missing_email")
                continue
            if not dry_run:
                cur.execute(
                    "SELECT id FROM nexus_global_leads WHERE email=%s LIMIT 1", (email,)
                )
                row = cur.fetchone()
                if not row:
                    _skip("leads", "employee_not_in_global_leads")
                    continue
                insert_rows.append((
                    ws_pg, campaign_pg, row[0],
                    int(doc.get("relevance_score", 0)),
                ))
            else:
                insert_rows.append((ws_pg, campaign_pg, 0, 0))

    _batch_insert(cur, "nexus_leads",
                  ["workspace_id", "campaign_id", "global_lead_id", "icp_score"],
                  insert_rows, dry_run)


# =============================================================================
#  PHASE 10 — Lead Enrichment  →  nexus_lead_enrichment
# =============================================================================

def migrate_lead_enrichment(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: lead enrichment → nexus_lead_enrichment ───")
    docs = list(mdb["leadenrichments"].find({}))
    log.info(f"  Found {len(docs)} enrichment records in MongoDB")
    rows = []
    for doc in docs:
        domain = (doc.get("domain") or "").lower().strip()
        if not domain:
            continue
        rows.append((
            domain,
            doc.get("page_title", ""),
            doc.get("meta_description", ""),
            _j(doc.get("headings", [])),
            doc.get("site_summary", ""),
            _j(doc.get("tech_stack", [])),
            _j(doc.get("news_headlines", [])),
            _ts(doc, "enriched_at") or _now(),
        ))
    _batch_insert(cur, "nexus_lead_enrichment",
                  ["company_domain", "page_title", "meta_description", "headings",
                   "body_snippet", "tech_stack", "news_headlines", "fetched_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 11 — Intent Signals  →  nexus_intent_signals
# =============================================================================

def migrate_intent_signals(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: intent signals → nexus_intent_signals ───")
    docs = list(mdb["intentsignals"].find({}))
    log.info(f"  Found {len(docs)} intent signals in MongoDB")
    # Mongo IntentSignal.lead_id refers to a *global* lead, but the Postgres
    # `nexus_intent_signals.lead_id` FKs to nexus_leads.id (the workspace-scoped
    # junction row). Resolve through (workspace, campaign, global_lead). Skip
    # rows where no junction exists — those signals were detached from any
    # active campaign and cannot be safely re-attached.

    if dry_run:
        log.info(f"  [DRY-RUN] nexus_intent_signals: would attempt {len(docs)} rows "
                 "(dry-run cannot resolve workspace-lead FKs without writing)")
        return

    rows = []
    for doc in docs:
        gl_pg = _pgid("global_lead", _oid(doc, "lead_id"))
        if not gl_pg:
            _skip("intent_signals", "global_lead_unresolved")
            continue
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        camp_pg = _pgid("campaign", _oid(doc, "campaign_id"))

        # Most-specific match first; fall back to any nexus_lead for this
        # (workspace, global_lead) pair if campaign isn't known.
        nexus_lead_pg = None
        if ws_pg and camp_pg:
            cur.execute(
                "SELECT id FROM nexus_leads "
                "WHERE global_lead_id=%s AND workspace_id=%s AND campaign_id=%s "
                "LIMIT 1",
                (gl_pg, ws_pg, camp_pg),
            )
            r = cur.fetchone()
            nexus_lead_pg = r[0] if r else None
        if not nexus_lead_pg and ws_pg:
            cur.execute(
                "SELECT id FROM nexus_leads "
                "WHERE global_lead_id=%s AND workspace_id=%s LIMIT 1",
                (gl_pg, ws_pg),
            )
            r = cur.fetchone()
            nexus_lead_pg = r[0] if r else None
        if not nexus_lead_pg:
            cur.execute(
                "SELECT id FROM nexus_leads WHERE global_lead_id=%s LIMIT 1",
                (gl_pg,),
            )
            r = cur.fetchone()
            nexus_lead_pg = r[0] if r else None

        if not nexus_lead_pg:
            _skip("intent_signals", "no_matching_nexus_lead")
            continue

        rows.append((
            nexus_lead_pg,
            doc.get("signal_type") or doc.get("source") or "unknown",
            _j({"signal_type": doc.get("signal_type"),
                "score": doc.get("score", 0),
                "source": doc.get("source"),
                "raw_data": doc.get("raw_data")}),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_intent_signals",
                  ["lead_id", "signal_type", "payload", "observed_at"],
                  rows, dry_run)

    dropped = sum(_drop_counters.get("intent_signals", {}).values())
    if dropped:
        log.warning(f"  nexus_intent_signals: {dropped} rows dropped — no matching workspace-lead")


# =============================================================================
#  PHASE 12 — Apollo Lead Cache  →  nexus_apollo_lead_cache
# =============================================================================

def migrate_apollo_cache(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: Apollo cache → nexus_apollo_lead_cache ───")
    docs = list(mdb["apollo_lead_cache"].find({}))
    log.info(f"  Found {len(docs)} Apollo cache entries in MongoDB")
    rows = []
    for doc in docs:
        key = f"{doc.get('apollo_person_id','')}:{doc.get('workspace_id','default')}"
        query_hash = hashlib.sha256(key.encode()).hexdigest()[:64]
        response = {k: str(v) if isinstance(v, ObjectId) else v
                    for k, v in doc.items() if k not in ("_id", "__v")}
        rows.append((
            query_hash,
            _j(response),
            _ts(doc, "enriched_at") or _now(),
        ))
    _batch_insert(cur, "nexus_apollo_lead_cache",
                  ["query_hash", "response", "fetched_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 13 — Suppression List  →  nexus_suppression_list
# =============================================================================

def migrate_suppression(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: suppression list → nexus_suppression_list ───")
    docs = list(mdb["suppressionlists"].find({}))
    log.info(f"  Found {len(docs)} suppressed addresses in MongoDB")

    workspace_pgs = list(set(_map["workspace"].values()))
    if not workspace_pgs:
        log.warning("  No workspaces resolved — suppression list skipped")
        return

    rows = []
    for doc in docs:
        email = (doc.get("email") or "").lower().strip()
        if not email:
            continue
        reason = doc.get("reason", "unsubscribed")
        added_at = _ts(doc, "suppressed_at") or _now()
        # Write one row per workspace — all workspaces honour the same suppression
        for ws_pg in workspace_pgs:
            rows.append((ws_pg, email, reason, added_at))

    _batch_insert(cur, "nexus_suppression_list",
                  ["workspace_id", "email_lower", "reason", "added_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 14 — Conversation Accounts  →  nexus_conversation_accounts
# =============================================================================

def migrate_conversation_accounts(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: conversation accounts → nexus_conversation_accounts ───")
    docs = list(mdb["conversationaccounts"].find({}))
    log.info(f"  Found {len(docs)} mailbox accounts in MongoDB")
    rows = []
    for doc in docs:
        user_pg = _pgid("user", _oid(doc, "user_id"))
        if not user_pg:
            continue
        ws_pg = _ws_from_user(cur, user_pg) if not dry_run else None
        rows.append((
            ws_pg,
            (doc.get("mailbox_email") or "").lower(),
            50,  # daily_send_limit default
            0,   # daily_send_count
            doc.get("provider", "gmail"),
            doc.get("refresh_token"),
            "active" if doc.get("status") == "connected" else "inactive",
        ))
    _batch_insert(cur, "nexus_conversation_accounts",
                  ["workspace_id", "email_address", "daily_send_limit",
                   "daily_send_count", "provider", "refresh_token", "status"],
                  rows, dry_run)


# =============================================================================
#  PHASE 15 — Sequences  →  nexus_sequences
# =============================================================================

def migrate_sequences(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: sequences → nexus_sequences ───")
    docs = list(mdb["sequences"].find({}))
    log.info(f"  Found {len(docs)} sequences in MongoDB")
    rows, mongo_ids = [], []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            user_pg = _pgid("user", _oid(doc, "user_id"))
            ws_pg = _get_or_create_workspace_for_user(cur, user_pg, dry_run)
        if not ws_pg:
            _skip("sequences", "no_workspace_resolvable")
            continue
        steps = [
            {"order": s.get("order", i), "channel": s.get("channel", "email"),
             "delay_days": s.get("delay_days", 3),
             "subject_template": s.get("subject_template", ""),
             "body_template": s.get("body_template", "")}
            for i, s in enumerate(doc.get("steps") or [])
        ]
        rows.append((
            ws_pg,
            _pgid("campaign", _oid(doc, "campaign_id")),
            doc.get("name", "Sequence"),
            _j(steps),
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_sequences",
        ["workspace_id", "campaign_id", "name", "steps", "created_at"],
        rows, None, "sequence", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 16 — Automations  →  nexus_automations
# =============================================================================

def migrate_automations(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: automations → nexus_automations ───")
    docs = list(mdb["automations"].find({}))
    log.info(f"  Found {len(docs)} automations in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            user_pg = _pgid("user", _oid(doc, "user_id"))
            ws_pg = _get_or_create_workspace_for_user(cur, user_pg, dry_run)
        if not ws_pg:
            _skip("automations", "no_workspace_resolvable")
            continue
        status = _safe_status(doc.get("status", "active"),
                               ("active", "paused", "completed", "failed"), "active")
        rows.append((
            ws_pg,
            _pgid("campaign", _oid(doc, "campaign_id")),
            doc.get("name", "Automation"),
            doc.get("schedule_type", "daily"),
            int(doc.get("num_leads_target", 50)),
            _ts(doc, "next_run_at"),
            _ts(doc, "last_run_at"),
            status,
            int(doc.get("run_count", 0)),
            int(doc.get("leads_generated_total", 0)),
            doc.get("last_run_status"),
            str(doc.get("last_run_summary", ""))[:2000],
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_automations",
                  ["workspace_id", "campaign_id", "name", "schedule_type",
                   "target_leads", "next_run_at", "last_run_at", "status",
                   "run_count", "leads_generated", "last_run_status",
                   "last_run_summary", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 17 — Lead Sequences  →  nexus_lead_sequences
# =============================================================================

def migrate_lead_sequences(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: lead sequences → nexus_lead_sequences ───")
    docs = list(mdb["leadsequences"].find({}))
    log.info(f"  Found {len(docs)} lead sequence enrollments in MongoDB")
    rows, mongo_ids = [], []
    for doc in docs:
        seq_pg = _pgid("sequence", _oid(doc, "sequence_id"))
        if not seq_pg:
            _skip("lead_sequences", "missing_sequence")
            continue
        status = _safe_status(
            doc.get("status", "active"),
            ("active", "paused", "replied", "bounced", "unsubscribed", "dead", "completed"),
            "active"
        )
        halt = status if status in ("bounced", "unsubscribed", "dead") else None
        rows.append((
            _pgid("workspace", _oid(doc, "workspace_id")),
            _pgid("campaign", _oid(doc, "campaign_id")),
            _pgid("global_lead", _oid(doc, "lead_id")),
            seq_pg,
            int(doc.get("current_step", 0)),
            _ts(doc, "next_action_at"),
            status,
            doc.get("last_error", ""),
            halt,
            _ts(doc, "started_at", "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    _insert_returning(
        cur, "nexus_lead_sequences",
        ["workspace_id", "campaign_id", "lead_id", "sequence_id",
         "current_step", "next_action_at", "status", "last_error",
         "halt_reason", "enrolled_at"],
        rows, None, "lead_sequence", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 18 — Touchpoints  →  nexus_touchpoints
# =============================================================================

def migrate_touchpoints(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: touchpoints → nexus_touchpoints ───")
    docs = list(mdb["touchpoints"].find({}))
    log.info(f"  Found {len(docs)} touchpoints in MongoDB")
    log.info("  (Phase 7 denormalized cols populated: lead_id, campaign_id, "
             "channel, body_snapshot, error_msg, user_id, workspace_id.)")

    # Workspace derived from campaign — cache to avoid repeat SELECTs.
    ws_by_camp: Dict[int, Optional[int]] = {}

    rows = []
    for doc in docs:
        camp_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        ws_pg = None
        if camp_pg:
            if camp_pg in ws_by_camp:
                ws_pg = ws_by_camp[camp_pg]
            elif not dry_run:
                cur.execute(
                    "SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1",
                    (camp_pg,),
                )
                r = cur.fetchone()
                ws_pg = r[0] if r else None
                ws_by_camp[camp_pg] = ws_pg

        body_snapshot = doc.get("body_snapshot", "")
        rows.append((
            # Original (legacy) columns
            _pgid("lead_sequence", _oid(doc, "lead_sequence_id")),
            int(doc.get("step", 0)),
            doc.get("subject", ""),
            body_snapshot,                                 # body (legacy col)
            _ts(doc, "sent_at") or _now(),
            doc.get("message_id", ""),
            _safe_status(doc.get("status", "sent"),
                         ("sent", "failed", "opened", "clicked", "replied"), "sent"),
            doc.get("error_msg", ""),
            # Phase 7 denormalized cols
            ws_pg,                                          # workspace_id
            _pgid("user",        _oid(doc, "user_id")),     # user_id
            _pgid("global_lead", _oid(doc, "lead_id")),     # lead_id
            camp_pg,                                        # campaign_id
            doc.get("channel", "email"),                    # channel
            body_snapshot,                                  # body_snapshot mirror
            doc.get("error_msg", ""),                       # error_msg mirror
            _ts(doc, "createdAt") or _now(),               # created_at
        ))
    _batch_insert(
        cur, "nexus_touchpoints",
        ["lead_sequence_id", "step", "subject", "body", "sent_at",
         "resend_message_id", "status", "error",
         "workspace_id", "user_id", "lead_id", "campaign_id", "channel",
         "body_snapshot", "error_msg", "created_at"],
        rows, dry_run,
    )


# =============================================================================
#  PHASE 19 — Personalization Cache  →  nexus_personalization_cache
# =============================================================================

def migrate_personalization_cache(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: personalization cache → nexus_personalization_cache ───")
    docs = list(mdb["personalizationcaches"].find({}))
    log.info(f"  Found {len(docs)} cached personalizations in MongoDB")
    rows = []
    for doc in docs:
        lead_pg = _pgid("global_lead", _oid(doc, "lead_id"))
        seq_pg = _pgid("sequence", _oid(doc, "sequence_id"))
        if not lead_pg or not seq_pg:
            continue
        rows.append((
            lead_pg, seq_pg,
            int(doc.get("step", 0)),
            doc.get("subject", ""),
            doc.get("body", ""),
            doc.get("source", "gemini"),
            _ts(doc, "generated_at", "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_personalization_cache",
                  ["lead_id", "sequence_id", "step", "subject", "body",
                   "model_used", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 20 — Send Logs  →  nexus_send_logs
# =============================================================================

def migrate_send_logs(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: send logs → nexus_send_logs ───")
    docs = list(mdb["sendlogs"].find({}))
    log.info(f"  Found {len(docs)} send log entries in MongoDB")
    rows = []
    for doc in docs:
        user_pg = _pgid("user", _oid(doc, "user_id"))
        if not user_pg:
            continue
        ws_pg = _ws_from_user(cur, user_pg) if not dry_run else None
        rows.append((
            ws_pg,
            _ts(doc, "sent_at") or _now(),
            doc.get("mailbox", "default"),
        ))
    _batch_insert(cur, "nexus_send_logs",
                  ["workspace_id", "sent_at", "recipient"],
                  rows, dry_run)


# =============================================================================
#  PHASE 21 — Inbound Leads  →  nexus_inbound_leads
# =============================================================================

def migrate_inbound_leads(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: inbound leads → nexus_inbound_leads ───")
    docs = list(mdb["inboundleads"].find({}))
    log.info(f"  Found {len(docs)} inbound leads in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: company, status. "
             "`name` is split into first_name/last_name. "
             "workspace_id is required NOT NULL — resolved from the first "
             "migrated workspace; rows with no workspace are skipped.)")

    # Inbound leads in Mongo aren't workspace-scoped, but the Postgres table is.
    # Attach all inbound leads to the first known workspace; in single-tenant
    # NEXUS deployments this is the only workspace anyway.
    default_ws = next(iter(_map["workspace"].values()), None)
    if not default_ws:
        log.warning("  No workspaces migrated — every inbound_lead will be skipped.")

    rows, mongo_ids = [], []
    for doc in docs:
        email = (doc.get("email") or "").lower().strip()
        if not email:
            _skip("inbound_leads", "missing_email")
            continue
        if not default_ws:
            _skip("inbound_leads", "no_default_workspace")
            continue
        full = doc.get("name") or ""
        rows.append((
            default_ws,
            email,
            _fn(full),
            _ln(full),
            doc.get("source", "resend"),
            _ts(doc, "firstSeenAt", "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))

    # Real UNIQUE constraint is `uq_inbound_lead_ws_email` on
    # (workspace_id, email) per migrations/phase5.py:36 — use composite ON CONFLICT.
    _insert_returning(
        cur, "nexus_inbound_leads",
        ["workspace_id", "email", "first_name", "last_name", "source", "created_at"],
        rows, ["workspace_id", "email"], "inbound_lead", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 22 — Inbound Threads  →  nexus_inbound_threads
# =============================================================================

def migrate_inbound_threads(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: inbound threads → nexus_inbound_threads ───")
    docs = list(mdb["inboundthreads"].find({}))
    log.info(f"  Found {len(docs)} inbound threads in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: normalized_subject, "
             "last_intent, demo_requested. unread_count → message_count. "
             "workspace_id NOT NULL is resolved from the linked inbound_lead.)")

    # Cache workspace per inbound_lead id so we don't hit the DB once per row.
    ws_by_lead: Dict[int, Optional[int]] = {}

    rows, mongo_ids = [], []
    for doc in docs:
        lead_pg = _pgid("inbound_lead", _oid(doc, "leadId"))
        if not lead_pg:
            _skip("inbound_threads", "missing_inbound_lead")
            continue
        if lead_pg in ws_by_lead:
            ws_pg = ws_by_lead[lead_pg]
        elif dry_run:
            ws_pg = next(iter(_map["workspace"].values()), None)
            ws_by_lead[lead_pg] = ws_pg
        else:
            cur.execute(
                "SELECT workspace_id FROM nexus_inbound_leads WHERE id=%s LIMIT 1",
                (lead_pg,),
            )
            r = cur.fetchone()
            ws_pg = r[0] if r else None
            ws_by_lead[lead_pg] = ws_pg
        if not ws_pg:
            _skip("inbound_threads", "no_workspace_for_lead")
            continue
        rows.append((
            ws_pg,
            lead_pg,
            doc.get("subject", ""),
            _ts(doc, "lastMessageAt") or _now(),
            int(doc.get("unreadCount", 0)),  # → message_count (best-effort)
            "open",
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))
    _insert_returning(
        cur, "nexus_inbound_threads",
        ["workspace_id", "lead_id", "subject", "last_message_at",
         "message_count", "status", "created_at"],
        rows, None, "inbound_thread", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 23 — Inbound Messages  →  nexus_inbound_messages
# =============================================================================

def migrate_inbound_messages(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: inbound messages → nexus_inbound_messages ───")
    docs = list(mdb["inboundmessages"].find({}))
    log.info(f"  Found {len(docs)} inbound messages in MongoDB")
    log.info("  (Mongo field dropped — no DDL home: leadId. "
             "from→from_email, to→to_email, body→body_text, "
             "resendId→message_id_header per the real DDL.)")

    rows = []
    for doc in docs:
        thread_pg = _pgid("inbound_thread", _oid(doc, "threadId"))
        if not thread_pg:
            _skip("inbound_messages", "missing_thread")
            continue
        direction = doc.get("direction", "inbound")
        if direction not in ("inbound", "outbound"):
            direction = "inbound"
        rows.append((
            thread_pg,
            direction,
            doc.get("from", ""),
            doc.get("to", ""),
            doc.get("subject", ""),
            doc.get("body", ""),
            "",  # body_html — not present in legacy Mongo
            doc.get("resendId", ""),
            "",  # in_reply_to_header — not tracked in legacy
            _ts(doc, "receivedAt") or _now(),
            doc.get("intent") or None,
            bool(doc.get("isRead", False)),
        ))
    _batch_insert(cur, "nexus_inbound_messages",
                  ["thread_id", "direction", "from_email", "to_email",
                   "subject", "body_text", "body_html", "message_id_header",
                   "in_reply_to_header", "received_at", "intent", "is_read"],
                  rows, dry_run)


# =============================================================================
#  PHASE 24 — Inbox  →  nexus_inbox
# =============================================================================

def migrate_inbox(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: inbox → nexus_inbox ───")
    log.warning(
        "  SKIPPED: the Postgres `nexus_inbox` table is a denormalized "
        "unread-index pointing at (workspace_id, thread_id, message_id) per "
        "migrations/phase5.py:95-103. The legacy Mongo `inboxes` collection "
        "is a flat reply log with completely different columns (from_email, "
        "reply_text, intent, status, …). They model different things; the "
        "rows can't be 1:1 transferred. nexus_inbox will populate organically "
        "as new inbound traffic lands in PIPELYT."
    )
    return


# =============================================================================
#  PHASE 25 — Unmatched Replies  →  nexus_unmatched_replies
# =============================================================================

def migrate_unmatched_replies(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: unmatched replies → nexus_unmatched_replies ───")
    docs = list(mdb["unmatchedreplies"].find({}))
    log.info(f"  Found {len(docs)} unmatched replies in MongoDB")
    log.info("  (Mongo field dropped — no DDL home: gmail_thread_id. "
             "in_reply_to → in_reply_to_header, raw_snippet → raw_payload.)")

    default_ws = next(iter(_map["workspace"].values()), None)
    rows = []
    for doc in docs:
        rows.append((
            default_ws,  # workspace_id is nullable per phase5 DDL
            doc.get("from_email", ""),
            doc.get("subject", ""),
            doc.get("raw_snippet", ""),  # body_text — closest field
            "",                          # body_html — not in legacy
            "",                          # message_id_header — not in legacy
            doc.get("in_reply_to", ""),
            doc.get("raw_snippet", ""),
            _ts(doc, "received_at", "createdAt") or _now(),
            False,
        ))
    _batch_insert(cur, "nexus_unmatched_replies",
                  ["workspace_id", "from_email", "subject", "body_text",
                   "body_html", "message_id_header", "in_reply_to_header",
                   "raw_payload", "received_at", "processed"],
                  rows, dry_run)


# =============================================================================
#  PHASE 26 — Processed Gmail Messages  →  nexus_processed_gmail_messages
# =============================================================================

def migrate_processed_gmail(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: processed Gmail messages → nexus_processed_gmail_messages ───")
    log.warning(
        "  SKIPPED: nexus_processed_gmail_messages requires "
        "conversation_account_id NOT NULL (FK to a NEXUS mailbox) per "
        "migrations/phase5.py:128-135. The legacy Mongo collection only "
        "stores raw Gmail message IDs with no mailbox association, so we "
        "cannot reconstruct that FK. Skipping — this table will populate "
        "organically as the Gmail poller runs on the new NEXUS deployment."
    )
    return


# =============================================================================
#  PHASE 27 — LinkedIn Messages  →  nexus_linkedin_messages
# =============================================================================

def migrate_linkedin_messages(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: LinkedIn messages → nexus_linkedin_messages ───")
    docs = list(mdb["linkedinmessages"].find({}))
    log.info(f"  Found {len(docs)} LinkedIn messages in MongoDB")
    log.info("  (Mongo fields dropped — no DDL home: user_id, campaign_id, "
             "status, error, created_at as a column. "
             "message → body. direction defaults to 'outbound'. "
             "workspace_id NOT NULL is resolved via the linked campaign.)")

    # Cache workspace per campaign to avoid repeat SELECTs.
    ws_by_camp: Dict[int, Optional[int]] = {}

    rows = []
    for doc in docs:
        camp_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        if not camp_pg:
            _skip("linkedin_messages", "missing_campaign")
            continue
        if camp_pg in ws_by_camp:
            ws_pg = ws_by_camp[camp_pg]
        elif dry_run:
            ws_pg = next(iter(_map["workspace"].values()), None)
            ws_by_camp[camp_pg] = ws_pg
        else:
            cur.execute(
                "SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1",
                (camp_pg,),
            )
            r = cur.fetchone()
            ws_pg = r[0] if r else None
            ws_by_camp[camp_pg] = ws_pg
        if not ws_pg:
            _skip("linkedin_messages", "no_workspace_for_campaign")
            continue
        rows.append((
            ws_pg,
            _pgid("global_lead", _oid(doc, "lead_id")),
            "outbound",
            doc.get("message", ""),
            _ts(doc, "generated_at", "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_linkedin_messages",
                  ["workspace_id", "lead_id", "direction", "body", "sent_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 28 — Voice Calls  →  nexus_voice_calls
# =============================================================================

def migrate_voice_calls(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: voice calls → nexus_voice_calls ───")
    docs = list(mdb["voicecalls"].find({}))
    log.info(f"  Found {len(docs)} voice call records in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            continue
        transcript_items = doc.get("transcript", [])
        transcript_text = "\n".join(
            f"[{t.get('role','?')}] {t.get('text','')}" for t in transcript_items
        )
        rows.append((
            ws_pg,
            _pgid("user", _oid(doc, "user_id")),
            _pgid("global_lead", _oid(doc, "lead_id")),
            _pgid("campaign", _oid(doc, "campaign_id")),
            _pgid("product", _oid(doc, "product_id")),
            doc.get("to_number", ""),
            doc.get("from_number", ""),
            doc.get("provider", "twilio"),
            doc.get("provider_call_sid", ""),
            doc.get("status", "queued"),
            doc.get("outcome", "unknown"),
            doc.get("last_speech_result", ""),
            doc.get("error_msg", ""),
            _ts(doc, "started_at") or _now(),
            _ts(doc, "answered_at"),
            _ts(doc, "ended_at"),
            transcript_text,
        ))
    _batch_insert(cur, "nexus_voice_calls",
                  ["workspace_id", "user_id", "lead_id", "campaign_id", "product_id",
                   "to_number", "from_number", "provider", "twilio_call_sid",
                   "status", "outcome", "last_speech_result", "error",
                   "started_at", "answered_at", "ended_at", "transcript"],
                  rows, dry_run)


# =============================================================================
#  PHASE 29 — Demo Bookings  →  nexus_demo_bookings
# =============================================================================

def migrate_demo_bookings(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: demo bookings → nexus_demo_bookings ───")
    docs = list(mdb["demobookings"].find({}))
    log.info(f"  Found {len(docs)} demo bookings in MongoDB")
    status_map = {"pending_rep_confirm": "scheduled", "confirmed": "confirmed",
                  "cancelled": "cancelled", "no_show": "no_show"}
    rows, mongo_ids = [], []
    for doc in docs:
        campaign_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        ws_pg = None
        if campaign_pg and not dry_run:
            cur.execute(
                "SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1",
                (campaign_pg,),
            )
            r = cur.fetchone()
            ws_pg = r[0] if r else None
        if not ws_pg:
            ws_pg = next(iter(_map["workspace"].values()), None)
        rows.append((
            ws_pg,
            _pgid("global_lead", _oid(doc, "lead_id")),
            campaign_pg,
            _pgid("product", _oid(doc, "product_id")),
            "ms_bookings",
            doc.get("ms_appointment_id", ""),
            doc.get("ms_booking_business_id", ""),
            _ts(doc, "start_time"),
            _ts(doc, "end_time"),
            status_map.get(doc.get("status", ""), "scheduled"),
            (doc.get("lead_email") or "").lower(),
            doc.get("lead_name", ""),
            doc.get("rep_name", ""),
            doc.get("rep_email", ""),
            doc.get("rep_phone", ""),
            doc.get("confirm_token"),
            _ts(doc, "confirm_token_expires_at"),
            _ts(doc, "confirmed_at"),
            bool(doc.get("reminder_24h_sent", False)),
            bool(doc.get("reminder_1h_sent", False)),
            _ts(doc, "createdAt") or _now(),
        ))
        mongo_ids.append(str(doc["_id"]))
    _insert_returning(
        cur, "nexus_demo_bookings",
        ["workspace_id", "lead_id", "campaign_id", "product_id",
         "source", "booking_id_external", "ms_booking_business_id",
         "scheduled_at", "end_time", "status", "attendee_email", "attendee_name",
         "rep_name", "rep_email", "rep_phone",
         "confirm_token", "confirm_token_expires_at", "confirmed_at",
         "reminder_24h_sent", "reminder_1h_sent", "created_at"],
        rows, None, "demo_booking", mongo_ids, dry_run,
    )


# =============================================================================
#  PHASE 30 — Demo Briefings  →  nexus_demo_briefings
# =============================================================================

def migrate_demo_briefings(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: demo briefings → nexus_demo_briefings ───")
    docs = list(mdb["demobriefings"].find({}))
    log.info(f"  Found {len(docs)} demo briefings in MongoDB")
    rows = []
    for doc in docs:
        booking_pg = _pgid("demo_booking", _oid(doc, "booking_id"))
        if not booking_pg:
            continue
        parts = []
        for section, label in [
            ("about_person",     "About the Person"),
            ("about_company",    "About the Company"),
            ("why_this_product", "Why This Product"),
        ]:
            if doc.get(section):
                parts.append(f"## {label}\n{doc[section]}")
        rows.append((
            booking_pg,
            _pgid("global_lead", _oid(doc, "lead_id")),
            _safe_status(doc.get("status", "ready"),
                         ("generating", "ready", "failed"), "ready"),
            "\n\n".join(parts),
            doc.get("about_person", ""),
            doc.get("about_company", ""),
            doc.get("why_this_product", ""),
            _j(doc.get("talking_points", [])),
            _j(doc.get("questions_to_ask", [])),
            _j(doc.get("likely_objections", [])),
            _j(doc.get("raw_research", {})),
            _j(doc.get("refine_history", [])),
            _ts(doc, "generated_at"),
            _ts(doc, "regenerated_at"),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_demo_briefings",
                  ["demo_booking_id", "lead_id", "status", "briefing_md",
                   "about_person", "about_company", "why_this_product",
                   "talking_points", "questions_to_ask", "likely_objections",
                   "raw_research", "refine_history",
                   "generated_at", "regenerated_at", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 31 — Performance Insights  →  nexus_performance_insights
# =============================================================================

def migrate_performance_insights(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: performance insights → nexus_performance_insights ───")
    docs = list(mdb["performanceinsights"].find({}))
    log.info(f"  Found {len(docs)} performance insight records in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            continue
        rows.append((
            ws_pg,
            _pgid("user", _oid(doc, "user_id")),
            _safe_status(doc.get("status", "ready"),
                         ("running", "ready", "failed"), "ready"),
            _j(doc.get("icp_data")),
            _j(doc.get("outreach_data")),
            _j(doc.get("signal_data")),
            _j(doc.get("campaign_data")),
            _j(doc.get("icp_insights", [])),
            _j(doc.get("outreach_insights", [])),
            _j(doc.get("signal_insights", [])),
            _j(doc.get("campaign_insights", [])),
            doc.get("summary", ""),
            doc.get("error", ""),
            _ts(doc, "generated_at"),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_performance_insights",
                  ["workspace_id", "user_id", "status",
                   "icp_data", "outreach_data", "signal_data", "campaign_data",
                   "icp_insights", "outreach_insights", "signal_insights",
                   "campaign_insights", "summary", "error",
                   "generated_at", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 32 — Conversion Snapshots  →  nexus_conversion_snapshots
# =============================================================================

def migrate_conversion_snapshots(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: conversion snapshots → nexus_conversion_snapshots ───")
    docs = list(mdb["conversionsnapshots"].find({}))
    log.info(f"  Found {len(docs)} conversion snapshots in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            continue
        converted_at = _ts(doc, "converted_at") or _now()
        snapshot_date = converted_at[:10]
        rows.append((
            ws_pg,
            _pgid("user", _oid(doc, "user_id")),
            _pgid("global_lead", _oid(doc, "lead_id")),
            _pgid("campaign", _oid(doc, "campaign_id")),
            _pgid("product", _oid(doc, "product_id")),
            snapshot_date,
            None,
            _j(doc.get("lead")),
            _j(doc.get("outreach")),
            _j(doc.get("signals", [])),
        ))
    _batch_insert(cur, "nexus_conversion_snapshots",
                  ["workspace_id", "user_id", "lead_id", "campaign_id", "product_id",
                   "snapshot_date", "metrics", "lead", "outreach", "signals"],
                  rows, dry_run)


# =============================================================================
#  PHASE 33 — Credit Logs  →  nexus_credit_logs
# =============================================================================

def migrate_credit_logs(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: credit logs → nexus_credit_logs ───")
    # Custom collection name: 'credits_log'
    docs = list(mdb["credits_log"].find({}))
    log.info(f"  Found {len(docs)} credit log entries in MongoDB")
    log.info("  (Column renames: endpoint → action_type, credits_consumed → "
             "credits_used, called_at → created_at. campaign_id is also "
             "captured where the legacy doc carries a campaign reference.)")

    rows = []
    for doc in docs:
        ws_raw = str(doc.get("workspace_id", ""))
        ws_pg = _map["workspace"].get(ws_raw)
        if not ws_pg:
            _skip("credit_logs", "missing_workspace")
            continue
        rows.append((
            ws_pg,
            doc.get("endpoint", "people_match"),
            int(doc.get("credits_consumed", 0)),
            int(doc.get("successful_matches", 0)),
            int(doc.get("total_requested", 0)),
            None,                                          # ref_id
            doc.get("ref_type", ""),                       # ref_type
            _pgid("campaign", _oid(doc, "campaign_id")),   # campaign_id
            _ts(doc, "called_at", "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_credit_logs",
                  ["workspace_id", "action_type", "credits_used",
                   "successful_matches", "total_requested", "ref_id",
                   "ref_type", "campaign_id", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 34 — Token Usage  →  nexus_token_usage
# =============================================================================

def migrate_token_usage(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: token usage → nexus_token_usage ───")
    docs = list(mdb["tokenusages"].find({}))
    log.info(f"  Found {len(docs)} token usage records in MongoDB")
    rows = []
    for doc in docs:
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            continue
        rows.append((
            ws_pg,
            _pgid("user", _oid(doc, "user_id")),
            _pgid("campaign", _oid(doc, "campaign_id")),
            doc.get("operation", "email_generation"),
            int(doc.get("tokens_used", 0)),
            _ts(doc, "created_at", "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_token_usage",
                  ["workspace_id", "user_id", "campaign_id", "operation",
                   "tokens_used", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 35 — Winning Examples  →  nexus_winning_examples
# =============================================================================

def migrate_winning_examples(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: winning examples → nexus_winning_examples ───")
    docs = list(mdb["winningexamples"].find({}))
    log.info(f"  Found {len(docs)} winning examples in MongoDB")
    log.info("  (workspace_id NOT NULL — resolved via the linked campaign.)")

    ws_by_camp: Dict[int, Optional[int]] = {}

    rows = []
    for doc in docs:
        camp_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        if not camp_pg:
            _skip("winning_examples", "missing_campaign")
            continue
        if camp_pg in ws_by_camp:
            ws_pg = ws_by_camp[camp_pg]
        elif dry_run:
            ws_pg = next(iter(_map["workspace"].values()), None)
            ws_by_camp[camp_pg] = ws_pg
        else:
            cur.execute(
                "SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1",
                (camp_pg,),
            )
            r = cur.fetchone()
            ws_pg = r[0] if r else None
            ws_by_camp[camp_pg] = ws_pg
        if not ws_pg:
            _skip("winning_examples", "no_workspace_for_campaign")
            continue
        intent = _safe_status(doc.get("intent", "INTERESTED"),
                               ("INTERESTED", "DEMO_SCHEDULED"), "INTERESTED")
        rows.append((
            ws_pg,
            _pgid("global_lead", _oid(doc, "lead_id")),
            camp_pg,
            doc.get("email_subject", ""),
            doc.get("email_body_plain", ""),
            intent,
            _j(doc.get("lead_segment", {})),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_winning_examples",
                  ["workspace_id", "lead_id", "campaign_id", "email_subject",
                   "email_body_plain", "intent", "lead_segment", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 36 — Outreach  →  nexus_outreach
# =============================================================================

def migrate_outreach(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: outreach → nexus_outreach ───")
    docs = list(mdb["outreaches"].find({}))
    log.info(f"  Found {len(docs)} outreach records in MongoDB")
    valid_statuses = ("pending", "sent", "opened", "clicked", "replied",
                      "demo_scheduled", "bounced", "unsubscribed")

    # Cache workspace per campaign — used as fallback when outreach.workspace_id
    # is null in Mongo (nexus_outreach.workspace_id is NOT NULL).
    ws_by_camp: Dict[int, Optional[int]] = {}

    rows = []
    for doc in docs:
        lead_pg = _pgid("global_lead", _oid(doc, "lead_id"))
        campaign_pg = _pgid("campaign", _oid(doc, "campaign_id"))
        if not lead_pg or not campaign_pg:
            continue
        ws_pg = _pgid("workspace", _oid(doc, "workspace_id"))
        if not ws_pg:
            if campaign_pg in ws_by_camp:
                ws_pg = ws_by_camp[campaign_pg]
            else:
                cur.execute(
                    "SELECT workspace_id FROM nexus_campaigns WHERE id=%s LIMIT 1",
                    (campaign_pg,),
                )
                r = cur.fetchone()
                ws_pg = r[0] if r else None
                ws_by_camp[campaign_pg] = ws_pg
        if not ws_pg:
            _skip("outreach", "no_workspace_resolvable")
            continue
        rows.append((
            ws_pg,
            lead_pg,
            campaign_pg,
            doc.get("subject", ""),
            (doc.get("to_email") or "").lower(),
            doc.get("email_html", ""),
            _ts(doc, "email_sent_at"),
            _safe_status(doc.get("status", "pending"), valid_statuses, "pending"),
            doc.get("resend_message_id", ""),
            doc.get("gmail_thread_id", ""),
            _ts(doc, "opened_at"),
            _ts(doc, "clicked_at"),
            doc.get("reply_text", ""),
            _ts(doc, "createdAt") or _now(),
        ))
    _batch_insert(cur, "nexus_outreach",
                  ["workspace_id", "lead_id", "campaign_id", "subject",
                   "to_email", "email_html", "email_sent_at", "status",
                   "resend_message_id", "gmail_thread_id",
                   "opened_at", "clicked_at", "reply_text", "created_at"],
                  rows, dry_run)


# =============================================================================
#  PHASE 37 — Settings  →  nexus_settings
# =============================================================================

def migrate_settings(mdb, cur, dry_run: bool) -> None:
    log.info("─── Phase: settings → nexus_settings ───")
    docs = list(mdb["settings"].find({}))
    log.info(f"  Found {len(docs)} settings records in MongoDB")
    rows = []
    for doc in docs:
        user_pg = _pgid("user", _oid(doc, "user_id"))
        if not user_pg:
            continue
        ws_pg = _ws_from_user(cur, user_pg) if not dry_run else None
        if not ws_pg:
            continue
        settings_blob = {k: doc.get(k, "") for k in [
            "resend_api_key", "hunter_api_key", "apollo_api_key",
            "mjml_app_id", "mjml_secret_key", "pexels_api_key",
            "booking_link", "sender_name", "sender_email", "inbound_email",
            "backend_url", "rep_name", "rep_email", "rep_phone",
            "ms_booking_business_id", "ms_graph_subscription_id",
        ]}
        rows.append((
            ws_pg,
            _j(settings_blob),
            _ts(doc, "updatedAt") or _now(),
        ))
    _batch_insert(cur, "nexus_settings",
                  ["workspace_id", "settings", "updated_at"],
                  rows, dry_run)


# =============================================================================
#  Phase registry — ordered by FK dependency
# =============================================================================

PHASES = [
    # name                      function
    ("users",                   migrate_users),                 # must be first
    ("workspaces",              migrate_workspaces),            # needs users
    ("invitations",             migrate_invitations),           # needs workspaces
    ("products",                migrate_products),              # needs workspaces
    ("product_assets",          migrate_product_assets),        # needs products
    ("knowledge_embeddings",    migrate_knowledge_embeddings),  # needs products
    ("global_leads",            migrate_global_leads),          # no FK deps
    ("campaigns",               migrate_campaigns),             # needs workspaces + products
    ("leads",                   migrate_leads),                 # needs campaigns + global_leads
    ("lead_enrichment",         migrate_lead_enrichment),       # no FK deps
    ("intent_signals",          migrate_intent_signals),        # needs global_leads
    ("apollo_cache",            migrate_apollo_cache),          # no FK deps
    ("suppression",             migrate_suppression),           # needs workspaces
    ("conversation_accounts",   migrate_conversation_accounts), # needs users
    ("sequences",               migrate_sequences),             # needs workspaces
    ("automations",             migrate_automations),           # needs workspaces + campaigns
    ("lead_sequences",          migrate_lead_sequences),        # needs leads + sequences
    ("touchpoints",             migrate_touchpoints),           # needs lead_sequences
    ("personalization_cache",   migrate_personalization_cache), # needs global_leads + sequences
    ("send_logs",               migrate_send_logs),             # needs users
    ("inbound_leads",           migrate_inbound_leads),         # no FK deps
    ("inbound_threads",         migrate_inbound_threads),       # needs inbound_leads
    ("inbound_messages",        migrate_inbound_messages),      # needs inbound_threads
    ("inbox",                   migrate_inbox),                 # needs global_leads + campaigns
    ("unmatched_replies",       migrate_unmatched_replies),     # no FK deps
    ("processed_gmail",         migrate_processed_gmail),       # no FK deps
    ("linkedin_messages",       migrate_linkedin_messages),     # needs global_leads
    ("voice_calls",             migrate_voice_calls),           # needs global_leads + campaigns
    ("demo_bookings",           migrate_demo_bookings),         # needs global_leads + campaigns
    ("demo_briefings",          migrate_demo_briefings),        # needs demo_bookings
    ("performance_insights",    migrate_performance_insights),  # needs workspaces
    ("conversion_snapshots",    migrate_conversion_snapshots),  # needs workspaces + leads
    ("credit_logs",             migrate_credit_logs),           # needs workspaces
    ("token_usage",             migrate_token_usage),           # needs workspaces
    ("winning_examples",        migrate_winning_examples),      # needs global_leads + campaigns
    ("outreach",                migrate_outreach),              # needs global_leads + campaigns
    ("settings",                migrate_settings),              # needs workspaces (last)
]


# =============================================================================
#  Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NEXUS — MongoDB Atlas to PostgreSQL data migration"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print counts only. Does not write anything to PostgreSQL.",
    )
    parser.add_argument(
        "--only", metavar="PHASE", default=None,
        help=(
            "Run only this one phase. Valid names: "
            + ", ".join(name for name, _ in PHASES)
        ),
    )
    args = parser.parse_args()

    if args.only and args.only not in {name for name, _ in PHASES}:
        print(f"\nERROR: Unknown phase '{args.only}'.\nValid phases:\n  "
              + "\n  ".join(name for name, _ in PHASES))
        sys.exit(1)

    log.info("=" * 65)
    log.info("  NEXUS MongoDB → PostgreSQL Migration")
    log.info("=" * 65)
    if args.dry_run:
        log.info("  MODE: DRY RUN — nothing will be written to PostgreSQL")
    else:
        log.info("  MODE: LIVE — data WILL be written to PostgreSQL")
    log.info("=" * 65)

    # ── connect ───────────────────────────────────────────────────────────────
    try:
        mdb = connect_mongo()
    except Exception as e:
        log.error(f"Cannot connect to MongoDB Atlas: {e}")
        log.error("Check your MONGO_URI value and that your IP is whitelisted in Atlas.")
        sys.exit(1)

    try:
        pg_conn = connect_pg()
    except Exception as e:
        log.error(f"Cannot connect to PostgreSQL: {e}")
        log.error("Check your PG_HOST / PG_USER / PG_PASSWORD / PG_DATABASE values.")
        sys.exit(1)

    cur = pg_conn.cursor()

    # ── pre-flight ────────────────────────────────────────────────────────────
    # Preflight runs even in dry-run mode so we catch missing tables before
    # reporting "would insert" counts that wouldn't actually succeed.
    preflight_check(cur)

    # ── run phases ────────────────────────────────────────────────────────────
    phases_to_run = (
        [(name, fn) for name, fn in PHASES if name == args.only]
        if args.only else PHASES
    )

    errors = []
    for name, fn in phases_to_run:
        try:
            fn(mdb, cur, dry_run=args.dry_run)
            if not args.dry_run:
                pg_conn.commit()
        except Exception as exc:
            log.error(f"Phase '{name}' FAILED: {exc}")
            errors.append((name, str(exc)))
            try:
                pg_conn.rollback()
            except Exception:
                pass

    cur.close()
    pg_conn.close()

    # ── summary ───────────────────────────────────────────────────────────────
    log.info("=" * 65)
    if errors:
        log.warning(f"Migration finished with {len(errors)} error(s):")
        for phase_name, err in errors:
            log.warning(f"  [{phase_name}]  {err}")
        log.info("All other phases completed. Re-run with --only <phase> to retry failed ones.")
    else:
        log.info("  All 37 phases completed successfully.")
        if args.dry_run:
            log.info("  This was a DRY RUN. Run without --dry-run to write the data.")

    # Per-phase skipped-row report — surfaces silent data drops that would
    # otherwise hide behind `continue` statements inside individual phases.
    if _drop_counters:
        log.info("-" * 65)
        log.info("  Skipped rows by phase (FK miss / NOT NULL miss / empty key):")
        for phase_name in sorted(_drop_counters.keys()):
            reasons = _drop_counters[phase_name]
            total = sum(reasons.values())
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
            log.info(f"    {phase_name}: {total}  ({breakdown})")
    log.info("=" * 65)


if __name__ == "__main__":
    main()
