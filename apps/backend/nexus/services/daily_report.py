"""Daily-report emailer.

Legacy: apps/nexus-legacy/server/services/dailyReport.js
Exports (legacy): aggregateUserMetrics, renderHtml, sendReportForUser,
                  runDailyReport

Fires once per day (or per the user's `nexus_user_profiles.daily_report_time`)
and emails each opted-in user a digest of their previous day's metrics.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional


async def aggregate_user_metrics(
    user_id: int, since: datetime, until: datetime
) -> Dict[str, Any]:
    """Legacy: aggregateUserMetrics(user_id, since, until)."""
    return {
        "leads_added": 0,
        "emails_sent": 0,
        "replies": 0,
        "demos": 0,
        "tokens_used": 0,
    }


def render_html(user: Dict[str, Any], metrics: Dict[str, Any], date_label: str) -> str:
    """Legacy: renderHtml({ user, metrics, dateLabel })."""
    return ""


async def send_report_for_user(
    user: Dict[str, Any], since: Optional[datetime] = None, until: Optional[datetime] = None
) -> Dict[str, Any]:
    """Legacy: sendReportForUser(user, { since, until })."""
    return {"ok": False, "stub": True}


async def run_daily_report(
    since: Optional[datetime] = None, until: Optional[datetime] = None
) -> Dict[str, Any]:
    """Legacy: runDailyReport({ since, until }). Cron entry point."""
    return {"ok": False, "stub": True, "users_processed": 0}
