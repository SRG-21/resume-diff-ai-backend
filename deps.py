"""
Shared FastAPI dependencies for plan enforcement.
"""
from fastapi import Depends, HTTPException, status

from auth import get_current_user
from config import settings
from plans import FEATURES, get_plan


def upgrade_required(feature: str, plan_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "upgrade_required",
            "message": "This feature is not included in your current plan.",
            "feature": feature,
            "plan": plan_id,
        },
    )


def require_feature(feature: str):
    """Dependency factory: 402 unless the user's current plan includes `feature`."""
    assert feature in FEATURES, f"unknown feature {feature}"

    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        plan = get_plan(user)
        if not getattr(plan, feature):
            raise upgrade_required(feature, plan.id)
        return user

    return dependency


async def get_active_user(user: dict = Depends(get_current_user)) -> dict:
    """Authenticated user; optionally also requires a verified email (free-tier abuse control)."""
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "email_unverified", "message": "Please verify your email address to continue."},
        )
    return user
