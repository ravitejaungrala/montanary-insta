# Facebook (Meta) App Review — Complete Walkthrough for Pipelyt

**Audience:** Engineer / founder running the submission
**Purpose:** Get production-grade access to Facebook Pages + Instagram Business APIs so any user (not just developers/testers) can connect their accounts and let Pipelyt publish on their behalf.
**Time:** 2–4 days of prep + 5–14 days of Meta review.
**Last updated:** 2026-04-30.

**Project context (filled in for your account):**
| Field | Value |
|---|---|
| Company / brand | **NeuzenAI** (Pipelyt is one of NeuzenAI's products) |
| Meta Developer app admin email | `salman@neuzenai.com` (the account that created the app) |
| Meta Business Manager owner email | `contact@neuzenai.com` |
| Public business name on submission | **NeuzenAI** |
| Product being reviewed | **Pipelyt** |
| Production app URL | `https://app.pipelyt.ai` |
| Production API URL | `https://api.pipelyt.ai` |
| Privacy Policy URL | `https://pipelyt.ai/privacy` |
| Terms URL | `https://pipelyt.ai/terms` |
| Data Deletion URL | `https://api.pipelyt.ai/data-deletion` |

---

## Why you need this

Until your app passes review, Meta only lets the following people log into and use the Facebook/Instagram features:
- The app's **administrators**, **developers**, and **testers** (users you've added on the developer dashboard)
- Up to ~25 such users per app

Real public users who try to **Connect Facebook** or **Connect Instagram Business** in Pipelyt will see an error like *"This app is in development mode."* App Review unlocks the permissions for everyone.

---

## Permissions Pipelyt needs (and the access type for each)

These come straight out of `apps/backend/services/social_service.py` and the Meta scopes the OAuth flow requests.

### Access types — what to pick

Meta's dashboard offers two access levels per permission:

| Type | What it means | Who can use it | When to pick |
|---|---|---|---|
| **Standard Access** | Default tier. No review required. | Only people you've added as **Administrator / Developer / Tester** on the app (capped at ~25 across roles) | Dev / staging / internal testing only |
| **Advanced Access** | Production tier. Requires App Review approval. | **All** end users (anyone who clicks Connect Facebook) | Mandatory for every permission you need to ship to public users |

For Pipelyt to work for paying customers, **every permission below must be at Advanced Access**. If even one stays at Standard, customers will see a "this app is in Development mode" error during the OAuth grant for the affected permission.

### Where you set it

Meta App Dashboard → **App Review → Permissions and Features** → each permission has a row with a button:
- "**Get advanced access**" (if you currently have Standard)
- "Active" badge (if Advanced is already approved)

Clicking "Get advanced access" opens the App Review submission modal. You fill the form once per permission and submit them all together.

### The permission table — every one needs Advanced Access

| Permission | Access type to request | Why Pipelyt needs it | What review will see |
|---|---|---|---|
| `pages_show_list` | **Advanced** | Show the user the Facebook Pages they admin so they can pick one to connect | Connect Facebook → modal listing pages |
| `pages_read_engagement` | **Advanced** | Read post insights (impressions, likes) for the Analytics dashboard | Analytics page → engagement KPIs |
| `pages_read_user_content` | **Advanced** | Read comments on Pipelyt-published posts so the Reputation page can show them | Reputation page → comments stream |
| `pages_manage_posts` | **Advanced** | Publish text/image/video/PDF carousel posts to the connected Page | Agent Post → Publish button |
| `pages_manage_engagement` | **Advanced** | Reply to comments from the Reputation page / "Add a comment" composer | Reputation → AI reply Send button |
| `instagram_basic` | **Advanced** | Access the Instagram Business account linked to the Page | Connect Instagram Business |
| `instagram_content_publish` | **Advanced** | Publish posts/reels to Instagram | Agent Post → Publish to Instagram |
| `instagram_manage_comments` | **Advanced** | List and reply to Instagram comments | Reputation page Instagram tab |
| `instagram_manage_insights` | **Advanced** | Pull per-post Instagram metrics | Analytics → Instagram engagement |
| `business_management` | **Advanced** | Required by Meta to manage Business-Asset-owned Pages/IG accounts | Connect flow when account is in a Business |
| `email` *(usually default)* | Standard (auto-granted) | Read user's email during OAuth so we know who connected | No review needed |
| `public_profile` *(default)* | Standard (auto-granted) | Read basic user profile during OAuth | No review needed |

