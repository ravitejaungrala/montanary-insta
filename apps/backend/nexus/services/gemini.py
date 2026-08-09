"""
Gemini AI client — chat + embeddings for the NEXUS port.

Renamed 2026-05-26 from `nvidia.py` to `gemini.py`. The NVIDIA NIM era
ended a while back when we migrated to Google's `google-genai` SDK; the
legacy filename was historical baggage that made the codebase harder to
read.

Public function signatures are unchanged: `chat_completion`,
`analyze_product`, `refine_summary`, `embed_text`, `embed_batch`,
`extract_json`.

Models (constants — intentionally NOT env-tunable):
  - gemini-3.1-flash-lite       — chat / JSON extraction / refinement
  - gemini-embedding-001   — 3072-dim passage/query embeddings

The embed dimension is hardcoded to 3072 to match the Pinecone `nexus-kb`
index dimension (also hardcoded in `pinecone_kb.py`). A mismatch would
silently corrupt retrieval — the Pinecone index dim is fixed at create
time and can't be resized, so keeping these as code constants prevents
accidental drift across environments.

GEMINI_API_KEY is the only env var read here — it's a secret and must
stay outside the code.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pipelyt.nexus.gemini")

# ── Model constants (intentionally NOT env-tunable) ────────────────────────
# Changing these requires a code review + matching changes elsewhere:
#   - EMBED_MODEL / EMBED_DIMS must match the Pinecone index dim
#     (see pinecone_kb._EMBED_DIMS)
#   - CHAT_MODEL determines pricing + capability tier for every chat call
#   2026-06-18 — All GTM text generation is hardcoded to gemini-3.1-flash-lite
#   (no env override). This is the SINGLE source of truth for the chat model:
#   every other GTM module imports CHAT_MODEL instead of repeating the literal.
CHAT_MODEL = "gemini-3.1-flash-lite"
# Product/ICP extraction reuses the same model — no separate analyze tier.
ANALYZE_MODEL = CHAT_MODEL
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIMS = 3072

# Max page-content chars we feed Gemini in one prompt. Sized to the MODEL'S
# capacity, not an arbitrary number: Gemini 2.5 (Flash/Pro) accept ~1,000,000
# INPUT tokens (~4M chars). We budget ~3M chars (~750K tokens) so NO realistic
# website ever loses data, while leaving generous headroom for the prompt
# scaffolding (allowed-value lists, instructions) + the model's output. The only
# thing this ever bounds is a pathological multi-MB site. Env-tunable so a model
# swap (different context window) is a config change, not a code change.
GEMINI_MAX_INPUT_CHARS = int(
    os.getenv("NEXUS_GEMINI_MAX_INPUT_CHARS", str(3_000_000))
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    """Lazy-init the google-genai client. Cached per Lambda warm container."""
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set — cannot call Gemini. "
            "Configure it in the Lambda environment."
        )

    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    # HARD per-call HTTP ceiling for EVERY call through this shared client.
    # Without it the SDK can wait on a hung Gemini connection FOREVER —
    # observed as /analyze freezing right after "AFC is enabled" with no
    # further log line. 120s clears the slowest normal call (analyze_product
    # on 2.5-pro ≈ 30-40s) while bounding a hang. Env-tunable.
    _timeout_ms = int(os.getenv("GEMINI_HTTP_TIMEOUT_MS", "120000") or "120000")
    _client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=_timeout_ms),
    )
    return _client


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*|```", re.IGNORECASE)


def _strip_artifacts(text: str) -> str:
    text = _THINK_TAG_RE.sub("", text or "")
    text = _CODE_FENCE_RE.sub("", text)
    return text.strip()


def _repair_truncated_json(s: str) -> Optional[str]:
    """Best-effort repair for JSON that was truncated mid-stream.

    Gemini occasionally cuts a response off mid-string when its output
    budget runs out (even with `thinking_budget=0`). The result is
    syntactically invalid — a string opens but never closes, then the
    object/array's closing brackets are missing entirely. This helper
    walks the prefix that DID arrive, tracks bracket/quote depth, and
    appends the minimum suffix needed to close everything cleanly.

    Returns the patched string when a repair is possible, or None when
    the input is too garbled to salvage. The caller should still pass
    the result through json.loads to confirm it parses.
    """
    if not s:
        return None
    stack: List[str] = []
    in_string = False
    escape = False
    last_safe_end = -1  # last byte position where the JSON would have been valid

    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    last_safe_end = i
            else:
                return None  # mismatched bracket — can't trust this prefix

    # If the JSON is already balanced and string-closed, no repair needed.
    if not in_string and not stack:
        return s if last_safe_end >= 0 else None

    # Trim a trailing partial token (e.g. ", "key":") that would prevent
    # the closer from parsing. Walk back while we're inside whitespace
    # or a partial key/value introducer.
    tail_trim = s
    if in_string:
        tail_trim = tail_trim + '"'  # close the dangling string first
    # Drop a trailing comma — `[1, 2,]` would re-fail otherwise. Also
    # drop a trailing colon (e.g. `{"key":`) since no value followed.
    tail_trim = tail_trim.rstrip()
    while tail_trim and tail_trim[-1] in ",:":
        tail_trim = tail_trim[:-1].rstrip()
    # Append the closing brackets in reverse-stack order.
    repaired = tail_trim + "".join(reversed(stack))
    return repaired


def extract_json(raw: str) -> Any:
    """Pull the first parseable JSON object/array out of a model response.
    Tolerates code fences, <think>…</think>, surrounding prose, and as a
    last-resort fallback attempts to repair JSON that was truncated mid-
    stream by the model running out of output budget."""
    cleaned = _strip_artifacts(raw)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    obj_match = re.search(r"\{[\s\S]*\}", cleaned)
    arr_match = re.search(r"\[[\s\S]*\]", cleaned)
    candidates = []
    if obj_match:
        candidates.append(obj_match.group(0))
    if arr_match:
        candidates.append(arr_match.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            continue
    # Defensive last resort: the response is likely truncated mid-string.
    # Find the largest plausible JSON prefix and ask `_repair_truncated_json`
    # to close it. We try both the cleaned full text and the longest
    # object/array regex match, since the model may have emitted prose
    # before the JSON started.
    repair_candidates: List[str] = []
    # Prefer the longest prefix that starts with a JSON opener.
    for opener in ("{", "["):
        idx = cleaned.find(opener)
        if idx >= 0:
            repair_candidates.append(cleaned[idx:])
    repair_candidates.append(cleaned)
    for cand in repair_candidates:
        patched = _repair_truncated_json(cand)
        if patched:
            try:
                value = json.loads(patched)
                logger.warning(
                    "extract_json: salvaged truncated response (orig=%d chars, "
                    "patched=%d chars)", len(cand), len(patched),
                )
                return value
            except Exception:
                continue
    raise ValueError(f"No valid JSON in Gemini response. First 240 chars: {raw[:240]!r}")


# ---------------------------------------------------------------------------
# Chat completion
# ---------------------------------------------------------------------------

def chat_completion(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = 2048,
    response_format_json: bool = False,
) -> str:
    """Single-shot chat completion via Gemini. Returns raw assistant string.

    Pass ``max_tokens=None`` to omit the output cap entirely so the model
    uses its own full ceiling (no truncation of long JSON responses)."""
    from google.genai import types  # type: ignore

    client = _get_client()
    # Ignore caller-supplied legacy model names (e.g. "qwen/...", "meta/...") —
    # they have no Gemini equivalent; fall back to CHAT_MODEL.
    chosen_model = model if (model and model.startswith("gemini")) else CHAT_MODEL

    config_kwargs: Dict[str, Any] = {
        "system_instruction": system,
        "temperature": temperature,
    }
    if max_tokens is not None:
        config_kwargs["max_output_tokens"] = max_tokens
    if response_format_json:
        config_kwargs["response_mime_type"] = "application/json"

    # Thinking-budget handling — depends on the model.
    # - Flash (2.5 Flash): supports `thinking_budget=0` to disable
    #   hidden chain-of-thought. Without it the model burns most of
    #   `max_output_tokens` on thoughts and the visible JSON gets cut off.
    # - Pro (2.5 Pro): REQUIRES thinking mode — the API returns
    #   400 INVALID_ARGUMENT "Budget 0 is invalid. This model only works
    #   in thinking mode." if we try to disable it. We leave the budget
    #   unset (Pro picks its own internal default) so the call succeeds.
    # Wrapped in try/except because older google-genai SDKs don't expose
    # `ThinkingConfig`.
    _model_lower = (chosen_model or "").lower()
    if "pro" not in _model_lower:
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

    started = time.time()

    def _do_call(cfg: Dict[str, Any]):
        return client.models.generate_content(
            model=chosen_model,
            contents=user,
            config=types.GenerateContentConfig(**cfg),
        )

    try:
        resp = _do_call(config_kwargs)
    except Exception as exc:
        # Robustness for "thinking" models (e.g. Gemini 3.5 Flash): some
        # reject `thinking_budget=0` with 400 INVALID_ARGUMENT. If that's the
        # failure, retry ONCE without thinking_config so the model uses its own
        # default and the call still succeeds.
        _msg = str(exc).lower()
        if "thinking_config" in config_kwargs and ("budget" in _msg or "thinking" in _msg):
            try:
                _cfg = {k: v for k, v in config_kwargs.items() if k != "thinking_config"}
                resp = _do_call(_cfg)
            except Exception as exc2:
                logger.exception("Gemini chat call failed after thinking-retry (%s): %s", chosen_model, exc2)
                raise
        elif any(t in _msg for t in (
            "timeout", "timed out", "deadline", "504", "503",
            "unavailable", "connection",
        )):
            # Transient hang / outage (now BOUNDED by the client's HTTP
            # timeout instead of waiting forever) — retry ONCE on a fresh
            # connection; a second failure propagates to the caller.
            logger.warning(
                "Gemini chat transient failure (%s): %s — retrying once",
                chosen_model, str(exc)[:160],
            )
            resp = _do_call(config_kwargs)
        else:
            logger.exception("Gemini chat call failed (%s): %s", chosen_model, exc)
            raise

    elapsed_ms = int((time.time() - started) * 1000)
    content = (resp.text or "").strip()
    logger.info("Gemini chat ok model=%s elapsed_ms=%d", chosen_model, elapsed_ms)
    return content


async def chat_completion_async(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = 2048,
    response_format_json: bool = False,
) -> str:
    """Async sibling of `chat_completion` — same signature, returns the
    same raw string.

    Why this exists (2026-05-29): per-lead email generation parallelism.
    The sync `chat_completion` blocks the event loop, so fanning out 20
    leads via `asyncio.gather` would still serialize them. The
    `google-genai` SDK exposes `client.aio.models.generate_content`
    which is a true coroutine — N concurrent calls share the FastAPI
    event loop and finish in ~max(call_latency) instead of
    sum(call_latency).

    Speed:
      sync   20 leads × ~4 s each = ~80 s
      async  20 leads via gather  = ~4-6 s (dominated by slowest call)

    All other semantics — system prompt, thinking_budget=0, response
    format, model selection — match the sync function so callers can
    swap one for the other freely.
    """
    from google.genai import types  # type: ignore

    client = _get_client()
    chosen_model = model if (model and model.startswith("gemini")) else CHAT_MODEL

    config_kwargs: Dict[str, Any] = {
        "system_instruction": system,
        "temperature": temperature,
    }
    if max_tokens is not None:
        config_kwargs["max_output_tokens"] = max_tokens
    if response_format_json:
        config_kwargs["response_mime_type"] = "application/json"

    # Same anti-truncation guardrail as the sync path — disable hidden
    # thinking tokens (Flash only — Pro requires thinking mode).
    _model_lower = (chosen_model or "").lower()
    if "pro" not in _model_lower:
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

    started = time.time()
    try:
        resp = await client.aio.models.generate_content(
            model=chosen_model,
            contents=user,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as exc:
        logger.exception("Gemini async chat failed (%s): %s", chosen_model, exc)
        raise

    elapsed_ms = int((time.time() - started) * 1000)
    content = (resp.text or "").strip()
    logger.info(
        "Gemini async chat ok model=%s elapsed_ms=%d",
        chosen_model, elapsed_ms,
    )
    return content


# ---------------------------------------------------------------------------
# Product analysis prompt — modeled on legacy nvidiaService.analyzeProduct
# ---------------------------------------------------------------------------

# ─────────────────────────────────────────────────────────────────────────────
# _ANALYZE_SYSTEM — restructured 2026-05-28 with Role / Objective / Steps /
# Rules / Examples scaffold. The ICP block now emits 7 CANONICAL KEYS that
# map 1:1 to Apollo's lead-search filter fields — no naming drift, no silent
# field renames downstream. The two keys whose Apollo wire name differs are
# documented inline:
#   organization_industries  →  organization_industry_tag_ids   (lookup at
#                                                                Apollo boundary)
#   buyer_technologies       →  currently_using_any_of_technology_uids
#                                                                (rename only)
# Every other canonical key === its Apollo wire name.
#
# Each ICP value is a {values, confidence, evidence} triple so a downstream
# step can drop low-confidence guesses before sending them to Apollo.
# ─────────────────────────────────────────────────────────────────────────────
# The pre-2026-05-29 single-prompt strategy is replaced by three
# type-specific prompts (product / service / gcc) plus a shared rules +
# schema block, all living in _analyze_prompts.py. select_analyze_prompt()
# returns the right one based on the entity_type the user picked on the
# wizard URL step. _ANALYZE_SYSTEM is kept as a back-compat alias =
# the PRODUCT prompt (the historical default).
from nexus.services._analyze_prompts import (
    select_analyze_prompt,
    _ANALYZE_SYSTEM_PRODUCT,
    _ANALYZE_SYSTEM_SERVICE,
    _ANALYZE_SYSTEM_GCC,
)
_ANALYZE_SYSTEM = _ANALYZE_SYSTEM_PRODUCT


# Canonical ICP keys that map 1:1 to Apollo filter dimensions. The Gemini
# prompt emits these names; the suggest-targeting endpoint passes them
# through; the React state stores them; the Apollo body builder reads them.
# Two of the seven are renamed at the Apollo boundary (see _icp_to_apollo_body):
#   organization_industries -> organization_industry_tag_ids
#   buyer_technologies      -> currently_using_any_of_technology_uids
CANONICAL_ICP_FILTER_KEYS = (
    "person_titles",
    "person_locations",
    # person_states was added 2026-06-02 as a separate ICP field but
    # removed the same day — Apollo has only ONE location filter
    # (`person_locations`), so showing two rows in the wizard was UI
    # theater. The prompt now instructs Gemini to emit at the most
    # specific granularity the page supports (e.g. "Texas, United
    # States" rather than splitting state out into its own field).
    # person_seniorities removed 2026-06-02 — titles already imply
    # seniority and sending both was over-narrowing Apollo's match set.
    # person_departments removed 2026-06-08 — same rationale: titles
    # already imply department, and sending both over-narrowed discovery.
    "organization_industries",
    "revenue_range",
    "buyer_technologies",
)

# 2026-06-02 — strict-quote enforcement: for these 5 fields the model MUST
# back every emitted value with a verbatim page quote in `evidence`. The
# `_drop_unevidenced_values` helper below drops any value whose text doesn't
# appear (loosely) inside the evidence string, even if the model claimed
# high confidence. Titles + seniorities are EXEMPT — pages rarely name
# target buyer roles literally, so we allow inference there.
_STRICT_QUOTE_FIELDS = frozenset({
    "person_locations",
    "organization_industries",
    "revenue_range",
    "buyer_technologies",
})

# Common synonym map so a literal page quote that uses a different surface
# form still satisfies the strict-quote check (e.g. page says "USA", model
# emits "United States" — still considered evidenced).
_VALUE_SYNONYMS: Dict[str, set] = {
    # ── Locations ────────────────────────────────────────────────
    "united states": {"usa", "u.s.", "u.s.a.", "us", "america"},
    "united kingdom": {"uk", "u.k.", "britain", "england", "great britain"},
    "united arab emirates": {"uae", "u.a.e."},
    "south korea": {"korea"},
    # ── Industries: closed-list canonical ↔ on-page variants ─────
    "financial services": {"finance", "bfsi"},
    "information technology": {"it", "info tech", "infotech"},
    "human resources": {"hr"},
    "research and development": {"r&d", "rd"},
    "marketing technology": {"martech"},
    "e-commerce": {"ecommerce", "online retail", "online commerce"},
    "fintech": {"financial technology"},
    "edtech": {"educational technology"},
    "proptech": {"property technology", "real estate technology"},
    "biotech": {"biotechnology"},
    "saas": {"software as a service"},
    "consumer goods & fmcg": {"consumer goods", "fmcg", "consumer brands", "cpg"},
    "logistics & supply chain": {"logistics", "supply chain"},
    "media & publishing": {"media", "publishing"},
    "food & beverage": {"food", "beverage", "f&b"},
    "hospitality & tourism": {"hospitality", "tourism", "travel"},
    "mining & metals": {"mining", "metals"},
    "oil & gas": {"oil", "gas", "energy"},
    "agriculture & agtech": {"agriculture", "agtech", "farming"},
    "architecture & design": {"architecture", "design"},
    "advertising & marketing": {"advertising", "marketing"},
    "accounting & tax": {"accounting", "tax"},
    "clean energy & renewables": {"clean energy", "renewables", "renewable energy"},
    "data analytics & bi": {"data analytics", "business intelligence", "bi"},
    "education & edtech": {"education", "edtech"},
    "government & public sector": {"government", "public sector", "federal", "state agency"},
    # Closed-list Apollo industry ↔ short/logo forms a site uses instead of
    # the full word (e.g. a "Telecom" client logo). Lets the STRICT guard
    # accept the on-page short form as evidence — without loosening the
    # guard (evidence is still required; we just recognise the variant).
    "telecommunications": {"telecom", "telco", "telecommunication", "telecoms"},
}


import re as _re

# Canonical revenue bands keyed by their text label. Used by the
# revenue-specific matcher below — when the model emits a band, we
# look for ANY dollar amount on the page that falls inside the band's
# range. This is what lets "page says $10 million ad spend" support a
# model-emitted "$10M – $50M" band even though that label text never
# appears verbatim on the page.
_REVENUE_BAND_RANGES_USD: Dict[str, tuple] = {
    "< $1M":         (0,             1_000_000),
    "$1M – $10M":    (1_000_000,     10_000_000),
    "$10M – $50M":   (10_000_000,    50_000_000),
    "$50M – $200M":  (50_000_000,    200_000_000),
    "$200M – $1B":   (200_000_000,   1_000_000_000),
    "$1B+":          (1_000_000_000, 1_000_000_000_000),
}

# Regex finding dollar amounts in free text. Catches:
#   $10M, $10m, $10 m, $10 million, $1.2B, $1.2 billion, $500k, $500K
# Returns the numeric value normalised to USD (integer).
_DOLLAR_AMOUNT_RE = _re_for_dollars = None  # filled below


def _parse_dollar_amounts(text: str) -> List[int]:
    """Find every "$N (million|billion|m|b|k)" pattern in the text and
    return them as integer USD amounts. Used to support the
    revenue_range field — page rarely uses canonical band labels but
    often names concrete dollar amounts in case studies, customer
    descriptions, or buyer ICP statements.
    """
    if not text:
        return []
    pattern = r"\$\s*(\d+(?:\.\d+)?)\s*(billion|million|m|b|k)?\b"
    out: List[int] = []
    for m in _re.finditer(pattern, text.lower()):
        try:
            n = float(m.group(1))
        except (TypeError, ValueError):
            continue
        unit = (m.group(2) or "").strip()
        if unit in ("billion", "b"):
            n *= 1_000_000_000
        elif unit in ("million", "m"):
            n *= 1_000_000
        elif unit == "k":
            n *= 1_000
        # Bare "$10" with no unit is usually a price tag, not a revenue —
        # only count if it's at least $1M-scale so we don't false-match
        # tiny prices. Empty unit and value < 1000 → skip.
        if not unit and n < 1000:
            continue
        out.append(int(n))
    return out


def _revenue_band_contains_any(band_label: str, amounts: List[int]) -> bool:
    """True iff any parsed dollar amount falls inside the band's range."""
    bounds = _REVENUE_BAND_RANGES_USD.get((band_label or "").strip())
    if not bounds:
        return False
    lo, hi = bounds
    return any(lo <= a <= hi for a in amounts)


