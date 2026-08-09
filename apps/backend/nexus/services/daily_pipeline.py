"""Daily-pipeline orchestrator.

Legacy: apps/nexus-legacy/server/services/dailyPipeline.js
Exports (legacy): runDailyPipeline

Runs nightly via EventBridge → /nexus/scheduler/run-daily-pipeline.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


async def run_daily_pipeline(user_id_filter: Optional[int] = None) -> Dict[str, Any]:
    """Legacy: runDailyPipeline({ user_id_filter = null }).
    Iterates every active workspace, runs autonomous discovery +
    enrichment for any campaigns flagged daily."""
    return {"ok": False, "stub": True, "workspaces_processed": 0}
