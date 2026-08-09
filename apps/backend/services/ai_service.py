"""
Pipelyt AI Service
Full multi-agent pipeline: agents + orchestration + image generation.
"""
import os
import json
import uuid
import logging
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from PIL import Image
from google import genai
from google.genai import types
from core.config import GEMINI_API_KEY, S3_BUCKET_NAME
from core.s3_utils import get_s3_client, get_s3_url
from services.retry_helper import call_with_retry
from services.cost_ledger import get_current_ledger

logger = logging.getLogger("pipelyt.ai")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ---------------------------------------------------------------------------
# Core LLM helper
# ---------------------------------------------------------------------------

# Primary + fallback model for all campaign-time text agents (REFINER,
# CULTURAL_CALENDAR, RESEARCHER, COPYWRITER, CRITIC, BULK_COPYWRITER,
# and any other agent routed through `_call_agent`).
# PRIMARY   = alias that currently resolves to Gemini 3.1 Flash-Lite
#             ($0.25 in / $1.50 out per 1M tokens)
# FALLBACK  = older stable Gemini 2.5 Flash-Lite ($0.10 in / $0.40 out)
#             — used when primary exhausts all retry attempts
_TEXT_AGENT_PRIMARY_MODEL  = "gemini-flash-lite-latest"
_TEXT_AGENT_FALLBACK_MODEL = "gemini-2.5-flash-lite"


def _call_agent(agent_name, prompt, model_name=_TEXT_AGENT_PRIMARY_MODEL, temperature=0.7, *, web_search=False, fallback_model=_TEXT_AGENT_FALLBACK_MODEL):
    """Call Gemini and parse JSON response.

    When `web_search=True`, enables Gemini's native Google Search grounding
    tool. The model will run real web queries and ground its response in
    fresh sources. We then parse `grounding_metadata` off the response and
    attach it to the returned dict under `_grounding` so callers can render
    the sources panel.

    NOTE: Gemini does not allow `response_mime_type="application/json"` to be
    combined with the google_search tool. We rely on prompt-side discipline
    (the agent prompt tells the model to return strict JSON) plus the fence-
    stripping below.

    Retry + Fallback strategy:
      1. Try `model_name` (primary) with 3-retry exponential backoff.
         Transient errors (rate limit / 5xx / timeout) get retried;
         non-transient (spend cap / auth / bad input) fail fast.
      2. If primary exhausts retries, automatically try `fallback_model`
         with its own 3-retry policy. Fallback defaults to
         gemini-2.5-flash-lite — different underlying version so it
         survives if the primary alias hot-swaps to a broken preview.
      3. If BOTH exhaust, return error dict (same shape as before) so
         callers that check for `.get("error")` still work.

    Pass `fallback_model=None` to disable fallback for a specific call
    (e.g. background jobs where a slow fallback would matter more than
    a quick failure).
    """
    logger.info(f"[DEBUG] AI Service: Calling agent {agent_name} ({model_name=}, web_search={web_search})")
    if not client:
        return {"error": "AI client not initialized"}
    try:
        tools = [types.Tool(google_search=types.GoogleSearch())] if web_search else None
        # top_p and top_k pinned to Gemini API defaults — set explicitly so the
        # generation config is auditable (no silent reliance on SDK defaults
        # that could drift across SDK versions).
        config = types.GenerateContentConfig(
            temperature=temperature,
            top_p=0.95,
            top_k=40,
            tools=tools,
        )

        # ── Try primary model with retry ─────────────────────────────────
        # Then, if primary exhausts retries, try fallback with its own retry.
        # We record whichever model ACTUALLY served the response in the
        # ledger — so the CSV shows the truth about what got billed.
        import time as _time
        _call_t0 = _time.monotonic()
        _model_used = model_name
        res = None
        _primary_err = None
        try:
            res = call_with_retry(
                lambda: client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                ),
                label=f"Gemini/{agent_name}/{model_name}",
            )
        except Exception as _pe:
            _primary_err = _pe
            if fallback_model and fallback_model != model_name:
                logger.warning(
                    f"[{agent_name}] primary model {model_name!r} exhausted "
                    f"retries — falling back to {fallback_model!r}. "
                    f"Last error: {_pe}"
                )
                try:
                    res = call_with_retry(
                        lambda: client.models.generate_content(
                            model=fallback_model, contents=prompt, config=config
                        ),
                        label=f"Gemini/{agent_name}/{fallback_model}",
                    )
                    _model_used = fallback_model
                    logger.info(
                        f"[{agent_name}] fallback model {fallback_model!r} "
                        f"succeeded after primary failed"
                    )
                except Exception as _fe:
                    logger.error(
                        f"[{agent_name}] BOTH models failed. "
                        f"primary={_primary_err} fallback={_fe}"
                    )
                    raise  # outer except returns error-shaped dict
            else:
                raise  # no fallback configured — propagate primary error
        _call_elapsed = _time.monotonic() - _call_t0

        # Record the call in the current-request cost ledger (no-op if
        # no ledger active on this thread — e.g. background jobs).
        # Agent-slot mapping drives the per-agent CSV columns. Both the
        # BULK variant and the primary copywriter funnel into the same
        # slot so the analytical sheet shows one COPYWRITER row per post
        # regardless of which caller invoked it.
        _AGENT_TO_SLOT = {
            "REFINER":            "REFINER",
            "CULTURAL_CALENDAR":  "CULTURAL",
            "RESEARCHER":         "RESEARCHER",
            "COPYWRITER":         "COPYWRITER",
            "CRITIC":             "CRITIC",
            "BULK_COPYWRITER":    "COPYWRITER",
        }
        _slot = _AGENT_TO_SLOT.get(agent_name, "")
        try:
            _ledger = get_current_ledger()
            if _ledger is not None:
                _um = getattr(res, "usage_metadata", None)
                _in_tok = int(getattr(_um, "prompt_token_count", 0) or 0) if _um else 0
                _out_tok = int(getattr(_um, "candidates_token_count", 0) or 0) if _um else 0
                _ledger.record_gemini_text(
                    model=_model_used,   # record the model that ACTUALLY served
                    input_tokens=_in_tok,
                    output_tokens=_out_tok,
                    agent_slot=_slot,
                    time_sec=_call_elapsed,
                )
        except Exception as _le:
            logger.warning(f"[cost_ledger] failed to record Gemini/{agent_name}: {_le}")

        text = res.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        parsed = json.loads(text)

        # Attach grounding metadata when the call used google_search. Callers
        # that don't care can ignore the `_grounding` key; the frontend reads
        # it to render the Sources panel.
        if web_search and isinstance(parsed, dict):
            gm = res.candidates[0].grounding_metadata if res.candidates else None
            if gm:
                sources = []
                for c in (gm.grounding_chunks or []):
                    if getattr(c, "web", None):
                        sources.append({
                            "uri": getattr(c.web, "uri", None),
                            "title": getattr(c.web, "title", None),
                        })
                parsed.setdefault("_grounding", {})
                parsed["_grounding"]["sources"] = sources
                parsed["_grounding"]["queries"] = list(gm.web_search_queries or [])
                # Google's terms require rendering this HTML widget next to
                # any grounded output we display. Surface it for the frontend.
                sep = getattr(gm, "search_entry_point", None)
                if sep and getattr(sep, "rendered_content", None):
                    parsed["_grounding"]["search_entry_point_html"] = sep.rendered_content
                logger.info(f"[GROUND] {agent_name}: {len(sources)} sources, {len(parsed['_grounding']['queries'])} queries")
        return parsed
    except Exception as e:
        logger.error(f"Agent parsing error in {agent_name}: {e}")
        return {"error": "Invalid JSON", "raw": text if 'text' in locals() else str(e)}


# ---------------------------------------------------------------------------
# Content post-processing — strip Unicode styles, enforce hashtag caps
# ---------------------------------------------------------------------------

HASHTAG_CAP_PER_PLATFORM = {
    # 2026 values per content_style_references.md research.
    "twitter": 2,
    "linkedin": 5,
    "facebook": 3,
    "instagram": 15,
}

# Hard char caps per platform — server-enforced after the LLM call so no variant
# can ever exceed the platform limit regardless of what the model emits.
#
# Safety-buffered against each platform's true hard cap so a free-tier (non-
# premium) account never has a post rejected by the platform API:
#   • Twitter free-tier hard limit is 280  → cap at 270  (10-char buffer)
#   • LinkedIn personal post hard limit is 3000 → cap at 2800 (200-char buffer)
#   • Facebook is generous (63k)                → cap at 2200 (engagement zone)
#   • Instagram caption hard limit is 2200      → cap at 2100 (100-char buffer)
CHAR_CAP_PER_PLATFORM = {
    "twitter": 270,
    "linkedin": 2800,
    "facebook": 2200,
    "instagram": 2100,
}


