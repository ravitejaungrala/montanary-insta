"""Agent #10 — buying-signal scorer.

The live scoring engine. Import the entry points directly:

    from app.agents.agent10 import score_company_intent, derive_qualifying_signal
"""
from .scorer import derive_qualifying_signal, score_company_intent

__all__ = ["score_company_intent", "derive_qualifying_signal"]
