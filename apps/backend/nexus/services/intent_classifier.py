"""
Intent classification for inbound replies.

Calls Google Gemini (`gemini-3.1-flash-lite`) with a strict JSON-only system
prompt. Returns one of the eight downstream intents that other phases
switch on.

Falls back to a deterministic regex heuristic when:
  - No API key is configured (local dev).
  - The remote call fails / times out.
  - The JSON parse fails.

This keeps the inbound webhook responsive (< 200ms p99 in heuristic mode)
even when LLM providers are degraded.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

import phonenumbers

from nexus.schemas_phase5 import IntentClassification
from nexus.services.gemini import CHAT_MODEL as GEMINI_MODEL  # single source of truth

logger = logging.getLogger("nexus.services.intent_classifier")

VALID_INTENTS = {
    "DEMO_SCHEDULED",
    "INTERESTED",
    "QUESTION",
    "NOT_NOW",
    "NOT_INTERESTED",
    "LEFT_COMPANY",
    "OUT_OF_OFFICE",
    "UNSUBSCRIBE",
}

SYSTEM_PROMPT = """You are an email-intent classifier for a B2B sales inbox.

Read the email body below and choose EXACTLY ONE of these intents:

- DEMO_SCHEDULED   : Recipient confirms a meeting/demo time or accepts a calendar invite.
- INTERESTED       : Positive interest, wants more info or to continue the conversation.
- QUESTION         : Any question — features, timeline, integrations, OR pricing / cost / plans.
- NOT_NOW          : Polite defer ("circle back next quarter", "ask me later").
- NOT_INTERESTED   : Explicit rejection of the offer, from someone still at the company.
- LEFT_COMPANY     : Sender says THEY no longer work here / have left / moved on / changed jobs / are not the right contact anymore because they left. This is about their own employment, not a rejection of the product — and it still applies even if they also name a new company or a replacement contact.
- OUT_OF_OFFICE    : Auto-reply / vacation / OOO bounce.
- UNSUBSCRIBE      : Explicit opt-out, "stop emailing me", "remove me from your list".

Disambiguation: if the sender has LEFT the company, choose LEFT_COMPANY even
when they also decline — prefer it over NOT_INTERESTED. NOT_INTERESTED is only
for someone who still works there but isn't interested.

Return STRICT JSON ONLY — no prose, no markdown, no code fences. Shape:
{"intent": "<one of the above>", "confidence": <0.0-1.0 float>, "reasoning": "<one short sentence>"}
"""


# ---------- Heuristic fallback ------------------------------------------------

_HEURISTIC_RULES = [
    ("UNSUBSCRIBE", re.compile(r"\b(unsubscribe|opt[\s-]?out|remove me|stop emailing|do not contact)\b", re.I)),
    ("OUT_OF_OFFICE", re.compile(r"\b(out of (the )?office|on vacation|away from my desk|auto[\s-]?reply|annual leave)\b", re.I)),
    ("DEMO_SCHEDULED", re.compile(r"\b(calendar invite|booked|scheduled|see you (at|on)|works for me|confirm(ed|ing)? (the |our )?(meeting|demo|call))\b", re.I)),
    # Departure BEFORE rejection: "I've left / no longer with the company" is a
    # LEFT_COMPANY signal, not a NOT_INTERESTED one, even if the sender also
    # declines. Needles are deliberately departure-specific ("moved on FROM",
    # not a bare "moved on") to avoid catching "let's move on to pricing".
    ("LEFT_COMPANY", re.compile(
        r"\b(no longer (with|at|work)|left the (company|business|firm)|moved on from|"
        r"i'?ve left|i have left|don'?t work (here|there)|(work|working) (here|there) any ?longer|"
        r"not (with|at) .{0,30}? any ?more|changed (jobs|companies|roles)|"
        r"(no longer|won'?t be) the right (person|contact))\b",
        re.I)),
    ("NOT_INTERESTED", re.compile(r"\b(not interested|no thanks|no thank you|pass on this|not a fit)\b", re.I)),
    ("NOT_NOW", re.compile(r"\b(not (right )?now|next (quarter|year|month)|circle back|revisit|maybe later)\b", re.I)),
    ("QUESTION", re.compile(r"\?|\b(pric(e|ing)|cost|how much|quote|plan|tier|budget)\b", re.I)),
    ("INTERESTED", re.compile(r"\b(interested|tell me more|sounds good|love to|happy to|let'?s chat)\b", re.I)),
]


def _heuristic(text_body: str) -> IntentClassification:
    body = text_body or ""
    for intent, pattern in _HEURISTIC_RULES:
        if pattern.search(body):
            return IntentClassification(
                intent=intent,  # type: ignore[arg-type]
                confidence=0.55,
                reasoning=f"heuristic match: {pattern.pattern}",
            )
    return IntentClassification(intent="QUESTION", confidence=0.3, reasoning="no rule matched")


# ---------- LLM call ----------------------------------------------------------

LINKEDIN_SYSTEM_PROMPT = """You are an intent classifier for B2B LinkedIn direct messages.

LinkedIn DMs are SHORT — often three words, sometimes one. Judge the intent from
what little is there; do not require an email-length message.

Choose EXACTLY ONE intent:

- DEMO_SCHEDULED   : Confirms a specific meeting/call time ("Tues 2pm works", "sent you an invite").
- INTERESTED       : Positive or curious — wants to hear more. Includes bare "sure", "sounds good",
                     "ok send it over", "yes please", "how does it work?" when the tone is receptive.
- QUESTION         : Asks something — features, price, integrations — OR asks who we are
                     ("who is this?", "do we know each other?", "what is this about?").
                     Identity questions are the single most common LinkedIn reply; classify them here.
