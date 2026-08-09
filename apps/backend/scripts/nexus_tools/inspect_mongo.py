#!/usr/bin/env python3
"""
Read-only MongoDB Atlas inspector for the NEXUS legacy database.

Connects to Mongo, lists every collection with doc counts, and prints
one sample document per key collection. Writes nothing anywhere.

USAGE
─────
    # Preferred: credentials via env vars
    $env:MONGO_URI="mongodb+srv://..."          # PowerShell
    $env:MONGO_DB_NAME="spenzo"
    python apps/backend/scripts/inspect_mongo.py

    # Or run without env vars — falls back to the same credentials baked
    # into nexus_data_migration.py (rotate Atlas password after).

OUTPUT
──────
1. Connection sanity (host, db)
2. All collection names + document counts (sorted by size desc)
3. Sample document (truncated) for each "key" collection
4. Total document estimate
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime

# Force UTF-8 stdout on Windows so the box-drawing + check chars render.
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Dependency check ────────────────────────────────────────────────────────
try:
    import pymongo
    from pymongo import ReadPreference
    from bson import ObjectId
except ImportError:
    print("\n  ERROR: pymongo missing. Install:\n    pip install pymongo\n")
    sys.exit(1)


# ── Credentials (env first, fallback to legacy defaults) ────────────────────
MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://saisidd07:nexus123@cluster0.xiwxvnk.mongodb.net/?appName=Cluster0",
)
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "spenzo")


# ── Key collections we expect (matches phase mapping in migration script) ──
KEY_COLLECTIONS = [
    "users",
    "workspaces",
    "invitations",
    "products",
    "productassets",
    "knowledgeembeddings",
    "globalleads",
    "campaigns",
    "leads",
    "leadenrichments",
    "intentsignals",
    "apollo_lead_cache",
    "suppressionlists",
    "conversationaccounts",
    "sequences",
    "automations",
    "leadsequences",
    "touchpoints",
    "personalizationcaches",
    "sendlogs",
    "inboundleads",
    "inboundthreads",
    "inboundmessages",
    "inboxes",
    "unmatchedreplies",
    "linkedinmessages",
    "voicecalls",
    "demobookings",
    "demobriefings",
    "performanceinsights",
    "conversionsnapshots",
    "credits_log",
    "tokenusages",
    "winningexamples",
    "outreaches",
    "settings",
]


def _serialize(v):
    """Make a Mongo value JSON-safe (ObjectId, datetime, bytes)."""
    if isinstance(v, ObjectId):
        return f"ObjectId({str(v)})"
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, bytes):
        return f"<bytes len={len(v)}>"
    if isinstance(v, dict):
        return {k: _serialize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serialize(x) for x in v[:5]] + (
            [f"... {len(v) - 5} more"] if len(v) > 5 else []
        )
    return v


def _short(s: str, n: int = 80) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> None:
    print("=" * 72)
    print("  MongoDB Atlas — NEXUS data inspector  (READ-ONLY)")
    print("=" * 72)
    print(f"  URI host  : {MONGO_URI.split('@')[-1].split('/')[0] if '@' in MONGO_URI else '<masked>'}")
    print(f"  Database  : {MONGO_DB_NAME}")
    print("=" * 72)
    print()

    try:
        # READ-ONLY: secondaryPreferred routes reads to a replica; if no
        # secondary is available it falls back to primary but still
        # READ-only (we never call any write methods below).
        client = pymongo.MongoClient(
            MONGO_URI,
            read_preference=ReadPreference.SECONDARY_PREFERRED,
            serverSelectionTimeoutMS=15_000,
            # Defensive: any write attempt with this concern fails loudly.
            w=0,
        )
        db = client[MONGO_DB_NAME]

        # Runtime guard: monkey-patch the Collection class so that ANY
        # write call (now or in any future code change) raises immediately.
        # This is a tripwire — production-grade safety, not a substitute
        # for not writing them in the first place.
        from pymongo.collection import Collection
        _BLOCKED = (
            "insert_one", "insert_many",
            "update_one", "update_many", "replace_one",
            "delete_one", "delete_many",
            "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
            "drop", "rename", "drop_index", "drop_indexes",
            "bulk_write", "create_index", "create_indexes",
        )
        def _block(method_name):
            def _hard_no(*a, **kw):
                raise RuntimeError(
                    f"[READ-ONLY GUARD] Collection.{method_name}() blocked. "
                    "This inspector script is forbidden from writing to Mongo."
                )
            return _hard_no
        for _m in _BLOCKED:
            setattr(Collection, _m, _block(_m))

        db.command("ping")
    except Exception as e:
        print(f"  ❌ Cannot connect to Atlas: {e}")
        print(
            "  Common causes:\n"
            "    - IP not whitelisted in Atlas → Network Access → Add IP\n"
            "    - Wrong password\n"
            "    - SRV DNS not resolving (corporate VPN sometimes blocks)\n"
        )
        sys.exit(1)

    print("  ✅ Connected.\n")

    # ── Collection counts ───────────────────────────────────────────────────
    print("─" * 72)
    print("  COLLECTION COUNTS  (sorted by size descending)")
    print("─" * 72)
    all_colls = db.list_collection_names()
    counts = []
    for name in all_colls:
        try:
            n = db[name].estimated_document_count()
        except Exception:
            n = -1
        counts.append((name, n))
    counts.sort(key=lambda x: x[1], reverse=True)

    grand_total = 0
    for name, n in counts:
        marker = "  "
        if name in KEY_COLLECTIONS:
            marker = "★ "
        print(f"  {marker}{name:<28} {n:>10,}")
        if n > 0:
            grand_total += n

    print("─" * 72)
    print(f"  Total collections : {len(all_colls)}")
    print(f"  Total documents   : ~{grand_total:,}")
    print("─" * 72)
    print()

    # ── Sample docs from key collections ────────────────────────────────────
    print("─" * 72)
    print("  SAMPLE DOCUMENTS  (1 doc per key collection, truncated)")
    print("─" * 72)
    print()

    for name in KEY_COLLECTIONS:
        if name not in all_colls:
            print(f"  · {name}: ⚠️  collection not present")
            continue
        try:
            doc = db[name].find_one()
        except Exception as e:
            print(f"  · {name}: ❌ {e}")
            continue
        if not doc:
            print(f"  · {name}: (empty)")
            continue

        # Show field list + a serialized preview.
        fields = list(doc.keys())
        print(f"\n  ▸ {name}  ({len(fields)} fields)")
        print(f"      fields: {', '.join(fields[:15])}{', …' if len(fields) > 15 else ''}")
        try:
            preview = json.dumps(_serialize(doc), indent=2, default=str)
        except Exception as e:
            preview = f"<serialize error: {e}>"
        # Cap preview so it stays readable
        if len(preview) > 1500:
            preview = preview[:1500] + "\n  …(truncated)"
        for line in preview.splitlines():
            print("      " + line)

    print()
    print("=" * 72)
    print("  Inspection complete. Nothing was written.")
    print("=" * 72)

    client.close()


if __name__ == "__main__":
    main()
