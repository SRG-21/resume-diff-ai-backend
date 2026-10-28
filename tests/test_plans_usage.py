"""Plan resolution and quota/rate-limit behaviour"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import usage
from plans import effective_plan_id, get_plan


def test_effective_plan_rules():
    now = datetime.utcnow()
    assert effective_plan_id({}) == "free"
    assert effective_plan_id({"plan": "pro", "subscription_status": "active"}) == "pro"
    assert effective_plan_id({"plan": "pro", "subscription_status": "canceled"}) == "free"
    assert effective_plan_id({"plan": "pro", "subscription_status": "past_due", "plan_expires_at": now + timedelta(days=1)}) == "pro"
    assert effective_plan_id({"plan": "pro", "subscription_status": "past_due", "plan_expires_at": now - timedelta(days=1)}) == "free"
    assert effective_plan_id({"plan": "pass", "plan_expires_at": now + timedelta(days=5)}) == "pass"
    assert effective_plan_id({"plan": "pass", "plan_expires_at": now - timedelta(seconds=1)}) == "free"
    assert effective_plan_id({"plan": "bogus", "subscription_status": "active"}) == "free"


def test_free_plan_has_no_paid_features():
    plan = get_plan({})
    assert plan.scans_per_month == 3
    assert not any([plan.breakdown, plan.rewrites, plan.cover_letter, plan.export, plan.tracker, plan.bulk_rank])


async def test_consume_stops_at_limit_and_does_not_overcount(db):
    user = {"id": "u1", "plan": "free"}
    for _ in range(3):
        await usage.consume(user, usage.SCANS)
    with pytest.raises(HTTPException) as exc:
        await usage.consume(user, usage.SCANS)
    assert exc.value.status_code == 402 and exc.value.detail["code"] == "quota_exceeded"
    assert (await usage.get_usage("u1"))[usage.SCANS] == 3


async def test_ai_actions_require_paid_plan(db):
    with pytest.raises(HTTPException) as exc:
        await usage.consume({"id": "u2", "plan": "free"}, usage.AI_ACTIONS)
    assert exc.value.detail["code"] == "upgrade_required"


async def test_refund_restores_a_unit(db):
    user = {"id": "u3", "plan": "pro", "subscription_status": "active"}
    await usage.consume(user, usage.SCANS)
    await usage.refund(user, usage.SCANS)
    assert (await usage.get_usage("u3"))[usage.SCANS] == 0


async def test_rate_limit_window(db):
    for _ in range(3):
        await usage.enforce_rate_limit("k", 3, 60)
    with pytest.raises(HTTPException) as exc:
        await usage.enforce_rate_limit("k", 3, 60)
    assert exc.value.status_code == 429
