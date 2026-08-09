"""Shared row shape + dimension extraction for the Performance Agent.

`OutreachRow` is deliberately provider-agnostic: `aggregator.py` (real data)
and `simulator.py` (Gemini-generated synthetic data) both produce it, so
`scorer.py` cannot tell which source it's ranking. See /implementation.md
§1-§3 for the design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

# Ranking metrics exposed by the scorer. 'reply' is intentionally NOT a
# standalone ranking metric — plain replies include negative/OOO noise and
# aren't actionable alone. See /implementation.md §2.
METRICS = ("positive_reply", "meeting_booked", "combined")

# Intents from nexus/services/intent_classifier.py that count as a
# "positive reply" for ranking purposes.
POSITIVE_INTENTS = {"INTERESTED", "DEMO_SCHEDULED"}

# Revenue bands for `segment:revenue_band` — mirrors the band choices in
# NexusNewCampaign.jsx's "Company Revenue" picker (revenueLabel()).
_REVENUE_BANDS: List[Tuple[Optional[float], Optional[float], str]] = [
    (None, 1_000_000, "<1M"),
    (1_000_000, 10_000_000, "1M-10M"),
    (10_000_000, 50_000_000, "10M-50M"),
    (50_000_000, 250_000_000, "50M-250M"),
    (250_000_000, 1_000_000_000, "250M-1B"),
    (1_000_000_000, None, "1B+"),
]

_TIMING_BLOCK_HOURS = 3  # 8 buckets/day: 00-03, 03-06, ..., 21-24
_WEEKDAY_ABBR = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# `nexus_touchpoints.sent_at` is stored as naive UTC (Postgres NOW()), same
# as everywhere else in this codebase. Bucketing "Tuesday 9-12" in raw UTC
# would mean a different real-world time depending on who's reading it —
# same convention already used for calendar-day boundaries in
# sequencer.py's `_DAY_TZ_OFFSET`: shift to IST (UTC+5:30) before deriving
# any calendar-relative value, since that's this business's operating
# timezone. Mirrored here (not imported from sequencer.py — that module
# owns email-send scheduling, not performance analytics; duplicating one
# constant is cheaper than a cross-service import for it).
_TZ_OFFSET = timedelta(hours=5, minutes=30)


@dataclass
class OutreachRow:
    """One outreach attempt (a single touchpoint) with its outcome and the
    dimension values needed to slice it. `technologies` is a list because a
    lead/campaign can match more than one target technology at once — the
    row contributes one slice per technology, not one row per technology."""

    campaign_id: int
    lead_id: int
    channel: str  # 'email' | 'linkedin'
    variant_key: str
    sent_at: datetime
    replied: bool
    intent: Optional[str] = None  # 'INTERESTED' | 'DEMO_SCHEDULED' | ... | None
    meeting_booked: bool = False
    industry: Optional[str] = None
    revenue_band: Optional[str] = None  # pre-bucketed; use revenue_band_for() to build it
    role: Optional[str] = None
    # 'initial' | 'followup_1' | 'followup_2' | 'closing' — same vocabulary as
    # nexus_lead_emails.kind / sequencer.py's _STEP_KIND. Which cadence step
    # this attempt was (not what channel/segment/timing it targeted) — added
    # 2026-07-13, cheapest new dimension since real data already carries it.
    cadence_step: Optional[str] = None
    technologies: List[str] = field(default_factory=list)
    location: Optional[str] = None

    @property
    def positive_reply(self) -> bool:
        return bool(self.replied and self.intent in POSITIVE_INTENTS)


def revenue_band_for(min_val: Optional[float], max_val: Optional[float]) -> Optional[str]:
    """Bucket a {min,max} revenue window (as stored in campaign.icp.revenue_range)
    into one of the fixed bands above. Returns None for an unbounded/empty window
    (no revenue filter applied — shouldn't be treated as its own slice)."""
    if min_val is None and max_val is None:
        return None
    # Match by the band whose range overlaps the window's midpoint (or its one
    # bound, if only one side is set) — good enough for a coarse segment label;
    # exact Apollo-style multi-window splitting isn't needed for ranking.
    anchor = min_val if min_val is not None else max_val
    if max_val is not None and min_val is not None:
        anchor = (min_val + max_val) / 2
    for lo, hi, label in _REVENUE_BANDS:
        if (lo is None or anchor >= lo) and (hi is None or anchor < hi):
            return label
    return None


def timing_bucket_for(dt: Optional[datetime]) -> Optional[str]:
    """'tue_09-12'-style bucket: weekday + a 3-hour block of the send hour,
    in IST — see _TZ_OFFSET above. `dt` is the naive-UTC `sent_at` value;
    shifted here so "Tuesday 9-12" means the same real-world window
    regardless of who's reading the ranking."""
    if dt is None:
        return None
    local_dt = dt + _TZ_OFFSET
    weekday = _WEEKDAY_ABBR[local_dt.weekday()]
    block_start = (local_dt.hour // _TIMING_BLOCK_HOURS) * _TIMING_BLOCK_HOURS
    block_end = block_start + _TIMING_BLOCK_HOURS
    return f"{weekday}_{block_start:02d}-{block_end:02d}"


def extract_slices(row: OutreachRow) -> List[Tuple[str, str]]:
    """Every (dimension, slice_value) pair this row contributes to. A row
    with 3 target technologies contributes 3 `segment:technology` slices —
    one row, several slices, by design (see /implementation.md §1)."""
    slices: List[Tuple[str, str]] = []

    if row.channel:
        slices.append(("channel", row.channel))
    if row.variant_key:
        slices.append(("variant", row.variant_key))
    if row.cadence_step:
        slices.append(("cadence_step", row.cadence_step))
    if row.industry:
        slices.append(("segment:industry", row.industry))
    if row.revenue_band:
        slices.append(("segment:revenue_band", row.revenue_band))
    if row.role:
        slices.append(("segment:role", row.role))
    for tech in row.technologies or []:
        if tech:
            slices.append(("segment:technology", tech))
    if row.location:
        slices.append(("segment:location", row.location))
    tb = timing_bucket_for(row.sent_at)
    if tb:
        slices.append(("timing_bucket", tb))

    return slices


__all__ = [
    "OutreachRow",
    "METRICS",
    "POSITIVE_INTENTS",
    "revenue_band_for",
    "timing_bucket_for",
    "extract_slices",
]
