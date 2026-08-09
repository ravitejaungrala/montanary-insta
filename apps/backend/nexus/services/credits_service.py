"""Per-user credit balance: allotment, metering, monthly reset, top-ups.

Single source of truth = one `nexus_user_credits` row per FUNDING user (the
workspace owner / plan owner). Every credit-bearing operation funnels through
`consume()`, which atomically debits the balance, writes a `CreditLog` audit
row, and bumps the campaign's running total — so balance, audit, and
per-campaign spend always agree.

Mirrors Apollo: the monthly plan allotment AND purchased top-ups reset each
billing cycle (no rollover).
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import User
from nexus.models import CREDIT_COSTS, credit_allotment_for
from nexus.models_phase6 import CreditGrant, CreditLog, UserCredits

log = logging.getLogger("nexus.credits")


# ── cycle math ───────────────────────────────────────────────────────────────
def _add_month(dt: datetime) -> datetime:
    """dt + 1 month, clamping the day to the target month's last valid day."""
    year = dt.year + (dt.month // 12)
    month = (dt.month % 12) + 1
    last = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last))


def _sub_month(dt: datetime) -> datetime:
    """dt - 1 month, clamping the day to the target month's last valid day."""
    if dt.month == 1:
        year, month = dt.year - 1, 12
    else:
        year, month = dt.year, dt.month - 1
    last = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last))


def _cycle_bounds(anchor: Optional[datetime], now: datetime):
    """Return (current_cycle_start <= now, next_reset > now) by walking the
    anchor month-by-month. The anchor's day-of-month sets the reset day.

    The anchor can be a FUTURE date (e.g. subscription_end_date is the renewal
    date one year out), so we first normalise it BACKWARD until it's <= now —
    otherwise the forward walk below never runs and returns a future
    cur_start, which makes `_maybe_reset` wipe the balance on every call."""
    if not anchor:
        anchor = now
    guard = 0
    while anchor > now and guard < 1200:  # 100yr guard — pull a future anchor back
        anchor = _sub_month(anchor)
        guard += 1
    prev = anchor
    nxt = anchor
    # Walk forward until the boundary is in the future; `prev` trails one step.
    guard = 0
    while nxt <= now and guard < 1200:
        prev = nxt
        nxt = _add_month(nxt)
        guard += 1
    return prev, nxt


# ── row lifecycle ────────────────────────────────────────────────────────────
def _anchor_for(user: Optional[User], fallback: datetime) -> datetime:
    if user is not None:
        return (
            getattr(user, "subscription_end_date", None)
            or getattr(user, "created_at", None)
            or fallback
        )
    return fallback


def _maybe_reset(db: Session, row: UserCredits, user: Optional[User]) -> None:
    """Lazy monthly reset: if a new billing cycle started since last_reset_at,
    zero consumed + purchased and refresh the plan allotment. No rollover."""
    now = datetime.utcnow()
    anchor = _anchor_for(user, row.created_at or now)
    cur_start, _ = _cycle_bounds(anchor, now)
    if row.last_reset_at is None or row.last_reset_at < cur_start:
        plan = (user.pricing_plan if user else row.plan) or "free"
        row.plan = plan
        row.plan_allotment = credit_allotment_for(plan)
        row.consumed_this_cycle = 0
        row.purchased_this_cycle = 0
        row.cycle_anchor = anchor
        row.last_reset_at = now
        db.commit()


