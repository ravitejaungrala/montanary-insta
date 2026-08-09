"""Pre-flight validation of user campaign briefs.

Three-tier local guard that runs BEFORE the expensive AI pipeline:
  1. Basic — brief is present and long enough
  2. Policy — banned language (sexual, violent, illegal, hate)
  3. Generic — obvious placeholders ("generate a post", "test", etc.)

A 4th tier — semantic off-DNA check — lives inside the refiner agent
(services/ai_service.py::_refine_brief_agent) because it needs the
Business DNA + LLM to judge relevance. When the refiner detects a
semantic violation (harmful-but-paraphrased, off-brand, or substantive-
enough-to-pass-rules-but-still-nonsense) it raises `BriefRejected`
which the endpoint catches and converts to HTTP 422.

Usage:
    ok, message = validate_brief(brief_text)
    if not ok:
        raise HTTPException(status_code=422, detail=message)

    # Refiner-level semantic check:
    try:
        refined = _refine_brief_agent(brief, dna)
    except BriefRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc))
"""
from __future__ import annotations

import re
from typing import Tuple


class BriefRejected(Exception):
    """Raised by the refiner when the brief fails semantic validation
    (harmful intent paraphrased past the keyword list, off-DNA, or
    substantive-looking-but-meaningless). The message is always
    user-facing — it's shown verbatim on the frontend."""
    def __init__(self, message: str, category: str = "invalid"):
        super().__init__(message)
        self.category = category  # "harmful" | "generic" | "off_brand" | "invalid"


# Hard-banned keywords — any match blocks the brief regardless of context.
# Word-boundary matched, case-insensitive. Kept conservative on purpose;
# false positives are worse than false negatives here because users can
# rephrase around a refusal more easily than they can interpret a fake
# "working" output.
BANNED_TERMS = [
    # Sexual / adult — standalone words AND phrases. \b boundaries below
    # prevent "sex" from matching "sex education" research → actually it
    # WILL match, but for a B2B marketing tool the false-positive cost
    # of rejecting "sex ed" briefs is tiny vs the reputational cost of
    # generating explicit content. Users can rephrase if needed.
    "sex", "sexual", "sexy", "porn", "pornography", "xxx", "erotic",
    "nude", "naked", "horny", "onlyfans", "nsfw", "fetish", "hookup",
    "escort", "blowjob", "handjob", "masturbat", "dick pic", "nude pics",
    "sexting", "kinky",
    # Violence / harm
    "kill", "murder", "suicide", "bomb", "bombing", "shoot", "shooter",
    "shooting", "terrorist", "terrorism", "assault rifle", "behead",
    "massacre", "genocide",
    # Drugs / illegal
    "cocaine", "heroin", "meth", "opioid", "drug deal", "cartel",
    "illegal drugs",
    # Hate / slurs
    "nazi", "kkk", "white supremacy",
    # Scams / fraud
    "pyramid scheme", "ponzi", "money laundering", "phishing",
    # Weapons
    "automatic weapon", "machine gun", "silencer",
]

# Compiled once at module load.
_BANNED_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in BANNED_TERMS) + r")\b",
    re.IGNORECASE,
)

# Regexes that match briefs which are too generic to produce meaningful
# content. Each pattern tests the WHOLE trimmed brief (^...$) so we don't
# wrongly reject real briefs that happen to include the word "post".
GENERIC_PATTERNS = [
    r"^\s*create\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*generate\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*make\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*(?:post|write)\s+(?:about\s+)?(?:something|anything)?\s*\.?\s*$",
    r"^\s*(?:test|testing|hello|hi|hey|yo|asdf|lorem ipsum)\s*\.?\s*$",
    r"^\s*create\s+\w+\s*\.?\s*$",          # "create spenzo" / "create xyz"
    r"^\s*generate\s+\w+\s*\.?\s*$",        # "generate xyz"
    r"^\s*(?:do|pls|please)\s+.{0,20}$",    # "do something", "pls help"
]
_GENERIC_COMPILED = [re.compile(p, re.IGNORECASE) for p in GENERIC_PATTERNS]

MIN_WORD_COUNT = 4
MIN_CHAR_COUNT = 15

# Characters that, when they dominate the brief, signal junk input
# (emoji spam, keyboard mashing, all caps rants with no substance).
_ALPHA_RATIO_MIN = 0.45


def validate_brief(brief: str) -> Tuple[bool, str]:
    """Return (is_valid, reason). Reason is empty when valid, otherwise a
    user-facing error message the frontend can show verbatim."""
    if not brief or not brief.strip():
        return False, "Please enter a campaign brief before generating."

    text = brief.strip()
    lower = text.lower()

    # 1. Policy — banned terms. Match produces a clear refusal.
    m = _BANNED_PATTERN.search(lower)
    if m:
        return False, (
            "We can't generate marketing content around this topic. "
            "Your brief contained language our policy doesn't support — "
            "please rewrite with a business-appropriate focus "
            "(e.g. a product, service, offer, or announcement relevant "
            "to your audience)."
        )

    # 2. Length — anything shorter than the minimum is almost certainly
    # junk the AI can't ground in anything real. Generic message: don't
    # surface the word count or minimum threshold to the user — those are
    # internal tuning knobs that change over time.
    words = [w for w in re.split(r"\s+", lower) if w]
    if len(text) < MIN_CHAR_COUNT or len(words) < MIN_WORD_COUNT:
        return False, (
            "Your brief is too short. Please describe what you want to promote, "
            "who it's for, and why it matters. Example: 'Launch our new Q2 "
            "pricing with a focus on mid-market teams — emphasize the 40% "
            "time savings.'"
        )

    # 3. Alpha density — if the brief is mostly emojis / symbols / numbers
    # we have nothing to ground on.
    alpha_count = sum(1 for c in lower if c.isalpha())
    if alpha_count / max(1, len(lower)) < _ALPHA_RATIO_MIN:
        return False, (
            "Your brief has too few real words. Please describe the "
            "campaign in plain English — what you're promoting, who "
            "it's aimed at, and what the key benefit is."
        )

    # 4. Generic placeholder phrases. Catches "create a post", "generate
    # an ad", "create spenzo", "test", etc.
    for rx in _GENERIC_COMPILED:
        if rx.match(text):
            return False, (
                "Your brief is too generic. Please describe the actual "
                "campaign — what you're promoting, the audience, and the "
                "key message — instead of just a command. "
                "Example: 'Announce our new AI pricing insights feature "
                "to mid-market SaaS marketing leaders, highlighting how "
                "it replaces their weekly forecast meetings.'"
            )

    return True, ""
