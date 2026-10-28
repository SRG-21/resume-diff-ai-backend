"""
Stripe billing: Checkout, Customer Portal, webhook sync and plan status.

The user document is the source of truth for entitlements and is only changed by
the webhook (never by the browser redirect), so access always follows what Stripe
actually charged.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from auth import get_current_user
from config import settings
from database import get_stripe_events_collection, get_users_collection
from plans import COACH, PASS, PRO, effective_plan_id, get_plan, plan_to_dict
from usage import get_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["Billing"])

# checkout key -> (plan id, Checkout mode)
CHECKOUT_PRODUCTS = {
    "pro_monthly": (PRO, "subscription"),
    "pro_yearly": (PRO, "subscription"),
    "pass": (PASS, "payment"),
    "coach_monthly": (COACH, "subscription"),
}


def _price_ids() -> dict:
    return {
        "pro_monthly": settings.STRIPE_PRICE_PRO_MONTHLY,
        "pro_yearly": settings.STRIPE_PRICE_PRO_YEARLY,
        "pass": settings.STRIPE_PRICE_PASS,
        "coach_monthly": settings.STRIPE_PRICE_COACH_MONTHLY,
    }


def _plan_for_price(price_id: str) -> Optional[str]:
    for key, pid in _price_ids().items():
        if pid and pid == price_id:
            return CHECKOUT_PRODUCTS[key][0]
    return None


def _configure_stripe() -> None:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY


# --- thin wrappers around the Stripe SDK (blocking calls run in a thread; easy to patch in tests) ---

async def _stripe_call(fn, **kwargs):
    return await asyncio.to_thread(fn, **kwargs)


async def _create_customer(**kw):
    return await _stripe_call(stripe.Customer.create, **kw)


async def _create_checkout_session(**kw):
    return await _stripe_call(stripe.checkout.Session.create, **kw)


async def _create_portal_session(**kw):
    return await _stripe_call(stripe.billing_portal.Session.create, **kw)


async def _list_subscriptions(**kw):
    return await _stripe_call(stripe.Subscription.list, **kw)


async def _cancel_subscription(sub_id: str):
    return await asyncio.to_thread(stripe.Subscription.cancel, sub_id)


# ============ Models ============

class CheckoutRequest(BaseModel):
    product: str  # pro_monthly | pro_yearly | pass | coach_monthly


# ============ Routes ============

@router.get("/status")
async def billing_status(user: dict = Depends(get_current_user)):
    """Current plan, entitlements and this month's usage"""
    plan = get_plan(user)
    usage = await get_usage(user["id"])
    expires = user.get("plan_expires_at")
    return {
        "plan": plan.id,
        "plan_name": plan.name,
        "subscription_status": user.get("subscription_status"),
        "cancel_at_period_end": bool(user.get("cancel_at_period_end", False)),
        "plan_expires_at": expires.isoformat() if isinstance(expires, datetime) else None,
        "usage": usage,
        "limits": {"scans": plan.scans_per_month, "ai_actions": plan.ai_actions_per_month},
        "entitlements": plan_to_dict(plan),
        "has_billing_account": bool(user.get("stripe_customer_id")),
        "billing_enabled": bool(settings.STRIPE_SECRET_KEY),
    }


@router.post("/checkout")
async def create_checkout(body: CheckoutRequest, user: dict = Depends(get_current_user)):
    """Start a Stripe Checkout session for a plan and return its URL"""
    if body.product not in CHECKOUT_PRODUCTS:
        raise HTTPException(status_code=400, detail="Unknown product")
    price_id = _price_ids()[body.product]
    if not price_id:
        raise HTTPException(status_code=503, detail="This plan is not available yet")
    _configure_stripe()

    plan_id, mode = CHECKOUT_PRODUCTS[body.product]
    current = effective_plan_id(user)
    if current in (PRO, COACH) and user.get("subscription_status") in ("active", "trialing", "past_due"):
        raise HTTPException(
            status_code=400,
            detail="You already have an active subscription. Use the billing portal to change or cancel it.",
        )

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = await _create_customer(email=user["email"], name=user["name"], metadata={"user_id": user["id"]})
        customer_id = customer["id"]
        await get_users_collection().update_one({"_id": user["id"]}, {"$set": {"stripe_customer_id": customer_id}})

    meta = {"user_id": user["id"], "product": body.product}
    params = dict(
        mode=mode,
        customer=customer_id,
        client_reference_id=user["id"],
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.FRONTEND_URL}/billing?checkout=success",
        cancel_url=f"{settings.FRONTEND_URL}/pricing?checkout=cancelled",
        metadata=meta,
        allow_promotion_codes=True,
    )
    if mode == "subscription":
        params["subscription_data"] = {"metadata": meta}
    else:
        params["payment_intent_data"] = {"metadata": meta}

    try:
        session = await _create_checkout_session(**params)
    except stripe.StripeError as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return {"url": session["url"]}


@router.post("/portal")
async def create_portal(user: dict = Depends(get_current_user)):
    """Open the Stripe Customer Portal (update card, cancel, invoices)"""
    _configure_stripe()
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account yet")
    try:
        session = await _create_portal_session(customer=customer_id, return_url=f"{settings.FRONTEND_URL}/billing")
    except stripe.StripeError as e:
        logger.error(f"Stripe portal error: {e}")
        raise HTTPException(status_code=502, detail="Could not open the billing portal. Please try again.")
    return {"url": session["url"]}


