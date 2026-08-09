"""Conversion snapshotter — capture lead-conversion journey snapshots.

Legacy: apps/nexus-legacy/server/services/conversionSnapshotter.js
Exports (legacy): captureConversionSnapshot
"""
from __future__ import annotations
from typing import Any, Dict


async def capture_conversion_snapshot(
    lead: Dict[str, Any],
    outreach: Dict[str, Any] | None = None,
    campaign: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Legacy: captureConversionSnapshot({ lead, outreach, campaign })
    Writes a row to nexus_conversion_snapshots."""
    return {"ok": False, "stub": True}
