import stripe
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Body, Request, Header
from core import config
from routers.auth import get_current_user, require_role
from core.roles import MASTER_ADMIN
from models import User
from core.database import SessionLocal, get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/payment", tags=["payment"])
logger = logging.getLogger("pipelyt.payment")

stripe.api_key = config.STRIPE_SECRET_KEY


# One-time credit packs defined IN CODE (no pre-created Stripe Price IDs / env
# vars): credits -> price in cents. Server-side source of truth — the client's
# requested pack is validated against this; credits/amounts are never taken from
# client input. Stripe charges this amount via inline `price_data`.
CREDIT_PACKS = {
    500: 1000,     # $10
    1000: 2500,    # $25
    2500: 5000,    # $50
    5000: 10000,   # $100
    10000: 18000,  # $180
}


def _session_kind(obj) -> str:
    """Read metadata.kind off a Stripe session/object (normalizing StripeObject)."""
    d = obj.to_dict() if hasattr(obj, "to_dict") else obj
    return ((d.get("metadata") or {}) if isinstance(d, dict) else {}).get("kind") or ""


def _apply_credit_purchase(db: Session, session) -> bool:
    """Grant purchased credits from a completed one-time checkout session.

    Idempotent via credits_service.add_purchased (UNIQUE stripe_session_id), so
    the webhook AND the success-page confirm can both call this and credits are
    added exactly once. Returns True if this call performed the grant."""
    if hasattr(session, "to_dict"):
        session = session.to_dict()
    if session.get("payment_status") != "paid":
        return False
    meta = session.get("metadata") or {}
    if meta.get("kind") != "credit_purchase":
        return False
    user_id = meta.get("user_id")
    try:
        credits = int(meta.get("credits") or 0)
    except (TypeError, ValueError):
        credits = 0
    if not user_id or credits <= 0:
        return False
    amount_cents = int(session.get("amount_total") or 0)
    try:
        from nexus.services.credits_service import add_purchased
        granted = add_purchased(
            db, int(user_id), credits, str(session.get("id")), amount_cents
        )
        if granted:
            logger.info(
                "Credits granted: user=%s +%d credits (session %s).",
                user_id, credits, session.get("id"),
            )
        return granted
    except Exception:
        logger.exception("Credit-purchase grant failed for session %s", session.get("id"))
        return False


def _apply_paid_session_to_user(db: Session, session) -> User | None:
    """
    Idempotently flips a user to their paid state based on a Stripe Checkout
    Session object. Returns the updated User row, or None if the session can't
    be tied to a user.

    Used by BOTH the webhook (fires async, primary path in prod) AND the
    /confirm-session endpoint (fires on the success-page redirect, backup path
    that lets us survive dropped webhooks or localhost dev where Stripe can't
    reach us).

    Safe to call multiple times — we only re-read Stripe state and apply the
    same assignments, so idempotency is a property of the underlying ops.
    """
    # Stripe SDK >=8 returns StripeObject instances whose .get() method is
    # shadowed by __getattr__-based key lookup and raises AttributeError for
    # any missing key — so .get() does NOT behave like dict.get. Normalize
    # to a plain dict before any lookups. Accepts both StripeObject (from
    # stripe.Session.retrieve / webhook event['data']['object']) and plain
    # dict inputs (tests).
    if hasattr(session, "to_dict"):
        session = session.to_dict()

    user_id = (session.get("metadata") or {}).get("user_id")
    plan_identifier = (session.get("metadata") or {}).get("plan")
    payment_status = session.get("payment_status")

    if payment_status != "paid":
        logger.info(
            f"Skipping paid-session apply: payment_status={payment_status!r} "
            f"session={session.get('id')}"
        )
        return None

    if not user_id:
        logger.warning(
            f"Stripe session {session.get('id')} has no user_id in metadata — cannot apply."
        )
        return None

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        logger.warning(f"User ID {user_id} from session metadata not in DB.")
        return None

    # Capture the Stripe customer id on first paid session so future portal /
    # webhook lookups don't have to rely on email (which can collide across
    # account resets). Only set if empty — never overwrite an established link.
    session_customer_id = session.get("customer")
    if session_customer_id and not getattr(user, "stripe_customer_id", None):
        user.stripe_customer_id = session_customer_id

    # Plan normalization: "pro_yearly" -> "growth", "basic_monthly" -> "starter".
    raw_plan = plan_identifier.split("_")[0] if plan_identifier else (user.pricing_plan or "growth")
    plan_map = {"basic": "starter", "pro": "growth"}
    clean_plan = plan_map.get(raw_plan.lower(), raw_plan.lower())
    user.pricing_plan = clean_plan
    user.onboarded = True

    subscription_id = session.get("subscription")
    if subscription_id:
        try:
            from datetime import datetime
            sub = stripe.Subscription.retrieve(subscription_id)
            sub_d = sub.to_dict() if hasattr(sub, "to_dict") else sub
            # Stripe API 2025+ moved `current_period_end` from the
            # Subscription root onto each subscription item. Try the
            # item-level value first; fall back to the root value for
            # back-compat with older API versions.
            period_end = None
            items = (sub_d.get("items") or {}).get("data") or []
            if items:
                period_end = items[0].get("current_period_end")
            if not period_end:
                period_end = sub_d.get("current_period_end")
            if period_end:
                user.subscription_end_date = datetime.fromtimestamp(int(period_end))
            else:
                logger.warning(
                    f"Subscription {subscription_id} returned no current_period_end "
                    f"on root or items[0] — leaving subscription_end_date null."
                )
        except Exception as sub_err:
            logger.warning(f"Could not sync subscription end date: {sub_err}")

    db.commit()
    db.refresh(user)
    try:
        from nexus.services.credits_service import apply_plan_to_credits
        apply_plan_to_credits(db, user)
    except Exception:
        logger.warning("apply_plan_to_credits failed (non-fatal)", exc_info=True)
    logger.info(f"User {user.email} marked paid (plan={clean_plan}) via session {session.get('id')}.")
    return user