The token you already get back (`Meta Token Scopes for facebook` log line) shows the user has granted all these. That means the OAuth scope list is correct — the only thing left is Meta switching the dashboard rows from Standard → Advanced after review.

### How to verify after approval

Once Meta approves, the **App Review → Permissions and Features** page shows each permission with a green **Advanced access — Active** badge. From that moment, any user (not just admins/testers) can complete the Connect Facebook / Connect Instagram flow without errors.

---

## Phase 1.5 — Make a successful test API call per permission (REQUIRED before submit)

**Meta blocks the "Request advanced access" button until your app has made at least one successful API call using each specific permission.** When you hover the disabled button you'll see:

> *"To request advanced access to this permission, you need to make a successful test API call. It may take up to 24 hours after the first API call for this button to become active."*

This is a recently-added gate. Without firing each permission once, you literally cannot submit. Here's how to satisfy it for every permission Pipelyt needs, using actions you do **inside Pipelyt itself** (so you don't have to manually craft Graph API calls).

### Setup once

1. Open https://developers.facebook.com/apps/ → Pipelyt → **App Roles** → **Roles**.
2. Add `salman@neuzenai.com` as **Administrator** (or `Tester`) if not already there. The app needs to be in **Live mode** for this to work for non-admins, but for these test calls Standard access is enough — `salman@` is already an admin.
3. Sign in to Pipelyt (local dev OR staging) as a user owned by `salman@neuzenai.com` — that user can connect a Facebook Page since they're listed as the app's admin/tester.
4. Have a **test Facebook Page** ready (the "Pipelyt Demo Page" from §2.2) plus its linked **Instagram Business** account.

### Actions to perform — each one fires a different permission

Do these in order; one Pipelyt UI flow each. Backend logs in CloudWatch / `%TEMP%\pipelyt-backend\err.log` should show successful 200s for each Meta Graph API call.

| # | Permission | Pipelyt action that fires it |
|---|---|---|
| 1 | `pages_show_list` | **Connect Facebook** in Connections → on the OAuth screen, the Page picker appears → pick the Demo Page → Save. (Lists pages = `GET /me/accounts`.) |
| 2 | `instagram_basic` + `business_management` | Right after the Facebook connect succeeds, click **Connect Instagram Business** → pick the linked IG account. (Reads `GET /{page_id}?fields=instagram_business_account` then `GET /{ig_user_id}`.) |
| 3 | `pages_read_engagement` + `instagram_manage_insights` | Open **Analytics** tab → click the **Sync** button (or wait for auto-sync). (Backend hits `GET /{page_id}/insights` and `GET /{ig_media_id}/insights`.) |
| 4 | `pages_manage_posts` | Open **Create New Post → Manual Post → Text** → write any short caption → target the Demo Page → click **Publish**. (POSTs `/{page_id}/feed`.) Verify the post lands on the actual Facebook Page. |
| 5 | `instagram_content_publish` | Same flow but **Manual Post → Image** → upload any image → target the connected Instagram Business → **Publish**. (POSTs `/{ig_user_id}/media` then `/{ig_user_id}/media_publish`.) |
| 6 | `pages_read_user_content` + `instagram_manage_comments` | Leave a real comment on the post you just published (from another personal Facebook/Instagram account). Then in Pipelyt go to **Reputation** → click that post → comments load on the right side. (Backend hits `GET /{post_id}/comments` and `GET /{ig_media_id}/comments`.) |
| 7 | `pages_manage_engagement` | In **Reputation**, click **Reply** on the loaded comment → type any text → **Send**. (POSTs `/{comment_id}/comments`.) Verify the reply appears on the actual Facebook Page comment. |
| 8 | `instagram_manage_comments` (reply) | Same as #7 but on an Instagram-platform post in Reputation. (POSTs `/{ig_comment_id}/replies`.) |

### Verify each call succeeded

Two ways to confirm Meta saw it:

