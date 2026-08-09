"""Synthetic outreach data for validating the scorer before real campaign
volume exists (see /implementation.md §4.2, §6). Also the 'simulated' path
of the cold-start data-source switch in generator.py.

Primary path: one Gemini call (nexus.services.gemini.chat_completion) asks
for a realistic, deliberately-biased spread of outreach attempts. Same
strict-JSON-with-fallback pattern as nexus/services/intent_classifier.py.

Fallback path (no GEMINI_API_KEY, google-genai not installed, or the model
call/parse fails): a deterministic local generator producing the same shape,
biased the same way, so the scorer can be developed and validated fully
offline. This is not a toy — it's the same fallback discipline used
elsewhere in this codebase (intent_classifier.py's regex heuristic), and it
produces real OutreachRow objects the scorer cannot distinguish from a
Gemini-backed batch.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .dimensions import OutreachRow

logger = logging.getLogger("pipelyt.nexus.performance_agent.simulator")

_CHANNELS = ["email", "linkedin"]
# Real persona-bucket names from email_kit_generator.py's PERSONA_BUCKETS —
# a lead's title maps to one of exactly these labels in production, so
# simulated data uses the same real vocabulary instead of invented codes
# (was cfo_v1/cfo_v2/cmo_v1/generic_v1 — see /implementation-v2.md §3).
_VARIANTS = ["Finance", "CMO", "Data / IT", "Operations", "General"]
_CADENCE_STEPS = ["initial", "followup_1", "followup_2", "closing"]
_INDUSTRIES = ["Retail", "Financial Services", "Insurance"]
_REVENUE_WINDOWS = [(1_000_000, 10_000_000), (10_000_000, 50_000_000), (50_000_000, 250_000_000)]
_ROLES = ["CMO", "VP Marketing", "Director Of Marketing", "Growth Manager"]
_TECHNOLOGIES = ["Google BigQuery", "Snowflake", "HubSpot", "Google Ads", "Meta Ads"]
_LOCATIONS = ["United States", "India"]

# The combo the fallback generator deliberately favors, so a caller can
# assert the scorer recovers it as the #1-ranked slice on every dimension it
# touches. Matches the example in /implementation.md §4.2.
BIASED_COMBO = {
    "channel": "linkedin",
    "role": "CMO",
    "industry": "Retail",
    "weekday": 1,  # Tuesday
    "hour": 10,  # morning
}
_BIASED_POSITIVE_RATE = 0.55
_BIASED_MEETING_RATE = 0.30
_BASELINE_POSITIVE_RATE = 0.08
_BASELINE_MEETING_RATE = 0.02

# Below this row count, a Gemini batch is treated as unreliable regardless of
# whether the call itself succeeded — see generate_synthetic_campaign_data().
_MIN_ACCEPTABLE_GEMINI_ROWS = 50


def _deterministic_fallback(n_leads: int, seed: int = 42) -> List[OutreachRow]:
    """Explicitly injects a guaranteed block of rows matching BIASED_COMBO
    (~12% of the total, floor 30) at the elevated rate, plus a baseline
    block sampled uniformly across every dimension at the baseline rate.

    An earlier version tried to derive "is this row biased?" by chance from
    5 independently-sampled fields — the conjunction was so rare
    (~1/450 rows) that at realistic n it produced under 1 biased row on
    average, so the signal never showed up. Forcing a dedicated block fixes
    that without changing what the scorer is asked to prove."""
    rng = random.Random(seed)
    rows: List[OutreachRow] = []
    base_date = datetime(2026, 1, 5)
    biased_weekday_offset = (BIASED_COMBO["weekday"] - base_date.weekday()) % 7

    n_biased = max(30, int(n_leads * 0.12))
    n_baseline = max(n_leads - n_biased, n_biased)  # keep the baseline population comfortably larger

    def _make_row(i: int, *, force_biased: bool) -> OutreachRow:
        if force_biased:
            channel = BIASED_COMBO["channel"]
            role = BIASED_COMBO["role"]
            industry = BIASED_COMBO["industry"]
            hour = rng.choice([9, 10, 11])
            weekday_offset = biased_weekday_offset
        else:
            channel = rng.choice(_CHANNELS)
            role = rng.choice(_ROLES)
            industry = rng.choice(_INDUSTRIES)
            hour = rng.choice([9, 10, 11, 14, 15, 16, 18, 20])
            weekday_offset = rng.randint(0, 6)

        variant = rng.choice(_VARIANTS)
        cadence_step = rng.choice(_CADENCE_STEPS)
        rev_lo, rev_hi = rng.choice(_REVENUE_WINDOWS)
        techs = rng.sample(_TECHNOLOGIES, k=rng.randint(0, 2))
        location = rng.choice(_LOCATIONS)
        sent_at = base_date + timedelta(days=weekday_offset, hours=hour)

        positive_rate = _BIASED_POSITIVE_RATE if force_biased else _BASELINE_POSITIVE_RATE
        meeting_rate = _BIASED_MEETING_RATE if force_biased else _BASELINE_MEETING_RATE
        positive = rng.random() < positive_rate
        meeting_booked = positive and rng.random() < (meeting_rate / max(positive_rate, 1e-9))
        replied = positive or rng.random() < 0.10  # some non-positive replies too (NOT_INTERESTED etc.)
        intent = None
        if meeting_booked:
            intent = "DEMO_SCHEDULED"
        elif positive:
            intent = "INTERESTED"
        elif replied:
            intent = rng.choice(["NOT_INTERESTED", "NOT_NOW", "QUESTION"])

        return OutreachRow(
            campaign_id=1,
            lead_id=1000 + i,
            channel=channel,
            variant_key=variant,
            sent_at=sent_at,
            replied=replied,
            intent=intent,
            meeting_booked=meeting_booked,
            industry=industry,
            revenue_band=_band_label(rev_lo, rev_hi),
            role=role,
            cadence_step=cadence_step,
            technologies=techs,
            location=location,
        )

    for i in range(n_biased):
        rows.append(_make_row(i, force_biased=True))
    for i in range(n_baseline):
        rows.append(_make_row(n_biased + i, force_biased=False))

    rng.shuffle(rows)
    return rows


def _band_label(lo: int, hi: int) -> str:
    from .dimensions import revenue_band_for

    return revenue_band_for(lo, hi) or f"{lo}-{hi}"


_SYSTEM_PROMPT = """You generate realistic SYNTHETIC B2B outreach campaign data for
testing a ranking algorithm. Output STRICT JSON ONLY — an array of objects, no
prose, no markdown fences. Each object:
{"channel": "email"|"linkedin",
 "variant_key": "Finance"|"CMO"|"Data / IT"|"Operations"|"General"|"Marketing"|"Founder / Owner"|"Engineering Leadership",
 "cadence_step": "initial"|"followup_1"|"followup_2"|"closing",
 "sent_at": "YYYY-MM-DDTHH:MM:SS", "replied": true|false,
 "intent": "INTERESTED"|"DEMO_SCHEDULED"|"NOT_INTERESTED"|"NOT_NOW"|"QUESTION"|null,
 "meeting_booked": true|false, "industry": "<industry>",
 "revenue_band": "<band>", "role": "<job title>",
 "technologies": ["<tech>", ...], "location": "<country>"}