def _price_id_to_plan_name(price_id: str) -> str | None:
    """Reverse map: Stripe price ID -> internal plan name (starter/growth/agency/enterprise).
    Used by subscription.updated webhooks where we only get the price id back."""
    if not price_id:
        return None
    table = {
        config.STRIPE_BASIC_MONTHLY_ID:      "starter",
        config.STRIPE_BASIC_YEARLY_ID:       "starter",
        config.STRIPE_PRO_MONTHLY_ID:        "growth",
        config.STRIPE_PRO_YEARLY_ID:         "growth",
        config.STRIPE_AGENCY_MONTHLY_ID:     "agency",
        config.STRIPE_AGENCY_YEARLY_ID:      "agency",
        config.STRIPE_ENTERPRISE_MONTHLY_ID: "enterprise",
        config.STRIPE_ENTERPRISE_YEARLY_ID:  "enterprise",
    }
    return table.get(price_id)


def _user_for_subscription(db: Session, sub) -> User | None:
    """Resolve a Subscription -> User row.
    Order: subscription.metadata.user_id  →  stripe_customer_id column
    →  customer email lookup (last-resort fallback)."""
    if hasattr(sub, "to_dict"):
        sub = sub.to_dict()
    user_id = (sub.get("metadata") or {}).get("user_id")
    if user_id:
        u = db.query(User).filter(User.id == int(user_id)).first()
        if u:
            return u
    customer_id = sub.get("customer")
    if customer_id:
        # Prefer the stored stripe_customer_id link — set on first paid
        # session, immune to email changes or collisions.
        u = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if u:
            return u
        try:
            cust = stripe.Customer.retrieve(customer_id)
            email = cust.get("email") if isinstance(cust, dict) else getattr(cust, "email", None)
            if email:
                return db.query(User).filter(User.email == email).first()
        except Exception as e:
            logger.warning(f"Could not resolve customer {customer_id}: {e}")
    return None