# Stopwords + connector tokens we ignore when token-matching a value
# against the scrape. "&" and "and" + 1-3 char glue words like "the",
# "of", "to" are too short / generic to constrain the match.
_TOKEN_MATCH_STOPWORDS = frozenset({
    "and", "or", "the", "of", "to", "for", "in", "on", "at", "by",
    "a", "an", "&", "vs",
})


def _meaningful_tokens(value: str) -> List[str]:
    """Tokenize a value into 2+-char tokens we'll use for whole-word
    matching. Strips punctuation, lowercases, removes glue stopwords
    (and / or / the / & / etc). 2-char minimum preserves canonical
    Apollo enums like "it" and "hr"; word-boundary regex elsewhere
    prevents them from matching inside larger words."""
    v = _normalise_for_match(value)
    raw = _re.split(r"[^a-z0-9]+", v)
    return [
        t for t in raw
        if t and t not in _TOKEN_MATCH_STOPWORDS and len(t) >= 2
    ]


def _word_match(needle: str, haystack: str) -> bool:
    """Whole-word case-insensitive search. `needle` is matched as one
    or more tokens bounded by non-word characters. Both sides must
    already be normalised. Returns False on empty needle."""
    if not needle or not haystack:
        return False
    return bool(_re.search(rf"\b{_re.escape(needle)}\b", haystack))


