# TikTok App Setup Guide for Pipelyt

**Written for a non-technical person.** Follow every step in order. Don't skip.

---

## What you will have when you finish

1. A TikTok Developer account.
2. A TikTok app named "Pipelyt".
3. Three products enabled on the app (Login Kit, Content Posting API, Display API).
4. Two secret values (`Client Key` + `Client Secret`) that you will paste into Pipelyt's `.env` file.
5. A submitted app-review request so Pipelyt can post videos to real TikTok users (not just yourself).

---

## Before you begin — have these ready

Keep these in a notepad. You will paste them into forms.

| Item | Example | What it's for |
|---|---|---|
| Company name | `Neuzen AI` | App branding |
| App name | `Pipelyt` | Shown to end-users during OAuth |
| App description (1 sentence) | `AI-powered social media scheduler that helps creators and small businesses plan and publish content across TikTok, YouTube, LinkedIn, and more.` | Review form |
| Terms of Service URL | `https://www.pipelyt.ai/terms` | Required |
| Privacy Policy URL | `https://www.pipelyt.ai/privacy` | Required |
| App homepage URL | `https://www.pipelyt.ai` | Required |
| Business email | `contact@neuzenai.com` | Contact |
| Production redirect URL | `https://app.pipelyt.ai/auth/tiktok/callback` | OAuth |
| Staging redirect URL | `https://staging.app.pipelyt.ai/auth/tiktok/callback` | OAuth |
| App icon | Square PNG, 512×512, transparent background | Required |
| Demo video | 90-second screen recording of "connect TikTok → schedule post → publish" flow | Required for review |

If you don't have the privacy policy or terms URLs live yet, **publish them first**. TikTok's review will reject the app if these URLs return 404.

---

## Step 1 — Create a TikTok Developer account

1. Open **<https://developers.tiktok.com>** in your browser.
2. Top-right corner → click **"Log in"**.
3. Sign in with the TikTok account you want to own the Pipelyt developer profile. Use a business TikTok account if you have one — not a personal one tied to a team member who might leave.
4. On first login TikTok will ask you to accept the **Developer Terms of Service**. Tick the box and click **"Continue"**.
5. Fill in the developer profile:
   - **Name** — your name
   - **Organization** — `Neuzen AI` (your company)
   - **Country / region** — your country
   - **Phone number** — you'll receive an SMS code. Enter it.
   - **Email** — `contact@neuzenai.com`. TikTok will send a verification email. Open it and click the link.
6. Click **"Submit"**. You should land on the Developer Portal dashboard.

✅ **Checkpoint:** at the top-right you should see your name + a **"Manage apps"** button.

---

## Step 2 — Create the Pipelyt app

1. In the top navigation, click **"Manage apps"**.
   Direct link: **<https://developers.tiktok.com/apps>**
2. Click the **"Connect an app"** button (top-right of the page).
3. Fill out the **"App details"** form:

   | Field | What to enter |
   |---|---|
   | App name | `Pipelyt` |
   | App icon | Upload your 512×512 PNG |
   | Category | `Productivity` (or `Social Networking` — either is fine) |
   | Description | `AI-powered social media management tool that helps creators, small businesses, and marketing teams plan, draft, schedule, and publish content to TikTok from a unified dashboard. Users authenticate their own TikTok account and review every post before publishing.` |
   | Platform | Tick **Web** |
   | Web / Desktop URL | `https://app.pipelyt.ai` |
   | Terms of Service URL | `https://www.pipelyt.ai/terms` |
   | Privacy Policy URL | `https://www.pipelyt.ai/privacy` |

4. Click **"Confirm"** at the bottom.
5. You'll land on the app's detail page. **At the top of this page you will see two values:**
   - **Client Key** (starts with letters/digits, ~18-24 chars)
   - **Client Secret** (click the eye icon to reveal — ~40 chars)

   **Copy both now into your notepad.** You will paste them into `.env` at Step 6.

✅ **Checkpoint:** the page header shows "Pipelyt" with a Client Key and Client Secret visible.

---

## Step 3 — Add the three required products

On the left sidebar of the app page, click **"Add products"**. You'll see a grid of products.

Add these **three** (click "Add" on each one):