def _apply_subscription_updated(db: Session, sub) -> None:
    """customer.subscription.updated — fires on plan switch in the Customer Portal,
    on renewal, on payment-method change, etc. We only act on the plan-change case
    (price id moved to a different tier) and on cancel-at-period-end toggles."""
    if hasattr(sub, "to_dict"):
        sub = sub.to_dict()
    user = _user_for_subscription(db, sub)
    if not user:
        logger.warning(f"subscription.updated: could not map sub {sub.get('id')} to a user")
        return

    items = (sub.get("items") or {}).get("data") or []
    if not items:
        logger.warning(f"subscription.updated: sub {sub.get('id')} has no items")
        return
    price_id = ((items[0].get("price") or {}).get("id"))
    new_plan = _price_id_to_plan_name(price_id)

    status = sub.get("status")  # 'active', 'past_due', 'canceled', etc.
    cancel_at_period_end = bool(sub.get("cancel_at_period_end"))

    if new_plan and status in ("active", "trialing", "past_due"):
        if user.pricing_plan != new_plan:
            logger.info(f"User {user.email}: plan {user.pricing_plan} -> {new_plan} via portal")
        user.pricing_plan = new_plan

    # Track period end so the UI can show "renews on …" or "ends on …".
    # Stripe API 2025+: current_period_end lives on the subscription ITEM,
    # not the subscription root. Fall back to root for older API versions.
    period_end = (items[0].get("current_period_end") if items else None) or sub.get("current_period_end")
    if period_end:
        try:
            from datetime import datetime
            user.subscription_end_date = datetime.fromtimestamp(int(period_end))
        except Exception:
            pass

    # If the user clicked "Cancel" in the portal, Stripe leaves them active
    # until period end — do NOT downgrade yet, the .deleted event handles that.
    # Persist the flag so the UI can switch the label from "Next Billing" to
    # "Cancels on …" without dropping their plan early.
    user.subscription_cancel_at_period_end = bool(cancel_at_period_end)
    if cancel_at_period_end:
        logger.info(f"User {user.email}: subscription set to cancel at period end")

    db.commit()
    try:
        from nexus.services.credits_service import apply_plan_to_credits
        apply_plan_to_credits(db, user)
    except Exception:
        logger.warning("apply_plan_to_credits failed (non-fatal)", exc_info=True)


def _apply_subscription_deleted(db: Session, sub) -> None:
    """customer.subscription.deleted — fires when the cancellation period elapses
    or the user's payment fails permanently. We drop them back to the free tier."""
    if hasattr(sub, "to_dict"):
        sub = sub.to_dict()
    user = _user_for_subscription(db, sub)
    if not user:
        logger.warning(f"subscription.deleted: could not map sub {sub.get('id')} to a user")
        return
    logger.info(f"User {user.email}: plan {user.pricing_plan} -> free (subscription deleted)")
    user.pricing_plan = "free"
    user.subscription_end_date = None
    user.subscription_cancel_at_period_end = False
    db.commit()
    try:
        from nexus.services.credits_service import apply_plan_to_credits
        apply_plan_to_credits(db, user)
    except Exception:
        logger.warning("apply_plan_to_credits failed (non-fatal)", exc_info=True)


