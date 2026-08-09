"""GTM LinkedIn Agent — the single home for LinkedIn copy generation.

Five generators the sequence engine calls, all grounded on the lead + product +
enrichment context. The short text variants (note / message / follow-up / close)
reuse the established, banned-phrase-guarded primitive
`nexus.services.linkedin_service._generate_one`; the InMail variant (which needs
a subject + a longer body) is generated here via `gemini.chat_completion`.

Model: every call goes through `gemini` → `gemini.CHAT_MODEL`
(gemini-3.1-flash-lite), the single source of truth.

`lead`    : {first_name, last_name, title, company_name, linkedin_url, ...}
`product` : {name, value_prop, icp_pain, ...}
`enrichment` (optional): {recent_news, tech_stack, hiring_signals, summary, ...}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Mapping, Optional

from nexus.services import gemini
from nexus.services import linkedin_service as _li

logger = logging.getLogger("pipelyt.gtm_linkedin.content")

# Cadence step indices (mirror the email engine's _STEP_KIND for parity).
STEP_MESSAGE_1 = 0
STEP_FOLLOWUP_1 = 1
STEP_FOLLOWUP_2 = 2
STEP_CLOSE = 3

_INMAIL_MAX = 1200
_BANNED = getattr(_li, "_BANNED", re.compile(r"$^"))  # reuse the buzzword filter


def generate_connection_note(
    lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Optional[Mapping[str, Any]] = None
) -> str:
    """Short connection-request note (<280 chars, no greeting fluff)."""
    return _li._generate_one(lead, product, enrichment or {}, step=0, message_type="connect")


def generate_linkedin_message(
    lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Optional[Mapping[str, Any]] = None,
    step: int = STEP_MESSAGE_1,
) -> str:
    """First post-acceptance DM (<600 chars)."""
    return _li._generate_one(lead, product, enrichment or {}, step=step, message_type="dm")


def generate_followup_message(
    lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Optional[Mapping[str, Any]] = None,
    step: int = STEP_FOLLOWUP_1,
) -> str:
    """A follow-up DM in the post-acceptance branch (step 1 or 2)."""
    return _li._generate_one(lead, product, enrichment or {}, step=step, message_type="dm")


def generate_close_message(
    lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Optional[Mapping[str, Any]] = None,
) -> str:
    """The final 'break-up' / close DM (last step)."""
    return _li._generate_one(lead, product, enrichment or {}, step=STEP_CLOSE, message_type="dm")


# ── InMail (subject + longer body) — generated here ──────────────────────────
_INMAIL_SYSTEM = (
    "You write LinkedIn InMail messages for B2B sales. Output STRICT JSON: "
    '{"subject": str, "body": str}. The subject is 4-9 words, specific to the '
    "recipient's role or company. The body is 600-1200 characters, 2-4 short "
    "paragraphs, warm but professional: one concrete observation about them, a "
    "value tie-in to their role, a soft single-sentence CTA. Plain text only, no "
    "emojis, no hashtags, no made-up stats. NEVER use leverage, synergy, "
    "game-changer, disrupt, transform, innovative, cutting-edge, world-class, "
    "supercharge, 10x, unlock, empower."
)


def _inmail_user_prompt(lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Mapping[str, Any]) -> str:
    # Reuse the DM user-prompt builder for the grounded context, then ask for InMail JSON.
    base = _li._user_prompt(lead, product, enrichment or {}, step=0, message_type="dm")
    return base + "\n\nReturn an InMail as STRICT JSON {\"subject\", \"body\"} only."


# ── Conversation reply (in-thread) ───────────────────────────────────────────
# The only generator whose output answers a real person's real question, in our
# customer's name. Everything about it is tighter than the cadence generators:
# it may decline to answer, it may not invent facts, and it is length-capped to
# what a human actually types in a DM.
_REPLY_MAX = 500

_REPLY_SYSTEM = (
    "You are a B2B salesperson replying to a prospect's LinkedIn message. "
    "Output STRICT JSON: {\"can_answer\": bool, \"body\": str}.\n\n"
    "Set can_answer=false and leave body empty whenever answering would require "
    "a fact you were not given — pricing not in the context, a customer name, a "
    "statistic, an integration, a contractual or legal commitment, a specific "
    "date. Deferring to a colleague is ALWAYS acceptable; inventing is never.\n\n"
    "When you can answer: 1-3 short sentences, under 400 characters, plain text. "
    "Answer the actual question first. Sound like a person typing on their phone "
    "— no greeting boilerplate, no signature, no bullet points, no emojis, no "
    "hashtags, no marketing language. At most ONE question back, and only if it "
    "moves things forward. NEVER use leverage, synergy, game-changer, disrupt, "
    "transform, innovative, cutting-edge, world-class, supercharge, 10x, unlock, "
    "empower."
)


def generate_conversation_reply(
    lead: Mapping[str, Any], product: Mapping[str, Any],
    their_message: str, enrichment: Optional[Mapping[str, Any]] = None,
    prior_messages: Optional[list] = None,
) -> Optional[str]:
    """Reply to a prospect's LinkedIn message.

    Returns the body, or **None** meaning "do not send" — the model declined, the
    output was empty, it tripped the banned-phrase filter, or generation failed.

    None is a first-class, safe outcome, unlike the cadence generators which fall
    back to a template. There is no safe generic answer to a question we cannot
    ground: sending nothing and flagging a human beats sending something wrong
    under the customer's name.
    """
    if not (their_message or "").strip():
        return None

    ctx = _li._user_prompt(lead, product, enrichment or {}, step=0, message_type="dm")
    history = ""
    if prior_messages:
        history = "\n".join(f"{m.get('who', '?')}: {m.get('body', '')}" for m in prior_messages[-6:])
        history = f"\n\nCONVERSATION SO FAR:\n{history}"
    user = (
        f"{ctx}{history}\n\n"
        f"THEIR LATEST MESSAGE:\n{their_message.strip()[:2000]}\n\n"
        "Reply to it. Use ONLY facts present in the context above. If the answer "
        "is not there, set can_answer=false."
    )

    try:
        raw = gemini.chat_completion(
            system=_REPLY_SYSTEM, user=user, temperature=0.4, response_format_json=True,
        )
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not data.get("can_answer"):
            logger.info("gtm_linkedin: reply generator declined to answer (ungrounded) — handing off")
            return None
        body = (data.get("body") or "").strip()
        if not body:
            return None
        if _BANNED.search(body):
            logger.info("gtm_linkedin: conversation reply hit a banned phrase — not sending")
            return None
        if len(body) > _REPLY_MAX:
            body = body[:_REPLY_MAX].rsplit(" ", 1)[0].rstrip(",.;:-")
        return body
    except Exception:
        logger.exception("gtm_linkedin: conversation reply generation failed — not sending")
        return None


def generate_linkedin_inmail(
    lead: Mapping[str, Any], product: Mapping[str, Any], enrichment: Optional[Mapping[str, Any]] = None
) -> Dict[str, str]:
    """InMail for the pre-acceptance / not-connected branch. Returns
    {"subject", "body"}; falls back to a safe template on any failure."""
    first = (lead.get("first_name") or "there").strip()
    company = (lead.get("company_name") or "your team").strip()
    pname = (product.get("name") or "our platform").strip()
    fallback = {
        "subject": f"Quick thought for {company}",
        "body": (
            f"Hi {first},\n\nI came across your work at {company} and wanted to "
            f"reach out. We built {pname} to help teams like yours; if it's "
            "relevant, I'd value a quick 15-minute look. Either way, happy to "
            "share what we're seeing in your space.\n\nBest"
        ),
    }
    try:
        raw = gemini.chat_completion(
            system=_INMAIL_SYSTEM,
            user=_inmail_user_prompt(lead, product, enrichment or {}),
            temperature=0.5,
            response_format_json=True,
        )
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        subject = (data.get("subject") or fallback["subject"]).strip()
        body = (data.get("body") or fallback["body"]).strip()
        if _BANNED.search(subject) or _BANNED.search(body):
            logger.info("gtm_linkedin: inmail hit banned phrase — using fallback")
            return fallback
        if len(body) > _INMAIL_MAX:
            body = body[:_INMAIL_MAX].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
        return {"subject": subject[:120], "body": body}
    except Exception:
        logger.exception("gtm_linkedin: inmail generation failed — using fallback")
        return fallback
