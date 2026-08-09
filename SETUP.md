# Pipelyt — Local Setup

Autonomous Social Media Agent. FastAPI backend + React frontend + PostgreSQL. Multi-agent content pipeline (Refiner → Cultural → Researcher → Copywriter → Art Director → gpt-image-2 → Critic) with per-platform publishing across LinkedIn, X, Facebook, Instagram, YouTube, TikTok, Pinterest.

This guide gets you from `git clone` to a working local dev environment.

---

## 1. Prerequisites

Install these before starting:

| Tool | Version | Why |
|---|---|---|
| **Python** | 3.11+ | Backend runtime |
| **Node.js** | 20+ (via nvm recommended) | Frontend build tools |
| **PostgreSQL** | 15+ | App database |
| **Git** | 2.40+ | Cloning + hooks |
| **AWS CLI** *(optional)* | latest | For inspecting S3 cost ledgers |

Windows users: use PowerShell or Git Bash. Every command below works in both unless noted.

You'll also need accounts / API keys for:
- **OpenAI** (gpt-5.1 for Art Director, gpt-image-2 for image rendering)
- **Google AI Studio** (Gemini flash-lite for text agents)
- **AWS** (S3 bucket for uploaded images, PDFs, and cost-ledger CSVs)
- **Social platforms** for publishing — LinkedIn, X, Meta (FB+IG), YouTube, TikTok, Pinterest (each needs its own OAuth app)

---

## 2. Clone

```bash
git clone https://github.com/NEUZENAI-IT-SOLUTIONS-PVT-LTD/nai_pipelyt.git
cd nai_pipelyt
```

Repo layout:
```
nai_pipelyt/
├── apps/
│   ├── backend/          FastAPI app, agents, publishing, analytics
│   └── product-page/     React + Vite frontend
├── docs/                 Architecture notes, agent audits
├── docker-compose.yml    Optional Postgres + backend container
└── SETUP.md              (this file)
```

---

## 3. PostgreSQL

Create a database and note the connection string:

```bash
# macOS / Linux (with psql on PATH)
createdb pipelyt_dev
```

```powershell
# Windows PowerShell (psql on PATH)
& "C:\Program Files\PostgreSQL\15\bin\createdb.exe" -U postgres pipelyt_dev
```

Connection string will look like:
```
postgresql://<user>:<password>@localhost:5432/pipelyt_dev
```

The backend auto-syncs its schema on first start — you don't need to run migrations manually.

---

## 4. Backend setup

### 4a. Virtual environment

```bash
cd apps/backend
python -m venv venv

# Activate
source venv/bin/activate            # macOS / Linux
.\venv\Scripts\Activate.ps1         # Windows PowerShell
```

### 4b. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4c. Environment file

Create `apps/backend/.env` with the following. Everything commented out is optional — the app boots with just the required lines and skips the disabled platforms.

```dotenv
# ─── Required ──────────────────────────────────────────────
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/pipelyt_dev
SECRET_KEY=any-long-random-string-for-jwt-signing

# AI providers
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...

# S3 (Pipelyt uses S3 for uploaded media, AI-generated images, cost ledgers)
S3_BUCKET_NAME=your-bucket-name
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Feature flags
NEXUS_ENABLED=false                 # true only if you're wiring up NEXUS outreach
NEXUS_SKIP_MIGRATIONS=true

# ─── Optional: social platform OAuth ───────────────────────
# LinkedIn
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:5173/oauth/linkedin

# Meta (Facebook + Instagram share the same app)
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=http://localhost:5173/oauth/meta

# X / Twitter
TWITTER_CLIENT_ID=
TWITTER_CLIENT_SECRET=
TWITTER_REDIRECT_URI=http://localhost:5173/oauth/twitter

# YouTube (Google Cloud OAuth)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:5173/oauth/google

# TikTok
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=http://localhost:5173/oauth/tiktok

# Pinterest
PINTEREST_APP_ID=
PINTEREST_APP_SECRET=
PINTEREST_REDIRECT_URI=http://localhost:5173/oauth/pinterest

# ─── Optional: email (for team invites) ────────────────────
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=

# ─── Optional: Canva (for the Design with Canva flow) ──────
CANVA_CLIENT_ID=
CANVA_CLIENT_SECRET=
```

### 4d. Run the backend

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

You should see logs like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
[pipelyt] Syncing database schema for Business DNA fields...
[pipelyt] drafts.thumbnail_url column synced
[pipelyt] scheduled_posts.thumbnail_url column synced
INFO:     Application startup complete.
```

Health check: open `http://localhost:8000/docs` — FastAPI's auto-generated Swagger UI should list every endpoint.

