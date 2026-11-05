"""Plan-limit crossings reach operators and schools (docs/16 §5.10, §19 Q16; FR-PLT-021,
FR-NOT-001; lead decision D57).

The usage collector records the first crossing of 80 % and of 100 % of each plan limit per
school, metric and billing period (``platform.usage_threshold_events``) and audits it. Operators
read this period's crossings through ``GET /platform/usage/limit-alerts`` and the dashboard tile
``plan_limit_alerts``; the hourly school-side pull (``app.notifications.quota_sync``) tells the
owner and the principal once per period, metric and threshold, reading the previous period too
so a crossing recorded at the roll is not lost. Limits never block work; no e-mail. Counts and
metric codes only. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.notifications import quota_sync
from app.platform import repository as repo
from app.platform import service as control_plane
from app.platform import usage
from app.platform.billing import previous_period_start
from app.platform.common import today_ist
from app.platform.schemas import PlanLimitAlertOut

from .conftest import Api, MakeOperator, Operator
from .test_school_ai_bundle import _add_member, _live_school

pytestmark = pytest.mark.db


def _period(sub: uuid.UUID) -> tuple[dt.date, dt.date]:
    """(previous period start, current period start) of a subscription."""
    with platform_session() as s:
        row: Any = (
            s.execute(
                text(
                    "SELECT s.current_period_start, s.billing_anchor_day, p.billing_period "
                    "FROM platform.subscriptions s JOIN platform.plans p ON p.id = s.plan_id "
                    "WHERE s.id = :i"
                ),
                {"i": sub},
            )
            .mappings()
            .one()
        )
    current = row["current_period_start"]
    return previous_period_start(current, row, row["billing_anchor_day"]), current


def _cross(
    tid: uuid.UUID, metric: str, threshold: int, period_start: dt.date, used: str = "850"
) -> None:
    with platform_session() as s:
        assert repo.record_threshold(
            s,
            {
                "tenant_id": tid,
                "metric": metric,
                "threshold": threshold,
                "period_start": period_start,
                "usage_value": Decimal(used),
                "limit_value": Decimal("1000"),
            },
        )


def _notices(admin: Engine, tid: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.params, n.dedupe_key, r.key AS role "
                "FROM ops.notifications n "
                "JOIN core.membership_roles mr ON mr.membership_id = n.recipient_membership_id "
                "AND mr.tenant_id = n.tenant_id "
                "JOIN core.roles r ON r.id = mr.role_id AND r.tenant_id = n.tenant_id "
                "WHERE n.tenant_id = :t AND n.template_key = :k ORDER BY n.dedupe_key, r.key"
            ),
            {"t": tid, "k": quota_sync.PLAN_LIMIT_TEMPLATE},
        ).mappings()
        return [dict(r) for r in rows]


@pytest.fixture
def shared_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: True)


def test_FR_PLT_021_D57_collector_crossing_is_listed_for_operators(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    plan = make_plan(limits={"students": 10, "staff_users": 50})
    tid, sub, _subject = _live_school(api, owner, plan, admin_engine)
    quiet, _qsub, _qs = _live_school(api, owner, make_plan(), admin_engine)
    _prev, current = _period(sub)
    with platform_session() as s:
        repo.upsert_usage(
            s,
            {
                "tenant_id": tid,
                "usage_date": today_ist(),
                "source": "shared_collector",
                "active_users": 1,
                "staff_users": 1,
                "students_active": 9,
                "storage_bytes": 0,
                "documents": 0,
                "ai_queries": 0,
                "ai_answers": 0,
                "ai_input_tokens": 0,
                "ai_output_tokens": 0,
                "ai_cost_usd": Decimal("0"),
                "ai_cost_inr": Decimal("0"),
            },
        )
        assert usage.check_thresholds(s, tid) == [("students", 80)]

    for role in ("platform_viewer", "support_agent", "billing_admin"):
        res = api.call("GET", "/usage/limit-alerts", make_operator(role))
        assert res.status_code == 200, res.text
    mine = [r for r in res.json() if r["tenant_id"] == str(tid)]
    assert mine == [
        {
            "tenant_id": str(tid),
            "metric": "students",
            "threshold": 80,
            "period_start": current.isoformat(),
            "usage_value": "9.00",
            "limit_value": "10.00",
            "crossed_at": mine[0]["crossed_at"],
        }
    ]
    assert str(quiet) not in res.text
    # The previous period has nothing for this school.
    older = api.call("GET", "/usage/limit-alerts?period=previous", owner).json()
    assert str(tid) not in {r["tenant_id"] for r in older}
    assert api.call("GET", "/usage/limit-alerts?period=last", owner).status_code == 422


def test_FR_PLT_021_D57_limit_alerts_route_denies_without_usage_read(
    api: Api, make_operator: MakeOperator
) -> None:
    nobody = make_operator()
    assert api.call("GET", "/usage/limit-alerts", nobody).status_code == 403
    staff = api.client.get(
        "/api/v1/platform/usage/limit-alerts",
        headers=api.headers(None, tenant_subject="staff-sub"),
    )
    assert staff.status_code == 401


def test_FR_PLT_021_D57_dashboard_tile_counts_schools_by_highest_threshold(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    before = api.call("GET", "/dashboard", owner).json()["plan_limit_alerts"]
    near, sub1, _s1 = _live_school(api, owner, make_plan(), admin_engine)
    full, sub2, _s2 = _live_school(api, owner, make_plan(), admin_engine)
    old, sub3, _s3 = _live_school(api, owner, make_plan(), admin_engine)
    _cross(near, "students", 80, _period(sub1)[1])
    _cross(near, "storage_gb", 80, _period(sub1)[1])  # one school, counted once
    _cross(full, "students", 80, _period(sub2)[1])
    _cross(full, "students", 100, _period(sub2)[1], used="1000")
    _cross(old, "students", 100, _period(sub3)[0], used="1000")  # last period: not on the tile

    after = api.call("GET", "/dashboard", owner).json()["plan_limit_alerts"]
    assert after["80"] - before["80"] == 1
    assert after["100"] - before["100"] == 1
    previous = api.call("GET", "/usage/limit-alerts?period=previous", owner).json()
    assert [(r["metric"], r["threshold"]) for r in previous if r["tenant_id"] == str(old)] == [
        ("students", 100)
    ]


def test_FR_PLT_021_D57_pull_notifies_owner_and_principal_once(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    shared_tier: None,
) -> None:
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    _add_member(admin_engine, tid, "principal")
    _add_member(admin_engine, tid, "accountant")  # reads billing, is not told
    _prev, current = _period(sub)

    assert quota_sync.sync_plan_limits(tid) == 0  # nothing crossed yet
    _cross(tid, "students", 80, current)
    assert quota_sync.sync_plan_limits(tid) == 2
    assert quota_sync.sync_plan_limits(tid) == 0  # dedupe: once per period, metric, threshold
    notices = _notices(admin_engine, tid)
    assert {n["role"] for n in notices} == {"owner", "principal"}
    assert {n["dedupe_key"] for n in notices} == {f"billing.plan_limit:{current}:students:80"}
    assert all(n["params"] == {"metric": "students", "threshold": 80} for n in notices)

    # 100 % of the same metric is a new notice; the sweep sends it, the next sweep nothing.
    _cross(tid, "students", 100, current, used="1000")
    assert quota_sync.sweep_plan_limits()["sent"] >= 2
    assert quota_sync.sync_plan_limits(tid) == 0
    assert len(_notices(admin_engine, tid)) == 4


def test_FR_PLT_021_D57_previous_period_crossing_is_still_delivered(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    shared_tier: None,
) -> None:
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    previous, current = _period(sub)
    _cross(tid, "storage_gb", 100, previous, used="1000")
    # Two periods ago is history: never pulled.
    _cross(
        tid, "documents", 80, previous_period_start(previous, {"billing_period": "monthly"}, None)
    )
    assert quota_sync.sync_plan_limits(tid) == 1  # the owner only
    assert [n["dedupe_key"] for n in _notices(admin_engine, tid)] == [
        f"billing.plan_limit:{previous}:storage_gb:100"
    ]
    assert quota_sync.sync_plan_limits(tid) == 0
    assert current > previous


def test_FR_PLT_021_D57_suspended_school_is_told_like_the_quota_pull(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    shared_tier: None,
) -> None:
    assert quota_sync.TENANT_STATUSES == ("active", "suspended")
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    _cross(tid, "students", 80, _period(sub)[1])
    with admin_engine.begin() as c:
        c.execute(text("UPDATE core.tenants SET status = 'suspended' WHERE id = :t"), {"t": tid})
    try:
        quota_sync.sweep_plan_limits()
        assert len(_notices(admin_engine, tid)) == 1
    finally:
        with admin_engine.begin() as c:
            c.execute(text("UPDATE core.tenants SET status = 'active' WHERE id = :t"), {"t": tid})


def test_FR_PLT_021_D57_pull_does_nothing_on_a_dedicated_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: False)
    assert quota_sync.sync_plan_limits(uuid.uuid4()) == 0
    assert quota_sync.sweep_plan_limits() == {"tenants": 0, "sent": 0, "failed": 0}


def test_ADR_0020_D57_school_pull_carries_codes_and_counts_only() -> None:
    assert set(PlanLimitAlertOut.model_fields) == {
        "tenant_id",
        "metric",
        "threshold",
        "period_start",
        "usage_value",
        "limit_value",
        "crossed_at",
    }
    assert {"plan_limit_alerts_for_school", "ai_quota_alerts_for_school"} <= set(
        control_plane.__all__
    )
    assert control_plane.plan_limit_alerts_for_school(uuid.uuid4()) == []
