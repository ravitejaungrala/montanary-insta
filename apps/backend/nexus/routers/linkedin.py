"""NEXUS LinkedIn outreach router.

Endpoints:
  POST /nexus/linkedin/generate/{lead_id}    AI-write a LinkedIn DM for a lead
  POST /nexus/linkedin/send/{message_id}     mark a draft as sent (records send;
                                              actual delivery is operator-driven
                                              since LinkedIn has no first-party
                                              outbound API for DMs)
  GET  /nexus/linkedin/pending               list draft DMs awaiting send
  GET  /nexus/linkedin/lead/{lead_id}        list DM history for a single lead

Generation is per-company deduplicated: if the lead's company_domain already
has an outbound draft (any direction='outbound') we return the existing draft
rather than burning another LLM call.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import get_nexus_user, require_current_workspace, trial_guard
from nexus.models_phase3 import NexusGlobalLead, NexusLead
from nexus.models_phase5 import LinkedInMessage
from nexus.services.gemini import CHAT_MODEL as GEMINI_MODEL  # single source of truth

logger = logging.getLogger("nexus.routers.linkedin")

router = APIRouter(prefix="/nexus/linkedin", tags=["Nexus — LinkedIn"])


class LinkedInMessageOut(BaseModel):
    id: int
    workspace_id: int
    lead_id: Optional[int] = None
    direction: str
    body: Optional[str] = None
    linkedin_message_urn: Optional[str] = None
    sent_at: Optional[datetime] = None
    intent: Optional[str] = None
    # `variant` and `subject` were added in phase5's ADD COLUMN
    # IF NOT EXISTS step. Both are nullable / defaulted so existing
    # DM rows still serialize cleanly (variant='dm', subject=None).
    variant: Optional[str] = None
    subject: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- LLM generation ----------------------------------------------------


_LI_SYSTEM = (
    "ROLE\n"
    "You are an elite LinkedIn outreach copywriter specializing in personalized "
    "connection requests to senior B2B decision-makers. You write short, "
    "natural, and highly personalized connection notes that earn acceptance and "
    "start genuine conversations.\n\n"
    "OBJECTIVE\n"
    "Earn a reply: tie one real detail about the prospect's company to ONE "
    "concrete way the product helps, then make one low-friction ask. Every "
    "sentence must be specific to THIS person/company — if the message could be "
    "sent to any other lead unchanged, rewrite it.\n\n"
    "STEPS (follow in order)\n"
    "1. Read the CONTEXT below (lead profile, company background, product "
    "description).\n"
    "2. Open with ONE specific, TRUE observation about the lead's company or "
    "role — taken only from the company background or their LinkedIn headline.\n"
    "3. In one sentence, say how the product helps a team like theirs — using "
    "ONLY the product description.\n"
    "4. End with ONE clear, low-pressure ask (a quick look, 15 minutes, or "
    "'open to a chat?').\n\n"
    "GROUNDING RULES (must follow)\n"
    "- Use ONLY facts present in the CONTEXT. Never invent companies, news, "
    "metrics, customers, or capabilities.\n"
    "- Describe the product ONLY from the product description. Do NOT rename or "
    "add capabilities (e.g. if it does 'spend optimization', do not call it "
    "'revenue forecasting').\n"
    "- NO numbers, percentages, money, or statistics — words only.\n"
    "- No greetings like 'I hope this finds you well', no buzzwords (leverage, "
    "synergy, game-changer, unlock), no emojis.\n"
    "- If the context has no real detail, keep the opener about their role / "
    "company name only — do NOT fabricate.\n\n"
    "OUTPUT FORMAT\n"
    "- Plain text only. No labels, no quotes, no subject line.\n"
    "- Max 200 characters total — this is a LinkedIn connection-request note "
    "(free accounts cap at 200). Aim for 150-200. Hard cap 200.\n\n"
    "EXAMPLES\n"
    "GOOD (lead: Maya, VP Marketing at Brightleaf, a DTC brand scaling paid "
    "social; product: an attribution platform that spots wasted vs underfunded "
    "channels) — note it is specific to Brightleaf and under 200 chars:\n"
    "  Saw Brightleaf is scaling paid social and CTV — that's often where "
    "channel spend quietly starts overlapping. We help teams see which channels "
    "actually drive results. Open to a quick look?\n"
    "BAD (and WHY):\n"
    "  Hi Maya! Hope this finds you well. Our game-changing AI platform boosts "
    "revenue 3x with revenue forecasting for brands like yours — let's hop on a "
    "call to unlock synergies!\n"
    "  WHY BAD: filler greeting + buzzwords; invented '3x' stat; claims "
    "'revenue forecasting' (a capability not in the product description); "
    "generic and pushy."
)


def _context_block(
    *,
    role: str = "",
    company: str = "",
    location: str = "",
    linkedin_headline: str = "",
    product_name: str = "",
    product_description: Optional[dict] = None,
    company_summary: str = "",
    company_news: Optional[list] = None,
) -> str:
    """Build the shared grounded-context block fed to BOTH the DM and InMail
    prompts — the same knowledge base the email path uses (3-section product
    description + lead profile + real company background). All fields optional;
    only the parts we actually have are rendered."""
    sections: list = []

    lead_bits = []
    if linkedin_headline:
        lead_bits.append(f"LinkedIn headline: {linkedin_headline}")
    if location:
        lead_bits.append(f"Location: {location}")
    if lead_bits:
        sections.append("LEAD:\n" + "\n".join(f"  - {b}" for b in lead_bits))

    pd = product_description if isinstance(product_description, dict) else {}
    if pd:
        p = []
        if pd.get("what_the_company_is"):
            p.append(f"What it is: {pd['what_the_company_is']}")
        if pd.get("what_they_do"):
            p.append(f"What we do: {pd['what_they_do']}")
        caps = pd.get("key_capabilities")
        if isinstance(caps, list) and caps:
            p.append("Capabilities: " + "; ".join(str(c) for c in caps[:6]))
        serve = pd.get("who_they_serve") or ""
        inds = pd.get("target_industries")
        if isinstance(inds, list) and inds:
            serve = (serve + " - " if serve else "") + ", ".join(str(i) for i in inds[:6])
        if serve:
            p.append(f"Who we serve: {serve}")
        if p:
            sections.append(
                f"PRODUCT ({product_name}) - grounded, use ONLY this:\n"
                + "\n".join(f"  - {x}" for x in p)
            )

    bg = []
    if company_summary:
        bg.append(f"About: {company_summary}")
    if isinstance(company_news, list) and company_news:
        bg.append("Recent: " + " | ".join(str(n) for n in company_news[:2]))
    if bg:
        sections.append(
            "LEAD'S COMPANY BACKGROUND (real facts - ground the opener in this):\n"
            + "\n".join(f"  - {b}" for b in bg)
        )

    return "\n\n".join(sections)


def _generate_li_body(
    *,
    first_name: str,
    role: str,
    company: str,
    product_name: str,
    product_value_prop: str,
    location: str = "",
    linkedin_headline: str = "",
    product_description: Optional[dict] = None,
    company_summary: str = "",
    company_news: Optional[list] = None,
) -> str:
    """Call Gemini for the DM body. Falls back to a static template if no key."""
    fallback = (
        f"Hey {first_name or 'there'} — saw your work at {company or 'your company'}. "
        f"We help teams like yours with {product_value_prop or product_name}. "
        f"Worth a quick 15-min look?"
    )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception:
        return fallback

    ctx = _context_block(
        role=role,
        company=company,
        location=location,
        linkedin_headline=linkedin_headline,
        product_name=product_name,
        product_description=product_description,
        company_summary=company_summary,
        company_news=company_news,
    )
    prompt = (
        f"Lead: {first_name} — {role} at {company}\n"
        + (f"\n{ctx}\n" if ctx else f"Product: {product_name} — {product_value_prop or 'B2B SaaS'}\n")
        + "\nWrite the DM body only (no quotes, no labels)."
    )
    try:
        client = genai.Client(api_key=api_key)
        # max_output_tokens=600 because Gemini 2.5 Flash counts its hidden
        # "thinking" tokens against this budget. At 200 the visible reply
        # was being truncated to single fragments ("BlackRock's scale").
        # 600 leaves room for ~300 chars of DM after a typical
        # think-budget. Empty / sub-50 char replies fall back below.
        # Also disable thinking outright when the SDK supports it —
        # we don't need chain-of-thought for a 300-char DM.
        gen_kwargs: dict = {
            "system_instruction": _LI_SYSTEM,
            "temperature": 0.5,
            "max_output_tokens": 600,
        }
        try:
            # google-genai >= 0.4 exposes types.ThinkingConfig; older SDK
            # builds don't. Best-effort import + best-effort attach.
            gen_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**gen_kwargs),
        )
        body = (resp.text or "").strip()
        # Reject obviously truncated single-fragment outputs and use the
        # deterministic fallback instead — keeps the UI from showing
        # garbage like "BlackRock's scale" with a Copy button.
        if len(body) < 50:
            logger.warning(
                "linkedin gen returned short reply (%d chars) — using fallback",
                len(body),
            )
            return fallback
        # LinkedIn connection-request note caps at 200 chars on free accounts
        # (300 only on Premium). Cap at 200 so the note is never rejected.
        if len(body) > 200:
            body = body[:200].rstrip()
        return body
    except Exception as exc:  # noqa: BLE001
        logger.warning("linkedin gen failed: %s", exc)
        return fallback


# ---------- InMail generation -------------------------------------------------
#
# Separate from the DM generator because LinkedIn InMail has different
# constraints: it's a paid message (Premium / Sales Navigator) sent to
# people you're NOT connected to, it carries an explicit subject line,
# and the body sits in the 500-1500 char range — a much longer, more
# formal pitch than a 300-char DM. The DM generator above is untouched;
# this is a fully parallel path so existing /generate behaviour is
# unaffected.

_INMAIL_SYSTEM = """ROLE
You are an elite LinkedIn InMail copywriter specializing in personalized 1:1
outreach to senior B2B decision-makers. You write natural, relevant, and highly
personalized InMails that clearly communicate how the product can help the
prospect's team and encourage meaningful engagement.

