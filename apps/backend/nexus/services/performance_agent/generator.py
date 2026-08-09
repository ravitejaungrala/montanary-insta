"""The Performance Agent itself: aggregate (or simulate) -> score -> summarize
-> persist. See /implementation.md §5.2 and §6.

`generate_insight()` needs a live DB session — it is the one piece of this
package that could not be executed in the environment this was built in (no
DATABASE_URL configured there). Everything it calls (scorer.rank_slices,
_build_summary) is unit-tested independently; this function's correctness
was verified by code review against the exact schema/columns, not by a live
run — flag this for a real DB smoke test before shipping.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import aggregator, simulator
from .dimensions import METRICS
from .scorer import RankedSlice, rank_slices

logger = logging.getLogger("pipelyt.nexus.performance_agent.generator")

# Workspace-wide floors (not per-slice — see scorer.DEFAULT_MIN_SAMPLE_SIZE for
# that). Env-tunable without a deploy, same pattern as AGENT10_ACCEPT_THRESHOLD
# in app/agents/agent10/scorer.py. See /implementation.md §6.1 / §7 item 4.
#
# Two floors, both required, because volume alone doesn't prove the data is
# informative: 500 sends with 0 replies still produces a flat,
# prior-dominated ranking on every slice (nothing to actually rank), which
# would display with full 'real' confidence and no caveat — worse than the
# clearly-labeled simulated fallback it's supposed to replace. Requiring a
# minimum count of actual outcomes (positive replies + meetings) alongside
# sends ensures 'real' only shows once there's real signal to rank on.
MIN_REAL_TOUCHPOINTS = int(os.getenv("PERFORMANCE_MIN_REAL_TOUCHPOINTS", "50") or "50")
MIN_REAL_OUTCOMES = int(os.getenv("PERFORMANCE_MIN_REAL_OUTCOMES", "10") or "10")

_TOP_N_WINNING_EXAMPLES = 5


def _resolve_data_source(db: Session, workspace_id: int, force: Optional[str] = None) -> str:
    """Returns 'real' or 'simulated'. See /implementation.md §6.1."""
    if force in ("real", "simulated"):
        return force
    if aggregator.count_real_touchpoints(db, workspace_id) < MIN_REAL_TOUCHPOINTS:
        return "simulated"
    if aggregator.count_real_outcomes(db, workspace_id) < MIN_REAL_OUTCOMES:
        return "simulated"
    return "real"


def _ranked_to_jsonable(ranked: List[RankedSlice]) -> List[Dict[str, Any]]:
    return [
        {
            "dimension": r.dimension,
            "slice_value": r.slice_value,
            "sends": r.sends,
            "successes": r.successes,
            "raw_rate": r.raw_rate,
            "posterior_mean": r.posterior_mean,
            "confidence": r.confidence,
        }
        for r in ranked
    ]


# Human labels for dimension keys, used only inside generated recommendation
# text (the frontend has its own copy for panel headers — see
# NexusPerformanceAgent.jsx's DIMENSION_LABELS. Kept separate deliberately:
# one's Python summary text, the other's React UI chrome; not worth a shared
# module for 8 short strings).
_PRETTY_DIM = {
    "channel": "Channel",
    "variant": "Email Persona",
    "cadence_step": "Email stage",
    "segment:industry": "Industry",
    "segment:revenue_band": "Company size",
    "segment:role": "Job title",
    "segment:technology": "Tech stack",
    "segment:location": "Location",
    "timing_bucket": "Send time",
}


def _pretty_dim(dimension: str) -> str:
    return _PRETTY_DIM.get(dimension, dimension)


def _top_lines(ranked: List[RankedSlice], n: int = 5) -> List[str]:
    """Feeds Gemini the PRETTY dimension name (e.g. "Send time"), not the raw
    key ("timing_bucket") — otherwise the model echoes the raw key verbatim
    into generated action text (found via live testing, 2026-07-13)."""
    ok = [r for r in ranked if r.confidence == "ok"] or ranked
    return [
        f"{_pretty_dim(r.dimension)}={r.slice_value} (rate {r.raw_rate:.1%}, {r.sends} sends)"
        for r in ok[:n]
    ]


_METRIC_VERB = {
    "combined": "is your strongest overall combination",
    "positive_reply": "gets the most positive replies",
    "meeting_booked": "converts best to booked meetings",
}


def _build_recommendations(ranked_by_metric: Dict[str, List[RankedSlice]], data_source: str) -> Dict[str, Any]:
    """Pure function (no DB) — takes ranked slices, returns
    {headline, summary, actions (top 5, each {title, detail}), caveat}.
    Tries Gemini first, falls back to a template built directly from the
    ranked data on any failure (no key, SDK missing, call error, bad JSON)
    — never raises, never returns an empty `actions` list.
    See /implementation-v2.md §4."""
    try:
        rec = _recommendations_via_gemini(ranked_by_metric, data_source)
        if rec and rec.get("actions"):
            return rec
    except Exception as exc:  # noqa: BLE001
        logger.warning("generator: Gemini recommendations failed, using templated fallback: %s", exc)
    return _recommendations_fallback(ranked_by_metric, data_source)


def _recommendations_fallback(ranked_by_metric: Dict[str, List[RankedSlice]], data_source: str) -> Dict[str, Any]:
    def _confident_first(metric: str) -> List[RankedSlice]:
        ranked = ranked_by_metric.get(metric) or []
        ok = [r for r in ranked if r.confidence == "ok"]
        return ok or ranked

    # Best slice PER DIMENSION (preferring 'combined' — the blended "best
    # overall" measure — then positive_reply, then meeting_booked), so the 5
    # actions cover 5 different levers instead of repeating one dimension.
    best_by_dim: Dict[str, tuple] = {}
    for metric_label in ("combined", "positive_reply", "meeting_booked"):
        for r in _confident_first(metric_label):
            if r.dimension not in best_by_dim:
                best_by_dim[r.dimension] = (r, metric_label)

    ordered = sorted(best_by_dim.values(), key=lambda pair: pair[0].posterior_mean, reverse=True)[:5]

    # Condensed TL;DR — same top levers as `actions`, but terse "Dimension:
    # value" chips instead of title+detail cards. A distinct, shorter section,
    # not a duplicate of the 5-point list (see /implementation-v2.md follow-up).
    quick_wins = [f"{_pretty_dim(r.dimension)}: {r.slice_value}" for r, _ in ordered[:3]]

    actions = [
        {
            "title": f"{_pretty_dim(r.dimension)}: {r.slice_value}",
            "detail": f"{_METRIC_VERB[metric_label]} — {r.raw_rate:.0%} of {r.sends} sends.",
        }
        for r, metric_label in ordered
    ]

    if ordered:
        top = ordered[0][0]
        headline = f'Your best lever right now: {_pretty_dim(top.dimension)} "{top.slice_value}".'
        lever_list = ", ".join(f'{_pretty_dim(r.dimension)} "{r.slice_value}"' for r, _ in ordered[:3])
        summary = (
            f"Outreach performance varies noticeably by segment right now. The clearest "
            f"patterns are {lever_list}. Concentrate additional volume on these "
            f"combinations while continuing to test the weaker segments — a small sample "
            f"today can still shift meaningfully as more campaigns run."
        )
    else:
        headline = "Not enough data yet to identify a clear winner."
        summary = "Not enough outreach activity yet to identify meaningful patterns — check back after more campaigns run."

    caveat = (
        "Based on typical outreach patterns, not your own campaigns yet — treat as directional."
        if data_source == "simulated"
        else ""
    )
    return {
        "headline": headline,
        "summary": summary,
        "quick_wins": quick_wins,
        "actions": actions,
        "caveat": caveat,
    }


def _recommendations_via_gemini(ranked_by_metric: Dict[str, List[RankedSlice]], data_source: str) -> Optional[Dict[str, Any]]:
    from nexus.services.gemini import chat_completion, extract_json

    combined_lines = "\n".join(_top_lines(ranked_by_metric.get("combined") or [], n=8)) or "(none)"
    pos_lines = "\n".join(_top_lines(ranked_by_metric.get("positive_reply") or [], n=8)) or "(none)"
    meet_lines = "\n".join(_top_lines(ranked_by_metric.get("meeting_booked") or [], n=8)) or "(none)"
    source_note = (
        "This is SIMULATED data (not enough real campaigns yet) — the caveat "
        "field must say so plainly."
        if data_source == "simulated"
        else "This is real campaign data — caveat can be an empty string unless "
        "the sample is clearly thin."
    )
    user_prompt = (
        f"{source_note}\n\n"
        f"Top overall-score slices (dimension=value, rate, sends):\n{combined_lines}\n\n"
        f"Top positive-reply slices:\n{pos_lines}\n\n"
        f"Top meeting-booked slices:\n{meet_lines}\n\n"
        'Return STRICT JSON ONLY (no markdown fences): {"headline": "<one '
        'sentence: the single biggest lever right now>", "summary": "<2-3 '
        'sentences giving fuller context on what the data shows and why it '
        'matters, for a marketer reading this report>", "quick_wins": '
        '["<3-5 word phrase>", "<3-5 word phrase>", "<3-5 word phrase>"], '
        '"actions": [{"title": "<short imperative, 8 words or fewer>", '
        '"detail": "<one sentence: the supporting evidence and why it '
        'matters>"}, ... exactly 5 of these], "caveat": "<one sentence, or '
        'empty string>"}\n'
        "`quick_wins` is a CONDENSED 3-item TL;DR (just \"Dimension: value\", "
        "e.g. \"Send time: Tuesday mornings\") — shorter and terser than the "
        "5 detailed actions, not a duplicate of them in wording. Each of the "
        "5 actions MUST cover a DIFFERENT dimension (channel, persona, "
        "industry, company size, job title, tech stack, location, email "
        "stage, or send time) — never repeat the same lever twice. Never "
        "invent numbers not present in the input."
    )
    system_prompt = (
        "You are a B2B outreach performance analyst. Output ONLY the JSON "
        "object requested, nothing else. Never invent facts or numbers."
    )
    raw = chat_completion(
        system=system_prompt,
        user=user_prompt,
        temperature=0.4,
        max_tokens=900,
        response_format_json=True,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        return None
    actions = []
    for a in (parsed.get("actions") or [])[:5]:
        if not isinstance(a, dict):
            continue
        title = str(a.get("title") or "").strip()
        detail = str(a.get("detail") or "").strip()
        if title or detail:
            actions.append({"title": title, "detail": detail})
    if not actions:
        return None
    quick_wins = [str(q).strip() for q in (parsed.get("quick_wins") or []) if str(q).strip()][:3]
    return {
        "headline": str(parsed.get("headline") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "quick_wins": quick_wins,
        "actions": actions,
        "caveat": str(parsed.get("caveat") or "").strip(),
    }


def _flatten_recommendations(rec: Dict[str, Any]) -> str:
    """Plain-text mirror of the structured recommendations, kept in the
    existing `summary` TEXT column for anything that still reads it
    directly (back-compat)."""
    lines = [rec.get("headline") or ""]
    if rec.get("summary"):
        lines.append(rec["summary"])
    for q in rec.get("quick_wins") or []:
        lines.append(f"* {q}")
    for a in rec.get("actions") or []:
        title, detail = a.get("title") or "", a.get("detail") or ""
        if title and detail:
            lines.append(f"- {title}: {detail}")
        elif title or detail:
            lines.append(f"- {title or detail}")
    if rec.get("caveat"):
        lines.append(rec["caveat"])
    return "\n".join(line for line in lines if line)


def generate_insight(
    db: Session,
    workspace_id: int,
    campaign_id: Optional[int] = None,
    since: Optional[datetime] = None,
    force_source: Optional[str] = None,
) -> int:
    """Runs one full Performance Agent pass. Returns the new
    nexus_performance_insights row id. Never raises past step 1 — any
    failure after the row exists is captured as status='failed' on that row."""
    from nexus.models_phase6 import PerformanceInsight, WinningExample

    period_end = date.today()
    period_start = (since.date() if since else period_end - timedelta(days=30))

    insight = PerformanceInsight(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        status="running",
        period_start=period_start,
        period_end=period_end,
    )
    db.add(insight)
    db.commit()
    db.refresh(insight)

    try:
        data_source = _resolve_data_source(db, workspace_id, force=force_source)
        if data_source == "real":
            rows = aggregator.collect_outreach_rows(
                db, workspace_id, campaign_id=campaign_id, since=since
            )
        else:
            rows = simulator.generate_synthetic_campaign_data(n_leads=300)

        ranked_by_metric = {metric: rank_slices(rows, metric=metric) for metric in METRICS}
        recommendations = _build_recommendations(ranked_by_metric, data_source)

        insight.metrics = {"data_source": data_source, "total_rows": len(rows)}
        insight.outreach_data = {"row_count": len(rows)}
        insight.outreach_insights = {
            metric: _ranked_to_jsonable(ranked) for metric, ranked in ranked_by_metric.items()
        }
        # campaign_insights was an unused phase6 JSONB column — repurposed here
        # for the structured {headline, actions, caveat} block (see
        # /implementation-v2.md §4). No migration needed.
        insight.campaign_insights = recommendations
        insight.summary = _flatten_recommendations(recommendations)
        insight.status = "ready"
        insight.generated_at = datetime.utcnow()
        db.add(insight)

        _upsert_winning_examples(db, workspace_id, campaign_id, ranked_by_metric, data_source)

        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("generator: generate_insight failed for workspace=%s", workspace_id)
        db.rollback()
        insight.status = "failed"
        insight.error = str(exc)[:2000]
        db.add(insight)
        db.commit()

    return insight.id


def _upsert_winning_examples(
    db: Session,
    workspace_id: int,
    campaign_id: Optional[int],
    ranked_by_metric: Dict[str, List[RankedSlice]],
    data_source: str,
) -> None:
    """Replaces nexus_winning_examples for this (workspace_id, data_source)
    with the current top-N confident `variant` slices, so
    email_kit_generator.py / content_generator.py can reuse them (§5.6/§6.4
    — real-data rows only get read by the feedback loop; simulated rows are
    tagged so that guardrail can filter them out).

    BUG FIX (2026-07-14, found via live review): this used to delete only
    the row matching the EXACT same (workspace_id, example_type,
    lead_segment) key before inserting — so changing the persona vocabulary
    (or anything else that changes `lead_segment`'s contents) orphaned every
    old row forever, since its key would never recur. The frontend then
    showed old and new rows mixed together indefinitely. Fixed by wiping
    every row for this (workspace_id, data_source) scope first — this table
    is documented as "the current best-patterns cache," not a historical
    log, so a full replace on every generate() call is the correct
    behavior, not a regression."""
    from nexus.models_phase6 import WinningExample

    ranked = ranked_by_metric.get("positive_reply") or []
    top_variants = [
        r for r in ranked if r.dimension == "variant" and r.confidence == "ok"
    ][:_TOP_N_WINNING_EXAMPLES]

    db.query(WinningExample).filter(
        WinningExample.workspace_id == workspace_id,
        WinningExample.example_type == "subject",
        WinningExample.lead_segment["data_source"].astext == data_source,
    ).delete(synchronize_session=False)

    for r in top_variants:
        lead_segment = {"variant_key": r.slice_value, "data_source": data_source}
        db.add(
            WinningExample(
                workspace_id=workspace_id,
                outreach_id=None,
                lead_id=None,
                campaign_id=campaign_id if data_source == "real" else None,
                example_type="subject",
                # Human-readable, not the raw "variant=cfo_v1"-style dump this
                # used to be — this string is shown directly in the frontend's
                # "what's working right now" list (see /implementation-v2.md §5).
                text=f"Email Persona: {r.slice_value} — {r.raw_rate:.0%} positive reply rate",
                personalization_depth="",
                prompt_version="",
                intent="INTERESTED",
                lead_segment=lead_segment,
                reply_rate=r.posterior_mean,
            )
        )


__all__ = ["generate_insight", "MIN_REAL_TOUCHPOINTS", "MIN_REAL_OUTCOMES"]