---

## 5. Frontend setup

Open a **second terminal** (leave the backend running in the first):

```bash
cd apps/product-page
npm install
npm run dev
```

Vite serves at `http://localhost:5173`. Open it in a browser. The dev server proxies `/api/*` calls to `http://localhost:8000` — no CORS setup needed.

---

## 6. Seed a user & sign in

The backend seeds a default admin on first start. Check the terminal log for a line like:

```
[pipelyt] Seeding process complete.
```

Sign up flow via the UI creates a new tenant. Alternatively hit the seed endpoint directly:

```bash
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"changeme123","full_name":"You"}'
```

Log in at `http://localhost:5173` and you should land on the Dashboard.

---

## 7. Verify the AI pipeline

Simplest smoke test: create a **text** campaign (Agent Post → Text). Text posts skip the image model, so this runs in ~15s and only costs Gemini tokens.

Watch the backend log — you should see:

```
[TRACE] refine BEGIN → refine END dur=3.2s
[CULTURAL] cached for 2026-07-13
[TRACE] research BEGIN → research END dur=8s
[TRACE] content BEGIN → content END dur=5s
[cost_ledger] row #N written env=local user=... post_type='text' total=$0.014
```

If you see the `[cost_ledger] row #N written` line at the end, the full backend pipeline works.

For the image pipeline, create an image campaign — expect ~200s wall-clock (gpt-image-2 renders take ~150s each and run in parallel).

---

## 8. Optional: Docker Compose

If you'd rather run Postgres + the backend containerized:

```bash
docker-compose up
```

`docker-compose.yml` at the repo root spins up:
- `postgres:15` on `localhost:5432` with a seeded database
- `pipelyt-backend` built from `apps/backend/Dockerfile`

The frontend is not containerized — run `npm run dev` on the host as usual.

---

## 9. Common issues

| Symptom | Fix |
|---|---|
| `FATAL: password authentication failed` on backend start | Wrong `DATABASE_URL` — try `postgresql://postgres:postgres@localhost:5432/pipelyt_dev` |
| `[cost_ledger] S3 not configured — skipping upload` | `S3_BUCKET_NAME` blank or S3 keys wrong. Local dev works without S3; you just won't see the S3 mirror of the cost CSV. |
| Frontend loads but every API call returns 401 | Not logged in. Sign up, or check the JWT cookie in browser devtools. |
| `Agent parsing error in RESEARCHER: Expecting ',' delimiter` | Harmless — Gemini's grounded call sometimes returns invalid JSON. Pipeline auto-retries with `web_search=False`. |
| gpt-image-2 fails with 429 | OpenAI image quota exhausted — check your OpenAI billing dashboard. |
| Port 8000 already in use | Kill the other process: `netstat -ano \| grep :8000` (Windows) or `lsof -i :8000` (macOS/Linux) then `taskkill /F /PID <pid>` or `kill -9 <pid>` |

---

## 10. Where to look next

| File | What it does |
|---|---|
| `apps/backend/main.py` | FastAPI entry, schema-sync on startup |
| `apps/backend/routers/content.py` | `/generate-content`, `/generate-visuals`, `/schedule`, `/post` |
| `apps/backend/services/ai_service.py` | Orchestrator for the 7-agent pipeline |
| `apps/backend/services/agents.py` | Individual Gemini agent prompts (Refiner, Cultural, Researcher, Copywriter, Critic) |
| `apps/backend/services/magic_image_pipeline.py` | Art Director (gpt-5.1) + Image Generator (gpt-image-2) |
| `apps/backend/services/carousel_pipeline.py` | Multi-slide PDF carousel generation |
| `apps/backend/services/style_catalog.py` | 15-style visual catalog + dynamic Art Director system prompt |
| `apps/backend/services/cost_ledger.py` | Per-request time & cost CSV — mirrors to S3 |
| `apps/product-page/src/pages/Dashboard.jsx` | Agent Post composer |
| `apps/product-page/src/pages/Dashboard/components/CampaignBrief.jsx` | Brief input + chip picker (Business DNA, Logo, Aspect Ratio, Style, Channels, Strategy) |
| `apps/product-page/src/pages/UserGuide.jsx` | End-user documentation |

---

## 11. Contributing

Before opening a PR:

```bash
# Backend syntax check
cd apps/backend && python -c "import ast, io; [ast.parse(io.open(f, encoding='utf-8').read()) for f in ['main.py']]"

# Frontend build
cd apps/product-page && npm run build
```

Commit style — one commit per logical change, descriptive subject, "why" in the body.

That's it. Welcome to the codebase.