def _value_is_in_evidence(value: str, evidence: str) -> bool:
    """True iff `value` is supported by `evidence` (the full scraped
    page text), case-insensitive, with three matching strategies tried
    in order:

      1. Whole-VALUE word-boundary match — catches "United States",
         "Hyderabad, India", "Banking", "IT" verbatim on the page.
         Word boundary prevents short values ("it", "hr") from matching
         inside larger words ("architecture", "shrubbery").
      2. Synonym map — page may use a different surface form. "United
         States" ↔ "USA"; "E-Commerce" ↔ "ecommerce"; "FinTech" ↔
         "financial technology"; "Consumer Goods & FMCG" ↔ "consumer
         goods" / "fmcg" / "consumer brands".
      3. Token-overlap — for compound labels like "Mining & Metals"
         where the page may not have the literal "&" form, accept if
         EVERY meaningful token of the value appears as a whole word
         in the evidence.
    """
    v = _normalise_for_match(value)
    e = _normalise_for_match(evidence)
    if not v or not e:
        return False

    # 1. Whole-value word match. Word-boundary, not substring, so
    # short values can't match inside larger words.
    if _word_match(v, e):
        return True

    # 2. Synonym map (also word-boundary so short syns like "it" and
    # "us" don't match arbitrary substrings).
    for canon, syns in _VALUE_SYNONYMS.items():
        if v == canon and any(_word_match(s, e) for s in syns):
            return True
        if v in syns and _word_match(canon, e):
            return True

    # 3. Token-overlap fallback. ALL non-stopword tokens must word-
    # boundary-match in the evidence.
    tokens = _meaningful_tokens(value)
    if not tokens:
        return False
    return all(_word_match(t, e) for t in tokens)


def _normalise_for_match(s: str) -> str:
    """Lower-case + strip periods + collapse whitespace.
    Used to fuzz-match an evidence quote or a value against the scraped
    page text.

    2026-06-02 — periods are stripped on BOTH sides so the model's
    "Dallas, USA" matches the page's "Dallas, U.S.A". Without this,
    `"dallas, usa" in "dallas, u.s.a"` is False (different bytes), so
    legitimate location values were getting dropped at the value-in-
    scrape check just because of punctuation styling on the page.
    Other punctuation (commas, parens) is left alone — they survive
    intact so substring matches like "hyderabad, india" still work.
    """
    return " ".join((s or "").lower().replace(".", "").split())


