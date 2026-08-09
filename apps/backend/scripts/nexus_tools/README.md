# NEXUS one-shot tools (NOT runtime code)

Local-only scripts used to **set up and verify** the NEXUS migration from
MongoDB to Postgres. None of these run in production Lambda — they're
excluded from the Docker image via `apps/backend/.dockerignore`.

Keep them in source control as **reference**: future me / future devs may
need to re-run them after schema changes, or use them as templates for
similar one-shot tasks.

---

## What's here

| File | Purpose | Reads | Writes |
|---|---|---|---|
| `inspect_mongo.py` | List Mongo collections + sample docs (READ-ONLY) | Mongo | nothing |
| `inspect_user_mapping.py` | Check Mongo users vs PIPELYT users (READ-ONLY) | Mongo + Postgres | nothing |
| `nexus_data_migration.py` | Full Mongo → Postgres migration (37 phases) | Mongo | `nexus_*` tables |
| `backfill_outreach.py` | Re-run only the `outreach` phase via direct ID lookup | Mongo | `nexus_outreach` |

---

## Running them

All scripts assume CWD is `apps/backend/` and the venv is active:

```bash
cd apps/backend
source venv/Scripts/activate           # Windows Git Bash
# or: .\venv\Scripts\activate          # Windows PowerShell

# Read-only checks (safe anytime)
python scripts/nexus_tools/inspect_mongo.py
python scripts/nexus_tools/inspect_user_mapping.py

# Migration (writes to Postgres — RDS snapshot first!)
python scripts/nexus_tools/nexus_data_migration.py --dry-run
python scripts/nexus_tools/nexus_data_migration.py
python scripts/nexus_tools/nexus_data_migration.py --only outreach

# Backfill outreach (if main migration's outreach phase failed)
python scripts/nexus_tools/backfill_outreach.py
```

---

## Safety guarantees

- `inspect_*.py` — runtime-blocked from any Mongo write op (monkey-patch tripwire)
- `nexus_data_migration.py` — only INSERTs (uses `ON CONFLICT DO NOTHING` where natural keys exist)
- `backfill_outreach.py` — same INSERT-only pattern

None of these `DROP`, `DELETE`, or `UPDATE` non-NEXUS data.

---

## Credentials (security note)

`nexus_data_migration.py` and `backfill_outreach.py` have hardcoded Mongo Atlas
credentials at the top. **Rotate the Mongo password** in Atlas after migration
is verified done. These files are NEVER deployed to Lambda (`.dockerignore`
excludes the `scripts/` folder).

If/when you re-run these, prefer env vars:
```bash
export MONGO_URI="mongodb+srv://..."
export MONGO_DB_NAME="spenzo"
python scripts/nexus_tools/inspect_mongo.py
```
The inspector scripts already check env vars first; the migration script
falls back to hardcoded values (a dev TODO to clean up later).
