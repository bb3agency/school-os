"""Usage metering and plan limits (FR-PLT-020, FR-PLT-021; docs/16 §5.10, §11).

Counts only, never records. Shared tier: a daily job fans out over ``core.list_tenant_ids``
and, per school, reads ``core.tenant_usage_summary`` (definer, counts only) plus one aggregate
count in the school's own ``tenant_session`` (distinct active users from its audit log).
Dedicated tier: the heartbeat carries the same counts. Limits never block school work in M0;
crossing 80% / 100% is recorded once per metric per billing period and audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.core.db import platform_session, tenant_session
from app.core.logging import get_logger
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import IST, SYSTEM, audit_platform, billing_cfg, today_ist
from app.platform.schemas import HbUsage, UsageDailyOut
from app.tenancy import service as tenancy

log = get_logger(__name__)

# metric -> (usage_daily column or aggregate, plan limit key, scale applied to the usage value)
LIMIT_METRICS: dict[str, tuple[str, str, Decimal]] = {
    "students": ("students_active", "students", Decimal(1)),
    "staff_users": ("staff_users", "staff_users", Decimal(1)),
    "documents": ("documents", "documents", Decimal(1)),
    "storage_gb": ("storage_bytes", "storage_gb", Decimal(1) / Decimal(10**9)),
    "ai_tokens_month": ("ai_tokens_period", "ai_tokens_month", Decimal(1)),
    "ai_budget_inr": ("ai_cost_period", "ai_budget_inr", Decimal(1)),
}

_ACTIVE_USERS_SQL = text(
    "SELECT count(DISTINCT e.actor_id) FROM audit.events AS e "
    "WHERE e.tenant_id = :t AND e.actor_type = 'user' AND e.occurred_at >= :start "
    "AND e.occurred_at < :end"
)


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(), IST)
    return start, start + dt.timedelta(days=1)


def active_users_on(tenant_id: uuid.UUID, day: dt.date) -> int:
    """Distinct users with an audited action that IST day (count only, in the school's session)."""
    start, end = _day_bounds(day)
    with tenant_session(tenant_id) as s:
        return int(
            s.execute(_ACTIVE_USERS_SQL, {"t": tenant_id, "start": start, "end": end}).scalar_one()
        )


def snapshot(tenant_id: uuid.UUID, day: dt.date) -> dict[str, Any]:
    """Counts for one school and IST day (no write). Used by the collector and heartbeats."""
    with platform_session() as s:
        counts = tenancy.tenant_usage(s, tenant_id)
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "usage_date": day,
        "source": "shared_collector",
        "active_users": active_users_on(tenant_id, day),
        "staff_users": counts.active_memberships,
        # Student, storage, document and AI meters join core.tenant_usage_summary as the
        # sis/kb modules land (definer function extended in their migrations).
        "students_active": 0,
        "storage_bytes": 0,
        "documents": 0,
        "ai_queries": 0,
        "ai_input_tokens": 0,
        "ai_output_tokens": 0,
        "ai_cost_usd": Decimal("0"),
        "ai_cost_inr": Decimal("0"),
    }
    return values


def collect_tenant(tenant_id: uuid.UUID, day: dt.date) -> dict[str, Any]:
    values = snapshot(tenant_id, day)
    with platform_session() as s:
        repo.upsert_usage(s, values)
        check_thresholds(s, tenant_id)
    return values


def collect_daily(day: dt.date | None = None) -> int:
    """Beat task body (01:30 IST): yesterday's counts for every active or suspended school."""
    day = day or (today_ist() - dt.timedelta(days=1))
    with platform_session() as s:
        ids = tenancy.list_tenant_ids(s, ("active", "suspended"))
    done = 0
    for tenant_id in ids:
        try:
            collect_tenant(tenant_id, day)
            done += 1
        except Exception:
            log.exception("platform.usage.collect_failed", tenant_id=str(tenant_id))
    log.info("platform.usage.collected", count=done, outcome="ok")
    return done


def ingest_heartbeat(session: Any, tenant_id: uuid.UUID, usage: HbUsage) -> None:
    rate = Decimal(str(billing_cfg()["usd_inr_rate"]))
    repo.upsert_usage(
        session,
        {
            "tenant_id": tenant_id,
            "usage_date": usage.date,
            "source": "heartbeat",
            "active_users": usage.active_users,
            "staff_users": usage.staff_users,
            "students_active": usage.students_active,
            "storage_bytes": usage.storage_bytes,
            "documents": usage.documents,
            "ai_queries": usage.ai_queries,
            "ai_input_tokens": usage.ai_input_tokens,
            "ai_output_tokens": usage.ai_output_tokens,
            "ai_cost_usd": usage.ai_cost_usd,
            "ai_cost_inr": (usage.ai_cost_usd * rate).quantize(Decimal("0.01")),
        },
    )
    check_thresholds(session, tenant_id)


def check_thresholds(session: Any, tenant_id: uuid.UUID) -> list[tuple[str, int]]:
    """Record first 80% / 100% crossings per metric per billing period; returns new crossings."""
    sub = repo.live_subscription(session, tenant_id)
    latest = repo.latest_usage(session, tenant_id)
    if sub is None or latest is None:
        return []
    plan = repo.get(session, m.plans, sub["plan_id"])
    limits: dict[str, Any] = dict(plan["limits"]) if plan else {}
    period_rows = repo.usage_range(
        session, tenant_id, sub["current_period_start"], latest["usage_date"]
    )
    values: dict[str, Decimal] = {
        k: Decimal(latest[k])
        for k in ("students_active", "staff_users", "documents", "storage_bytes")
    }
    values["ai_tokens_period"] = Decimal(
        sum(int(r["ai_input_tokens"]) + int(r["ai_output_tokens"]) for r in period_rows)
    )
    values["ai_cost_period"] = sum((Decimal(r["ai_cost_inr"]) for r in period_rows), Decimal(0))
    crossed: list[tuple[str, int]] = []
    for metric, (column, limit_key, scale) in LIMIT_METRICS.items():
        limit = limits.get(limit_key)
        if limit in (None, 0):
            continue
        used = values[column] * scale
        pct = used * 100 / Decimal(str(limit))
        for threshold in billing_cfg()["usage_thresholds"]:
            if pct >= threshold and repo.record_threshold(
                session,
                {
                    "tenant_id": tenant_id,
                    "metric": metric,
                    "threshold": int(threshold),
                    "period_start": sub["current_period_start"],
                    "usage_value": used.quantize(Decimal("0.01")),
                    "limit_value": Decimal(str(limit)),
                },
            ):
                crossed.append((metric, int(threshold)))
                audit_platform(
                    session,
                    SYSTEM,
                    "usage.limit_threshold_crossed",
                    "tenant",
                    tenant_id,
                    {"metric": metric, "threshold": int(threshold)},
                    tenant_id=tenant_id,
                )
                log.warning(
                    "platform.usage.threshold_crossed",
                    tenant_id=str(tenant_id),
                    action=metric,
                    count=int(threshold),
                )
    return crossed


def usage_for(tenant_id: uuid.UUID | None, start: dt.date, end: dt.date) -> list[UsageDailyOut]:
    with platform_session() as s:
        rows = repo.usage_range(s, tenant_id, start, end)
    return [UsageDailyOut.model_validate(dict(r)) for r in rows]