- NOT_NOW          : Defers ("not right now", "maybe next quarter", "ping me later", "busy at the moment").
- NOT_INTERESTED   : Declines, from someone still at the company ("no thanks", "not for us", "we're all set").
- LEFT_COMPANY     : Says THEY no longer work there / have moved on / are not the right contact
                     because they left. About their own employment, not a rejection of the offer.
- OUT_OF_OFFICE    : Away / travelling / on leave and will respond later. RARE on LinkedIn — do not
                     reach for it unless absence is stated explicitly.
- UNSUBSCRIBE      : Asks us to stop ("stop messaging me", "remove me", "don't contact me again",
                     "please stop"). Any explicit request to stop belongs here, NOT in NOT_INTERESTED.

Guidance specific to this channel:
- A bare acknowledgement ("ok", "thanks", "👍") is NOT interest. Prefer NOT_NOW with LOW confidence —
  it is a polite close, and treating it as interest produces a pushy follow-up.
- Annoyance or profanity directed at being messaged is UNSUBSCRIBE, not NOT_INTERESTED.
- If the sender has LEFT the company, choose LEFT_COMPANY even when they also decline.
- If genuinely ambiguous, return your best guess with confidence BELOW 0.5. Low confidence is
  handled safely downstream; a confident wrong answer is not.