def _strip_unicode_styles(text: str) -> str:
    """Normalize math-alphanumeric Unicode (Bold, Sans Bold, etc.) back to ASCII."""
    if not text:
        return text
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _enforce_hashtag_cap(text: str, platform: str) -> str:
    """Trim hashtags to the per-platform limit."""
    if not text:
        return text
    cap = HASHTAG_CAP_PER_PLATFORM.get(platform, 5)
    tags = re.findall(r"(?<![\w&])#[A-Za-z0-9_]+", text)
    if len(tags) <= cap:
        return text
    kept_ordered = []
    for t in tags:
        if t not in kept_ordered and len(kept_ordered) < cap:
            kept_ordered.append(t)
    kept_lookup = set(kept_ordered)

    def _maybe_strip(match):
        return match.group(0) if match.group(0) in kept_lookup else ""

    cleaned = re.sub(r"(?<![\w&])#[A-Za-z0-9_]+", _maybe_strip, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    return cleaned.strip()


def _enforce_char_cap(text: str, platform: str) -> str:
    """Hard-truncate to the platform's character cap at a word boundary.

    Preserves trailing hashtags when possible: if the overflow is in the body
    and hashtags sit at the end, we try to keep them by trimming the body
    first. If the hashtag block itself is over cap (rare), we truncate it too.
    """
    if not text:
        return text
    cap = CHAR_CAP_PER_PLATFORM.get(platform)
    if not cap or len(text) <= cap:
        return text

    # Try to preserve trailing hashtag line
    stripped = text.rstrip()
    hashtag_tail_match = re.search(r"(\n*(?:#[A-Za-z0-9_]+(?:[ \t]+|$))+)\s*$", stripped)
    tail = ""
    body = stripped
    if hashtag_tail_match:
        tail = hashtag_tail_match.group(1).strip()
        body = stripped[: hashtag_tail_match.start()].rstrip()

    # Budget for body: cap minus tail (plus a newline separator) minus ellipsis
    tail_budget = len(tail) + (2 if tail else 0)  # "\n" + tail
    body_budget = cap - tail_budget - 1  # room for "…"
    if body_budget < 20:
        # Tail alone eats the cap — drop tail, truncate whole thing
        return _truncate_at_word(text, cap - 1) + "…"

    if len(body) > body_budget:
        body = _truncate_at_word(body, body_budget).rstrip(" ,.;:") + "…"

    out = f"{body}\n{tail}" if tail else body
    return out[:cap]  # final safety net


def _truncate_at_word(text: str, limit: int) -> str:
    """Cut text to <= limit characters at the last whitespace boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:  # only honor boundary if it's not too early
        cut = cut[:last_space]
    return cut.rstrip()


def _apply_content_post_processing(content_dict: dict) -> dict:
    """Clean generated variants: cap hashtags, enforce platform char caps.
    Char cap is hard — the server guarantees the output will fit the platform
    limit regardless of what the LLM emitted.

    v3 note: Unicode bold (𝐀𝐁𝐂) is no longer stripped server-side. The
    copywriter prompt instructs the model to use Unicode bold only when the
    brand's tone fits it (tactical / marketing voices like Lifesight). Strip
    here would override that judgment. The legacy `_strip_unicode_styles`
    helper is still available if a tenant ever wants it back.

    Iterates over EVERY variant key in the dict so the new `festival_variant`
    slot (emitted only when research.festival_alerts is non-empty) is
    processed identically to viral_reach / high_interaction / follower_growth.
    """
    if not isinstance(content_dict, dict):
        return content_dict
    result = {}
    for platform, variants in content_dict.items():
        if not isinstance(variants, dict):
            result[platform] = variants
            continue
        cleaned_variants = {}
        for variant_name, text in variants.items():
            if not isinstance(text, str):
                cleaned_variants[variant_name] = text
                continue
            text = _strip_placeholder_links(text)
            text = _enforce_hashtag_cap(text, platform.lower())
            text = _enforce_char_cap(text, platform.lower())
            cleaned_variants[variant_name] = text
        result[platform] = cleaned_variants
    return result


import re as _re

# Belt-and-braces fallback for copywriter rule #9. Even with the prompt
# explicitly forbidding placeholders, the model occasionally still emits
# `[Insert Link 1]`, `[link]`, `[your website]`, etc. This strips them so
# the user never sees fake URLs in their post.
#
# Strategy: detect the placeholder + any nearby decorative artefacts
# (preceding bullet emoji, trailing " - description" tail) and remove
# the whole orphan line. If the placeholder is mid-sentence we just drop
# the bracketed token and leave the surrounding prose alone.
_PLACEHOLDER_LINK_PATTERNS = [
    # Bracketed placeholders — common variants
    _re.compile(r"\[\s*insert\s*link(?:\s*\d+)?\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*link(?:\s*\d+)?\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*url(?:\s*\d+)?\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*your\s+website\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*brand\s+website\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*product\s+url\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*learn\s+more(?:\s+here)?\s*\]", _re.IGNORECASE),
    _re.compile(r"\[\s*click\s+here\s*\]", _re.IGNORECASE),
    _re.compile(r"\(\s*link\s*\)", _re.IGNORECASE),
    # Obviously-fake URLs
    _re.compile(r"https?://(?:www\.)?(?:example|placeholder|your-?site|your-?domain|brand-?site)\.[a-z]{2,5}\S*", _re.IGNORECASE),
]
_PLACEHOLDER_LINE_PATTERN = _re.compile(
    r"^\s*[•\-\*\d0-9⃣①-⓿❶-❿]*\s*"
    r".*\[\s*(?:insert\s*)?(?:link|url)(?:\s*\d+)?\s*\].*$",
    _re.IGNORECASE,
)


def _strip_placeholder_links(text: str) -> str:
    """Remove placeholder URL tokens from generated copy.

    Two-pass:
      1. Drop whole lines whose primary content is a `[Insert Link N] - …`
         resource-list item (these are unrecoverable — there's no real URL
         to slot in, so the line is dead).
      2. Strip inline bracketed placeholders from prose, leaving the
         surrounding sentence intact.
    """
    if not text:
        return text
    # Pass 1 — line-level: drop dead resource-list items
    kept_lines = []
    for line in text.splitlines():
        if _PLACEHOLDER_LINE_PATTERN.match(line):
            # This whole line is a "[Insert Link 1] - Best overview" item.
            # No real URL means the line is dead — drop it entirely.
            continue
        kept_lines.append(line)
    text = "\n".join(kept_lines)
    # Pass 2 — token-level: scrub any remaining inline placeholders
    for pattern in _PLACEHOLDER_LINK_PATTERNS:
        text = pattern.sub("", text)
    # Collapse any double-newlines we may have introduced
    text = _re.sub(r"\n{3,}", "\n\n", text)
    # Trim trailing whitespace on each line
    text = "\n".join(ln.rstrip() for ln in text.splitlines())
    return text.strip()


BANNED_CORPORATE_PHRASES = [
    "paradigm shift", "revolutionary", "revolutionize", "unparalleled",
    "game-changer", "game changer", "the future is here", "the future of",
    "next level", "state of the art", "state-of-the-art", "cutting-edge",
    "cutting edge", "let's talk about", "let's dive into", "in today's",
    "in today's fast-paced", "in today's world", "in this day and age",
    "at the end of the day", "when it comes to", "needless to say",
    "it goes without saying", "the sky's the limit",
    "low-hanging fruit", "circle back", "synergy", "leverage",
    "take it to the next level", "bottleneck to a breakthrough",
    "drowning in", "officially over", "era of", "don't get left behind",
    "join the revolution", "unlock the full potential", "harness the power",
    "transform your", "supercharge your",
]

BANNED_PHRASES_LIST_STR = ", ".join(f'"{p}"' for p in BANNED_CORPORATE_PHRASES)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def _refine_brief_agent(campaign_brief, user_context=""):
    """Agent 1: Turns the user's raw brief into a structured strategic brief
    that downstream agents (research, content, visualist) can execute against
    without guessing.

    Design notes (April 2026 rewrite):
      - Output stays a string keyed "refined_brief" so downstream agents keep
        working unchanged. The string is a labeled-sections brief document,
        not free-flowing prose.
      - HOOK PROTOCOL and BANNED PHRASES are NOT enforced here — they live in
        the content agent where the actual post prose is written. The refiner
        produces strategy, not prose.
      - The refiner is instructed to aggressively fill gaps from Business DNA
        and uploaded documents when the user's brief is lean (<20 words). Any
        assumptions made are tagged explicitly under ASSUMPTIONS MADE so the
        user can spot when the AI inferred something.
      - Every SUPPORTING POINT must cite its source (DNA field, doc filename,
        or the user's own brief) — anti-hallucination is enforced via source-
        requirement rather than a prohibition list.
    """
    brief_text = (campaign_brief or "").strip()
    word_count = len(brief_text.split()) if brief_text else 0
    if word_count == 0:
        quality_hint = "empty"
    elif word_count < 20:
        quality_hint = "lean"
    elif word_count < 50:
        quality_hint = "specific"
    else:
        quality_hint = "professional"

    # v2: brief is the primary source of truth; DNA is supporting knowledge.
    # The dna_attached flag lets the prompt branch its source-tagging rules
    # without us having to maintain two prompt copies.
    dna_attached = "yes" if (user_context and user_context.strip()) else "no"
    knowledge_block = user_context if dna_attached == "yes" else "(no brand knowledge attached — work from brief only)"

    prompt = f"""
    You are a Senior Social Media Growth Strategist.

    Your job: take a user's raw campaign brief and produce a structured
    STRATEGIC BRIEF that downstream agents (research, copywriter, visualist)
    can execute against without guessing what the user meant.

    ═══════════════════════════════════════════════════════════════
    PRIMARY SOURCE OF TRUTH = THE USER'S CAMPAIGN BRIEF
    ═══════════════════════════════════════════════════════════════
    The brief is the only authoritative source for:
      • WHAT the campaign is about (topic, subject, announcement)
      • WHY it matters (the user's objective)
      • Any specific facts, numbers, names, dates the user included

    The brief is what you respect. DNA is what you reach for when the
    brief leaves a gap that must be filled to produce something usable.

    ═══════════════════════════════════════════════════════════════
    SUPPORTING KNOWLEDGE = BUSINESS DNA + UPLOADED DOCUMENTS (optional)
    ═══════════════════════════════════════════════════════════════
    DNA attached: {dna_attached}

    ===== BUSINESS DNA + KNOWLEDGE BASE =====
    {knowledge_block}
    ===== END KNOWLEDGE =====

    USE THIS KNOWLEDGE STRICTLY FOR:
      ✓ Voice / tone / brand values when shaping TONE
      ✓ Audience persona inference when the brief didn't name one
      ✓ Concrete supporting facts (only when sourced & cited)
      ✓ Visual flavor hints (brand aesthetic)

    DO NOT USE KNOWLEDGE FOR:
      ✗ Changing the topic the user asked about
      ✗ Inserting a product into a brief that's about something else
      ✗ Inventing metrics, percentages, dollar figures, or claims not
        present in the brief OR in the knowledge text above
      ✗ Pivoting an industry-news / opinion / educational brief into
        a product pitch

    If the user is campaigning ABOUT their own product, DNA naturally
    becomes deeply relevant. If the user is campaigning about an adjacent
    topic (industry trends, news, general education), DNA contributes
    voice and audience only — the post stays on the user's topic.

    ═══════════════════════════════════════════════════════════════
    USER'S RAW CAMPAIGN BRIEF
    ═══════════════════════════════════════════════════════════════
    ({word_count} words, quality hint: {quality_hint})

    \"\"\"
    {brief_text or '(empty — user did not provide any brief text)'}
    \"\"\"

    ═══════════════════════════════════════════════════════════════
    STEP 0 — VALIDATE THE BRIEF BEFORE REFINING (CRITICAL)
    ═══════════════════════════════════════════════════════════════
    If ANY category below fires, DO NOT produce a strategic brief.
    Return the rejection JSON shown at the end of this section.

    A. HARMFUL — promotes, glorifies, instructs, or plans:
       • violence (killing, bombing, shootings, terrorism, weapons,
         beheadings, massacre, genocide)
       • self-harm or suicide
       • sexual / explicit content / adult services / minors
       • illegal activity (drug dealing, trafficking, fraud, scams,
         pyramid schemes, phishing, money laundering, hacking-for-hire)
       • hate speech, slurs, harassment, targeting of any group
       Even if framed as "marketing" for a product implying these — reject.

    B. GENERIC / EMPTY INTENT — no actual campaign concept:
       • "create a post" / "generate an ad" / "make me content"
       • "write something about our company"
       • "test", "hello", random words, single product name with no detail
       • briefs that name no subject, no audience, no purpose

    C. NO MARKETING UTILITY — the brief has no plausible social-media
       marketing use case for ANY business (e.g. "what is 2+2", personal
       diary entries, requests for code/legal/medical advice).
       IMPORTANT: do NOT reject for being "off-brand vs DNA". Cross-topic
       briefs (e.g. a marketing tool company posting about general AI
       industry news) are allowed — DNA contributes voice only. Only
       reject when no business could plausibly market this content.

    If REJECTED, return EXACTLY this JSON (no markdown fences):
    {{
      "valid": false,
      "rejection_category": "harmful" | "generic" | "no_utility",
      "rejection_message": "<one short user-facing sentence, specific to the reason, no apology boilerplate>"
    }}

    If VALID, set "valid": true and produce the refined_brief below.

    ═══════════════════════════════════════════════════════════════
    GAP-FILLING (only when brief is lean/empty AND a downstream agent
    needs the field)
    ═══════════════════════════════════════════════════════════════
    QUALITY = empty | lean   → you MAY infer AUDIENCE, TONE, VISUAL HINT,
                               ANGLE format from knowledge or general
                               plausibility. You MUST list every inferred
                               field under ASSUMPTIONS MADE.

    QUALITY = specific | professional
                             → respect everything the user wrote. Only
                               fill fields they left blank. ASSUMPTIONS
                               MADE = "Minimal".

    NEVER fill TOPIC or KEY MESSAGE by invention — both must trace
    directly to the brief. If the brief is too thin to produce these,
    that's STEP 0 Category B (generic) territory — reject.

    NEVER refuse to produce a brief for being "too short" once it passes
    STEP 0. Always emit a best-effort strategic brief.

    ═══════════════════════════════════════════════════════════════
    SOURCE-GROUNDING (CRITICAL — anti-hallucination)
    ═══════════════════════════════════════════════════════════════
    Every entry under SUPPORTING POINTS MUST cite its source:
      1. "[user brief]"          — the user literally wrote this
      2. "[DNA: <field name>]"   — only if DNA was attached above
      3. "[doc: <filename>]"     — only if that file is in KNOWLEDGE

    When DNA is not attached ({dna_attached} = "no"), only [user brief]
    tags are valid.

    If you cannot cite a source for a factual claim, DO NOT include it.
    Fewer well-sourced points > more unverifiable ones.

    DO NOT invent specific metrics, percentages, dollar amounts, partner
    counts, time savings, customer counts, or other numeric proof unless
    the source explicitly states them.

    ═══════════════════════════════════════════════════════════════
    OUTPUT FORMAT — produce these labelled sections, in this order
    ═══════════════════════════════════════════════════════════════
    STRATEGIC BRIEF
    ──────────────────────────────────────────
    USER GOAL
      [One sentence. Paraphrase the user's literal intent — what they
       asked you to help produce. This is the anchor every other field
       must serve.]

    TOPIC
      [2 sentences. Faithfully describe the subject of the campaign
       exactly as the user framed it. Do NOT pivot to a related brand
       topic. If the user said "latest AI updates", the topic is latest
       AI updates — not "how our product uses AI".]

    AUDIENCE
      [Specific persona in 1-2 sentences with concrete role titles,
       company size, or stage words. If the user named an audience, use
       theirs. Else if DNA suggests one that fits the topic, use it.
       Else state "General audience interested in [topic]".]

    KEY MESSAGE
      [One sentence — the single thing the audience should walk away
       knowing. Must serve USER GOAL, not a brand pitch.]

    ANGLE
      [The POV / framing that delivers the USER GOAL compellingly. Choose
       what fits the brief: announcement, educational explainer, opinion,
       contrarian take, breakdown, story, listicle, news commentary, etc.
       No fixed formula. No "lead with the pain" by default.]

    SUPPORTING POINTS
      - [Fact or claim] [source tag]
      - [Fact or claim] [source tag]
      - [Fact or claim] [source tag]
      (2-5 points. Every point sourced. Fewer is better than fabricated.)

    TONE
      [Voice directives. If DNA brand_tone exists, use it verbatim. Else
       pick a sensible default for the topic (e.g. "informative, neutral,
       plain English"). Be specific: "direct, not salesy" / "curious,
       not authoritative".]

    SOURCES REFERENCED
      - User brief: [yes / no — set "no" only if every field came from DNA]
      - Business DNA: [fields used, or "not attached"]
      - Uploaded docs: [filenames you sourced from, or "none"]

    USER LINKS
      [Extract EVERY URL the user typed in their raw brief, VERBATIM.
       List each one on its own line as "- https://...". These are
       authoritative — the copywriter will use them as real resource
       links in the post (rule #9c). Common patterns to look for:
         • "Promote my course at https://..."
         • "Read more here: https://..."
         • "Sign up: https://..."
         • "https://" or "http://" appearing anywhere in the brief
       Capture the URL exactly, including query strings and trailing
       slashes. Do NOT shorten, edit, or "clean up" the URL. If the
       user typed no URLs, write a single line: "- none"]

    VISUAL HINT
      [One sentence describing what the ideal visual should CAPTURE —
       a scene, metaphor, or moment that delivers the USER GOAL. Not
       "an image of the product" unless the brief is about the product.]

    CONSTRAINTS
      - [Things to avoid for THIS brief, e.g. "no invented stats",
         "don't pivot to product pitch", "match brand_tone"]
      - [Platform-agnostic — no hook rules or banned phrases here]

    USER INPUT QUALITY: {quality_hint}
    ASSUMPTIONS MADE:
      [If quality is empty/lean: list every field above you inferred.
       If specific/professional: write "Minimal — user brief covered
       the main points."]

    ═══════════════════════════════════════════════════════════════
    Return STRICTLY this JSON (no markdown code fences, raw JSON):
    ═══════════════════════════════════════════════════════════════
    If STEP 0 rejected:
      {{ "valid": false, "rejection_category": "...", "rejection_message": "..." }}

    If STEP 0 passed:
      {{ "valid": true, "refined_brief": "<the labeled-sections text block above, with real newlines — NOT escaped>" }}
    """
    result = _call_agent("REFINER", prompt)

    # Enforce STEP 0 outcome on the Python side so callers don't have to
    # remember to check `valid`. BriefRejected propagates up through
    # run_in_threadpool → the endpoint catches it and converts to HTTP 422.
    if isinstance(result, dict) and result.get("valid") is False:
        from services.brief_guard import BriefRejected
        category = str(result.get("rejection_category") or "invalid").lower()
        # Generic per-category messages — ALWAYS shown, regardless of what
        # custom message the refiner wrote. We want users to see consistent,
        # predictable rejection text per category rather than a different
        # phrasing every time. `off_brand` kept as an alias of `no_utility`
        # so older cached rejection responses still surface a sensible
        # message.
        generic_messages = {
            "harmful": "We can't generate marketing content for briefs that involve violence, sexual content, or illegal activity. Please rewrite with a business-appropriate focus.",
            "generic": "Your brief is too generic. Please describe what you want to promote, the audience, and the key message — not just a command like 'create a post'.",
            "no_utility": "This brief doesn't describe content any business could plausibly market. Please rewrite as a campaign idea — what you want to promote, who it's for, and why it matters.",
            "off_brand": "This brief doesn't describe content any business could plausibly market. Please rewrite as a campaign idea — what you want to promote, who it's for, and why it matters.",
            "invalid": "Please provide a clearer campaign brief describing what to promote, who it's for, and the key message.",
        }
        message = generic_messages.get(category, generic_messages["invalid"])
        # Still log the refiner's custom message for debugging — but only
        # the generic message is surfaced to the user.
        refiner_custom = str(result.get("rejection_message") or "").strip()
        logger.warning(
            f"[REFINER] rejected brief — category={category!r} "
            f"generic_msg={message[:80]!r} refiner_custom={refiner_custom[:120]!r}"
        )
        raise BriefRejected(message, category=category)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Cultural calendar — runs once per day, attached to every research call so
# the researcher (and downstream copywriter) can acknowledge live cultural
# moments in the post. Cached in-memory by date — fine for single-instance
# dev. For Lambda prod use you'll want to back this with Redis / DynamoDB
# so all warm containers share the same daily fetch.
# ─────────────────────────────────────────────────────────────────────────────
_CULTURAL_CACHE: dict = {}


def _get_cultural_calendar(force_refresh: bool = False) -> dict:
    """Fetch today + tomorrow festivals/observances in India and USA.

    Uses Gemini google_search to ground the result in real sources. Cached
    by UTC date so we run at most one search per day regardless of how many
    /generate-content requests come in.
    """
    from datetime import datetime, timedelta
    today_dt = datetime.utcnow()
    tomorrow_dt = today_dt + timedelta(days=1)
    today_iso = today_dt.strftime("%Y-%m-%d")
    tomorrow_iso = tomorrow_dt.strftime("%Y-%m-%d")

    cache_key = today_iso
    if not force_refresh and cache_key in _CULTURAL_CACHE:
        logger.info(f"[CULTURAL] cache hit for {cache_key}")
        return _CULTURAL_CACHE[cache_key]

    prompt = f"""
    You have access to google_search. Find ONLY the major, nation-wide
    cultural moments actually being observed today or tomorrow that a
    mainstream marketer would actually acknowledge in social content.

    Today's date (UTC): {today_iso}
    Tomorrow's date (UTC): {tomorrow_iso}

    ═══════════════════════════════════════════════════════════════
    STRICT INCLUSION RULES — be conservative, empty arrays are fine
    ═══════════════════════════════════════════════════════════════

    INCLUDE for INDIA only if it qualifies on at least ONE of:
      • National gazetted public holiday (observed across the WHOLE
        country, not a single state) — e.g. Independence Day,
        Republic Day, Gandhi Jayanti, Diwali, Holi, Eid-ul-Fitr,
        Eid-ul-Adha (Bakrid), Christmas, Good Friday.
      • Major nationally-recognised Hindu / Muslim / Sikh / Christian
        festival broadly observed across multiple regions — e.g.
        Diwali, Holi, Raksha Bandhan, Janmashtami, Ganesh Chaturthi,
        Navratri, Dussehra, Maha Shivratri, Eid-ul-Fitr (Ramzan Eid),
        Eid-ul-Adha (Bakrid), Muharram, Guru Nanak Jayanti, Christmas,
        Easter, Good Friday.

    INCLUDE for USA only if it qualifies on at least ONE of:
      • Federal public holiday (observed nationally) — e.g. New Year's
        Day, MLK Day, Presidents Day, Memorial Day, Juneteenth,
        Independence Day (July 4), Labor Day, Columbus / Indigenous
        Peoples Day, Veterans Day, Thanksgiving, Christmas.
      • Top-tier mainstream culturally-marketed day — Valentine's Day,
        St. Patrick's Day, Mother's Day, Father's Day, Halloween,
        Easter, Hanukkah, Super Bowl Sunday, Black Friday, Cyber Monday.

    EXPLICITLY EXCLUDE — do NOT include any of these:
      ✗ Single-state or single-city public holidays (e.g. "Public
        holiday in Jammu & Kashmir", "Holiday in Himachal Pradesh",
        "Local election holiday", "Statehood Day for X").
      ✗ Regional / niche festivals observed only in one state or
        community (e.g. Vaikasi Visakam, Tibetan Cultural Festival,
        Onam outside Kerala, Pongal outside Tamil Nadu — UNLESS the
        brief's audience is specifically in that region).
      ✗ "National X Day" novelty days (National Pizza Day, National
        Donut Day, etc.) UNLESS they are top-tier marketing days
        listed in the INCLUDE rules above.
      ✗ UN / WHO international observance days (International Day of
        Peace, International Day of UN Peacekeepers, World MS Day,
        etc.).
      ✗ Heritage months observed only in one city / state / community
        (Croatian American Heritage Month, etc.). National-level
        heritage months (Black History Month, Hispanic Heritage Month,
        Pride Month) can be included on their start/end dates only.
      ✗ Minor religious observances (vrats, fasts, partial-day rituals)
        that aren't broadly observed nationally.
      ✗ Anything you cannot ground in a reliable source.

    Other rules:
      - Date-shifting festivals (Diwali, Eid, Easter, Holi, etc.):
        include ONLY on the actual observance date this year.
      - Multi-day festivals: include on each observed date in the
        window. Note in the "note" field if it's day 1 / final day.
      - Return EMPTY arrays when nothing qualifies — do NOT pad with
        minor events.

    Return STRICTLY this JSON (no markdown, no code fences):
    {{
      "today_date": "{today_iso}",
      "tomorrow_date": "{tomorrow_iso}",
      "india": {{
        "today":    [{{"name":"...","type":"national_holiday|major_festival","note":"one-line context"}}],
        "tomorrow": [{{"name":"...","type":"...","note":"..."}}]
      }},
      "usa": {{
        "today":    [{{"name":"...","type":"federal_holiday|major_observance","note":"..."}}],
        "tomorrow": [{{"name":"...","type":"...","note":"..."}}]
      }}
    }}
    """
    result = _call_agent(
        "CULTURAL_CALENDAR",
        prompt,
        temperature=0.2,
        web_search=True,
    )
    if isinstance(result, dict) and not result.get("error"):
        _CULTURAL_CACHE[cache_key] = result
        logger.info(
            f"[CULTURAL] cached for {cache_key} "
            f"(IN today={len(result.get('india',{}).get('today',[]))}, "
            f"IN tomorrow={len(result.get('india',{}).get('tomorrow',[]))}, "
            f"US today={len(result.get('usa',{}).get('today',[]))}, "
            f"US tomorrow={len(result.get('usa',{}).get('tomorrow',[]))})"
        )
        return result
    # Failure is non-fatal — return an empty skeleton so callers can keep going.
    return {
        "today_date": today_iso,
        "tomorrow_date": tomorrow_iso,
        "india":   {"today": [], "tomorrow": []},
        "usa":     {"today": [], "tomorrow": []},
        "error": result.get("error") if isinstance(result, dict) else "unknown",
    }


def _format_cultural_calendar_for_prompt(cal: dict) -> str:
    """Convert the cultural calendar dict into a compact prompt block."""
    if not cal or cal.get("error"):
        return "(no cultural calendar data available)"
    def _fmt(events):
        if not events:
            return "  (none notable)"
        return "\n".join(
            f"  - {e.get('name','?')} ({e.get('type','?')}): {e.get('note','')}".rstrip(": ")
            for e in events
        )
    return (
        f"Today ({cal.get('today_date','?')}):\n"
        f"  India:\n{_fmt(cal.get('india',{}).get('today',[]))}\n"
        f"  USA:\n{_fmt(cal.get('usa',{}).get('today',[]))}\n"
        f"Tomorrow ({cal.get('tomorrow_date','?')}):\n"
        f"  India:\n{_fmt(cal.get('india',{}).get('tomorrow',[]))}\n"
        f"  USA:\n{_fmt(cal.get('usa',{}).get('tomorrow',[]))}"
    )


# ---------------------------------------------------------------------------
# Planning-window cultural calendar (for the strategy/scheduler planner)
# ---------------------------------------------------------------------------
# The single-day calendar above only knows today + tomorrow. For multi-day
# plan generation we need to look ahead N days so the planner can inject a
# festival slot on the correct day. Cached by (today_iso, days) so the same
# planning window doesn't re-query within a day.
_PLANNING_WINDOW_CACHE: dict = {}


def _get_planning_window_calendar(days: int = 7, force_refresh: bool = False) -> dict:
    """Fetch major festivals/holidays falling in the next `days` days.

    First post date = tomorrow (planner skips today). Window = tomorrow .. today+days.
    Same strict include/exclude rules as `_get_cultural_calendar` so the planner
    only sees nation-wide events worth posting about.
    """
    from datetime import datetime, timedelta
    today_dt = datetime.utcnow()
    first_dt = today_dt + timedelta(days=1)
    last_dt  = today_dt + timedelta(days=max(1, int(days)))
    today_iso = today_dt.strftime("%Y-%m-%d")
    first_iso = first_dt.strftime("%Y-%m-%d")
    last_iso  = last_dt.strftime("%Y-%m-%d")

    cache_key = (today_iso, int(days))
    if not force_refresh and cache_key in _PLANNING_WINDOW_CACHE:
        logger.info(f"[CULTURAL_WINDOW] cache hit for {cache_key}")
        return _PLANNING_WINDOW_CACHE[cache_key]

    prompt = f"""
    You have access to google_search. Find ONLY the major, nation-wide cultural
    moments actually being observed BETWEEN {first_iso} and {last_iso} (inclusive)
    that a mainstream marketer would actually acknowledge in social content.

    Today's date (UTC):           {today_iso}
    Planning window FIRST date:   {first_iso}
    Planning window LAST date:    {last_iso}

    ═══════════════════════════════════════════════════════════════
    STRICT INCLUSION RULES — be conservative, empty arrays are fine
    ═══════════════════════════════════════════════════════════════

    INCLUDE for INDIA only if it qualifies on at least ONE of:
      • National gazetted public holiday (observed across the WHOLE country,
        not a single state) — e.g. Independence Day, Republic Day, Gandhi
        Jayanti, Diwali, Holi, Eid-ul-Fitr, Eid-ul-Adha, Christmas, Good Friday.
      • Major nationally-recognised Hindu / Muslim / Sikh / Christian festival
        broadly observed across multiple regions — Diwali, Holi, Raksha
        Bandhan, Janmashtami, Ganesh Chaturthi, Navratri, Dussehra, Maha
        Shivratri, Muharram, Guru Nanak Jayanti, Christmas, Easter, Good Friday.

    INCLUDE for USA only if it qualifies on at least ONE of:
      • Federal public holiday — New Year's Day, MLK Day, Presidents Day,
        Memorial Day, Juneteenth, Independence Day, Labor Day, Columbus /
        Indigenous Peoples Day, Veterans Day, Thanksgiving, Christmas.
      • Top-tier mainstream culturally-marketed day — Valentine's Day,
        St. Patrick's Day, Mother's Day, Father's Day, Halloween, Easter,
        Hanukkah, Super Bowl Sunday, Black Friday, Cyber Monday.

    EXPLICITLY EXCLUDE:
      ✗ Single-state or single-city public holidays.
      ✗ Regional / niche festivals observed only in one state.
      ✗ "National X Day" novelty days (National Pizza Day, etc).
      ✗ UN / WHO international observance days.
      ✗ Minor religious vrats / partial-day rituals.
      ✗ Anything you cannot ground in a reliable source.

    Other rules:
      - Multi-day festivals: list ONCE on the most important observance date
        in the window (day 1 OR final day, whichever is more publicly observed).
      - Return EMPTY array when nothing qualifies — do NOT pad.

    Return STRICTLY this JSON (no markdown, no code fences):
    {{
      "window_start": "{first_iso}",
      "window_end":   "{last_iso}",
      "festivals": [
        {{
          "date":          "YYYY-MM-DD",
          "country":       "india" | "usa",
          "name":          "Diwali",
          "type":          "national_holiday|major_festival|federal_holiday|major_observance",
          "note":          "one-line context for the copywriter"
        }}
      ]
    }}
    """
    result = _call_agent(
        "CULTURAL_CALENDAR_WINDOW",
        prompt,
        temperature=0.2,
        web_search=True,
    )
    if isinstance(result, dict) and not result.get("error"):
        # Normalize: ensure festivals is a list
        if not isinstance(result.get("festivals"), list):
            result["festivals"] = []
        _PLANNING_WINDOW_CACHE[cache_key] = result
        logger.info(
            f"[CULTURAL_WINDOW] cached {cache_key} — {len(result['festivals'])} festival(s) in window"
        )
        return result
    # Failure is non-fatal — empty window so planner skips festival injection.
    return {
        "window_start": first_iso,
        "window_end":   last_iso,
        "festivals":    [],
        "error": result.get("error") if isinstance(result, dict) else "unknown",
    }


def _format_planning_window_calendar_for_prompt(cal: dict) -> str:
    """Compact prompt block for the planner agent."""
    if not cal or cal.get("error"):
        return "(no planning-window calendar data available — skip festival injection)"
    festivals = cal.get("festivals") or []
    if not festivals:
        return f"No major festivals in window [{cal.get('window_start','?')} .. {cal.get('window_end','?')}]."
    lines = [f"Window [{cal.get('window_start','?')} .. {cal.get('window_end','?')}] — {len(festivals)} festival(s):"]
    for f in festivals:
        lines.append(
            f"  - {f.get('date','?')} ({f.get('country','?').upper()}) "
            f"{f.get('name','?')} [{f.get('type','?')}]: {f.get('note','')}".rstrip(": ")
        )
    return "\n".join(lines)


# Triggers that flip the researcher into web-grounded mode. Time-sensitive
# vocabulary in the refined brief means the model needs fresh sources to
# avoid making things up. Conservative on purpose — only fires when the
# brief actually asks for current/recent/news-style content.
_WEB_TRIGGER = re.compile(
    r"\b(latest|recent|recently|this\s+week|this\s+month|today|yesterday|"
    r"news|update[ds]?|announc(?:e|ed|ement|ements)|launch(?:ed|es|ing)?|"
    r"release[ds]?|releasing|newly|breaking|trending|"
    r"q[1-4]\s*20\d{2}|20\d{2})\b",
    re.IGNORECASE,
)


def _gating_needs_web(refined_brief: str) -> bool:
    """Return True if the refined brief asks for time-sensitive info.

    The researcher only needs web grounding when the brief is about recent
    events, launches, news, or specific recent timeframes. Brand-only
    campaigns and opinion pieces don't benefit — and grounded calls cost
    money + add latency, so we skip them when not needed.
    """
    return bool(_WEB_TRIGGER.search(refined_brief or ""))


def _build_offline_research_prompt(refined_brief, user_context="", cultural_calendar=None):
    """Original researcher prompt — used when web grounding is not needed."""
    cal_block = _format_cultural_calendar_for_prompt(cultural_calendar) if cultural_calendar else "(not available)"
    return f"""
    You are a Research Analyst feeding a downstream Content Agent.
    Your job is NOT to re-summarize what the Refiner already produced.
    Your job is to (a) sharpen the angles, (b) protect downstream agents from
    hallucinating numbers or trends, and (c) pick the single product edge worth
    leading with.

    REFINED BRIEF (already source-tagged by upstream refiner):
    {refined_brief}

    USER / BRAND CONTEXT (DNA + uploaded docs):
    {user_context}

    LIVE CULTURAL CALENDAR (today + tomorrow, India + USA — fetched live
    via web search; safe to reference because already grounded):
    {cal_block}

    Use the cultural calendar ONLY if it's relevant to the brief or the
    audience. Do NOT force a festival mention into an unrelated brief.
    When relevant, you may surface it in `angles_to_test` as a topical
    angle ("tie the post to {{festival}}") or in `trending_context` as
    cultural backdrop.

    ### GROUNDING RULES (STRICT)
    - You have NO web access. You cannot know 2025/2026 statistics, competitor
      launches, or current news. If a claim is not supported by the refined
      brief, the DNA, or a [doc: filename], tag it [speculative].
    - Every factual sentence in your output must end with exactly ONE tag:
      [user brief] | [DNA: field_name] | [doc: filename] | [speculative] | [inference].
      Use [inference] when you are reasoning FROM tagged facts (e.g. combining
      two DNA fields into an audience segment).
    - NEVER emit a number, percentage, or dollar figure with [speculative] or
      [inference]. If you have no grounded number, write the qualitative claim
      instead. Numbers belong ONLY under [user brief], [DNA: ...], or [doc: ...].

    ### FIELDS TO PRODUCE

    1. target_audience — 1-2 segments MOST likely to engage. Concrete (role,
       company size / life stage, pain trigger). One sentence per segment,
       each source-tagged.

    2. trending_context — 1-3 sentences on the market/category context. If you
       have no grounded source, write a conservative qualitative observation
       and tag [speculative]. Do NOT invent percentages.

    3. problem_solving_opportunity — the specific friction this post should
       name. Tied to at least one DNA or doc tag when possible.

    4. company_product_analysis — the ONE edge worth leading with (unique
       capability, proof point, or positioning). Tagged.

    5. angles_to_test — exactly 3 distinct strategic angles the content agent
       can A/B. Each is a short directive sentence, not a headline. They must
       be meaningfully different (e.g. contrarian vs. story-led vs. data-led).

    6. do_not_claim — up to 5 concrete things the content agent MUST NOT say
       because we cannot verify them. Examples: specific user counts, ranking
       claims, fabricated client names, comparison superlatives. If refined
       brief / DNA / docs give no grounding for a tempting claim, add it here.

    7. grounding_confidence — overall label:
       • "grounded"    — majority of facts come from [user brief] / [DNA] / [doc]
       • "partial"     — mix of grounded + inference
       • "speculative" — mostly inference/speculative; content agent should
         stay qualitative and avoid numbers

    Return STRICTLY this JSON (no markdown, no code fences):
    {{
        "target_audience": "...",
        "trending_context": "...",
        "problem_solving_opportunity": "...",
        "company_product_analysis": "...",
        "angles_to_test": ["...", "...", "..."],
        "do_not_claim": ["...", "..."],
        "grounding_confidence": "grounded | partial | speculative"
    }}
    """


def _build_grounded_research_prompt(refined_brief, user_context="", cultural_calendar=None):
    """Always-on web-grounded researcher prompt.

    Strategy is intent-driven: the LLM reads the brief + DNA and decides
    what to actually search for. Always finds trending hashtags + keywords
    for social-media reach. Brand-focused briefs also pull company +
    competitor news. Topic-focused briefs pull topic news.
    """
    from datetime import datetime, timedelta
    today_iso = datetime.utcnow().strftime("%Y-%m-%d")
    seven_days_ago_iso = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    has_dna = bool(user_context and user_context.strip() and "(no brand knowledge attached" not in user_context)
    cal_block = _format_cultural_calendar_for_prompt(cultural_calendar) if cultural_calendar else "(not available)"

    return f"""
    You are a Research Analyst feeding a downstream Content Agent.
    Today's date is {today_iso}.

    You HAVE access to Google Search via the google_search tool.
    Use it aggressively to ground EVERY factual claim in real, recent
    sources. Do NOT rely on training-data memory for anything time-sensitive.

    ═══════════════════════════════════════════════════════════════
    INPUTS
    ═══════════════════════════════════════════════════════════════
    REFINED BRIEF (the user's intent — primary source of truth):
    {refined_brief}

    USER / BRAND CONTEXT (DNA + uploaded docs — supporting knowledge):
    {user_context}

    LIVE CULTURAL CALENDAR (today + tomorrow, India + USA — already
    fetched, do not re-search):
    {cal_block}

    DNA attached: {"yes" if has_dna else "no"}

    ═══════════════════════════════════════════════════════════════
    PART 1 — MAIN RESEARCH (intent-driven, depends on brief)
    ═══════════════════════════════════════════════════════════════
    Decide what to search BASED ON THE BRIEF:

    A. If the brief is about a TOPIC / NEWS / TREND (e.g. "latest AI
       updates", "marketing trends", "industry news"):
         • Search for the most recent news, launches, and developments on
           that specific topic in the last 7 days.
         • Pull from primary sources (company blogs, press releases) when
           possible.
         • DO NOT pivot to brand promotion — stay on the topic.

    B. If the brief is about the USER'S OWN PRODUCT / COMPANY (DNA
       attached AND the brief is about their product, launch, feature,
       milestone):
         • Search for fresh angles + supporting context for the product.
         • ALSO search for COMPETITOR news in the last 7 days (look at DNA
           overview/tagline to identify the category, then find who else
           operates in it). Surface this under competitor_news.
         • Surface DNA-grounded facts with [DNA: …] tags; web-grounded
           facts with [src:N] tags.

    C. If the brief is BRAND + TOPIC HYBRID (e.g. "our take on the latest
       AI trends"):
         • Combine A and B. Topic news for the angle; DNA + competitor
           news to position the brand's POV.

    Pick whichever of A/B/C applies and produce trending_context,
    company_product_analysis, problem_solving_opportunity, and (when B)
    competitor_news from the main-research searches.

    ═══════════════════════════════════════════════════════════════
    PART 2 — AUXILIARY RESEARCH (always-on, runs EVERY call,
    independent of the main brief topic)
    ═══════════════════════════════════════════════════════════════
    These four items run on EVERY research call regardless of what
    Part 1 looks like. Even if the brief is purely brand-focused
    (Strategy B), you still do all four. They give the downstream
    content agent fresh cultural + discoverability context for every post.

    AUX-1. CULTURAL CALENDAR — already pre-fetched above. Do NOT
           re-search. Read the calendar block and decide whether any
           listed events fit the brief / audience for inclusion in
           angles_to_test or trending_context.

           ALSO — fire a festival_alert WHEN AND ONLY WHEN ALL of these
           are true:
             • The calendar shows a NAMED CULTURAL, RELIGIOUS, OR
               NATIONAL festival / public holiday for India or USA today
               or tomorrow (e.g. Diwali, Christmas, Eid, Ramadan,
               Ganesh Chaturthi, Rakshabandhan, Guru Purnima, Chinese
               New Year, Thanksgiving, Independence Day, Republic Day,
               Memorial Day, Labour Day, Onam, Pongal, Holi, Navratri,
               Bhai Dooj, Karva Chauth). Type MUST be one of
               "national_holiday" / "major_festival" / "federal_holiday" /
               "major_observance" AND the festival_name MUST be a
               widely-recognised proper noun; AND
             • The user's brief does NOT already mention that festival
               by name or by theme.

           NEVER fire a festival_alert for ANY of these — they are NOT
           festivals even if the calendar mentions them:
             • Admission windows, intake seasons, application deadlines
               ("2026 UK intake open", "Fall admissions", "MS applications")
             • Product launches, feature releases, version bumps
             • Sales, discount events, promotional windows ("summer sale",
               "Black Friday", "Cyber Monday", "end-of-season sale")
             • Fiscal periods (Q1/Q2/Q3/Q4, "year-end", "tax season")
             • Awareness weeks / months ("Cybersecurity Awareness Month",
               "Pride Month" — these are not FESTIVALS in the sense we
               want here; they may inspire content but not a festival
               variant)
             • Generic "new year" hooks tied to admissions or business
               cycles ("2026 intake", "2026 planning season")
             • Product-launch anniversaries, company milestones

           The alert flags the moment for the downstream copywriter so
           it can add an EXTRA festival-themed variant the user might
           have forgotten about ("today is Diwali — I forgot, let me
           post a Diwali greeting"). If unsure whether an event is a
           real cultural festival, DEFAULT TO EMPTY — false positives
           are worse than misses.
           Populate the `festival_alerts` array (see schema). Empty
           array on quiet days. Multiple alerts allowed if multiple
           qualifying festivals fall in the window.

    AUX-2. TRENDING TOPICS — broader topics currently moving in the
           user's industry / audience space (NOT just the brief's
           specific subject). Examples:
             • Marketing-analytics brief → search "trending topics in
               marketing analytics this week"
             • AI-news brief → search "what AI topics are getting
               attention on social media this week"
             • B2B SaaS brief → search "B2B SaaS topics trending on
               LinkedIn this week"
           Emit 3-7 short phrases under trending_topics. Grounded only.

    AUX-3. TRENDING HASHTAGS — currently used in conversations about
           the topic / industry / audience. Search Twitter/X trending,
           LinkedIn trending, niche hashtag aggregators. Format
           "#WithHash". Emit 3-10 under trending_hashtags.

    AUX-4. TRENDING KEYWORDS — keywords + phrases people use when
           discussing this topic right now (for SEO / SMO
           discoverability). Emit 3-10 under trending_keywords.

    LITERAL-QUERY RULE (READ BEFORE BUILDING SEARCHES)
    If the brief contains a specific entity, product name, person,
    event, or phrasing that the user explicitly wants researched,
    pass that string VERBATIM as one of your google_search queries
    BEFORE doing any paraphrased queries. The user's wording is
    the ground truth for intent.
    Examples:
      • Brief says "research Gemini 3.5 release" → one query MUST be
        the literal string "Gemini 3.5 release" (plus a date qualifier
        if the brief mentions one).
      • Brief says "what did Sundar Pichai say at I/O" → one query
        MUST be "Sundar Pichai Google I/O" (verbatim person + event).
      • Brief says "Spenzo Pulse launch" → one query MUST be
        "Spenzo Pulse launch" (verbatim product name).
    Do NOT replace the user's wording with synonyms or category
    terms. Their phrasing is intentional — search it as written.

    Run 3-6 distinct google_search queries total covering both PART 1
    and the auxiliary items (combine where you can — one search for
    "{{topic}} news this week" can cover both A and AUX-2 if it returns
    a related-topics roundup). Common query shapes:
      • "<verbatim string from brief>"  ← MANDATORY when brief is specific
      • "{{topic}} news this week site:*.com -roundup"
      • "{{competitor brand}} {{topic}} announcement {{month year}}"
      • "trending topics in {{industry}} {{month year}}"
      • "trending hashtags {{industry}} {{platform}} {{month year}}"
      • "{{topic}} keywords marketers using {{year}}"

    ═══════════════════════════════════════════════════════════════
    TIME WINDOW (STRICT — honor what the brief asked for)
    ═══════════════════════════════════════════════════════════════
    Read the brief and pick the tightest window that fits:
      • Brief says "today" / "this morning" / "right now" → only sources
        from {today_iso} (last 24 h). If nothing fresh enough exists,
        say so explicitly in trending_context. Do NOT fall back to
        7-day-old news and present it as "today".
      • Brief says "yesterday" → sources from the last 48 h.
      • Brief says "this week" / "past week" / "recently" / no explicit
        window → sources on or after {seven_days_ago_iso} (7-day default).
      • Brief says "this month" → sources from the last 30 days.
      • Brief says a specific date or month/year (e.g. "Q2 2026 launches",
        "November 2025") → match that exact window.
    • Record the publication date for every source you cite.
    • Older sources allowed ONLY as labelled background context — never
      cited as "fresh news".

    ═══════════════════════════════════════════════════════════════
    CITATION RULES (CRITICAL — anti-hallucination)
    ═══════════════════════════════════════════════════════════════
    • Every concrete fact in trending_context, target_audience,
      problem_solving_opportunity, company_product_analysis, and
      competitor_news MUST carry an inline [src:N] marker pointing to
      sources[].
    • Numbers, dates, product names, and company names need [src:N] tags.
    • Brand/DNA-derived claims use [DNA: field] / [doc: file] / [user brief].
    • Trending hashtags/keywords must be grounded — if you can't ground
      a hashtag in a real source, drop it. Better to return 2 grounded
      hashtags than 10 invented ones.
    • Claims you cannot ground go into do_not_claim — never speculate.

    ═══════════════════════════════════════════════════════════════
    OUTPUT FIELDS
    ═══════════════════════════════════════════════════════════════
    1. target_audience — 1-2 specific segments most likely to engage.
       Role titles + company size / life stage + pain trigger. Tagged.

    2. trending_context — 1-4 sentences of fresh news / launches /
       trends relevant to the brief. Every factual sentence has [src:N].
       If nothing fresh exists, say so explicitly.

    3. problem_solving_opportunity — the specific friction this post
       should name. Tagged.

    4. company_product_analysis — the ONE edge worth leading with.
       Brand context: [DNA: …]. Cross-topic angles: [src:N].

    5. angles_to_test — exactly 3 distinct strategic angles. Meaningfully
       different (contrarian / story-led / data-led / news-led / cultural-
       moment-led / etc.). Each is a directive sentence, not a headline.

    6. do_not_claim — up to 5 things the content agent MUST NOT say.

    7. trending_topics — array of 3-7 short phrases naming broader
       topics CURRENTLY trending in the user's industry / audience
       space (not just the brief's specific subject). These exist for
       editorial planning — even if the current post doesn't use them,
       the user can see what's in the air. Grounded only.

    8. trending_hashtags — array of 3-10 hashtags currently being used in
       conversations about this topic / audience. Format: "#WithHash".
       Order: most relevant first. Each grounded in a source. Empty array
       if nothing real surfaced — never invent.

    9. trending_keywords — array of 3-10 keywords / phrases that are
       trending in the topic discourse right now (for SEO/discoverability).
       Each grounded. Empty if nothing real.

    10. competitor_news — array of 0-5 items. ONLY populate when the brief
        is brand/product-focused AND DNA is attached. Each item: a recent
        competitor launch / move / announcement that's strategically
        relevant. Format: {{ "competitor": "...", "headline": "...",
        "src": N, "published": "YYYY-MM-DD" }}. Empty array otherwise.

    11. festival_alerts — array (0-3 items) of major NAMED cultural/
        religious/national festivals the copywriter should consider
        creating an EXTRA themed variant for. Fires ONLY when (a) the
        cultural_calendar shows a NAMED cultural/religious festival or
        national public holiday today or tomorrow (see AUX-1 for the
        strict include list and the explicit "NEVER fire for" list —
        admission windows, product launches, sales, fiscal periods,
        awareness months etc. are NOT festivals), AND (b) the user's
        brief does not already reference it.
        Each item:
          {{ "country": "india" | "usa",
             "festival_name": "Diwali",
             "when": "today" | "tomorrow",
             "date": "YYYY-MM-DD",
             "type": "national_holiday|major_festival|federal_holiday|major_observance",
             "mentioned_in_brief": false,
             "suggested_angle": "one-line nudge for the copywriter, e.g. 'short Diwali greeting tying brand voice to the festival'" }}
        Empty array when nothing qualifies.

    13. grounding_confidence — overall label:
        • "grounded"    — facts well-supported by sources[]
        • "partial"     — mix of fresh sources + DNA + qualitative inference
        • "speculative" — searches returned little fresh; content agent
          should stay qualitative and avoid numbers

    14. sources — array of objects, ONE per cited URL:
        {{ "id": 1, "url": "...", "title": "...", "published": "YYYY-MM-DD",
           "publisher": "..." }}

    15. referenced_entities — Per-entity homepages for list-style briefs.
        FIRES ONLY when the brief asks for a list / roundup / comparison
        of distinct named entities. Detect patterns like:
          • "top N <thing>", "best N <thing>", "list of <thing>"
          • "X vs Y", "compare X, Y, Z"
          • "tools / platforms / frameworks / books / channels / brands
             to follow / try / read / watch"
          • "newsletters / podcasts / repos / databases worth knowing"
        For each entity the post will name, run ONE additional Google
        search like "<entity name> official site" to find the entity's
        canonical homepage. Capture:
          {{ "name": "ChatGPT",
             "url": "https://chat.openai.com",
             "one_liner": "OpenAI's flagship conversational assistant." }}
        Include 5-15 entities depending on what the brief asks for
        (e.g. "top 10" → 10 entries). The copywriter will use these
        URLs verbatim when listing each entity in the post.
        For non-list briefs (product announcements, brand storytelling,
        thought-leadership), emit an empty array — do NOT force-fill.

    Return STRICTLY this JSON (no markdown, no code fences):
    {{
        "target_audience": "...",
        "trending_context": "...",
        "problem_solving_opportunity": "...",
        "company_product_analysis": "...",
        "angles_to_test": ["...", "...", "..."],
        "do_not_claim": ["...", "..."],
        "trending_topics": ["topic one", "topic two"],
        "trending_hashtags": ["#One", "#Two"],
        "trending_keywords": ["phrase one", "phrase two"],
        "competitor_news": [
            {{ "competitor": "...", "headline": "...", "src": 1, "published": "YYYY-MM-DD" }}
        ],
        "festival_alerts": [
            {{
                "country": "india",
                "festival_name": "Diwali",
                "when": "today",
                "date": "YYYY-MM-DD",
                "type": "major_festival",
                "mentioned_in_brief": false,
                "suggested_angle": "Short Diwali greeting tying brand voice to the festival of lights."
            }}
        ],
        "grounding_confidence": "grounded | partial | speculative",
        "sources": [
            {{ "id": 1, "url": "...", "title": "...", "published": "YYYY-MM-DD", "publisher": "..." }}
        ],
        "referenced_entities": [
            {{ "name": "ChatGPT", "url": "https://chat.openai.com", "one_liner": "OpenAI's flagship conversational assistant." }}
        ]
    }}
    """


def _research_agent(refined_brief, user_context="", cultural_calendar=None):
    """Agent 2: Research — always-on web-grounded research.

    The researcher reads the refined brief + DNA and dynamically decides
    what to search for: topic news, company news, competitor news, and
    always trending hashtags + keywords for social-media reach. Nothing is
    gated by keyword regex — the LLM picks the search strategy per brief.

    The offline researcher prompt is kept as a fallback for the (rare) case
    where the grounded call returns an error dict.

    `cultural_calendar` (optional dict from `_get_cultural_calendar()`) is
    a pre-fetched, day-cached lookup of festivals/holidays today + tomorrow
    in India and USA. Passed into the prompt so the researcher can weave
    relevant cultural moments into angles without re-searching.
    """
    logger.info("[RESEARCH] always-grounded (intent-driven search strategy)")
    result = _call_agent(
        "RESEARCHER",
        _build_grounded_research_prompt(refined_brief, user_context, cultural_calendar),
        temperature=0.3,
        web_search=True,
    )
    # Fall back to the ungrounded prompt only if the grounded call genuinely
    # failed (network blip, Gemini quota hit). Successful but sparse results
    # are still preferred over the ungrounded version.
    if isinstance(result, dict) and result.get("error"):
        logger.warning(f"[RESEARCH] grounded call returned error — falling back to offline ({result.get('error')})")
        result = _call_agent(
            "RESEARCHER",
            _build_offline_research_prompt(refined_brief, user_context, cultural_calendar),
            temperature=0.7,
            web_search=False,
        )
    # ─────────────────────────────────────────────────────────────────
    # SAFETY NET — strip false-positive festival_alerts. The prompt
    # already forbids admission-window / product-launch / sales /
    # awareness-month "festivals", but models slip. Drop any alert
    # whose festival_name looks like a marketing / business event
    # instead of a real named cultural/religious/national festival.
    # Rejected alerts get logged so we can tune the disallow list.
    # ─────────────────────────────────────────────────────────────────
    if isinstance(result, dict):
        _NON_FESTIVAL_TOKENS = {
            "intake", "admission", "admissions", "application",
            "applications", "deadline", "enrollment", "enrolment",
            "launch", "release", "rollout", "beta", "preview",
            "sale", "discount", "offer", "promo", "promotion",
            "black friday", "cyber monday",
            "end of season", "end-of-season", "clearance",
            "fiscal", "quarter", "q1", "q2", "q3", "q4",
            "year end", "year-end", "tax season",
            "awareness month", "awareness week",
            "planning season", "budget season",
            "product hunt", "kickstarter",
            "season opens", "season open",
        }
        alerts = result.get("festival_alerts") or []
        if alerts:
            kept, dropped = [], []
            for a in alerts:
                name = str((a or {}).get("festival_name") or "").strip().lower()
                if any(tok in name for tok in _NON_FESTIVAL_TOKENS):
                    dropped.append(name)
                else:
                    kept.append(a)
            if dropped:
                logger.info(
                    f"[RESEARCH] festival_alerts safety-net dropped "
                    f"{len(dropped)} non-festival entries: {dropped}"
                )
            result["festival_alerts"] = kept
    return result


def _content_agent(refined_brief, research, platforms, user_context="", cultural_calendar=None):
    """Agent 3 — v3 (Free-Style, Reference-Informed).

    Drops the rigid PRODUCT / SERVICE mode templates and the forced
    "first-5-words shocking stat" hook rule. Gives the model full context
    (refined brief + research + cultural calendar + DNA + KB) and a clear
    engagement goal, then lets it pick voice / structure / hook / CTA
    per variant. See content_style_references.md for the reference-post
    analysis this prompt is informed by.

    Variant keys are stable for downstream compatibility:
      • viral_reach      — visibility-flavored
      • high_interaction — comment-driven
      • follower_growth  — authority / depth
      • festival_variant — emitted ONLY when research.festival_alerts is
                           non-empty; omitted entirely otherwise

    Platform char caps + hashtag caps are still enforced post-call in
    _apply_content_post_processing as a hard guarantee.
    """
    platforms_str = ", ".join(platforms)
    festival_alerts = (research or {}).get("festival_alerts") or []
    has_festival_alert = "yes" if festival_alerts else "no"
    cultural_calendar_block = (
        _format_cultural_calendar_for_prompt(cultural_calendar)
        if cultural_calendar
        else "(not available)"
    )
    research_json = json.dumps(research) if research is not None else "{}"

    prompt = f"""
    You are a senior social-media copywriter. Your goal: maximize REACH,
    FOLLOWERS, COMMENTS, and SAVES on the brand's actual social presence.
    Not shares. Not vanity likes. Real engagement that compounds over time.

    You have full creative freedom over voice, structure, hook style,
    CTA shape, and visual format. Pick what fits THIS brief, THIS brand,
    THIS platform, and THIS moment. There is no single "winning template" —
    the best posts match their brand's voice and the post's purpose.

    ═══════════════════════════════════════════════════════════════
    INPUTS — your full context
    ═══════════════════════════════════════════════════════════════

    BRAND PROFILE (voice, tone, values — your soft constraint):
    {user_context}

    REFINED BRIEF (the user's actual intent — primary source of truth):
    {refined_brief}

    ⚠️ THE REFINED BRIEF IS A STRATEGY DOCUMENT, NOT POST COPY.
    It contains labelled sections — USER GOAL, TOPIC, AUDIENCE, KEY MESSAGE,
    ANGLE, SUPPORTING POINTS, TONE, SOURCES REFERENCED, VISUAL HINT,
    CONSTRAINTS, USER INPUT QUALITY, ASSUMPTIONS MADE.
    These are instructions FOR YOU about how to write. They are NEVER content
    to copy into the post. The user must never see the literal strings
    "Visual hint:", "Audience:", "Key message:", "Tone:", "Supporting points:",
    "Angle:", "Topic:", "Sources referenced:", "Constraints:", "Assumptions
    made:", "User goal:", or any other labelled-section header in the
    generated post text. If you find yourself writing one, delete it.

    RESEARCH REPORT (everything the researcher found, including
    angles_to_test, do_not_claim, grounding_confidence, trending_topics,
    trending_hashtags, trending_keywords, competitor_news,
    festival_alerts, sources):
    {research_json}

    CULTURAL CALENDAR (today + tomorrow, India + USA):
    {cultural_calendar_block}

    PLATFORMS: {platforms_str}
    Festival alert active: {has_festival_alert}

    ═══════════════════════════════════════════════════════════════
    HARD RULES (NON-NEGOTIABLE)
    ═══════════════════════════════════════════════════════════════

    1. ANTI-HALLUCINATION.
       • Never emit any claim listed in research.do_not_claim.
       • Numbers / percentages / dollar figures / dates / customer counts /
         product names / company names must trace to the refined brief, the
         DNA, an uploaded doc tag, or research.sources. No tag = drop it.
       • If research.grounding_confidence == "speculative", you may NOT
         use numeric proof. Use named-scenario qualitative proof instead.

    2. CHAR CAPS (server truncates anything over — write to be read, not cut).
       Safety-buffered against each platform's true hard limit so a post
       from a free-tier (non-premium) account never gets rejected:
         LinkedIn:  ≤ 2800   (true cap 3000 · sweet spot 500-1500)
         Twitter/X: ≤ 270    (true free-tier cap 280 — write punchy)
         Facebook:  ≤ 2200   (first 480 chars above the "See more" line)
         Instagram: ≤ 2100   (true cap 2200 · first 125 chars above "more")

    3. HASHTAG CAPS (server enforces).
         LinkedIn:  0-5    (prestige brands use 0; growth brands use 3-5)
         Twitter/X: 0-2    (most brands use 0-1)
         Facebook:  0-3
         Instagram: 8-15   (engagement sweet spot, also the upper cap)

    4. NO SHARE-BAIT — banned across every variant:
         "share this", "tag someone who", "send this to your team",
         "quote this with your take", "RT if you agree", "spread the word"
       This account does NOT optimize for shares.

    5. DISTINCT ANGLES — each variant you produce for a given platform
       must map to a DIFFERENT entry in research.angles_to_test. Do not
       collapse two variants onto the same angle.

    6. BRAND VOICE — read brand_tone + brand_values from DNA. Match them.
       If the brand is technical-warm, write technical-warm. If playful,
       write playful. If formal-analytical (PwC-style), write that.
       Brand name does NOT need to appear in every variant — voice
       consistency matters more than name repetition. Do NOT open with
       "[Brand] is the leader in…" press-release voice. First-person
       "We're [verb]…" / "We just [verb]…" is fine.

    7. NO BUZZWORD CORPORATE-SPEAK. These words kill engagement on real
       social feeds — avoid unless the DNA literally requires them:
         empower / empowers / empowering
         democratize / democratizing
         leverage (as a verb)
         unlock (the power of / the potential)
         transform (your workflow / your business)
         accelerate (deployment / growth)
         unify (fragmented systems)
         seamlessly / seamless
         end-to-end (as filler)
         next-generation / next-gen
         best-in-class / world-class
         streamline (your workflow)
         drive (efficiency / growth / outcomes)
         revolutionize / game-changer / paradigm shift
       Write what the product actually does in plain English. "Drag your
       data sources onto a canvas" beats "empower your team to seamlessly
       unify fragmented systems."

    8. NO LAZY CTA. These specific shapes are banned because they read
       cliché and reduce real engagement:
         "Comment below."
         "Let us know in the comments."
         "We want to hear your vision."
         "Share your thoughts."
         "What are your thoughts?"
       If you ask a question, ask a SPECIFIC one tied to the reader's
       actual workflow / experience (see CTA MENU below).

    9. REAL URLS ONLY — NEVER USE PLACEHOLDER LINKS.
       Every URL you write in a post must be a REAL, working URL drawn
       from one of these sources (in priority order):

         (a) **USER LINKS** — URLs the user typed VERBATIM in their raw
             brief. The refiner extracted these into the USER LINKS
             section of the REFINED BRIEF above. HIGHEST priority — the
             user explicitly chose them. Use them exactly as written.

         (b) **research.referenced_entities[]** — for list / roundup /
             comparison briefs (e.g. "top 10 AI tools", "5 newsletters to
             read", "ChatGPT vs Claude vs Gemini"), the researcher has
             already looked up each named entity's OFFICIAL HOMEPAGE.
             Each entry is `{{name, url, one_liner}}`. When the post
             lists entities, use the entity URL verbatim alongside the
             name. Example format:
                "1. ChatGPT — https://chat.openai.com"
                "2. Claude — https://claude.ai"
                "3. Midjourney — https://midjourney.com"
             Do not paraphrase the name. Do not shorten the URL.

         (c) **research.sources[].url** — Google-grounded research
             citations. Use these for "further reading", "best overview
             for beginners", news citation, or any other "go deeper"
             link in the post.

         (d) **Brand DNA URL fields** visible in BRAND PROFILE above —
             `website_url`, `product_url`, `pricing_url`, `docs_url`,
             `blog_url`, `careers_url`, `social_links.*`. Use these for
             product CTAs, sign-up CTAs, careers posts, etc.

       BANNED — you must NEVER emit any of these placeholders:
         "[Insert Link 1]", "[Insert Link 2]", "[Insert Link 3]",
         "[Link 1]", "[Link]", "[Insert link here]", "[URL]",
         "[your website]", "[brand website]", "[product URL]",
         "[learn more here]", "(link)", "https://example.com",
         "https://placeholder.com", or any other bracketed / fake URL.

       Decision rule:
         • If you need N resource links and `research.sources` has ≥ N
           items, use the top N most relevant sources verbatim.
         • If you have fewer real URLs than you wanted to list, WRITE
           FEWER ITEMS — do NOT fill remaining slots with placeholders.
           A 1-link list with a real URL beats a 3-link list with two
           placeholders.
         • If you have NO real URL for a CTA, omit the CTA URL entirely
           and use a non-URL CTA from the CTA MENU instead (e.g.
           "When did you start learning AI?" rather than
           "Learn more here: [link]").

       Format URLs naked (no markdown link syntax — social platforms
       don't render markdown). Example: "Take a look → https://spenzo.io"
       NOT "Take a look [here](https://spenzo.io)".

    ═══════════════════════════════════════════════════════════════
    SOFT GUIDANCE — pick what fits the brief, ignore the rest
    ═══════════════════════════════════════════════════════════════

    HOOK MENU (real-brand patterns observed across LinkedIn + X)
    Pick a DIFFERENT one for each variant — do not repeat hook style:
      • Milestone / announcement       ("We're opening our second center…")
      • Time-stamped news              ("Last week at Google I/O…")
      • Open audience question         ("What's the one skill people should be building right now?")
      • Stat + claim                   ("92% of workers feel cognitive strain.")
      • Bold POV                       ("Click-based measurement undervalues video.")
      • Human story lead               ("Donald Overton lost his sight from a blast in Iraq…")
      • Pain-then-solve                ("Most marketing teams have a measurement gap. They just don't know how big it is.")
      • Product reveal + themed emoji  ("Nano Banana for video is here 🍌🎥")
      • Punchy contrast                ("One agent is easy. Enterprise AI is not.")

    CTA MENU (real-brand patterns observed)
    Match CTA shape to variant intent — do not force "comment your letter":
      • Arrow + URL                    ("Take a look inside → https://…")
      • Direct verb                    ("Learn more →", "Register now:", "Reserve your spot:")
      • Open question                  ("When did you join LinkedIn?", "What's one interview take you stand by?")
      • Soft prompt for replies        ("What have you learned from your most popular posts?")
      • Personal close                 ("Congratulations to all the recent grads…")
      • Thread continuation (X only)   ("Here's how it works ↓")

    FORMATTING — use freely; avoid forbidden patterns
      USE:
        • Single-line paragraphs with white space rhythm
        • Emoji bullets (✅ ⚡ 🔩 🧠 1️⃣ 2️⃣ 3️⃣)
        • Numbered lists
        • Pull-quotes from the brief / research
        • Unicode bold 𝐀𝐁𝐂 — ONLY when DNA brand_tone signals a tactical /
          marketing voice. Skip for prestige / corporate tones.
      AVOID:
        • Arrow bullets ↳  (zero of 36 reference posts use these)
        • "Click the link in bio" — only valid on Instagram
        • Press-release "[Brand] is the leader in / [Brand] helps / [Brand] is…"
        • Walls of text without breaks

    PLATFORM-NATIVE PATTERNS (only when they fit)
      LinkedIn:
        • Line-break paragraphs for readability
        • Hashtags on a final line if used at all (or none for prestige)
        • Long-form OK up to 1500 chars; engagement falls past ~2000
      Twitter/X:
        • Punchy. Often under 400 chars.
        • `↓` arrow at end signals a continuing thread — use when the
          brief has enough depth for a 2-3-tweet thread
        • @-mention partner brands / own sub-products / executive voices
          when relevant for reach amplification
        • Sparse emoji (1-2 themed) for corporate; heavier (3-5) for tactical
      Facebook:
        • Hook value MUST land in the first 480 chars (before "See more")
        • Conversational, story-led, photo-paired
        • Question CTAs still work here (unlike LinkedIn which has
          moved past them)
        • 0-2 hashtags
      Instagram:
        • First 125 chars MUST carry the hook + core promise
        • Visual rhythm via line breaks + decorative emoji
        • Hashtag wall at end (8-15 typical), separated from copy by
          2-3 line breaks
        • Captions cannot carry clickable links — push to bio link

    ═══════════════════════════════════════════════════════════════
    VARIANT STRUCTURE — what to emit per platform
    ═══════════════════════════════════════════════════════════════

    For each platform in {platforms_str}, produce 3 variants tied to
    DIFFERENT entries in research.angles_to_test. Variant keys are stable
    for downstream compatibility — the intent is fixed, the form is yours:

      • viral_reach      — VISIBILITY-flavored variant. Goal: saves + follows
                           via an angle the broadest audience can grasp.
                           NOT share-bait. NOT click-bait. Strong hook,
                           concrete proof, save-driving CTA.

      • high_interaction — COMMENT-DRIVEN variant. Goal: genuine replies
                           from people who've lived the brief's subject.
                           Hook frames a decision / take / scenario the
                           reader has an opinion on. CTA invites a real
                           reply — NOT "comment A or B and one reason".

      • follower_growth  — AUTHORITY / DEPTH variant. Goal: profile click →
                           follow + save. Promise of more value to come
                           from the brand. Frameworks, sharp POV,
                           signature voice.

    Pick a DISTINCT angle and a DISTINCT hook style for each.

    ═══════════════════════════════════════════════════════════════
    FESTIVAL VARIANT (conditional)
    ═══════════════════════════════════════════════════════════════

    Festival alert active: {has_festival_alert}

    If "yes" (research.festival_alerts is non-empty), ALSO emit a
    `festival_variant` per platform — a short voice-faithful festival
    post tied to the brand. Read the festival_alert's suggested_angle
    for guidance. Keep it under each platform's "sweet spot" length
    (festival posts read better short). Same hard rules apply.

    If "no", OMIT the festival_variant key entirely from the output
    (do not emit it as null or empty string).

    ═══════════════════════════════════════════════════════════════
    COMPANY MODE (informs tone, not template)
    ═══════════════════════════════════════════════════════════════

    Glance at the BRAND PROFILE + BRIEF and decide whether THIS post is:
      • "product"  — about a usable app / platform / SaaS / tool
      • "service"  — about expertise / done-for-you work / case study
      • "hybrid"   — DNA covers both, brief sits in between
      • "topic"    — cross-topic post; brand contributes voice only
    Tag this in `mode` for downstream / analytics signals. Variants
    follow voice, not this label.

    ═══════════════════════════════════════════════════════════════
    Return STRICTLY this JSON (no markdown, no code fences):
    ═══════════════════════════════════════════════════════════════
    {{
        "mode": "product | service | hybrid | topic",
        "mode_reason": "one sentence citing DNA / brief",
        "recommendation": {{
            "best_variant": "viral_reach | high_interaction | follower_growth | festival_variant",
            "reason": "which variant is strongest for this moment and why"
        }},
        "content": {{
            "<platform_name>": {{
                "viral_reach":      "...",
                "high_interaction": "...",
                "follower_growth":  "...",
                "festival_variant": "..."
            }}
        }}
    }}
    OMIT the `festival_variant` key entirely when Festival alert active = "no".
    Do NOT omit any platform from {platforms_str}.
    """
    data = _call_agent("COPYWRITER", prompt)
    if "content" in data:
        data["content"] = {k.lower(): v for k, v in data["content"].items()}
        data["content"] = _apply_content_post_processing(data["content"])
    return data


def _linkedin_visualist_agent(linkedin_content, refined_brief, primary_color="#FF4500", domain_name="pipelyt.com"):
    """Agent 4: Visualist — produces 3 HIGH-FIDELITY image prompts anchored to
    the campaign subject. Every image must depict WHAT THE PRODUCT DOES or WHAT
    PAIN IT SOLVES.

    Uses 6 narrative devices, campaign anchor concept, and text_zone / logo_corner
    / cta_corner coordinates for the compositor.
    """
    prompt = f"""
    You are a SENIOR CREATIVE DIRECTOR generating HIGH-FIDELITY image prompts
    for marketing assets (Gemini 2.5 Flash Image).

    INPUT:
    1. REFINED CAMPAIGN BRIEF: "{refined_brief}"
    2. PRIMARY POST CONTENT: "{linkedin_content}"
    3. BRAND PRIMARY COLOR: {primary_color}
    4. DOMAIN NAME FOR CTA BUTTON (USE THIS EXACT STRING): "{domain_name}"

    ---

    ### STEP 0 — CAMPAIGN ANCHOR (DO THIS FIRST)
    Extract the CAMPAIGN ANCHOR: the concrete visual subject shared by all 3 variants.
    Source from (priority order):
    1. The specific product name and function in the refined brief
    2. The user/company context — especially Overview and products
    3. A visual metaphor that makes the product's core mechanism visible

    GENERIC ANCHOR ARCHETYPES (pick the one that fits):
    A. DOCUMENT PROCESSING — stacks of documents flowing through a visible filter into a clean sorted output
    B. DATA CONVERGENCE — multiple coloured data streams converging into a single clean output
    C. VISUAL INSPECTION — scan beam or AI highlight revealing hidden detail on a real-world subject
    D. WORKFLOW AUTOMATION — branching flow of nodes/triggers/actions resolving into a final outcome
    E. INTEGRATION NETWORK — product as central hub connected to 3-5 platform icons
    F. BEFORE/AFTER TRANSFORMATION — pain state and solved state side by side

    BAD anchors (do not use):
    - "business people in a modern office talking"
    - "a 3D chip or lens-shaped gadget floating in space"
    - "a laptop on a desk with sunshine"
    - "people smiling at a dashboard"

    ### STEP 0.5 — ANCHOR REUSE
    The same campaign anchor appears in all 3 variants. Variants differ only in NARRATIVE DEVICE.

    ---

    ### I. THE 6 NARRATIVE DEVICES (choose 3; anchor stays constant)

    1. BEFORE/AFTER SPLIT — vertical/diagonal split. Left = pain state (chaos, red, clock).
       Right = solved state (clean, calm, fast, green). Product lives at the junction. CTA: BOTTOM-LEFT.

    2. MOMENT OF USE — photo-grade scene of ONE target user at the exact moment of gaining value.
       Facial expression shows emotional payoff. Background is user's real context. CTA: BOTTOM-RIGHT.

    3. TRANSFORMATION ARROW — before-form on left processed through central apparatus (the product)
       emerging as after-form on right. Apparatus is the hero, sits center, in brand color. CTA: BOTTOM-RIGHT.

    4. EDITORIAL HERO — high-fidelity photo of target user in their real workplace, mid-workflow.
       Domain MUST depict the campaign domain (hospital, factory, HR office — not generic boardroom).
       CTA: BOTTOM-LEFT.

    5. METAPHORICAL STILL-LIFE — single cinematic still-life object that IS the metaphor for the
       product's value. Must be unambiguously tied to the brief. CTA: BOTTOM-RIGHT. NO people.

    6. INTEGRATION FLOW — product as glowing central hub in brand color, connected to 3-5
       recognizable platform icons. Use ONLY for integration/connector/workflow products. CTA: BOTTOM-RIGHT.

    MANDATORY: Variant 1 uses device #1 or #4. Variant 2 uses #2, #3, or #6. Variant 3 picks
    from remaining unused devices.

    ---

    ### II. IMAGE STYLE RULES
    - NO sci-fi, NO glowing robot heads, NO neon chaotic junk, NO decorative abstractions.
    - PREFERRED: Realistic professional people, modern office interiors, clean 3D hardware,
      sharp software dashboards (shapes/icons only — NO text labels), crystalline 3D data-charts.
    - Quality: 8k, cinematic lighting, shallow depth of field, premium textures.

    ### II.b PURE BACKGROUND ONLY — CRITICAL RULE
    Image generation is PURELY a background. Logo + headline + sub-heading + CTA pill are
    composited onto the image AFTER generation using Pillow.

    ABSOLUTE PROHIBITIONS:
    1. NO TEXT anywhere in the image — no headlines, no dashboard labels, no chart axes,
       no tab names, no column headers, no background signage, no word clouds.
    2. NO LOGOS — do NOT draw any brand logo or mark anywhere.
    3. NO CTA BUTTONS OR PILL-SHAPED ELEMENTS.
    4. RESERVED ZONES (must be pure uniform background):
       - TOP 30% of canvas (full width) — reserved for logo + headline + sub-heading overlay.
         Scene must START at y=30% downward. NO faces, objects, screens in top 30%.
       - BOTTOM-LEFT corner (15% wide × 12% tall) — reserved for CTA pill.
       - BOTTOM-RIGHT corner (15% wide × 12% tall) — reserved for CTA pill.

    Place all creative composition in the MIDDLE BAND from y=30% to y=88%.

    ---

    ### III. TEXT ZONE AND LOGO CORNER MAPPING

    | Device                  | text_zone         | logo_corner  | cta_corner     |
    | ----------------------- | ----------------- | ------------ | -------------- |
    | BEFORE/AFTER SPLIT      | TOP_LEFT_PANEL    | TOP_RIGHT    | BOTTOM_LEFT    |
    | MOMENT OF USE           | TOP_RIGHT_PANEL   | TOP_LEFT     | BOTTOM_RIGHT   |
    | TRANSFORMATION ARROW    | TOP_CENTER_BAND   | TOP_LEFT     | BOTTOM_RIGHT   |
    | EDITORIAL HERO          | LEFT_GLASS_CARD   | TOP_RIGHT    | BOTTOM_LEFT    |
    | METAPHORICAL STILL-LIFE | TOP_CENTER_BAND   | TOP_LEFT     | BOTTOM_RIGHT   |
    | INTEGRATION FLOW        | TOP_CENTER_BAND   | TOP_RIGHT    | BOTTOM_LEFT    |

    ---

    ### IV. OUTPUT

    Text fields for the compositor (rendered by font engine — NEVER drawn in the image):
    - heading: maximum 4 words, simple common English
    - sub_heading: maximum 6 words, simple common English
    - highlight_words: exactly 2 words from the heading that render in brand color

    {{
      "campaign_anchor": "Single sentence describing the concrete visual subject.",
      "variants": [
        {{
          "platform": "Shared",
          "narrative_device": "BEFORE/AFTER SPLIT | MOMENT OF USE | TRANSFORMATION ARROW | EDITORIAL HERO | METAPHORICAL STILL-LIFE | INTEGRATION FLOW",
          "name": "Short creative concept name",
          "heading": "4-word outcome hook",
          "sub_heading": "6-word benefit line",
          "highlight_words": ["Word1", "Word2"],
          "text_zone": "TOP_LEFT_PANEL | TOP_RIGHT_PANEL | TOP_CENTER_BAND | LEFT_GLASS_CARD",
          "cta_corner": "BOTTOM_LEFT | BOTTOM_RIGHT",
          "logo_corner": "TOP_LEFT | TOP_RIGHT",
          "generation_prompt": "CAMPAIGN ANCHOR: [restate anchor]. STRICT SQUARE 1:1, 1024x1024, ultra-high resolution. NARRATIVE DEVICE: [describe how it frames the anchor]. VISUAL: [scene, objects, lighting]. TEXT ZONE: Reserve [zone location] as clean uniform [dark/brand color/soft neutral] — NO visual elements there. LOGO: Place nothing at [TOP-LEFT or TOP-RIGHT] — compositor handles it. CTA CORNER: Leave [BOTTOM-LEFT or BOTTOM-RIGHT] empty background. NO TEXT RULE (ABSOLUTE): Do not render any written words anywhere — no headlines, subheadings, CTA text, dashboard labels, chart axes, background signage, billboards, whiteboards. Dashboard elements use bars/shapes/icons only. LIGHTING: Cinematic, premium, magazine-quality."
        }},
        {{ ... }},
        {{ ... }}
      ]
    }}

    RETURN ONLY JSON. No explanation.
    """
    data = _call_agent("VISUALIST", prompt)

    # Normalize output: new shape is {campaign_anchor, variants:[...]}
    # Older model runs may return a flat list — handle both.
    campaign_anchor = ""
    variants = None
    if isinstance(data, dict):
        campaign_anchor = (data.get("campaign_anchor") or "").strip()
        variants = data.get("variants")
    if isinstance(data, list):
        variants = data

    if not isinstance(variants, list):
        logger.warning(f"Visualist returned unexpected shape: {type(data).__name__}. Returning raw for fallback.")
        return data

    # Safety net: stamp campaign_anchor and domain into every concept.
    for concept in variants:
        if not isinstance(concept, dict):
            continue
        gp = concept.get("generation_prompt", "") or ""
        if campaign_anchor and campaign_anchor.lower() not in gp.lower():
            gp = f"CAMPAIGN ANCHOR (subject of this image): {campaign_anchor}. " + gp
        if gp and domain_name and domain_name not in gp:
            gp = (
                gp.rstrip(". ")
                + f'. CTA BUTTON TEXT (LOCKED, VERBATIM): "{domain_name}".'
            )
        concept["generation_prompt"] = gp
        concept["domain"] = domain_name
        concept["campaign_anchor"] = campaign_anchor
        # Backwards-compat: older orchestrator reads `layout`
        if "layout" not in concept and concept.get("narrative_device"):
            concept["layout"] = concept["narrative_device"]

    return variants


def _visual_critic_agent(image_bytes, original_prompt, refined_brief, primary_color="#FF4500"):
    """Agent 5: Audits the RAW (pre-overlay) background image for quality.

    Text rendering is handled by the compositor (font engine), so the critic
    is told to IGNORE text quality — that is guaranteed separately.
    """
    if not client:
        return {"is_valid": True}

    prompt = f"""
    You are the Lead Visual Quality Auditor for Pipelyt campaign images.
    This image is the RAW visual background BEFORE a headline/CTA text
    overlay is composited onto it in post-processing.

    1. REFINED BRIEF: "{refined_brief}"
    2. IMAGE GENERATION PROMPT: "{original_prompt}"

    QUALITY CHECKLIST (audit ONLY these):
    - CAMPAIGN ANCHOR FIDELITY: Does the image unmistakably depict the campaign
      subject? Fail if the image is generic stock imagery.
    - TEXT ZONE RESPECT: Is there a clean empty area at the top 30% for the
      text overlay? Fail if the scene has objects/faces in the top 30%.
    - TEXT LEGIBILITY (HARD FAIL): Every visible text element must be
      crisp and readable. Fail (rating ≤ 4) if ANY of these apply:
        • Garbled / misspelled / nonsense words ("Projectsd", "shifel",
          "Optricsalion", "Genna show")
        • Blurry, smeared, or low-contrast small text
        • Chat bubbles, speech balloons, Q&A transcripts, conversation
          logs (these are forbidden — should be a dashboard instead)
        • Long sentence-style copy inside the UI (anything > 4 words
          per element typically fails — flag it)
        • Visibly duplicated labels ("Home Home", "Meta Ads Meta Ads")
    - BRAND COLORS: Brand primary color ({primary_color}) should appear somewhere.
    - SHARPNESS: Is the scene sharp and in focus (not blurry or soft)?

    DO NOT audit: headline typos, sub-heading typos, CTA button text, logo
    placement — those are handled by the compositor after this step.

    Return STRICTLY in this JSON format:
    {{
        "rating_out_of_10": (int),
        "reason": "Summary focused ONLY on the items above.",
        "improvement_advice": "Specific visual fix if needed.",
        "is_valid": true | false
    }}

    Set `is_valid` to `false` if rating is 6 or lower.
    """
    try:
        res = client.models.generate_content(
            model='gemini-flash-lite-latest',
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(res.text)
    except Exception as e:
        logger.error(f"Visual Critic failed: {e}")
        return {"is_valid": True, "critique": "Audit skipped due to error"}


def _critic_agent(final_payload, campaign_brief, user_context=""):
    """Agent 6: Strategic Critic — verifies the full payload for growth fitness."""
    prompt = f"""
    You are the Senior Strategic Critic for the following user profile:
    {user_context}

    ORIGINAL BRIEF: "{campaign_brief}"
    GENERATED PAYLOAD: {json.dumps(final_payload, indent=2)}

    ### PIPELYT STYLE RULES (treat as ground truth)
    - ALL platforms use PLAIN TEXT. Bold Unicode (𝗔𝗕𝗖) is FORBIDDEN everywhere.
      Flag its PRESENCE as a critical error. Do NOT flag its absence.
    - Twitter/X: ≤ 240 chars, 0-1 hashtag, no emojis.
    - LinkedIn: ≤ 1500 chars, 3-5 hashtags, arrow (↳) bullets OK.
    - Facebook: ≤ 1200 chars, 1-3 hashtags.
    - Instagram: ≤ 2200 chars, 8-12 hashtags.
    - CTAs must be reply-bait or save-bait. NEVER "share this", "tag a friend",
      "follow us", "don't miss out".
    - Banned corporate phrases: {BANNED_PHRASES_LIST_STR}
    - First 5 words of every post must be a pattern interrupt.

    VERIFICATION CHECKLIST:
    1. Does every variant open with a strong hook (not a setup)?
    2. Is any banned corporate phrase still present?
    3. Are hashtag counts within platform caps?
    4. Are CTAs reply-bait or save-bait?
    5. Is content factually aligned with the original brief?
    6. Are the 3 variants structurally different (contrarian vs question vs proof-stack)?
    7. Are the visuals distinct in layout?

    Return STRICTLY in this JSON format:
    {{
        "is_valid": true | false,
        "critique": "Overall strategic assessment...",
        "adjustments": "Specific actionable fixes if any (otherwise empty string)"
    }}

    Set is_valid=false ONLY if a real growth-blocking issue exists.
    Do NOT raise false alarms about Bold Unicode being missing — that's intentional.
    """
    return _call_agent("CRITIC", prompt)


def _planner_agent(
    refined_brief,
    research,
    platforms,
    days=7,
    user_context="",
    post_type="image",
    planning_window_calendar=None,
):
    """Agent 7: Creates a multi-day, multi-platform strategic campaign plan.

    Dates dynamically calculated from TODAY. First post is always TOMORROW
    (user expects future scheduling, not immediate). So for days=4 on
    2026-04-19, the plan covers 2026-04-20, 21, 22, 23.

    New as of strategy-pipeline v2:
      • `post_type` is enforced globally — every slot's content_type matches it.
      • Per-slot `needs_research` + `research_reason` so the scheduler can
        decide whether to JIT-generate on fire day (research) or pre-generate
        now for user review (static/product/service).
      • Planning-window cultural calendar drives auto-injected festival slots
        on the actual festival date inside [tomorrow .. last_day].
      • Time picker constrained to platform-best-practice bands per day-of-week
        (no more random 03:47 times).
    """
    from datetime import timedelta
    platforms_str = ", ".join(platforms)
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    last_day = today + timedelta(days=days)
    # Normalize post_type to one of the four supported content_type labels.
    # Frontend may send image|text|video|document; planner emits the display form.
    _PT_MAP = {"image": "Image", "text": "Text", "video": "Video", "document": "Document"}
    pt_norm = _PT_MAP.get((post_type or "image").lower(), "Image")
    window_cal_block = _format_planning_window_calendar_for_prompt(planning_window_calendar) if planning_window_calendar else "(not available)"
    prompt = f"""
    You are a Senior Social Media Campaign Architect for the following profile:
    {user_context}

    TODAY'S DATE:     {today.strftime("%Y-%m-%d")} (reference only — do NOT schedule on today)
    FIRST POST DATE:  {tomorrow.strftime("%Y-%m-%d")} (start the plan HERE)
    LAST POST DATE:   {last_day.strftime("%Y-%m-%d")} (plan ends on or before this)
    POST TYPE (LOCKED): {pt_norm}  ← every slot's content_type MUST be this value.

    Create a high-impact {days}-day posting plan for these platforms: {platforms_str}.

    BRIEF: "{refined_brief}"
    RESEARCH: {json.dumps(research)}

    PLANNING-WINDOW CULTURAL CALENDAR (festivals/holidays falling within
    [{tomorrow.strftime("%Y-%m-%d")} .. {last_day.strftime("%Y-%m-%d")}]):
    {window_cal_block}

    ═══════════════════════════════════════════════════════════════
    PLATFORM BEST-TIME GUIDE — you MUST pick a time from these bands.
    Do NOT invent random times. Pick the optimal slot for the platform on
    that day-of-week.
    ═══════════════════════════════════════════════════════════════
    LinkedIn  → Tue/Wed/Thu peak. Use 09:00-10:30 or 12:00-13:00.
                Mon/Fri OK at 09:30. Avoid Sat/Sun unless brief is consumer.
    Twitter/X → Mon-Fri. Use 09:00, 12:00, or 17:00-18:00.
                Tue 09:00 is the peak engagement slot — prefer it when available.
    Facebook  → Wed/Thu best. Use 13:00-15:00.
                Mon/Tue OK at 09:00. Avoid weekends for B2B.
    Instagram → Tue/Wed/Thu/Fri. Use 11:00-13:00 or 19:00-21:00.
                Weekends 10:00-12:00 work for consumer brands.

    For multi-platform same-day slots, pick the time best for the PRIMARY
    platform (first in the user's selection: {platforms_str.split(',')[0].strip()}).
    If the day-of-week is suboptimal for the platform, still pick the closest
    in-band time AND mention it briefly in `theme`.

    ═══════════════════════════════════════════════════════════════
    NEEDS_RESEARCH CLASSIFIER — per slot
    ═══════════════════════════════════════════════════════════════
    Set `needs_research: true` when the topic depends on information that
    DOES NOT EXIST YET at plan time — news, trending events, this-week's
    numbers, fresh launches, today's market state, festival cultural moments.
    Example: "Latest AI news this week", "Today's Google I/O announcements",
    "Diwali greeting", "This week's trending marketing topic".

    Set `needs_research: false` when the topic is grounded in STATIC info
    already known from the brand DNA, uploaded docs, or evergreen knowledge —
    product features, brand story, team milestones, how-to explainers.
    Example: "Spotlight on Spenzo Budget Planner feature", "Our company's
    journey from 2021", "How attribution modeling works (educational)".

    Always include `research_reason` — one short sentence explaining the call.

    ═══════════════════════════════════════════════════════════════
    FESTIVAL INJECTION (mandatory when calendar shows entries)
    ═══════════════════════════════════════════════════════════════
    The PLANNING-WINDOW CULTURAL CALENDAR above lists every major festival/
    federal holiday inside the plan window. For EACH festival listed:
      • Add ONE extra slot on that festival's exact date.
      • topic: festival-themed angle tied to the brand voice (e.g. for a
        SaaS brand on Diwali: "Light up your pricing strategy this Diwali").
      • theme: "Festival".
      • is_festival: true
      • festival_name: "<festival name from the calendar>"
      • needs_research: true   (festivals catch the day-of moment, JIT-generate)
      • research_reason: "Festival post — fires on the day to catch the
                         actual cultural moment."

    These are EXTRA slots BEYOND the {days} count. A 7-day plan with 1
    festival in the window becomes 8 slots total. A 7-day plan with 2
    festivals becomes 9 slots total.

    If the calendar shows no festivals, do NOT invent any. Skip injection.

    ═══════════════════════════════════════════════════════════════
    OUTPUT — STRICT JSON (no markdown, no code fences)
    ═══════════════════════════════════════════════════════════════
    {{
        "plan": [
            {{
                "week": 1,
                "date": "YYYY-MM-DD",
                "day": "DayName",
                "channel": "Platform",
                "content_type": "{pt_norm}",
                "topic": "Creative Hook or Title",
                "theme": "Strategy (e.g. Awareness, Conversion, Education, Community, Festival)",
                "cta": "Primary Call to Action",
                "time": "HH:MM (24h, from the best-time bands above)",
                "needs_research": true,
                "research_reason": "one-line classifier reason",
                "is_festival": false,
                "festival_name": null
            }}
        ]
    }}

    RULES (HARD):
    1. First post MUST be on {tomorrow.strftime("%Y-%m-%d")}. Last post MUST be
       on or before {last_day.strftime("%Y-%m-%d")}. NEVER schedule on or before
       today ({today.strftime("%Y-%m-%d")}).
    2. SLOT GRID — emit one PRIMARY slot for EVERY (day × platform) combination.
       Total primary slots = {days} days × {len(platforms)} platforms = {days * len(platforms)}.
       Same day, different platform = a SEPARATE slot. Topic + time + theme may
       differ across platforms on the same day (LinkedIn long-form vs Twitter
       punchy take). channel is exactly ONE platform per slot — never a list.
    3. FESTIVAL SLOTS — for each festival in the planning-window calendar,
       emit one EXTRA slot PER platform on the festival's date.
       Total festival slots = (festivals in window) × {len(platforms)}.
       Mark each with is_festival=true + festival_name + needs_research=true.
       These are ADDITIVE to the day-grid above, not replacements.
    4. content_type MUST equal "{pt_norm}" on EVERY slot. No mixing.
    5. Time MUST come from the PLATFORM BEST-TIME GUIDE bands above.
    6. needs_research + research_reason are MANDATORY on every slot.
    7. is_festival defaults to false. Set true only on festival-injected slots
       and populate festival_name.
    8. Vary topics across (day, platform) — don't repeat the same headline on
       LinkedIn + Twitter the same day; reframe it for each platform's native
       voice. Themes can repeat across platforms on a day (same theme, different
       copy angle) but should vary day-to-day for a balanced feed.
    """
    return _call_agent("PLANNER", prompt)


# ---------------------------------------------------------------------------
# Image utilities (PIL helpers)
# ---------------------------------------------------------------------------

def _pad_image_to_square(image_bytes):
    """Pad an image to a white square to normalize its aspect ratio."""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            max_dim = max(width, height)
            square_img = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
            offset = ((max_dim - width) // 2, (max_dim - height) // 2)
            square_img.paste(img, offset)
            out_buffer = BytesIO()
            square_img.save(out_buffer, format="PNG")
            return out_buffer.getvalue()
    except Exception as e:
        logger.error(f"Image padding failed: {e}")
        return image_bytes


def _strip_metadata(image_bytes):
    """Remove all EXIF and metadata from an image."""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)
            out_buffer = BytesIO()
            clean_img.save(out_buffer, format="JPEG", quality=95, optimize=True)
            return out_buffer.getvalue()
    except Exception as e:
        logger.error(f"Metadata stripping failed: {e}")
        return image_bytes


# ---------------------------------------------------------------------------
# Image generation — two-pass: Gemini background → compositor overlay
# ---------------------------------------------------------------------------

def _gen_single_variant(index, visual_concepts=None, logo_bytes=None, primary_color="#FF4500", raw_prompt=None, domain_name=""):
    """
    Generate one image variant.

    Pass 1 — Gemini generates a PURE background scene (no text, no logo, no buttons).
    Pass 2 — visual_compositor places logo + heading + sub-heading + CTA pill at
              coordinated positions (text_zone, logo_corner, cta_corner).
    """
    try:
        concept = None
        if raw_prompt:
            prompt = raw_prompt
            heading = "Custom Concept"
            sub_heading = "Professional Visual"
            highlight_words = []
            text_zone = "TOP_CENTER_BAND"
            cta_corner = "BOTTOM_RIGHT"
            logo_corner = "TOP_LEFT"
        else:
            concept = visual_concepts[index]
            prompt = concept.get("generation_prompt")
            heading = concept.get("heading") or "Success"
            sub_heading = concept.get("sub_heading") or "Modern Solutions"
            highlight_words = concept.get("highlight_words") or []
            if not highlight_words:
                parts = [w for w in heading.split() if len(w) > 1]
                highlight_words = parts[:2]
            text_zone = (concept.get("text_zone") or "TOP_CENTER_BAND").upper()
            cta_corner = (concept.get("cta_corner") or "BOTTOM_RIGHT").upper()
            logo_corner = (concept.get("logo_corner") or "TOP_LEFT").upper()

        # Safety: text_zone and logo_corner must not be the same corner
        if text_zone == "TOP_LEFT_PANEL" and logo_corner == "TOP_LEFT":
            logo_corner = "TOP_RIGHT"
        elif text_zone == "TOP_RIGHT_PANEL" and logo_corner == "TOP_RIGHT":
            logo_corner = "TOP_LEFT"
        elif text_zone == "LEFT_GLASS_CARD" and logo_corner == "TOP_LEFT":
            logo_corner = "TOP_RIGHT"

        if not prompt:
            logger.warning(f"Skipping Variant {index + 1}: prompt is missing.")
            return None

        # Strip stale placeholders from older concepts
        prompt = prompt.replace("[HEADING]", "").replace("[SUBHEADING]", "")

        # Belt-and-braces: prepend pure-background directive.
        #
        # NOTE (Apr 2026): the old directive asked Gemini to confine the scene
        # to "the MIDDLE 70% of the canvas (y=30% to y=88%)" so the compositor
        # had empty safe-zones for logo/CTA. In practice Gemini rendered those
        # safe zones as solid cream/gray bands, and on full-bleed templates
        # (T2, T5 after the recent edge-to-edge fix, T7, T10) those bands
        # read as ugly "gray strips" at the top and bottom of the image.
        # The compositor already places the logo on an opaque WHITE chip and
        # draws the CTA on a solid pill, so the scene can safely reach every
        # edge of the canvas — we no longer need Gemini to leave white space
        # for us. Ask for a FULL-BLEED scene, no empty bands.
        #
        # Product-in-use rule: if the concept carries a `product_name`, ask
        # Gemini to render that name on any laptop/monitor/tablet screen in
        # the scene so the viewer can see the people are using OUR product.
        _product_name = (concept or {}).get("product_name") if concept else ""
        _prod_rule = (
            f'On any visible laptop, monitor, or tablet screen in the scene, '
            f'render the product name "{_product_name}" ONCE as a bold '
            f'sans-serif header at the TOP of that dashboard UI in the brand '
            f'primary color, spelled EXACTLY as written — this is the ONLY '
            f'text allowed in the image. The dashboard BODY below that '
            f'header uses shapes only (bars, dots, lines, icons). '
            if _product_name else
            "Do not include any text, logo, CTA button, or pill-shaped "
            "element anywhere in the image. "
        )
        prompt = (
            "PURE BACKGROUND ONLY. " + _prod_rule +
            "The compositor will add the headline, sub-heading, and CTA "
            "pill after generation. The scene MUST be FULL-BLEED: the "
            "photograph / illustration should extend edge-to-edge on all "
            "four sides with NO empty borders, NO solid-color bands at the "
            "top or bottom, NO letterboxing, NO vignette frames, and NO "
            "cream/gray safe zones. Fill the ENTIRE 1024x1024 canvas with "
            "the scene — people, objects, architecture, environment — so "
            "every pixel at y=0 and y=1023 is part of the photograph.\n\n"
        ) + prompt

        if not client:
            raise Exception("AI_CLIENT_MISSING")

        logger.info(f"Gemini image gen — Variant {index + 1} (background-only)...")
        image_bytes = None
        for chunk in client.models.generate_content_stream(
            model="gemini-2.5-flash-image",
            contents=[prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        ):
            if chunk.parts:
                for part in chunk.parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break
            if image_bytes:
                break

        if not image_bytes:
            raise Exception("NO_IMAGES_RETURNED_BY_GEMINI")

        clean_image_bytes = _strip_metadata(image_bytes)

        # Composite overlay via visual_compositor
        try:
            from services.visual_compositor import composite_overlay
            composited = composite_overlay(
                image_bytes=clean_image_bytes,
                heading=heading,
                sub_heading=sub_heading,
                highlight_words=highlight_words,
                domain=domain_name or "",
                primary_color=primary_color,
                text_zone=text_zone,
                cta_corner=cta_corner,
                logo_bytes=logo_bytes,
                logo_corner=logo_corner,
            )
            logger.info(f"Variant {index + 1}: overlay composited (text_zone={text_zone} logo={logo_corner} cta={cta_corner})")
        except Exception as compose_err:
            logger.error(f"Variant {index + 1}: compositor failed, shipping raw image: {compose_err}")
            composited = clean_image_bytes

        # Upload to S3
        s3 = get_s3_client()
        file_name = f"ai_gen/visual_{uuid.uuid4().hex}.jpg"
        s3.upload_fileobj(
            BytesIO(composited),
            S3_BUCKET_NAME,
            file_name,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        final_url = get_s3_url(file_name)
        logger.info(f"Variant {index + 1} ready at {final_url}")

        return {
            "url": final_url,
            "bytes": clean_image_bytes,  # pre-overlay bytes for critic
            "name": (concept.get("name") if concept else f"Variant {index + 1}") or f"Variant {index + 1}",
            "layout": (concept.get("layout") if concept else "default") or "default",
            "heading": heading,
            "sub_heading": sub_heading,
            "highlight_words": highlight_words,
            "text_zone": text_zone,
            "cta_corner": cta_corner,
            "logo_corner": logo_corner,
        }

    except Exception as e:
        logger.error(f"Image Gen variant {index} FAILED: {e}")
        return None


def generate_visual_variants(visual_concepts, logo_bytes=None, refined_brief="", primary_color="#FF4500", domain_name=""):
    """Generates image variants with Visual Critic self-correction loop (max 3 attempts)."""
    if not isinstance(visual_concepts, list) or len(visual_concepts) == 0:
        logger.warning(f"Invalid visual_concepts ({type(visual_concepts)}). Using generic fallbacks.")
        visual_concepts = [
            {
                "layout": "Before/After Split",
                "narrative_device": "BEFORE/AFTER SPLIT",
                "name": "Transformation Flow",
                "heading": "FROM CHAOS TO CLARITY",
                "sub_heading": "AI-powered workflow in seconds",
                "highlight_words": ["CHAOS", "CLARITY"],
                "text_zone": "TOP_LEFT_PANEL",
                "cta_corner": "BOTTOM_LEFT",
                "logo_corner": "TOP_RIGHT",
                "generation_prompt": (
                    "PURE BACKGROUND ONLY. STRICT SQUARE 1:1, 1024x1024, 8k, cinematic. "
                    "SCENE: Diagonal split composition. Left side = cluttered paper stacks, "
                    "chaotic red overlays, visual noise. Right side = clean minimal workspace, "
                    "calm cool tones, single green checkmark. Junction = soft glowing brand color transition. "
                    "TOP 30% of canvas = clean uniform dark background (reserved for text overlay). "
                    "BOTTOM corners = clean background. "
                    "ABSOLUTE RULE: NO text, NO logos, NO buttons, NO labels anywhere."
                ),
            },
            {
                "layout": "Moment of Use",
                "narrative_device": "MOMENT OF USE",
                "name": "Expert in Action",
                "heading": "RESULTS IN REAL TIME",
                "sub_heading": "Every decision backed by AI",
                "highlight_words": ["RESULTS", "REAL"],
                "text_zone": "TOP_RIGHT_PANEL",
                "cta_corner": "BOTTOM_RIGHT",
                "logo_corner": "TOP_LEFT",
                "generation_prompt": (
                    "PURE BACKGROUND ONLY. STRICT SQUARE 1:1, 1024x1024, 8k, photorealistic. "
                    "SCENE: A single focused professional (side profile, no face directly visible) "
                    "in a modern premium workspace, expression showing calm confidence and relief. "
                    "A single large metric number is visible on their screen (no text label, just a number). "
                    "TOP 30% of canvas = clean uniform dark gradient (reserved for text overlay). "
                    "BOTTOM corners = clean background. "
                    "ABSOLUTE RULE: NO text labels, NO logos, NO buttons, NO captions anywhere."
                ),
            },
            {
                "layout": "Metaphorical Still-Life",
                "narrative_device": "METAPHORICAL STILL-LIFE",
                "name": "Precision Object",
                "heading": "BUILT FOR PRECISION",
                "sub_heading": "Control every outcome with confidence",
                "highlight_words": ["BUILT", "PRECISION"],
                "text_zone": "TOP_CENTER_BAND",
                "cta_corner": "BOTTOM_RIGHT",
                "logo_corner": "TOP_LEFT",
                "generation_prompt": (
                    "PURE BACKGROUND ONLY. STRICT SQUARE 1:1, 1024x1024, 8k, studio render. "
                    "SCENE: A single pristine sculptural object — a perfectly balanced scale or "
                    "crystalline geometric prism — floating center-right against a deep dark background. "
                    "Warm amber accent lighting. Studio-quality shadows. Translucent premium material. "
                    "TOP 30% of canvas = clean uniform dark gradient (reserved for text overlay). "
                    "BOTTOM corners = clean background. "
                    "ABSOLUTE RULE: NO text, NO logos, NO buttons, NO labels anywhere."
                ),
            },
        ]

    logger.info(f"Generating {len(visual_concepts)} image variants...")

    final_logo = logo_bytes
    if final_logo:
        logger.info("Normalising brand logo to 1:1 square...")
        final_logo = _pad_image_to_square(final_logo)

    results = []
    for i in range(len(visual_concepts)):
        attempts = 0
        best_rating = -1
        best_variant_data = None

        while attempts < 3:
            logger.info(f"Variant {i + 1} — Attempt {attempts + 1}/3...")
            variant_data = _gen_single_variant(
                i, visual_concepts,
                logo_bytes=final_logo,
                primary_color=primary_color,
                domain_name=domain_name,
            )

            if not variant_data:
                break

            logger.info(f"Running Visual Audit for Variant {i + 1}...")
            audit = _visual_critic_agent(
                variant_data['bytes'],
                visual_concepts[i].get('generation_prompt', ''),
                refined_brief,
                primary_color,
            )

            rating = audit.get('rating_out_of_10', 8)
            reason = audit.get('reason', 'N/A')
            advice = audit.get('improvement_advice', 'Improve visual relevance.')

            logger.info(f"Audit — Variant {i + 1} Attempt {attempts + 1}: {rating}/10")

            if rating > best_rating:
                best_rating = rating
                best_variant_data = variant_data
                best_variant_data['rating_out_of_10'] = rating
                best_variant_data['audit_reason'] = reason
                best_variant_data['attempts_count'] = attempts + 1

            if rating >= 7:
                logger.info(f"Variant {i + 1} PASSED ({rating}/10).")
                break

            logger.warning(f"Variant {i + 1} scored {rating}/10. Retrying with critic directive.")
            visual_concepts[i]['generation_prompt'] = (
                f"IMPROVEMENT DIRECTIVE: {advice}\n\n"
                f"ORIGINAL PROMPT: {visual_concepts[i]['generation_prompt']}"
            )
            attempts += 1

        if best_variant_data:
            best_variant_data.pop('bytes', None)
            results.append(best_variant_data)

    return results


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def _build_user_context(user, extra_context="", product_name=None):
    """Extract DNA, domain, primary_color, and user_context string from a User object.

    `product_name` (optional): when set and matches a key in
    business_dna.products, narrow the DNA to that product's branding (tone,
    brand_values, documents, colors). Otherwise use the top-level company DNA.
    Legacy dict-shaped extra_context is still honoured for backward compat.
    """
    dna = getattr(user, 'business_dna', {}) or {}
    domain_name = "pipelyt.com"
    primary_color = "#FF4500"
    user_context = ""

    target_product_name = product_name
    if not target_product_name and isinstance(extra_context, dict):
        target_product_name = extra_context.get('product_name')
    target_product = dna.get('products', {}).get(target_product_name) if target_product_name else None

    if target_product:
        context_source = target_product
        context_type = f"SPECIFIC PRODUCT DNA: {target_product_name}"
        raw_url = target_product.get('url', '') or getattr(user, 'business_url', '') or ''
    else:
        context_source = dna
        context_type = "GENERAL COMPANY DNA"
        raw_url = getattr(user, 'business_url', '') or dna.get('url', '') or ''

    domain_name = raw_url.split('//')[-1].split('/')[0].strip() or "pipelyt.com"
    primary_color = context_source.get('colors', {}).get('primary', '#FF4500')

    doc_context = ""
    entity_docs = context_source.get('documents', [])
    if entity_docs:
        doc_context = "\nREFERENCE DOCUMENTS CONTENT:\n"
        for doc in entity_docs:
            doc_context += f"--- {doc.get('name')} ---\n{doc.get('text', '')}\n\n"

    # Surface every URL field the copywriter is allowed to reach for when it
    # needs a real link in a CTA, "learn more", or "further reading" slot.
    # Rule #9 of the copywriter prompt explicitly tells the model to draw URLs
    # from here instead of emitting `[Insert Link N]` placeholders.
    url_lines = []
    for key in (
        "url", "website_url", "product_url", "pricing_url",
        "docs_url", "blog_url", "careers_url", "demo_url",
        "signup_url", "trial_url", "case_studies_url",
    ):
        v = context_source.get(key) or dna.get(key)
        if v and isinstance(v, str) and v.startswith("http"):
            url_lines.append(f"      {key}: {v}")
    # Top-level business_url fallback (when DNA dict is sparse).
    biz_url = getattr(user, 'business_url', '') or ''
    if biz_url and biz_url.startswith("http") and not any("business_url" in l for l in url_lines):
        url_lines.append(f"      business_url: {biz_url}")
    # Social links — flat key → value pairs.
    social_block = context_source.get("social_links") or dna.get("social_links") or {}
    if isinstance(social_block, dict):
        for k, v in social_block.items():
            if v and isinstance(v, str) and v.startswith("http"):
                url_lines.append(f"      social_links.{k}: {v}")
    if url_lines:
        urls_section = "Resource URLs (use these as REAL links in CTAs and resource lists — never invent or use placeholders):\n" + "\n".join(url_lines)
    else:
        urls_section = "Resource URLs: (none on file — when a brief calls for a link, draw from research.sources or omit the link entirely)"

    user_context = f"""
    {context_type}
    Entity Name: {context_source.get('name', getattr(user, 'company_name', None) or dna.get('company_name', 'N/A'))}
    Target Domain: {domain_name}
    Tagline: {context_source.get('tagline', 'N/A')}
    Brand Values: {', '.join(context_source.get('brand_values', [])) if context_source.get('brand_values') else 'N/A'}
    Brand Tone: {', '.join(context_source.get('brand_tone', [])) if context_source.get('brand_tone') else 'N/A'}
    Brand Aesthetic: {', '.join(context_source.get('brand_aesthetic', [])) if context_source.get('brand_aesthetic') else 'N/A'}
    Fonts: {', '.join(context_source.get('fonts', [])) if context_source.get('fonts') else 'N/A'}
    Colors: {json.dumps(context_source.get('colors', {}))}
    Overview: {context_source.get('overview', 'N/A')}
    {urls_section}
    {doc_context}
    """

    return user_context, domain_name, primary_color


# =============================================================================
# VISUALIST v2 (April 2026) — template-aware, DNA-color-driven, 3 bgs × 11 tpls
# =============================================================================

# Startup banner — if you see this in the logs, the v2 code path is loaded.
_v2_default = os.getenv("USE_VISUALIST_V2", "true").lower() not in ("0", "false", "no", "off")
logger.info(
    "Pipelyt Visualist v2 loaded — default=%s (set USE_VISUALIST_V2=false to disable)",
    "ON" if _v2_default else "OFF",
)

def _dna_product_name_tagline(user, extra_context="", product_name=None) -> tuple:
    """Pull the authoritative product name + tagline from business_dna.

    Priority:
      1. business_dna.products[product_name] — specific product scope
      2. business_dna (company-level) — company name + top-level tagline
      3. Empty strings if nothing configured

    Returns (product_name_str, tagline_str).
    """
    if not user:
        return ("", "")
    dna = getattr(user, 'business_dna', {}) or {}

    target = product_name
    if not target and isinstance(extra_context, dict):
        target = extra_context.get('product_name')

    source = None
    if target:
        source = dna.get('products', {}).get(target)

    name_out = ""
    tagline_out = ""

    if source:
        # Product-level DNA (best case)
        name_out = (source.get('name') or target or "").strip()
        tagline_out = (source.get('tagline') or source.get('sub_tagline') or "").strip()
    # Fallback to company-level (even if product was specified but lacks name/tagline)
    if not name_out:
        name_out = (dna.get('company_name') or dna.get('name') or getattr(user, 'company_name', '') or "").strip()
    if not tagline_out:
        tagline_out = (dna.get('tagline') or dna.get('sub_tagline') or "").strip()

    return (name_out, tagline_out)


def _visualist_v2_agent(
    refined_brief: str,
    user_context: str,
    primary_color: str,
    aspect_ratio: str = "16:9",
) -> dict:
    """Agent 4 v2 — writes 3 background prompts + overlay copy for compositor.

    Output contract (consumed by _generate_visuals_v2):
    {
      "brand": {"primary_color", "neutral_color", "aspect_ratio"},
      "campaign_analysis": {"product_name", "product_category",
                            "audience", "industry_context",
                            "campaign_moment"},
      "overlay_copy": {"headline", "subheading", "cta"},
      "image_prompts": [
        {"scene_type": "product_workspace"|..., "prompt": "..."},
        {"scene_type": "...",                   "prompt": "..."},
        {"scene_type": "...",                   "prompt": "..."}
      ]
    }

    Replaces `_linkedin_visualist_agent`. The old agent produced 3 narrative-
    device prompts with baked-in layout hints; v2 separates concerns — the
    agent picks only the scene (people, context, dressing) and the PIL
    compositor handles layout across 11 fixed templates per background.
    """
    prompt = f"""
    You are an Art Director writing 3 image briefs for Gemini 2.5 Flash Image.
    A PIL compositor overlays logo + headline + subheading + CTA on top of the
    generated photo, so YOUR image must be clean editorial photography with NO
    overlay graphics (the only allowed text is on a product UI screen, per RULE 1).

    ----- INPUT -----
    REFINED BRIEF: {refined_brief}

    USER / BRAND CONTEXT (DNA + uploaded docs): {user_context}

    BRAND PRIMARY COLOR: {primary_color}
    ASPECT RATIO: {aspect_ratio}

    ----- STEP 1. CAMPAIGN ANALYSIS (renders inside the image) -----
    Both fields represent the BRAND, not the campaign subject. Pull from DNA
    verbatim — never derive, never invent.

      • product_name    — Company / brand product name. Source priority:
                          DNA `product_name` → DNA `company_name` → [DNA: ...]
                          tags in the brief. NEVER pull from the brief topic
                          (the brief may spotlight a feature like "Pulse
                          module" — that is the campaign subject, not the
                          product). Spell exactly as given.
      • product_tagline — DNA's `tagline` field verbatim, including casing
                          and punctuation. If empty, return "".

    ----- STEP 2. OVERLAY COPY (SENTENCE FORMATION — MANDATORY) -----
    Scroll-stopping problem→solution. NOT pitchy, NOT generic. Reference
    a real audience pain. Both headline AND subheading MUST be GRAMMATICAL
    SENTENCES (subject + verb), not slogan fragments. Title-case fragments
    like "Watch Workflows Run Live", "Live Workflow Monitoring", or "See
    Every Step, Live" are FORBIDDEN — they are slogan headers, not
    sentences. The reader must understand the campaign brief from the
    overlay alone, without needing context.

      • headline    A SHORT DECLARATIVE SENTENCE. 5–9 words. Subject + verb
                    + object. Sentence case (not Title Case). Ends with a
                    period — the period is REQUIRED.
                    Good:
                      "Live workflow monitoring is now in {{product_name}}."
                      "{{product_name}} cuts attribution time by 60%."
                      "Spenzo now turns marketing data into instant answers."
                      "{{product_name}} ships campaigns without status meetings."
                    Forbidden:
                      "Live Workflow Monitoring" (no verb, Title Case, no period)
                      "Watch Workflows Run Live" (imperative slogan, no period)
                      "See Every Step, Live" (fragment)
                      "Cut Attribution Time 60%" (imperative fragment)
                      Any "?", any "!", "Introducing...", "Did you know...".

      • subheading  ONE OR TWO COMPLETE SENTENCES. 14–28 words total.
                    Sentence case. Each sentence ends with a period.
                    MUST explain the MECHANISM / PROOF / "how it works"
                    behind the headline — NOT restate it. MUST include
                    product_name literally and at least one concrete
                    capability noun (dashboard, replay, copilot, recipe,
                    workflow, report, integration, etc.).
                    Good:
                      "{{product_name}}'s real-time monitor streams every
                       node, error, and replay link to your dashboard. No
                       extra setup, no log digging."
                      "Spenzo's MMM copilot runs weekly reports in two
                       clicks and shows the channel mix that maximizes ROAS."

      • cta         3–5 words. NO trailing punctuation. Verb + concrete
                    object. Good: "See the Data", "Run the Report", "Get the
                    Guide", "Book a Demo", "Try {{product_name}} Free",
                    "Download the Playbook", "Watch the Walkthrough",
                    "Register Now", "Reserve Your Seat".
                    BANNED (never emit): "Learn more", "Click here",
                    "Read more", "Find out more", "Submit", "Get Started",
                    "More info", "More Details".

      VALIDATION (self-check before emitting):
        1. Does headline contain a verb? Does it end with "."?
        2. Does subheading contain product_name literally? Does each
           sentence end with "."?
        3. Could a stranger reading ONLY the overlay understand what the
           campaign is announcing? If no — rewrite.
      Any "no" → regenerate.

    ----- STEP 3. SHARED IMAGE STYLE (applies to RULES 2-3, NOT RULE 1) -----

    • Editorial documentary photography (WSJ / Monocle / Bloomberg / HBR).
    • Sharp focus throughout, f/11, deep DOF. NO bokeh, NO background blur.
    • All faces visible and clear. Natural focused expressions, not smiling
      at camera.
    • Abundant natural daylight from windows. Warm highlights, soft cool
      fill. Clean shadows.
    • Vibrant editorial color grade — saturated naturals, NOT orange-washed.
      One or two {primary_color} accents naturally placed in scene.
    • Scene dressing varies per prompt: warm wood, monitors/laptops, coffee,
      plants, notebooks.
    • NO text anywhere in the image except a product_name header on a
      visible monitor (per RULE 4 product-in-use rule below).
    • Full-bleed composition — fills every edge. Compositor handles overlay
      space; do NOT leave blank bands.

    ----- STEP 4. PICK 3 SCENE TYPES -----

    RULE 1 — One prompt MUST be `product_workspace`. This is a LITERAL flat
             2D screenshot of the feature inside the user's product UI
             (Stripe / Linear / Notion / Vercel landing-page-screenshot
             aesthetic). NOT a metaphor, NOT abstract storytelling, NOT a
             decorative number. OVERRIDES Step 3's photography style.

      ── 1A. PARSE THE BRIEF FIRST ──
      Before writing, list mentally:
        1. WHAT FEATURE / SCREEN the brief announces (e.g. "Pulse dashboard",
           "Budget Planner", "AI recommendation card", "Integration setup").
        2. EVERY specific NUMBER (dollars, %, multipliers, time, counts) —
           write them literally as the brief gives them.
        3. EVERY named ENTITY (channels: TV, paid search, Meta Ads, LinkedIn;
           modes: Fixed/Flex; partners, products, dates).
        4. EVERY scenario / what-if (e.g. "shift 15% TV→paid search →
           +$2.3M revenue").
        5. WHICH UI SURFACE fits: multi-widget dashboard / single feature
           panel / recommendation card / workflow wizard / empty→populated /
           comparison / report-export.

      ── 1B. EVERY ELEMENT FROM 1A.2-1A.4 MUST APPEAR ──
      Image is a fail if any are missing. Render each as a tile/label/badge/
      chart anchor / scenario card. EXACT NUMBERS only — never substitute
      brand-DNA stats for brief stats.

      ── 1C. NO METAPHORS / NO BRAND-DNA STATS ──
      No "data → insight" abstract splits. No giant decorative numerals
      floating in space. No brand-DNA stats unless brief mentions them
      verbatim.

      ── 1D. ALL UI TEXT IN DOUBLE QUOTES ──
      Every literal label/value in the prompt: "Revenue", "$2.3M", "92%",
      "Active", "Salesforce". So the model knows it's text, not concept.

      ── 1E. THIRD-PARTY BRANDS — OFFICIAL LOGOS, STRICT 1:1 PAIRING ──
      When the brief names a third party, render its OFFICIAL corporate
      logo next to the channel/integration name as a literal row. Strict
      1:1 — NEVER duplicate ("Meta Ads, Meta Ads" forbidden). Critical
      logos:
        Google: 4-color "G" mark
        Meta:   deep-blue infinity-twist / Möbius-strip mark (NOT Facebook 'f')
        LinkedIn: white "in" on #0A66C2 square
        TikTok: stylized music note + brand wordmark
        AWS:    lowercase black "aws" with curved orange smile-arrow under
        Salesforce: blue cloud mark
        SAP:    blue rectangle with "SAP" wordmark
        MuleSoft: 4-petal blue/purple flower mark
      Prefer label-light widgets (KPI tiles, gauges, sparklines) over
      heavy bar/line charts.

      ── 1F. CHARTS (use only when brief asks for 4+ named items over time) ──
      • EXACTLY 4 axis ticks, strictly ascending, same unit, NO duplicates.
      • Y-axis quoted: "$0", "$100K", "$200K", "$300K"  OR  "0%", "25%",
        "50%", "75%"  OR  "0", "5K", "10K", "15K".
      • X-axis time labels: months/quarters/days as the brief implies.
      • Realistic shapes — natural fluctuations, not perfectly identical bars.
      • Numeric callouts use the brief's actual values in their own unit
        ("$2.3M", "92%", "3.2x"), in double quotes.
      • Legends use channel names from brief WITH official logos (per 1E),
        unique-set never multiset.
      • All chart text legible at IG/LinkedIn render size.

      ── 1G. UNIVERSAL AESTHETIC ──
      • FULL-BLEED UI — the product UI fills the ENTIRE frame edge-to-
        edge (no surrounding white canvas, no centered floating card,
        no empty margins). Treat it like a literal flat 2D screenshot
        cropped tight to the application chrome. Zero tilt/perspective.
      • The window outline / app chrome must run all the way to each of
        the 4 image edges. NO breathing room around the panel. NO drop
        shadow under the panel (because there is no surrounding canvas
        for the shadow to fall on).
      • Subtle interior surfaces (light grey #F5F7FA panels, white
        #FFFFFF cards) are fine INSIDE the UI itself — but the outermost
        layer of the image must be the application surface, not a blank
        background.
      • Open prompt with: "A flat 2D full-bleed product UI screenshot
        that fills the entire frame edge-to-edge (no surrounding canvas,
        no centered floating card, no empty margins)…"
      • {primary_color} = dominant accent (active states, recommended
        items, chart fills, badges).
      • FEATURE NAME (e.g. "Budget Planning", "Pulse", "Summarizer") in
        TOP-LEFT corner, ~28-36px bold sans-serif, dark slate (#0F172A) —
        NOT centered, NOT in {primary_color}.
      • DO NOT render the parent product name (e.g. "Spenzo AI",
        "NeuzenAI", "Z-NINTH", "Zyntegrate") anywhere on screen — the
        compositor adds the brand mark afterwards. The brief mentioning
        the parent brand (even as possessive: "NeuzenAI's Strategic
        Consulting") is NOT permission to render it on-screen.
      • NO "Powered by" / "Made with" / attribution badges. NO physical
        objects (laptop, desk, hands, coffee). NO dark backgrounds.
      • Lighting: bright, even, neutral SaaS-marketing screenshot.

      ── 1H. TEXT DENSITY — HARD LIMITS (anti-blur / anti-gibberish) ──
      Gemini image gen produces garbled / blurred text whenever the UI
      contains long sentences, chat bubbles, paragraphs, or many small
      labels. Treat these rules as HARD FAILS:
      • ABSOLUTELY NO chat / messaging UIs, NO speech bubbles, NO
        question-answer transcripts, NO conversation logs. Even when the
        brief sounds conversational ("ask the AI…", "what if…"), render
        it as a STRUCTURED dashboard / form / KPI widget — never a chat.
      • NO sentence-length copy anywhere on screen. Every visible text
        element must be ≤ 4 words (a label, a metric name, a category,
        a button, a value). Long narrative strings WILL come out blurry
        and unreadable. If you cannot express it in ≤ 4 words, DON'T
        put it on the screen — leave it for the surrounding marketing
        copy outside the UI panel.
      • NO body paragraphs, NO multi-line tooltips, NO long onboarding
        copy, NO multi-sentence helper text under fields.
      • Maximum 12 distinct text elements visible on the entire UI
        panel. Prefer fewer, larger labels over many small ones.
      • Every text element must render at ≥ 18px equivalent at the
        final 1024×1024 canvas — anything smaller WILL blur. Use big
        bold KPI numbers, big chart axis labels, big section headers.
      • All numbers/values in double quotes (per 1D) and short — "$2.3M"
        OK, "$2,347,891.42" NOT OK.
      • NO duplicated logos / labels (per 1E) — duplicate text is the
        single most common cause of garbled output.

    RULE 2 — One prompt MUST be `single_user_focus`. EXACTLY ONE person,
             no teammates. Has TWO MODES — pick using the decision rule below.

      ── 2A. CLASSIFY BRIEF — HARD DECISION RULE (first match wins) ──

      Run STEP 1 FIRST and STOP if it matches. Only proceed to STEP 2 if
      STEP 1 produced ZERO matches. This ordering is non-negotiable.

      STEP 1 → MODE A if brief contains ANY of these patterns (case-insensitive,
      substring match — "What we believe" matches "we believe"):
        • Hiring: "hiring" / "we're hiring" / "join us" / "join our team" /
          "apply now" / "open role" / "we are looking for"
        • Quote: text in quote marks attributed to a person, "X said:",
          "as X puts it"
        • Brand manifesto / values: "we believe", "what we believe",
          "our mission", "our purpose", "X's purpose is", "our values",
          "what we stand for", "our why", "our vision"
        • Person's name as the subject (founder/employee spotlight)
        • Anniversary / milestone / award / press recognition

      STEP 2 → MODE B if brief describes a specific product/feature/service/
               workflow OR contains specific numbers / named integrations /
               named channels / named modes.

      STEP 3 → Ambiguous (no STEP 1 match, no concrete STEP 2 anchor) →
               DEFAULT TO MODE A.

      HARD VETO: STEP 1 always wins. If brief contains a STEP 1 pattern,
      pick MODE A even when STEP 2 patterns ALSO appear. Product names,
      integrations, and numbers are CONTEXT for the brand statement —
      NOT the subject.

      Worked example (DO follow this routing):
        Brief: "What we believe: enterprises don't fail because of a lack
                of data — they fail because their systems don't talk to
                each other. Z-Ninth's purpose is to fix that — integrating
                Salesforce, MuleSoft, and AI into one connected layer."
        → STEP 1 matches "we believe" / "our purpose" → MODE A.
        → "Salesforce, MuleSoft, AI" are CONTEXT, not subject. Do NOT
          escalate to MODE B because of them.

      Test: WHO speaks and ABOUT WHAT?
        • Brand voice / quote / hiring / values → MODE A (human subject)
        • Feature / service / metrics / workflow → MODE B (product subject)

      ── 2B. MODE A — PORTRAIT (white seamless studio) ──
      • Open with: "Editorial corporate portrait of a single [persona]
        against a pure white (#FFFFFF) seamless studio backdrop,
        photographed waist-up, facing the camera straight on…"
      • PURE WHITE backdrop, two-softbox studio lighting, no chiaroscuro.
      • Subtle floor shadow under subject (CSS-equiv: 0 24px 60px
        rgba(0,0,0,0.06)).
      • Pose: relaxed confident, arms NOT crossed, eyes on camera.
      • Wardrobe: smart/business casual neutral colors + ONE accent piece
        in {primary_color}.
      • Persona: concrete (age, gender, ethnicity, hair, wardrobe) —
        NEVER "a professional person".
      • NO text, NO product UI, NO environment, NO multiple people.

      ── 2C. MODE B — PRODUCT-IN-USE (screen is the hero) ──
      • Open with: "Editorial documentary photograph of a single [persona],
        photographed from a 3/4 angle profile, head and shoulder visible
        at the [left/right] edge of the frame. The camera faces a large
        thin-bezel monitor head-on (zero tilt), filling ~60-70% of the
        frame and displaying the [feature name] UI in sharp, fully-readable
        detail…"
      • Camera DIRECTLY in front of monitor, screen parallel to image plane,
        zero tilt/perspective. UI reads as clean upright rectangle.
      • Person 20-30% of frame, profile/3/4, head + shoulder only at one
        edge. ONE hand visibly on mouse/keyboard. Face partially visible
        (profile/3-quarter), NEVER full back-of-head, NEVER cropped out.
      • DEEP DOF — both person AND screen sharp. Person does NOT obstruct
        UI content. PERSON IS MANDATORY (image without a visible human is
        a fail).
      • SCREEN MIRRORS RULE 1 (same feature name top-left, same widgets,
        same logos, same numbers, same brand color, same white panel
        aesthetic). Apply RULE 1's 1B (every brief element appears) and
        1E (official logos, 1:1 pairing) on the monitor's UI.
      • PARENT PRODUCT NAME RULE: same as 1G — never render "Spenzo AI",
        "NeuzenAI", "Z-NINTH", "Zyntegrate", etc. on the monitor's UI.
        Brief possessives ("NeuzenAI's Consulting…") are NOT permission.
        FAIL CONDITION: any mention of the parent brand on-screen
        invalidates the image. Re-read your prompt before finalizing.
      • Background: bright, understated office. Modern matte-white desk,
        thin-bezel monitor. Environment must NOT compete with screen.
      • NO second person, NO "Powered by" badges, NO sofa/lap/café shots.

      ── 2D. STATE THE MODE ──
      Begin the single_user_focus prompt with "[MODE A — Portrait]" or
      "[MODE B — Product-in-use]" so a reviewer can verify routing.

    RULE 3 — Third prompt MUST be `team_moment`. 2-3 people in a
             CONFERENCE / MEETING ROOM, Step 3's editorial documentary
             style. The CENTERPIECE is a LARGE WALL-MOUNTED PRESENTATION
             SCREEN / SMART TV (NOT a desktop monitor, NOT a laptop) —
             roughly 55-75 inches diagonal, mounted on the back wall of
             the room. The team is standing or seated facing it,
             discussing insights as if in a strategy review or
             stakeholder readout. State this verbatim in the prompt:
                "A large wall-mounted presentation screen / smart TV
                (~65 inches, mounted at standing height) dominates the
                back wall of a modern conference room. The team of 2-3
                people stands or sits in the foreground, gestures at
                the screen, and discusses insights. The TV is large
                enough that the on-screen UI is readable from the
                viewer's seat. NO laptops on the table as the focal
                point — the WALL TV is the hero."
             The screen content MIRRORS RULE 1 (product_workspace) and
             RULE 2C (single_user_focus MODE B) EXACTLY — same product
             UI surface, NOT a shape-only abstraction:
               • Feature name top-left in {primary_color} bold sans
                 (e.g. "Pulse", "Connectors", "Budget Planner")
               • Same KPI tiles, charts, widgets, and partner logos
                 the brief implies
               • Apply RULE 1B (every brief number / partner / scenario
                 must appear) on the monitor's UI
               • Apply RULE 1E (official third-party logos, 1:1 pairing)
                 on the monitor's UI
               • Apply RULE 1F chart rules (4 ascending ticks, real
                 numbers in double quotes) when charts are shown
               • Camera angle: monitor is partially visible at 3/4 angle
                 from behind the team's shoulder, BUT the UI surface is
                 still readable — at least one face from the team is
                 visible turned toward camera, the rest face the screen
             Same parent-brand prohibition as 1G/2C — feature name only,
             never the parent product name on the in-image UI.
             The 3 scenes (product_workspace, single_user_focus, and
             team_moment) should display VISUALLY CONSISTENT product UI
             content — same feature name, same widget layout, same
             color palette — so the post reads as one coherent product
             story, not three disjoint screens.

    RULE 4 — Each prompt is one flowing 4-6-sentence paragraph. Vary
             environments across the 3 prompts (loft / open-plan / boutique).
             Describe each character + clothing concretely (distinct people).
             Do NOT mention aspect ratio (set via API) — instead compose for
             {aspect_ratio} (portrait=vertical, square=balanced,
             landscape=wide).

    RULE 5 — No scene_type may repeat.

    ----- OUTPUT (STRICT JSON, no markdown fences) -----
    {{
      "campaign_analysis": {{ "product_name": "...", "product_tagline": "..." }},
      "overlay_copy":      {{ "headline": "...", "subheading": "...", "cta": "..." }},
      "image_prompts": [
        {{ "scene_type": "product_workspace", "prompt": "..." }},
        {{ "scene_type": "single_user_focus", "prompt": "..." }},
        {{ "scene_type": "team_moment",       "prompt": "..." }}
      ]
    }}
    }}
    """
    data = _call_agent("VISUALIST_V2", prompt)
    # Normalize + defensive defaults
    if not isinstance(data, dict):
        logger.warning("Visualist v2 returned non-dict; wrapping")
        return {
            "campaign_analysis": {},
            "overlay_copy": {"headline": "", "subheading": "", "cta": "See the Data"},
            "image_prompts": [],
        }
    # `brand` is no longer asked of the LLM — primary_color and aspect_ratio
    # are already known by the backend (they're the function args). Read those
    # directly downstream instead of round-tripping through the model.
    data.setdefault("campaign_analysis", {})
    data.setdefault("overlay_copy", {"headline": "", "subheading": "", "cta": "See the Data"})
    data.setdefault("image_prompts", [])

    # Safety net (sentence formation policy):
    # • headline + subheading MUST end with "." — append it if the LLM
    #   slips and emits a fragment.
    # • cta is a button label — strip any trailing sentence punctuation.
    _overlay = data.get("overlay_copy") or {}
    _cta = _overlay.get("cta")
    if isinstance(_cta, str):
        _overlay["cta"] = _cta.rstrip().rstrip(".!?;:").rstrip()
    for _k in ("headline", "subheading"):
        _v = _overlay.get(_k)
        if isinstance(_v, str):
            _v = _v.rstrip().rstrip(";:").rstrip()
            # Convert "!" / "?" to "." (declarative only — per prompt rules).
            if _v.endswith("!") or _v.endswith("?"):
                _v = _v[:-1] + "."
            elif not _v.endswith("."):
                _v = _v + "."
            _overlay[_k] = _v
    data["overlay_copy"] = _overlay
    return data


def _gen_background_with_critic(
    prompt: str,
    refined_brief: str,
    primary_color: str,
    aspect_ratio: str,
    max_retries: int = 3,
) -> tuple:
    """Generate one background + critic retry loop. Returns (bytes, rating, attempts).

    Returns best-of-attempts if no attempt hits rating ≥ 7.
    """
    from google.genai import types as _types  # local import to avoid top-level coupling

    best_bytes = None
    best_rating = -1
    best_reason = ""
    current_prompt = prompt

    for attempt in range(max_retries):
        image_bytes = None
        # Build config with aspect_ratio when SDK supports ImageConfig
        cfg_kwargs = {"response_modalities": ["IMAGE", "TEXT"]}
        try:
            cfg_kwargs["image_config"] = _types.ImageConfig(aspect_ratio=aspect_ratio)  # type: ignore[attr-defined]
        except Exception:
            pass  # older SDK — fall back to prose-only aspect
        try:
            for chunk in client.models.generate_content_stream(
                model="gemini-2.5-flash-image",
                contents=[current_prompt],
                config=_types.GenerateContentConfig(**cfg_kwargs),
            ):
                if chunk.parts:
                    for part in chunk.parts:
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                if image_bytes:
                    break
        except Exception as e:
            logger.error(f"v2 bg gen attempt {attempt + 1} failed: {e}")
            continue

        if not image_bytes:
            continue

        image_bytes = _strip_metadata(image_bytes)

        audit = _visual_critic_agent(image_bytes, current_prompt, refined_brief, primary_color)
        rating = audit.get("rating_out_of_10", 8)
        reason = audit.get("reason", "N/A")
        advice = audit.get("improvement_advice", "Improve visual relevance.")

        if rating > best_rating:
            best_rating = rating
            best_bytes = image_bytes
            best_reason = reason

        if rating >= 7:
            logger.info(f"v2 bg PASS attempt {attempt + 1}: {rating}/10")
            break
        logger.warning(f"v2 bg attempt {attempt + 1} scored {rating}/10. Retrying with directive.")
        current_prompt = f"IMPROVEMENT DIRECTIVE: {advice}\n\nORIGINAL PROMPT: {prompt}"

    return best_bytes, best_rating, best_reason


def _run_freeform_pipeline(
    refined_brief: str,
    dna: dict,
    logo_bytes,
    n_variants: int,
    campaign_id: str,
    prefix: str,
) -> list:
    """Freeform helper — N parallel slots, each running a critic-driven
    retry loop (max 2 retries per slot if score < 9). After all slots
    finish, ALL attempts are pooled and the TOP `n_variants` by critic
    score become the returned variants. The pool is then uploaded to S3
    in parallel.

    Returns shape-compatible variants tagged `pipeline=freeform_v3` plus
    a `critic_score` field so the frontend can show the score per tile
    if useful.
    """
    from concurrent.futures import ThreadPoolExecutor
    from services.freeform_visual_service import generate_freeform_with_critic
    import time as _t

    s3 = get_s3_client()

    def _upload_jpeg(data: bytes, key: str) -> str:
        s3.upload_fileobj(
            BytesIO(data), S3_BUCKET_NAME, key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        return get_s3_url(key)

    _t0 = _t.monotonic()

    # Step 1 — run N parallel critic-driven slots. Each slot returns a
    # LIST of attempts (1..3 = 1 base + up to 2 retries).
    def _run_slot(slot_idx: int):
        try:
            return generate_freeform_with_critic(
                refined_brief, dna or {}, logo_bytes,
                max_retries=2, pass_score=9, slot_idx=slot_idx,
            ) or []
        except Exception as exc:
            logger.error(f"freeform slot {slot_idx} crashed: {exc}")
            return []

    all_attempts: list = []
    with ThreadPoolExecutor(max_workers=min(n_variants, 5)) as pool:
        for atts in pool.map(_run_slot, range(n_variants)):
            all_attempts.extend(atts)

    logger.info(
        f"[TIMING] stage=freeform_parallel dur={_t.monotonic()-_t0:.2f}s "
        f"slots={n_variants} total_attempts={len(all_attempts)}"
    )
    if not all_attempts:
        return []

    # Step 2 — sort all attempts (across all slots) by critic score desc
    # and take the top N. Each slot's BEST attempt has already been kept
    # (loop bails at score >= 9), but if multiple slots overshot 9,
    # we still pick the top-N by score so the gallery shows the strongest.
    # Tie-break: prefer earlier slots (more diversity), then earlier attempts.
    all_attempts.sort(
        key=lambda a: (-a.get("score", 0), a.get("slot_idx", 0), a.get("attempt_idx", 0))
    )
    top_attempts = all_attempts[:n_variants]
    logger.info(
        f"[freeform-critic] picked top {len(top_attempts)}/{len(all_attempts)} "
        f"attempts: scores={[a.get('score') for a in top_attempts]}"
    )

    # Step 3 — parallel S3 upload of the chosen top-N attempts.
    upload_tasks = []
    for i, a in enumerate(top_attempts):
        upload_tasks.append((i, a["image_bytes"],
                             f"{prefix}/freeform_{i + 1}.jpg"))
    url_map: dict = {}
    with ThreadPoolExecutor(max_workers=min(8, len(upload_tasks))) as pool:
        fut_map = {pool.submit(_upload_jpeg, data, key): idx
                   for (idx, data, key) in upload_tasks}
        for fut in fut_map:
            idx = fut_map[fut]
            try:
                url_map[idx] = fut.result(timeout=30)
            except Exception as exc:
                logger.error(f"freeform {idx} S3 upload failed: {exc}")
                url_map[idx] = None

    # Step 4 — assemble shape-compatible result list.
    result = []
    dropped = []
    for i, a in enumerate(top_attempts):
        url = url_map.get(i)
        if not url:
            dropped.append(i)
            continue
        critic = a.get("critic", {}) or {}
        result.append({
            "background_index": i,
            "scene_type": a.get("template_chosen", "freeform"),
            "background_url": url,
            "background_rating": a.get("score"),
            "background_reason": (
                "; ".join(critic.get("issues", []))[:200]
                if critic.get("issues") else ""
            ),
            "templates": {"FF": url},
            "campaign_id": campaign_id,
            "url": url,
            "primary_template": "FF",
            "headline": a.get("headline", ""),
            "subheading": a.get("subheading", ""),
            "cta": a.get("cta", ""),
            "template_chosen": a.get("template_chosen", ""),
            "pipeline": "freeform_v3",
            "critic_score": a.get("score"),
            "critic_attempt_idx": a.get("attempt_idx", 0),
        })
    if dropped:
        logger.warning(
            f"[freeform] {len(dropped)} top-attempts DROPPED at S3 upload step: "
            f"indices={dropped} (final result has {len(result)}/{len(top_attempts)})"
        )
    else:
        logger.info(
            f"[freeform] result assembled: {len(result)} top variants  "
            f"scores={[r.get('critic_score') for r in result]}"
        )
    return result


def _run_v2_pil_pipeline(
    refined_brief: str,
    dna: dict,
    logo_bytes,
    primary_color: str,
    aspect_ratio: str,
    user,
    campaign_id: str,
    prefix: str,
    product_name: str = None,
) -> list:
    """Legacy V2 PIL helper — restored: visualist v2 agent → 3 backgrounds
    via `_gen_background_with_critic` → 11 PIL composites per background
    via `image_templates.compose_all`. Each variant tagged `pipeline=v2_pil`
    so frontend can distinguish.

    `product_name` (optional): when set and present in
    business_dna.products, the visualist agent's user_context is narrowed
    to that product's DNA only — no company-level fallback, no parent
    DNA mixing. Critical for multi-product accounts (e.g. Zyntegrate
    inside Z-Ninth) — without this, Zyntegrate generations would leak
    NeuZenAI/Z-Ninth branding into the rendered images.
    """
    from concurrent.futures import ThreadPoolExecutor
    from services.image_templates import compose_all
    import time as _t

    s3 = get_s3_client()

    def _upload_jpeg(data: bytes, key: str) -> str:
        s3.upload_fileobj(
            BytesIO(data), S3_BUCKET_NAME, key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        return get_s3_url(key)

    # Build user_context PRODUCT-SCOPED so the visualist agent only sees
    # the selected product's DNA — no parent company fallback.
    if user:
        user_context, _, _ = _build_user_context(user, product_name=product_name)
    else:
        user_context = ""

    # Step 1: visualist v2 agent (text agent for image prompts)
    _t0 = _t.monotonic()
    try:
        visualist_out = _visualist_v2_agent(
            refined_brief, user_context,
            primary_color=primary_color,
            aspect_ratio=aspect_ratio,
        )
    except Exception as exc:
        logger.error(f"v2 visualist agent failed: {exc}")
        return []
    logger.info(f"[TIMING] stage=v2_visualist dur={_t.monotonic()-_t0:.2f}s")

    # Step 2: DNA overrides — product-scoped so a Zyntegrate generation
    # gets "Zyntegrate" / its tagline, not the parent Z-Ninth values.
    if user:
        try:
            dna_name, dna_tagline = _dna_product_name_tagline(
                user, product_name=product_name,
            )
            if dna_name or dna_tagline:
                visualist_out.setdefault("dna_overrides", {})
                if dna_name:
                    visualist_out["dna_overrides"]["product_name"] = dna_name
                if dna_tagline:
                    visualist_out["dna_overrides"]["product_tagline"] = dna_tagline
        except Exception as exc:
            logger.warning(f"v2 dna override failed: {exc}")

    overlay_copy = visualist_out.get("overlay_copy") or {}
    image_prompts = visualist_out.get("image_prompts") or []
    campaign_analysis = visualist_out.get("campaign_analysis") or {}
    dna_overrides = visualist_out.get("dna_overrides") or {}
    overlay_copy = {
        **overlay_copy,
        "product_name": (
            dna_overrides.get("product_name")
            or campaign_analysis.get("product_name") or ""
        ),
        "product_tagline": (
            dna_overrides.get("product_tagline")
            or campaign_analysis.get("product_tagline") or ""
        ),
    }

    # Step 3: 3 backgrounds in parallel via critic loop
    valid_prompts = []
    for idx, p in enumerate(image_prompts[:3]):
        ptext = p.get("prompt", "") if isinstance(p, dict) else str(p)
        scene = (p.get("scene_type", "unknown")
                 if isinstance(p, dict) else "legacy")
        if ptext:
            valid_prompts.append((idx, scene, ptext))

    def _gen_one_bg(job):
        bg_idx, scene, ptext = job
        try:
            bg_bytes, rating, reason = _gen_background_with_critic(
                ptext, refined_brief, primary_color, aspect_ratio
            )
            if not bg_bytes:
                return None
            return {"index": bg_idx, "scene_type": scene,
                    "bytes": bg_bytes, "rating": rating, "reason": reason}
        except Exception as exc:
            logger.error(f"v2 bg {bg_idx} failed: {exc}")
            return None

    _t1 = _t.monotonic()
    backgrounds = []
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(valid_prompts)))) as ex:
        for r in ex.map(_gen_one_bg, valid_prompts):
            if r is not None:
                backgrounds.append(r)
    backgrounds.sort(key=lambda b: b["index"])
    logger.info(
        f"[TIMING] stage=v2_bg_parallel dur={_t.monotonic()-_t1:.2f}s "
        f"count={len(backgrounds)}"
    )

    if not backgrounds:
        return []

    # Step 4: compose scene-specific PIL templates per bg.
    # We restrict templates by scene so each background only renders the
    # layouts that look good for that kind of imagery.
    #   • product_workspace → TFB (full-bleed product UI window) + T10
    #     (orange framed mockup with logo lockup + CTA)
    #   • single_user_focus / team_moment → TBD (full set for now until
    #     the user picks the curated layouts)
    SCENE_TEMPLATE_MAP = {
        "product_workspace": ["TFB", "T10"],
    }
    composed: list = []
    for bg in backgrounds:
        scene = (bg.get("scene_type") or "").strip()
        scene_tids = SCENE_TEMPLATE_MAP.get(scene)  # None → fall back to all
        try:
            tpl_map = compose_all(
                bg["bytes"], overlay_copy, logo_bytes, primary_color,
                template_ids=scene_tids,
            )
            for tid, jpeg in tpl_map.items():
                composed.append((bg["index"], tid, jpeg))
        except Exception as exc:
            logger.error(f"v2 compose_all bg {bg['index']} failed: {exc}")

    # Step 5: parallel S3 upload — 3 bgs + N composites
    upload_tasks = []
    for bg in backgrounds:
        upload_tasks.append(
            ("bg", bg["index"], bg["bytes"],
             f"{prefix}/v2_bg_{bg['index'] + 1}.jpg")
        )
    for bg_idx, tid, data in composed:
        upload_tasks.append(
            ("tpl", (bg_idx, tid), data,
             f"{prefix}/v2_{tid}_bg{bg_idx + 1}.jpg")
        )

    url_map: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(_upload_jpeg, data, key): (kind, key_id)
                   for (kind, key_id, data, key) in upload_tasks}
        for fut in fut_map:
            kind, key_id = fut_map[fut]
            try:
                url_map[(kind, key_id)] = fut.result(timeout=30)
            except Exception as exc:
                logger.error(f"v2 S3 upload {kind} {key_id} failed: {exc}")
                url_map[(kind, key_id)] = None

    # Step 6: assemble shape-compatible variants
    produced_tids = sorted({tid for (_, tid, _) in composed})
    primary_t = ("T5" if "T5" in produced_tids
                 else (produced_tids[0] if produced_tids else "T5"))

    result = []
    for bg in backgrounds:
        bg_url = url_map.get(("bg", bg["index"]))
        tpl_urls = {}
        for tid in produced_tids:
            u = url_map.get(("tpl", (bg["index"], tid)))
            if u:
                tpl_urls[tid] = u
        result.append({
            "background_index": bg["index"] + 100,  # offset so freeform 0..4 don't collide
            "scene_type": bg["scene_type"],
            "background_url": bg_url,
            "background_rating": bg["rating"],
            "background_reason": bg["reason"],
            "templates": tpl_urls,
            "campaign_id": campaign_id,
            "url": tpl_urls.get(primary_t) or bg_url,
            "primary_template": primary_t,
            "headline": overlay_copy.get("headline", ""),
            "subheading": overlay_copy.get("subheading", ""),
            "cta": overlay_copy.get("cta", ""),
            "pipeline": "v2_pil",
        })
    return result


def _generate_visuals_v2(
    refined_brief: str,
    dna: dict,
    logo_bytes,
    primary_color: str = "#FF4500",
    aspect_ratio: str = "16:9",
    user=None,
    n_variants: int = 3,
    visualist_output: dict = None,  # legacy positional, ignored
    product_name: str = None,
) -> list:
    """HYBRID orchestrator: runs the freeform pipeline AND the legacy V2 PIL
    pipeline IN PARALLEL, then concatenates all variants. Frontend gallery
    shows freeform variants first (5), then V2 PIL variants (3 backgrounds
    × 11 PIL templates = up to 33). `flattenVisuals` already handles both
    shapes natively.

    Each variant carries a `pipeline` field ("freeform_v3" or "v2_pil") so
    the gallery can group / label / filter by source pipeline.
    """
    import uuid as _uuid
    from concurrent.futures import ThreadPoolExecutor
    import time as _t

    _ = visualist_output  # noqa — legacy arg accepted for back-compat

    campaign_id = _uuid.uuid4().hex[:12]
    prefix = f"ai_gen/{campaign_id}"

    logger.warning(
        f">>> HYBRID PATH HIT  campaign_id={campaign_id}  "
        f"primary={primary_color}  freeform_n={n_variants} <<<"
    )

    t0 = _t.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_free = pool.submit(
            _run_freeform_pipeline,
            refined_brief, dna, logo_bytes, n_variants, campaign_id, prefix,
        )
        f_v2pil = pool.submit(
            _run_v2_pil_pipeline,
            refined_brief, dna, logo_bytes, primary_color, aspect_ratio,
            user, campaign_id, prefix, product_name,
        )
        try:
            free_variants = f_free.result(timeout=300) or []
        except Exception as e:
            logger.error(f"freeform pipeline failed at top level: {e}")
            free_variants = []
        try:
            v2_variants = f_v2pil.result(timeout=300) or []
        except Exception as e:
            logger.error(f"v2 pil pipeline failed at top level: {e}")
            v2_variants = []

    logger.info(
        f"[TIMING] hybrid total dur={_t.monotonic()-t0:.1f}s "
        f"freeform={len(free_variants)} v2pil={len(v2_variants)} "
        f"campaign_id={campaign_id}"
    )

    # Freeform first so it appears at the top of the gallery; V2 PIL
    # composites scroll below. Both shapes are flattenVisuals-compatible.
    return list(free_variants) + list(v2_variants)


def generate_strategic_content(campaign_brief, platforms, user=None, logo_bytes=None, extra_context="", product_name=None, post_type="image", product_image_urls=None, image_style=None):
    """Orchestrates the sequential multi-agent workflow.

    `product_name` (optional): when supplied and present in
    business_dna.products, narrows the DNA context to that product's branding
    for every downstream agent (refinement, research, content, visuals).

    `post_type` ("image" | "text" | "video" | "document"):
      - "image" (default): full pipeline including AI visuals (15 composites).
      - "text" / "video" / "document": skips the Visualist entirely — saves
        ~60-90s of Gemini image generation and returns visuals=[] so the
        frontend can swap in a variant picker / upload card instead.

    `product_image_urls` (optional, list[str]): public S3 URLs of user-uploaded
    product photos. Passed to the carousel + image pipelines as additional
    reference images so gpt-image-2 features the actual product instead of
    generic stock-style imagery. Capped to 4 images downstream.
    """
    logger.info(f"Orchestrating Multi-Agent Workflow for brief: {campaign_brief[:50]}... (product_name={product_name!r}, post_type={post_type!r})")

    user_context = ""
    domain_name = "pipelyt.com"
    primary_color = "#FF4500"

    if user:
        user_context, domain_name, primary_color = _build_user_context(user, extra_context, product_name=product_name)

    # Handle both legacy string form and new dict form {docs, aspect_ratio, ...}
    if isinstance(extra_context, dict):
        docs_text = extra_context.get("docs", "") or ""
    else:
        docs_text = extra_context or ""
    full_brief = (
        f"{campaign_brief}\n\nADDITIONAL CONTEXT FROM SESSION DOCUMENTS:\n{docs_text}"
        if docs_text
        else campaign_brief
    )

    # ----- Timing instrumentation -----
    # Log wall-clock duration for each agent stage so we can see where the
    # 60-120s of "Generate" time actually goes. Appears in the log as
    # [TIMING] stage=refine dur=12.3s ... and a final [TIMING] total=... line.
    import time as _t
    from datetime import datetime as _dt, timezone as _tz
    _stage_times = {}
    _overall_t0 = _t.monotonic()
    logger.info(f"[TRACE] orchestrator BEGIN ts={_dt.now(_tz.utc).isoformat()} post_type={post_type!r}")

    def _trace_begin(name):
        logger.info(f"[TRACE] {name} BEGIN ts={_dt.now(_tz.utc).isoformat()}")
        return _t.monotonic()

    def _trace_end(name, t0):
        dur = _t.monotonic() - t0
        logger.info(f"[TRACE] {name} END   ts={_dt.now(_tz.utc).isoformat()} dur={dur:.2f}s")
        return dur

    # 1. Refinement
    _t_stage = _trace_begin("refine")
    refinement = _refine_brief_agent(full_brief, user_context)
    _stage_times["refine"] = _trace_end("refine", _t_stage)
    logger.info(f"[TIMING] stage=refine dur={_stage_times['refine']:.2f}s post_type={post_type!r}")
    refined_brief_raw = refinement.get("refined_brief", full_brief)
    refined_brief = json.dumps(refined_brief_raw, indent=2) if isinstance(refined_brief_raw, dict) else str(refined_brief_raw)

    # 2a. Cultural calendar (today + tomorrow festivals, India + USA).
    # Day-cached so this is a one-off live web search per day across all
    # users / requests. Cheap on warm cache hits; ~3-5s on a cold run.
    _t_stage = _trace_begin("cultural")
    cultural_calendar = _get_cultural_calendar()
    _stage_times["cultural"] = _trace_end("cultural", _t_stage)
    logger.info(f"[TIMING] stage=cultural dur={_stage_times['cultural']:.2f}s")

    # 2b. Research — receives cultural calendar so it can surface relevant
    # festivals as angles when they fit.
    _t_stage = _trace_begin("research")
    research = _research_agent(refined_brief, user_context, cultural_calendar)
    _stage_times["research"] = _trace_end("research", _t_stage)
    logger.info(f"[TIMING] stage=research dur={_stage_times['research']:.2f}s")

    # 3. Content Generation
    _t_stage = _trace_begin("content")
    content_data = _content_agent(refined_brief, research, platforms, user_context, cultural_calendar=cultural_calendar)
    _stage_times["content"] = _trace_end("content", _t_stage)
    logger.info(f"[TIMING] stage=content dur={_stage_times['content']:.2f}s platforms={platforms}")

    # 4. Visual Design — v2 (template-aware, 15 composites) is DEFAULT.
    # Set USE_VISUALIST_V2=false to fall back to the legacy 3-narrative-device
    # pipeline (kept as an escape hatch while the new path stabilizes).
    _v2_flag = os.getenv("USE_VISUALIST_V2", "true").lower()
    use_v2 = _v2_flag not in ("0", "false", "no", "off")
    # v4 image agent — pure Gemini image gen, no templates, no overlay compositor.
    # When on (default), it BYPASSES freeform v3 entirely. Flip to "false" to
    # restore the freeform + templated pipeline temporarily.
    _v4_flag = os.getenv("USE_IMAGE_AGENT_V4", "true").lower()
    use_v4 = _v4_flag not in ("0", "false", "no", "off")
    # Magic Image Pipeline (GPT-5 + gpt-image-2 high). Opt-in via env. When
    # this flag is on, it OVERRIDES v4 and v2 — the magic pipeline becomes
    # the renderer for image posts. Off by default; turn on by setting
    # USE_MAGIC_IMAGE_PIPELINE=true in .env (also requires OPENAI_API_KEY).
    _magic_flag = os.getenv("USE_MAGIC_IMAGE_PIPELINE", "false").lower()
    use_magic = _magic_flag not in ("0", "false", "no", "off")
    aspect_ratio = None
    if isinstance(extra_context, dict):
        aspect_ratio = extra_context.get("aspect_ratio")
    # Default aspect ratio for image generation is 16:9 (landscape — best for
    # LinkedIn / X / Facebook feed real estate). Frontend only sends an explicit
    # aspect_ratio when the user picks one from the dropdown; otherwise we fall
    # back to 16:9 here.
    aspect_ratio = aspect_ratio or "16:9"

    # Non-image post types: text / video skip visuals entirely (user
    # supplies their own media). document gets routed through the
    # Carousel pipeline when USE_CAROUSEL_PIPELINE is on, which generates
    # a multi-slide PDF; if disabled, document falls back to "user uploads
    # their own PDF" (legacy behavior).
    _carousel_flag = os.getenv("USE_CAROUSEL_PIPELINE", "false").lower()
    use_carousel  = _carousel_flag not in ("0", "false", "no", "off")
    _t_stage = _trace_begin("visuals")
    if post_type in ("text", "video"):
        logger.info(f"Skipping visualist — post_type={post_type!r}")
        visual_variants = []
        visualist_meta = None
    elif post_type == "document" and use_carousel:
        # CAROUSEL PIPELINE — GPT-5 (Director) + gpt-image-2 + reportlab
        # stitch -> S3 PDF URL ready for linkedin_upload_document().
        logger.info(
            f"Using Carousel Pipeline (post_type=document, "
            f"aspect={aspect_ratio}, platforms={platforms})"
        )
        try:
            from services.carousel_pipeline import run_carousel_pipeline

            # business_dna_label resolution mirrors the magic-pipeline branch
            _dna_for_label: dict = {}
            try:
                from routers.content import _active_dna as _active_dna_helper
                _dna_for_label = _active_dna_helper(user, product_name) if user else {}
            except Exception:
                _dna_for_label = (getattr(user, "business_dna", None) or {}) if user else {}
            business_dna_label = (
                product_name
                or (isinstance(_dna_for_label, dict) and (
                    _dna_for_label.get("company_name")
                    or _dna_for_label.get("business_name")
                ))
                or getattr(user, "email", "")
                or "anonymous"
            )

            # Extract the business category so the carousel director can pick
            # the right industry playbook (physical_product -> product-first
            # photography rules, everything else -> current defaults).
            _biz_category_carousel = ""
            if isinstance(_dna_for_label, dict):
                _biz_category_carousel = str(_dna_for_label.get("category") or "").strip().lower()
            logger.info(
                f"[carousel] business_category resolved to "
                f"{_biz_category_carousel!r} (from Business DNA — drives industry playbook)"
            )

            # Pull the LinkedIn caption the content agent produced. Document
            # posts are LinkedIn-only (enforced at publish time), so we read
            # the LinkedIn content slot. Prefer viral_reach, then any non-empty.
            _linkedin = ((content_data.get("content") or {}).get("linkedin") or {})
            post_text_for_carousel = (
                _linkedin.get("viral_reach")
                or _linkedin.get("high_interaction")
                or _linkedin.get("follower_growth")
                or _linkedin.get("festival_variant")
                or ""
            ).strip()

            if not post_text_for_carousel:
                raise RuntimeError(
                    "Carousel pipeline: no LinkedIn caption found in content_data — "
                    "cannot decompose into slides"
                )

            # Pipeline selection. Set USE_HTML_CAROUSEL_PIPELINE=true in
            # .env to swap the gpt-image-2 renderer for the HTML+Chromium
            # Variant C pipeline (LLM writes HTML, Playwright screenshots).
            # ~30x faster, ~5x cheaper, pixel-perfect text, deterministic
            # logo + CTA placement. Both pipelines return the same dict
            # shape so downstream code is unchanged.
            _use_html_pipeline = os.getenv(
                "USE_HTML_CAROUSEL_PIPELINE", "false"
            ).strip().lower() in ("1", "true", "yes", "on")

            if _use_html_pipeline:
                from services.carousel_html_pipeline import run_carousel_html_pipeline
                logger.info(
                    "Using HTML Carousel Pipeline (Variant C: gpt-5 -> HTML -> "
                    "Playwright Chromium -> PDF)"
                )
                _carousel_runner = run_carousel_html_pipeline
            else:
                _carousel_runner = run_carousel_pipeline

            carousel_result = _carousel_runner(
                post_text=post_text_for_carousel,
                brand_name=business_dna_label,
                brand_color=primary_color,
                logo_bytes=logo_bytes,
                aspect_ratio=aspect_ratio,
                business_dna_label=business_dna_label,
                business_category=_biz_category_carousel,
                # Raw user brief (incl. any session docs) so the CSV captures
                # the original input that triggered this carousel.
                campaign_brief=full_brief,
                # Upstream Gemini stage timings — refine/cultural/research/content
                # are all populated in _stage_times by this point. Pass them so the
                # carousel CSV captures the full per-request profile too.
                upstream_stage_times={
                    "refine":   _stage_times.get("refine", 0.0),
                    "cultural": _stage_times.get("cultural", 0.0),
                    "research": _stage_times.get("research", 0.0),
                    "content":  _stage_times.get("content", 0.0),
                },
                # User-uploaded product reference photos; pipeline downloads
                # the bytes once and attaches them to every slide render.
                product_image_urls=product_image_urls,
                # User-selected style; None / "auto" = current behaviour.
                image_style=image_style,
            )

            # Shape into the existing visual_variants contract. The
            # downstream code reads .url + .media_type to decide what to
            # save into ScheduledPost.image_url + media_type.
            # thumbnail_url = slide-1 PNG. Frontend uses this as an <img>
            # preview in the Drafts grid (way more reliable than rendering
            # the PDF in an iframe).
            _slides = carousel_result.get("slides") or []
            _thumb = _slides[0].get("png_s3_url") if _slides else None
            visual_variants = [{
                "url":           carousel_result["pdf_url"],
                "image_url":     carousel_result["pdf_url"],
                "thumbnail_url": _thumb,
                "media_type":    "document",
                "pdf_title":     carousel_result["pdf_title"],
                "slide_count":   carousel_result["slide_count"],
                "slides":        carousel_result["slides"],
                "variant_idx":   0,
                "pipeline":      "carousel-gpt-image-2",
            }]
            visualist_meta = None
        except Exception as _carousel_err:
            logger.error(
                f"[carousel] pipeline failed: {_carousel_err} — falling back "
                f"to user-upload-only mode for this document post"
            )
            # Soft fallback: behave like the legacy "skip visuals" branch so
            # the user can still publish by attaching their own PDF.
            visual_variants = []
            visualist_meta = None
    elif post_type == "document":
        # Carousel pipeline disabled — keep legacy "user uploads their own PDF" behavior.
        logger.info("Skipping visualist — post_type='document' (USE_CAROUSEL_PIPELINE=false)")
        visual_variants = []
        visualist_meta = None
    elif use_magic:
        # MAGIC IMAGE PIPELINE — GPT-5 (Agent 1: magic prompt) + gpt-image-2
        # high (Agent 2: render). Overrides v4 and v2 entirely when the flag
        # is on. Logs one CSV row per variant.
        logger.info(
            f"Using Magic Image Pipeline (GPT-5 + gpt-image-2, "
            f"aspect={aspect_ratio}, platforms={platforms})"
        )
        try:
            from services.magic_image_pipeline import run_magic_image_pipeline

            # business_dna_label for the CSV: product > dna company > user email
            _dna_for_label: dict = {}
            try:
                from routers.content import _active_dna as _active_dna_helper
                _dna_for_label = _active_dna_helper(user, product_name) if user else {}
            except Exception:
                _dna_for_label = (getattr(user, "business_dna", None) or {}) if user else {}
            business_dna_label = (
                product_name
                or (isinstance(_dna_for_label, dict) and (
                    _dna_for_label.get("company_name")
                    or _dna_for_label.get("business_name")
                ))
                or getattr(user, "email", "")
                or "anonymous"
            )

            # Business category drives the industry playbook injected into
            # Agent 1's prompt. Physical_product overrides SaaS defaults with
            # product-first commercial photography rules. Empty / other
            # categories fall through to the current prompt.
            _biz_category_magic = ""
            if isinstance(_dna_for_label, dict):
                _biz_category_magic = str(_dna_for_label.get("category") or "").strip().lower()
            logger.info(
                f"[magic] business_category resolved to "
                f"{_biz_category_magic!r} (from Business DNA — drives industry playbook + auto-style routing)"
            )

            magic_results = run_magic_image_pipeline(
                campaign_brief=refined_brief,
                content_dict=content_data.get("content") or {},
                selected_platforms=platforms or [],
                primary_brand_color=primary_color,
                aspect_ratio=aspect_ratio,
                logo_bytes=logo_bytes,
                business_dna_label=business_dna_label,
                business_category=_biz_category_magic,
                dna_category=_biz_category_magic,
                business_dna=_dna_for_label if isinstance(_dna_for_label, dict) else None,
                user_id=getattr(user, "id", None),
                product_name=product_name,
                raw_user_brief=full_brief,
                # Upstream Gemini stage timings (refine/cultural/research/content)
                # are already populated in _stage_times by this point. Pass them
                # through so the CSV captures the FULL per-request profile.
                upstream_stage_times={
                    "refine":   _stage_times.get("refine", 0.0),
                    "cultural": _stage_times.get("cultural", 0.0),
                    "research": _stage_times.get("research", 0.0),
                    "content":  _stage_times.get("content", 0.0),
                },
                # User-uploaded product photos → attached as gpt-image-2
                # references so every variant features the actual product.
                product_image_urls=product_image_urls,
                # User-selected style; None / "auto" = current behaviour.
                image_style=image_style,
            )

            # Shape to match the v4 contract so downstream code keeps working
            # (it reads `url`, `variant_idx`, `pipeline`).
            visual_variants = [
                {
                    "url":           r["url"],
                    "image_url":     r["url"],
                    "variant_idx":   i,
                    "variant_type":  r["variant_type"],
                    "platform_used": r["platform_used"],
                    "magic_prompt":  r["magic_prompt"],
                    "pipeline":      "magic-gpt-image-2",
                }
                for i, r in enumerate(magic_results)
            ]
        except Exception as _magic_err:
            logger.error(
                f"[magic] pipeline failed: {_magic_err} — falling back to "
                f"Image Agent v4 for this request"
            )
            # Soft-fall-through: trigger the v4 branch on the next iteration
            # by emulating its flag. Simpler than copying its body here.
            use_magic = False  # local override so error doesn't loop
            visual_variants = []
        visualist_meta = None
        # If we successfully generated variants via magic, skip the v4 / v2
        # branches below. If magic failed and visual_variants is empty, fall
        # through to v4.
        if not visual_variants and use_v4:
            # re-fire v4 manually since the elif chain was already entered
            logger.info(
                f"Magic pipeline produced no variants — retrying with "
                f"Image Agent v4 (aspect={aspect_ratio})"
            )
            try:
                from services.image_agent_v4 import generate_image_variants_v4
                visual_variants = generate_image_variants_v4(
                    refined_brief=refined_brief,
                    content_data=content_data,
                    recommendation=content_data.get("recommendation") or {},
                    user_context=user_context,
                    primary_color=primary_color,
                    logo_bytes=logo_bytes,
                    aspect_ratio=aspect_ratio,
                    n_variants=3,
                    client=client,
                )
            except Exception as _v4_err:
                logger.error(f"[image_v4] fallback also failed: {_v4_err}")
                visual_variants = []
    elif use_v4:
        # v4: 5 distinct image variants from the AI-recommended copy +
        # campaign brief. No templates. No overlay compositor.
        logger.info(f"Using Image Agent v4 (5 variants, aspect={aspect_ratio}, model={os.getenv('IMAGE_AGENT_V4_MODEL', 'gemini-3.1-flash-image')})")
        try:
            from services.image_agent_v4 import generate_image_variants_v4
            visual_variants = generate_image_variants_v4(
                refined_brief=refined_brief,
                content_data=content_data,
                recommendation=content_data.get("recommendation") or {},
                user_context=user_context,
                primary_color=primary_color,
                # Logo bytes are resolved upstream in routers/content.py
                # (uploaded file > explicit URL > DNA logo_url). Passing them
                # in lets the image model see the exact authorised mark as a
                # reference; the prompt forbids redrawing or modifying it.
                logo_bytes=logo_bytes,
                aspect_ratio=aspect_ratio,
                n_variants=3,
                client=client,
            )
        except Exception as _v4_err:
            logger.error(f"[image_v4] pipeline failed: {_v4_err} — returning empty visuals")
            visual_variants = []
        visualist_meta = None
    elif use_v2:
        logger.info(f"Using Freeform pipeline (5 variants, aspect={aspect_ratio})")
        # Resolve the active DNA (product-scoped if product_name present, else
        # company-level). Freeform agent reads DNA directly — no separate
        # _visualist_v2_agent call, no DNA-override massaging.
        try:
            from routers.content import _active_dna as _active_dna_helper
            _dna = _active_dna_helper(user, product_name) if user else {}
        except Exception:
            _dna = (getattr(user, "business_dna", None) or {}) if user else {}
            if isinstance(_dna, dict) and product_name:
                _prods = _dna.get("products") or {}
                if product_name in _prods:
                    _dna = {**_dna, **(_prods[product_name] or {})}

        visual_variants = _generate_visuals_v2(
            refined_brief,
            _dna,
            logo_bytes,
            primary_color,
            aspect_ratio,
            user,
            3,  # n_variants — freeform tile count (cost-trimmed from 5 → 3 May 2026)
            None,  # visualist_output (legacy)
            product_name,  # NEW: product-scope V2 PIL pipeline so multi-product
                           # accounts (e.g. Zyntegrate inside Z-Ninth) don't
                           # leak parent brand into the rendered images.
        )
        first = (visual_variants[0] if visual_variants else {}) or {}
        visualist_meta = {
            "overlay_copy": {
                "headline": first.get("headline", ""),
                "subheading": first.get("subheading", ""),
                "cta": first.get("cta", ""),
            },
        }

        # Persist to gallery (best-effort — failure here must not kill the response)
        try:
            if user and visual_variants:
                from core.database import SessionLocal
                from models import GeneratedCampaign as _GC
                campaign_id = first.get("campaign_id")
                _db = SessionLocal()
                try:
                    _db.add(_GC(
                        user_id=user.id,
                        campaign_id=campaign_id,
                        headline=first.get("headline"),
                        subheading=first.get("subheading"),
                        cta=first.get("cta"),
                        primary_color=primary_color,
                        aspect_ratio=aspect_ratio,
                        visuals=visual_variants,
                    ))
                    _db.commit()
                    logger.info(f"Gallery: persisted campaign {campaign_id} for user {user.id}")
                finally:
                    _db.close()
        except Exception as persist_err:
            logger.warning(f"Gallery persist failed (non-fatal): {persist_err}")
    else:
        # Legacy narrative-device path REMOVED (May 2026). Even when env
        # USE_VISUALIST_V2 is set to false, we route to the freeform path
        # so the user always gets the new 5-variant output. The env flag
        # is now honored only by post_type=text/video/document above.
        logger.warning(
            "USE_VISUALIST_V2 disabled but post_type=image — running Freeform anyway"
        )
        try:
            from routers.content import _active_dna as _active_dna_helper
            _dna = _active_dna_helper(user, product_name) if user else {}
        except Exception:
            _dna = (getattr(user, "business_dna", None) or {}) if user else {}
        visual_variants = _generate_visuals_v2(
            refined_brief, _dna, logo_bytes, primary_color, aspect_ratio, user, 3,
            None, product_name,
        )
        first = (visual_variants[0] if visual_variants else {}) or {}
        visualist_meta = {
            "overlay_copy": {
                "headline": first.get("headline", ""),
                "subheading": first.get("subheading", ""),
                "cta": first.get("cta", ""),
            },
        }

    _stage_times["visuals"] = _trace_end("visuals", _t_stage)
    logger.info(f"[TIMING] stage=visuals dur={_stage_times['visuals']:.2f}s skipped={post_type in ('text','video','document')}")

    # 5b. Image Check — local-only post-generation visual audit. Runs in
    # parallel on the 3 image variants, downloads each PNG, asks Gemini
    # vision to spot typos / duplicates / hex leaks / AI-look. Bullet
    # findings are surfaced on each visual dict (`audit`) and written to
    # the local CSV. No-op on Lambda.
    image_audits: list[str] = []
    # Skip image_check globally now. Originally it audited single PNGs for
    # typos / hex leaks / AI look via Gemini vision, but:
    #   - For carousels (post_type=document) it chokes on PDFs entirely.
    #   - For image posts it burns 25-65s on every request and the audit
    #     output isn't consumed anywhere critical downstream — gpt-image-2
    #     quality at high/medium is reliable enough that this vision QA
    #     doesn't earn its wall-clock cost.
    # Env override CAROUSEL_KEEP_IMAGE_CHECK=true to re-enable temporarily
    # for debugging bad renders.
    import os as _os_ic
    _keep_ic = _os_ic.getenv("KEEP_IMAGE_CHECK", "false").lower() in ("1","true","yes","on")
    _skip_image_check = (not _keep_ic) or (post_type == "document")
    if visual_variants and not _skip_image_check:
        try:
            from services.image_check_agent import audit_image_variants_parallel
            _t_stage = _trace_begin("image_check")
            # The copywriter's recommended copy block (joined for context).
            _copy = ""
            try:
                _content = content_data.get("content") or {}
                _best = (content_data.get("recommendation") or {}).get("best_variant")
                if _best and isinstance(_content, dict):
                    _copy = "\n\n".join(
                        f"--- {p.upper()} ---\n{(_content.get(p) or {}).get(_best, '')}"
                        for p in _content.keys()
                    )
            except Exception:
                _copy = ""
            image_audits = audit_image_variants_parallel(
                visual_variants, refined_brief, _copy, client,
            )
            # Stash on the visuals so the frontend can also surface them later.
            for i, v in enumerate(visual_variants):
                if i < len(image_audits) and isinstance(v, dict):
                    v["audit"] = image_audits[i]
            _stage_times["image_check"] = _trace_end("image_check", _t_stage)
            logger.info(f"[TIMING] stage=image_check dur={_stage_times['image_check']:.2f}s")
        except Exception as _ic_err:
            logger.warning(f"[image_check] orchestrator hook failed: {_ic_err}")
    elif _skip_image_check:
        logger.info("[image_check] skipped — post_type='document' (carousel)")

    # 6. Final Payload Assembly
    # `pipeline` field is a stamped marker so the frontend / DevTools can
    # verify which generation path produced the response. After May 2026 the
    # only path is freeform — if you ever see anything other than
    # "freeform_v3" in this field, the request is being served by stale code.
    #
    # Web-grounded research surfaces `sources` and `web_searches` at the top
    # level too so the frontend doesn't have to dig into research_report._grounding
    # to render the Sources panel.
    _research_grounding = (research or {}).get("_grounding") or {}
    assembled_payload = {
        "refined_brief": refined_brief,
        "research_report": research,
        "recommendation": content_data.get("recommendation"),
        "content": content_data.get("content"),
        "visuals": visual_variants,
        "visualist_meta": visualist_meta,  # v2 only — None in legacy
        "sources": (research or {}).get("sources") or _research_grounding.get("sources") or [],
        "web_searches": _research_grounding.get("queries") or [],
        "search_entry_point_html": _research_grounding.get("search_entry_point_html"),
        "cultural_calendar": cultural_calendar,
        # Top-level mirror of research.festival_alerts so the frontend
        # can render the "you might have forgotten today is X" banner
        # without digging into research_report.
        "festival_alerts": (research or {}).get("festival_alerts") or [],
        "pipeline": "freeform_v3",
        # Per-stage wall-clock durations in seconds (refine, cultural,
        # research, content, visuals, critic). Surfaced so the local CSV
        # logger can record them per row without re-parsing the logs.
        "stage_times": dict(_stage_times),
    }

    # 7. Critic Verification — skip for non-image post types. The critic
    # primarily audits the AI-generated visuals for brand consistency; when
    # there are no visuals it's a 40s no-op that just re-reads the variant
    # copy the content agent already produced. Saves ~40s on text posts.
    _t_stage = _trace_begin("critic")
    if post_type in ("text", "video", "document"):
        logger.info(f"Skipping critic — post_type={post_type!r}")
        critic_report = {"is_valid": True, "critique": "Skipped for non-image post type."}
    else:
        critic_report = _critic_agent(assembled_payload, campaign_brief, user_context)
    _stage_times["critic"] = _trace_end("critic", _t_stage)
    logger.info(f"[TIMING] stage=critic dur={_stage_times['critic']:.2f}s skipped={post_type in ('text','video','document')}")

    _total = _t.monotonic() - _overall_t0
    logger.info(
        f"[TIMING] total dur={_total:.2f}s post_type={post_type!r} "
        f"(refine={_stage_times.get('refine',0):.1f} research={_stage_times.get('research',0):.1f} "
        f"content={_stage_times.get('content',0):.1f} visuals={_stage_times.get('visuals',0):.1f} "
        f"critic={_stage_times.get('critic',0):.1f})"
    )
    # ============================================================
    # TRACE SUMMARY — single block you can copy into a ticket. Shows
    # every stage with its duration and percentage of total, in
    # execution order. Easy to spot the bottleneck.
    # ============================================================
    _order = ["refine", "cultural", "research", "content", "visuals", "image_check", "critic"]
    _summary_lines = [
        "",
        "============================================================",
        "  [TRACE SUMMARY] /generate-content",
        f"  total = {_total:6.2f}s   post_type={post_type!r}",
        "  ---------------------------------------------------------",
        f"  {'stage':<14} {'dur (s)':>9} {'pct':>7}",
        "  ---------------------------------------------------------",
    ]
    for _name in _order:
        _d = _stage_times.get(_name)
        if _d is None:
            _summary_lines.append(f"  {_name:<14} {'--':>9} {'--':>7}")
        else:
            _pct = (100.0 * _d / _total) if _total else 0.0
            _summary_lines.append(f"  {_name:<14} {_d:>9.2f} {_pct:>6.1f}%")
    _summary_lines.append("============================================================")
    logger.info("\n".join(_summary_lines))

    logger.info("Multi-Agent Content Generation Complete.")
    return {**assembled_payload, "verification": critic_report}


def generate_campaign_plan(campaign_brief, platforms, days=7, user=None, extra_context="", post_type="image"):
    """Generates a multi-day campaign strategy plan with user and document context.

    `post_type` (image | text | video | document) is enforced as the locked
    content_type on every slot. The planner also receives a planning-window
    cultural calendar so it can auto-inject festival slots on the correct day.
    """
    logger.info(f"Generating {days}-Day Campaign Plan for platforms: {', '.join(platforms)} (post_type={post_type})")

    user_context = ""
    if user:
        user_context, _, _ = _build_user_context(user, extra_context)

    full_brief = (
        f"{campaign_brief}\n\nRESOURCE DOCUMENTS:\n{extra_context}"
        if extra_context and not isinstance(extra_context, dict)
        else campaign_brief
    )

    refinement = _refine_brief_agent(full_brief, user_context)
    refined_brief = refinement.get("refined_brief", campaign_brief)
    research = _research_agent(refined_brief, user_context)
    # Fetch the multi-day festival calendar BEFORE planning so the planner can
    # inject festival slots. Cached per (today_iso, days) so concurrent users
    # planning the same window pay for it once.
    planning_window_calendar = _get_planning_window_calendar(days)
    plan_data = _planner_agent(
        refined_brief, research, platforms, days, user_context,
        post_type=post_type,
        planning_window_calendar=planning_window_calendar,
    )

    actual_plan = []
    if isinstance(plan_data, list):
        actual_plan = plan_data
    elif isinstance(plan_data, dict):
        actual_plan = plan_data.get("plan", plan_data.get("campaign_plan", []))

    for slot in actual_plan:
        try:
            date_str = slot.get('date', '')
            if date_str:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                slot['day'] = dt.strftime("%A").upper()
        except Exception as e:
            logger.warning(f"Failed to normalize day for slot: {e}")
        # Backstop defaults so older planner outputs (or partial JSON) don't
        # break the downstream UI / booking code.
        slot.setdefault("needs_research", True)
        slot.setdefault("research_reason", "")
        slot.setdefault("is_festival", False)
        slot.setdefault("festival_name", None)

    return {
        "refined_brief": refined_brief,
        "research": research,
        "plan": actual_plan,
        "planning_window_calendar": planning_window_calendar,
    }


def generate_single_post_content(brief, topic, platforms, user=None):
    """Fast-tracked generation for a single post in a bulk campaign."""
    logger.info(f"Fast-generating content for topic: {topic}")

    user_context = ""
    if user:
        dna = getattr(user, 'business_dna', {}) or {}
        doc_context = ""
        entity_docs = dna.get('documents', [])
        if entity_docs:
            doc_context = "\nREFERENCE DOCUMENTS:\n"
            for doc in entity_docs:
                doc_context += f"- {doc.get('name')}\n"
        user_context = f"""
        Entity Name: {getattr(user, 'company_name', None) or dna.get('company_name', 'N/A')}
        Overview: {dna.get('overview', 'N/A')}
        Brand Tone: {', '.join(dna.get('brand_tone', [])) if dna.get('brand_tone') else 'N/A'}
        {doc_context}
        Timezone: {getattr(user, 'timezone', None) or 'UTC'}
        """

    platforms_str = ", ".join(platforms)
    prompt = f"""
    You are a Lead Copywriter for the following profile:
    {user_context}

    Create 2 strategic variants for a post on these platforms: {platforms_str}.

    CAMPAIGN BRIEF: "{brief}"
    POST TOPIC/TITLE: "{topic}"

    STRICT RULE: Incorporate specific facts or tone from the profile provided.

    Return STRICTLY in this JSON format:
    {{
        "content": {{
            "platform_name": {{ "reach": "...", "engagement": "..." }}
        }}
    }}
    """
    data = _call_agent("BULK_COPYWRITER", prompt)
    if "content" in data:
        data["content"] = {k.lower(): v for k, v in data["content"].items()}
    return data


def refine_for_twitter_agent(content, user_context=""):
    """Agent: Specifically shortens a post to fit Twitter's character limit (280) while preserving ROI.
    
    Returns: {"shortened_content": "..."}
    """
    logger.info("Calling Refine-for-Twitter Agent...")
    prompt = f"""
    You are a Twitter Optimization Expert. 
    Your objective is to take the following content and condense it to under 240-280 characters for Twitter (X).

    ### CORE PRINCIPLES:
    1. **Preserve the Lead**: Keep the hook or the most impactful first sentence.
    2. **Preserve the CTA**: If there's a link or a call-to-action, do not remove it.
    3. **Ruthless Editing**: Remove fluff, corporate jargon, and decorative adjectives.
    4. **Formatting**: Use clean line breaks. No more than 1 hashtag.

    ### CONTEXT:
    {user_context}

    ### ORIGINAL CONTENT:
    \"\"\"
    {content}
    \"\"\"

    Return STRICTLY this JSON format (no markdown):
    {{ "shortened_content": "..." }}
    """
    result = _call_agent("TWITTER_OPTIMIZER", prompt)
    if not isinstance(result, dict) or "shortened_content" not in result:
        # Fallback if AI fails or returns weird JSON
        return {"shortened_content": content[:277] + "..."}
    return result


def analyze_comments_sentiment(comments: list[dict], post_context: str = None):
    """Agent: Analyze sentiment of social media comments with Business Awareness.
    
    `comments`: list of normalized dicts each having 'message' and 'id'.
    `post_context`: Original content/caption of the post being analyzed.
    Returns: {overall_score, overall_summary, top_insight, analyzed_comments: [{id, sentiment_label, reasoning}]}
    """
    if not comments:
        return {"error": "No comments to analyze"}
    
    comments_block = ""
    for i, c in enumerate(comments):
        comments_block += f"[{i}] (ID: {c.get('id')}) Message: {c.get('message')}\n"

    prompt = f"""
    You are a Senior Business Performance Auditor and Linguistic Expert.
    Your objective is to perform a POWER-ANALYSIS on the following comments. 

    ### THE 80/20 ANALYSIS PRINCIPLE:
    - **80% WEIGHT (Contextual Relevance)**: How directly does the comment relate to the Post Content below? Does it address the specific business offer, problem, or call-to-action?
    - **20% WEIGHT (Linguistic Sentiment)**: What is the emotional tone (Praise, Anger, Sarcasm)?

    STEP 1: ANALYZE THE POST CONTENT (The Context)
    Post Content:
    \"\"\"
    {post_context or "N/A - General Business Post"}
    \"\"\"
    Analyze the above: What is the Core Business Offer (CBO) or Target Solution?

    STEP 2: AUDIT EACH COMMENT
    COMMENTS TO AUDIT:
    \"\"\"
    {comments_block}
    \"\"\"

    ### CLASSIFICATION SYSTEM:
    1. **POSITIVE (Success/Lead)**:
       - High Relevance (80%): Comment asks about the specific solution, pricing, or how to bridge the gap mentioned in the post.
       - Positive Tone (20%): Gratitude, support, or praise.
       - RULE: If a user asks a deep question about the business topic (e.g., "How are you bridging that gap opreationally?"), it is 100% SUCCESS/POSITIVE.

    2. **NEUTRAL (Passive Engagement)**:
       - Low/Generic Relevance: "Interesting", "Cool", "Ok".
       - Neutral Tone: User tagging (@user) or generic informational statements.

    3. **NEGATIVE (Irrelevant/Toxic)**:
       - Negative/Irrelevant content: Abuse, toxicity, irrelevant spam, or purely destructive complaints ("This is junk") without any intent to learn or engage.

    Return STRICTLY JSON format:
    {{
      "overall_score": 0-100,
      "overall_summary": "Summary of business engagement quality.",
      "top_insight": "Actionable insight based on the 80/20 analysis.",
      "sentiment_counts": {{ "positive": 0, "neutral": 0, "negative": 0 }},
      "analyzed_comments": [
        {{ 
          "id": "Return the exact ID from the prompt", 
          "sentiment_label": "Positive | Neutral | Negative", 
          "sentiment_score": 0-100,
          "reasoning": "80% Relevance Check + 20% Tone Check = Final Decision." 
        }}
      ]
    }}
    """
    # Use temperature=0 for absolute consistency (same input = same output)
    return _call_agent("SENTIMENT_ANALYZER", prompt, model_name='gemini-flash-lite-latest', temperature=0.0)


def generate_replies_for_comments(comments: list[dict], post_context: str = None, business_dna: str = None):
    """Agent: Generate professional, context-aware replies for social media comments.
    
    `comments`: list of normalized dicts with 'message' and 'id'.
    `post_context`: Original content of the post to provide contextual grounding.
    `business_dna`: Brand voice, values, and product details for persona matching.
    
    Returns: {replies: [{id, generated_reply, reasoning}]}
    """
    if not comments:
        return {"replies": []}

    comments_block = ""
    for i, c in enumerate(comments):
        comments_block += f"[{i}] (ID: {c.get('id')}) Message: {c.get('message')}\n"

    prompt = f"""
    You are a World-Class Community Manager and Brand Voice Expert.
    Your objective is to generate high-quality, professional, and helpful replies to the comments below.

    ### BRAND IDENTITY (BUSINESS DNA):
    \"\"\"
    {business_dna or "Professional Business"}
    \"\"\"

    ### POST CONTEXT (What the comments are responding to):
    \"\"\"
    {post_context or "General brand engagement."}
    \"\"\"

    ### REPLIES GUIDELINES:
    1. **Be Context-Aware**:
       - If the post is about **Hiring/Recruitment**: If an email is mentioned in the post, tell the commenter to send their resume to that email. If no email is in the post but one exists in the DNA, use the DNA email.
       - If the post is about a **Product/Offer**: Answer questions about features based on the DNA. Be helpful but not pushy.
       - If the comment is **Positive/Praise**: Respond with gratitude and warmth.
       - If the comment is **Neutral/Question**: Answer clearly and invite further conversation.
       - If the comment is **Negative/Toxic**: Be professional, de-escalate, or offer to take it to private messages. Do NOT be defensive.

    2. **Tone & Style**:
       - Match the "Brand Tone" from the DNA (e.g., professional, playful, authoritative).
       - Keep replies concise (usually 1-3 sentences).
       - Do NOT use generic corporate jargon (the "BANNED PHRASES" listed in your training).
       - Use the user's name if provided in the comment metadata (if not, use "Hi!" or just the response).

    3. **Accuracy**:
       - Never invent facts about the company or product that are not in the DNA.
       - If you don't know the answer, politely suggest they contact support or visit the website.

    ### COMMENTS TO REPLY TO:
    \"\"\"
    {comments_block}
    \"\"\"

    Return STRICTLY JSON format:
    {{
      "replies": [
        {{
          "id": "Return the exact ID from the prompt",
          "generated_reply": "Content of the AI-generated reply.",
          "reasoning": "Brief explanation of why this reply was chosen based on post context."
        }}
      ]
    }}
    """
    return _call_agent("COMMENT_REPLY_AGENT", prompt, model_name='gemini-flash-lite-latest', temperature=0.7)