# ============ Webhook ============

def _ts(value) -> Optional[datetime]:
    return datetime.utcfromtimestamp(int(value)) if value else None


def _subscription_period_end(sub: dict) -> Optional[datetime]:
    """current_period_end moved from the subscription to its items in newer API versions"""
    end = sub.get("current_period_end")
    if not end:
        items = (sub.get("items") or {}).get("data") or []
        if items:
            end = items[0].get("current_period_end")
    return _ts(end)


def _subscription_price_id(sub: dict) -> Optional[str]:
    items = (sub.get("items") or {}).get("data") or []
    if items:
        return (items[0].get("price") or {}).get("id")
    return None


async def _find_user(user_id: Optional[str], customer_id: Optional[str]) -> Optional[dict]:
    users = get_users_collection()
    user = None
    if user_id:
        user = await users.find_one({"_id": user_id})
    if not user and customer_id:
        user = await users.find_one({"stripe_customer_id": customer_id})
    return user


async def _apply_subscription(sub: dict) -> None:
    meta = sub.get("metadata") or {}
    user = await _find_user(meta.get("user_id"), sub.get("customer"))
    if not user:
        logger.warning(f"Subscription {sub.get('id')} has no matching user")
        return
    plan_id = _plan_for_price(_subscription_price_id(sub) or "") or _plan_from_metadata(meta)
    update = {
        "stripe_customer_id": sub.get("customer"),
        "stripe_subscription_id": sub.get("id"),
        "subscription_status": sub.get("status"),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "plan_expires_at": _subscription_period_end(sub),
    }
    if plan_id:
        update["plan"] = plan_id
    await get_users_collection().update_one({"_id": user["_id"]}, {"$set": update})
    logger.info(f"Synced subscription {sub.get('id')} -> user {user['_id']} status={sub.get('status')}")


def _plan_from_metadata(meta: dict) -> Optional[str]:
    product = (meta or {}).get("product")
    return CHECKOUT_PRODUCTS[product][0] if product in CHECKOUT_PRODUCTS else None


async def _apply_checkout_completed(session: dict) -> None:
    meta = session.get("metadata") or {}
    user = await _find_user(session.get("client_reference_id") or meta.get("user_id"), session.get("customer"))
    if not user:
        logger.warning(f"Checkout {session.get('id')} has no matching user")
        return
    product = meta.get("product")
    if session.get("mode") == "payment" and product == "pass" and session.get("payment_status") == "paid":
        now = datetime.utcnow()
        base = user.get("plan_expires_at") if user.get("plan") == PASS else None
        start = base if isinstance(base, datetime) and base > now else now
        await get_users_collection().update_one(
            {"_id": user["_id"]},
            {"$set": {
                "plan": PASS,
                "subscription_status": "paid",
                "plan_expires_at": start + timedelta(days=settings.PASS_DURATION_DAYS),
                "stripe_customer_id": session.get("customer") or user.get("stripe_customer_id"),
            }},
        )
        logger.info(f"Granted Job Hunt Pass to user {user['_id']}")
    elif session.get("mode") == "subscription":
        # Plan details arrive with customer.subscription.* events; record the link now
        await get_users_collection().update_one(
            {"_id": user["_id"]},
            {"$set": {
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": session.get("subscription"),
            }},
        )


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint (signature verified, idempotent)"""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook is not configured")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        # Verifies the signature and timestamp tolerance; we then read the same bytes as
        # plain JSON so handlers do not depend on the SDK's object types.
        stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
        event = json.loads(payload)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_id = event["id"]
    try:
        await get_stripe_events_collection().insert_one({"event_id": event_id, "type": event["type"], "created_at": datetime.utcnow()})
    except DuplicateKeyError:
        return {"received": True, "duplicate": True}

    obj = event["data"]["object"]
    etype = event["type"]
    try:
        if etype == "checkout.session.completed":
            await _apply_checkout_completed(obj)
        elif etype in ("customer.subscription.created", "customer.subscription.updated"):
            await _apply_subscription(obj)
        elif etype == "customer.subscription.deleted":
            obj = {**obj, "status": "canceled"}
            await _apply_subscription(obj)
        elif etype == "invoice.payment_failed":
            user = await _find_user(None, obj.get("customer"))
            if user:
                logger.warning(f"Payment failed for user {user['_id']}")
    except Exception as e:
        # Let Stripe retry: forget the event so the retry is processed
        logger.error(f"Webhook handler failed for {etype}: {e}", exc_info=True)
        await get_stripe_events_collection().delete_one({"event_id": event_id})
        raise HTTPException(status_code=500, detail="Webhook processing failed")
    return {"received": True}


async def cancel_subscriptions_for_user(user: dict) -> None:
    """Cancel active Stripe subscriptions when an account is deleted (best effort)"""
    customer_id = user.get("stripe_customer_id")
    if not customer_id or not settings.STRIPE_SECRET_KEY:
        return
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        subs = await _list_subscriptions(customer=customer_id, status="active")
        for sub in subs.get("data", []):
            await _cancel_subscription(sub["id"])
    except Exception as e:
        logger.error(f"Failed to cancel subscriptions for {user.get('id')}: {e}")