OBJECTIVE
Using ONLY the CONTEXT below, write a 4-step InMail cadence for ONE lead:
step 0 Initial, step 1 Follow-up 1, step 2 Follow-up 2, step 3 Closing.

INSUFFICIENT-CONTEXT RULE (most important)
If the context lacks enough information to personalize, do NOT invent details.
Use only the available context and personalize as much as possible from the
lead's role, company, and product relevance — in that priority order.

PERSONALIZATION REQUIREMENT
- Every message must reference at least one lead-specific detail from the context.
- Do not write generic outreach that could be reused for another prospect.
- If context is thin, use company-specific detail before role-based detail.

SUBJECT LINE (every step)
- 40-90 characters (hard max 200); must display fully on mobile.
- Personalize with their company or role.
- Frame as a genuine QUESTION when natural — question subjects open better.
- No ALL-CAPS, no excessive punctuation, no salesy phrasing.

INITIAL BODY — write these beats, in order, one tight line each:
1. HOOK   — something specific & true about them/their company. Front-load it.
2. VALUE  — what's in it for THEM, using ONLY the product description (the
            outcome, not a feature list).
3. CREDIBILITY — see CREDIBILITY RULE (often omitted).
4. CTA    — one low-friction ask in words (see CTA RULES).

CREDIBILITY RULE
- Never fabricate credibility. If no customer proof, industry proof, partner
  proof, or mutual context exists in the context, OMIT the credibility line
  entirely. Do not replace it with marketing language.