Return STRICT JSON ONLY — no prose, no markdown, no code fences. Shape:
{"intent": "<one of the above>", "confidence": <0.0-1.0 float>, "reasoning": "<one short sentence>"}
"""

# LinkedIn-shaped fallback rules, ordered most-decisive first. Distinct from the
# email set: no auto-reply/bounce patterns, identity questions ("who is this?")
# are first-class, and terse acknowledgements are caught explicitly.
_HEURISTIC_RULES_LINKEDIN = [
    ("UNSUBSCRIBE", re.compile(
        r"\b(stop (messaging|contacting|reaching)|remove me|don'?t (contact|message|reach)|"
        r"leave me alone|unsubscribe|opt[\s-]?out|not interested in (being |getting )?(spam|messages))\b", re.I)),
    ("LEFT_COMPANY", re.compile(
        r"\b(no longer (with|at|work)|left (the )?(company|firm)|i'?ve left|i have left|"
        r"don'?t work (here|there)|moved on from|changed (jobs|companies|roles)|"
        r"(no longer|not) the right (person|contact))\b", re.I)),
    ("DEMO_SCHEDULED", re.compile(
        r"\b(works for me|see you (at|on)|booked|sent you (an |the )?invite|"
        r"calendar invite|confirm(ed|ing)? (the |our )?(call|meeting|demo))\b", re.I)),
    ("OUT_OF_OFFICE", re.compile(
        r"\b(out of (the )?office|on (vacation|leave|holiday)|annual leave|"
        r"travel(l)?ing (until|till|this)|back (on|after) \w+)\b", re.I)),
    ("NOT_INTERESTED", re.compile(
        r"\b(not interested|no thanks|no thank you|not a fit|not for us|we'?re (all )?set|pass)\b", re.I)),
    ("NOT_NOW", re.compile(
        r"\b(not (right )?now|next (quarter|year|month)|circle back|later|"
        r"busy (at the moment|right now)|check back)\b", re.I)),
    # Identity questions before the generic '?' rule so they can't be swallowed
    # by INTERESTED — "who are you?" is a question, not enthusiasm.
    ("QUESTION", re.compile(
        r"\b(who (is this|are you|r u)|do (we|i) know (you|each other)|"
        r"what.{0,12}(is )?this (about|regarding)|how did you (get|find))\b", re.I)),
    ("INTERESTED", re.compile(
        r"\b(interested|tell me more|sounds good|sounds interesting|send (it|me|over)|"
        r"happy to|let'?s (chat|talk)|sure|yes please|go ahead)\b", re.I)),
    ("QUESTION", re.compile(r"\?|\b(pric(e|ing)|cost|how much|quote|plan|tier|budget)\b", re.I)),
]

# A bare acknowledgement is a polite close, not interest. Matched on the WHOLE
# message so "ok" alone is caught but "ok, tell me more" falls through.
_LI_ACK_ONLY = re.compile(r"^[\s\W]*(ok(ay)?|thanks|thank you|ty|got it|noted|cool|sure thing)[\s\W]*$", re.I)


def _heuristic_linkedin(text_body: str) -> IntentClassification:
    body = (text_body or "").strip()
    if _LI_ACK_ONLY.match(body):
        return IntentClassification(
            intent="NOT_NOW", confidence=0.35,
            reasoning="bare acknowledgement — polite close, not interest",
        )
    for intent, pattern in _HEURISTIC_RULES_LINKEDIN:
        if pattern.search(body):
            return IntentClassification(
                intent=intent, confidence=0.55,  # type: ignore[arg-type]
                reasoning=f"linkedin heuristic: {pattern.pattern[:60]}",
            )
    # Unmatched short DMs are genuinely ambiguous. Below the acting threshold on
    # purpose, so the engine stops rather than guessing at a stranger's meaning.
    return IntentClassification(intent="QUESTION", confidence=0.2, reasoning="no linkedin rule matched")


def _llm_call(text_body: str, system_prompt: Optional[str] = None) -> Optional[IntentClassification]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:
        logger.warning("google-genai SDK not available: %s", exc)
        return None

    try:
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or SYSTEM_PROMPT,
            temperature=0.0,
            max_output_tokens=200,
            response_mime_type="application/json",
        )
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=(text_body or "")[:6000],
            config=config,
        )
        content = (resp.text or "").strip()
        parsed = json.loads(content)
        intent = parsed.get("intent")
        if intent not in VALID_INTENTS:
            logger.warning("LLM returned invalid intent %r — falling back", intent)
            return None
        return IntentClassification(
            intent=intent,  # type: ignore[arg-type]
            confidence=float(parsed.get("confidence", 0.7)),
            reasoning=parsed.get("reasoning"),
        )
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("intent LLM parse failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("intent LLM call failed: %s", exc)
        return None


# ---------- Public API --------------------------------------------------------

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Matches a stated OOO return date via a cue word followed (within a short gap)
# by a month + day. Cues cover both "returning/back/available <date>" AND the
# END of an out-of-office RANGE — "out ... through July 31", "until Aug 3",
# "from Jul 22 to Jul 31" — which is when they're back. Deliberately narrow: a
# fast-path override for an explicitly stated date, not a general date parser.
_RETURN_DATE_RE = re.compile(
    r"\b(?:returning|return(?:ing)?\s+to\s+(?:the\s+)?(?:office|desk)|back|available|"
    r"through|thru|until|untill|till|til|to)\b"
    r"[^.\n]{0,40}?\b(" + "|".join(_MONTHS.keys()) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)


def extract_ooo_return_date(text_body: str, received_at: Optional[datetime] = None) -> Optional[date]:
    """Best-effort, deterministic extraction of a stated OOO return date.

    No LLM call — this is a fast-path used only when intent is
    OUT_OF_OFFICE, so reply-aware follow-up regeneration (sequencer.py)
    can reschedule the next send to roughly when the lead is actually
    back, instead of trusting a model's date arithmetic. Returns None
    when nothing confidently matches (the caller falls back to the
    relative-duration parser, then the model-recommended delay).

    Recognises "back on July 9" AND the END of an OOO range ("out from July 22
    through July 31" -> July 31). When several dates are cued, the LATEST wins —
    that's when they're fully back, so we never send before they return.

    `received_at` anchors the year: OOO messages are near-term, so a
    parsed month/day more than ~60 days in the PAST relative to
    received_at is assumed to mean next year (handles a Dec -> Jan
    wraparound OOO reply).
    """
    if not text_body:
        return None
    anchor = received_at or datetime.utcnow()
    if getattr(anchor, "tzinfo", None) is not None:
        anchor = anchor.replace(tzinfo=None)

    def _resolve(month: int, day: int) -> Optional[date]:
        if not (1 <= day <= 31):
            return None
        try:
            cand = date(anchor.year, month, day)
        except ValueError:
            return None
        if (anchor.date() - cand) > timedelta(days=60):
            try:
                cand = date(anchor.year + 1, month, day)
            except ValueError:
                return None
        return cand

    candidates = []
    for m in _RETURN_DATE_RE.finditer(text_body):
        month = _MONTHS.get(m.group(1).lower())
        if month is None:
            continue
        try:
            day = int(m.group(2))
        except ValueError:
            continue
        c = _resolve(month, day)
        if c is not None:
            candidates.append(c)
    return max(candidates) if candidates else None


# Relative OOO duration → days. Quantity words (a/one/…/twelve, couple, few,
# several, half) and units (day/week/fortnight/month). Deterministic + tiny, on
# purpose — same spirit as _RETURN_DATE_RE: a fast fallback, not a general NLP
# duration parser.
_DURATION_QTY = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "couple": 2, "few": 3, "several": 3, "half": 0.5,
}
_DURATION_UNIT_DAYS = {
    "day": 1, "days": 1, "week": 7, "weeks": 7, "wk": 7, "wks": 7,
    "fortnight": 14, "fortnights": 14, "month": 30, "months": 30,
}
# <qty> <space> (filler words) <unit>. Fillers ("of a/the", "about/around/
# roughly") let "half a month" and "a couple of weeks" match while the qty +
# unit stay the only two captured groups. A space between qty and unit is
# required: it's how real replies are written ("2 weeks", "2 wks"), and it
# keeps a glued word like "aweek" from being read as "a week".
_DURATION_RE = re.compile(
    r"\b(\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|couple|few|several|half)\b"
    r"(?:\s+(?:of|a|an|the|about|around|approximately|approx\.?|roughly))*"
    r"\s+(days?|wks?|weeks?|fortnights?|months?)\b",
    re.I,
)
_OOO_MAX_DELAY_DAYS = 120


def extract_ooo_return_delay_days(text_body: str) -> Optional[int]:
    """Best-effort deterministic estimate of a RELATIVE OOO duration, in days.

    For vague return windows that name no calendar date — "back in a week",
    "out for 10 days", "away half the month", "back in a month" — so the next
    send can still shift to land after they're likely back. This is deliberately
    the FALLBACK to extract_ooo_return_date: an explicit month-and-day always
    wins (it's exact), and this only runs when none is stated.

    Rough by design: "a week" -> 7, "10 days" -> 10, "half a month" -> 15,
    "a month" -> 30, "a couple of weeks" -> 14, "a fortnight" -> 14. Clamped to
    [1, 120] days so a typo or odd phrasing can't push a follow-up absurdly far
    out. Returns None when no duration is stated.
    """
    if not text_body:
        return None
    m = _DURATION_RE.search(text_body)
    if not m:
        return None
    qty_raw, unit_raw = m.group(1).lower(), m.group(2).lower()
    qty = int(qty_raw) if qty_raw.isdigit() else _DURATION_QTY.get(qty_raw)
    unit_days = _DURATION_UNIT_DAYS.get(unit_raw)
    if qty is None or unit_days is None:
        return None
    days = int(round(qty * unit_days))
    if days < 1:
        return None
    return min(days, _OOO_MAX_DELAY_DAYS)


# Regions tried, IN ORDER, when a candidate number has no explicit country
# code ("+..."). A number WITH a country code parses correctly under ANY of
# these, so this list ONLY affects bare local-format numbers (e.g.
# "0421 557 958" is valid under AU's plan but not a plain 9-digit run). For an
# ambiguous local number the FIRST region whose real dialing-plan rules accept
# it wins — hence the order: primary English-speaking / target markets first,
# then Western & Northern Europe, then other major economies. Where a lead
# genuinely lives is far more likely to be near the top.
#
# Deliberately a curated ~40, NOT all 200 libphonenumber regions: every extra
# region raises the chance a formatted non-phone digit run (or a local number
# from one country) coincidentally validates against an unrelated country's
# plan — a confidently-wrong capture, worse than missing a rare local number.
# This list widens coverage to all common markets while keeping that risk
# bounded. Numbers from anywhere still work when written in +CC form.
_PHONE_CANDIDATE_REGIONS = [
    # Primary English-speaking / core markets
    "US", "CA", "GB", "AU", "IN", "NZ", "IE", "SG", "ZA", "PH", "NG",
    # Western, Northern & Southern Europe (+ EFTA)
    "DE", "FR", "NL", "ES", "IT", "BE", "SE", "CH", "AT", "DK", "NO",
    "FI", "PT", "PL", "CZ", "GR", "RO", "HU",
    # Other major economies (Asia-Pacific, Middle East, Latin America)
    "JP", "KR", "CN", "HK", "TW", "AE", "SA", "IL", "TR",
    "BR", "MX", "AR", "ID", "MY", "TH", "VN",
]

# A calendar date ("2026-07-23", "23/07/2026") can have the exact digit count +
# separators of a valid phone number under some country's plan, so libphonenumber
# will happily "validate" it — a false positive that gets worse as the candidate
# region list grows. This guard rejects a match that is unambiguously a date: a
# 19xx/20xx year at the start OR end, a valid month (1-12) and day (1-31), and
# the SAME separator throughout (\1/\2 backrefs). High precision — a real phone
# never carries a 19xx/20xx year in month/day structure, so "402.873.2318" and
# "0421 557 958" are untouched.
_DATE_LIKE_RE = re.compile(
    r"^\s*(?:"
    r"(?:19|20)\d{2}([-/.])(?:0?[1-9]|1[0-2])\1(?:0?[1-9]|[12]\d|3[01])"   # YYYY-MM-DD
    r"|"
    r"(?:0?[1-9]|[12]\d|3[01])([-/.])(?:0?[1-9]|1[0-2])\2(?:19|20)\d{2}"   # DD-MM-YYYY
    r"|"
    r"(?:19|20)\d{2}\s*[-–/]\s*(?:19|20)\d{2}"                        # YYYY-YYYY range
    r")\s*$"
)

# Structured US IDs that are 9-digit dash tokens a wide region list will accept
# as a phone: a ZIP+4 postal code ("90210-1234", 5-4) and an EIN ("12-3456789",
# 2-7). Both appear in business text/signatures (an address, a footer) but a
# genuine phone is essentially never grouped exactly 5-4 or 2-7 as a bare token,
# so rejecting these precise shapes is safe. (SSN 3-2-4 is already rejected by
# libphonenumber's own validity check, so it needs no guard here.)
_ID_LIKE_RE = re.compile(r"^\s*(?:\d{5}-\d{4}|\d{2}-\d{7})\s*$")

# A bare, UNFORMATTED digit run ("9459475257") is normally rejected — on its own
# it's indistinguishable from an invoice/order/ID number. But when it directly
# follows a phone cue ("reach me on 9459475257", "mobile 9811120034") it's
# almost certainly a real number, so we accept it in that context only. Cues are
# matched in a short window immediately BEFORE the number.
_PHONE_CONTEXT_RE = re.compile(
    r"\b(?:reach|call|phone|mobile|mob|cell|tel|telephone|contact|dial|whats?app|"
    r"ring|number|line|text)\b",
    re.I,
)

# nexus_global_leads.person_country stores country NAMES; libphonenumber needs
# an ISO-2 region code. Small curated map (the countries our leads come from);
# an unrecognised name just falls through to region-priority order.
_COUNTRY_NAME_TO_ISO = {
    "india": "IN", "australia": "AU",
    "united states": "US", "united states of america": "US", "usa": "US",
    "u.s.": "US", "u.s.a.": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB",
    "canada": "CA", "new zealand": "NZ", "ireland": "IE", "singapore": "SG",
    "south africa": "ZA", "philippines": "PH", "nigeria": "NG",
    "germany": "DE", "france": "FR", "netherlands": "NL", "the netherlands": "NL",
    "spain": "ES", "italy": "IT", "belgium": "BE", "sweden": "SE",
    "switzerland": "CH", "austria": "AT", "denmark": "DK", "norway": "NO",
    "finland": "FI", "portugal": "PT", "poland": "PL", "japan": "JP",
    "china": "CN", "hong kong": "HK", "united arab emirates": "AE", "uae": "AE",
    "saudi arabia": "SA", "israel": "IL", "brazil": "BR", "mexico": "MX",
    "malaysia": "MY", "thailand": "TH", "indonesia": "ID", "vietnam": "VN",
    "south korea": "KR", "korea": "KR", "turkey": "TR", "argentina": "AR",
    "taiwan": "TW", "greece": "GR", "romania": "RO", "hungary": "HU",
    "czech republic": "CZ", "czechia": "CZ",
}


def country_to_iso(name: Optional[str]) -> Optional[str]:
    """Country NAME ("India") -> ISO-2 region ("IN"), or None if unrecognised."""
    if not name:
        return None
    return _COUNTRY_NAME_TO_ISO.get(name.strip().lower())


# ── Inline country cues (Phase 1: per-number label reader) ───────────────────
# A reply often labels each number with its country — "IN: 96111 30094",
# "AU: 0421 557 256", "my Sydney mobile 0421…". Reading that cue lets us give
# EACH number the right +CC even when a reply mixes countries and the lead's
# person_country only matches one of them. The cue is only ever a *preferred
# region*: the number must still strictly validate under it, so a wrong label
# can never force an invalid/misattributed number.

# Uppercase 2-3 letter codes accepted ONLY as an explicit label (followed by
# ':' / '-'), so the English words "in", "it", "us", "no" can't false-match.
_ISO_LABEL_MAP = {
    "AU": "AU", "IN": "IN", "US": "US", "USA": "US", "UK": "GB", "GB": "GB",
    "CA": "CA", "NZ": "NZ", "IE": "IE", "SG": "SG", "ZA": "ZA", "NG": "NG",
    "PH": "PH", "DE": "DE", "FR": "FR", "NL": "NL", "ES": "ES", "IT": "IT",
    "JP": "JP", "CN": "CN", "HK": "HK", "AE": "AE", "UAE": "AE", "SA": "SA",
    "BR": "BR", "MX": "MX", "MY": "MY", "TH": "TH", "ID": "ID", "KR": "KR",
}
_ISO_LABEL_RE = re.compile(r"\b([A-Z]{2,3})\s*[:\-]\s*$")

# An ISO label immediately followed by a stray "+" before a national number
# ("IN: + 98111 20034"). The "+" makes libphonenumber read the leading digits
# as a country code (IN -> +98 Iran, AU -> +49 Germany), which either drops the
# number (invalid) or mis-attributes it. `_strip_spurious_label_plus` removes
# just that "+" so the national digits parse under the LABELLED country — but
# only when the "+" is genuinely spurious and the number stays FORMATTED (still
# has a separator) so the matcher can isolate it. An unformatted token keeps its
# "+" and is corrected later by to_international's label override.
_LABEL_PLUS_RE = re.compile(r"\b([A-Z]{2,3})(\s*[:\-]\s*)\+(\s*)(\d[\d\s().\-]*\d)")


def _strip_spurious_label_plus(m: "re.Match") -> str:
    code = m.group(1).upper()
    region = _ISO_LABEL_MAP.get(code)
    if not region:
        return m.group(0)
    numtok = m.group(4)
    digits = re.sub(r"\D", "", numtok)
    try:
        n = phonenumbers.parse("+" + digits, None)
        if phonenumbers.is_valid_number(n) and phonenumbers.region_code_for_number(n) == region:
            return m.group(0)  # legit "+CC" that matches the label — keep it
    except Exception:  # noqa: BLE001
        pass
    # Only strip if the token stays formatted (matcher can still isolate it) AND
    # the national digits are actually valid under the labelled country.
    if not re.search(r"[ \-.()]", numtok.strip()):
        return m.group(0)
    try:
        n2 = phonenumbers.parse(digits, region)
        if phonenumbers.is_valid_number(n2):
            return m.group(1) + m.group(2) + m.group(4)  # drop the stray "+"
    except Exception:  # noqa: BLE001
        pass
    return m.group(0)


# Demonyms + major cities → region. Country NAMES come from _COUNTRY_NAME_TO_ISO.
_DEMONYM_CITY_TO_ISO = {
    # demonyms
    "indian": "IN", "australian": "AU", "aussie": "AU", "american": "US",
    "british": "GB", "canadian": "CA", "kiwi": "NZ", "irish": "IE",
    "singaporean": "SG", "german": "DE", "french": "FR", "dutch": "NL",
    "spanish": "ES", "italian": "IT", "japanese": "JP", "emirati": "AE",
    # cities (unambiguous, common in signatures)
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "perth": "AU",
    "london": "GB", "manchester": "GB", "mumbai": "IN", "bengaluru": "IN",
    "bangalore": "IN", "hyderabad": "IN", "delhi": "IN", "chennai": "IN",
    "pune": "IN", "kolkata": "IN", "toronto": "CA", "vancouver": "CA",
    "auckland": "NZ", "wellington": "NZ", "dublin": "IE", "berlin": "DE",
    "munich": "DE", "madrid": "ES", "barcelona": "ES", "dubai": "AE",
    "abu dhabi": "AE",
}
# One alternation over all geo WORDS (country names + demonyms + cities),
# longest-first so "new zealand" / "abu dhabi" match before any shorter token.
_GEO_WORD_TO_ISO = {**_COUNTRY_NAME_TO_ISO, **_DEMONYM_CITY_TO_ISO}
_GEO_WORD_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(w) for w in sorted(_GEO_WORD_TO_ISO, key=len, reverse=True)
    ) + r")\b",
    re.I,
)


def _region_hint(text: str, start: int, end: int) -> Optional[str]:
    """The country a number is LABELLED with in the surrounding text, as an
    ISO-2 region, or None. Checks (a) an uppercase ISO label right before the
    number ("AU: 0421…"), then (b) any country name / demonym / city in a short
    window around it ("my Sydney line 0421…"). Returns a *hint* only — the
    caller still validates the number under it."""
    before = text[max(0, start - 30):start]
    m = _ISO_LABEL_RE.search(before)
    if m and m.group(1).upper() in _ISO_LABEL_MAP:
        return _ISO_LABEL_MAP[m.group(1).upper()]
    window = before + " " + text[end:end + 20]
    gm = _GEO_WORD_RE.search(window)
    if gm:
        return _GEO_WORD_TO_ISO.get(gm.group(1).lower())
    return None


def to_international(
    raw: str, *preferred_regions: Optional[str], label_region: Optional[str] = None
) -> str:
    """Return `raw` in readable +CC INTERNATIONAL form ("+91 98111 20034") when
    the country can be determined CONFIDENTLY; otherwise return `raw` unchanged.
    Never emits a WRONG country code — an ambiguous bare local number is left
    exactly as written (country_code_number.md).

    `label_region` is the region from an EXPLICIT inline label next to the number
    ("AU: 0421 557 256" -> AU). It's the strongest signal and is authoritative:
    it beats even a leading "+" when that "+" yields a DIFFERENT country or an
    invalid number (the sender typed a stray "+" before a national number, e.g.
    "AU: + 492936446" which "+" reads as +49 Germany). A labelled number is
    resolved by VALIDITY under the label (not strict grouping), so an unformatted
    "AU: 492936446" still lands as +61.

    Confidence tiers, in order:
      1. leading "+" -> its own country code, UNLESS an explicit `label_region`
         disagrees with (or the "+" is invalid) — then the label wins.
      2. explicit `label_region` -> the number if it's VALID under that country.
      3. bare local + a `preferred_regions` (lead country) that STRICTLY
         validates -> anchored.
      4. bare local valid under EXACTLY ONE candidate region -> that one.
      5. otherwise (ambiguous across regions, or unparseable) -> keep `raw`.

    Idempotent: an already-international value re-normalizes to itself.
    """
    if not raw or not raw.strip():
        return raw
    _INTL = phonenumbers.PhoneNumberFormat.INTERNATIONAL
    _E164 = phonenumbers.PhoneNumberFormat.E164

    def _valid_under(digits, region):
        # Parse `digits` as a NATIONAL number in `region`; return its +CC form
        # only if it's a genuinely valid number there. Used for an explicit
        # label, where the country is already known so grouping needn't confirm.
        try:
            n = phonenumbers.parse(digits, region)
            if phonenumbers.is_valid_number(n):
                return phonenumbers.format_number(n, _INTL)
        except Exception:  # noqa: BLE001
            pass
        return None

    stripped = raw.strip()

    # 1. Explicit country code (leading "+"). Normally exact — but an explicit
    #    label that names a DIFFERENT country, or a "+" whose code doesn't even
    #    form a valid number, means the "+" is a stray the sender put before a
    #    national number ("AU: + 492936446"). Honour the label in that case.
    if stripped.startswith("+"):
        plus_intl = plus_region = None
        try:
            n = phonenumbers.parse(stripped, None)
            if phonenumbers.is_valid_number(n):
                plus_intl = phonenumbers.format_number(n, _INTL)
                plus_region = phonenumbers.region_code_for_number(n)
        except Exception:  # noqa: BLE001
            pass
        if label_region and (plus_intl is None or plus_region != label_region):
            lab = _valid_under(re.sub(r"\D", "", stripped), label_region)
            if lab:
                return lab
        return plus_intl or raw

    # 2. Explicit inline label — authoritative. Resolve by validity so an
    #    unformatted labelled number ("AU: 492936446") the strict matcher would
    #    reject still lands under its stated country.
    if label_region:
        lab = _valid_under(stripped, label_region)
        if lab:
            return lab

    # The STRICT_GROUPING matcher (same as extraction / _phone_dedup_key) is the
    # confidence signal: it accepts a local number only under a region whose real
    # grouping matches how it's written. "0421 557 958" strictly matches AU alone;
    # a bare unformatted "9459475257" matches US AND IN (ambiguous). Lenient
    # parse() would wrongly call the first "valid" everywhere.
    def _strict_intl(region):
        for m in phonenumbers.PhoneNumberMatcher(
            raw, region, leniency=phonenumbers.Leniency.STRICT_GROUPING
        ):
            try:
                return phonenumbers.format_number(m.number, _INTL)
            except Exception:  # noqa: BLE001
                return None
        return None

    # 3. Anchor on each preferred region (the lead's own country) — the first
    #    that STRICTLY validates the number.
    for region in preferred_regions:
        if region:
            anchored = _strict_intl(region)
            if anchored:
                return anchored
    # An explicit label was given but the number didn't validate under it (nor
    # the anchor): the sender named a country and it didn't hold, so DON'T guess
    # some other country below — keep raw (never guess wrong).
    if label_region:
        return raw

    # 4. Normalize only if the strict matcher resolves it to exactly one country.
    e164s = set()
    first_intl = None
    for region in _PHONE_CANDIDATE_REGIONS:
        for m in phonenumbers.PhoneNumberMatcher(
            raw, region, leniency=phonenumbers.Leniency.STRICT_GROUPING
        ):
            try:
                e = phonenumbers.format_number(m.number, _E164)
            except Exception:  # noqa: BLE001
                continue
            if not e164s:
                first_intl = phonenumbers.format_number(m.number, _INTL)
            e164s.add(e)
    if len(e164s) == 1:
        return first_intl
    # 5. Ambiguous or unparseable — leave it as the sender wrote it.
    return raw


def extract_phone_number(text_body: str, default_region: Optional[str] = None) -> Optional[str]:
    """Best-effort extraction of a real phone number from a reply's
    signature block, using Google's libphonenumber (the `phonenumbers`
    package) instead of hand-rolled regex.

    Regex can only ever cover the digit-grouping conventions someone
    thought to test for — the first version of this function used a
    North-American-only 3-3-4 pattern and silently missed Australia's
    4-3-3 mobile format entirely. libphonenumber instead understands each
    country's actual numbering-plan rules (valid area codes, length,
    mobile vs. landline prefixes, ...), so it both catches far more real
    formats AND rejects things that only *look* like a phone number
    (dates, ZIP+4s, order numbers) via genuine validity checking rather
    than a hand-tuned separator heuristic.

    Matched at STRICT_GROUPING leniency (the candidate's separators, where
    present, must fall exactly where that region's real numbering plan
    puts them — not just be "roughly the right length"). That alone still
    isn't enough: an unbroken 10-digit run is itself one *valid* way to
    write a NANP number with no separators at all, so something like
    "Invoice #4028732318" (which really is a valid area code + exchange)
    passes validity/grouping checks despite not being a phone number.
    Genuine signatures are always formatted — a lone, unbroken digit run
    elsewhere in an email essentially never is — so an extracted match is
    additionally required to contain real formatting (a space, dash, dot,
    parens, or a leading "+") before being trusted.

    No LLM call — this needs to run on every inbound reply, not just a
    specific intent, and a real number is either valid or it isn't; there
    is no judgment call for a model to make here.

    Backward-compatible thin wrapper over `extract_phone_numbers` — returns
    the FIRST number in reading order, or None. Callers that want every
    number in a reply (a signature can carry an office AND a mobile, or an
    Indian and an Australian line) should use `extract_phone_numbers`.

    `default_region` (ISO-2, e.g. the lead's country) anchors bare local
    numbers so they normalize to the right +CC form — see extract_phone_numbers.
    """
    numbers = extract_phone_numbers(text_body, default_region=default_region)
    return numbers[0] if numbers else None


def _phone_dedup_key(raw: str) -> str:
    """Canonical key for de-duplicating numbers written differently
    ("+91 98111 20034" vs "+919811120034"). Uses the SAME region-priority
    STRICT_GROUPING matcher as extraction, so a stored formatted number keys
    identically to how it was captured; falls back to a digits-only key so two
    numbers never collapse merely because matching failed."""
    if not raw:
        return ""
    for region in _PHONE_CANDIDATE_REGIONS:
        for match in phonenumbers.PhoneNumberMatcher(
            raw, region, leniency=phonenumbers.Leniency.STRICT_GROUPING
        ):
            try:
                return phonenumbers.format_number(
                    match.number, phonenumbers.PhoneNumberFormat.E164
                )
            except Exception:  # noqa: BLE001
                pass
    return re.sub(r"\D", "", raw)


def extract_phone_numbers(text_body: str, default_region: Optional[str] = None) -> List[str]:
    """Every distinct valid phone number in the text, in reading order, each
    normalized to +CC INTERNATIONAL form when the country is known confidently
    (else kept as written — see to_international).

    Same validation as `extract_phone_number` (libphonenumber, STRICT_GROUPING,
    plus the must-be-formatted guard against unformatted digit runs), but
    returns ALL of them instead of just the first — a reply can carry more than
    one number, and a later reply can add another.

    `default_region` (ISO-2, typically the lead's own country from
    person_country) is tried FIRST when resolving a bare local number, so it
    lands the right country code instead of guessing by global priority order.

    De-duplicated two ways: by text SPAN (the same number matched under several
    candidate regions is counted once) and by canonical E.164 (the same number
    written twice, or differently, is returned once).
    """
    if not text_body:
        return []
    # Try the lead's own country first so a bare local number ("098111 20034")
    # resolves to it rather than to whichever region happens to be first.
    regions = _PHONE_CANDIDATE_REGIONS
    if default_region:
        regions = [default_region] + [r for r in _PHONE_CANDIDATE_REGIONS if r != default_region]
    # HTML-signature-to-plaintext conversion routinely drops the line
    # breaks between "P:", "E:", "A:", "W:" fields, gluing them together
    # with no whitespace at all — "P:0421 557 958E:rob@x.com" — which hides
    # the token boundary the matcher needs to isolate the number from what
    # follows it. Splitting at every letter/digit adjacency is safe here:
    # it can only ever ADD a boundary the matcher didn't have, never merge
    # two already-separated tokens, so it can't turn a non-number into one.
    normalized = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text_body)
    # Drop a stray "+" a sender put between a country label and a national number
    # ("IN: + 98111 20034") so the digits parse under the labelled country
    # instead of being read as a bogus country code and dropped/mis-tagged.
    normalized = _LABEL_PLUS_RE.sub(_strip_spurious_label_plus, normalized)
    seen_spans = set()   # text spans already claimed by a higher-priority region
    seen_keys = set()    # canonical E.164 keys already collected
    found = []           # (start_offset, raw_string), sorted to reading order at the end
    for region in regions:
        for match in phonenumbers.PhoneNumberMatcher(
            normalized, region, leniency=phonenumbers.Leniency.STRICT_GROUPING
        ):
            raw = match.raw_string
            if not (raw.startswith("+") or any(c in raw for c in " -.()")):
                # Unformatted digit run: accept ONLY when a phone cue sits right
                # before it (so "reach me on 9459475257" is kept while "invoice
                # 4028732318" / "order 9811120034" stay rejected) — OR when an
                # explicit country label tags it ("AU: 492936446"), which is
                # itself strong evidence the run is a phone number.
                window = normalized[max(0, match.start - 40):match.start]
                if not _PHONE_CONTEXT_RE.search(window) and not _region_hint(
                    normalized, match.start, match.start + len(raw)
                ):
                    continue
            if _DATE_LIKE_RE.match(raw) or _ID_LIKE_RE.match(raw):
                continue  # a calendar date or structured ID (ZIP+4 / EIN), not a phone
            span = (match.start, match.start + len(raw))
            if span in seen_spans:
                continue
            seen_spans.add(span)
            try:
                key = phonenumbers.format_number(
                    match.number, phonenumbers.PhoneNumberFormat.E164
                )
            except Exception:  # noqa: BLE001
                key = re.sub(r"\D", "", raw)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append((match.start, match.start + len(raw), raw))
    found.sort(key=lambda t: t[0])
    # Normalize each to +CC international form, resolving country per-number:
    # an inline label next to THIS number ("AU: 0421 557 256") wins over the
    # lead's default_region, so a reply mixing countries lands each correctly.
    # Ambiguous bare locals with no cue keep their raw form.
    return [
        to_international(raw, default_region, label_region=_region_hint(normalized, s, e))
        for s, e, raw in found
    ]


def merge_phone_lists(existing: Optional[List[str]], new: Optional[List[str]]) -> List[str]:
    """Append genuinely-new numbers from `new` onto `existing`, de-duplicated by
    canonical form, preserving order. NEVER drops or reorders existing entries —
    a lead's phone list only ever grows (scenario 9: a second number in a later
    reply joins the first rather than replacing it)."""
    result = [p for p in (existing or []) if isinstance(p, str) and p.strip()]
    keys = {_phone_dedup_key(p) for p in result}
    for n in new or []:
        if not (isinstance(n, str) and n.strip()):
            continue
        k = _phone_dedup_key(n)
        if k and k not in keys:
            keys.add(k)
            result.append(n.strip())
    return result


_DEMO_TIME_SYSTEM = (
    "You extract the meeting time a B2B prospect committed to in an email reply. "
    "The prospect wrote back to a sales email and named a day/time for a demo or "
    "call (e.g. 'Tuesday at 2pm works', 'let's do Thursday 3:30', 'the 5th at "
    "10am'). Resolve any relative day against the RECEIVED timestamp given, "
    "picking the NEXT such day in the future. SAME-DAY RULE: if the named "
    "weekday is the SAME as the received day-of-week, it means TODAY when the "
    "stated clock time is still later than the received time; only if that time "
    "has already passed today does it mean the following week. 'today' always "
    "means the received date; 'tomorrow' the day after. Return STRICT JSON ONLY:\n"
    '{"found": true|false, "date": "YYYY-MM-DD", "time": "HH:MM", '
    '"confidence": <0.0-1.0>}\n'
    "Rules: 24-hour HH:MM. found=false unless a specific day AND time are both "
    "clearly stated — never guess a time that wasn't given. If only a day is "
    "given with no clock time, found=false. Output the prospect's local "
    "wall-clock time exactly as they meant it; do NOT convert time zones."
)


def extract_demo_datetime(
    text_body: str, received_at: Optional[datetime] = None
) -> Optional[datetime]:
    """Extract the specific demo/meeting time a reply committed to, as a naive
    LOCAL wall-clock datetime (the time the prospect meant, in their own zone —
    no timezone math here; the caller pairs it with a configured zone when it
    creates the calendar event).

    Scenario 7: a worded confirmation like "Tuesday at 2pm works" should
    produce a real calendar invite, the same outcome as clicking the booking
    link. This resolves the stated time so that invite can be created.

    Uses an LLM (relative days like "Tuesday"/"tomorrow" need resolving against
    the received date, which regex does poorly), but only runs on the
    DEMO_SCHEDULED path — not every reply. Returns None whenever a specific day
    AND clock time aren't both clearly stated: "never guess a time", and a
    None simply falls back to today's behaviour (marked booked, no invite).
    """
    if not text_body or not text_body.strip():
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    anchor = received_at or datetime.utcnow()
    if getattr(anchor, "tzinfo", None) is not None:
        anchor = anchor.replace(tzinfo=None)

    from nexus.services import gemini

    user = (
        "RECEIVED timestamp (treat as 'now'): %s (%s)\n\n"
        "Reply body:\n%s"
        % (anchor.isoformat(), anchor.strftime("%A"), (text_body or "")[:2000])
    )
    try:
        raw = gemini.chat_completion(
            system=_DEMO_TIME_SYSTEM,
            user=user,
            temperature=0.0,
            max_tokens=120,
            response_format_json=True,
        )
        data = gemini.extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_demo_datetime call failed: %s", exc)
        return None

    if not isinstance(data, dict) or not data.get("found"):
        return None
    try:
        if float(data.get("confidence", 0)) < 0.6:
            return None
    except (TypeError, ValueError):
        return None
    d, t = str(data.get("date") or "").strip(), str(data.get("time") or "").strip()
    if not d or not t:
        return None
    try:
        parsed = datetime.strptime("%s %s" % (d, t), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    # Sanity: a committed demo time is in the near future, never the past and
    # never absurdly far out. Reject anything outside (now-1d, now+120d) so a
    # model date-arithmetic slip can't create a nonsense invite.
    if not (anchor - timedelta(days=1) <= parsed <= anchor + timedelta(days=120)):
        logger.warning("extract_demo_datetime: parsed %s outside sane window — ignoring", parsed)
        return None
    return parsed


def classify(text_body: str, channel: str = "email") -> IntentClassification:
    """Classify the inbound message body. Never raises — always returns a
    valid IntentClassification (falling through to heuristic on any error).

    `channel="linkedin"` swaps the prompt and the regex fallback for LinkedIn-DM
    shaped text. The intent VOCABULARY is deliberately identical across channels
    so the UI, analytics and downstream branching stay single-sourced; only the
    reading of that text differs (see LINKEDIN_SYSTEM_PROMPT).
    """
    if not text_body or not text_body.strip():
        return IntentClassification(intent="QUESTION", confidence=0.1, reasoning="empty body")

    is_li = (channel or "").lower() == "linkedin"
    llm_result = _llm_call(
        text_body,
        system_prompt=LINKEDIN_SYSTEM_PROMPT if is_li else SYSTEM_PROMPT,
    )
    if llm_result is not None:
        return llm_result
    return _heuristic_linkedin(text_body) if is_li else _heuristic(text_body)


__all__ = [
    "classify",
    "VALID_INTENTS",
    "IntentClassification",
    "extract_ooo_return_date",
    "extract_ooo_return_delay_days",
    "extract_phone_number",
    "extract_phone_numbers",
    "merge_phone_lists",
    "to_international",
    "country_to_iso",
    "extract_demo_datetime",
]
