"""EventBridge-triggered weekly dashboard-stats report endpoint.

Requires the `X-Scheduler-Secret` header to match `core.config.SCHEDULER_SECRET`
— same guard pattern used by nexus/routers/scheduler.py. Intended to be poked
by lambda_pinger.py's Monday-morning heartbeat check.

Named distinctly from nexus/routers/reports.py (Nexus CSV/PDF workspace
exports) to avoid confusion between the two unrelated "reports" modules.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from core.config import SCHEDULER_SECRET
from core.database import get_db
from services.weekly_report import run_weekly_reports

logger = logging.getLogger("pipelyt.reports")

router = APIRouter()


def _verify_secret(x_scheduler_secret: str = Header(None)):
    if not SCHEDULER_SECRET or x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/scheduler/weekly-report", dependencies=[Depends(_verify_secret)])
async def weekly_report_trigger(dry_run: bool = False, db: Session = Depends(get_db)):
    """Triggered by AWS EventBridge (via lambda_pinger.py) every Monday
    ~9:00am IST. Sends the weekly dashboard-stats digest to every eligible
    user. `?dry_run=true` computes stats without sending or marking users
    as reported — safe for manual verification."""
    logger.info(f"Weekly report trigger fired (dry_run={dry_run})")
    result = await run_weekly_reports(db, dry_run=dry_run)
    return result