@router.post("/create-checkout-session")
async def create_checkout_session(
    plan: str = Body(..., embed=True),
    promotion_code: str = Body(None, embed=True),
    current_user: User = Depends(require_role(MASTER_ADMIN))  # plan change = Master Admin only
):
    logger.info(
        f"Creating checkout session for user {current_user.email} (ID: {current_user.id}) - "
        f"Plan: {plan}, PromoCode: {promotion_code or 'none'}"
    )
    try:
        # Map plan to Price IDs from environment variables
        price_mapping = {
            "starter_monthly": config.STRIPE_BASIC_MONTHLY_ID,
            "starter_yearly": config.STRIPE_BASIC_YEARLY_ID,
            "growth_monthly": config.STRIPE_PRO_MONTHLY_ID,
            "growth_yearly": config.STRIPE_PRO_YEARLY_ID,
            "agency_monthly": getattr(config, "STRIPE_AGENCY_MONTHLY_ID", None),
            "agency_yearly": getattr(config, "STRIPE_AGENCY_YEARLY_ID", None),
            "enterprise_monthly": config.STRIPE_ENTERPRISE_MONTHLY_ID,
            "enterprise_yearly": config.STRIPE_ENTERPRISE_YEARLY_ID,
            # Back-compat for old plan names if still coming from frontend
            "basic_monthly": config.STRIPE_BASIC_MONTHLY_ID,
            "basic_yearly": config.STRIPE_BASIC_YEARLY_ID,
            "pro_monthly": config.STRIPE_PRO_MONTHLY_ID,
            "pro_yearly": config.STRIPE_PRO_YEARLY_ID,
        }

        price_id = price_mapping.get(plan)
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Invalid plan: {plan}")

        # Build session kwargs. Stripe's API is mutually exclusive:
        #   - discounts=[{promotion_code: ...}]  OR
        #   - allow_promotion_codes=True
        # ...never both. If the user pre-applied a code during onboarding we
        # resolve it to the Stripe promotion-code ID and pre-attach it so the
        # discount is already visible on the hosted checkout page. Otherwise
        # we leave the native "Have a promo code?" field enabled so they can
        # type one on Stripe directly.
        session_kwargs = dict(
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{config.FRONTEND_URL}/dashboard?status=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{config.FRONTEND_URL}/onboarding?status=cancel",
            customer_email=current_user.email,
            metadata={"user_id": current_user.id, "plan": plan},
            # Propagate user_id onto the Subscription object too so the
            # customer.subscription.updated / .deleted webhooks can find
            # the user without a fragile email lookup.
            subscription_data={"metadata": {"user_id": current_user.id}},
        )

        resolved_promo_id = None
        if promotion_code:
            try:
                promos = stripe.PromotionCode.list(code=promotion_code, active=True, limit=1)
                if promos.data:
                    resolved_promo_id = promos.data[0].id
                else:
                    logger.warning(f"Promo code '{promotion_code}' not active — ignoring, user can retry on Stripe page.")
            except stripe.error.StripeError as e:
                logger.warning(f"Stripe lookup for promo '{promotion_code}' failed: {e}")

        if resolved_promo_id:
            session_kwargs["discounts"] = [{"promotion_code": resolved_promo_id}]
        else:
            session_kwargs["allow_promotion_codes"] = True

        checkout_session = stripe.checkout.Session.create(**session_kwargs)
        logger.info(f"Checkout session created successfully: {checkout_session.id}")
        return {"url": checkout_session.url}
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buy-credits")
async def buy_credits(
    credits: int = Body(..., embed=True),
    current_user: User = Depends(require_role(MASTER_ADMIN)),  # billing = Master Admin only
):
    """One-time purchase of a credit pack (Stripe mode='payment'). The pack and
    its price are defined in code (CREDIT_PACKS) and validated server-side;
    credits are granted on webhook / confirm-session."""
    amount_cents = CREDIT_PACKS.get(int(credits))
    if not amount_cents:
        raise HTTPException(status_code=400, detail=f"Invalid credit pack: {credits}")
    try:
        session = stripe.checkout.Session.create(
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"{int(credits)} Pipelyt credits"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{config.FRONTEND_URL}/dashboard?status=credits_success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{config.FRONTEND_URL}/dashboard?status=credits_cancel",
            customer_email=current_user.email,
            metadata={
                "user_id": current_user.id,
                "kind": "credit_purchase",
                "credits": int(credits),
            },
        )
        logger.info(f"Buy-credits session created for user {current_user.id}: {int(credits)} credits (${amount_cents/100:.0f})")
        return {"url": session.url}
    except Exception as e:
        logger.error(f"Error creating buy-credits session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-coupon")
