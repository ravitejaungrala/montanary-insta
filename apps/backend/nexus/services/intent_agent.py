"""Bridge: NEXUS gate config <-> the Agent #10 scorer.

The live scoring engine moved to ``app/agents/agent10/scorer.py`` (2026-06-04).
This module is now a thin bridge: it keeps the NEXUS-level gate configuration
(kill-switch + score floor + fail-open ceiling) and RE-EXPORTS the scorer entry
points so existing callers keep importing ``intent_agent.score_company_intent``
and ``intent_agent.derive_qualifying_signal`` unchanged.

Scoring itself is a single Google-Search-grounded Gemini call per company,
signal-only (firmographic/ICP fit is handled upstream) — see the scorer module.
"""
from __future__ import annotations

import logging
import os

# Live scorer (self-contained; no NEXUS/DB imports). Re-exported so callers can
# keep using `intent_agent.score_company_intent` / `.derive_qualifying_signal`.
from app.agents.agent10 import derive_qualifying_signal, score_company_intent  # noqa: F401

logger = logging.getLogger("pipelyt.nexus.intent_agent")

# Accepted leads surface at >= this score (owner: ">=1 signal -> 70+").
INTENT_ACCEPT_FLOOR = 70

# Fail-open guard. If the agent errors on a company this many times, the lead is
# enrolled anyway (status "included_failopen") rather than stranded forever, so a
# broken agent can never permanently block outreach.
MAX_SCORE_ATTEMPTS = 3


def gate_enabled() -> bool:
    """Master kill-switch. ON by default. Set NEXUS_INTENT_GATE=0 to restore the
    pre-gate pipeline: leads enroll immediately on discovery with no scoring."""
    return os.getenv("NEXUS_INTENT_GATE", "1").strip() not in ("0", "false", "False", "")


__all__ = [
    "score_company_intent",
    "derive_qualifying_signal",
    "gate_enabled",
    "INTENT_ACCEPT_FLOOR",
    "MAX_SCORE_ATTEMPTS",
]
