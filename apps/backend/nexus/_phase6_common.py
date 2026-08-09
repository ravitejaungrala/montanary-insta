"""Shared helpers for Phase 6 routers.

Phase 1 deps (`get_nexus_user`, `require_current_workspace`, `trial_guard`,
`check_limit`, `increment_usage`) are owned by another agent. We import them
when available; otherwise fall back to a permissive shim that reuses PIPELYT
JWT auth so this phase is independently importable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger("pipelyt.nexus.phase6")


# ─── Phase 1 deps (defensive import) ─────────────────────────────────────────


def _make_fallback_get_nexus_user() -> Callable[..., Any]:
    """Falls back to PIPELYT's plain get_current_user when nexus.deps is unavailable.

    Returns a callable that yields a dict {id, email, workspace_id} consistent
    with what Phase 1's get_nexus_user is expected to deliver.
    """
    try:
        from routers.auth import get_current_user  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.warning("PIPELYT routers.auth.get_current_user unavailable: %s", e)

        def _hard_fail():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth dependency unavailable",
            )

        return _hard_fail

    def shim(current=Depends(get_current_user)):
        return current

    return shim


try:
    from nexus.deps import (  # type: ignore  # noqa: F401
        get_nexus_user,
        require_current_workspace,
        trial_guard,
        check_limit,
        increment_usage,
    )
except Exception:  # noqa: BLE001
    get_nexus_user = _make_fallback_get_nexus_user()  # type: ignore

    def require_current_workspace(user=Depends(get_nexus_user)):  # type: ignore
        """Fallback: return a minimal workspace dict using the user's id."""
        if isinstance(user, dict):
            return {"id": user.get("workspace_id") or user.get("id"), "user_id": user.get("id")}
        wid = getattr(user, "workspace_id", None) or getattr(user, "id", None)
        return {"id": wid, "user_id": getattr(user, "id", None)}

    def trial_guard(workspace=Depends(require_current_workspace)):  # type: ignore
        return workspace

    def check_limit(_action: str):  # type: ignore
        def _dep(workspace=Depends(require_current_workspace)):
            return workspace

        return _dep

    def increment_usage(_action: str, _amount: int = 1):  # type: ignore
        def _dep(workspace=Depends(require_current_workspace)):
            return workspace

        return _dep


# ─── Scheduler secret guard ──────────────────────────────────────────────────


def require_scheduler_secret(x_scheduler_secret: Optional[str] = Header(None)) -> None:
    """Block any caller that doesn't present the configured SCHEDULER_SECRET."""
    try:
        from core.config import SCHEDULER_SECRET  # type: ignore
    except Exception:
        SCHEDULER_SECRET = None  # type: ignore

    if not SCHEDULER_SECRET:
        # Misconfiguration safer than open endpoint.
        raise HTTPException(status_code=503, detail="Scheduler secret not configured")
    if x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid scheduler secret")


# ─── Workspace ID extraction ─────────────────────────────────────────────────


def extract_workspace_id(workspace: Any) -> Optional[int]:
    """Pull the workspace ID off whatever Phase 1 hands us."""
    if workspace is None:
        return None
    if isinstance(workspace, dict):
        wid = workspace.get("id") or workspace.get("workspace_id")
    else:
        wid = getattr(workspace, "id", None) or getattr(workspace, "workspace_id", None)
    try:
        return int(wid) if wid is not None else None
    except Exception:  # noqa: BLE001
        return None


def extract_user_id(user: Any) -> Optional[int]:
    if user is None:
        return None
    if isinstance(user, dict):
        uid = user.get("id")
    else:
        uid = getattr(user, "id", None)
    try:
        return int(uid) if uid is not None else None
    except Exception:  # noqa: BLE001
        return None