**A. App dashboard**
1. https://developers.facebook.com/apps/ → Pipelyt → **App Review → Permissions and Features**.
2. Search for the permission row.
3. The **API calls** column changes from `0` to `1+` after each call. The **"Ready to use"** badge in the same row turns green. (Wait 5–60 minutes — the dashboard polls in batches.)

**B. Graph API Explorer audit**
1. https://developers.facebook.com/tools/explorer → top-right "App ID" → pick Pipelyt.
2. Logs of recent calls appear in the right-hand panel. Filter by permission name.

### After all 8 actions are done

1. Wait **up to 24 hours** (per Meta's tooltip — usually it activates within 30 min for fresh API calls, but they're conservative on the warning).
2. Reload **App Review → Permissions and Features** in incognito.
3. The **"Request advanced access"** button on every Pipelyt-needed permission should now be clickable.
4. Click it for each permission and proceed to Phase 3 (the submission form) using the per-permission text from earlier in this guide.

### What to do if a call fails

If the API calls log shows `0` for a permission you DID exercise:
- **Permission not in your token** — re-authorize the OAuth flow making sure the scope is requested (check `apps/backend/services/social_service.py` line that builds the OAuth URL).
- **Backend 4xx silently swallowed** — search the backend log for the platform name + `40[0-9]` to find the failing request, fix the issue (token expired, missing field, wrong endpoint), retry.
- **Permission requires Business asset** — `instagram_*` permissions only work if the IG account is a Business profile linked to a Page. If the test Demo IG isn't Business, switch it via the Instagram app → Settings → Account → Switch to Professional.

---

## Phase 1 — Pre-submission prep (2–4 days)

Each block below is a hard requirement. Skip any one and review fails on day 1.

### 1.1 Business Verification (the biggest gate)

Meta only reviews permissions for apps owned by a **verified Business** in **Meta Business Manager**.

You have two distinct accounts in play — keep them straight:
- `salman@neuzenai.com` → the developer who **created the Meta Developer app**. This is the admin on https://developers.facebook.com/apps/.
- `contact@neuzenai.com` → owns the **Meta Business Manager** at https://business.facebook.com/.

The app needs to be **linked to the Business** (Business Manager → Apps → Add → Connect an existing app). Until that link exists, App Review can't accept submissions for advanced permissions.

#### Steps

1. Sign in to https://business.facebook.com/ as `contact@neuzenai.com`.
2. **Business Settings → Business Info** — verify these are filled:
   - **Legal business name**: the actual entity registered with the tax authority (e.g. `NeuzenAI Pvt Ltd` or whatever is on your IRS / MCA / GST registration). This MUST match official paperwork — Meta cross-checks.
   - **Address**: same as on your business registration / utility bills.
   - **Business email**: `contact@neuzenai.com` (matches the Business owner).
   - **Phone number**: a real number you can pick up.
3. **Security Centre → Verify Now** (or **Business Verification** in newer UI). Meta will ask for:
   - **Business legal name**
   - **Country**
   - **Tax ID** — EIN (US), CIN/PAN (India), VAT (EU), etc.
   - **Document upload** — one of:
     - Certificate of Incorporation (cleanest)
     - Tax registration document (EIN letter / GSTIN / VAT cert)
     - Recent business utility bill or bank statement showing legal name + address (less ideal — Meta sometimes asks for more)
   - **Domain confirmation** — Meta may auto-verify off `neuzenai.com` if it matches your business email, or ask you to add a TXT record to your DNS.
4. After upload, status options:
   - **Verified** (typically 1–3 business days for clean docs) → ready to proceed
   - **More info needed** → click **Resolve** on the same case, upload extra docs. Don't start a brand-new request; reviewers track history per case.

#### Linking the Pipelyt app to NeuzenAI Business

