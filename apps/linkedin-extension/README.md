# Pipelyt GTM — LinkedIn Connector (Chrome extension)

Captures a user's **already-logged-in** LinkedIn session (cookies) from their own
browser and sends it, encrypted at rest, to the Pipelyt GTM backend. All outreach
then runs server-side, reusing that session.

**Why this design:** the user logs into LinkedIn normally — solving any 2FA /
CAPTCHA themselves, on their own residential IP — so the platform never has to
automate a login, which is what triggers LinkedIn's bot defenses. The backend
route is `POST /nexus/linkedin-agent/session/token` (see
`apps/backend/gtm/linkedin/router.py`).

## What it sends
- `linkedin_email` — the account being connected
- `cookies` — the raw `linkedin.com` cookies (the backend normalizes them to
  Playwright format and **requires `li_at`**, the session cookie)
- Never the password.

## Build the manifest (URLs come from config, not hand-edited)
```bash
npm run genkey        # ONCE — generates the stable signing key (fixed extension ID)
npm run build         # LOCAL manifest (reads VITE_API_URL + FRONTEND_URL from .env)
npm run build:prod    # PROD manifest (staging.app.pipelyt.ai + the Lambda API)
```
The stable key keeps the extension ID constant across machines, reloads, and the
published build — so the web app's `EXTENSION_ID` never needs to change.

## Load it (developer mode)
1. `npm run build` (or `build:prod`) to generate `manifest.json`.
2. Chrome → `chrome://extensions` → enable **Developer mode**.
3. **Load unpacked** → select this `apps/linkedin-extension/` folder.

## How a user connects
**One-click (recommended):** in Pipelyt GTM → *Connectors → Connect LinkedIn →
Browser session*, the page hands a short-lived connect token to the extension
automatically (`chrome.runtime.sendMessage`) — the user just clicks **Connect via
extension**. No token copying.

**Manual fallback:** the user clicks **"Copy connect token"** in the same modal,
then opens the extension popup → Settings → pastes the API URL + token → enters
their LinkedIn email → **Connect**.

## Backend auth
The extension posts to `POST /nexus/linkedin-agent/session/token` with
`Authorization: Bearer <connect-token>`. The connect token is a short-lived (30-min)
signed JWT minted by `POST /nexus/linkedin-agent/connect-token`, encoding the
workspace / user / brand — so the extension never needs the app session cookie.
Token expired? Re-issue it (re-click Connect, or "Copy connect token").

## Re-connect on expiry
When the backend detects an expired session it marks the account
`reauth_required`. Prompt the user to **Connect** again — the extension re-pushes
fresh cookies. No manual work from the team.

## Files
| File | Purpose |
|---|---|
| `gen-key.js` | Generates the stable signing key (run once) |
| `build-manifest.js` | Generates `manifest.json` (local from `.env`, or `--prod`) |
| `background.js` | Service worker — auto-handoff (`pipelyt_connect`) + cookie capture |
| `popup.html` / `popup.js` | Manual fallback UI |
| `key.pem` | **Private** signing key — gitignored, back it up securely |
| `manifest-key.txt` | Public key embedded in the manifest (committed) |
