"""This month's AI answers and the school's budget state (``GET /knowledge/usage``, FR-KB-040).

- ``state`` (everyone with ``kb.ask``): ``search_only`` when a question asked now would be
  answered with passages only: the kill switch ``SOS_KB_ENABLED``, the school's AI setting or
  its ``kb.ask.enabled`` flag is off (``ai_disabled``), the month's budget would refuse the
  answer model's call (``ai_budget_exhausted``; the gateway's reservation rule, read without
  reserving) or the spend store cannot be read (``ai_unavailable``; calls fail closed then).
  ``near_limit`` from ``models.yaml`` ``budget.alert_fraction`` (80 %) of the monthly budget
  or of the bundle's included answers; ``ok`` otherwise.
- ``answers_used`` / ``included_answers`` (only ``tenant.billing.read`` holders): billable
  answers of the IST calendar month so far, counted from ``kb.queries`` with
  ``budget.bundle.billable_statuses`` (ADR-0038 decision 4; the statuses the control plane's
  daily collector counts, ``platform.usage.ai_counts_on``), and the bundle's included answers
  (``tenancy.service.ai_answer_allowance``; null without a bundle). The count is live: the
  billing page sums the collector's whole days (``answers_counted_to``), so today's answers
  appear here first.

Counts only: no question, answer, person, token or rupee leaves this module, and nothing is
logged.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal

from app.knowledge import repository as repo
from app.knowledge.gateway.budget import IST, BudgetState
from app.knowledge.schemas import UsageOut, UsageState
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext
    from app.knowledge.composition import Runtime

BILLING_READ: Final = "tenant.billing.read"
ANSWER_ROLE: Final = "answer"
"""The model role whose call decides whether a question gets an AI answer."""

Reason = Literal["ai_budget_exhausted", "ai_disabled", "ai_unavailable"]
_REASON: Final[dict[BudgetState, Reason]] = {
    "disabled": "ai_disabled",
    "exhausted": "ai_budget_exhausted",
    "unavailable": "ai_unavailable",
}


def month_bounds(now: dt.datetime) -> tuple[str, dt.datetime, dt.datetime]:
    """``YYYY-MM`` of ``now`` in IST and the month's ``[start, end)`` (IST midnights)."""
    local = now.astimezone(IST)
    start = dt.datetime(local.year, local.month, 1, tzinfo=IST)
    end = dt.datetime(local.year + local.month // 12, local.month % 12 + 1, 1, tzinfo=IST)
    return start.strftime("%Y-%m"), start, end


def _budget_state(runtime: Runtime, ctx: UserContext) -> BudgetState:
    """What a question asked now would meet (the gateway's checks, read-only)."""
    if not runtime.settings.kb_enabled:
        return "disabled"
    if runtime.budget is None:
        # An injected gateway (tests): only the school's switch is known.
        policy = runtime.policy
        if policy is not None and not policy.settings_for(ctx.tenant_id).ai_enabled:
            return "disabled"
        return "ok"
    return runtime.budget.status(ctx.tenant_id, runtime.llm_config.roles[ANSWER_ROLE]).state


def school_usage(
    session: Session, ctx: UserContext, *, runtime: Runtime, now: dt.datetime
) -> UsageOut:
    """The usage of the caller's school this IST month (numbers only for billing readers)."""
    month, start, end = month_bounds(now)
    budget = runtime.llm_config.budget
    used = repo.count_answers(
        session, start=start, end=end, statuses=budget.bundle.billable_statuses
    )
    included = tenancy.ai_answer_allowance(session)
    level = _budget_state(runtime, ctx)
    reason = _REASON.get(level)
    alert = Decimal(str(budget.alert_fraction))
    state: UsageState
    if reason is not None:
        state = "search_only"
    elif level == "alert" or (included is not None and used >= included * alert):
        state = "near_limit"
    else:
        state = "ok"
    numbers = ctx.has(BILLING_READ)
    return UsageOut(
        month=month,
        state=state,
        search_only_reason=reason,
        answers_used=used if numbers else None,
        included_answers=included if numbers else None,
    )


__all__ = ["BILLING_READ", "month_bounds", "school_usage"]