def _salvage_location(value: str, evidence: str) -> Optional[str]:
    """Recover the coarsest STILL-QUOTED form of a comma-separated location
    whose full string failed the strict in-page check.

    The analyze prompt asks the model for the deepest granularity
    ("Dallas, Texas, United States"), but the page often names only some of
    those segments (e.g. "Dallas" + "U.S.A" but never "Texas"). The all-
    tokens-must-match rule then drops the WHOLE value even though part of it
    is literally on the page. This keeps only the segments that are
    individually evidenced (synonyms honoured) and rejoins them in the
    original order — so a real, page-quoted location survives instead of
    being thrown away. Never adds anything not on the page.

    Returns the salvaged location string, or None if nothing is quoted.
    """
    segs = [s.strip() for s in (value or "").split(",") if s.strip()]
    if len(segs) <= 1:
        return None  # single-segment values are handled by the caller
    # Each segment is validated INDIVIDUALLY (synonyms honoured), so a
    # country segment like "United States" survives via the page's "USA"
    # even though "united"/"states" aren't separate page tokens. Don't
    # re-validate the rejoined string with token-overlap — that would
    # wrongly require every token again and undo the synonym match.
    kept = [s for s in segs if _value_is_in_evidence(s, evidence)]
    if not kept:
        return None
    return ", ".join(kept)


def _drop_unevidenced_values(
    field_key: str,
    triple: Dict[str, Any],
    scrape_text_norm: str = "",
) -> Dict[str, Any]:
    """For the 5 strict fields, drop any value whose text (or a known
    synonym) doesn't appear in the SCRAPED PAGE TEXT directly.

    2026-06-02 rewrite. The original implementation required the model's
    `evidence` string to be a literal substring of the scraped page.
    That broke in practice because Gemini ergonomically stitches two
    non-adjacent fragments with "..." (e.g. "Dallas, U.S.A ... Hyderabad,
    India") or paraphrases a paragraph into one sentence ("Success
    stories mention X's retail clients") — both are semantically faithful
    but neither is a literal page substring, so the field was wiped
    even though every value was actually on the page.

    New rule: ignore the narrative quote; check the VALUES directly
    against the scraped page. The model can phrase its evidence however
    it likes — only the page content matters.

    Still catches the original hallucination case ("Germany" with evidence
    "we serve EU"): "germany" isn't on the page → dropped.

    Back-compat: if `scrape_text_norm` wasn't threaded through (legacy
    callers), fall back to the prior evidence-based check so the field
    isn't trivially wiped.
    """
    if field_key not in _STRICT_QUOTE_FIELDS:
        return triple

    values = triple.get("values") or []
    evidence = triple.get("evidence") or ""

    # No values → keep the empty field as-is.
    if not values:
        return triple

    # ── Special case: revenue_range ──────────────────────────────────
    # Canonical band labels ("$10M – $50M") almost never appear
    # verbatim on a marketing site. The page uses natural language
    # like "$10 million in annual spend" or "$10M ARR" or "Fortune 500
    # buyers". Parse any dollar amount out of the scrape and check
    # whether the band's [min, max] range contains it.
    if field_key == "revenue_range" and scrape_text_norm:
        scraped_amounts = _parse_dollar_amounts(scrape_text_norm)
        if scraped_amounts:
            kept = [
                v for v in values
                if isinstance(v, str) and _revenue_band_contains_any(v, scraped_amounts)
            ]
            if kept:
                return {"values": kept[:1], "confidence": triple.get("confidence", 0), "evidence": evidence}
        return {"values": [], "confidence": 0, "evidence": ""}

    # ── Primary path: value-must-be-on-the-page ──────────────────────
    # Per-value check against the FULL scraped text. Synonyms via
    # _value_is_in_evidence already handle USA ↔ United States, UK ↔
    # United Kingdom, BFSI ↔ Banking, etc.
    if scrape_text_norm:
        kept: List[str] = []
        salvaged_any = False
        for v in values:
            if not isinstance(v, str):
                continue
            if _value_is_in_evidence(v, scrape_text_norm):
                kept.append(v)
                continue
            # person_locations only: the model often over-specifies
            # granularity ("Dallas, Texas, United States") when the page
            # names only some segments. Recover the coarsest quoted form
            # instead of dropping the whole (page-grounded) location.
            if field_key == "person_locations":
                salvaged = _salvage_location(v, scrape_text_norm)
                if salvaged:
                    kept.append(salvaged)
                    salvaged_any = True
        # De-dupe while preserving order (salvage can collapse two values
        # onto the same coarser location, e.g. both → "United States").
        seen: set = set()
        kept = [x for x in kept if not (x in seen or seen.add(x))]
        if not kept:
            return {"values": [], "confidence": 0, "evidence": ""}
        if len(kept) == len(values) and not salvaged_any:
            return triple
        new_conf = min(int(triple.get("confidence") or 0), 80)
        return {"values": kept, "confidence": new_conf, "evidence": evidence}

    # ── Back-compat path: no scrape text available ───────────────────
    # Fall back to the evidence-based per-value check the way the
    # original 2026-06-02 implementation did. Drops fields where the
    # model emitted values with no supporting evidence at all.
    if not evidence:
        return {"values": [], "confidence": 0, "evidence": ""}
    kept = [
        v for v in values
        if isinstance(v, str) and _value_is_in_evidence(v, evidence)
    ]
    if not kept:
        return {"values": [], "confidence": 0, "evidence": ""}
    if len(kept) == len(values):
        return triple
    # Some values dropped; lower confidence to reflect partial trust.
    new_conf = min(int(triple.get("confidence") or 0), 80)
    return {"values": kept, "confidence": new_conf, "evidence": evidence}

# Free-context ICP keys — used by the copywriter, NOT sent to Apollo.
_LEGACY_ICP_LIST_KEYS = ("pain_points", "buying_triggers", "negative_signals")

# Body-text caps REMOVED 2026-05-29 per user request — see
# `_format_page_section` in this file. Full page text flows to Gemini
# (~1M token input budget is plenty for any real-world marketing site).


# Legacy string-confidence mapping. Kept here as well as in
# analyze_targeting.py so this module is self-contained for callers that
# don't go through the router (e.g. analyze.py persistence path).
_LEGACY_STRING_CONFIDENCE = {"high": 90, "medium": 60, "low": 20}


def _coerce_confidence_int(raw: Any) -> int:
    """Coerce Gemini's per-field confidence into an integer 0–100.

    Gemini now emits integer 0–100 directly (see _analyze_prompts.py),
    but tolerate:
      - int / float in any range → clamped to [0, 100]
      - numeric string ("85") → parsed and clamped
      - legacy strings "high" / "medium" / "low" → mapped to 90 / 60 / 20
        so rows persisted before the 2026-06-02 switch still gate correctly
      - anything else → 0 (treated as "no evidence")
    """
    if isinstance(raw, bool):
        return 100 if raw else 0
    if isinstance(raw, (int, float)):
        try:
            return max(0, min(100, int(raw)))
        except Exception:  # noqa: BLE001
            return 0
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return 0
        if s in _LEGACY_STRING_CONFIDENCE:
            return _LEGACY_STRING_CONFIDENCE[s]
        try:
            return max(0, min(100, int(float(s))))
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _coerce_icp_filter_field(raw: Any) -> Dict[str, Any]:
    """Normalize one of the 7 canonical ICP filter fields to the
    `{values, confidence, evidence}` triple shape used downstream.

    Tolerant of three input shapes coming back from Gemini (or from
    legacy callers):
      - dict already in the right shape → cleaned + filled with defaults
      - list  → wrapped as {values: list, confidence: 60, evidence: ""}
      - None / anything else → empty triple with confidence=0

    2026-06-02: confidence is now numeric (int 0–100). Legacy
    "high"/"medium"/"low" rows are auto-mapped by `_coerce_confidence_int`.
    """
    if isinstance(raw, dict):
        values = raw.get("values")
        if not isinstance(values, list):
            values = [values] if values else []
        confidence = _coerce_confidence_int(raw.get("confidence"))
        evidence = str(raw.get("evidence") or "").strip()
        return {
            "values": [v for v in values if v not in (None, "")],
            "confidence": confidence,
            "evidence": evidence,
        }
    if isinstance(raw, list):
        return {
            "values": [v for v in raw if v not in (None, "")],
            "confidence": 60,
            "evidence": "",
        }
    return {"values": [], "confidence": 0, "evidence": ""}