Deliberately bias the data: pick ONE specific combination of channel + role +
industry + a weekday/time-of-day window and give it a MUCH higher reply/
meeting rate than everything else (~50%+ positive reply rate vs ~5-10%
baseline elsewhere), so a ranking algorithm run over this data has a clear,
checkable #1 answer. Vary the other rows across a realistic spread of all
fields. intent must be null when replied is false."""


def generate_synthetic_campaign_data(
    n_leads: int = 200, bias_hint: str = "", seed: int = 42
) -> List[OutreachRow]:
    """Returns `n_leads` synthetic OutreachRow objects. Tries Gemini first;
    falls back to a deterministic local generator on any failure (no API
    key, SDK not installed, bad/unparseable response) OR an unreliable
    response — never raises.

    Found via live testing (2026-07-12): Gemini doesn't reliably follow the
    prompt's row-count or realistic-distribution instructions run to run —
    one live call returned 30 rows for a requested 300, with EVERY row
    marked as a success (raw_rate 1.0 on every dimension), a degenerate
    batch that produces a meaningless "everything is a 100% winner" insight.
    A too-small batch is treated as unreliable and discarded in favor of the
    deterministic generator, which always returns exactly `n_leads` rows
    with a controlled, realistic distribution."""
    try:
        rows = _via_gemini(n_leads, bias_hint)
        min_acceptable = min(_MIN_ACCEPTABLE_GEMINI_ROWS, n_leads)
        if rows and len(rows) >= min_acceptable:
            return rows
        if rows:
            logger.warning(
                "simulator: Gemini returned only %d/%d rows (below reliability "
                "floor of %d), using deterministic fallback instead",
                len(rows), n_leads, min_acceptable,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("simulator: Gemini generation failed, using deterministic fallback: %s", exc)
    return _deterministic_fallback(n_leads, seed=seed)


def _via_gemini(n_leads: int, bias_hint: str) -> Optional[List[OutreachRow]]:
    from nexus.services.gemini import chat_completion, extract_json

    user_prompt = f"Generate {n_leads} rows. {bias_hint}".strip()
    raw = chat_completion(
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.7,
        response_format_json=True,
        max_tokens=8192,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        return None

    rows: List[OutreachRow] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        try:
            sent_at = datetime.fromisoformat(str(item.get("sent_at")).replace("Z", ""))
        except (ValueError, TypeError):
            continue
        rows.append(
            OutreachRow(
                campaign_id=1,
                lead_id=1000 + i,
                channel=str(item.get("channel") or "email"),
                variant_key=str(item.get("variant_key") or "v1"),
                sent_at=sent_at,
                replied=bool(item.get("replied")),
                intent=item.get("intent") or None,
                meeting_booked=bool(item.get("meeting_booked")),
                industry=item.get("industry") or None,
                revenue_band=item.get("revenue_band") or None,
                role=item.get("role") or None,
                cadence_step=item.get("cadence_step") or None,
                technologies=[t for t in (item.get("technologies") or []) if isinstance(t, str)],
                location=item.get("location") or None,
            )
        )
    return rows or None


__all__ = ["generate_synthetic_campaign_data", "BIASED_COMBO"]
