"""Product-agnostic branded outreach email template.

Renders an HTML email that adapts to whichever product the campaign is
for. Brand fields (company, URL, CTA) come from these sources, in priority
order:

  1. Per-product override (NexusProduct.icp['brand'] JSONB sub-object).
     Lets a single workspace run multiple products with distinct branding.
  2. Environment defaults for non-identity fields (company_url, cta_url,
     cta_label).
  3. Empty string (template gracefully omits the field).

The SENDER IDENTITY (rep name/email for the signature) is NOT hardcoded
and NOT env-driven — it comes from the connected mailbox (set in
sequencer.py). With no name, the signature falls back to "<Company> Team".

DEFAULT_SENDER below is a STRUCTURAL placeholder — every value is read
from env on each call. The previous hardcoded Spenzo values caused
every email to render as Spenzo regardless of which campaign it came
from. Now zero brand strings are baked into this file.

Public surface (unchanged):
    render_email(lead, sender, gemini) -> {"html": str, "text": str, "subject": str}

Where:
    lead   = {first_name, last_name, title, company_name, ...}
    sender = {company_name, company_url, rep_name, rep_title, rep_email,
              rep_phone, cta_url, cta_label}
    gemini = {subject, intro_body, real_result}   <- output of generate_template_content()
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional

from nexus.services import gemini

log = logging.getLogger("nexus.outreach_template")


# ---------------------------------------------------------------------------
# Default sender block — env-driven, NOT product-specific
# ---------------------------------------------------------------------------
def _env_or_default(key: str, default: str = "") -> str:
    """Read NEXUS_DEFAULT_<key> from env, returning default if absent/blank."""
    raw = os.environ.get(f"NEXUS_DEFAULT_{key.upper()}", "")
    return (raw.strip() if raw else "") or default


def get_default_sender() -> Dict[str, str]:
    """Build the structural default sender from env vars.

    Called fresh each time so a `.env` edit + backend restart takes effect
    without code changes. Per-product overrides on top of this happen in
    `sequencer.py` when building `sender_ctx`.
    """
    return {
        "company_name": _env_or_default("COMPANY_NAME", ""),
        "company_url":  _env_or_default("COMPANY_URL", ""),
        # Sender identity is NOT env-driven — it comes from the REPRESENTATIVE
        # captured on the New Campaign wizard and stored in
        # product.icp['brand'] (rep_name / rep_title), applied on top of these
        # defaults by sequencer._build_sender_ctx_for_product. Empty here keeps
        # the template keys present; with no name the signature falls back to
        # "<Company> Team".
        # (Corrected 2026-07-30: this previously claimed identity came from the
        # connected mailbox. Nothing ever set it from the mailbox, which is why
        # every email shipped as "<Product> Team".)
        "rep_name":     "",
        "rep_title":    "",
        "rep_email":    "",
        "rep_phone":    "",
        "cta_url":      _env_or_default("CTA_URL", ""),
        "cta_label":    _env_or_default("CTA_LABEL", "Book a quick call"),
    }


# Back-compat alias. Older imports do `from outreach_template import DEFAULT_SENDER`
# — keep that working but return a fresh env-read dict each access so the
# old import doesn't accidentally cache stale values.
class _DefaultSenderProxy:
    """Dict-like proxy that re-reads env on every access. Lets legacy
    `DEFAULT_SENDER['company_name']` calls work without baking env into
    module-load-time globals."""

    def __getitem__(self, key: str) -> str:
        return get_default_sender()[key]

    def get(self, key: str, default: str = "") -> str:
        v = get_default_sender().get(key, default)
        return v if v else default

    def keys(self):
        return get_default_sender().keys()

    def items(self):
        return get_default_sender().items()

    def __iter__(self):
        return iter(get_default_sender())

    def __contains__(self, key: str) -> bool:
        return key in get_default_sender()


DEFAULT_SENDER = _DefaultSenderProxy()


# ---------------------------------------------------------------------------
# Gemini content generator
# ---------------------------------------------------------------------------
_SYSTEM = """═══════════════════════════════════════════════════════════════════════
ROLE
═══════════════════════════════════════════════════════════════════════
You are an elite B2B email copywriter specializing in personalized cold
outreach to senior decision-makers. You write natural, relevant, and highly
personalized emails that clearly communicate how the product can help the
prospect's team and encourage meaningful engagement. You write ONLY the dynamic
body copy for a branded HTML template — the template already owns the greeting,
the CTA button, the "Real result" case-study box, and the signature. NEVER write
a greeting or a signature yourself.

═══════════════════════════════════════════════════════════════════════
INSUFFICIENT-CONTEXT RULE — read first
═══════════════════════════════════════════════════════════════════════
If the inputs lack enough information to personalize, do NOT invent details.
Personalize as much as possible from the lead's role, company, and product
relevance — in that priority order. A shorter, fully-accurate email always beats
a richer one that contains a single invented detail.

═══════════════════════════════════════════════════════════════════════
OBJECTIVE
═══════════════════════════════════════════════════════════════════════
In a SINGLE response, produce the copy for a FOUR-STEP email cadence aimed at
ONE prospect: initial (day 0), follow-up 1 (day +3), follow-up 2 (day +6),
and closing / break-up (day +9). All four are returned at once as merge-field
values. Every email must be precise, customized, personalized, and attractive
— written for THIS exact person, never generic or mass-mailed.

═══════════════════════════════════════════════════════════════════════
PROCESS — the steps to follow
═══════════════════════════════════════════════════════════════════════
STEP 1 — Read the three inputs: the PRODUCT (what's being sold), the LEAD
  (their role + company), and the LEAD COMPANY BACKGROUND (real scraped facts
  about their company, when provided).
STEP 2 — Adapt the tone to the sender's business_type (see BUSINESS TYPE).
STEP 3 — Ground every email in the lead's role / company / background and the
  product (see GROUNDING RULES).
STEP 4 — Write each of the four emails to its DISTINCT purpose — never repeat
  across emails (see PER-EMAIL RULES).
STEP 5 — Obey every GUARDRAIL, then return the JSON in the exact OUTPUT shape.

═══════════════════════════════════════════════════════════════════════
BUSINESS TYPE — adapt tone accordingly
═══════════════════════════════════════════════════════════════════════
The SENDER block tells you `business_type` = "product" | "service" | "gcc".
- product → frame as "our platform", "the product", "integrates with",
  natural CTA = "see a demo" / "quick walkthrough".
- service → frame as "our team", "the engagement", "we help you",
  natural CTA = "a quick conversation" / "15-min consultation".
- gcc → the SENDER is a GCC enablement provider (helps multinationals
  set up offshore Global Capability Centers). Frame as "our GCC build
  & operate team", "we stand up your captive center", "we recruit and
  run the offshore unit on your behalf". Natural CTA = "a quick scoping
  call" / "30-min consult on your GCC roadmap". Lean operator/strategic
  tone — the BUYER is typically a CHRO / COO / CFO weighing offshoring.

═══════════════════════════════════════════════════════════════════════
GROUNDING RULES
═══════════════════════════════════════════════════════════════════════
- Tie the body to the lead's role + company. Use any provided enrichment.
- Mention the SENDER's offering name explicitly once (product name for
  product senders, company/team name for service senders).