### 3a. Login Kit
- **What it does:** Lets users log in to Pipelyt using their TikTok account.
- **Required scopes (tick these checkboxes when the product form opens):**
  - `user.info.basic` ✅
  - `user.info.profile` ✅
  - `user.info.stats` ✅
- **Redirect URI** — click "Add redirect URI" and paste each on a separate line:
  - `https://app.pipelyt.ai/auth/tiktok/callback`
  - `https://staging.app.pipelyt.ai/auth/tiktok/callback`

  Then click **"Save"**.

### 3b. Content Posting API
- **What it does:** Lets Pipelyt publish videos and photos to the user's TikTok on their behalf.
- **Required scopes (tick these):**
  - `video.publish` ✅ (direct post — video appears on user's profile)
  - `video.upload` ✅ (upload to user's drafts/inbox for manual finish)
- Click **"Save"**.

### 3c. Display API
- **What it does:** Lets Pipelyt read the user's own videos to show stats (views, likes, comments, shares) in the Pipelyt analytics dashboard.
- **Required scopes (tick these):**
  - `video.list` ✅
- Click **"Save"**.

✅ **Checkpoint:** the left sidebar now shows three products under "My products": **Login Kit**, **Content Posting API**, **Display API**.

---

## Step 4 — Verify URL properties (REQUIRED for posting)

TikTok requires domain ownership proof before it will allow posting from your servers.

1. Left sidebar → **"URL properties"** (sometimes called "Manage URL properties").
2. Click **"Add property"**.
3. Enter: `https://app.pipelyt.ai` → click **"Next"**.
4. TikTok shows two verification methods. Pick **"HTML file upload"** (easiest):
   - Download the HTML file TikTok gives you (named like `tiktok-developers-site-verification.html`).
   - Upload it to the root of your production frontend so it's reachable at `https://app.pipelyt.ai/tiktok-developers-site-verification.html`.
   - **If you can't upload a file** (no filesystem access), switch to the **"Meta tag"** method instead. Copy the `<meta name="tiktok-developers-site-verification" content="xxx" />` tag and paste it into your site's `<head>` section.
5. Click **"Verify"** back in the TikTok portal.
6. Repeat for `https://staging.app.pipelyt.ai` (same steps).

✅ **Checkpoint:** both domains show a green **"Verified"** badge.

⚠️ If you skip this step, API calls using `PULL_FROM_URL` will fail with error `url_ownership_unverified`.

---

## Step 5 — Fill in "Trust & safety" / "App review" fields

Still in the Pipelyt app page, left sidebar → **"Basic information"**. Fill these if they aren't already set:

| Field | What to paste |
|---|---|
| Category | `Productivity` |
| Description | Same description you wrote in Step 2 |
| Terms of Service URL | `https://www.pipelyt.ai/terms` |
| Privacy Policy URL | `https://www.pipelyt.ai/privacy` |
| Contact email | `contact@neuzenai.com` |

Click **"Save"**.

---

## Step 6 — Copy the keys into Pipelyt's `.env` file

Open `apps/backend/.env` (create it from `.env.example` if it doesn't exist yet).

Add these **four** lines at the bottom:

```
# TikTok
TIKTOK_CLIENT_KEY=paste_your_client_key_here
TIKTOK_CLIENT_SECRET=paste_your_client_secret_here
TIKTOK_REDIRECT_URI=https://staging.app.pipelyt.ai/auth/tiktok/callback
TIKTOK_API_BASE_URL=https://open.tiktokapis.com
```

For production Lambda, set the same four variables in **AWS Lambda → Configuration → Environment variables** (with the production redirect URL instead of staging).

**🚨 NEVER commit `.env` to git.** Double-check `.gitignore` contains `.env`.

---

## Step 7 — Understand Sandbox mode (important)

Your app is now in **Sandbox mode**. This means:

- ✅ You can test the OAuth login flow end-to-end.
- ✅ You can upload videos through the Content Posting API.
- ⚠️ **All uploaded videos are forced to `SELF_ONLY` privacy** (only the uploading user can see them, even if you set `PUBLIC_TO_EVERYONE` in the request).
- ⚠️ Only target users listed on the app are allowed to log in.

To test in sandbox:
1. Left sidebar → **"Sandbox"**.
2. Click **"Add target user"**.
3. Enter the TikTok username of the tester (e.g. your own account or a team member's). Up to 10 testers.

This is enough for development. To **publish publicly to real users**, you need App Review (Step 8).

---

## Step 8 — Submit the app for review (to leave Sandbox)

1. Left sidebar → **"App Review"** (sometimes called "Production Release").
2. Click **"Submit for review"**.
3. TikTok shows a multi-section form. Here's what each section asks for and what to paste:

### Section A — App information
Mostly auto-filled from Step 2. Confirm everything is accurate. Upload a **higher-quality app icon** if yours looks blurry.

### Section B — Use case description

**Prompt:** *"How will your app use TikTok's APIs?"*

**Paste this:**

```
Pipelyt is a SaaS content-management dashboard at https://app.pipelyt.ai
used by creators, small businesses, and marketing teams to plan and
publish content across multiple social networks (including TikTok,
YouTube, LinkedIn, Twitter/X, Facebook, Instagram, and Pinterest) from
one unified interface.

TikTok-specific usage:

1. LOGIN KIT — users authenticate their own TikTok account via OAuth2
   so Pipelyt can act on their behalf. We only read identity and public
   profile data (display name, avatar, follower/following/likes/video
   counts) to personalize the dashboard.

2. CONTENT POSTING API — users compose a video or photo post in
   Pipelyt, review it, and click "Publish Now" or schedule it for
   later. Pipelyt uploads the media to TikTok via the
   /v2/post/publish/video/init/ and /v2/post/publish/content/init/
   endpoints. Users ALWAYS review captions, privacy settings,
   duet/stitch/comment toggles, and branded-content disclosure before
   publishing. There is no fully autonomous posting.

3. DISPLAY API — after a post is published, Pipelyt fetches basic
   video metrics (view_count, like_count, comment_count, share_count)
   via /v2/video/query/ to display in the user's analytics dashboard.

We do NOT scrape other users' content, do NOT train AI models on
TikTok data, and do NOT redistribute or resell any TikTok data. All
content displayed in Pipelyt belongs to the authenticated user.
```

### Section C — Scopes justification

For each scope you enabled, TikTok wants a one-line reason. Paste:

| Scope | Justification |
|---|---|
| `user.info.basic` | Display the user's avatar and display name in the Pipelyt connected-accounts panel. |
| `user.info.profile` | Show the user's public TikTok profile link in the Pipelyt account settings. |
| `user.info.stats` | Show follower count, following count, likes, and video count in the Pipelyt analytics overview. |
| `video.publish` | Publish the user's composed video directly to their TikTok profile when they click "Publish" in Pipelyt. |
| `video.upload` | Upload videos to the user's TikTok drafts/inbox when they choose "Send to TikTok app to finish" instead of direct post. |
| `video.list` | List the user's own TikTok videos to display performance metrics in the Pipelyt analytics dashboard. |

### Section D — Demo video

Upload a **60-120 second screen recording** (MP4, under 50 MB) showing:

1. User lands on Pipelyt dashboard (3s).
2. User clicks "Connect TikTok" → redirects to TikTok OAuth page → logs in → grants scopes → returns to Pipelyt with the account now connected (20s).
3. User clicks "Create Post" → composes a video with caption → picks privacy (Public / Friends / Only Me) → toggles comments/duet/stitch → clicks "Publish Now" (40s).
4. Pipelyt shows a success message. User opens the TikTok app on their phone and the video is live on their profile (15s).
5. User returns to Pipelyt → opens "Analytics" tab → sees view/like/comment counts for the video (10s).

Record with **Loom** (free: <https://www.loom.com>), or OBS, or the built-in Windows **Xbox Game Bar** (Win+G).

### Section E — Platform & Region
- **Platforms where the app is live:** Web
- **Regions:** pick all regions Pipelyt will operate in (start with your home country + US + EU).

### Section F — Security

**Prompt:** *"How do you store and secure user tokens?"*

**Paste this:**

```
Access tokens and refresh tokens are stored encrypted at rest in our
AWS RDS PostgreSQL database (AES-256 via AWS RDS encryption + column-
level encryption for the token fields). TLS 1.2+ is enforced on all
network traffic between the Pipelyt frontend, our AWS Lambda backend,
and TikTok's APIs. Tokens are never logged, never exposed to
client-side code, and never shared with third parties. Users can
disconnect their TikTok account from the Pipelyt dashboard at any
time, which immediately deletes the stored tokens from our database.
```

### Section G — Data handling

**Prompt:** *"What user data do you collect, and how long do you retain it?"*

**Paste this:**

```
Collected:
- TikTok open_id and union_id (required for API calls)
- Display name and avatar URL (UI display only)
- Follower/following/likes/video counts (analytics display)
- Video IDs + public metrics (view/like/comment/share counts) of the
  user's OWN videos, pulled via /v2/video/query/

Retention:
- Tokens and profile data: retained until the user disconnects their
  TikTok account from Pipelyt, then deleted within 24 hours.
- Published-post metadata: retained for as long as the user's Pipelyt
  account is active, so they can see historical analytics. Deleted
  within 30 days of account deletion.

We do NOT collect data about any user other than the authenticated
Pipelyt user. We do NOT collect private / direct messages, contacts,
or videos we do not own.
```

4. Click **"Submit for review"** at the bottom.

---

## Step 9 — Wait for review

- **Typical response time:** 3-7 business days.
- TikTok emails the contact address with approval or a rejection reason.
- **If rejected:** the email will list specific items to fix. Address each one, re-record the demo video if required, and resubmit. Most apps get approved on the second or third attempt.

**While you wait:**
- Keep developing against Sandbox mode. All code you write against the API works identically — only the `privacy_level` is forced to `SELF_ONLY`. Once approved, that restriction lifts automatically.
- Start the same setup for **YouTube** and **Pinterest** in parallel (those reviews run concurrently).

---

## Step 10 — After approval: go-live checklist

Once TikTok emails approval:

1. Left sidebar → **"App Review"** → status changes to **"Production"**.
2. Update `.env` on production Lambda:
   ```
   TIKTOK_REDIRECT_URI=https://app.pipelyt.ai/auth/tiktok/callback
   ```
3. Test with a non-sandbox TikTok account by logging in to Pipelyt and publishing a public video. Confirm it appears on the account's public profile.
4. Turn on TikTok in the Pipelyt connected-accounts UI for all users.

---

## Quick reference — what's in `.env`

```bash
# TikTok — obtained from https://developers.tiktok.com/apps → your Pipelyt app
TIKTOK_CLIENT_KEY=                         # Step 2, "Client Key"
TIKTOK_CLIENT_SECRET=                      # Step 2, "Client Secret" (eye icon to reveal)
TIKTOK_REDIRECT_URI=https://app.pipelyt.ai/auth/tiktok/callback
TIKTOK_API_BASE_URL=https://open.tiktokapis.com
```

---

## Quick reference — which scopes for what

| Feature in Pipelyt | Scope |
|---|---|
| User clicks "Connect TikTok" | `user.info.basic` |
| Show user's profile link | `user.info.profile` |
| Show follower/like counts on dashboard | `user.info.stats` |
| User clicks "Publish Now" to TikTok | `video.publish` |
| User clicks "Save as Draft to TikTok" | `video.upload` |
| Analytics dashboard showing video metrics | `video.list` |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| OAuth page shows "redirect_uri mismatch" | The URL in `.env` must match EXACTLY what you entered in Login Kit → Redirect URI (including trailing slash and https). |
| Publish returns `url_ownership_unverified` | You skipped Step 4. Go verify the domain in URL properties. |
| Publish always returns `SELF_ONLY` | You're still in Sandbox. Complete Step 8 (App Review) and wait for approval. |
| `access_token` expired after 24 hours | Normal. Call `/v2/oauth/token/` with `grant_type=refresh_token` using the stored refresh token. Store the new tokens. |
| Review rejected | Read the email reason carefully, fix the exact item (usually the demo video is incomplete), resubmit. Most apps are approved by attempt #2-3. |

---

## Useful links

- **TikTok Developer Portal (start here):** <https://developers.tiktok.com>
- **Your apps list:** <https://developers.tiktok.com/apps>
- **Content Posting API docs:** <https://developers.tiktok.com/doc/content-posting-api-get-started>
- **Login Kit docs:** <https://developers.tiktok.com/doc/login-kit-web>
- **Display API docs:** <https://developers.tiktok.com/doc/display-api-overview>
- **Scopes list:** <https://developers.tiktok.com/doc/scopes-overview>
- **Content sharing guidelines (read before submitting):** <https://developers.tiktok.com/doc/content-sharing-guidelines>