CTA RULES
- Exactly one CTA per message; low-friction; vary the wording across the cadence.
- Write the CTA in WORDS ONLY. Do NOT include any URL or link — the system
  appends the booking link automatically after your message.
- Examples: "Open to a quick look?" / "Worth exploring?" / "Is this something
  your team is evaluating?" / "Would it make sense to compare notes?"
- Never use aggressive meeting demands.

FOLLOW-UP STRATEGY
- Follow-up 1: expand on a DIFFERENT pain point, workflow, or outcome.
- Follow-up 2: introduce a DIFFERENT observation, question, or use case.
- Never repeat earlier value-proposition wording. Never summarize prior messages.
- Never say "following up", "bumping this", "checking in", or similar.

CLOSING (step 3)
- Brief, polite break-up. No pressure, no guilt-based language.
- Explicitly leave the door open; if it becomes relevant later, invite them to
  reach out or connect. End positively and professionally.

LENGTH
- Initial body: 1000-1300 characters. Hard max 1,900.
- Follow-up 1 & 2: 700-1000 characters each.
- Closing: 1-3 short sentences.
- Use line breaks between beats so longer messages stay skimmable on mobile.

LINKEDIN INMAIL RULES
- Write like a LinkedIn conversation, not an email. Avoid formal email language
  and long introductions. One question per message. Readable on mobile.

GROUNDING RULES (non-negotiable)
- Use ONLY facts in the context. Never invent companies, news, metrics,
  customers, funding, or capabilities.
- Describe the product ONLY from the product description; never rename or broaden
  a capability (e.g. "spend optimization" is NOT "revenue forecasting").
- NO numbers, percentages, money, or statistics — words only.
- No self-credentials, no "I hope this finds you well", no buzzwords (leverage,
  synergy, game-changer, unlock), no emojis, no [Placeholder] tokens, no URLs.