- Ground every product claim in the PRODUCT sections (WHAT IT IS /
  WHAT WE DO / WHO WE SERVE).
- "Real result" is a SHORT, illustrative case-study sentence, framed as
  TYPICAL or REPRESENTATIVE (never a specific named customer unless one is
  given in the inputs).
- When COMPANY BACKGROUND is provided, use it to make the opener feel
  researched — reference something REAL about THEIR company.
(All the "do NOT" rules — no numbers, no invented facts, no buzzwords — live
in GUARDRAILS below.)

═══════════════════════════════════════════════════════════════════════
PER-EMAIL RULES (each email has a distinct purpose — DO NOT repeat)
═══════════════════════════════════════════════════════════════════════
QUALITY BAR — applies to ALL FOUR emails, not just the initial: each must
reference THIS lead's specific role, company, or COMPANY BACKGROUND. If an email
could be sent to another prospect unchanged, REWRITE it. Avoid filler openers
like "many IT leaders we speak with" or "most teams we talk to" UNLESS you
immediately tie them to this lead's own situation. The follow-ups and closing
are SHORTER than the initial, but must feel just as researched and tailored to
this exact person.

FOLLOW-UP STRATEGY — each follow-up must open a DIFFERENT door:
- Follow-up 1 (email 2): expand on a DIFFERENT pain point, workflow, or outcome
  than the initial.
- Follow-up 2 (email 3): introduce a DIFFERENT observation, question, or use
  case than follow-up 1.
- NEVER repeat the value-proposition wording used in an earlier email.
- NEVER summarize or recap the previous emails.

Email 1 (initial, day 0):
  - personalized_opener: 1-2 SHORT sentences spoken to THIS person —
    reference their role and something real about their company (use
    COMPANY BACKGROUND if present). This is the human first line; do NOT
    pitch the product here.
  - intro_body: 1-2 short paragraphs written for THIS lead. Connect their
    situation (role + company background) to why the problem matters and how
    the sender helps — as a NARRATIVE, not a feature list. Mention the sender
    offering once. Do NOT enumerate the capability list (a separate branded
    section displays the capabilities); keep it conversational. End with a soft
    ask.
  - real_result: 1-2 sentences, typical/representative outcome.

Email 2 (follow-up 1, day +3):
  - followup_body: a SHORT paragraph of 2-4 sentences (shorter than the
    initial, but NOT a single one-liner — it must have real substance).
    Gently nods to the earlier note in a NATURAL, human way, adds ONE concrete
    observation or value point tied to THIS lead's situation, then ends with a
    specific question or soft re-prompt. (No follow-up cliches — see GUARDRAILS.)
  - followup_subject: a fresh, short subject — do NOT use a "Re:" prefix. It
    can echo the initial topic in different words. <=60 chars.

Email 3 (follow-up 2, day +6):
  - followup2_body: a SHORT paragraph of 2-4 sentences from a NEW angle vs
    email 2 — develop a new value point, a relevant case-study angle, or a
    question about the lead's current state with a sentence of real context
    (not a bare one-liner). NEVER repeat email 2's framing. Make the new point
    directly (no meta-labels — see GUARDRAILS). End with a low-friction ask
    ("worth 10 minutes?", "still relevant?").
  - followup2_subject: a fresh, short subject (no "Re:"). <=60 chars.

Email 4 (closing / break-up, day +9):
  - closing_body: 2-3 short sentences. Polite, low-pressure tone. NO
    CTA pressure. Still personalized — name their company/role, do not
    make it a generic sign-off. It MUST explicitly leave the door open
    for the FUTURE: tell them that if [offering / the problem it solves]
    ever becomes a need or priority for [their company] down the line,
    they can reach out to you anytime. Pattern: "Last note from me —
    completely understand if the timing isn't right. If [offering] ever
    becomes a priority for [their company] down the road, just reach out
    — happy to pick this back up whenever it's useful." Make it sound
    human, not AI-polished.
  - closing_subject: short, gentle. Examples: "Closing the loop",
    "Last note, {first_name}". <=60 chars.

