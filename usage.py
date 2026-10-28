"""
Usage metering and rate limiting.

* Quotas: monthly counters per user (scans and AI actions), checked against the
  user's plan before any paid OpenAI call is made.
* Rate limits: short fixed-window counters to slow brute-force logins and bursts.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from pymongo import ReturnDocument

from database import get_rate_limits_collection, get_usage_collection
from plans import get_plan, month_key

logger = logging.getLogger(__name__)

SCANS = "scans"
AI_ACTIONS = "ai_actions"
_LIMIT_FIELD = {SCANS: "scans_per_month", AI_ACTIONS: "ai_actions_per_month"}


async def get_usage(user_id: str) -> dict:
    doc = await get_usage_collection().find_one({"user_id": user_id, "month": month_key()}) or {}
    return {SCANS: int(doc.get(SCANS, 0)), AI_ACTIONS: int(doc.get(AI_ACTIONS, 0))}


async def consume(user: dict, kind: str, amount: int = 1) -> dict:
    """Atomically reserve `amount` units of `kind` or raise 402 if over quota.

    The increment is done first and rolled back on overflow so two concurrent
    requests cannot both slip under the limit.
    """
    plan = get_plan(user)
    limit = getattr(plan, _LIMIT_FIELD[kind])
    if limit <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "upgrade_required", "message": "This feature requires a paid plan.", "plan": plan.id},
        )

    coll = get_usage_collection()
    key = {"user_id": user["id"], "month": month_key()}
    doc = await coll.find_one_and_update(
        key, {"$inc": {kind: amount}, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    used = int(doc.get(kind, 0))
    if used > limit:
        await coll.update_one(key, {"$inc": {kind: -amount}})
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "quota_exceeded",
                "message": f"You have used all {limit} {kind.replace('_', ' ')} included in the {plan.name} plan this month.",
                "plan": plan.id,
                "limit": limit,
            },
        )
    return {"used": used, "limit": limit}


async def refund(user: dict, kind: str, amount: int = 1) -> None:
    """Give back units when the paid operation failed through no fault of the user."""
    try:
        await get_usage_collection().update_one(
            {"user_id": user["id"], "month": month_key()}, {"$inc": {kind: -amount}}
        )
    except Exception as e:  # never mask the original error
        logger.warning(f"Usage refund failed: {e}")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(key: str, limit: int, window_seconds: int, detail: Optional[str] = None) -> None:
    """Fixed-window rate limit. Raises 429 once `limit` hits occur in the window."""
    now = datetime.now(timezone.utc)
    window = int(now.timestamp()) // window_seconds
    bucket = f"{key}:{window}"
    coll = get_rate_limits_collection()
    doc = await coll.find_one_and_update(
        {"key": bucket},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": (now + timedelta(seconds=window_seconds * 2)).replace(tzinfo=None)}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if int(doc.get("count", 0)) > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail or "Too many requests. Please try again shortly.",
            headers={"Retry-After": str(window_seconds)},
        )