SELF-CHECK BEFORE OUTPUT — verify; if any fails, rewrite:
1. Each message has lead-specific context.  2. No invented facts/metrics/
customers.  3. No buzzwords.  4. No placeholders or URLs.  5. No repeated CTA
wording.  6. No repeated value-prop wording.  7. Length: initial body
1000-1300 chars, each follow-up 700-1000 chars.  8. Reads like LinkedIn InMail,
not email.

OUTPUT FORMAT — return STRICTLY this JSON, nothing else:
{
  "initial":    {"subject": "...", "message": "..."},
  "followup_1": {"subject": "...", "message": "..."},
  "followup_2": {"subject": "...", "message": "..."},
  "closing":    {"subject": "...", "message": "..."}
}

EXAMPLE — GOOD (lead: Maya, VP Marketing at Brightleaf, a DTC brand scaling paid
social + connected TV; product: an attribution platform that flags wasted vs
underfunded channels)
{
  "initial": {"subject": "Is Brightleaf's channel mix getting hard to read?", "message": "Hi Maya,\\n\\nI've been following Brightleaf's push into paid social and connected TV - it's clearly working, but it's also usually the exact moment when channel performance gets harder to read. As you add channels, spend starts overlapping in ways the platform dashboards quietly hide, and it gets tough to tell which channels are genuinely driving revenue versus which are simply riding on the momentum of others.\\n\\nThat's the gap we help marketing teams close. We bring your channel data together and model how each one actually contributes to results, so instead of guessing you can see where budget is working, where it's being wasted, and where a shift would pay off - before you commit the spend.\\n\\nWe work with other DTC growth teams wrestling with the same multi-channel complexity, so the patterns at Brightleaf's stage tend to be familiar ones.\\n\\nWould it be worth a short look at how this maps to your current channel mix - or is there someone on your side who owns attribution I should include?"},
  "followup_1": {"subject": "Are some channels riding on others' results?", "message": "Hi Maya,\\n\\nCircling back with a different angle. A pattern we see a lot with brands scaling paid social and CTV is that a slice of spend ends up buying conversions you'd have won anyway through organic or branded search - so the channel looks efficient on its own dashboard, while the real incremental lift actually sits elsewhere.\\n\\nAt Brightleaf's stage, untangling that usually matters more than the headline return number, because it changes where the next bit of budget should go. We model each channel's true contribution side by side, including the overlap, so you can see which channels are genuinely additive versus which are quietly double-counting.\\n\\nWould it be useful to look at how that breaks down for your current mix?"},
  "followup_2": {"subject": "A thought before next quarter's planning", "message": "Hi Maya,\\n\\nOne more thought, then I'll leave it with you. As you head into the next planning cycle, the hardest question is usually not how much to spend but where to move it - and that's tough to answer from historical reports alone, since last quarter's mix won't behave the same way next quarter.\\n\\nThis is where teams at Brightleaf's stage tend to get the most value from us: instead of reacting after the spend lands, you can simulate a few budget scenarios up front and see the likely trade-offs between channels before committing. It turns planning into something you can pressure-test rather than guess at.\\n\\nIf that's relevant for your upcoming cycle, would it make sense to compare notes?"},
  "closing": {"subject": "Closing the loop", "message": "Hi Maya - I'll leave this here so I'm not crowding your inbox.\\n\\nIf this becomes relevant for Brightleaf down the road, feel free to reach out or connect anytime.\\n\\nWishing you continued success."}
}

