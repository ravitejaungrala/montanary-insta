"""Reply post-processor — sync sequence state + fire dossier on replies.

Legacy: apps/nexus-legacy/server/services/replyPostProcessor.js
Exports (legacy): postProcess, syncLeadSequenceStatus, deferOutOfOffice,
                  fireDossier, looksLikeReferral, extractReferral,
                  enrollReferral
"""
from __future__ import annotations

import re
from typing import Any, Dict


async def sync_lead_sequence_status(
    lead: Dict[str, Any], intent: str
) -> Dict[str, Any]:
    """Legacy: syncLeadSequenceStatus({ lead, intent })."""
    return {"ok": False, "stub": True}


async def defer_out_of_office(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: deferOutOfOffice({ lead })."""
    return {"ok": False, "stub": True}


async def fire_dossier(
    lead: Dict[str, Any], campaign: Dict[str, Any], intent: str, reply_text: str
) -> Dict[str, Any]:
    """Legacy: fireDossier({ lead, campaign, intent, replyText })."""
    return {"ok": False, "stub": True}


def looks_like_referral(text: str) -> bool:
    """Cheap pre-filter deciding whether a reply is worth spending a Gemini
    call to check for a named alternate contact — used by
    sequencer.py::handle_referral_mentions (halting intents) and, via the
    same heuristic spirit, informs the referral_contacts extraction bundled
    into continuing-intent regeneration. Originally a direct port of the
    legacy JS (looksLikeReferral) with a narrow needle list; broadened
    2026-07 after a live simulation (Test_mail.md scenario 15) showed real
    referral phrasing — "already knows the context, go direct", "try Taylor
    on our team" — slipping past the original list entirely, silently
    skipping the extraction call rather than just returning no referrals.

    False positives here just cost one harmless extra Gemini call (the
    extraction prompt itself won't invent a referral that isn't there), so
    this errs broad — a missed real referral (false negative) is worse.
    """
    if not text:
        return False
    needles = (
        "forward", "contact our", "please reach", "speak to", "speak with",
        "is the right", "would be better", "talk to", "reach out to",
        "get in touch", "loop in", "loop him", "loop her", "cc'", "cc ",
        "copying", "go direct", "already knows", "on our team",
        "better positioned", "point of contact", "reach him", "reach her",
        "reach them", "try reaching", "you could try",
    )
    t = text.lower()
    return any(n in t for n in needles)


# ── Demo-intent heuristics (scenario 7) ──────────────────────────────────────
#
# Scenario 7 splits "wants a demo" into two replies that need different
# responses: one that already names a time (handled as DEMO_SCHEDULED — a real
# calendar invite is created), and one that just expresses demo interest with no
# time given (handled as continuing interest — the next follow-up proactively
# sends the booking link). These cheap regex gates decide, without an LLM call,
# whether a reply is the second kind so the follow-up regeneration can include
# the booking link.

_DEMO_REQUEST_RE = re.compile(
    r"\b(demo|walk[\s-]?through|walkthrough|see it in action|see a demo|"
    r"live demo|show me|see the product|product tour|book a (?:call|time|slot)|"
    r"quick call|hop on a call|see how it works)\b",
    re.I,
)

# Any sign the reply named a concrete time/day. If present, the reply almost
# certainly classified as DEMO_SCHEDULED (which owns time parsing + the invite),
# so the booking-link path deliberately stands down to avoid double-handling.
_TIME_MENTION_RE = re.compile(
    r"\b("
    r"mon(day)?|tue(s|sday)?|wed(nesday)?|thu(r|rs|rsday)?|fri(day)?|"
    r"sat(urday)?|sun(day)?|"                                   # weekday names
    r"tomorrow|today|tonight|"
    r"\d{1,2}\s?(?:am|pm)|\d{1,2}:\d{2}|"                        # 2pm, 14:30
    r"\d{1,2}(?:st|nd|rd|th)|"                                   # 5th
    r"o'?clock|noon|midday|"
    r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
    r"aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?"
    r")\b",
    re.I,
)


def looks_like_demo_request(text: str) -> bool:
    """True if a reply expresses interest in seeing a demo / getting on a call.
    Cheap pre-filter — a false positive just gates one extra check downstream."""
    return bool(text) and bool(_DEMO_REQUEST_RE.search(text))


def mentions_specific_time(text: str) -> bool:
    """True if the reply names a day/date/time. Used to keep the booking-link
    path from firing on a reply that already stated a time (that reply is the
    DEMO_SCHEDULED / real-invite case instead)."""
    return bool(text) and bool(_TIME_MENTION_RE.search(text))


def wants_demo_without_time(text: str) -> bool:
    """Scenario 7's "no time given" branch: the lead wants a demo but named no
    day/time, so the next follow-up should proactively offer the booking link."""
    return looks_like_demo_request(text) and not mentions_specific_time(text)


async def extract_referral(reply_text: str) -> Dict[str, Any]:
    """Legacy: extractReferral(replyText) -> { email, name, title } | None."""
    return {}


async def enroll_referral(
    referral: Dict[str, Any], lead: Dict[str, Any], campaign: Dict[str, Any]
) -> Dict[str, Any]:
    """Legacy: enrollReferral({ referral, lead, campaign })."""
    return {"ok": False, "stub": True}


async def post_process(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: postProcess(ctx). Top-level entry called from inbound flow."""
    return {"ok": False, "stub": True}
