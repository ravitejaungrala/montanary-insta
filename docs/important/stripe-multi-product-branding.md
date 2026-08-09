# Stripe Branding for Multi-Product Portfolio

**Audience:** Manager / decision-maker
**Author:** Engineering
**Status:** Decision needed — pick Option A or Option B

---

## TL;DR (read this if nothing else)

1. **Stripe doesn't allow per-product branding inside a single Stripe account.** The company name, logo, and "Return to X" link on Checkout / Customer Portal are account-level settings. Whatever we set there shows for *every* product on that account.
2. **We currently have one Stripe account registered to "Z-ninth LLC".** Every checkout for Pipelyt, Spenzo, and future products will say "Z-ninth LLC" at the top — not "Pipelyt" or "Spenzo".
3. **There are exactly two clean ways forward** — separate Stripe accounts per product (recommended, ~30 min setup per product, fully isolated branding) or stay on the shared account (zero work, all products carry Z-ninth branding).

---

## The Problem

Stripe's Checkout page, Customer Portal, and customer-facing emails all pull these from the Stripe **account-level** settings:

| Element on the page | Comes from |
|---|---|
| Company name at the top | Account → Public business name |
| Logo / icon | Account → Branding |
| "Return to X" link | Account return URL |
| `BUSINESS*PRODUCT` on customer's bank statement | Account → Statement descriptor |

If we change those to "Pipelyt", every Spenzo customer also sees Pipelyt branding when they pay or open their billing portal. Inverse if we set them to Spenzo. There is no per-product override; Stripe deliberately keeps account branding consistent so customers always see the same trust signals.

---

## Options

### Option A — One Stripe account per product (recommended)

Create a separate Stripe account for each product (Pipelyt, Spenzo, future SaaS). Z-ninth LLC stays the **legal entity** on every account (one EIN, one bank, one tax filing) but each product gets its own customer-facing Stripe dashboard.

| Aspect | Detail |
|---|---|
| **Customer experience** | Pipelyt customers see "Pipelyt" everywhere; Spenzo customers see "Spenzo". Trust signals match the brand they expect. |
| **Branding** | Independent logo, icon, brand color, support URL per product. |
| **Webhooks** | Pipelyt's backend only receives Pipelyt events; Spenzo's only receives Spenzo events. No accidental cross-routing. |
| **Analytics** | Stripe Dashboard charts (MRR, churn, revenue) are per-account, so per-product KPIs are native — no manual filtering. |
| **Compliance / tax** | Same Z-ninth EIN on every account → tax filings consolidate at the corporate level just like today. |
| **Setup cost** | ~30 minutes per product (sign up, recreate product catalog, configure branding, activate Customer Portal, register webhook endpoint, paste keys into Lambda env). |
| **Operational cost** | One extra dashboard to log into per product. Payouts land in the same Z-ninth bank account but appear labeled per product (helpful for ops). |

### Option B — Stay on the shared Z-ninth account

| Aspect | Detail |
|---|---|
| **Customer experience** | Pipelyt and Spenzo customers both see "Z-ninth LLC" branding. Some customer confusion likely (they signed up for "Pipelyt" but their receipt says "Z-ninth"). |
| **Branding** | Account-level — only one brand can win. |
| **Mitigations available** | Per-product image uploaded to each Stripe product (shows above the price on Checkout), and per-session statement-descriptor suffix (e.g. `ZNINTH* PIPELYT` on credit card statements). |
| **Webhooks** | All events go to one webhook endpoint; backend has to inspect the product / price ID on every event to route it to the right product's logic. Adds risk of cross-product bugs. |
| **Analytics** | Stripe MRR / churn / revenue dashboards combine all products — manual SQL/exports needed for per-product views. |
| **Compliance / tax** | Same as today. |
| **Setup cost** | Zero. |

---

## Impact comparison

|  | Option A (separate accounts) | Option B (shared account) |
|---|---|---|
| Customer trust at checkout | ✅ Brand they signed up for | ⚠️ "Z-ninth LLC" — unfamiliar |
| Failed-card / churn email branding | ✅ Per product | ⚠️ Always Z-ninth |
| Stripe Dashboard analytics | ✅ Per product, native | ⚠️ Combined, manual filters |
| Webhook isolation | ✅ Strict | ❌ All events to one endpoint |
| Engineering setup time | ⚠️ ~30 min per product | ✅ None |
| Long-term ops overhead | ⚠️ Multiple dashboards | ✅ One |
| Risk of cross-product bug | ✅ Impossible | ⚠️ Possible (price-ID misroute) |
| Per-product MRR visibility | ✅ Native | ⚠️ Custom reporting needed |

---

## Recommendation

**Option A.** The customer-trust impact alone justifies the ~30-minute setup per product. Most multi-product SaaS holding companies (Stripe themselves, Atlassian, Adobe, etc.) operate exactly this way: one legal entity, one EIN, one bank, but each customer-facing product has its own Stripe account so the brand promise is consistent end-to-end.

Z-ninth's holding-company structure is unaffected — accounting, taxes, and bank reconciliation roll up the same way they do today.

---

## What we need to proceed (Option A)

1. **Email address** to register the new Pipelyt Stripe account (e.g. `billing@pipelyt.ai`).
2. **Bank account** to receive Pipelyt payouts (can be the same Z-ninth account already linked).
3. **Z-ninth EIN + tax info** (already on file with Stripe — just re-entered for the new account).
4. **30 minutes of engineering time** to recreate the product catalog and rotate keys into the Pipelyt Lambda env.

Repeat the same checklist for Spenzo and any future product when ready — no rush, products can be migrated one at a time.

---

## Three-point summary for the team

1. **Why we can't just rename:** Stripe locks branding at the account level. Renaming the Z-ninth account to "Pipelyt" would also rename Spenzo's checkout to "Pipelyt" — that breaks Spenzo's customer trust.
2. **What we recommend:** create one Stripe account per product (Pipelyt has its own, Spenzo has its own, etc.), all owned by the Z-ninth legal entity. ~30 minutes of setup per product, no ongoing overhead beyond an extra login.
3. **What we get:** clean per-product branding on every customer touchpoint (checkout page, billing portal, receipts, statement descriptors), isolated webhooks, native per-product MRR/churn dashboards, and zero impact on Z-ninth's corporate accounting or tax structure.
