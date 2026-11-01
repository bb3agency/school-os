"""AI answer quota alerts at 80 % and 100 % of a bundle's included answers (ADR-0038 follow-up,
docs/16 §19 Q16; FR-PLT-020, FR-PLT-030, FR-NOT-001).

The usage collector records each crossing once per school, calendar month and threshold
(``platform.usage_threshold_events``, metric ``ai_answers``) and audits it on the platform chain.
Operators see it in ``GET /platform/usage/ai-quota-alerts`` and on the school's AI answers meter;
the school sees it on its billing card, and the school-side pull
(``app.notifications.quota_sync``) notifies the owner and the principal once per month and
level. Alert only. Counts only. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.notifications import quota_sync
from app.platform import usage
from app.platform.common import today_ist

from .conftest import Api, MakeOperator, Operator
from .test_school_ai_bundle import (
    _add_member,
    _answers,
    _counts_from,
    _get,
    _live_school,
    _set_bundle,
)

pytestmark = pytest.mark.db


def _check(tid: uuid.UUID, day: dt.date) -> list[int]:
    with platform_session() as s:
        return usage.check_ai_quota(s, tid, day)


def _events(tid: uuid.UUID) -> list[tuple[str, int, dt.date]]:
    with platform_session() as s:
        rows: Any = s.execute(
            text(
                "SELECT metric, threshold, period_start FROM platform.usage_threshold_events "
                "WHERE tenant_id = :t ORDER BY threshold"
            ),
            {"t": tid},
        ).all()
    return [(r[0], int(r[1]), r[2]) for r in rows]


def _audits(tid: uuid.UUID) -> list[dict[str, Any]]:
    with platform_session() as s:
        rows: Any = s.execute(
            text(
                "SELECT summary FROM platform.audit_events WHERE subject_tenant_id = :t "
                "AND action = 'usage.ai_quota_threshold_crossed' ORDER BY seq"
            ),
            {"t": tid},
        ).scalars()
        return [dict(r) for r in rows]


def _notifications(admin: Engine, tid: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.template_key, n.params, n.dedupe_key, r.key AS role "
                "FROM ops.notifications n "
                "JOIN core.membership_roles mr ON mr.membership_id = n.recipient_membership_id "
                "AND mr.tenant_id = n.tenant_id "
                "JOIN core.roles r ON r.id = mr.role_id AND r.tenant_id = n.tenant_id "
                "WHERE n.tenant_id = :t AND n.template_key = :k ORDER BY n.dedupe_key, r.key"
            ),
            {"t": tid, "k": quota_sync.TEMPLATE},
        ).mappings()
        return [dict(r) for r in rows]


def test_Q16_80_and_100_percent_recorded_once_per_month(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    bundle = _set_bundle(api, owner, sub, "ai-lite")
    included = int(bundle["included_answers"])
    month = today_ist().replace(day=1)
    _counts_from(sub, month)
    day2 = month + dt.timedelta(days=1)

    eighty = included * 80 // 100
    _answers(tid, month, eighty - 1)
    assert _check(tid, month) == []
    _answers(tid, day2, 1)  # exactly 80 %
    assert _check(tid, day2) == [80]
    assert _check(tid, day2) == []  # a second run records nothing new
    _answers(tid, day2, included - eighty + 1)  # past 100 %
    assert _check(tid, day2) == [100]
    assert _check(tid, day2) == []

    assert _events(tid) == [("ai_answers", 80, month), ("ai_answers", 100, month)]
    audits = _audits(tid)
    assert [a["threshold"] for a in audits] == [80, 100]
    assert all(set(a) == {"metric", "threshold", "month"} for a in audits)
    # Last month's answers never count towards this month's alert.
    assert usage.ai_quota_alert(tid) is not None
    assert usage.ai_quota_alert(tid, today=month - dt.timedelta(days=1)) is None


def test_Q16_collector_and_no_bundle_or_not_yet_counting(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    month = today_ist().replace(day=1)
    day2 = month + dt.timedelta(days=1)
    # Without a bundle: nothing.
    plain, _sub0, _s0 = _live_school(api, owner, make_plan(), admin_engine)
    _answers(plain, month, 5000)
    assert _check(plain, month) == []
    # A bundle that counts from next month: nothing yet.
    later, sub1, _s1 = _live_school(api, owner, make_plan(), admin_engine)
    _set_bundle(api, owner, sub1, "ai-lite")
    _answers(later, month, 5000)
    assert _check(later, month) == []
    assert _events(later) == []
    # The daily collector runs the check (its own day counts 0 answers; earlier days stay).
    tid, sub, _s = _live_school(api, owner, make_plan(), admin_engine)
    bundle = _set_bundle(api, owner, sub, "ai-lite")
    _counts_from(sub, month)
    _answers(tid, month, int(bundle["included_answers"]))
    usage.collect_tenant(tid, day2)
    assert [t for _m, t, _p in _events(tid)] == [80, 100]


def test_Q16_operators_and_the_school_see_the_alert(
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    tid, sub, subject = _live_school(api, owner, make_plan(), admin_engine)
    quiet, _qsub, _qs = _live_school(api, owner, make_plan(), admin_engine)
    bundle = _set_bundle(api, owner, sub, "ai-lite")
    included = int(bundle["included_answers"])
    month = today_ist().replace(day=1)
    _counts_from(sub, month)
    _answers(tid, month, included * 80 // 100)
    _check(tid, month)

    viewer = make_operator("platform_viewer")
    res = api.call("GET", "/usage/ai-quota-alerts", viewer)
    assert res.status_code == 200, res.text
    mine = [r for r in res.json() if r["tenant_id"] == str(tid)]
    assert mine == [
        {
            "tenant_id": str(tid),
            "threshold": 80,
            "month_start": month.isoformat(),
            "answers_used": included * 80 // 100,
            "included_answers": included,
            "crossed_at": mine[0]["crossed_at"],
        }
    ]
    assert str(quiet) not in res.text
    meter = api.call("GET", f"/tenants/{tid}/ai-answers", viewer).json()
    assert meter["bundle"]["quota_alert"] == 80
    assert _get(api, subject).json()["ai_bundle"]["quota_alert"] == 80

    _answers(tid, month + dt.timedelta(days=1), included)
    _check(tid, month)
    assert _get(api, subject).json()["ai_bundle"]["quota_alert"] == 100
    (row,) = [
        r
        for r in api.call("GET", "/usage/ai-quota-alerts", viewer).json()
        if r["tenant_id"] == str(tid)
    ]
    assert row["threshold"] == 100
    # Another month: nothing listed for it.
    last = (month - dt.timedelta(days=1)).isoformat()
    older = api.call("GET", f"/usage/ai-quota-alerts?month={last}", viewer).json()
    assert str(tid) not in {r["tenant_id"] for r in older}


def test_Q16_school_side_pull_notifies_owner_and_principal_once(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: True)
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    _add_member(admin_engine, tid, "principal")
    _add_member(admin_engine, tid, "accountant")  # reads billing, is not told
    bundle = _set_bundle(api, owner, sub, "ai-lite")
    included = int(bundle["included_answers"])
    month = today_ist().replace(day=1)
    _counts_from(sub, month)

    assert quota_sync.sync_school(tid) == 0  # nothing crossed yet
    _answers(tid, month, included * 80 // 100)
    _check(tid, month)
    assert quota_sync.sync_school(tid) == 2
    assert quota_sync.sync_school(tid) == 0  # idempotent
    near = _notifications(admin_engine, tid)
    assert {n["role"] for n in near} == {"owner", "principal"}
    assert {n["params"]["level"] for n in near} == {"near"}
    assert {n["params"]["included"] for n in near} == {included}
    assert {n["dedupe_key"] for n in near} == {f"billing.ai_quota:{month:%Y-%m}:80"}

    _answers(tid, month + dt.timedelta(days=1), included)
    _check(tid, month)
    assert quota_sync.sweep_all()["sent"] >= 2
    assert quota_sync.sync_school(tid) == 0
    used_up = [n for n in _notifications(admin_engine, tid) if n["params"]["level"] == "used_up"]
    assert {n["role"] for n in used_up} == {"owner", "principal"}


def test_Q16_school_side_pull_does_nothing_on_a_dedicated_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: False)
    assert quota_sync.sync_school(uuid.uuid4()) == 0
    assert quota_sync.sweep_all() == {"tenants": 0, "sent": 0, "failed": 0}
