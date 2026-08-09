#!/usr/bin/env python3
"""
Standalone outreach backfill — resolves Mongo->Postgres IDs by querying
the already-migrated Postgres tables. Use after the main migration if
the outreach phase failed or you ran the script with --only.

Read-only on Mongo. Writes only to nexus_outreach (via INSERT ON CONFLICT
DO NOTHING — duplicates from a partial earlier run are skipped).
"""
from __future__ import annotations
import io, sys

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import pymongo
from pymongo import ReadPreference
from bson import ObjectId
import psycopg2
import psycopg2.extras
import logging

# Inline credentials (same as main migration)
MONGO_URI = "mongodb+srv://saisidd07:nexus123@cluster0.xiwxvnk.mongodb.net/?appName=Cluster0"
MONGO_DB_NAME = "spenzo"
PG_HOST     = "pipelyt-db.cmdi2a884dca.us-east-1.rds.amazonaws.com"
PG_PORT     = 5432
PG_DATABASE = "postgres"
PG_USER     = "postgres"
PG_PASSWORD = "NeuZenAI"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("outreach_backfill")


def main():
    mc = pymongo.MongoClient(MONGO_URI, read_preference=ReadPreference.SECONDARY_PREFERRED)
    mdb = mc[MONGO_DB_NAME]
    log.info("Mongo connected")

    pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE, user=PG_USER, password=PG_PASSWORD)
    pg.autocommit = False
    cur = pg.cursor()
    log.info("Postgres connected")

    # Build email→postgres_lead_id and (campaign-name + workspace) maps from Postgres.
    cur.execute("SELECT id, email FROM nexus_global_leads")
    leads_by_email = {r[1].lower(): r[0] for r in cur.fetchall() if r[1]}
    log.info(f"  loaded {len(leads_by_email)} global leads")

    # Build campaign map by name (campaigns may share names across workspaces;
    # take first) — fall back to ANY match.
    cur.execute("SELECT id, name, workspace_id FROM nexus_campaigns ORDER BY id")
    campaigns_by_name = {}
    for r in cur.fetchall():
        if r[1] and r[1] not in campaigns_by_name:
            campaigns_by_name[r[1]] = (r[0], r[2])
    log.info(f"  loaded {len(campaigns_by_name)} campaigns")

    # Iterate Mongo outreaches.
    docs = list(mdb["outreaches"].find({}))
    log.info(f"  found {len(docs)} outreach docs in Mongo")

    # Resolve mongo campaign OIDs → name via Mongo campaigns collection.
    mongo_campaigns = {str(c["_id"]): c for c in mdb["campaigns"].find({})}

    valid_statuses = ("pending", "sent", "opened", "clicked", "replied",
                      "demo_scheduled", "bounced", "unsubscribed")

    rows = []
    skipped = {"no_email_match": 0, "no_campaign_match": 0, "no_workspace": 0}
    for d in docs:
        to_email = (d.get("to_email") or "").lower().strip()
        lead_pg = leads_by_email.get(to_email)
        if not lead_pg:
            skipped["no_email_match"] += 1
            continue
        # Resolve campaign
        camp_mongo_id = str(d.get("campaign_id")) if d.get("campaign_id") else None
        camp_doc = mongo_campaigns.get(camp_mongo_id) if camp_mongo_id else None
        camp_name = None
        if camp_doc:
            ps = camp_doc.get("product_summary") or {}
            camp_name = ps.get("name") or camp_doc.get("product_url") or "Campaign"
        camp_data = campaigns_by_name.get(camp_name) if camp_name else None
        if not camp_data:
            skipped["no_campaign_match"] += 1
            continue
        camp_pg, ws_pg = camp_data
        if not ws_pg:
            skipped["no_workspace"] += 1
            continue
        status = d.get("status", "pending")
        if status not in valid_statuses:
            status = "pending"
        sent_at = d.get("email_sent_at")
        opened_at = d.get("opened_at")
        clicked_at = d.get("clicked_at")
        rows.append((
            ws_pg, lead_pg, camp_pg,
            d.get("subject", ""),
            to_email,
            d.get("email_html", ""),
            sent_at.isoformat() if sent_at else None,
            status,
            d.get("resend_message_id", ""),
            d.get("gmail_thread_id", ""),
            opened_at.isoformat() if opened_at else None,
            clicked_at.isoformat() if clicked_at else None,
            d.get("reply_text", ""),
            d.get("createdAt").isoformat() if d.get("createdAt") else None,
        ))

    log.info(f"  prepared {len(rows)} rows; skipped: {skipped}")

    if rows:
        sql = """
            INSERT INTO nexus_outreach
              (workspace_id, lead_id, campaign_id, subject, to_email, email_html,
               email_sent_at, status, resend_message_id, gmail_thread_id,
               opened_at, clicked_at, reply_text, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (lead_id, campaign_id) DO NOTHING
        """
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)
        pg.commit()
        log.info(f"  inserted (subject to ON CONFLICT dedup)")

    # Final count
    cur.execute("SELECT COUNT(*) FROM nexus_outreach")
    log.info(f"  nexus_outreach total rows: {cur.fetchone()[0]}")

    cur.close()
    pg.close()
    mc.close()


if __name__ == "__main__":
    main()
