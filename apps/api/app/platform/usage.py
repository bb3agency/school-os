"""Usage metering and plan limits (FR-PLT-020, FR-PLT-021; docs/16 §5.10, §11).

Counts only, never records. Shared tier: a daily job fans out over ``core.list_tenant_ids``
and, per school, reads ``core.tenant_usage_summary`` (definer, counts only) plus aggregate
counts in the school's own ``tenant_session`` (RLS applies): distinct active users from its
audit log, its Ask questions and billable AI answers from ``kb.queries`` (ADR-0020 B2), and its
active students, active documents and stored bytes (audit 2026-10-17 L-09).
Dedicated tier: the heartbeat carries the same counts. Limits never block school work in M0;
crossing 80% / 100% is recorded once per metric per billing period and audited.

AI answer quota alerts (ADR-0038 follow-up, docs/16 §19 Q16): after each collection (and each
heartbeat) the month's billable answers are compared with the bundle's included answers; the
first crossing of 80 % and of 100 % in a calendar month is recorded in ``usage_threshold_events``
(metric ``ai_answers``, ``period_start`` = the month) and audited
``usage.ai_quota_threshold_crossed``.
Alert only: nothing is switched off. Operators read the crossings (``ai_quota_alerts``); the
school side pulls its own (``ai_quota_alert``) into in-app notifications.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from app.core.db import platform_session, tenant_session
from app.core.logging import get_logger
from app.platform import billing
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import IST, SYSTEM, audit_platform, billing_cfg, config, today_ist
from app.platform.schemas import AiQuotaAlertOut, HbUsage, UsageDailyOut
from app.tenancy import service as tenancy

log = get_logger(__name__)

AI_QUOTA_METRIC = billing.AI_QUOTA_METRIC

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


# Questions asked and billable AI answers (billing.yaml ai_answers.billable_statuses) that IST
# day. Counts only: no question, answer or person leaves the school's session.
_AI_COUNTS_SQL = text(
    "SELECT count(*) AS queries, "
    "count(*) FILTER (WHERE q.status = ANY(CAST(:billable AS text[]))) AS answers "
    "FROM kb.queries AS q "
    "WHERE q.tenant_id = :t AND q.created_at >= :start AND q.created_at < :end"
)


# Students, documents and stored bytes at collection time (audit 2026-10-17 L-09). Counts only,
# in the school's own session: active students (``status`` only), active documents, and the size
# of every stored document version (quarantined versions are discarded, not stored).
_HOLDINGS_SQL = text(
    "SELECT "
    "(SELECT count(*) FROM sis.students AS st WHERE st.tenant_id = :t "
    "AND st.status = 'active') AS students, "
    "(SELECT count(*) FROM kb.documents AS d WHERE d.tenant_id = :t "
    "AND d.status = 'active') AS documents, "
    "(SELECT coalesce(sum(v.size_bytes), 0) FROM kb.document_versions AS v "
    "WHERE v.tenant_id = :t AND v.status <> 'quarantined') AS storage"
)


def holdings(tenant_id: uuid.UUID) -> tuple[int, int, int]:
    """(active students, active documents, stored bytes) of one school, counted in its own
    ``tenant_session`` (RLS applies; only the numbers leave; ADR-0020 amendment B2 pattern)."""
    with tenant_session(tenant_id) as s:
        row = s.execute(_HOLDINGS_SQL, {"t": tenant_id}).one()
    return int(row.students), int(row.documents), int(row.storage)


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


def ai_counts_on(tenant_id: uuid.UUID, day: dt.date) -> tuple[int, int]:
    """(questions asked, billable AI answers) that IST day, in the school's own session."""
    start, end = _day_bounds(day)
    billable = [str(v) for v in config()["ai_answers"]["billable_statuses"]]
    with tenant_session(tenant_id) as s:
        row = s.execute(
            _AI_COUNTS_SQL, {"t": tenant_id, "start": start, "end": end, "billable": billable}
        ).one()
    return int(row.queries), int(row.answers)


def snapshot(tenant_id: uuid.UUID, day: dt.date) -> dict[str, Any]:
    """Counts for one school and IST day (no write). Used by the collector and heartbeats."""
    with platform_session() as s:
        counts = tenancy.tenant_usage(s, tenant_id)
    ai_queries, ai_answers = ai_counts_on(tenant_id, day)
    students_active, documents, storage_bytes = holdings(tenant_id)
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "usage_date": day,
        "source": "shared_collector",
        "active_users": active_users_on(tenant_id, day),
        "staff_users": counts.active_memberships,
        # The AI tokens and cost stay 0 until the gateway meters join the snapshot.
        "students_active": students_active,
        "storage_bytes": storage_bytes,
        "documents": documents,
        "ai_queries": ai_queries,
        "ai_answers": ai_answers,
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
        check_ai_quota(s, tenant_id, day)
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
        try:
            # Reconcile the AI answer allowance the school's budget is derived from (ADR-0020 B3).
            billing.sync_ai_allowance(tenant_id)
        except Exception as exc:
            log.warning(
                "platform.ai_allowance.sync_failed",
                tenant_id=str(tenant_id),
                error_type=type(exc).__name__,
            )
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
            "ai_answers": usage.ai_answers,
            "ai_input_tokens": usage.ai_input_tokens,
            "ai_output_tokens": usage.ai_output_tokens,
            "ai_cost_usd": usage.ai_cost_usd,
            "ai_cost_inr": (usage.ai_cost_usd * rate).quantize(Decimal("0.01")),
        },
    )
    check_thresholds(session, tenant_id)
    check_ai_quota(session, tenant_id, usage.date)


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


