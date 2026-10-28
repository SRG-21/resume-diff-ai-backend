"""Stripe checkout, portal and webhook handling"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import database
from plans import effective_plan_id

SECRET = "whsec_test_secret"


def signed(event: dict, secret: str = SECRET):
    payload = json.dumps(event).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


async def post_event(client, event, secret=SECRET):
    payload, headers = signed(event, secret)
    return await client.post("/api/billing/webhook", content=payload, headers=headers)


def sub_event(event_id, uid, status="active", price="price_pro_m", etype="customer.subscription.updated", end=None):
    end = end or int((datetime.utcnow() + timedelta(days=30)).timestamp())
    return {
        "id": event_id, "type": etype, "object": "event",
        "data": {"object": {
            "id": "sub_1", "customer": "cus_1", "status": status, "metadata": {"user_id": uid},
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": price}, "current_period_end": end}]},
        }},
    }


async def user_doc(uid):
    return await database.get_users_collection().find_one({"_id": uid})


async def test_status_for_new_user(client, make_user):
    headers, _ = await make_user()
    s = (await client.get("/api/billing/status", headers=headers)).json()
    assert s["plan"] == "free" and s["limits"] == {"scans": 3, "ai_actions": 0}
    assert s["usage"] == {"scans": 0, "ai_actions": 0}
    assert s["entitlements"]["rewrites"] is False


async def test_checkout_creates_customer_and_session(client, make_user, monkeypatch):
    create_customer = AsyncMock(return_value={"id": "cus_new"})
    create_session = AsyncMock(return_value={"url": "https://checkout.stripe.test/s1"})
    monkeypatch.setattr("billing._create_customer", create_customer)
    monkeypatch.setattr("billing._create_checkout_session", create_session)
    headers, uid = await make_user()

    r = await client.post("/api/billing/checkout", headers=headers, json={"product": "pro_monthly"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test")
    kwargs = create_session.call_args.kwargs
    assert kwargs["mode"] == "subscription" and kwargs["customer"] == "cus_new"
    assert kwargs["line_items"] == [{"price": "price_pro_m", "quantity": 1}]
    assert kwargs["client_reference_id"] == uid
    assert (await user_doc(uid))["stripe_customer_id"] == "cus_new"

    # The pass is a one-time payment
    await client.post("/api/billing/checkout", headers=headers, json={"product": "pass"})
    assert create_session.call_args.kwargs["mode"] == "payment"
    create_customer.assert_awaited_once()  # customer reused


async def test_checkout_rejects_unknown_product_and_double_subscription(client, make_user, monkeypatch):
    monkeypatch.setattr("billing._create_checkout_session", AsyncMock(return_value={"url": "x"}))
    headers, _ = await make_user()
    assert (await client.post("/api/billing/checkout", headers=headers, json={"product": "nope"})).status_code == 400
    pro_headers, _ = await make_user("pro")
    r = await client.post("/api/billing/checkout", headers=pro_headers, json={"product": "coach_monthly"})
    assert r.status_code == 400 and "portal" in r.json()["detail"]


async def test_portal_requires_billing_account(client, make_user, monkeypatch):
    monkeypatch.setattr("billing._create_portal_session", AsyncMock(return_value={"url": "https://portal.test"}))
    headers, uid = await make_user()
    assert (await client.post("/api/billing/portal", headers=headers)).status_code == 400
    await database.get_users_collection().update_one({"_id": uid}, {"$set": {"stripe_customer_id": "cus_1"}})
    assert (await client.post("/api/billing/portal", headers=headers)).json()["url"] == "https://portal.test"


async def test_webhook_rejects_bad_signature(client):
    payload, headers = signed({"id": "evt_x", "type": "ping", "data": {"object": {}}}, secret="wrong")
    r = await client.post("/api/billing/webhook", content=payload, headers=headers)
    assert r.status_code == 400
    r = await client.post("/api/billing/webhook", content=b"{}")
    assert r.status_code == 400


async def test_subscription_lifecycle(client, make_user):
    headers, uid = await make_user()

    r = await post_event(client, sub_event("evt_1", uid))
    assert r.status_code == 200
    user = await user_doc(uid)
    assert user["plan"] == "pro" and user["stripe_customer_id"] == "cus_1"
    assert effective_plan_id(user) == "pro"
    assert (await client.get("/api/billing/status", headers=headers)).json()["plan"] == "pro"

    # Upgrade to coach through the portal
    await post_event(client, sub_event("evt_2", uid, price="price_coach"))
    assert (await user_doc(uid))["plan"] == "coach"

    # Cancellation drops access immediately
    await post_event(client, sub_event("evt_3", uid, etype="customer.subscription.deleted"))
    user = await user_doc(uid)
    assert user["subscription_status"] == "canceled" and effective_plan_id(user) == "free"
    assert (await client.get("/api/billing/status", headers=headers)).json()["plan"] == "free"


async def test_webhook_is_idempotent(client, make_user):
    _, uid = await make_user()
    assert (await post_event(client, sub_event("evt_dup", uid))).json().get("duplicate") is None
    await database.get_users_collection().update_one({"_id": uid}, {"$set": {"plan": "free"}})
    again = await post_event(client, sub_event("evt_dup", uid))
    assert again.json()["duplicate"] is True
    assert (await user_doc(uid))["plan"] == "free"  # replay was ignored


async def test_pass_purchase_grants_ninety_days_and_stacks(client, make_user):
    _, uid = await make_user()

    def checkout(event_id, paid="paid"):
        return {"id": event_id, "type": "checkout.session.completed", "data": {"object": {
            "id": "cs_1", "mode": "payment", "payment_status": paid, "customer": "cus_9",
            "client_reference_id": uid, "metadata": {"user_id": uid, "product": "pass"},
        }}}

    await post_event(client, checkout("evt_unpaid", paid="unpaid"))
    assert (await user_doc(uid)).get("plan", "free") == "free"

    await post_event(client, checkout("evt_p1"))
    first = await user_doc(uid)
    assert first["plan"] == "pass" and effective_plan_id(first) == "pass"
    days = (first["plan_expires_at"] - datetime.utcnow()).days
    assert 88 <= days <= 90

    await post_event(client, checkout("evt_p2"))
    second = await user_doc(uid)
    assert (second["plan_expires_at"] - first["plan_expires_at"]).days == 90


async def test_failed_handler_allows_stripe_retry(client, make_user, monkeypatch):
    _, uid = await make_user()
    monkeypatch.setattr("billing._apply_subscription", AsyncMock(side_effect=RuntimeError("db down")))
    r = await post_event(client, sub_event("evt_retry", uid))
    assert r.status_code == 500
    assert await database.get_stripe_events_collection().count_documents({"event_id": "evt_retry"}) == 0