After Business is verified:
1. **Business Settings → Accounts → Apps**.
2. Click **Add → Connect an existing app**.
3. Enter the Pipelyt App ID (from the Meta Developer dashboard at https://developers.facebook.com/apps/ → Pipelyt → Settings → Basic → App ID).
4. The owner of `salman@neuzenai.com` will get a confirmation request — accept it.
5. Now in the app dashboard's **App Settings → Basic**, you'll see "Business Manager" populated with NeuzenAI.

Verify the link by reloading https://developers.facebook.com/apps/ → Pipelyt → Settings → Basic. The "Business Manager Account" field should show your NeuzenAI Business with a green check.

### 1.2 App Mode + Basic Settings

Sign in to https://developers.facebook.com/apps/ as `salman@neuzenai.com` (the app admin) → click your Pipelyt app.

1. **Settings → Basic**:
   - **Display name**: `Pipelyt`
   - **App icon**: upload `pipelyt-icon.png` (the 512×512 from `apps/landing-page/public/`)
   - **App category**: `Business and Pages`
   - **Privacy Policy URL**: `https://pipelyt.ai/privacy` (must be live before submission; see 1.4)
   - **Terms of Service URL**: `https://pipelyt.ai/terms`
   - **Data Deletion Instructions URL**: `https://api.pipelyt.ai/data-deletion` (you already have a `data_deletion` router — confirm it's reachable; see 1.5)
   - **App Domains**: add `pipelyt.ai`, `app.pipelyt.ai`, `staging.app.pipelyt.ai`, `api.pipelyt.ai`
   - **Contact email**: `contact@neuzenai.com` (Meta sends review correspondence here — make sure someone monitors it)
   - **Business Account**: select **NeuzenAI** (the verified Business from 1.1; this dropdown only shows verified Businesses linked to your account)
2. **Settings → Advanced**:
   - **Native or desktop app?** No.
   - **Server IP allowlist** — leave blank unless your Lambda has a fixed Elastic IP (it doesn't by default).
3. Click **Save Changes** at the bottom.

### 1.3 Add Products to your app

If not already added, go to **App Dashboard → Products → +** and add:
- **Facebook Login** → Settings → fill **Valid OAuth Redirect URIs** with:
  - `http://localhost:8000/auth/facebook/callback`
  - `https://api.pipelyt.ai/auth/facebook/callback`
  - `https://staging-api.pipelyt.ai/auth/facebook/callback`
- **Instagram Graph API**
- **Webhooks** (optional but increasingly required) — point at `https://api.pipelyt.ai/webhooks/meta`

### 1.4 Privacy Policy + Terms — actually live URLs

Meta's reviewers click these links. If they 404 or look like placeholders, instant rejection.

- Hit `https://pipelyt.ai/privacy` and `https://pipelyt.ai/terms` in an incognito browser. Both must:
  - Render full content (not "Coming soon")
  - Mention what data Pipelyt collects from Facebook/Instagram (name, page list, post content, comment text, follower counts, etc.)
  - Mention how users can delete their data (link to your Data Deletion endpoint)
  - Be mobile-friendly

If your landing page doesn't have these yet, copy from a template generator (Termly, iubenda, Termsfeed) and customise. The legal text doesn't need to be perfect — it just needs to exist and address Meta's specific points.

### 1.5 Data Deletion endpoint

Verify https://api.pipelyt.ai/data-deletion serves a working page. Meta will hit this with `signed_request` payloads when a user de-authorizes the app on Facebook. Your endpoint must:
- Accept `POST /data-deletion`
- Validate the `signed_request` (HMAC against your app secret)
- Trigger user data wipe in your DB
- Return a confirmation URL the user can visit to check status

If your `routers/data_deletion.py` doesn't do all of this, fix it before submission — Meta tests it.

---

## Phase 2 — Build the Test Account + Demo Recording (1 day)

This is the part most apps fail on. Reviewers need to:
1. Sign up for a Pipelyt test account
2. Click "Connect Facebook"
3. See the OAuth screen and grant permissions
4. Use the feature that requires each permission
5. See it work end-to-end

Make their life easy.

### 2.1 Create a dedicated test user in Pipelyt

Open your production (or staging) Pipelyt and:

1. **Sign up** with a fresh email like `meta-reviewer@pipelyt.ai` (or any mailbox you control).
2. Use a memorable password: `MetaReview2026!` or similar.
3. Complete onboarding: business URL, brand DNA — pick `https://pipelyt.ai` so the DNA agent has real content to chew on.
4. Don't connect any social accounts yet — leave that for the reviewer to do.
5. Note the credentials. You'll paste them into the App Review form.

### 2.2 Prepare a Facebook test Page + Instagram Business

You need a real Facebook Page (not the Pipelyt brand page — keep that separate). Reviewers will connect this Page during testing.

1. Create a new Facebook Page if you don't have a sandbox: **facebook.com/pages/create** → "Business or Brand" → name it `Pipelyt Demo Page`.
2. Convert an Instagram account to **Business** and link it to that Page (Instagram Settings → Account → Switch to Professional Account → connect to Page).
3. Make a few posts on each so the engagement KPIs aren't empty when reviewers see Analytics.

### 2.3 Record the screencast (mandatory — most rejection trigger)

Use **Loom** or **OBS** or QuickTime. Aim for 60-90 seconds per permission you're requesting. One single recording covering everything is fine if it's tight.

**Recording outline:**

```
[0:00–0:10] Show the Pipelyt sign-in screen at app.pipelyt.ai.
            Sign in with the test account credentials.

[0:10–0:25] Land on the dashboard. Briefly point out the "Connect
            Accounts" tab in the sidebar and click it.

[0:25–0:50] Click "Connect Now" on the Facebook Page tile.
            Show the Facebook OAuth permissions screen — pause for a
            second so reviewers can read the permissions being asked.
            Approve and select the test Page from the list.

[0:50–1:10] Back in Pipelyt: show the Page now appears in the
            Connections panel. Switch to the Instagram tile and
            repeat the connect flow for Instagram Business.
            (Demonstrates: pages_show_list, instagram_basic,
            business_management.)

[1:10–1:40] Click "Create New Post" → AI Campaign → write a brief.
            Pick the Facebook Page + Instagram account as targets.
            Click Publish.
            Show the Pipelyt success toast, then SWITCH WINDOWS to
            the actual Facebook Page tab and refresh — the post
            should be there with the right content + image.
            (Demonstrates: pages_manage_posts, instagram_content_publish.)

[1:40–2:10] Back in Pipelyt → Reputation page. Show comments fetched
            from the post you just made (you'll need to leave a
            real comment from another account first so there's
            something to display). Click Reply on a comment, type
            a response, click Send.
            (Demonstrates: pages_read_user_content, pages_manage_engagement,
            instagram_manage_comments.)

[2:10–2:30] Open Analytics page. Show the Total Followers, Engagement,
            Reach KPI cards populated for both Facebook + Instagram.
            Hover the chart, show platform-specific data.
            (Demonstrates: pages_read_engagement, instagram_manage_insights.)

[2:30–2:45] Quick wrap: re-show the Connections page, click the
            three-dot menu on the Facebook account and demonstrate
            "Disconnect" works (proves data lifecycle).
```

**Recording tips:**
- 1080p minimum, 30 fps.
- Cursor highlighter ON.
- Speak narration over the actions: "Now I'm clicking…" — reviewers often watch on mute, but narration helps when they don't.
- One unbroken take is fine; light edits OK; jump-cuts make reviewers suspicious.
- Upload to **Vimeo** or **Google Drive** with link sharing set to "Anyone with the link can view". YouTube unlisted is also fine.

### 2.4 Take screenshots backing the screencast

Reviewers also want stills. Export from your screen-record tool, or take fresh ones:
1. Pipelyt sign-in screen.
2. Connections page with Facebook + Instagram both connected (avatars + page names visible).
3. The OAuth permission grant screen (each permission listed).
4. Pipelyt's Analytics dashboard with non-zero numbers.
5. A real Facebook Page showing a Pipelyt-published post.
6. A real Instagram profile showing a Pipelyt-published post.

5–10 screenshots is fine. They'll be uploaded as part of the submission form.

---

## Phase 3 — The actual App Review submission (30 min once prep is done)

### 3.1 Open the request form

1. https://developers.facebook.com/apps/ → your Pipelyt app
2. Sidebar → **App Review → Permissions and Features**
3. You'll see a long list of all Meta permissions. For each one in section "Permissions Pipelyt needs" above, click **Request advanced access**.

### 3.2 For each permission, fill the form

Meta opens a modal with these fields. Same template applies to all of them with permission-specific tweaks:

#### Field: "How will your app use this permission?"

Use this template, swap in the right verb per permission:

> Pipelyt is an AI-powered social-media management SaaS by NeuzenAI for marketing teams. Users connect their Facebook Pages and Instagram Business accounts to:
>
> 1. **Schedule and publish branded posts** generated by Pipelyt's AI campaign agent (text, images, video, PDF carousels).
> 2. **Monitor comments** in a unified Reputation dashboard and reply individually or with AI-suggested drafts.
> 3. **View consolidated analytics** (followers, engagement, reach) across all connected platforms in one place.
>
> The `<PERMISSION_NAME>` permission is required specifically to **<PERMISSION-SPECIFIC ACTION>**, which is the core flow that delivers value to our users.

Permission-specific replacements:
- `pages_show_list` → "list the user's owned Pages so they can pick which one to connect"
- `pages_read_engagement` → "fetch impressions, likes, comments, and share counts on each post for the analytics dashboard"
- `pages_read_user_content` → "load the comment threads on Pipelyt-published Page posts so users can review and reply from one inbox"
- `pages_manage_posts` → "publish text, image, video, and document posts on the user's behalf when they confirm a campaign in Pipelyt"
- `pages_manage_engagement` → "post a reply, written either by the user or by Pipelyt's AI assistant after explicit user approval, to a comment thread on a Page post"
- `instagram_basic` → "discover and read the Instagram Business account linked to the connected Page"
- `instagram_content_publish` → "publish photo / video / reel posts to Instagram on the user's behalf when they confirm a campaign"
- `instagram_manage_comments` → "load and reply to comment threads on Pipelyt-published Instagram posts"
- `instagram_manage_insights` → "fetch per-post and per-account analytics (reach, impressions, profile views) for the analytics dashboard"
- `business_management` → "support users whose Page is owned by a Meta Business — without it the OAuth grant fails for the majority of professional accounts"

#### Field: "Do you only use this for testing/development?"

Always answer **No**. (If you say yes, you don't get advanced access.)

#### Field: Test instructions for the reviewer

Same template per permission, customised:

> 1. Open https://app.pipelyt.ai
> 2. Sign in with: `meta-reviewer@pipelyt.ai` / `MetaReview2026!`
> 3. Click **Connections** in the left sidebar.
> 4. Click **Connect Now** under the Facebook Page tile.
> 5. On Facebook's OAuth screen, approve all requested permissions.
> 6. Pick the **Pipelyt Demo Page** from the page list and confirm.
> 7. **<PERMISSION-SPECIFIC STEP>**
>
> A 2-minute demo video covering this flow is attached.

Permission-specific final step:
- For `pages_manage_posts`: "Click **Create New Post → AI Campaign**, write a brief, target the Pipelyt Demo Page, click **Publish**. Verify the post appears on the actual Facebook Page within 30 seconds."
- For `pages_manage_engagement`: "Open the **Reputation** tab, click any post that has a comment, click **Reply**, type a response, click **Send**. Verify the reply appears on Facebook."
- For `instagram_*`: substitute "Pipelyt Demo Page → Instagram Business account".
- For `pages_read_engagement` / `pages_read_user_content` / `instagram_manage_insights`: "Open the **Analytics** tab. Verify per-post engagement numbers and the comment list display non-zero data within 10 seconds."

#### Field: Upload screencast + screenshots

- **Screencast video URL** — paste the Loom/Vimeo/Drive link.
- **Screenshots** — drag in the 5–10 stills.

Hit **Save**. The permission moves into the queue.

### 3.3 Repeat for each permission

You can fill these in any order. After each Save, the row in the permission table flips from "Standard access" to "Submitted". Once all are submitted, click **Submit for Review** at the top.

### 3.4 What happens next

1. Meta auto-validates the form (15 minutes). If something's missing — screencast unreachable, OAuth redirect URI doesn't match, business not verified — they reject within an hour with a "Resolve issues" message.
2. If auto-validation passes, the request enters human review. Wait time: **5–14 business days**.
3. You'll get an email outcome:
   - **Approved** → permissions become "Advanced access" — every user can now use them.
   - **Need more info** → the email lists exactly what to fix. Click **Edit** on the rejected row, fix, re-submit. Usually 2–5 day re-review turnaround.

---

## Common rejection reasons (and how to dodge them)

| Reason | Fix |
|---|---|
| "App is not live" / "We could not access the app" | Make sure your app is set to **Live mode** (App Dashboard → top toggle). Reviewers can't test apps in Development mode. |
| Test account credentials don't work | Re-verify them in incognito just before submitting. Lock the password — don't change it during review. |
| Screencast doesn't show the permission being used | Re-record. The 90-second target per permission is forgiving — just make sure each permission's "money shot" is visible. |
| Privacy Policy URL is dead or generic | Add specific Facebook/Instagram language. Even one paragraph naming the platforms + the data types is enough. |
| Business not verified | Finish §1.1 before anything else. Without verification you literally cannot submit advanced-access permissions. |
| Reviewer can't post (rate limit / sandbox restriction on test Page) | Make sure the Demo Page you provided is real, not a fresh empty placeholder. A page with even 5 prior posts converts much better. |
| OAuth redirect URI mismatch | Add every URL the reviewer might be redirected to (localhost variants too) to **Facebook Login → Settings → Valid OAuth Redirect URIs**. |
| "We see this app store user data — please describe deletion process" | Make `https://api.pipelyt.ai/data-deletion` actually work. Test it manually with curl. |

---

## Quick checklist before you click Submit

- [ ] Meta Business Manager (owned by `contact@neuzenai.com`) → verified status (green check) for **NeuzenAI**
- [ ] Pipelyt app linked to NeuzenAI Business under **Business Settings → Apps**
- [ ] Logged in to https://developers.facebook.com/apps/ as `salman@neuzenai.com` (app admin) for the next steps
- [ ] App icon: 512×512 Pipelyt orange icon uploaded
- [ ] App switched from **Development** to **Live** mode (top-of-dashboard toggle)
- [ ] Privacy Policy URL → live, mentions Facebook + Instagram by name + lists data types collected
- [ ] Terms URL → live
- [ ] Data Deletion URL → returns 200, validates `signed_request` with HMAC against the App Secret
- [ ] OAuth redirect URIs include `localhost:8000`, `https://api.pipelyt.ai/auth/facebook/callback`, `https://staging-api.pipelyt.ai/auth/facebook/callback`
- [ ] Test user `meta-reviewer@pipelyt.ai` exists and credentials work in incognito
- [ ] Demo Facebook Page + Instagram Business set up with real content (5+ posts each)
- [ ] Screencast 2–3 min, covers each permission, link is public (Loom / Vimeo / Drive)
- [ ] Screenshots 5–10 high-res (sign-in, OAuth grant, connections, analytics, real Page post, real IG post)
- [ ] Each permission has its tailored "How will you use" text + test instructions
- [ ] All requested permissions = **Advanced access** (not Standard)
- [ ] Contact email on the app = `contact@neuzenai.com` (or another mailbox monitored daily)

If every box is ticked, hit **Submit**. Refresh once a day for the next week — Meta usually responds in 5–10 business days for clean submissions.

---

## After approval

1. Permissions in the dashboard flip to **Approved**.
2. Test from a brand-new Facebook account that's NOT a developer/tester on your app — the OAuth screen no longer says "Only available to admins/testers" and any user can connect.
3. Bump up your real-traffic monitoring — Meta watches for permission abuse and can revoke access if user complaints spike.

---

## Recommended order of work (to compress timeline)

1. **Day 1 morning**: Submit Business Verification (longest external blocker — 1–3 days waiting).
2. **Day 1 afternoon**: Set up Demo Page + Demo Instagram Business account, Privacy/Terms pages while you wait.
3. **Day 1 evening**: **Run the 8 actions in Phase 1.5** — fires every permission once so the "Request advanced access" buttons start to unlock the next day.
4. **Day 2**: Build the reviewer test account in Pipelyt. Confirm Phase 1.5's API call counter ticked up on every permission.
5. **Day 3**: Record screencast + screenshots.
6. **Day 3 evening**: Business verification came back AND every permission's "Request advanced access" button is clickable → fill App Review form → submit.
7. **Day 4–14**: Wait on Meta. Use the time to harden the OAuth flow + confirm the data-deletion endpoint actually wipes records.

End-to-end: realistically 2 weeks from "I want to start" to "production-grade Facebook + Instagram access". Most of that is waiting on Meta — your active work time is only 2–3 days. The Phase 1.5 step is what keeps you on a 3-day track instead of a 5-day one (because it adds a 24h cool-off if you forget it).
