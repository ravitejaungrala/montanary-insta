from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from jose import jwt, JWTError
from core.database import get_db
from core.config import SECRET_KEY, ALGORITHM
from models import User as DBUser
from schemas import UserCreate, User
from core.auth import verify_password, create_access_token, get_password_hash
from services.email_service import email_service
from services.team_service import get_plan_owner
from services.plan_service import has_feature
from models import UserOTP
from schemas import OTPRequest, OTPVerify, PasswordChange, PasswordReset
import random
import string
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
# Non-raising variant: returns None instead of 401 "Not authenticated" when no
# token is present. Used by guards (e.g. block_user_writes) that run on PUBLIC
# GET routes too — like the OAuth provider callback, which is a browser redirect
# carrying no bearer token. Write auth is still enforced per-endpoint via
# get_nexus_user.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def _assert_login_allowed(db: Session, user: DBUser) -> None:
    """Raise HTTPException if this user should not be allowed to proceed.

    Gates:
      1. user.disabled → soft-disabled by admin or self
      2. team member whose admin's plan no longer includes the `teams`
         feature (Agency/Enterprise) → block until admin re-upgrades.
    """
    if getattr(user, "disabled", False):
        raise HTTPException(
            status_code=403,
            detail="This account has been disabled. Please contact your administrator.",
        )
    if getattr(user, "team_owner_id", None):
        owner = get_plan_owner(db, user)
        if not has_feature(owner, "teams"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Your organization's plan no longer includes team members. "
                    "Ask your administrator to upgrade to Agency or Enterprise."
                ),
            )

# Shared password-strength policy — same threshold used by /auth/change-password
# and /auth/reset-password so all password-write surfaces stay consistent.
_MIN_PASSWORD_LEN = 6


