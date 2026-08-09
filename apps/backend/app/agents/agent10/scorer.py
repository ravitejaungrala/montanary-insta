"""Agent #10 — buying-signal scorer (the live engine).

ONE Google-Search-grounded Gemini call per company: it researches a fixed set of
buying signals + a product-specific signal (derived from the product being sold)
and returns an overall fit BAND, mapped to a score and an accept/not-picked
verdict.

Self-contained: NO NEXUS/DB imports — only the Gemini SDK + stdlib. Callers
(``nexus.services.intent_sweep`` via the ``nexus.services.intent_agent`` bridge)
dedupe by company domain and fan the calls out concurrently, so a whole campaign
scores in roughly one company's wall-clock.

Scoring is on SIGNALS ONLY — firmographic/ICP fit is handled upstream (leads
arrive already ICP-filtered), so this judge looks purely at buying intent.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pipelyt.agent10")

# Gemini returns a direct 0-100 buying-intent score per lead (based on how many
# signals matched and how strong/recent they are). A lead QUALIFIES (enrolls +
# flows to the campaign) when its score >= AGENT10_ACCEPT_THRESHOLD; below it is
# dropped (not shown). Tunable via env (no code edit / restart only).
_ACCEPT_THRESHOLD = int(os.getenv("AGENT10_ACCEPT_THRESHOLD", "75") or "75")

# Per-call ceiling for the grounded Google-Search Gemini call. Real successful
# calls land in ~30-70s, so 90s clears the normal band while BOUNDING a hung
# call (a 504 DEADLINE_EXCEEDED hangs to whatever cap it's given — the old
# 120s just made every hung company cost 30s more). Pairs with
# cancel-on-target (intent_sweep): a run that already met its target cancels
# stragglers, and one that NEEDS every company waits at most 90s. Env-tunable;
# don't go much below ~75s or real slow-tail calls start timing out and the
# run loses leads it could have had. Companies score
# AGENT10_SCORE_CONCURRENCY-at-a-time, so the batch wall-clock ≈ the slowest
# single call.
_CALL_TIMEOUT_MS = int(os.getenv("AGENT10_CALL_TIMEOUT_MS", "90000") or "90000")
# HARD wall-clock guillotine (2026-06-11). The 90s above is an HTTP READ
# timeout — it measures gaps between bytes, not total time, so a call whose
# socket Google keeps warm can ride past it until Google's own server
# deadline 504s at ~100-104s (observed). This cap bounds the TOTAL call:
# the SDK call runs in a disposable thread and is abandoned at the limit
# (the thread dies when Google closes the socket moments later — by then
# Google's ~105s deadline has fired anyway). A whole scoring wave can
# therefore never be held hostage longer than this. Deliberately HARDCODED
# (not env-tunable): it is the physics ceiling of Google's grounded
# endpoint, not a tuning knob.
_HARD_TIMEOUT_SEC = 105.0
# Same-call retries on a transient failure (504 / timeout / unparseable). 1 = NO
# retry: a 504 DEADLINE_EXCEEDED almost always 504s again, so the old retry just
# DOUBLED the dead time (one hung company = 180s x 2 = 6 min, which stalled an
# entire run). Return 'error' immediately instead and let the cross-tick
# MAX_SCORE_ATTEMPTS fail-open path re-score it in the BACKGROUND — never
# blocking the foreground run on a retry that will almost certainly time out too.
_CALL_ATTEMPTS = int(os.getenv("AGENT10_CALL_ATTEMPTS", "1") or "1")


def _grounded_model() -> str:
    """The Gemini model for the grounded scoring call. Hardcoded (model name no
    longer read from env): gemini-3.1-flash-lite supports Google Search
    grounding at a fraction of the token price of the larger models."""
    return "gemini-3.1-flash-lite"


def _values(raw: Any) -> List[str]:
    """Pull a string list from a plain list OR the {values,...} triple OR a str."""
    if isinstance(raw, dict):
        raw = raw.get("values")
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if isinstance(v, str) and v.strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _product_signal_line(product_context: Dict[str, Any]) -> str:
    """Fallback product/service-specific signal line (entity-type heuristic).

    Used only when a per-product `qualifying_signal` couldn't be derived. The
    first five signals are universal; this is the 6th, tailored to WHAT is being
    sold so the agent looks for the buying signal that matters for THIS product.
    """
    name = (product_context.get("name") or "our product").strip()
    desc = (product_context.get("description") or "").strip()
    etype = (product_context.get("entity_type") or "product").strip().lower()
    sells = f'"{name}"' + (f" — {desc}" if desc else "")
    if etype == "gcc":
        return (
            "6. EXPANSION / GCC FIT — research the company's expansion plans, "
            "especially global capability centers, offshore/India expansion, or "
            "new engineering/operations hubs."
        )
    if etype == "service":
        return (
            f"6. SERVICE FIT — we provide {sells}. Look for evidence the company "
            "is adopting, announcing, or investing in this kind of work (e.g. for "
            "an AI-solutions service, are they launching AI initiatives or hiring "
            "for AI?)."
        )
    return (
        f"6. PRODUCT FIT — we sell {sells}. Look for evidence the company has the "
        "need or spend this addresses (e.g. for an ad/marketing-management "
        "product, are they actively running paid ads or investing in "
        "marketing/ads platforms?)."
    )


def derive_qualifying_signal(product_context: Dict[str, Any]) -> str:
    """ONE Gemini call (per product/run): given what THIS product does, decide the
    single most important product-specific buying signal to look for in a target
    company. Derived from the product's scraped/distilled content so the signal
    changes with the product (Spenzo -> 'spending on ads'; Pipelyt -> 'investing
    in sales/GTM tooling'). Cached by the caller. Returns '' on failure (the
    prompt then falls back to the entity-type heuristic)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""
    name = (product_context.get("name") or "our product").strip()
    desc = (product_context.get("description") or "").strip()
    category = (product_context.get("category") or "").strip()
    benefits = (product_context.get("key_benefits") or "").strip()
    prompt = (
        "We sell the product/service below. In ONE sentence, state the single most "
        "important BUYING SIGNAL to look for when researching a target company on the "
        "web — i.e. concrete, web-researchable evidence that the company has the need "
        "or spend our offering addresses, making it a qualified lead. Example: for an "
        "ad-spend management tool -> 'the company is actively running paid ad campaigns "
        "or investing in marketing/ads platforms'. Return ONLY the sentence.\n\n"
        f"NAME: {name}\nWHAT IT DOES: {desc}\nCATEGORY: {category}\nKEY BENEFITS: {benefits}"
    )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))
        resp = client.models.generate_content(
            model=_grounded_model(),
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        return (getattr(resp, "text", "") or "").strip().strip('"')
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent10: derive_qualifying_signal failed: %s", exc)
        return ""


def _build_scoring_prompt(
    *, company_name: str, domain: Optional[str],
    campaign_icp: Dict[str, Any], product_context: Dict[str, Any],
    qualifying_signal: str = "",
) -> str:
    target_titles = _values((campaign_icp or {}).get("person_titles"))
    titles_str = ", ".join(target_titles) if target_titles else "any C-level / VP role"
    # Point 6 is product-specific: prefer the per-product derived signal; fall
    # back to the entity-type heuristic only if derivation was unavailable.
    product_line = (
        f"6. PRODUCT FIT — {qualifying_signal.strip()}"
        if qualifying_signal and qualifying_signal.strip()
        else _product_signal_line(product_context)
    )
    return f"""You are a B2B buying-intent analyst. Use web search to research the company below, then judge how strong its RECENT BUYING SIGNALS are. Score on signals only — not on firmographic/ICP fit.

COMPANY: {company_name} ({domain or "domain unknown"})

Research the web for RECENT (last ~12 months) evidence of these signals:
1. REVENUE GROWTH — record revenue, strong growth, financial milestones (company website / news).
2. INVESTMENTS / FUNDING — recent funding rounds, capital raised, or investments the company made.
3. HIRING — active hiring, headcount growth, many open roles (LinkedIn / job boards / careers page).
4. COMPANY EXPANSION — new offices, new markets, product launches, M&A, partnerships.
5. NEW LEADERSHIP — did they RECENTLY appoint/add anyone in these target roles: {titles_str}? (new CxO/VP hire).
{product_line}

Then give an OVERALL buying-intent SCORE from 0 to 100, based PURELY on the signals above — how MANY of the 6 signals you found real evidence for and how strong, recent, and concrete they are (more strong / recent / dated signals = higher). Do NOT factor in firmographic or ICP "fit" (industry/revenue/size/geo); score ONLY the signals.
Calibration guide:
- 0: no real signals found.
- ~25: a single weak, vague, or old/undated signal.
- ~50: a couple of moderate signals.
- ~75: several clear, recent signals.
- 90-100: multiple strong, recent, concrete (dated) signals.

Return ONLY a JSON object (no prose, no markdown fences):
{{"score": <integer 0-100>,
  "reason": "<1-2 sentences explaining the score>",
  "signals": [{{"type": "revenue_growth|investments|hiring|expansion|new_leadership|product_fit",
    "summary": "<=160 chars", "date": "YYYY-MM-DD or null", "url": "<source url or null>"}}]}}

Rules: only include a signal you found REAL evidence for; never fabricate facts, dates, or URLs; if you found nothing, return an empty signals array and score 0."""


def _web_search_queries(resp: Any) -> List[str]:
    """Pull the list of Google Search queries the grounded call actually ran.

    Each query here is a BILLED Google Search (the "content search query" cost).
    Read from the response's grounding metadata; returns [] if absent."""
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return []
        gm = getattr(cands[0], "grounding_metadata", None)
        if gm is None:
            return []
        q = getattr(gm, "web_search_queries", None) or []
        return [str(x) for x in q if x]
    except Exception:  # noqa: BLE001
        return []


def _parse_verdict_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def score_company_intent(
    *,
    company_name: str,
    domain: Optional[str],
    person_title: Optional[str],
    campaign_icp: Dict[str, Any],
    product_context: Optional[Dict[str, Any]] = None,
    qualifying_signal: str = "",
) -> Dict[str, Any]:
    """Score ONE company with a single Google-Search-grounded Gemini call.

    Returns a verdict dict:
      { accepted: bool, intent_score: int, in_market_score: int,
        icp_fit_score, icp_fit_band, gate_decision, reason, signals: [...] }
    Never raises — on failure returns gate_decision='error' so the sweep's
    fail-open path can still let the lead through after MAX_SCORE_ATTEMPTS.
    """
    product_context = product_context or {}
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"accepted": False, "intent_score": 0, "in_market_score": 0,
                "icp_fit_score": None, "icp_fit_band": None, "gate_decision": "error",
                "reason": "", "signals": [], "error": "no GEMINI_API_KEY"}

    prompt = _build_scoring_prompt(
        company_name=company_name or (domain or ""), domain=domain,
        campaign_icp=campaign_icp or {}, product_context=product_context,
        qualifying_signal=qualifying_signal,
    )
    model = _grounded_model()
    label = company_name or domain or "?"
    logger.info("Agent#10 ▶ scoring %s (%s) via %s + Google Search…", label, domain or "?", model)
    # Grounded call with a generous, env-tunable timeout + immediate retry on a
    # transient failure (504 DEADLINE_EXCEEDED / timeout / unparseable). Each
    # company runs in its own worker thread (the sweep fans out 20 at a time),
    # so this retry only extends THIS company's wall-clock, never blocks others.
    parsed = None
    web_searches: List[str] = []  # the Google Search queries this call ran (billed)
    last_err = "unparseable verdict"
    for attempt in range(1, _CALL_ATTEMPTS + 1):
        _t = time.monotonic()
        try:
            from google import genai
            from google.genai import types

            def _grounded_call():
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=_CALL_TIMEOUT_MS),
                )
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.0,
                    ),
                )

            # Disposable single-use thread + result(timeout=...) = a TRUE
            # wall-clock cap. NO `with` block — its __exit__ would JOIN the
            # hung thread and block anyway. On timeout the thread is
            # abandoned (shutdown(wait=False)); it exits on its own when the
            # socket dies (Google's ~105s server deadline guarantees that's
            # moments away).
            import concurrent.futures as _cf
            _pool = _cf.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="agent10-hardcap"
            )
            _fut = _pool.submit(_grounded_call)
            try:
                resp = _fut.result(timeout=_HARD_TIMEOUT_SEC)
            except _cf.TimeoutError:
                raise TimeoutError(
                    f"hard wall-clock cap hit ({_HARD_TIMEOUT_SEC:.0f}s)"
                )
            finally:
                _pool.shutdown(wait=False)
            web_searches = _web_search_queries(resp)
            parsed = _parse_verdict_json(getattr(resp, "text", "") or "")
            if parsed:
                break
            last_err = "unparseable verdict"
            logger.warning("Agent#10 ⟳ %s — attempt %d/%d unparseable after %.1fs",
                           label, attempt, _CALL_ATTEMPTS, time.monotonic() - _t)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:200]
            logger.warning("Agent#10 ⟳ %s — attempt %d/%d FAILED after %.1fs: %s",
                           label, attempt, _CALL_ATTEMPTS, time.monotonic() - _t, exc)

    if not parsed:
        # All attempts failed — return 'error' so the sweep bumps attempts and,
        # after MAX_SCORE_ATTEMPTS across ticks, fail-opens (never a hard reject).
        logger.warning("Agent#10 ✗ %s — all %d attempt(s) failed: %s",
                       label, _CALL_ATTEMPTS, last_err)
        return {"accepted": False, "intent_score": 0, "in_market_score": 0,
                "icp_fit_score": None, "icp_fit_band": None, "gate_decision": "error",
                "reason": "", "signals": [], "web_searches": len(web_searches),
                "error": last_err}

    try:
        score = int(round(float(parsed.get("score"))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))  # clamp to 0-100
    accepted = score >= _ACCEPT_THRESHOLD

    raw_sigs = parsed.get("signals") if isinstance(parsed.get("signals"), list) else []
    signals = [
        {
            "type": str(s.get("type") or "signal"),
            "summary": str(s.get("summary") or "")[:300],
            "date": s.get("date") or None,
            "source": "web",
            "url": s.get("url") or None,
        }
        for s in raw_sigs if isinstance(s, dict) and s.get("summary")
    ]

    reason = str(parsed.get("reason") or "").strip()
    if not reason and not accepted:
        reason = "Low fit — no strong recent buying signals for this product"

    mark = "✓ QUALIFIED" if accepted else "✗ dropped"
    logger.info(
        "Agent#10 %s %s → score=%d (cutoff %d) in %.1fs — %d signal(s), "
        "%d Google search(es) [1 grounded API call]%s",
        mark, label, score, _ACCEPT_THRESHOLD, time.monotonic() - _t, len(signals),
        len(web_searches),
        "" if accepted else f" — {reason[:80]}",
    )

    return {
        "accepted": accepted,
        "intent_score": score,
        "in_market_score": score,
        "icp_fit_score": score,
        "icp_fit_band": None,
        "icp_missed_criteria": [],
        "gate_decision": "scored",
        "reason": "" if accepted else reason,
        "signals": signals,
        # How many Google Search queries this grounded call ran (billed) — the
        # caller sums these for a per-run cost log.
        "web_searches": len(web_searches),
    }


__all__ = ["score_company_intent", "derive_qualifying_signal"]
