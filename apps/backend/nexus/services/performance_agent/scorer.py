"""Pure Bayesian ranking math for the Performance Agent. No DB, no LLM, no
I/O — fully unit-testable in isolation (see scripts/test_performance_agent_scorer.py).

Design note (differs slightly from the original /implementation.md §4.1
sketch): v1 does not persist per-slice alpha/beta across runs (no new table
— see /implementation.md §5.7), so there is no cross-run state to decay.
Instead, recency is applied PER ROW within a single aggregation window (an
outreach attempt from 6 months ago counts less than one from last week),
then folded into one Beta-Binomial update per slice. Same idea as the
persisted-posterior design, just single-pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .dimensions import METRICS, OutreachRow, extract_slices

# Weak prior centered around a ~5% success rate (Beta(1,19) has mean 0.05) —
# a reasonable cold-outreach baseline that a slice needs real evidence to
# beat. Not tuned against real data yet; revisit once volume exists.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 19.0

DEFAULT_HALF_LIFE_DAYS = 60.0
DEFAULT_MIN_SAMPLE_SIZE = 10

# See /implementation.md §7 open item 1 — meeting-booked weighted higher as
# the stronger buying signal. Placeholder; sanity-check once real data exists.
COMBINED_WEIGHTS = {"positive_reply": 0.4, "meeting_booked": 0.6}


@dataclass
class RankedSlice:
    dimension: str
    slice_value: str
    sends: int
    successes: int
    raw_rate: float
    posterior_mean: float
    confidence: str  # 'low' | 'ok'


def posterior_mean(alpha: float, beta: float) -> float:
    total = alpha + beta
    return (alpha / total) if total > 0 else 0.0


def update_posterior(prior_alpha: float, prior_beta: float,
                      weighted_sends: float, weighted_successes: float) -> Tuple[float, float]:
    """One Beta-Binomial update. `weighted_*` may be fractional — callers
    fold per-row recency decay into these sums before calling this."""
    weighted_successes = min(weighted_successes, weighted_sends)  # guard against caller bugs
    alpha = prior_alpha + weighted_successes
    beta = prior_beta + (weighted_sends - weighted_successes)
    return alpha, beta


def _decay_weight(sent_at: Optional[datetime], now: datetime, half_life_days: float) -> float:
    if not sent_at or half_life_days <= 0:
        return 1.0
    days = max(0.0, (now - sent_at).total_seconds() / 86400.0)
    return 0.5 ** (days / half_life_days)


def _compute_single_metric(
    rows: List[OutreachRow],
    metric: str,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    prior_alpha: float = PRIOR_ALPHA,
    prior_beta: float = PRIOR_BETA,
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
) -> List[RankedSlice]:
    """metric in {'positive_reply', 'meeting_booked'} only — 'combined' is
    handled by rank_slices(), which blends two calls to this function."""
    if now is None:
        # Anchor recency to the freshest `sent_at` IN THE DATASET, not real
        # wall-clock time. Bug found via live testing (2026-07-12): simulated
        # rows carry LLM-invented or fixture dates that can be arbitrarily far
        # from the real calendar date (months to years). Decaying against
        # real `utcnow()` then collapsed EVERY slice's weighted sends/successes
        # toward ~0, so every posterior_mean converged on the prior (~0.05)
        # regardless of actual reply rate — the ranking became meaningless
        # (raw_rate stayed correct; posterior_mean, the thing sorted on, did
        # not). Anchoring to the dataset's own max(sent_at) is correct for
        # both real data (freshest touchpoint is naturally near true "now")
        # and simulated data (decay is measured relative to the fictional
        # timeline instead of a mismatched real one).
        sent_ats = [r.sent_at for r in rows if r.sent_at]
        now = max(sent_ats) if sent_ats else datetime.utcnow()
    buckets: Dict[Tuple[str, str], Dict[str, float]] = {}

    for row in rows:
        outcome = row.positive_reply if metric == "positive_reply" else bool(row.meeting_booked)
        weight = _decay_weight(row.sent_at, now, half_life_days)
        for dim, val in extract_slices(row):
            b = buckets.setdefault(
                (dim, val),
                {"sends": 0, "successes": 0, "w_sends": 0.0, "w_successes": 0.0},
            )
            b["sends"] += 1
            b["w_sends"] += weight
            if outcome:
                b["successes"] += 1
                b["w_successes"] += weight

    ranked: List[RankedSlice] = []
    for (dim, val), b in buckets.items():
        alpha, beta = update_posterior(prior_alpha, prior_beta, b["w_sends"], b["w_successes"])
        sends = int(b["sends"])
        raw_rate = (b["successes"] / sends) if sends else 0.0
        ranked.append(
            RankedSlice(
                dimension=dim,
                slice_value=val,
                sends=sends,
                successes=int(b["successes"]),
                raw_rate=round(raw_rate, 4),
                posterior_mean=round(posterior_mean(alpha, beta), 4),
                confidence="ok" if sends >= min_sample_size else "low",
            )
        )

    ranked.sort(key=lambda r: r.posterior_mean, reverse=True)
    return ranked


def rank_slices(
    rows: List[OutreachRow],
    metric: str,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    prior_alpha: float = PRIOR_ALPHA,
    prior_beta: float = PRIOR_BETA,
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
    combined_weights: Optional[Dict[str, float]] = None,
) -> List[RankedSlice]:
    """metric in {'positive_reply', 'meeting_booked', 'combined'}.

    Groups rows by every (dimension, slice_value) pair from dimensions.py,
    applies a recency-weighted Beta-Binomial update per slice, sorts by
    posterior_mean descending. Slices with sends < min_sample_size are
    INCLUDED but flagged confidence='low' — never hidden, since a
    low-sample winner is still useful signal to a human reviewing it.

    'combined' blends the two real metrics into one score rather than
    deriving its own posterior:
        score = weights['positive_reply'] * posterior_mean(positive_reply)
              + weights['meeting_booked']  * posterior_mean(meeting_booked)
    """
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")

    if metric != "combined":
        return _compute_single_metric(
            rows, metric, now=now, half_life_days=half_life_days,
            prior_alpha=prior_alpha, prior_beta=prior_beta,
            min_sample_size=min_sample_size,
        )

    weights = combined_weights or COMBINED_WEIGHTS
    pos_by_key = {
        (r.dimension, r.slice_value): r
        for r in _compute_single_metric(
            rows, "positive_reply", now=now, half_life_days=half_life_days,
            prior_alpha=prior_alpha, prior_beta=prior_beta, min_sample_size=min_sample_size,
        )
    }
    meet_by_key = {
        (r.dimension, r.slice_value): r
        for r in _compute_single_metric(
            rows, "meeting_booked", now=now, half_life_days=half_life_days,
            prior_alpha=prior_alpha, prior_beta=prior_beta, min_sample_size=min_sample_size,
        )
    }

    combined: List[RankedSlice] = []
    for key in set(pos_by_key) | set(meet_by_key):
        dim, val = key
        p = pos_by_key.get(key)
        m = meet_by_key.get(key)
        # Both are computed from the identical row population for this slice
        # (just different outcome flags), so their `sends` always agree when
        # both exist.
        sends = p.sends if p else (m.sends if m else 0)
        # BUG FIX (2026-07-13, found via live UI testing): this used to set
        # raw_rate = the SAME blended posterior score as posterior_mean, so
        # the UI displayed it as a plain "%" indistinguishable from a real
        # successes/sends rate — misleading, since it's actually a weighted
        # Bayesian composite, not an empirical measurement. raw_rate now
        # blends the two metrics' own *raw* (unshrunk) rates, so it stays an
        # honest "what actually happened" number on every tab; posterior_mean
        # keeps the Bayesian blend used for ranking/sorting/confidence.
        raw_score = (
            weights.get("positive_reply", 0.0) * (p.raw_rate if p else 0.0)
            + weights.get("meeting_booked", 0.0) * (m.raw_rate if m else 0.0)
        )
        posterior_score = (
            weights.get("positive_reply", 0.0) * (p.posterior_mean if p else 0.0)
            + weights.get("meeting_booked", 0.0) * (m.posterior_mean if m else 0.0)
        )
        combined.append(
            RankedSlice(
                dimension=dim,
                slice_value=val,
                sends=sends,
                # Not a literal count (mixes two outcome types) — informational only.
                successes=(p.successes if p else 0) + (m.successes if m else 0),
                raw_rate=round(raw_score, 4),
                posterior_mean=round(posterior_score, 4),
                confidence="ok" if sends >= min_sample_size else "low",
            )
        )

    combined.sort(key=lambda r: r.posterior_mean, reverse=True)
    return combined


__all__ = [
    "RankedSlice",
    "PRIOR_ALPHA",
    "PRIOR_BETA",
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_MIN_SAMPLE_SIZE",
    "COMBINED_WEIGHTS",
    "posterior_mean",
    "update_posterior",
    "rank_slices",
]
