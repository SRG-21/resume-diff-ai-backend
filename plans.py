"""
Plan definitions and entitlement helpers.

A user's *effective* plan is derived from the subscription fields stored on the
user document (kept in sync by Stripe webhooks), so a lapsed subscription or an
expired Job Hunt Pass drops back to "free" without any scheduled job.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

FREE = "free"
PRO = "pro"
PASS = "pass"
COACH = "coach"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    scans_per_month: int          # comparisons per calendar month
    ai_actions_per_month: int     # rewrites, cover letters, interview prep
    history_limit: int            # saved comparisons visible (0 = unlimited)
    saved_resumes: int
    breakdown: bool               # score breakdown by category
    ats_details: bool             # full ATS issue list (score is always shown)
    rewrites: bool
    cover_letter: bool
    interview_prep: bool
    export: bool                  # tailored DOCX export
    job_url_import: bool
    tracker: bool
    bulk_rank: bool               # coach / recruiter feature
    bulk_max_resumes: int


PLANS = {
    FREE: Plan(FREE, "Free", 3, 0, 5, 1, False, False, False, False, False, False, False, False, False, 0),
    PRO: Plan(PRO, "Pro", 300, 200, 0, 10, True, True, True, True, True, True, True, True, False, 0),
    PASS: Plan(PASS, "Job Hunt Pass", 300, 200, 0, 10, True, True, True, True, True, True, True, True, False, 0),
    COACH: Plan(COACH, "Coach", 1500, 800, 0, 50, True, True, True, True, True, True, True, True, True, 10),
}

# Features that can be requested by name from the API layer
FEATURES = (
    "breakdown", "ats_details", "rewrites", "cover_letter", "interview_prep",
    "export", "job_url_import", "tracker", "bulk_rank",
)


def effective_plan_id(user: dict, now: Optional[datetime] = None) -> str:
    """Return the plan a user is currently entitled to."""
    now = now or datetime.utcnow()
    plan_id = user.get("plan", FREE)
    if plan_id not in PLANS or plan_id == FREE:
        return FREE

    status = user.get("subscription_status")
    if plan_id == PASS:
        expires = user.get("plan_expires_at")
        if isinstance(expires, datetime) and expires > now:
            return PASS
        return FREE

    # Subscriptions: past_due keeps access during Stripe's retry window
    if status in ("active", "trialing", "past_due"):
        period_end = user.get("plan_expires_at")
        if isinstance(period_end, datetime) and period_end < now and status == "past_due":
            return FREE
        return plan_id
    return FREE


def get_plan(user: dict) -> Plan:
    return PLANS[effective_plan_id(user)]


def plan_to_dict(plan: Plan) -> dict:
    return asdict(plan)


def month_key(now: Optional[datetime] = None) -> str:
    """Calendar-month bucket used for usage counters, e.g. '2026-10'."""
    return (now or datetime.utcnow()).strftime("%Y-%m")
