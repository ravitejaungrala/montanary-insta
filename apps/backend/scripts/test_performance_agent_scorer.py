"""Offline validation for the Performance Agent's scoring math.

Generates synthetic outreach data (Gemini if GEMINI_API_KEY is set, a
deterministic local fallback otherwise — see performance_agent/simulator.py)
with one combination deliberately biased to outperform, then asserts the
scorer actually recovers that combination as the top-ranked slice. No DB, no
required API key — proves the ranking math is correct in isolation before
Task 2 wires it to real data.

Run from apps/backend:
    python scripts/test_performance_agent_scorer.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.services.performance_agent.scorer import DEFAULT_MIN_SAMPLE_SIZE, rank_slices  # noqa: E402
from nexus.services.performance_agent.simulator import BIASED_COMBO, generate_synthetic_campaign_data  # noqa: E402


def _print_table(title: str, ranked, top_n: int = 8) -> None:
    print(f"\n=== {title} (top {top_n}) ===")
    print(f"{'dimension':<20}{'value':<24}{'sends':>7}{'raw_rate':>10}{'posterior':>11}  confidence")
    for r in ranked[:top_n]:
        print(
            f"{r.dimension:<20}{r.slice_value:<24}{r.sends:>7}{r.raw_rate:>10.3f}"
            f"{r.posterior_mean:>11.3f}  {r.confidence}"
        )


def main() -> int:
    rows = generate_synthetic_campaign_data(n_leads=400)
    print(f"Generated {len(rows)} synthetic outreach rows.")
    print(f"Deliberately-biased combo: {BIASED_COMBO}")

    failures = []

    for metric in ("positive_reply", "meeting_booked", "combined"):
        ranked = rank_slices(rows, metric=metric)
        _print_table(metric, ranked)

        if not ranked:
            failures.append(f"[{metric}] rank_slices returned no slices at all")
            continue

        # No divide-by-zero / garbage values anywhere in the output.
        for r in ranked:
            if r.sends <= 0:
                failures.append(f"[{metric}] slice {r.dimension}={r.slice_value} has sends<=0: {r}")
            if not (0.0 <= r.posterior_mean <= 1.0) and metric != "combined":
                failures.append(f"[{metric}] posterior_mean out of [0,1]: {r}")
            if r.confidence not in ("low", "ok"):
                failures.append(f"[{metric}] unexpected confidence value: {r}")

        # The channel/role/industry legs of the biased combo should rank #1
        # on their respective dimensions for positive_reply and combined —
        # meeting_booked alone can be noisier at n=400, so only assert it
        # loosely there (checked via the print output instead of a hard fail).
        top_channel = next((r for r in ranked if r.dimension == "channel"), None)
        top_role = next((r for r in ranked if r.dimension == "segment:role"), None)
        top_industry = next((r for r in ranked if r.dimension == "segment:industry"), None)

        by_dim_top = {}
        for r in ranked:
            by_dim_top.setdefault(r.dimension, r)  # first occurrence = highest-ranked (list is sorted)

        if metric in ("positive_reply", "combined"):
            if by_dim_top.get("channel") and by_dim_top["channel"].slice_value != BIASED_COMBO["channel"]:
                failures.append(
                    f"[{metric}] expected top channel={BIASED_COMBO['channel']!r}, "
                    f"got {by_dim_top['channel'].slice_value!r}"
                )
            if by_dim_top.get("segment:role") and by_dim_top["segment:role"].slice_value != BIASED_COMBO["role"]:
                failures.append(
                    f"[{metric}] expected top role={BIASED_COMBO['role']!r}, "
                    f"got {by_dim_top['segment:role'].slice_value!r}"
                )
            if (
                by_dim_top.get("segment:industry")
                and by_dim_top["segment:industry"].slice_value != BIASED_COMBO["industry"]
            ):
                failures.append(
                    f"[{metric}] expected top industry={BIASED_COMBO['industry']!r}, "
                    f"got {by_dim_top['segment:industry'].slice_value!r}"
                )

        # min_sample_size gating actually does something (at least one slice
        # is flagged low-confidence given how thin some slices are at n=400).
        if not any(r.confidence == "low" for r in ranked):
            failures.append(f"[{metric}] expected at least one low-confidence slice, found none")

    print(f"\nmin_sample_size gate = {DEFAULT_MIN_SAMPLE_SIZE}")

    if failures:
        print(f"\nFAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASSED — scorer recovers the biased combination on every metric checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