def _build_allowed_values_appendix() -> str:
    """Return the "ALLOWED VALUES" block appended to every analyze prompt.

    Lazy-imports the closed lists from `routers.analyze_targeting` so the
    Gemini prompt always sees the SAME labels the downstream validator
    enforces. Without this, the LLM emits near-misses (e.g. "EdTech")
    that get silently dropped at the Apollo boundary. Pulling the lists
    at call time (not module load) avoids a circular import — gemini.py
    is imported BY analyze_targeting at module load, so we cannot import
    back at the top level.

    If the lists are unavailable for any reason (import error during
    startup, etc.) the appendix degrades gracefully to an empty string —
    the prompt still works, just with a higher silent-drop rate.
    """
    try:
        # Local import to defer module resolution until first call.
        from nexus.routers.analyze_targeting import (  # noqa: WPS433
            ALLOWED_INDUSTRIES,
            ALLOWED_LOCATIONS,
            ALLOWED_ROLES,
            REVENUE_OPTIONS,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "analyze prompt: could not import ALLOWED_* vocabulary from "
            "analyze_targeting — emitting prompt without closed-list "
            "appendix (downstream drops will be higher than usual)."
        )
        return ""

    lines: List[str] = [
        "",
        "═══════ ALLOWED VALUES (use these exact labels) ═══════",
        "",
        "person_titles — pick from this list verbatim. Verbatim page",
        "quotes are also OK for unusual titles.",
        f"    {', '.join(ALLOWED_ROLES)}",
        "",
        "person_locations — full country/region names, exact spelling.",
        f"    {', '.join(ALLOWED_LOCATIONS)}",
        "",
        "organization_industries — closed list, exact spelling (mind the",
        "en-dashes and '&' characters).",
        f"    {', '.join(ALLOWED_INDUSTRIES)}",
        "",
        "revenue_range — emit EXACTLY ONE value as a single-element list.",
        f"    {', '.join(REVENUE_OPTIONS)}",
        "",
        "buyer_technologies — FREE-FORM (no closed list). Quote tech",
        "names exactly as the page writes them.",
    ]
    return "\n".join(lines)


# Per-page body caps REMOVED 2026-05-29 per user request — Gemini 2.5
# Flash handles ~1M tokens of input (~4M chars), so the prior 12K/4K
# truncation was clipping signal-rich /services and /industries pages
# right where the content gets specific. Each page's body now flows
# into the prompt in full. If a future scrape returns a pathological
# multi-megabyte page we'll address it then with a soft cap, but real-
# world marketing sites top out at ~30-50 KB of clean text per page.


def _format_page_section(*, label: str, page: Any) -> str:
    """Render one labelled page section for the Gemini user prompt.

    `label` is the human-readable section header (e.g. "HOMEPAGE",
    "/services"). `page` is a ScrapeResult-shaped object or string.

    No body truncation — the full page text flows through to Gemini.
    """
    if isinstance(page, str):
        title = ""
        meta_description = ""
        h1s: List[str] = []
        h2s: List[str] = []
        body = page or ""
    else:
        title = (getattr(page, "title", "") or "").strip()
        meta_description = (getattr(page, "meta_description", "") or "").strip()
        h1s = list(getattr(page, "h1s", []) or [])
        h2s = list(getattr(page, "h2s", []) or [])
        body = getattr(page, "text", "") or ""

    lines: List[str] = ["", f"═══════ {label} ═══════"]
    if title:
        lines.append(f"Title:            {title}")
    if meta_description:
        lines.append(f"Meta description: {meta_description}")
    if h1s:
        lines.append("H1 headings:      " + " | ".join(h1s))
    if h2s:
        lines.append("H2 headings:      " + " | ".join(h2s))
    if body:
        lines.append("")
        lines.append("Body:")
        lines.append(body)
    return "\n".join(lines)


def _build_analyze_user_prompt(
    *,
    url: str,
    scrape_result: Any,
    description: Optional[str],
) -> str:
    """Build the sectioned user prompt fed into Gemini.

    Layout:

        Product URL: <url>
        [User-provided description: <text>]            # optional

        ═══════ HOMEPAGE ═══════
        Title:            …
        Meta description: …
        H1 headings:      …
        H2 headings:      …
        Body: …

        ═══════ /services ═══════       (only if scrape_result is a ScrapeBundle
        Title: …                          with subpages)
        Body: …

        ═══════ /industries ═══════
        …

        ═══════ ALLOWED VALUES ═══════
        <closed-list vocabularies>

    The Gemini system prompt tells the LLM how to read this layout
    (INPUT FORMAT section).

    Args:
      scrape_result: one of:
        - `ScrapeBundle` (preferred — has `.homepage` and `.subpages`)
        - `ScrapeResult` dataclass (homepage only)
        - any duck-typed object exposing `.title` / `.meta_description`
          / `.h1s` / `.h2s` / `.text`
        - a plain string (legacy back-compat — treated as body text)
    """
    parts: List[str] = [f"Product URL: {url}"]
    if description:
        parts.append(f"\nUser-provided description:\n{description.strip()}")

    # ── Detect ScrapeBundle vs single ScrapeResult vs raw string ─────
    # We do duck-typing on `.homepage` because importing ScrapeBundle
    # at module load creates a circular import risk (scraper imports
    # nothing from gemini today, but keeping it loose is safer).
    homepage = getattr(scrape_result, "homepage", None)
    subpages = getattr(scrape_result, "subpages", None)

    if homepage is not None and subpages is not None:
        # It's a ScrapeBundle — render homepage + each subpage.
        parts.append(_format_page_section(label="HOMEPAGE", page=homepage))
        for sp in subpages:
            sp_url = getattr(sp, "url", "") or ""
            # Friendly label: just the path portion of the URL.
            try:
                from urllib.parse import urlparse
                label = urlparse(sp_url).path or sp_url
            except Exception:
                label = sp_url
            parts.append(_format_page_section(label=label, page=sp))
    else:
        # It's a single ScrapeResult (or string) — homepage only.
        parts.append(_format_page_section(
            label="HOMEPAGE", page=scrape_result,
        ))

    # Closed-list appendix — see _build_allowed_values_appendix().
    appendix = _build_allowed_values_appendix()
    if appendix:
        parts.append(appendix)

    return "\n".join(parts)