async def verify_coupon(
    code: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Hardcoded 100% OFF for testing/early users
    if code.upper() == "PIPELYT100":
        return {"valid": True, "discount": 100}
    
    try:
        # 1. First, check if it's a "Promotion Code" (e.g., 'SAVE20')
        # This is what customers usually type.
        promo_codes = stripe.PromotionCode.list(code=code, active=True, limit=1)
        if promo_codes.data:
            promo = promo_codes.data[0]
            coupon = promo.coupon
            discount = coupon.percent_off
            if not discount and coupon.amount_off:
                # If it's a fixed amount (e.g. $10 off), we return it though 
                # frontend might expect a percentage.
                discount = coupon.amount_off / 100 

            return {"valid": True, "discount": discount}
            
        # 2. Fallback to direct Coupon ID check
        coupon = stripe.Coupon.retrieve(code)
        if coupon and coupon.valid:
            discount = coupon.percent_off or 100
            return {"valid": True, "discount": discount}
    except stripe.error.StripeError as e:
        logger.warning(f"Coupon verification failed for '{code}': {e}")

    return {"valid": False, "discount": 0}

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature")
):
    payload = await request.body()
    logger.info("Received Stripe Webhook request")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, config.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.warning(f"Invalid Stripe Webhook signature: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    logger.info(f"Webhook event verified: {event['type']}")

    db = SessionLocal()
    try:
        evt_type = event["type"]
        obj = event["data"]["object"]

        if evt_type == "checkout.session.completed":
            if _session_kind(obj) == "credit_purchase":
                # One-time credit pack — add credits (idempotent).
                _apply_credit_purchase(db, obj)
            else:
                # Initial subscribe — flip user.pricing_plan to the chosen tier.
                _apply_paid_session_to_user(db, obj)

        elif evt_type == "customer.subscription.updated":
            # Plan upgrade/downgrade in the customer portal, renewals, payment
            # method updates, "cancel at period end" toggles.
            _apply_subscription_updated(db, obj)

        elif evt_type == "customer.subscription.deleted":
            # Subscription has actually ended — drop user back to free tier.
            _apply_subscription_deleted(db, obj)

        else:
            # Unhandled event types are fine — Stripe just expects 200.
            logger.debug(f"Webhook event {evt_type} acknowledged but not handled")
    except Exception as db_err:
        logger.exception(f"Webhook handler error for {event.get('type')}: {db_err}")
    finally:
        db.close()

    return {"status": "success"}


@router.post("/confirm-session")
async def confirm_session(
    session_id: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Success-page confirmation endpoint. The frontend calls this after Stripe
    redirects the user back with `?status=success&session_id={CHECKOUT_SESSION_ID}`.

    Why this exists in addition to the webhook:
      - Webhooks are async and may arrive late, fail to deliver (network blips,
        cold starts), or be impossible to reach in local development. Relying on
        them alone leaves users stranded on the onboarding screen even after a
        successful charge.
      - This endpoint re-queries Stripe server-side (trusted source of truth)
        so a client-provided session_id cannot fake a paid status.
      - Idempotent: safe to call repeatedly, the underlying helper only writes
        when Stripe itself reports payment_status == 'paid'.

    Security: the session's metadata.user_id MUST match the caller, so one user
    cannot confirm another user's session by guessing its id.
    """
    try:
        stripe_session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        logger.warning(f"Stripe session retrieval failed for {session_id}: {e}")
        raise HTTPException(status_code=400, detail="Invalid Stripe session")

    # Normalize Stripe SDK object → plain dict (see note in _apply_paid_session_to_user).
    session = stripe_session.to_dict() if hasattr(stripe_session, "to_dict") else stripe_session

    metadata_user_id = (session.get("metadata") or {}).get("user_id")
    if not metadata_user_id or str(metadata_user_id) != str(current_user.id):
        logger.warning(
            f"User {current_user.id} tried to confirm session {session_id} "
            f"owned by user_id={metadata_user_id!r}"
        )
        raise HTTPException(status_code=403, detail="Session does not belong to this user")

    # Credit purchase (one-time) — grant credits idempotently, then return.
    if _session_kind(session) == "credit_purchase":
        granted = _apply_credit_purchase(db, session)
        paid = session.get("payment_status") == "paid"
        return {
            "status": "paid" if paid else "pending",
            "kind": "credit_purchase",
            "granted": granted,
        }

    updated_user = _apply_paid_session_to_user(db, session)
    if not updated_user:
        # Session exists but hasn't reached paid state yet (user just submitted,
        # Stripe is still processing, etc.). Not an error — tell the frontend
        # to keep polling or wait for the webhook.
        return {
            "status": "pending",
            "payment_status": session.get("payment_status"),
            "onboarded": current_user.onboarded,
        }

    return {
        "status": "paid",
        "payment_status": "paid",
        "onboarded": updated_user.onboarded,
        "pricing_plan": updated_user.pricing_plan,
    }

@router.post("/create-portal-session")
async def create_portal_session(
    current_user: User = Depends(get_current_user)
):
    """Create a Stripe Customer Portal session for managing subscriptions."""
    try:
        # 1. Find or create Stripe Customer
        customers = stripe.Customer.list(email=current_user.email, limit=1)
        if not customers.data:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.full_name,
                metadata={"user_id": current_user.id}
            )
        else:
            customer = customers.data[0]

        # 2. Create the portal session
        portal_session = stripe.billing_portal.Session.create(
            customer=customer.id,
            return_url=f"{config.FRONTEND_URL}/settings?tab=billing",
        )
        return {"url": portal_session.url}
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
