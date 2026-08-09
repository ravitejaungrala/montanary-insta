"""In-memory store — dev/test cache layer.

Legacy: apps/nexus-legacy/server/services/memStore.js
Exports (legacy): createCampaign, findCampaignById, insertLeads,
                  findLeadsByCampaign, findLeadById, updateLead,
                  bulkUpdateLeads, reset

LEGACY ROLE: A pre-Mongo prototype shim. Production uses Postgres
directly; this stub is kept ONLY so any legacy import path still
resolves. Calls should be migrated to direct SQL.
"""
from __future__ import annotations
from typing import Any, Dict, List


_campaigns: Dict[str, Dict[str, Any]] = {}
_leads: Dict[str, Dict[str, Any]] = {}


def create_campaign(data: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(data.get("id") or len(_campaigns) + 1)
    row = {"id": cid, **data}
    _campaigns[cid] = row
    return row


def find_campaign_by_id(cid: str | int) -> Dict[str, Any] | None:
    return _campaigns.get(str(cid))


def insert_leads(leads_arr: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for l in leads_arr:
        lid = str(l.get("id") or len(_leads) + 1)
        row = {"id": lid, **l}
        _leads[lid] = row
        out.append(row)
    return out


def find_leads_by_campaign(campaign_id: str | int) -> List[Dict[str, Any]]:
    return [l for l in _leads.values() if str(l.get("campaign_id")) == str(campaign_id)]


def find_lead_by_id(lid: str | int) -> Dict[str, Any] | None:
    return _leads.get(str(lid))


def update_lead(lid: str | int, updates: Dict[str, Any]) -> Dict[str, Any] | None:
    row = _leads.get(str(lid))
    if not row:
        return None
    row.update(updates)
    return row


def bulk_update_leads(ids: List[str | int], updates: Dict[str, Any]) -> int:
    n = 0
    for lid in ids:
        if update_lead(lid, updates):
            n += 1
    return n


def reset() -> None:
    _campaigns.clear()
    _leads.clear()