def analyze_product(
    *,
    url: str,
    scrape_result: Any = None,
    description: Optional[str] = None,
    entity_type: Optional[str] = None,
    # Legacy back-compat kwarg — older callers passed `scraped_text=<str>`
    # before the function signature changed. We accept it, wrap it as a
    # body-only ScrapeResult-shaped input, and log a deprecation note.
    scraped_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the product-extraction prompt. Returns the parsed JSON dict.

    Signature change 2026-05-28: previously took `scraped_text: str` and
    discarded the rest of the scrape metadata. Now takes the full
    `ScrapeResult` dataclass (or a `ScrapeBundle` covering homepage +
    subpages) so the prompt can include title, meta description, and
    headings as structural anchors. The legacy `scraped_text=` kwarg is
    still accepted for back-compat.

    Signature change 2026-05-29: `entity_type` is now an explicit
    parameter. NEXUS supports three entity types (product / service /
    gcc) and each gets its OWN system prompt with tailored ROLE, pillar
    interpretations, and worked example. See nexus/services/
    _analyze_prompts.py for the three prompts and their selector. When
    entity_type is None or unrecognised, falls back to the PRODUCT
    prompt (the historical default).
    """
    # Resolve which input shape the caller used.
    if scrape_result is None and scraped_text is not None:
        scrape_result = scraped_text  # legacy: plain string body
        logger.debug(
            "analyze_product: legacy scraped_text= path — caller should "
            "pass scrape_result= (ScrapeResult / ScrapeBundle) instead"
        )
    if scrape_result is None:
        raise ValueError(
            "analyze_product requires `scrape_result` (or legacy `scraped_text`)"
        )

    user = _build_analyze_user_prompt(
        url=url,
        scrape_result=scrape_result,
        description=description,
    )

    # Pick the system prompt matching the user-selected entity_type.
    # Logged so we can correlate "wrong type detected" bug reports back
    # to the prompt that actually ran.
    system_prompt = select_analyze_prompt(entity_type)
    logger.info(
        "analyze_product: entity_type=%s prompt=%s user_prompt_chars=%d",
        entity_type or "(default→product)",
        "PRODUCT" if entity_type in (None, "", "product") else entity_type.upper(),
        len(user),
    )

    # 2026-06-02 — Verbose dump of the EXACT text being sent to Gemini.
    # Gated by env var NEXUS_LOG_FULL_PROMPT=1 so we don't blow up
    # CloudWatch in production. Set the env var on your dev box when
    # debugging "why did the model hallucinate X?" — the answer is
    # almost always "the page text we sent didn't contain X".
    if os.environ.get("NEXUS_LOG_FULL_PROMPT", "").strip().lower() in (
        "1", "true", "yes",
    ):
        logger.info(
            "=" * 60
            + "\n"
            + "FULL USER PROMPT FED TO GEMINI (analyze_product)\n"
            + "url=%s entity_type=%s chars=%d\n"
            + "=" * 60
            + "\n%s\n"
            + "=" * 60
            + " END OF PROMPT " + "=" * 60,
            url, entity_type or "(default→product)", len(user), user,
        )

    # 2026-05-29 — Timing for product-description generation. Emitted
    # alongside the existing "Gemini chat ok" log so the operator can
    # eyeball how long Pro vs Flash took for THIS ICP extraction.
    def _fmt_mmss(sec: float) -> str:
        sec = max(0.0, float(sec))
        m = int(sec // 60)
        return f"{m:02d}:{sec - m * 60:05.2f}"

    _analyze_started = time.monotonic()

    # Token budget — the response schema is large (15+ keys, several arrays
    # of {values, confidence, evidence} triples, rationale, value_proposition)
    # and the verbose `evidence` quotes inflate it further on content-rich
    # pages. CRITICAL: ANALYZE_MODEL is gemini-3.1-flash-lite, which REQUIRES
    # thinking mode (we cannot set thinking_budget=0 for Pro — see
    # chat_completion), and Pro's hidden thinking tokens are billed against
    # max_output_tokens. So the old 6000 cap was being split between thinking
    # + JSON, and a rich page (e.g. z-ninth with many case studies) would
    # TRUNCATE the JSON mid-output → extract_json "No valid JSON".
    #
    # Set to 60000 (env-tunable) — essentially Pro's full output ceiling
    # (gemini-3.1-flash-lite caps at 65536). max_output_tokens is only a CEILING,
    # NOT a target: the model still generates only what it needs, so a high
    # value costs nothing extra but GUARANTEES neither the thinking nor the
    # JSON is ever truncated — no data lost on any page, however rich.
    raw = chat_completion(
        system=system_prompt,
        user=user,
        temperature=0.2,
        max_tokens=int(os.getenv("NEXUS_ANALYZE_MAX_OUTPUT_TOKENS", "60000")),
        response_format_json=True,
        model=ANALYZE_MODEL,
    )
    logger.info(
        "[TIMING] Product description generation took %s — using %s for %s entity",
        _fmt_mmss(time.monotonic() - _analyze_started),
        ANALYZE_MODEL,
        entity_type or "product",
    )

    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"analyze_product expected JSON object, got {type(parsed).__name__}"
        )

    # 2026-06-02 — Build a normalized version of the SCRAPED PAGE TEXT so
    # the strict-quote check below can verify the model's `evidence`
    # quotes are actually in the page (not fabricated from training
    # data). Falls back to "" when we can't extract the text — in that
    # case the check #1 inside _drop_unevidenced_values silently skips
    # (preserves existing behavior for callers that pass a bare string).
    scrape_text_norm = ""
    try:
        if hasattr(scrape_result, "combined_text"):
            scrape_text_norm = _normalise_for_match(scrape_result.combined_text)
        elif hasattr(scrape_result, "text"):
            scrape_text_norm = _normalise_for_match(scrape_result.text or "")
        elif isinstance(scrape_result, str):
            scrape_text_norm = _normalise_for_match(scrape_result)
    except Exception:  # noqa: BLE001
        scrape_text_norm = ""

    # Top-level offering fields — unchanged from the prior schema.
    parsed.setdefault("name", None)
    parsed.setdefault("category", None)
    parsed.setdefault("value_proposition", None)
    parsed.setdefault("key_benefits", [])
    parsed.setdefault("pricing_tier", None)
    parsed.setdefault("industry_relevance", None)

    # NEW (2026-05-28) — GCC support + 3-pillar product description.
    # parent_company is REQUIRED for entity_type="gcc" and empty
    # otherwise (e.g. "Walmart Inc." for Walmart Global Tech India).
    parent_company_raw = parsed.get("parent_company")
    parsed["parent_company"] = (
        str(parent_company_raw).strip() if parent_company_raw else ""
    )

    # product_description — 3-pillar block consumed by the wizard's
    # summary step. Defaults to empty strings + empty arrays so legacy
    # callers don't KeyError. Frontend should tolerate empty pillars.
    pd_raw = parsed.get("product_description")
    pd_out: Dict[str, Any] = {}
    if not isinstance(pd_raw, dict):
        pd_raw = {}
    for str_key in ("what_the_company_is", "what_they_do",
                    "who_they_serve", "customer_profile"):
        v = pd_raw.get(str_key)
        pd_out[str_key] = str(v).strip() if v else ""
    for list_key in ("key_capabilities", "target_industries",
                     "target_geographies"):
        v = pd_raw.get(list_key)
        pd_out[list_key] = (
            [str(x).strip() for x in v if isinstance(x, str) and x.strip()]
            if isinstance(v, list) else []
        )
    parsed["product_description"] = pd_out

    icp_raw = parsed.get("icp") or {}
    icp_out: Dict[str, Any] = {}

    # entity_type + entity_type_confidence + entity_type_rationale (used
    # for product-vs-service-vs-gcc routing in downstream sequencer /
    # outreach template / suggest-targeting). These are NOT filter fields.
    #
    # 2026-05-28 rename — the top-level confidence/rationale fields were
    # renamed to `entity_type_confidence` / `entity_type_rationale` in
    # the new prompt so they no longer name-collide with the per-filter
    # `confidence` inside each filter triple. We still accept the legacy
    # short names (`confidence`, `rationale`) for one cycle so cached
    # Gemini responses or downstream snapshots keep working.
    #
    # 2026-05-28 GCC support — "gcc" (Global Capability Center) joins
    # product/service as a valid entity_type. Captive offshore units
    # like Walmart Global Tech India / Goldman Bangalore. Safe default
    # stays "product" since most outbound runs are SaaS.
    entity_type = (icp_raw.get("entity_type") or "").strip().lower()
    if entity_type not in ("product", "service", "gcc"):
        entity_type = "product"  # safe default
    icp_out["entity_type"] = entity_type
    # Mirror entity_type at top-level too — analyze.py / scrape_preview.py
    # historically expect it on the parsed dict, not just on .icp.
    parsed.setdefault("entity_type", entity_type)

    # entity_type_confidence — same numeric (0-100) axis as the per-field
    # ICP confidences. Legacy "high"/"medium"/"low" rows are mapped by
    # `_coerce_confidence_int`. Default 60 (≈ medium) when the model is
    # silent — matches the prior "medium" default exactly.
    entity_conf_raw = (
        icp_raw.get("entity_type_confidence")
        or icp_raw.get("confidence")  # legacy
    )
    entity_conf = _coerce_confidence_int(entity_conf_raw) if entity_conf_raw is not None else 60
    icp_out["entity_type_confidence"] = entity_conf
    # Keep the legacy key populated too — readers across the codebase
    # (outreach_template, product card display) may still read it.
    icp_out["confidence"] = icp_out["entity_type_confidence"]

    rationale_raw = (
        icp_raw.get("entity_type_rationale")
        or icp_raw.get("rationale")  # legacy
        or ""
    )
    icp_out["entity_type_rationale"] = (
        str(rationale_raw).strip() if rationale_raw else ""
    )
    icp_out["rationale"] = icp_out["entity_type_rationale"]  # legacy alias

    # 6 canonical filter fields — each as {values, confidence, evidence}.
    # Coerce defensively so a malformed Gemini response (list instead of
    # dict, missing keys, etc.) never raises downstream.
    #
    # 2026-06-08 — EVERY ICP FILTER MUST COME FROM THE SCRAPED PAGES ONLY,
    # EXCEPT person_titles (roles). Per product direction:
    #   - person_locations, organization_industries, buyer_technologies,
    #     revenue_range  → STRICT. _drop_unevidenced_values keeps a value
    #     only if its text (or a known synonym, or — for revenue — a dollar
    #     amount falling in the band) is literally on the scraped page. Not
    #     found → the field is left BLANK (the user can add chips manually).
    #     This stops the model from inferring values that aren't page data
    #     (e.g. guessing "$1B+" revenue from named customers, or a target
    #     geography that's only the company's own office).
    #   - person_titles → EXEMPT (not in _STRICT_QUOTE_FIELDS). Pages almost
    #     never name the BUYER's job titles, so we trust the model's
    #     inference here. This is the single allowed-inference field.
    # Grounding is still informed by the model reading the FULL scrape +
    # self-reported confidence; the strict guard is the safety net that
    # guarantees "scraped data only" for the non-role fields. Per-field log
    # line emitted when the guard blanks a field, so the operator sees why.
    for key in CANONICAL_ICP_FILTER_KEYS:
        triple_before = _coerce_icp_filter_field(icp_raw.get(key))
        triple_after = _drop_unevidenced_values(key, triple_before, scrape_text_norm)
        if (triple_before.get("values") or []) and not (triple_after.get("values") or []):
            logger.info(
                "analyze_product: strict-quote guard BLANKED field=%s "
                "values=%s evidence=%r — not found in scraped pages.",
                key,
                triple_before.get("values"),
                (triple_before.get("evidence") or "")[:120],
            )
        icp_out[key] = triple_after

    # Free-context lists — passed to the copywriter prompts; NOT sent to
    # Apollo. Kept as plain arrays (no confidence triples) so existing
    # downstream readers (outreach_template, rag_reply) continue to work.
    for key in _LEGACY_ICP_LIST_KEYS:
        val = icp_raw.get(key)
        if isinstance(val, dict):  # tolerate triple shape too
            val = val.get("values") or []
        icp_out[key] = list(val) if isinstance(val, list) else []

    parsed["icp"] = icp_out
    return parsed


# ---------------------------------------------------------------------------
# (REMOVED 2026-06-02) Google-Search-grounded ICP "deep research"
# ---------------------------------------------------------------------------
# The grounded research path (research_targeting_grounded + _RESEARCH_SYSTEM)
# was removed per user requirement: the ICP must be derived from the scraped
# pages ONLY — never from Google Search, Crunchbase, LinkedIn, news or any
# other source the model may pull in via grounding. The only ICP path is now
# analyze_product() above, which reads the scraped homepage + subpages and
# emits the 7 canonical fields strictly from that text.
#
# To restore later: re-add the function below this comment and re-import
# `types.Tool(google_search=...)` in the call config.

# DEAD CODE — preserved only so the next reader sees what the grounded path
# used to do. The `research_targeting_grounded` function below is no longer
# called from anywhere (see analyze_targeting.py). Keep until 2026-09 so a
# rollback is one-edit away, then delete entirely.
_RESEARCH_SYSTEM_DEPRECATED = (
    "You are a senior B2B go-to-market strategist. Use Google Search to "
    "research the company on the live web — its own site, Crunchbase, "
    "LinkedIn, G2/Capterra reviews, news, and especially its NAMED customers "
    "/ case studies.\n\n"
    "METHOD — follow in order, do not skip:\n"
    "1. Pin down PRECISELY what the product does, the specific PAIN it "
    "removes, and the measurable VALUE it delivers.\n"
    "2. Derive the ICP by asking: which companies feel THAT pain most acutely "
    "AND have budget to pay to solve it? The best-fit "
    "organization_industries are the verticals where this product's value is "
    "SHARPEST — NOT broad industries that merely 'could' use it.\n"
    "3. Cross-check against the company's ACTUAL named customers / case "
    "studies / reviews when you find them; let real customers override "
    "theory.\n\n"
    "ANTI-PATTERNS (avoid):\n"
    "- Do NOT default to large catch-all industries (Healthcare, Retail, "
    "Financial Services) just because they are big or 'advertise too'. Only "
    "name an industry if the product's value is specifically strong there.\n"
    "- If the product serves a HORIZONTAL function (e.g. ad-spend management, "
    "marketing analytics, devtools), pick the verticals that are the HEAVIEST "
    "users/spenders of that function (for ad-spend: e-commerce/D2C brands, "
    "consumer mobile apps, gaming, online marketplaces, travel, media, "
    "marketing agencies) — not generic giants.\n"
    "- person_titles must be the people who OWN this budget/pain (be "
    "specific, e.g. 'Head of Performance Marketing', 'VP Growth'), not a "
    "generic C-suite list.\n\n"
    "Confidence rubric (a wrong filter is worse than an empty one):\n"
    "  high   — corroborated by the site AND independent sources, especially "
    "NAMED customers.\n"
    "  medium — inferred from strong signals / a clear primary market.\n"
    "  low    — weak guess with little supporting evidence.\n\n"
    "Put the concrete finding + where you found it in each field's `evidence`. "
    "Return ONLY a valid JSON object — no prose, no markdown, no code fences."
)


def research_targeting_grounded(
    *,
    product_name: str,
    product_summary: str,
    url: Optional[str] = None,
    entity_type: str = "product",
    scraped_content: Optional[str] = None,
) -> Dict[str, Any]:
    """Grounded (Google-Search) ICP research. Returns the 7 canonical filter
    fields as {values, confidence, evidence} triples, or {} on failure.

    `scraped_content` is the actual multi-page text we scraped from the site
    (homepage + subpages). When provided it is given to Gemini verbatim (in
    addition to live grounding) so the model reasons over the real page text —
    including customer/case-study pages — not just a short summary.
    """
    from google.genai import types  # type: ignore

    et = (entity_type or "product").strip().lower()
    if et == "service":
        framing = (
            "This company is a SERVICE business. Research its IDEAL CLIENT "
            "companies — their geographies, industries, typical revenue band, "
            "and the decision-maker titles/seniorities/departments who sign a "
            "services engagement."
        )
    elif et == "gcc":
        framing = (
            "This company is a GCC PROVIDER (helps multinationals set up global "
            "capability centers / overseas operations). Research the "
            "MULTINATIONAL BUYERS it targets — where they are HQ'd, their "
            "industries, revenue band ($50M-$1B+), and the HR/Operations/Finance "
            "executives who co-sign such deals."
        )
    else:
        framing = (
            "This company sells a PRODUCT. Research the companies that BUY/USE "
            "it — their geographies, industries, typical revenue band, and the "
            "decision-maker titles/seniorities/departments who approve the "
            "purchase, plus technologies those buyers already use."
        )

    # The actual scraped site text (homepage + subpages) when we have it —
    # this is the "provide the scraped content to Gemini" path. We feed the
    # FULL scraped text so the model sees everything (footers with HQ
    # locations, contact pages, case studies) and decides confidently. Gemini
    # 2.5 handles ~1M tokens (~4M chars); the prior 16K cap was clipping the
    # tail of multi-page sites (e.g. a /contact or /locations subpage), which
    # starved the ICP research. Keep only a generous sanity bound (200K chars
    # ≈ 50K tokens) so a pathological site still can't blow the budget.
    scraped_block = ""
    if scraped_content and scraped_content.strip():
        scraped_block = (
            "ACTUAL SCRAPED WEBSITE CONTENT (homepage + key subpages — read "
            "this carefully, especially any customer / case-study sections and "
            "the footer / contact details which often carry HQ locations):\n"
            f"{scraped_content.strip()[:GEMINI_MAX_INPUT_CHARS]}\n\n"
        )

    user = (
        f"{framing}\n\n"
        f"Company: {product_name or 'Unknown'}\n"
        f"Website: {url or '(not provided — search by company name)'}\n\n"
        f"Short summary:\n{(product_summary or '')[:50000]}\n\n"
        f"{scraped_block}"
        "Work through this SILENTLY (do NOT write your reasoning as text): "
        "(a) the core PAIN this product removes and for whom; (b) Google-"
        "search to verify the company and find its REAL named customers / "
        "case studies and who buys it; (c) derive the ICP from that.\n\n"
        "Then output ONLY a JSON object with EXACTLY these 7 keys, each a "
        "{values, confidence, evidence} object:\n"
        "  person_titles, person_locations, person_seniorities, "
        "person_departments, organization_industries, revenue_range, "
        "buyer_technologies\n"
        "revenue_range.values must be a single-element list.\n\n"
        "person_titles — list the buyers across ALL relevant seniority "
        "levels, and ALWAYS include the C-LEVEL owner of the function this "
        "product serves: CMO for a marketing/social/ads product, CRO/VP Sales "
        "for sales, CFO for finance, CTO/CIO for engineering/IT, CHRO for HR. "
        "Then add the VP / Head / Director / Manager levels of that same "
        "function. Don't omit the obvious C-suite buyer.\n"
        "person_locations — DO determine the target market geography; don't "
        "leave it empty when it's reasonably knowable. Strong signals: where "
        "the company's named customers/case studies are; the markets it says "
        "it serves; failing those, the company's OWN headquarters / primary "
        "operating country (most B2B companies sell into their home market "
        "first). Mark 'high' when customers/served-markets are named, 'medium' "
        "when you're falling back to the company's HQ/primary country, 'low' "
        "only when there is genuinely no geographic signal at all.\n"
        f"{_build_allowed_values_appendix()}\n\n"
        'Example field: {"values": ["FinTech","Banking"], "confidence": '
        '"high", "evidence": "Crunchbase + 3 case studies name fintech '
        'clients"}\n\n'
        "IMPORTANT: respond with the JSON object and NOTHING else — no "
        "preamble, no explanation, no markdown fences, before or after."
    )

    client = _get_client()
    config_kwargs: Dict[str, Any] = {
        "system_instruction": _RESEARCH_SYSTEM,
        "temperature": 0.2,
        # Generous output budget so the full 7-key JSON never gets clipped — a
        # truncated object drops whichever field sits at the end (e.g.
        # person_locations), which showed up as intermittently-missing
        # locations. Gemini 2.5 Flash allows up to 65K output tokens; the JSON
        # itself is small (~2-3K), so this is pure safety headroom (it only
        # consumes what it needs). Env-tunable.
        "max_output_tokens": int(os.getenv("NEXUS_TARGETING_MAX_OUTPUT_TOKENS", "16000")),
        "tools": [types.Tool(google_search=types.GoogleSearch())],
    }
    # Flash supports disabling hidden thinking; keeps the JSON from being
    # truncated. Wrapped because older SDKs lack ThinkingConfig.
    try:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:  # noqa: BLE001
        pass

    started = time.time()
    try:
        resp = client.models.generate_content(
            model=CHAT_MODEL,
            contents=user,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("grounded ICP research failed (%s): %s", CHAT_MODEL, exc)
        return {}

    raw = (resp.text or "").strip()
    logger.info(
        "grounded ICP research ok model=%s elapsed_ms=%d chars=%d",
        CHAT_MODEL, int((time.time() - started) * 1000), len(raw),
    )
    try:
        parsed = extract_json(raw)
    except Exception:  # noqa: BLE001
        logger.warning("grounded ICP research: could not parse JSON output")
        return {}
    if not isinstance(parsed, dict):
        return {}

    out: Dict[str, Any] = {}
    for key in CANONICAL_ICP_FILTER_KEYS:
        out[key] = _coerce_icp_filter_field(parsed.get(key))
    return out


# ---------------------------------------------------------------------------
# Refine-summary prompt
# ---------------------------------------------------------------------------

_REFINE_SYSTEM = """You are a product positioning expert helping refine a product summary for outbound sales campaigns.

The user has an existing product summary and wants to change something specific. Apply their instruction precisely and return the refined summary.

Rules:
- Preserve the existing structure and formatting (line breaks, bullet points, sections).
- Only change what the user explicitly asks — do not add or remove unrelated content.
- Return a JSON object with a single key: {"refined_summary": "..."}
- The refined_summary value must be the full revised summary text, preserving newlines with \\n.
- Never add preamble, explanation, or wrapper text outside the JSON.
"""


def refine_summary(*, current_summary: str, instruction: str) -> str:
    """Apply a user instruction to an existing product summary. Returns new summary."""
    user = (
        f"Current summary:\n{current_summary.strip()}\n\n"
        f"Instruction: {instruction.strip()}\n\n"
        'Respond with JSON: {"refined_summary": "..."}'
    )

    raw = chat_completion(
        system=_REFINE_SYSTEM,
        user=user,
        temperature=0.4,
        max_tokens=1024,
        response_format_json=True,
    )
    parsed = extract_json(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("refined_summary"), str):
        return parsed["refined_summary"].strip()
    return raw.strip()


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def embed_text(text: str, *, input_type: str = "passage") -> List[float]:
    """Embed a single string. `input_type` is 'passage' when indexing,
    'query' when searching. Tries multiple Gemini model identifiers since
    `text-embedding-004` 404s on some API versions; final fallback is the
    zero vector so the caller's contract (always returns List[float]) holds.
    """
    from google.genai import types  # type: ignore

    client = _get_client()
    snippet = (text or "")[:2048]
    if not snippet.strip():
        return [0.0] * EMBED_DIMS

    task_type = "RETRIEVAL_DOCUMENT" if input_type == "passage" else "RETRIEVAL_QUERY"

    # Try the configured model first, then sensible fallbacks. Order matters
    # for accuracy + dim consistency — gemini-embedding-001 is the new default
    # (3072 dim) and matches what the Pinecone `nexus-kb` index was created
    # with. The text-embedding-004 fallbacks are only useful on older API
    # versions that 404'd the new model — harmless to keep listed.
    candidates: List[str] = []
    if EMBED_MODEL:
        candidates.append(EMBED_MODEL)
    for m in (
        "gemini-embedding-001",
        "models/gemini-embedding-001",
        "text-embedding-004",
        "models/text-embedding-004",
    ):
        if m not in candidates:
            candidates.append(m)

    for model_name in candidates:
        cfg_kwargs: dict = {"task_type": task_type}
        # Request the exact target dimension so passage + query vectors land
        # in the same space AND match the Pinecone index dim. gemini-embedding-001
        # supports 768 / 1536 / 3072; text-embedding-004 is fixed at 768 and
        # will ignore output_dimensionality (acceptable — fallback only).
        cfg_kwargs["output_dimensionality"] = EMBED_DIMS
        try:
            resp = client.models.embed_content(
                model=model_name,
                contents=snippet,
                config=types.EmbedContentConfig(**cfg_kwargs),
            )
            return list(resp.embeddings[0].values)
        except Exception as exc:
            logger.info("embed_text model %s failed: %s", model_name, exc)
            continue

    logger.warning("Gemini embed_text: all candidates failed — returning zero vector")
    return [0.0] * EMBED_DIMS


# Number of concurrent threads to embed chunks with. Gemini Tier 1 free
# allows ~1000 RPM (~16 req/sec). A single embed_text call is ~500ms, so
# 5 threads give us roughly 10 req/sec — safely under the limit while
# being ~5x faster than sequential. Larger pools risk hitting rate limits
# and the threads spend time waiting on 429-backoff anyway.
_EMBED_BATCH_WORKERS = 5


def embed_batch(texts: List[str], *, input_type: str = "passage") -> List[List[float]]:
    """Embed many texts in parallel using a thread pool.

    Each chunk's Gemini call is independent network I/O — perfect for
    threading (the GIL releases during HTTP waits). Order is preserved:
    output[i] corresponds to texts[i] regardless of which thread finishes
    first.

    Failures on individual chunks fall back to a zero-vector (matches the
    contract of `embed_text`) — the caller in `pinecone_kb` filters those
    out so noise vectors don't pollute the index.
    """
    if not texts:
        return []

    # For 1-2 texts the thread setup cost outweighs the gain — stay sync.
    if len(texts) <= 2:
        return [embed_text(t, input_type=input_type) for t in texts]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]
    worker_count = min(_EMBED_BATCH_WORKERS, len(texts))

    logger.info(
        "Embedding %d chunks with %d parallel workers (model=%s, dim=%d)",
        len(texts), worker_count, EMBED_MODEL, EMBED_DIMS,
    )

    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        future_to_idx = {
            ex.submit(embed_text, t, input_type=input_type): i
            for i, t in enumerate(texts)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                out[idx] = fut.result()
            except Exception as exc:
                logger.warning(
                    "Embedding for chunk %d failed; using zero vector. Reason: %s",
                    idx, exc,
                )
                out[idx] = [0.0] * EMBED_DIMS

    return out