def get_or_create(db: Session, user_id: int) -> UserCredits:
    user = db.query(User).filter(User.id == user_id).first()
    row = db.query(UserCredits).filter(UserCredits.user_id == user_id).first()
    if row is None:
        now = datetime.utcnow()
        plan = (user.pricing_plan if user else None) or "free"
        anchor = _anchor_for(user, now)
        row = UserCredits(
            user_id=user_id,
            plan=plan,
            plan_allotment=credit_allotment_for(plan),
            purchased_this_cycle=0,
            consumed_this_cycle=0,
            cycle_anchor=anchor,
            # Pin created_at == last_reset_at so a fresh row never looks like it
            # just crossed a cycle boundary (which would spuriously reset it).
            created_at=now,
            last_reset_at=now,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # concurrent create won the unique(user_id)
            row = db.query(UserCredits).filter(UserCredits.user_id == user_id).first()
    else:
        _maybe_reset(db, row, user)
    return row


def resolve_credit_user_id(db: Session, workspace_id: int) -> Optional[int]:
    """The funding user for a workspace = its owner (plan owner)."""
    r = db.execute(
        text("SELECT owner_id FROM nexus_workspaces WHERE id = :w"),
        {"w": workspace_id},
    ).first()
    return int(r[0]) if r and r[0] is not None else None


def _effective_allotment(row: UserCredits) -> int:
    if row.enterprise_custom_allotment is not None:
        return int(row.enterprise_custom_allotment)
    return int(row.plan_allotment or 0)


# ── reads ────────────────────────────────────────────────────────────────────
def get_balance(db: Session, user_id: int) -> Dict[str, Any]:
    row = get_or_create(db, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    eff = _effective_allotment(row)
    unlimited = eff == -1
    if unlimited:
        available = -1
    else:
        available = max(0, eff + int(row.purchased_this_cycle or 0) - int(row.consumed_this_cycle or 0))

    now = datetime.utcnow()
    anchor = _anchor_for(user, row.cycle_anchor or now)
    _, next_reset = _cycle_bounds(anchor, now)
    days = max(0, (next_reset - now).days)

    base = eff if eff > 0 else 0
    low_threshold = max(25, int(base * 0.10))
    low = (not unlimited) and 0 < available <= low_threshold

    return {
        "plan": row.plan,
        "plan_allotment": int(row.plan_allotment or 0),
        "purchased_this_cycle": int(row.purchased_this_cycle or 0),
        "used_this_cycle": int(row.consumed_this_cycle or 0),
        "available": available,
        "unlimited": unlimited,
        "cycle_reset_at": next_reset.replace(microsecond=0).isoformat() + "Z",
        "days_until_reset": days,
        "low_credits": low,
        "out_of_credits": (not unlimited) and available == 0,
    }


def available_credits(db: Session, user_id: int) -> int:
    """Remaining credits; -1 == unlimited."""
    b = get_balance(db, user_id)
    return -1 if b["unlimited"] else int(b["available"])


# ── writes ───────────────────────────────────────────────────────────────────
def consume(
    db: Session,
    user_id: int,
    workspace_id: int,
    campaign_id: Optional[int],
    op: str,
    units: int,
) -> int:
    """Debit credits for `units` of operation `op`. Returns the number of units
    actually granted (<= units), debiting the smaller of requested vs available.
    Atomic: row-locks the balance, then writes the CreditLog + campaign total in
    the same transaction. Unlimited plans are never debited (but still logged)."""
    if units <= 0:
        return 0
    cost_per = int(CREDIT_COSTS.get(op, 1))
    get_or_create(db, user_id)  # ensure row + reset

    locked = db.execute(
        text(
            "SELECT plan_allotment, purchased_this_cycle, consumed_this_cycle, "
            "enterprise_custom_allotment FROM nexus_user_credits "
            "WHERE user_id = :u FOR UPDATE"
        ),
        {"u": user_id},
    ).mappings().first()
    if locked is None:
        return 0

    eff = (
        int(locked["enterprise_custom_allotment"])
        if locked["enterprise_custom_allotment"] is not None
        else int(locked["plan_allotment"] or 0)
    )
    if eff == -1:
        granted_units = units  # unlimited — log but don't debit
    else:
        avail = max(0, eff + int(locked["purchased_this_cycle"] or 0) - int(locked["consumed_this_cycle"] or 0))
        granted_units = min(units, avail // cost_per)
        if granted_units > 0:
            db.execute(
                text(
                    "UPDATE nexus_user_credits "
                    "SET consumed_this_cycle = consumed_this_cycle + :c, updated_at = now() "
                    "WHERE user_id = :u"
                ),
                {"c": granted_units * cost_per, "u": user_id},
            )

    if granted_units > 0:
        db.add(
            CreditLog(
                workspace_id=workspace_id,
                action_type=op,
                credits_used=granted_units * cost_per,
                successful_matches=granted_units,
                total_requested=units,
                campaign_id=campaign_id,
            )
        )
        if campaign_id:
            db.execute(
                text("UPDATE nexus_campaigns SET credits_consumed = credits_consumed + :c WHERE id = :cid"),
                {"c": granted_units * cost_per, "cid": campaign_id},
            )
    db.commit()
    return granted_units


def add_purchased(
    db: Session,
    user_id: int,
    credits: int,
    stripe_session_id: str,
    amount_cents: int = 0,
) -> bool:
    """Add purchased credits to the current cycle. Idempotent via the unique
    nexus_credit_grants.stripe_session_id — returns False if this session
    already granted credits (so webhook + confirm-session can't double-credit)."""
    if credits <= 0 or not stripe_session_id:
        return False
    if db.query(CreditGrant).filter(CreditGrant.stripe_session_id == stripe_session_id).first():
        return False
    get_or_create(db, user_id)
    db.add(
        CreditGrant(
            user_id=user_id,
            stripe_session_id=stripe_session_id,
            credits=credits,
            amount_cents=amount_cents,
        )
    )
    try:
        db.flush()  # surface the unique violation before the balance bump
    except IntegrityError:
        db.rollback()
        return False
    db.execute(
        text(
            "UPDATE nexus_user_credits "
            "SET purchased_this_cycle = purchased_this_cycle + :c, updated_at = now() "
            "WHERE user_id = :u"
        ),
        {"c": credits, "u": user_id},
    )
    db.commit()
    return True


def apply_plan_to_credits(db: Session, user: User) -> None:
    """Sync the user's monthly allotment to their current plan. Called from the
    Stripe subscription funnels after pricing_plan changes. Keeps consumed on an
    upgrade; the lazy reset self-heals the rest at the next cycle."""
    try:
        get_or_create(db, user.id)
        plan = (user.pricing_plan or "free")
        db.execute(
            text(
                "UPDATE nexus_user_credits SET plan = :p, plan_allotment = :a, updated_at = now() "
                "WHERE user_id = :u"
            ),
            {"p": plan, "a": credit_allotment_for(plan), "u": user.id},
        )
        db.commit()
    except Exception:
        log.exception("apply_plan_to_credits failed for user %s", getattr(user, "id", "?"))
        db.rollback()
