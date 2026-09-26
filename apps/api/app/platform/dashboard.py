"""Dashboard KPIs (docs/16 §5.1). Each tile appears only when the operator holds its read
permission; every value is an aggregate over platform tables (no school records)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select

from app.core.db import platform_session
from app.platform import models as m
from app.platform.common import now, today_ist
from app.platform.schemas import DashboardOut

_TWO = Decimal("0.01")


def mrr(session: object) -> Decimal:
    """Monthly-equivalent price of active + past-due subscriptions (annual / 12), excl. GST."""
    price = func.coalesce(m.subscriptions.c.price_override_inr, m.plans.c.base_price_inr)
    monthly = case((m.plans.c.billing_period == "annual", price / 12), else_=price)
    value = session.execute(  # type: ignore[attr-defined]
        select(func.coalesce(func.sum(monthly), 0))
        .select_from(m.subscriptions)
        .join(m.plans, m.plans.c.id == m.subscriptions.c.plan_id)
        .where(m.subscriptions.c.status.in_(("active", "past_due")))
    ).scalar_one()
    return Decimal(value).quantize(_TWO)


def build(permissions: frozenset[str]) -> DashboardOut:
    out: dict[str, object] = {}
    today = today_ist()
    with platform_session() as s:
        if "platform.subscriptions.read" in permissions:
            value = mrr(s)
            out["mrr_inr"] = value
            out["arr_inr"] = (value * 12).quantize(_TWO)
            trials = s.execute(
                select(
                    func.count(),
                    func.count().filter(
                        m.subscriptions.c.trial_ends_at < now() + dt.timedelta(days=14)
                    ),
                ).where(m.subscriptions.c.status == "trial")
            ).one()
            out["trials_running"], out["trials_ending_14d"] = int(trials[0]), int(trials[1])
        if "platform.tenants.read" in permissions:
            out["schools_by_status"] = {
                k: int(v)
                for k, v in s.execute(
                    select(m.deployments.c.tenant_status, func.count()).group_by(
                        m.deployments.c.tenant_status
                    )
                ).all()
            }
            out["schools_by_tier"] = {
                k: int(v)
                for k, v in s.execute(
                    select(m.deployments.c.mode, func.count()).group_by(m.deployments.c.mode)
                ).all()
            }
        if "platform.invoices.read" in permissions:
            overdue = s.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(
                            m.invoices.c.total_inr
                            - m.invoices.c.amount_paid_inr
                            - m.invoices.c.tds_inr
                        ),
                        0,
                    ),
                    func.min(m.invoices.c.due_date),
                ).where(m.invoices.c.status == "issued", m.invoices.c.due_date < today)
            ).one()
            out["past_due_count"] = int(overdue[0])
            out["past_due_amount_inr"] = Decimal(overdue[1]).quantize(_TWO)
            out["oldest_overdue_due_date"] = overdue[2]
        if "platform.fleet.read" in permissions:
            out["fleet_by_status"] = {
                k: int(v)
                for k, v in s.execute(
                    select(m.deployments.c.status, func.count()).group_by(m.deployments.c.status)
                ).all()
            }
            out["fleet_versions"] = {
                (k or "unknown"): int(v)
                for k, v in s.execute(
                    select(m.deployments.c.app_version, func.count())
                    .where(m.deployments.c.mode == "dedicated")
                    .group_by(m.deployments.c.app_version)
                ).all()
            }
        if "platform.usage.read" in permissions:
            month_start = today.replace(day=1)
            spend: Any = s.execute(
                select(func.coalesce(func.sum(m.usage_daily.c.ai_cost_inr), 0)).where(
                    m.usage_daily.c.usage_date >= month_start
                )
            ).scalar_one()
            out["ai_spend_mtd_inr"] = Decimal(spend).quantize(_TWO)
            top = s.execute(
                select(m.usage_daily.c.tenant_id, func.sum(m.usage_daily.c.ai_cost_inr).label("c"))
                .where(m.usage_daily.c.usage_date >= month_start)
                .group_by(m.usage_daily.c.tenant_id)
                .order_by(func.sum(m.usage_daily.c.ai_cost_inr).desc())
                .limit(5)
            ).all()
            out["ai_spend_top"] = [
                {"tenant_id": str(t), "ai_cost_inr": str(Decimal(c).quantize(_TWO))} for t, c in top
            ]
        if "platform.support.read" in permissions:
            open_ = m.support_tickets.c.status.not_in(("resolved", "closed"))
            out["open_tickets_by_priority"] = {
                k: int(v)
                for k, v in s.execute(
                    select(m.support_tickets.c.priority, func.count())
                    .where(open_)
                    .group_by(m.support_tickets.c.priority)
                ).all()
            }
            out["tickets_sla_breached"] = int(
                s.execute(
                    select(func.count()).where(open_, m.support_tickets.c.resolution_due_at < now())
                ).scalar_one()
            )
    return DashboardOut.model_validate(out)
