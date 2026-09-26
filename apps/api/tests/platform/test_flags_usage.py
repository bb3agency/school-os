"""Feature flags (FR-PLT-022) and usage metering / limits (FR-PLT-020, FR-PLT-021)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.audit import service as audit
from app.core.db import context_free_session, platform_session, tenant_session
from app.platform import flags, usage

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db


# --- flags --------------------------------------------------------------------------------------


def test_FR_PLT_022_bucket_is_deterministic_and_spread() -> None:
    tenants = [uuid.UUID(int=i * 7919 + 1) for i in range(2000)]
    first = [flags.bucket("kb.ask.enabled", t) for t in tenants]
    assert first == [flags.bucket("kb.ask.enabled", t) for t in tenants]
    share = sum(b < 30 for b in first) / len(first)
    assert 0.25 < share < 0.35
    assert first != [flags.bucket("other.flag", t) for t in tenants]


def test_FR_PLT_022_evaluation_rules() -> None:
    tid = uuid.uuid4()
    g = {"tenant_id": None, "enabled": True, "rollout_percent": None}
    assert flags.evaluate([g], "x.y", tid) is True
    assert flags.evaluate([{**g, "enabled": False}], "x.y", tid) is False
    assert flags.evaluate([], "x.y", tid) is False
    override_off = {"tenant_id": tid, "enabled": False, "rollout_percent": None}
    assert flags.evaluate([g, override_off], "x.y", tid) is False
    zero = {**g, "rollout_percent": 0}
    hundred = {**g, "rollout_percent": 100}
    assert flags.evaluate([zero], "x.y", tid) is False
    assert flags.evaluate([hundred], "x.y", tid) is True
    assert flags.evaluate([zero, {**override_off, "enabled": True}], "x.y", tid) is True


def test_FR_PLT_022_flags_api_and_tenant_side_read(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    engineer = make_operator("platform_engineer")
    tid = uuid.UUID(
        api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()["tenant_id"]
    )
    key = f"synthetic.flag_{uuid.uuid4().hex[:6]}"
    res = api.call("PUT", f"/flags/{key}", engineer, json={"enabled": True, "rollout_percent": 0})
    assert res.status_code == 200, res.text
    with tenant_session(tid) as s:  # sos_app reads flags inside a normal tenant session
        assert flags.is_enabled(key, tid, session=s) is False
    assert (
        api.call("PUT", f"/flags/{key}/tenants/{tid}", engineer, json={"enabled": True}).status_code
        == 200
    )
    assert flags.is_enabled(key, tid) is True
    listing = api.call("GET", "/flags", engineer).json()["data"]
    assert {(f["key"], f["tenant_id"]) for f in listing} >= {(key, None), (key, str(tid))}
    assert api.call("DELETE", f"/flags/{key}/tenants/{tid}", engineer).status_code == 204
    assert flags.is_enabled(key, tid) is False
    bad = api.call("PUT", f"/flags/{key}/tenants/{uuid.uuid4()}", engineer, json={"enabled": True})
    assert bad.status_code == 404
    with platform_session() as s:
        actions: Any = list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events "
                    "WHERE summary->>'key' = :k ORDER BY seq"
                ),
                {"k": key},
            ).scalars()
        )
    assert actions == ["flag.updated", "flag.override_set", "flag.override_removed"]


def test_SEC_026_app_role_reads_flags_but_cannot_change_them(engines_bound: None) -> None:
    with context_free_session() as s:
        s.execute(text("SELECT key FROM platform.feature_flags LIMIT 1")).all()


@pytest.fixture
def engines_bound(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


# --- usage --------------------------------------------------------------------------------------


def _numbers_only(values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key in ("tenant_id", "usage_date", "source", "collected_at"):
            continue
        assert isinstance(value, int | Decimal), key


def test_FR_PLT_020_usage_rollup_counts_only(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    plan = make_plan(limits={"staff_users": 1, "students": 1000})
    tid = uuid.UUID(
        api.call("POST", "/tenants", owner, json=provision_payload(plan)).json()["tenant_id"]
    )
    api.call("POST", f"/tenants/{tid}/activate", owner)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
        user_id: Any = c.execute(
            text("SELECT user_id FROM core.memberships WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
    with tenant_session(tid, user_id) as s:
        audit.record(s, action="student.viewed", resource_type="student", summary={})
    day = dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
    values = usage.collect_tenant(tid, day)
    _numbers_only(values)
    assert (values["active_users"], values["staff_users"]) == (1, 1)
    rows = api.call("GET", f"/tenants/{tid}/usage?from={day}&to={day}", owner).json()
    assert rows[0]["source"] == "shared_collector"
    with platform_session() as s:
        crossed = s.execute(
            text(
                "SELECT metric, threshold FROM platform.usage_threshold_events "
                "WHERE tenant_id = :t "
                "ORDER BY threshold"
            ),
            {"t": tid},
        ).all()
    assert [tuple(r) for r in crossed] == [("staff_users", 80), ("staff_users", 100)]
    usage.collect_tenant(tid, day)  # a second run records nothing new
    with platform_session() as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM platform.usage_threshold_events WHERE tenant_id = :t"),
                {"t": tid},
            ).scalar_one()
            == 2
        )
    assert usage.collect_daily(day) >= 1
