"""LLM token-usage tracker.

Legacy: apps/nexus-legacy/server/services/tokenTracker.js
Exports (legacy): estimateTokens, trackTokenUsage
"""
from __future__ import annotations
from typing import Any, Dict, Optional


def estimate_tokens(text: str) -> int:
    """Legacy: estimateTokens(text). ~4 chars per token heuristic."""
    return max(1, (len(text or "") + 3) // 4)


async def track_token_usage(
    workspace_id: int,
    user_id: int,
    operation: str,
    tokens_used: int,
    campaign_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Legacy: trackTokenUsage(workspaceId, userId, operation, tokensUsed, campaignId).
    Writes a nexus_token_usage row."""
    return {"ok": False, "stub": True}
