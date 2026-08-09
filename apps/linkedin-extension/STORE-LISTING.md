# Chrome Web Store — submission copy & answers

Paste these into the Web Store Developer Dashboard when publishing
**Pipelyt GTM — LinkedIn Connector**.

Privacy policy URL: **https://pipelyt.ai/privacy**

---

## Single purpose (required)
> Connect the user's existing LinkedIn session to their Pipelyt GTM account so
> Pipelyt can run LinkedIn outreach on the user's behalf — without the user
> sharing their LinkedIn password.

## Name
Pipelyt GTM — LinkedIn Connector

## Short description (≤132 chars)
> Securely connect your LinkedIn account to Pipelyt GTM for outreach automation. Uses your existing session — no password needed.

## Detailed description
> Pipelyt GTM — LinkedIn Connector lets you link your LinkedIn account to Pipelyt
> in one click, so Pipelyt can send your connection requests, notes, and messages
> as part of your outreach campaigns.
>
> How it works:
> 1. Log in to LinkedIn normally in your browser (you handle any 2FA yourself).
> 2. Open Pipelyt → Connect LinkedIn → click "Connect via extension".
> 3. The extension securely passes your existing LinkedIn session to your Pipelyt
>    account. Your password is never seen or stored by Pipelyt.
>
> Why a session instead of a password? It's safer (no credentials shared) and more
> reliable than automated logins. You stay in control and can disconnect anytime
> from your Pipelyt account.
>
> This extension is intended for Pipelyt customers connecting their own LinkedIn
> account for their own outreach.

---

## Permission justifications (required, per permission)
- **cookies** — "Read the user's linkedin.com session cookies so they can connect
  their own LinkedIn account to Pipelyt without sharing their password."
- **storage** — "Remember the user's settings (Pipelyt API URL) between sessions."
- **host permission `*.linkedin.com`** — "Read the authenticated LinkedIn session
  cookies the user just created by logging in."
- **host permission `pipelyt.ai` / Pipelyt API** — "Send the captured session
  securely to the user's Pipelyt account over HTTPS."
- **Remote code** — "No. The extension executes no remote or eval'd code."

## Data use disclosures (Privacy practices tab)
- Data collected: **Authentication information** (LinkedIn session cookies).
- ☑ Data is transmitted over HTTPS (encrypted in transit) and stored encrypted at rest.
- ☑ NOT sold or transferred to third parties.
- ☑ NOT used or transferred for purposes unrelated to the single purpose above.
- ☑ NOT used to determine creditworthiness or for lending.

---

## ⚠️ Your /privacy page MUST contain these clauses (or Google rejects it)
Add a section like:

> **Pipelyt GTM — LinkedIn Connector (browser extension).** When you choose to
> connect your LinkedIn account, the extension reads your linkedin.com session
> cookies from your browser and transmits them over HTTPS to Pipelyt, where they
> are stored encrypted and used solely to perform LinkedIn outreach actions that
> you configure. We never collect your LinkedIn password. We do not sell or share
> these cookies with third parties. You can disconnect your LinkedIn account at any
> time from Pipelyt, which deletes the stored session.

---

## Assets needed for the listing
- Icon 128×128 — `icon128.png` (already in this folder)
- At least one screenshot — 1280×800 or 640×400 (e.g. the Connect LinkedIn modal)
- (Optional) small promo tile 440×280