def check_ai_quota(session: Any, tenant_id: uuid.UUID, day: dt.date) -> list[int]:
    """Record the first 80 % / 100 % crossing of the bundle's included answers in ``day``'s
    calendar month (once per school, month and threshold; safe to re-run). Returns the new
    thresholds. Nothing without a bundle, or while the month does not count against it yet."""
    month = day.replace(day=1)
    sub = repo.live_subscription(session, tenant_id)
    if sub is None or sub["ai_bundle_id"] is None:
        return []
    if sub["ai_bundle_from"] is None or sub["ai_bundle_from"] > month:
        return []
    bundle = repo.get(session, m.ai_bundles, sub["ai_bundle_id"])
    included = int(bundle["included_answers"]) if bundle else 0
    if included <= 0:
        return []
    used = repo.ai_answers_between(session, tenant_id, month, billing.next_month(month))
    crossed: list[int] = []
    for threshold in config()["ai_answers"]["quota_alert_thresholds"]:
        if used * 100 < int(threshold) * included:
            continue
        if repo.record_threshold(
            session,
            {
                "tenant_id": tenant_id,
                "metric": AI_QUOTA_METRIC,
                "threshold": int(threshold),
                "period_start": month,
                "usage_value": Decimal(used),
                "limit_value": Decimal(included),
            },
        ):
            crossed.append(int(threshold))
            audit_platform(
                session,
                SYSTEM,
                "usage.ai_quota_threshold_crossed",
                "tenant",
                tenant_id,
                {"metric": AI_QUOTA_METRIC, "threshold": int(threshold), "month": str(month)},
                tenant_id=tenant_id,
            )
            log.warning(
                "platform.usage.ai_quota_threshold_crossed",
                tenant_id=str(tenant_id),
                count=int(threshold),
            )
    return crossed


def _quota_rows(session: Any, month: dt.date, tenant_id: uuid.UUID | None) -> list[Any]:
    t = m.usage_threshold_events
    stmt = select(t).where(t.c.metric == AI_QUOTA_METRIC, t.c.period_start == month)
    if tenant_id is not None:
        stmt = stmt.where(t.c.tenant_id == tenant_id)
    return list(session.execute(stmt.order_by(t.c.tenant_id, t.c.threshold)).mappings())


def _highest(rows: list[Any]) -> dict[uuid.UUID, AiQuotaAlertOut]:
    out: dict[uuid.UUID, AiQuotaAlertOut] = {}
    for r in rows:  # ordered by threshold: the last one per school is the highest
        out[r["tenant_id"]] = AiQuotaAlertOut(
            tenant_id=r["tenant_id"],
            threshold=int(r["threshold"]),
            month_start=r["period_start"],
            answers_used=int(r["usage_value"]),
            included_answers=int(r["limit_value"]),
            crossed_at=r["crossed_at"],
        )
    return out


def ai_quota_alerts(month: dt.date | None = None) -> list[AiQuotaAlertOut]:
    """Operators: every school's highest AI answer quota alert in ``month`` (default: this
    calendar month, IST). Counts only, platform tables only."""
    start = (month or today_ist()).replace(day=1)
    with platform_session() as s:
        return list(_highest(_quota_rows(s, start, None)).values())


def ai_quota_alert(
    tenant_id: uuid.UUID, *, today: dt.date | None = None, session: Any = None
) -> AiQuotaAlertOut | None:
    """One school's highest AI answer quota alert this calendar month (IST), or ``None``."""
    month = (today or today_ist()).replace(day=1)
    if session is not None:
        return _highest(_quota_rows(session, month, tenant_id)).get(tenant_id)
    with platform_session() as s:
        return _highest(_quota_rows(s, month, tenant_id)).get(tenant_id)


def usage_for(tenant_id: uuid.UUID | None, start: dt.date, end: dt.date) -> list[UsageDailyOut]:
    with platform_session() as s:
        rows = repo.usage_range(s, tenant_id, start, end)
    return [UsageDailyOut.model_validate(dict(r)) for r in rows]