EXAMPLE — BAD (and WHY)
{"initial": {"subject": "UNLOCK 3X REVENUE NOW!!", "message": "Hi Maya, hope this finds you well! As an award-winning platform our game-changing AI gives [Company] revenue forecasting and 3x ROAS - book now: spenzo.ai/demo"}}
WHY BAD: ALL-CAPS clickbait subject; filler greeting; self-credentials; buzzwords;
invented "3x" stat; [Company] placeholder; claims "revenue forecasting" (not in
the product description); and it hard-codes a URL (the system appends the link).
"""


# InMail cadence step keys, in order. Index = stored `step` (0..3).
_INMAIL_STEPS = ("initial", "followup_1", "followup_2", "closing")


def _append_cta(body: str, cta_url: str) -> str:
    """Append the booking link on its own line. The model writes the CTA in
    words; the URL is placed by code so it can never be mangled/invented."""
    body = (body or "").strip()
    if cta_url:
        body = f"{body}\n\nBook a time: {cta_url}".strip()
    return body


def _generate_inmail(
    *,
    first_name: str,
    role: str,
    company: str,
    product_name: str,
    product_value_prop: str,
    location: str = "",
    linkedin_headline: str = "",
    product_description: Optional[dict] = None,
    company_summary: str = "",
    company_news: Optional[list] = None,
    cta_url: str = "",
) -> dict:
    """Call Gemini for a 4-STEP InMail cadence (initial / follow-up 1 /
    follow-up 2 / closing). Falls back to a deterministic cadence if no
    GEMINI_API_KEY / SDK / valid output.

    Returns: {"initial": {"subject","body"}, "followup_1": {...},
              "followup_2": {...}, "closing": {...}} — never raises. The
    booking `cta_url` (if any) is appended to each step's body in code.
    """
    def _fb_body(text: str) -> str:
        return _append_cta(text, cta_url)

    co = company or "your team"
    fallback = {
        "initial": {
            "subject": f"A quick idea for {co}",
            "body": _fb_body(
                f"Hi {first_name or 'there'},\n\nI've been following the work {co} is doing. "
                f"{product_value_prop or product_name or 'Our platform'} helps teams in "
                f"similar spots. Open to a quick look?"
            ),
        },
        "followup_1": {
            "subject": f"One more thought for {co}",
            "body": _fb_body(
                f"Hi {first_name or 'there'}, a different angle: {product_name or 'our platform'} "
                f"is built for exactly the stage {co} is at. Worth exploring?"
            ),
        },
        "followup_2": {
            "subject": f"Still relevant for {co}?",
            "body": _fb_body(
                f"Hi {first_name or 'there'}, happy to share how teams like {co} approach this. "
                f"Would it make sense to compare notes?"
            ),
        },
        "closing": {
            "subject": "Closing the loop",
            "body": _fb_body(
                f"Hi {first_name or 'there'}, I'll leave this here so I'm not crowding your "
                f"inbox. If it becomes relevant for {co} down the road, feel free to reach out "
                f"anytime. Wishing you continued success."
            ),
        },
    }

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception:
        return fallback

    ctx = _context_block(
        role=role,
        company=company,
        location=location,
        linkedin_headline=linkedin_headline,
        product_name=product_name,
        product_description=product_description,
        company_summary=company_summary,
        company_news=company_news,
    )
    prompt = (
        f"Lead: {first_name} — {role} at {company}\n"
        + (f"\n{ctx}\n\n" if ctx else f"Product: {product_name} — {product_value_prop or 'B2B SaaS'}\n\n")
        + "Write the 4-step InMail cadence strictly as the JSON shape in OUTPUT FORMAT."
    )
    try:
        client = genai.Client(api_key=api_key)
        gen_kwargs: dict = {
            "system_instruction": _INMAIL_SYSTEM,
            "temperature": 0.5,
            # Budget for 4 messages + Gemini's hidden thinking tokens.
            "max_output_tokens": 4000,
            "response_mime_type": "application/json",
        }
        try:
            gen_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**gen_kwargs),
        )
        raw = (resp.text or "").strip()
        import json
        import re
        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None
        if not isinstance(parsed, dict):
            logger.warning("inmail gen: model output not JSON — using fallback")
            return fallback

        out: dict = {}
        for key in _INMAIL_STEPS:
            step_obj = parsed.get(key)
            if not isinstance(step_obj, dict):
                logger.warning("inmail gen: missing step %s — using fallback", key)
                return fallback
            subject = str(step_obj.get("subject") or "").strip()
            # Accept either "message" (prompt key) or "body" defensively.
            body = str(step_obj.get("message") or step_obj.get("body") or "").strip()
            if len(subject) < 4 or len(body) < 30:
                logger.warning(
                    "inmail gen: short step %s (subj=%d, body=%d) — using fallback",
                    key, len(subject), len(body),
                )
                return fallback
            # LinkedIn caps: subject <=200, body <=1900. Cap below to be safe.
            subject = subject[:200].rstrip()
            if len(body) > 1900:
                body = body[:1900].rstrip()
            out[key] = {"subject": subject, "body": _append_cta(body, cta_url)}
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("inmail gen failed: %s", exc)
        return fallback


# ---------- Endpoints ---------------------------------------------------------


@router.post(
    "/generate/{lead_id}",
    response_model=LinkedInMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def generate_message(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Generate an outbound LinkedIn DM for a workspace-scoped lead.

    Per-lead dedup: if THIS lead already has an outbound draft we return
    it and DO NOT call the LLM again. We no longer reuse another lead's
    draft from the same company — every lead gets its own message.
    """
    nl = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id,
            NexusLead.workspace_id == workspace.id,
        )
        .first()
    )
    if not nl:
        raise HTTPException(status_code=404, detail="Lead not found")
    gl: Optional[NexusGlobalLead] = nl.global_lead
    if gl is None:
        gl = db.query(NexusGlobalLead).filter(NexusGlobalLead.id == nl.global_lead_id).first()
    if gl is None:
        raise HTTPException(status_code=404, detail="Global lead row missing")

    # Per-lead dedup. Scope to variant='dm' (or legacy NULL) so we
    # don't accidentally return an InMail row as the existing DM draft —
    # InMails have a populated subject and a much longer body, which the
    # DM workflow doesn't expect downstream. Legacy DM rows pre-date
    # the variant column and read as variant IS NULL.
    try:
        existing_for_lead = db.execute(
            text(
                """
                SELECT lim.id FROM nexus_linkedin_messages lim
                WHERE lim.workspace_id = :w
                  AND lim.direction = 'outbound'
                  AND (lim.variant IS NULL OR lim.variant = 'dm')
                  AND lim.lead_id = :lead
                ORDER BY lim.id DESC
                LIMIT 1
                """
            ),
            {"w": workspace.id, "lead": gl.id},
        ).first()
    except Exception:
        existing_for_lead = None
    if existing_for_lead:
        row = db.query(LinkedInMessage).filter(
            LinkedInMessage.id == int(existing_for_lead[0])
        ).first()
        if row:
            return row

    # Pull the SAME grounded context the email path / auto-sweep use:
    # 3-section product description + lead profile + real company background.
    # Best-effort — any miss falls back to the thin product line in the prompt.
    from nexus.services.sequencer import (
        _build_product_description,
        _li_company_background,
    )

    product_name = ""
    product_value_prop = ""
    product_description: dict = {}
    try:
        prow = db.execute(
            text(
                "SELECT name, value_proposition, icp, key_benefits, category, "
                "industry_relevance FROM nexus_products "
                "WHERE workspace_id = :w ORDER BY id LIMIT 1"
            ),
            {"w": workspace.id},
        ).mappings().first()
        if prow:
            product_name = prow.get("name") or ""
            product_value_prop = prow.get("value_proposition") or ""
            icp_obj = prow.get("icp") or {}
            if isinstance(icp_obj, str):
                import json as _json
                try:
                    icp_obj = _json.loads(icp_obj) or {}
                except Exception:
                    icp_obj = {}
            product_description = _build_product_description(
                name=product_name,
                category=prow.get("category") or "",
                value_proposition=product_value_prop,
                key_benefits=prow.get("key_benefits"),
                industry_relevance=prow.get("industry_relevance") or "",
                icp_obj=icp_obj,
            )
    except Exception:
        pass

    _location = ", ".join(
        x for x in (
            getattr(gl, "person_city", None),
            getattr(gl, "person_state", None),
            getattr(gl, "person_country", None),
        ) if x
    )
    _bg = _li_company_background(db, gl.company_domain)

    body = _generate_li_body(
        first_name=gl.first_name or "",
        role=gl.role or "",
        company=gl.company_name or gl.company_domain or "",
        product_name=product_name or "our product",
        product_value_prop=product_value_prop,
        location=_location,
        linkedin_headline=getattr(gl, "linkedin_headline", None) or "",
        product_description=product_description,
        company_summary=_bg["company_summary"],
        company_news=_bg["company_news"],
    )

    row = LinkedInMessage(
        workspace_id=workspace.id,
        lead_id=gl.id,
        direction="outbound",
        body=body,
        intent="generated",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# Note: the LinkedIn InMail variant is generated alongside the DM by the
# background sequencer (see services/sequencer.py — auto-runs for every
# newly-discovered lead), not by this HTTP endpoint. Keeping one
# generation path matches how email + DM are produced and avoids two
# different code paths producing the same artifact. The _generate_inmail
# helper below stays here so the sequencer can import it.


@router.post("/send/{message_id}", response_model=LinkedInMessageOut)
def mark_sent(
    message_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Record that the operator sent the DM externally. Stamps sent_at.

    There's no first-party LinkedIn outbound API for DMs, so the actual send
    happens manually (or via the operator's browser extension). This endpoint
    just closes the loop in our system.
    """
    row = (
        db.query(LinkedInMessage)
        .filter(
            LinkedInMessage.id == message_id,
            LinkedInMessage.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="LinkedIn message not found")
    if row.direction != "outbound":
        raise HTTPException(status_code=400, detail="Only outbound drafts can be marked sent")
    if not row.sent_at:
        row.sent_at = datetime.utcnow()
    row.intent = "sent"
    db.commit()
    db.refresh(row)
    return row


@router.get("/pending", response_model=List[LinkedInMessageOut])
def list_pending(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Outbound drafts not yet marked sent."""
    rows = (
        db.query(LinkedInMessage)
        .filter(
            LinkedInMessage.workspace_id == workspace.id,
            LinkedInMessage.direction == "outbound",
            LinkedInMessage.intent == "generated",
        )
        .order_by(LinkedInMessage.id.desc())
        .limit(min(limit, 200))
        .all()
    )
    return rows


@router.get("/lead/{lead_id}", response_model=List[LinkedInMessageOut])
def list_for_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Both directions for a single lead. Uses the global-lead id, matching
    the column in nexus_linkedin_messages.lead_id."""
    rows = (
        db.query(LinkedInMessage)
        .filter(
            LinkedInMessage.workspace_id == workspace.id,
            LinkedInMessage.lead_id == lead_id,
        )
        .order_by(LinkedInMessage.id.asc())
        .all()
    )
    return rows


# ---------- Per-lead flow state (drives the LinkedIn flow diagram) -------------


class LinkedInEventOut(BaseModel):
    event_type: str
    event_time: Optional[datetime] = None
    metadata: Optional[dict] = None


class LinkedInFlowOut(BaseModel):
    """Real per-lead position in the LinkedIn state machine, so the UI flow
    diagram can render the connection → note → inmail → acceptance → branch
    path with the correct step marked done and the correct branch shown."""
    enrolled: bool = False
    workflow_mode: Optional[str] = None          # standard|hybrid|combined
    connection_status: Optional[str] = None      # none|pending|accepted|declined|not_connectable
    connection_sent_at: Optional[datetime] = None
    connection_accepted_at: Optional[datetime] = None
    note_attached: bool = False
    inmail_status: Optional[str] = None          # none|sent|skipped_no_credit
    inmail_sent_at: Optional[datetime] = None
    message_sent_at: Optional[datetime] = None
    reply_status: Optional[str] = None           # none|replied
    last_reply_at: Optional[datetime] = None
    current_branch: Optional[str] = None         # pending_acceptance|accepted|inmail
    current_step: Optional[str] = None
    sequence_status: Optional[str] = None        # active|paused|stopped|completed|failed
    events: List[LinkedInEventOut] = []
    # ── Dynamic-flow fields ──────────────────────────────────────────────────
    # The remaining plan, so the Flow tab can render what will ACTUALLY happen
    # next for this lead rather than a fixed diagram every lead shares.
    plan: List[dict] = []
    reply_intent: Optional[str] = None           # classified intent of their last reply
    reply_confidence: Optional[float] = None
    reply_excerpt: Optional[str] = None          # first ~200 chars, for context
    stop_reason: Optional[str] = None            # why the sequence ended
    conversation_turns: int = 0                  # automated replies sent
    flagged_reason: Optional[str] = None         # why a human was asked to step in
    next_action_at: Optional[datetime] = None


@router.get("/lead/{lead_id}/flow", response_model=LinkedInFlowOut)
def lead_flow(
    lead_id: int,
    campaign_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Return the lead's LinkedIn flow state + event log. Read-only.

    `lead_id` is the global-lead id (gtm_linkedin_lead_state.lead_id). A lead
    can be enrolled per-campaign; without `campaign_id` we pick the most
    recently updated enrollment. Returns `enrolled=false` (all else null) when
    the lead has no LinkedIn state or the GTM tables aren't present."""
    try:
        params = {"ws": workspace.id, "lid": lead_id}
        camp_filter = ""
        if campaign_id is not None:
            camp_filter = " AND campaign_id = :cid"
            params["cid"] = campaign_id
        row = db.execute(
            text(
                f"""
                SELECT workflow_mode, linkedin_connection_status,
                       linkedin_connection_sent_at, linkedin_connection_accepted_at,
                       linkedin_inmail_status, linkedin_inmail_sent_at,
                       linkedin_message_sent_at, linkedin_message_reply_status,
                       last_reply_at, linkedin_current_branch, linkedin_current_step,
                       linkedin_sequence_status,
                       plan, conversation_turns, flagged_reason, next_action_at
                  FROM gtm_linkedin_lead_state
                 WHERE workspace_id = :ws AND lead_id = :lid{camp_filter}
                 ORDER BY (linkedin_sequence_status = 'active') DESC, updated_at DESC
                 LIMIT 1
                """
            ),
            params,
        ).first()
    except Exception:
        db.rollback()
        return LinkedInFlowOut(enrolled=False)

    if not row:
        return LinkedInFlowOut(enrolled=False)

    m = row._mapping
    conn_sent_at = m["linkedin_connection_sent_at"]
    mode = m["workflow_mode"] or "standard"

    # Check the actual URN to know if the note was delivered (not just assumed)
    note_actually_sent = False
    if conn_sent_at:
        try:
            urn_row = db.execute(
                text(
                    "SELECT linkedin_message_urn FROM nexus_linkedin_messages "
                    "WHERE workspace_id = :ws AND lead_id = :lid AND direction = 'outbound' "
                    "AND variant = 'dm' AND linkedin_message_urn IS NOT NULL "
                    "ORDER BY step, id LIMIT 1"
                ),
                {"ws": workspace.id, "lid": lead_id},
            ).first()
            if urn_row:
                urn_val = urn_row[0] or ""
                note_actually_sent = not urn_val.startswith("gtm-li-nonote")
        except Exception:
            db.rollback()

    events: List[LinkedInEventOut] = []
    try:
        ev_params = {"ws": workspace.id, "lid": lead_id}
        ev_camp = ""
        if campaign_id is not None:
            ev_camp = " AND campaign_id = :cid"
            ev_params["cid"] = campaign_id
        ev_rows = db.execute(
            text(
                f"""
                SELECT event_type, event_time, metadata
                  FROM gtm_linkedin_events
                 WHERE workspace_id = :ws AND lead_id = :lid{ev_camp}
                 ORDER BY event_time ASC
                 LIMIT 100
                """
            ),
            ev_params,
        ).fetchall()
        events = [
            LinkedInEventOut(
                event_type=e._mapping["event_type"],
                event_time=e._mapping["event_time"],
                metadata=e._mapping["metadata"],
            )
            for e in ev_rows
        ]
    except Exception:
        db.rollback()

    # Their last reply + how we read it. Drives the intent label under "Reply
    # Received" and the stop-reason copy on the terminal node.
    reply_intent = reply_conf = reply_excerpt = None
    try:
        r = db.execute(
            text(
                "SELECT intent, intent_confidence, body FROM nexus_linkedin_messages "
                " WHERE workspace_id = :ws AND lead_id = :lid AND direction = 'inbound' "
                " ORDER BY id DESC LIMIT 1"
            ),
            {"ws": workspace.id, "lid": lead_id},
        ).first()
        if r:
            reply_intent = r[0]
            reply_conf = float(r[1]) if r[1] is not None else None
            reply_excerpt = (r[2] or "")[:200] or None
    except Exception:
        db.rollback()

    # Why the sequence ended, from the immutable event log.
    stop_reason = None
    for e in reversed(events):
        if e.event_type == "sequence_stopped":
            stop_reason = (e.metadata or {}).get("reason")
            break

    # Only the touches still to come — a completed/stopped lead has none, which
    # is what lets the UI collapse the tail into one terminal node.
    raw_plan = m["plan"] if "plan" in m.keys() else None
    remaining_plan = [t for t in (raw_plan or []) if t.get("status") == "pending"]

    return LinkedInFlowOut(
        enrolled=True,
        workflow_mode=mode,
        connection_status=m["linkedin_connection_status"],
        connection_sent_at=conn_sent_at,
        connection_accepted_at=m["linkedin_connection_accepted_at"],
        note_attached=note_actually_sent,
        inmail_status=m["linkedin_inmail_status"],
        inmail_sent_at=m["linkedin_inmail_sent_at"],
        message_sent_at=m["linkedin_message_sent_at"],
        reply_status=m["linkedin_message_reply_status"],
        last_reply_at=m["last_reply_at"],
        current_branch=m["linkedin_current_branch"],
        current_step=m["linkedin_current_step"],
        sequence_status=m["linkedin_sequence_status"],
        plan=remaining_plan,
        reply_intent=reply_intent,
        reply_confidence=reply_conf,
        reply_excerpt=reply_excerpt,
        stop_reason=stop_reason,
        conversation_turns=int(m["conversation_turns"] or 0),
        flagged_reason=m["flagged_reason"],
        next_action_at=m["next_action_at"],
    )


__all__ = ["router"]