@router.post("/register", response_model=User)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    # M4 fix — normalize email to strip whitespace + lowercase so
    # 'Alice@Example.com' and '  alice@example.com ' don't create separate
    # accounts and don't collide with an existing 'alice@example.com'.
    email = (user_data.email or "").strip().lower()

    # H3 fix — enforce the same password-length policy as change/reset password.
    # Previously /auth/register accepted a 1-character password while
    # /auth/change-password required 6+. Consistency prevents "why can't I
    # change my 1-char password" support tickets.
    if not user_data.password or len(user_data.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )

    # Check if user exists (fast path — a nice 400 for the common case).
    existing = db.query(DBUser).filter(DBUser.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = DBUser(
        email=email,
        hashed_password=get_password_hash(user_data.password),
        # A self-serve signup is the owner of their own org → Master Admin.
        # (Team members are created with their granted role via the invite flow.)
        role="master_admin",
    )
    db.add(new_user)
    # H2 fix — race guard. Two concurrent /auth/register calls could both pass
    # the existence check above and reach db.commit(). The unique index on
    # users.email fires on ONE of them with IntegrityError — catch it here so
    # the loser gets a clean 400, not a raw 500.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    db.refresh(new_user)
    return new_user

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    # Gate stale tokens for disabled/downgraded users so a pre-existing
    # JWT can't be used after the admin revoked access.
    _assert_login_allowed(db, user)
    return user


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[DBUser]:
    """Like get_current_user, but returns None instead of raising when there's
    no / an invalid token. For guards that also run on public routes (e.g. the
    OAuth callback redirect, which carries no bearer token)."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
    except JWTError:
        return None
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user is None:
        return None
    try:
        _assert_login_allowed(db, user)
    except HTTPException:
        return None
    return user


def require_role(*roles: str):
    """Dependency factory: allow only users whose (normalized) role is one of
    `roles`. Use as `dependencies=[Depends(require_role("master_admin"))]` or
    inject the user: `user = Depends(require_role(*WRITE_ROLES))`."""
    allowed = set(roles)

    def _guard(current_user: DBUser = Depends(get_current_user)) -> DBUser:
        from core.roles import normalize_role
        current = normalize_role(getattr(current_user, "role", None))
        if current not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Forbidden: insufficient role",
                    "required": sorted(allowed),
                    "current": current,
                },
            )
        return current_user

    return _guard


def require_can(action: str):
    """Dependency factory: allow only users whose role may perform `action`
    (see core.roles.PERMISSIONS)."""

    def _guard(current_user: DBUser = Depends(get_current_user)) -> DBUser:
        from core.roles import can, normalize_role
        if not can(getattr(current_user, "role", None), action):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Forbidden",
                    "action": action,
                    "current": normalize_role(getattr(current_user, "role", None)),
                },
            )
        return current_user

    return _guard


def block_user_writes(
    request: Request,
    current_user: Optional[DBUser] = Depends(get_current_user_optional),
) -> Optional[DBUser]:
    """Router-level guard: a read-only USER may not perform any write.

    Blocks non-GET methods (POST/PATCH/PUT/DELETE) when the caller's canonical
    `users.role` is `user`; Master Admin + Admin pass, and ALL reads pass for
    everyone. Apply once per write-heavy router via
    `APIRouter(dependencies=[Depends(block_user_writes)])`.

    Uses OPTIONAL auth so it NEVER forces a login on safe/public routes (e.g.
    the OAuth provider callback, a browser redirect with no bearer token) — it
    previously 401'd those with "Not authenticated". Real auth is still enforced
    on every write endpoint via get_nexus_user; this guard only ADDS the
    read-only-role block, which can only apply when a user is actually present."""
    from core.roles import USER as _USER, normalize_role
    if request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
        if current_user is not None and normalize_role(getattr(current_user, "role", None)) == _USER:
            raise HTTPException(
                status_code=403,
                detail={"error": "Forbidden: your role is view-only", "current": _USER},
            )
    return current_user


def generate_otp():
    return "".join(random.choices(string.digits, k=6))


# ═════════════════════════════════════════════════════════════════════
# H3 + L3 fix — Failed-auth rate limiter
# ═════════════════════════════════════════════════════════════════════
# Simple in-memory per-email throttle for password/OTP guess attempts.
# Prevents scripted brute-forcing of /auth/login, /auth/verify-otp,
# and /auth/reset-password.
#
# Design notes:
#   • Failed attempts (bad password, bad OTP, unknown email) count.
#   • Successful attempts DO NOT count and RESET the counter for that
#     (email, action) pair — so honest users don't get locked out after
#     a few typos followed by a correct entry.
#   • Threshold: 10 failed attempts per email per hour, per action.
#   • TODO(prod): move to Redis so the counter survives worker restarts
#     and works across horizontally-scaled instances. In-memory is
#     acceptable for single-worker local/staging.
from collections import defaultdict
from threading import Lock as _Lock

_FAILED_AUTH_ATTEMPTS: dict[tuple[str, str], list[datetime]] = defaultdict(list)
_FAILED_AUTH_LOCK = _Lock()
_MAX_FAILED_ATTEMPTS_PER_HOUR = 10


def _check_auth_rate_limit(email: str, action: str) -> None:
    """Raise 429 if too many recent failed attempts for this (email, action)."""
    key = ((email or "").strip().lower(), action)
    cutoff = datetime.utcnow() - timedelta(hours=1)
    with _FAILED_AUTH_LOCK:
        recent = [t for t in _FAILED_AUTH_ATTEMPTS[key] if t > cutoff]
        _FAILED_AUTH_ATTEMPTS[key] = recent
        if len(recent) >= _MAX_FAILED_ATTEMPTS_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many failed attempts for this account. "
                    "Try again in an hour or use Forgot Password."
                ),
            )


def _record_failed_auth(email: str, action: str) -> None:
    """Record a failed auth attempt in the rolling window."""
    key = ((email or "").strip().lower(), action)
    with _FAILED_AUTH_LOCK:
        _FAILED_AUTH_ATTEMPTS[key].append(datetime.utcnow())


def _clear_failed_auth(email: str, action: str) -> None:
    """Reset the counter after a successful attempt."""
    key = ((email or "").strip().lower(), action)
    with _FAILED_AUTH_LOCK:
        _FAILED_AUTH_ATTEMPTS.pop(key, None)


# Server-side OTP throttle — hard cap of N requests per email per hour.
# Frontend already enforces a 30s cooldown per SignUpCard, but that lives
# only in the client. This backend guard protects against scripted callers,
# distributed frontends, and email-provider abuse.
_MAX_OTP_REQUESTS_PER_HOUR = 5


@router.post("/request-otp")
async def request_otp(data: OTPRequest, db: Session = Depends(get_db)):
    # M4 fix — normalize email so a case-shifted address doesn't get its own
    # separate OTP row + email send. Every downstream lookup uses this same
    # normalized form.
    email = (data.email or "").strip().lower()

    # Check if user is trying to register but already exists
    if data.purpose == "register":
        existing = db.query(DBUser).filter(DBUser.email == email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

    # M2 fix — server-side rate limit. Count all OTP requests for this email
    # in the last hour (any purpose — an attacker who wants to spam an inbox
    # doesn't care about the purpose column). 429 if exceeded so the frontend
    # can show a clear cooldown message.
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_count = (
        db.query(UserOTP)
        .filter(UserOTP.email == email, UserOTP.created_at > one_hour_ago)
        .count()
    )
    if recent_count >= _MAX_OTP_REQUESTS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many OTP requests for this email. "
                f"Try again in an hour."
            ),
        )

    # M1 fix — invalidate previous unused OTPs for the SAME (email, purpose)
    # before minting a new one. Otherwise the user_otps table grows unboundedly
    # and multiple concurrently-valid codes multiply the chance of a brute-
    # force guess. Marking as used (not deleting) preserves the audit trail.
    db.query(UserOTP).filter(
        UserOTP.email == email,
        UserOTP.purpose == data.purpose,
        UserOTP.is_used == False,   # noqa: E712 (SQLAlchemy needs `== False`)
    ).update({UserOTP.is_used: True})

    # 1. Generate OTP (fresh — no reuse of old codes even if the previous email
    # never landed).
    otp_code = generate_otp()

    # 2. Store OTP
    otp_entry = UserOTP(
        email=email,
        otp_code=otp_code,
        purpose=data.purpose,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        is_used=False
    )
    db.add(otp_entry)
    db.commit()

    # 3. Send via Azure
    success = email_service.send_otp_email(email, otp_code, data.purpose)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send OTP email. Please try again later.")

    return {"message": f"OTP sent to {email}"}

@router.post("/verify-otp")
async def verify_otp(data: OTPVerify, db: Session = Depends(get_db)):
    # M4 fix — normalize the incoming email the same way /auth/request-otp
    # does so the lookup below matches the row that was actually stored.
    email = (data.email or "").strip().lower()

    # L3 fix — rate limit OTP guesses. 10 failed guesses per (email, purpose)
    # per hour blocks brute-force of the 6-digit code space.
    _check_auth_rate_limit(email, f"verify_otp:{data.purpose}")

    # 1. Check most recent OTP
    otp_entry = db.query(UserOTP).filter(
        UserOTP.email == email,
        UserOTP.otp_code == data.code,
        UserOTP.purpose == data.purpose,
        UserOTP.is_used == False,
        UserOTP.expires_at > datetime.utcnow()
    ).order_by(UserOTP.created_at.desc()).first()

    if not otp_entry:
        _record_failed_auth(email, f"verify_otp:{data.purpose}")
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    _clear_failed_auth(email, f"verify_otp:{data.purpose}")

    # 2. Mark as used — except for reset_password, where the code is validated
    # here only as a gating check; the FINAL consumer is /auth/reset-password
    # (which needs to see an unused OTP to then update the password and mark
    # the row used atomically). Two-step forgot-password UX would otherwise
    # break with "code already used" on the reset submit.
    if data.purpose != "reset_password":
        otp_entry.is_used = True
        db.commit()
    
    # 3. Handle Login/Register
    if data.purpose in ["login", "register"]:
        user = db.query(DBUser).filter(DBUser.email == email).first()

        # If user exists, return full token
        if user:
            _assert_login_allowed(db, user)
            access_token = create_access_token(data={"sub": user.email})
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "is_new_user": False
            }
        else:
            # If new user and purpose is register, return a signal
            return {
                "message": "OTP verified successfully. Proceed to account creation.",
                "is_new_user": True,
                "email": email
            }
            
    return {"message": "OTP verified successfully"}

@router.post("/change-password")
async def change_password(data: PasswordChange, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    # Minimum password length — same threshold as /auth/reset-password so the
    # two password-change surfaces stay consistent. 6 is modest but keeps
    # literal-1-char passwords out of the hashed_password column.
    if not data.new_password or len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    # 1. If OTP is provided, verify it
    if data.otp_code:
        otp_entry = db.query(UserOTP).filter(
            UserOTP.email == current_user.email,
            UserOTP.otp_code == data.otp_code,
            UserOTP.purpose == "change_password",
            UserOTP.is_used == False,
            UserOTP.expires_at > datetime.utcnow()
        ).order_by(UserOTP.created_at.desc()).first()
        
        if not otp_entry:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP code")
        
        # Mark as used
        otp_entry.is_used = True
        db.commit()
    elif data.old_password:
        # 2. Fallback to old password if no OTP provided
        if not verify_password(data.old_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect old password")
    else:
        raise HTTPException(status_code=400, detail="OTP code or current password required")
    
    # 3. Update password
    current_user.hashed_password = get_password_hash(data.new_password)
    db.commit()

    return {"message": "Password changed successfully"}


@router.post("/reset-password")
async def reset_password(data: PasswordReset, db: Session = Depends(get_db)):
    """
    Unauthenticated password reset (forgot-password flow).

    The caller must present a valid, unused OTP with purpose='reset_password'
    issued to the target email — that code was delivered via Microsoft Graph
    to the mailbox owner, so only someone with access to the email can use it.

    The OTP is marked is_used=True on success so the same code cannot be
    replayed. Basic new-password length check guards against trivial inputs.
    """
    if not data.new_password or len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    # H2 fix — normalize email so a case-shifted address still matches the
    # account row + the OTP row (both stored lowercase after our M4 fix).
    email = (data.email or "").strip().lower()

    # L3 fix — rate limit reset attempts so an attacker can't brute-force
    # the 6-digit code by scripting POSTs to this endpoint.
    _check_auth_rate_limit(email, "reset_password")

    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user:
        _record_failed_auth(email, "reset_password")
        # Generic error — don't leak which emails exist in the database.
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    # M2 fix — a disabled/downgraded user shouldn't be able to reset their
    # password (they can't log in with the new one anyway — better to fail
    # here with a clear message than let them succeed and then bounce).
    try:
        _assert_login_allowed(db, user)
    except HTTPException:
        raise

    otp_entry = db.query(UserOTP).filter(
        UserOTP.email == email,
        UserOTP.otp_code == data.otp_code,
        UserOTP.purpose == "reset_password",
        UserOTP.is_used == False,
        UserOTP.expires_at > datetime.utcnow(),
    ).order_by(UserOTP.created_at.desc()).first()

    if not otp_entry:
        _record_failed_auth(email, "reset_password")
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    # Consume THIS OTP.
    otp_entry.is_used = True
    # M3 fix (defensive) — also invalidate any OTHER unused reset_password
    # OTPs for this email so the newly-set password can't be reset AGAIN
    # by any other still-valid code from an earlier request.
    db.query(UserOTP).filter(
        UserOTP.email == email,
        UserOTP.purpose == "reset_password",
        UserOTP.is_used == False,
        UserOTP.id != otp_entry.id,
    ).update({UserOTP.is_used: True})
    # Update password.
    user.hashed_password = get_password_hash(data.new_password)
    db.commit()

    # Clear the failed-auth counter so the user isn't rate-limited on their
    # very next login with the new password.
    _clear_failed_auth(email, "reset_password")
    _clear_failed_auth(email, "login")

    # Issue a fresh token so the frontend can auto-login the user after reset.
    access_token = create_access_token(data={"sub": user.email})
    return {
        "message": "Password reset successfully",
        "access_token": access_token,
        "token_type": "bearer",
    }


@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # H1 fix — normalize email the same way /auth/register and
    # /auth/request-otp do, so 'Alice@Example.com' logs into the account
    # stored as 'alice@example.com'.
    email = (form_data.username or "").strip().lower()

    # H3 fix — reject if this email has 10+ failed logins in the last hour.
    _check_auth_rate_limit(email, "login")

    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        _record_failed_auth(email, "login")
        # Generic error — don't leak whether the email exists.
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    try:
        _assert_login_allowed(db, user)
    except HTTPException:
        # Disabled/downgraded users hit their own gate; don't count that as
        # a failed guess (their credentials were actually correct).
        raise

    _clear_failed_auth(email, "login")
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}