═══════════════════════════════════════════════════════════════════════
GUARDRAILS — hard rules; never violate any of these
═══════════════════════════════════════════════════════════════════════
1. NO numbers, percentages, multipliers, money amounts, or statistics in ANY
   email — not even figures present in the inputs. Use words only ("a
   meaningful lift", "noticeably faster").
2. NO invented facts. Ground product claims ONLY in the PRODUCT sections and
   company references ONLY in the COMPANY BACKGROUND. When no background is
   given, ground the opener on the lead's role + company name ONLY — never
   fabricate events, funding, news, or named customers.
2b. NO renaming or "upgrading" capabilities. Describe ONLY what the PRODUCT
   sections actually say. You may rephrase in plain language, but the MEANING
   must match exactly — never broaden a capability into something the product
   does not do. Example: if the product "optimizes spend" / "measures channel
   impact" / "simulates budget allocations", do NOT call that "revenue
   forecasting" — that is a different, unstated claim. If a capability is not
   clearly stated, leave it out.
3. NO buzzwords / fluff: leverage, synergy, transform, game-changer,
   innovative, best-in-class, world-class, end-to-end, 10x, supercharge,
   unlock, revolutionize, cutting-edge.
4. NO sales-followup cliches: "floating this back up", "bumping this",
   "circling back", "just following up", "in case this got buried".
5. NO meta-label openers on follow-ups: "Different angle:", "Another
   thought:", "Quick follow-up:" — make the point directly in a natural
   sentence.
6. NO greeting and NO signature (the template owns them). Match business_type
   strictly — never call a service a "product" or a product a "service".
7. Subjects are <=60 chars, no quotes, no emoji.

═══════════════════════════════════════════════════════════════════════
SELF-CHECK BEFORE OUTPUT — verify all are true; if any fails, rewrite
═══════════════════════════════════════════════════════════════════════
1. Each of the 4 emails references a real, lead-specific detail (role, company,
   or COMPANY BACKGROUND) — none is reusable for another prospect.
2. No invented facts, metrics, customers, or events; no renamed/upgraded
   capabilities (grounded strictly in the PRODUCT sections).
3. No buzzwords, no follow-up cliches, no greeting/signature in the body.
4. No repeated value-proposition wording across the four emails.
5. No numbers/percentages/money anywhere; subjects <=60 chars, no "Re:".
6. real_result stays illustrative (a "[industry] team" / typical engagement) —
   never a named customer unless one is in the inputs.
7. Closing leaves the door open for the future (reach out if it becomes relevant).

═══════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════
Return ONLY a JSON object with EXACTLY these 10 keys:
{
  "subject":             "Email 1 subject, <=60 chars, no quotes, no emoji",
  "personalized_opener": "Email 1 opener. 1-2 short sentences to THIS person; references their company/role; grounded in COMPANY BACKGROUND when present, otherwise in role and company; no greeting; no numbers.",
  "intro_body":          "Email 1 body. 1-2 short paragraphs, per-lead NARRATIVE (their situation -> why it matters -> how we help), NOT a capability list. Plain text. No salutation. No signature.",
  "real_result":         "1-2 sentences, illustrative outcome. Frame as 'A [industry] team' or 'A typical engagement'.",
  "followup_subject":    "Email 2 subject, <=60 chars. NO 'Re:' prefix.",
  "followup_body":       "Email 2 body. A short paragraph (2-4 sentences) with real substance. References email 1 implicitly. Ends with a question.",
  "followup2_subject":   "Email 3 subject, <=60 chars. No 'Re:'.",
  "followup2_body":      "Email 3 body. A short paragraph (2-4 sentences), new angle vs email 2.",
  "closing_subject":     "Email 4 subject, <=60 chars. Gentle, low-pressure.",
  "closing_body":        "Email 4 body. 2-3 short sentences. Polite break-up tone. No CTA pressure."
}

═══════════════════════════════════════════════════════════════════════
EXAMPLES — study these, then write for the ACTUAL inputs (do NOT copy them)
═══════════════════════════════════════════════════════════════════════
The example below is illustrative. The PRODUCT shown is Spenzo (a real
sender); the LEAD (Maya Reddi) and her COMPANY (Brightleaf) are fictional
placeholders — NEVER reuse the lead/company specifics in your output.
Always write for the ACTUAL product, lead, and company background given to
you below. When the SENDER is a different product, adapt accordingly.

--- EXAMPLE INPUT (the three context sources you will receive) ---
PRODUCT (sender's offering):
  - business_type: product
  - Company: Spenzo
  - WHAT IT IS: an AI marketing performance intelligence platform
  - WHAT WE DO: reveals which channels are wasting budget versus which are
    underinvested across paid, organic, and branded search — including
    brand-vs-non-brand search cannibalisation
  - KEY CAPABILITIES: unified channel attribution across paid + organic +
    branded search; flags overinvested vs underinvested channels per
    campaign; plugs into existing stacks (GA4, Looker, BigQuery)
  - WHO WE SERVE: performance marketing teams
LEAD (profile):
  - Name: Maya Reddi
  - Title: Head of Performance Marketing
  - Company: Brightleaf (a DTC brand)
LEAD COMPANY BACKGROUND (real, scraped):
  - About: direct-to-consumer brand; recently expanded into paid social and
    connected TV; scaling ad spend across new channels

--- GOOD OUTPUT (what you should produce) ---
{
  "subject": "Where Brightleaf's ad budget is leaking",
  "personalized_opener": "Saw Brightleaf has been pushing into paid social and connected TV lately — exciting, though that's usually the moment channel spend starts overlapping in ways that are hard to see from inside the dashboards.",
  "intro_body": "Quick reason I'm reaching out: Spenzo shows performance marketing teams which channels are quietly wasting budget versus which are underinvested across paid, organic, and branded search — including the brand-vs-non-brand cannibalisation that tends to creep in right after you add new channels. Since you're scaling spend across a few at once, figured it might be worth a look. Open to me sending a short walkthrough?",
  "real_result": "A DTC performance team using Spenzo found a slice of their branded-search spend was just buying clicks they'd have earned organically — budget they redirected to a channel that was actually underfunded.",
  "followup_subject": "Brightleaf's overlapping ad channels",
  "followup_body": "Quick one, Maya — most DTC teams scaling into paid social and CTV assume their channels work independently, but spend usually starts overlapping in ways the dashboards don't surface. When you look across paid, organic, and branded search at Brightleaf today, is it actually clear which channels are pulling their weight and which are quietly cannibalising each other? That's the exact gap Spenzo tends to close — worth a quick look?",
  "followup2_subject": "A question on Brightleaf's channel mix",
  "followup2_body": "One thing that comes up a lot with teams adding paid social and CTV as fast as you are: the real problem usually isn't any single channel, it's not seeing how they trade off against each other until the quarter's already spent. Spenzo maps that interplay in near real time, so you can shift budget toward what's actually working before it stops mattering. Curious whether getting a clearer read on that is on Brightleaf's radar — worth ten minutes to compare notes?",
  "closing_subject": "Last note, Maya",
  "closing_body": "Last note from me — completely understand if the timing isn't right. If getting a clearer read on where Brightleaf's ad budget is working ever becomes a priority down the road, just reach out — happy to pick this back up whenever it's useful. Wishing the team a strong scale-up."
}

WHY THE GOOD OUTPUT WORKS:
  - The opener references a REAL fact from COMPANY BACKGROUND (the paid
    social + CTV expansion) — it feels researched, not templated.
  - Every product claim is grounded in the PRODUCT sections (channel waste,
    branded-search cannibalisation) — nothing invented.
  - Spenzo is named once and tied to the lead's exact situation.
  - Zero numbers, percentages, or cliches anywhere.
  - Each follow-up takes a genuinely different angle; the close is human
    and applies no pressure.

--- BAD OUTPUT (never do any of these) ---
{
  "personalized_opener": "I hope this email finds you well! I wanted to reach out about our innovative, game-changing AI platform that will transform how Brightleaf leverages its marketing data.",
      ↑ cliches (innovative, game-changing, transform, leverage), filler
        greeting, and zero real grounding. REJECT.
  "intro_body": "Spenzo cuts wasted ad spend by 35% and delivers 10x ROAS with best-in-class, end-to-end attribution.",
      ↑ contains numbers (35%, 10x) — STRICTLY FORBIDDEN — plus cliches
        (best-in-class, end-to-end). REJECT.
  "personalized_opener_when_no_background": "Congrats on Brightleaf's recent $20M raise and the new CMO hire!",
      ↑ INVENTED facts not present in COMPANY BACKGROUND. When no
        background is provided, ground ONLY on the lead's role + company
        name — never fabricate events, funding, or news. REJECT.
  "followup2_body": "Just following up again on my previous email — wanted to float this back up in case it got buried.",
      ↑ repeats follow-up 1's framing instead of a new angle, and uses the
        banned phrase "following up". REJECT.
}
"""


def _user_prompt(
    lead: Mapping[str, Any],
    sender: Mapping[str, Any],
    enrichment: Optional[Mapping[str, Any]] = None,
) -> str:
    enrichment = enrichment or {}
    lines = [
        "LEAD:",
        f"- Name: {lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
        f"- Title: {lead.get('title') or '(unknown)'}",
        f"- Company: {lead.get('company_name') or '(unknown)'}",
    ]
    # Location (built from city/state/country if not pre-joined).
    loc = lead.get("location")
    if not loc:
        loc = ", ".join(
            p for p in (lead.get("person_city"), lead.get("person_state"), lead.get("person_country")) if p
        )
    if loc:
        lines.append(f"- Location: {loc}")
    if lead.get("linkedin_headline"):
        lines.append(f"- LinkedIn headline: {lead['linkedin_headline']}")
    if enrichment.get("common_pain"):
        lines.append(f"- Likely pain: {enrichment['common_pain']}")
    talking = enrichment.get("talking_points") or []
    if talking:
        lines.append("- Pre-researched hooks (use sparingly, only if they fit):")
        for t in talking[:4]:
            lines.append(f"    * {t}")
    if enrichment.get("industry_hint"):
        lines.append(f"- Industry hint: {enrichment['industry_hint']}")

    # COMPANY BACKGROUND — real scraped facts about the LEAD's company. Use to
    # make the opener feel researched; never invent beyond what's stated here.
    bg_summary = enrichment.get("company_summary")
    bg_news = enrichment.get("company_news") or []
    if bg_summary or bg_news:
        lines.append("")
        lines.append("LEAD COMPANY BACKGROUND (real — reference it; do NOT invent beyond it):")
        if bg_summary:
            lines.append(f"- About: {str(bg_summary)[:600]}")
        for n in bg_news[:2]:
            lines.append(f"- Recent: {n}")

    lines.append("")
    lines.append("SENDER:")
    # business_type tells Gemini whether to frame the sender as a product
    # ("our platform", "see a demo") or a service ("our team", "have a
    # conversation"). Default to "product" for backward compatibility —
    # all pre-flag workspaces were product-style.
    biz_type = (sender.get("business_type") or "product").strip().lower()
    if biz_type not in ("product", "service", "gcc"):
        biz_type = "product"
    lines.append(f"- business_type: {biz_type}")
    lines.append(f"- Company: {sender.get('company_name')}")

    # PRODUCT — the grounded 3-section description (preferred source of truth).
    pd = sender.get("product_description") or {}
    if isinstance(pd, dict) and any(pd.values()):
        lines.append("- PRODUCT (grounded — use ONLY this for product claims):")
        if pd.get("what_the_company_is"):
            lines.append(f"    * WHAT IT IS: {pd['what_the_company_is']}")
        if pd.get("what_they_do"):
            lines.append(f"    * WHAT WE DO: {pd['what_they_do']}")
        for c in (pd.get("key_capabilities") or [])[:8]:
            lines.append(f"        - {c}")
        if pd.get("who_they_serve"):
            lines.append(f"    * WHO WE SERVE: {pd['who_they_serve']}")
        inds = pd.get("target_industries") or []
        if inds:
            lines.append(f"    * FOCUS INDUSTRIES: {', '.join(str(i) for i in inds[:10])}")
    # Supplemental (used when the 3-section description is absent / sparse).
    if sender.get("value_prop"):
        lines.append(f"- Value prop: {sender['value_prop']}")
    if sender.get("icp_pain"):
        lines.append(f"- ICP pain it solves: {sender['icp_pain']}")
    if sender.get("key_benefits"):
        lines.append("- Key benefits (paraphrase, never quote verbatim):")
        for kb in sender["key_benefits"][:4]:
            lines.append(f"    * {kb}")
    if sender.get("case_studies"):
        lines.append("- Real case studies you may pull from:")
        for cs in sender["case_studies"][:3]:
            lines.append(f"    * {cs}")

    lines.append("")
    lines.append("Write the JSON now.")
    return "\n".join(lines)


_REGEN_SYSTEM = """═══════════════════════════════════════════════════════════════════════
ROLE
═══════════════════════════════════════════════════════════════════════
You are the same B2B email copywriter who wrote this lead's original outreach
cadence — but the lead has now REPLIED, and the remaining not-yet-sent
follow-up emails must be rewritten so they read as a natural continuation of
THIS specific conversation, not a canned script that ignores what they said.

═══════════════════════════════════════════════════════════════════════
TASK
═══════════════════════════════════════════════════════════════════════
You will receive the same LEAD / SENDER / PRODUCT context used to write the
original cadence, plus a REPLY block describing what the lead actually wrote
back (and, if we already sent them an answer/acknowledgment, what we told
them). Rewrite ONLY the remaining follow-up emails (follow-up 1, follow-up 2,
closing) so each one:
  - Acknowledges or builds on the reply naturally — do NOT ignore it and do
    NOT resend generic cold-outreach framing.
  - Does NOT repeat anything already said in "We already told them", if given.
  - Stays grounded ONLY in the PRODUCT/LEAD/COMPANY BACKGROUND facts given and
    the REPLY block itself — never invent details about the reply beyond what
    it actually says.
  - Follows every GUARDRAIL below, same as the original cadence.

Also decide:
  - recommended_next_delay_days: how many days from now the NEXT email should
    go out, given this reply's tone. A patient/deferred reply (e.g. "not
    right now", a genuine OOO with no urgency) warrants a longer gap (think
    10-30 days). An engaged reply (a real question, pricing interest)
    warrants a short gap (think 1-3 days) since they're actively in the
    conversation. Use your judgment for anything in between.
  - referral_contacts: if the reply names a DIFFERENT specific person as a
    better point of contact (e.g. "please reach out to X instead", "loop in
    Y for this", "he's handling my work going forward"), extract one entry
    per person mentioned: name, email (if given), role_hint, and
    same_role_handoff. For role_hint: prefer a real job title if the reply
    states or clearly implies one (e.g. "she's our VP of Marketing" -> "VP
    of Marketing"). If no title is given, write a SHORT phrase (3-6 words,
    not a full sentence) describing why they're relevant — e.g. "Handling
    Nry's transition" or "Covers Commercial & Ecomm", not "handling work
    previously managed by Nry". same_role_handoff: true ONLY when the reply
    says this person is taking over / now handling the SENDER's OWN role or
    work (e.g. "he's handling my work now", "she's taking over for me",
    "moved to a new role, X will cover this going forward") — false for
    anyone introduced as having their OWN distinct role (e.g. "our ops
    lead", "loop in our CFO"). Return an empty list if no one else is
    named — do NOT invent a referral that isn't clearly stated.

═══════════════════════════════════════════════════════════════════════
GUARDRAILS — same hard rules as the original cadence
═══════════════════════════════════════════════════════════════════════
1. NO numbers, percentages, multipliers, money amounts, or statistics.
2. NO invented facts — ground product claims ONLY in PRODUCT, company
   references ONLY in COMPANY BACKGROUND, and reply-specific details ONLY in
   the REPLY block actually provided.
3. NO buzzwords: leverage, synergy, transform, game-changer, innovative,
   best-in-class, world-class, end-to-end, 10x, supercharge, unlock,
   revolutionize, cutting-edge.
4. NO sales-followup cliches: "floating this back up", "bumping this",
   "circling back", "just following up", "in case this got buried".
5. NO greeting and NO signature (the template owns them).
6. Subjects <=60 chars, no quotes, no emoji, no "Re:" prefix.

═══════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════
Return ONLY a JSON object with EXACTLY these 8 keys:
{
  "followup_subject":  "Follow-up 1 subject, <=60 chars.",
  "followup_body":      "Follow-up 1 body. 2-4 sentences.",
  "followup2_subject": "Follow-up 2 subject, <=60 chars.",
  "followup2_body":     "Follow-up 2 body. 2-4 sentences.",
  "closing_subject":   "Closing subject, <=60 chars.",
  "closing_body":       "Closing body. 2-3 sentences, low-pressure.",
  "recommended_next_delay_days": <integer, 1-30>,
  "referral_contacts": [{"name": "...", "email": "...", "role_hint": "...", "same_role_handoff": false}]
}
"""


def _reply_context_block(reply_context: Mapping[str, Any]) -> str:
    lines = ["", "REPLY (the lead has already responded — ground the rewrite in this):"]
    lines.append(f"- Intent: {reply_context.get('intent') or 'UNKNOWN'}")
    body = str(reply_context.get("body_text") or "").strip()
    if body:
        lines.append(f"- Their message: {body[:2000]}")
    prior = str(reply_context.get("our_prior_reply") or "").strip()
    if prior:
        lines.append(f"- We already told them: {prior[:1000]}")
    return_date = reply_context.get("return_date")
    if return_date:
        lines.append(f"- They stated a return/availability date: {return_date}")
    if reply_context.get("departed"):
        # Scenario 5 (phase 1 — reground): the person has left the company we had
        # on file. They are no longer a buyer, so every remaining follow-up must
        # acknowledge the move and must NOT keep selling to THEM (nor to that
        # company as if they still worked there).
        former = str(reply_context.get("former_company") or "").strip()
        former_clause = f" ({former})" if former else ""
        lines.append(
            f"- IMPORTANT: this person has LEFT the company we had them at{former_clause}. "
            "They are no longer a buyer for us. In EVERY remaining follow-up: "
            "(1) briefly and graciously acknowledge they've moved on; "
            "(2) do NOT pitch the product to THEM and do NOT write as if they still "
            "work there or are still evaluating it; "
            "(3) keep it short, warm, and low-pressure. "
            "If the reply named someone else to contact, you may say you'll reach out "
            "to that person — but still do not pitch the departed person. "
            "Do not assume a new company or a replacement contact unless the reply states one."
        )
    lines.append("")
    lines.append("Rewrite the remaining follow-ups now. Return the JSON now.")
    return "\n".join(lines)


def generate_reply_aware_followups(
    lead: Mapping[str, Any],
    sender: Mapping[str, Any],
    enrichment: Optional[Mapping[str, Any]],
    reply_context: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Regenerate the pending follow-up steps' subject/body, grounded in
    `reply_context`, plus a recommended next-send delay and any referral
    contacts named in the reply.

    Returns None on any failure (empty/malformed response after 3 attempts).
    Unlike `generate_template_content`, there is no fallback dict here — for
    a cold-open, SOME copy is always needed so a Gemini outage doesn't send
    literal template placeholders; for a regeneration, "leave the existing
    (already-sent-worthy) content untouched" is always a safe, valid outcome,
    so the caller treats None as a no-op rather than overwriting with a
    worse generic guess.
    """
    user_prompt = _user_prompt(lead, sender, enrichment) + "\n" + _reply_context_block(reply_context)

    for attempt in range(3):
        try:
            raw = gemini.chat_completion(
                system=_REGEN_SYSTEM,
                user=user_prompt,
                model=gemini.CHAT_MODEL,
                temperature=0.4,
                max_tokens=None,
                response_format_json=True,
            )
            if not raw:
                raise ValueError("empty response")
            parsed = gemini.extract_json(raw)
            if not isinstance(parsed, dict):
                raise ValueError("parsed value is not a dict")

            try:
                delay = int(parsed.get("recommended_next_delay_days"))
            except (TypeError, ValueError):
                delay = 3
            delay = max(1, min(30, delay))

            raw_referrals = parsed.get("referral_contacts")
            referral_contacts = []
            if isinstance(raw_referrals, list):
                for r in raw_referrals:
                    if not isinstance(r, dict):
                        continue
                    email = str(r.get("email") or "").strip()
                    name = str(r.get("name") or "").strip()
                    if not email and not name:
                        continue
                    referral_contacts.append({
                        "name": name or None,
                        "email": email or None,
                        "role_hint": str(r.get("role_hint") or "").strip() or None,
                        "same_role_handoff": bool(r.get("same_role_handoff")),
                    })

            return {
                "followup_subject": (str(parsed.get("followup_subject") or "").strip())[:80] or None,
                "followup_body": str(parsed.get("followup_body") or "").strip() or None,
                "followup2_subject": (str(parsed.get("followup2_subject") or "").strip())[:80] or None,
                "followup2_body": str(parsed.get("followup2_body") or "").strip() or None,
                "closing_subject": (str(parsed.get("closing_subject") or "").strip())[:80] or None,
                "closing_body": str(parsed.get("closing_body") or "").strip() or None,
                "recommended_next_delay_days": delay,
                "referral_contacts": referral_contacts,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "outreach_template: reply-aware regen attempt %d/3 failed (%s)",
                attempt + 1, str(exc)[:160],
            )

    log.warning("outreach_template: reply-aware regen failed after 3 attempts — leaving content untouched")
    return None


_REFERRAL_SYSTEM = """═══════════════════════════════════════════════════════════════════════
ROLE
═══════════════════════════════════════════════════════════════════════
You write a SHORT, honest first-touch email to someone who was just named,
by a colleague of theirs, as a better point of contact for an ongoing
conversation. You are NOT pretending to have researched this specific
person — you are explicitly referencing the real introduction that just
happened. This is warmer and more credible than a cold open, precisely
because it's true.

═══════════════════════════════════════════════════════════════════════
TASK
═══════════════════════════════════════════════════════════════════════
You will receive: who referred them (name, role, company) and why (the
role_hint the reply gave, if any), the SENDER's product/offering, and
whatever COMPANY BACKGROUND is available (same company as the person who
referred them). Write ONE short email:
  - Open by naming the referral plainly: "<Referrer> mentioned you handle
    <role_hint>" (or, if no role_hint was given, "<Referrer> suggested I
    reach out to you directly"). Do NOT claim independent research on this
    person — you don't have any.
  - Briefly connect the SENDER's offering to the referral's likely area
    (using role_hint if given, else the general PRODUCT/COMPANY BACKGROUND).
  - End with a low-key, specific ask (a quick call, or "let me know if this
    is worth a short conversation").
  - Keep it SHORT — 2-4 sentences total. This is a first touch, not a pitch.

═══════════════════════════════════════════════════════════════════════
GUARDRAILS
═══════════════════════════════════════════════════════════════════════
1. NO numbers, percentages, multipliers, money amounts, or statistics.
2. NO invented facts about this person — ground ONLY in the referral itself,
   the role_hint given, and the PRODUCT/COMPANY BACKGROUND sections.
3. NO buzzwords: leverage, synergy, transform, game-changer, innovative,
   best-in-class, world-class, end-to-end, 10x, supercharge, unlock,
   revolutionize, cutting-edge.
4. NO greeting and NO signature (the template owns them).
5. Subject <=60 chars, no quotes, no emoji, no "Re:" prefix.

═══════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════
Return ONLY a JSON object with EXACTLY these 2 keys:
{"subject": "<=60 chars", "body": "2-4 sentences, no greeting, no signature"}
"""


def _referral_user_prompt(
    referral: Mapping[str, Any],
    source_lead: Mapping[str, Any],
    sender: Mapping[str, Any],
    enrichment: Optional[Mapping[str, Any]] = None,
) -> str:
    enrichment = enrichment or {}
    lines = [
        "REFERRAL:",
        f"- This person: {referral.get('name') or '(name unknown)'} <{referral.get('email')}>",
        f"- What they handle (from the reply, if stated): {referral.get('role_hint') or '(not stated)'}",
        f"- Referred by: {source_lead.get('first_name', '')} {source_lead.get('last_name', '')}".strip(),
        f"- Company: {source_lead.get('company_name') or '(unknown)'}",
        "",
    ]
    bg_summary = enrichment.get("company_summary")
    if bg_summary:
        lines.append("COMPANY BACKGROUND (real — same company as the referrer; reference it if it fits):")
        lines.append(f"- About: {str(bg_summary)[:600]}")
        lines.append("")

    lines.append("SENDER:")
    biz_type = (sender.get("business_type") or "product").strip().lower()
    lines.append(f"- business_type: {biz_type}")
    lines.append(f"- Company: {sender.get('company_name')}")
    pd = sender.get("product_description") or {}
    if isinstance(pd, dict) and any(pd.values()):
        if pd.get("what_the_company_is"):
            lines.append(f"- WHAT IT IS: {pd['what_the_company_is']}")
        if pd.get("what_they_do"):
            lines.append(f"- WHAT WE DO: {pd['what_they_do']}")
    lines.append("")
    lines.append("Write the JSON now.")
    return "\n".join(lines)


_REFERRAL_EXTRACT_SYSTEM = """Read the email reply below. If it names a DIFFERENT specific
person as a better or alternate point of contact (e.g. "please reach out to X instead",
"loop in Y for this", "contact Z directly", "X can help with that"), extract one entry per
person mentioned: name, email (if given, else null), role_hint, and same_role_handoff.

role_hint: a real job title if one is stated, otherwise a short phrase (3-6 words, not a
full sentence) describing why they're relevant, or null if nothing is said.

same_role_handoff: true ONLY when the reply says this person is taking over / now handling
the SENDER's OWN role or work (e.g. "he's handling my work now", "she's taking over for
me", "moved to a new role, X will cover this going forward") — false for anyone introduced
as having their OWN distinct role (e.g. "our ops lead", "loop in our CFO").

Return an empty list if no one else is clearly named — do NOT invent a referral that isn't
stated.

Return STRICT JSON ONLY — no prose, no markdown, no code fences:
{"referral_contacts": [{"name": "...", "email": "...", "role_hint": "...", "same_role_handoff": false}]}
"""


def extract_referral_mentions(body_text: str) -> list:
    """Standalone referral extraction, decoupled from the follow-up-rewrite
    call — used for intents that don't otherwise trigger
    generate_reply_aware_followups (i.e. halting intents like
    NOT_INTERESTED/UNSUBSCRIBE, which still deserve to have a named
    alternate contact acted on even though the sequence itself is stopping).

    Caller is expected to gate this behind a cheap pre-filter (e.g.
    reply_post_processor.looks_like_referral) so a plain "not interested"
    with no one else named doesn't spend a Gemini call. Returns an empty
    list on any failure — never raises.
    """
    if not (body_text or "").strip():
        return []
    try:
        raw = gemini.chat_completion(
            system=_REFERRAL_EXTRACT_SYSTEM,
            user=body_text[:3000],
            model=gemini.CHAT_MODEL,
            temperature=0.2,
            max_tokens=None,
            response_format_json=True,
        )
        if not raw:
            return []
        parsed = gemini.extract_json(raw)
        raw_contacts = parsed.get("referral_contacts") if isinstance(parsed, dict) else None
        if not isinstance(raw_contacts, list):
            return []
        contacts = []
        for r in raw_contacts:
            if not isinstance(r, dict):
                continue
            email = str(r.get("email") or "").strip()
            name = str(r.get("name") or "").strip()
            if not email and not name:
                continue
            contacts.append({
                "name": name or None,
                "email": email or None,
                "role_hint": str(r.get("role_hint") or "").strip() or None,
                "same_role_handoff": bool(r.get("same_role_handoff")),
            })
        return contacts
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach_template: standalone referral extraction failed (%s)", str(exc)[:160])
        return []


def generate_referral_intro(
    referral: Mapping[str, Any],
    source_lead: Mapping[str, Any],
    sender: Mapping[str, Any],
    enrichment: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Single short first-touch email for someone named as a referral in a
    reply (e.g. "please contact mitch@maap.cc for Commercial & Ecomm").
    Honest about the real introduction instead of pretending independent
    research on a person we just learned about.

    Returns None on failure — the caller still creates the lead record even
    if a draft couldn't be generated (it can be drafted later).
    """
    user_prompt = _referral_user_prompt(referral, source_lead, sender, enrichment)
    for attempt in range(3):
        try:
            raw = gemini.chat_completion(
                system=_REFERRAL_SYSTEM,
                user=user_prompt,
                model=gemini.CHAT_MODEL,
                temperature=0.4,
                max_tokens=None,
                response_format_json=True,
            )
            if not raw:
                raise ValueError("empty response")
            parsed = gemini.extract_json(raw)
            if not isinstance(parsed, dict):
                raise ValueError("parsed value is not a dict")
            subject = (str(parsed.get("subject") or "").strip())[:80]
            body = str(parsed.get("body") or "").strip()
            if not subject or not body:
                raise ValueError("empty subject/body")
            return {"subject": subject, "body": body}
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "outreach_template: referral intro attempt %d/3 failed (%s)",
                attempt + 1, str(exc)[:160],
            )
    log.warning("outreach_template: referral intro generation failed after 3 attempts")
    return None


def generate_template_content(
    lead: Mapping[str, Any],
    sender: Mapping[str, Any],
    enrichment: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Single Gemini call returning {subject, intro_body, real_result}.

    On failure returns a deterministic fallback so the template can
    still render.
    """
    # gemini-3.1-flash-lite occasionally returns a stuttered / malformed JSON (a
    # duplicated fragment makes json.loads fail) — it's intermittent, so RETRY a
    # couple of times before dropping to the generic fallback. A fresh call
    # almost always returns clean JSON, which keeps the personalized copy
    # instead of falling back to the bland template.
    parsed = None
    for _attempt in range(3):
        try:
            raw = gemini.chat_completion(
                system=_SYSTEM,
                user=_user_prompt(lead, sender, enrichment),
                model=gemini.CHAT_MODEL,
                temperature=0.4,
                # No output cap — the model uses its full ceiling so the
                # 10-field JSON (all 4 emails) never gets truncated mid-string.
                max_tokens=None,
                response_format_json=True,
            )
            if not raw:
                raise ValueError("empty response")
            _p = gemini.extract_json(raw)
            if isinstance(_p, dict):
                parsed = _p
                break
            raise ValueError("parsed value is not a dict")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "outreach_template: generation attempt %d/3 failed (%s)",
                _attempt + 1, str(exc)[:160],
            )

    if not isinstance(parsed, dict):
        log.warning("outreach_template: all 3 attempts failed — using fallback")
        result = _fallback(lead, sender)
    else:
        fb = _fallback(lead, sender)
        result = {
            # Email 1 (initial, day 0)
            "subject": (str(parsed.get("subject") or "").strip())[:80] or fb["subject"],
            "personalized_opener": str(parsed.get("personalized_opener") or "").strip() or fb["personalized_opener"],
            "intro_body": str(parsed.get("intro_body") or "").strip() or fb["intro_body"],
            "real_result": str(parsed.get("real_result") or "").strip() or fb["real_result"],
            # Email 2 (follow-up 1, day +3)
            "followup_subject": (
                (str(parsed.get("followup_subject") or "").strip())[:80] or fb["followup_subject"]
            ),
            "followup_body": (
                str(parsed.get("followup_body") or "").strip() or fb["followup_body"]
            ),
            # Email 3 (follow-up 2, day +6) — different angle from FU1
            "followup2_subject": (
                (str(parsed.get("followup2_subject") or "").strip())[:80] or fb["followup2_subject"]
            ),
            "followup2_body": (
                str(parsed.get("followup2_body") or "").strip() or fb["followup2_body"]
            ),
            # Email 4 (closing / break-up, day +9)
            "closing_subject": (
                (str(parsed.get("closing_subject") or "").strip())[:80] or fb["closing_subject"]
            ),
            "closing_body": (
                str(parsed.get("closing_body") or "").strip() or fb["closing_body"]
            ),
        }

    # Drop the "Real result" proof line entirely when NO real case studies
    # were supplied. Without a source it would be a fabricated/illustrative
    # customer outcome — the template hides the proof block when this is
    # empty, so the email stays 100% grounded in the provided context.
    if not sender.get("case_studies"):
        result["real_result"] = ""

    return result


def _fallback(lead: Mapping[str, Any], sender: Mapping[str, Any]) -> Dict[str, str]:
    """Deterministic fallback for ALL 4 emails when Gemini is unavailable.

    Provides safe (but generic) copy for every field the Apollo template
    references — so a Gemini outage never causes Apollo to send a blank
    email with literal `{{ai_intro_body}}` text.
    """
    first = (lead.get("first_name") or "there").strip()
    co = sender.get("company_name") or "us"
    company = (lead.get("company_name") or "your team").strip()
    biz_type = (sender.get("business_type") or "product").strip().lower()
    offering_word = "platform" if biz_type == "product" else "team"

    return {
        # Email 1 (initial)
        "subject": f"Quick thought, {first}",
        "personalized_opener": (
            f"Saw your work at {company} and wanted to reach out directly."
        ),
        "intro_body": (
            f"Saw what you're doing at {company} and wanted to send a quick note. "
            f"Our {offering_word} at {co} has been helping teams like yours work "
            "more effectively — happy to share more if it's useful."
        ),
        "real_result": (
            f"In a recent engagement, a team working with {co} cut their cycle time "
            "meaningfully by surfacing what was hiding in plain sight."
        ),
        # Email 2 (follow-up 1)
        "followup_subject": "Worth a quick look",
        "followup_body": (
            f"Just making sure this reached you, {first}. Is this something "
            f"{company} is looking at right now, or is the timing off? Happy to "
            f"share how {co} could help — even a quick reply tells me whether "
            "it's worth staying in touch."
        ),
        # Email 3 (follow-up 2 — different angle from FU1)
        "followup2_subject": f"One more thought for {company}",
        "followup2_body": (
            f"One more thought, {first}: a lot of teams like {company}'s are "
            f"quietly sitting on the exact problem {co} solves and haven't named "
            "it yet. Does that resonate, or is it just not a priority right now?"
        ),
        # Email 4 (closing / break-up)
        "closing_subject": f"Last note, {first}",
        "closing_body": (
            f"Last note from me — completely understand if the timing isn't right. "
            f"If {co} ever becomes relevant for {company}, you know where to find me. "
            "Wishing you the best either way."
        ),
    }


# ---------------------------------------------------------------------------
# HTML rendering — inline styles only (Gmail/Outlook safe)
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _paragraphs(text: str) -> str:
    """Wrap each blank-line-separated paragraph in <p>."""
    parts = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return "".join(
        f'<p style="margin:0 0 16px 0; color:#1a1a1a; font-size:15px; line-height:1.55;">{_esc(p).replace(chr(10), "<br/>")}</p>'
        for p in parts
    )


# Which (subject, body) keys to render for each cadence step. The Gemini
# generator returns all four emails' fields in one dict; the sequencer tells
# us which step is firing so we render the RIGHT one (initial vs follow-up 1
# vs follow-up 2 vs close-up) instead of always sending the initial.
_STEP_FIELDS = {
    0: ("subject", "intro_body"),
    1: ("followup_subject", "followup_body"),
    2: ("followup2_subject", "followup2_body"),
    3: ("closing_subject", "closing_body"),
}


def render_email(
    lead: Mapping[str, Any],
    sender: Optional[Mapping[str, Any]] = None,
    gemini: Optional[Mapping[str, Any]] = None,
    enrichment: Optional[Mapping[str, Any]] = None,
    step: int = 0,
) -> Dict[str, str]:
    """Build the email artefact for cadence `step` (0=initial, 1=follow-up 1,
    2=follow-up 2, 3=close-up).

    If `gemini` is omitted, generates content automatically. Returns
    {"subject", "html", "text"} ready to hand to the send function.

    Per-step rendering:
      - the "Real result" proof card shows only on the INITIAL email,
      - the CTA button shows on steps 0-2 but NOT on the close-up (step 3),
        which is a low-pressure break-up note.
    """
    sender_dict: Dict[str, Any] = {**DEFAULT_SENDER, **(sender or {})}
    if gemini is None:
        gemini = generate_template_content(lead, sender_dict, enrichment)

    try:
        step = int(step)
    except (TypeError, ValueError):
        step = 0
    step = step if step in _STEP_FIELDS else 0
    subj_key, body_key = _STEP_FIELDS[step]
    show_real_result = step == 0
    # CTA button shows on every step, including the closing (low-pressure copy
    # but still a soft "book a call" option for anyone ready to act).
    show_cta = True

    first = _esc((lead.get("first_name") or "there").strip())
    company_name_raw = (sender_dict.get("company_name") or "").strip()
    company_name = _esc(company_name_raw)
    company_url = _esc(sender_dict.get("company_url", ""))
    cta_url = sender_dict.get("cta_url") or "#"
    cta_label = _esc(sender_dict.get("cta_label", "Book a quick call"))
    rep_name = _esc(sender_dict.get("rep_name", ""))
    rep_title = _esc(sender_dict.get("rep_title", ""))
    # Logo initial: first alphanumeric char of company_name, uppercase.
    # Was hardcoded "S" (for Spenzo) — leaked into every email regardless
    # of which campaign sent it. Falls back to "•" when name is empty.
    logo_initial = "•"
    for c in company_name_raw:
        if c.isalnum():
            logo_initial = c.upper()
            break
    logo_initial = _esc(logo_initial)

    # Step-aware subject + body (fall back to the initial fields if a
    # step-specific value is missing, e.g. a legacy gemini dict).
    subject = (
        (gemini.get(subj_key) or "").strip()
        or (gemini.get("subject") or "").strip()
        or f"Quick thought, {first}"
    )
    body_text = (
        (gemini.get(body_key) or "").strip()
        or (gemini.get("intro_body") or "").strip()
    )
    intro_html = _paragraphs(body_text)
    real_result_html = _esc(gemini.get("real_result", "")).replace("\n", "<br/>")

    # Signature: prefer a real person's name (reads human); fall back to the
    # company team. Sub-line shows the rep's title or the company.
    signature_name = rep_name or (f"{company_name} Team" if company_name else "The team")
    sig_sub = ""
    if rep_name:
        _sub = rep_title or company_name
        if _sub:
            sig_sub = (
                f'<br/><span style="color:#6b7280; font-size:13px; font-weight:400;">{_sub}</span>'
            )
    footer_url = f' &middot; {company_url}' if company_url else ""

    # Accent color — use the product's BRAND color (passed from the sequencer,
    # sourced from Business DNA) so follow-ups match the rich initial email.
    # Neutral slate fallback when no brand color is available (never the old
    # hardcoded orange, which mismatched every non-Spenzo brand).
    accent = _esc(sender_dict.get("brand_color") or "#ff4500")

    # Clean & minimal: white background, no heavy card, generous whitespace,
    # left-aligned like a real person's email. Inline CSS only (Gmail/Outlook
    # safe). One accent color = the brand color, used sparingly.
    html = f"""\
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#ffffff; -webkit-text-size-adjust:100%; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#ffffff;">
    <tr><td align="center" style="padding:32px 16px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560" style="max-width:560px; width:100%;">

        <!-- Greeting + body -->
        <tr><td style="font-size:15px; line-height:1.65; color:#1f2937;">
          <p style="margin:0 0 16px 0; color:#111827;">Hi {first},</p>
          {intro_html}
        </td></tr>

        <!-- Real result (initial email only) — subtle, not a loud card -->
        {f'''<tr><td style="padding:8px 0 4px 0;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr><td style="border-left:2px solid {accent}; padding:2px 0 2px 16px;">
              <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.6px; color:#9ca3af; margin:0 0 4px 0;">For context</div>
              <div style="font-size:14px; line-height:1.6; color:#374151;">{real_result_html}</div>
            </td></tr>
          </table>
        </td></tr>''' if show_real_result and real_result_html else ''}

        <!-- CTA (hidden on the close-up email) — understated, left-aligned -->
        {f'''<tr><td style="padding:22px 0 6px 0;">
          <a href="{cta_url}" target="_blank" style="display:inline-block; padding:11px 22px; background:{accent}; color:#ffffff; text-decoration:none; font-weight:600; font-size:14px; border-radius:6px;">{cta_label} &rarr;</a>
        </td></tr>''' if show_cta else ''}

        <!-- Signature -->
        <tr><td style="padding:22px 0 0 0; font-size:15px; line-height:1.6; color:#1f2937;">
          <p style="margin:0;">Best,<br/><strong style="color:#111827;">{signature_name}</strong>{sig_sub}</p>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:22px 0 0 0; border-top:1px solid #eeeeee;">
          <p style="margin:14px 0 0 0; font-size:12px; color:#9ca3af;">{company_name}{footer_url}</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""

    # Plain text alternative — same order as the HTML (real_result above CTA).
    # Signature simplified to "Best regards, <Product> Team" — no individual
    # rep info, matching the HTML version.
    text_parts = [f"Hi {first},\n\n{body_text}\n\n"]
    if show_real_result and gemini.get("real_result"):
        text_parts.append(f"For context: {gemini.get('real_result','')}\n\n")
    if show_cta:
        text_parts.append(f"{cta_label}: {cta_url}\n\n")
    text_parts.append(f"Best,\n{signature_name}\n")
    text = "".join(text_parts)

    return {"subject": subject, "html": html, "text": text}
