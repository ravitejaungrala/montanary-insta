"""Apollo.io raw API client — cache + credit-log wrappers.

Legacy: apps/nexus-legacy/server/services/apolloClient.js
Exports (legacy): apolloRequest, checkApolloCache, writeApolloCache,
                  writeCreditLog, normalizeDomain

NOTE: nexus/services/discovery_apollo.py contains the higher-level
discovery flow. This module preserves the raw-API wrapper exports.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


def normalize_domain(value: str) -> str:
    """Legacy: normalizeDomain(value). Strip protocol, path, www."""
    if not value:
        return ""
    s = value.lower().strip()
    s = s.replace("https://", "").replace("http://", "")
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    return s


async def apollo_request(
    method: str, path: str, body: Dict[str, Any] | None = None, label: str = "apollo"
) -> Dict[str, Any]:
    """Legacy: apolloRequest(method, path, body, label)."""
    return {"ok": False, "stub": True}


async def check_apollo_cache(
    apollo_person_id: str, workspace_id: int
) -> Optional[Dict[str, Any]]:
    """Legacy: checkApolloCache(apolloPersonId, workspaceId).
    Reads nexus_apollo_lead_cache."""
    return None


async def write_apollo_cache(data: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: writeApolloCache(data). Writes nexus_apollo_lead_cache row."""
    return {"ok": False, "stub": True}


async def write_credit_log(
    workspace_id: int,
    endpoint: str,
    people_count: int,
    successful_matches: int,
    campaign_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Legacy: writeCreditLog({...}). Appends nexus_credit_logs row."""
    return {"ok": False, "stub": True}
