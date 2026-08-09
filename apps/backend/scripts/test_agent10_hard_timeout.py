"""Offline test for Agent #10's HARD wall-clock cap (2026-06-11).

A grounded Gemini call whose socket stays warm can outlive the 90s HTTP read
timeout (observed: Google's server 504s at ~100-104s). The hard cap bounds
TOTAL call time: past the limit the call is abandoned and the company is
returned as gate_decision='error' -> the run spends NO credits on it and the
lead is ignored for this run.

This test fakes a Gemini client that hangs for 60s, sets the hard cap to 2s,
and asserts:
  1. score_company_intent returns in ~2s, not 60s (the guillotine fired);
  2. the verdict is gate_decision='error', accepted=False (lead ignored);
  3. the error names the hard cap;
  4. a FAST fake call still parses normally (the cap never harms real calls).

No network, no DB, no credits.

Run from apps/backend:
    venv/Scripts/python.exe -X utf8 scripts/test_agent10_hard_timeout.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")
os.environ["GEMINI_API_KEY"] = "fake-key-for-offline-test"

from app.agents.agent10 import scorer  # noqa: E402

# The production cap is HARDCODED at 105s; shrink it for the test so the
# hang case resolves in ~2s instead of ~2 minutes.
scorer._HARD_TIMEOUT_SEC = 2.0

from google import genai  # noqa: E402

FAILURES: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


class _FakeModels:
    def __init__(self, mode):
        self.mode = mode

    def generate_content(self, **kwargs):
        if self.mode == "hang":
            time.sleep(60)  # simulates the warm-socket hang
        class R:  # minimal response shape
            text = ('{"score": 80, "reason": "ok", "signals": '
                    '[{"type": "hiring", "summary": "hiring spree", '
                    '"date": "2026-06-01", "url": "https://x.test"}]}')
        return R()


class _FakeClient:
    mode = "hang"

    def __init__(self, *a, **k):
        self.models = _FakeModels(_FakeClient.mode)


def main() -> int:
    orig_client = genai.Client
    genai.Client = _FakeClient
    try:
        print("\n1) hung call is guillotined at the hard cap, lead ignored")
        _FakeClient.mode = "hang"
        t0 = time.monotonic()
        v = scorer.score_company_intent(
            company_name="HungCo", domain="hungco.test", person_title=None,
            campaign_icp={}, product_context={"name": "P", "description": "d"},
        )
        dt = time.monotonic() - t0
        check("returned at ~2s, not 60s", dt < 6, f"elapsed={dt:.1f}s")
        check("gate_decision='error' (no credits, lead ignored this run)",
              v.get("gate_decision") == "error" and v.get("accepted") is False,
              str({k: v.get(k) for k in ('gate_decision', 'accepted')}))
        check("error names the hard cap", "hard wall-clock cap" in (v.get("error") or ""),
              v.get("error"))

        print("\n2) fast call is untouched by the cap")
        _FakeClient.mode = "fast"
        t0 = time.monotonic()
        v = scorer.score_company_intent(
            company_name="FastCo", domain="fastco.test", person_title=None,
            campaign_icp={}, product_context={"name": "P", "description": "d"},
        )
        dt = time.monotonic() - t0
        check("normal verdict parsed", v.get("accepted") is True
              and v.get("intent_score") == 80, str(v.get("intent_score")))
        check("no added latency", dt < 3, f"elapsed={dt:.2f}s")
    finally:
        genai.Client = orig_client

    print("\n" + ("ALL TESTS PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
