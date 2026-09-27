"""Break-glass jobs and log hygiene (US-103 AC2, FR-OPS-004, SEC-008, invariant 5)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.breakglass import service
from app.breakglass.tasks import beat_schedule, sweep_all

from .conftest import OPERATOR_NAME, Campus, MakeOperator, grant_row, raise_request

pytestmark = pytest.mark.db


def test_FR_OPS_004_sweep_is_scheduled_every_minute() -> None:
    entry = beat_schedule()["breakglass-sweep"]
    assert entry["task"] == "breakglass.sweep"
    assert entry["schedule"] == 60.0
    from sos_worker.celery_app import TASK_MODULES, celery_app

    assert "app.breakglass.tasks" in TASK_MODULES
    assert "breakglass-sweep" in celery_app.conf.beat_schedule


def test_FR_OPS_004_sweep_all_pulls_and_expires_for_every_school(
    campus: Campus, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id = raise_request(campus.tenant_id, op)
    totals = sweep_all()
    assert totals["tenants"] >= 1
    assert totals["failed"] == 0
    assert grant_row(admin_engine, request_id)["status"] == "requested"
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.break_glass_grants SET requested_at = now() - interval '2 days' "
                "WHERE platform_request_id = :r"
            ),
            {"r": request_id},
        )
    sweep_all()
    assert grant_row(admin_engine, request_id)["status"] == "expired"


def test_SEC_008_breakglass_flow_logs_no_personal_data(
    campus: Campus,
    api: Any,
    make_operator: MakeOperator,
    capsys: pytest.CaptureFixture[str],
) -> None:
    op = make_operator("support_agent")
    request_id = raise_request(campus.tenant_id, op, scope={"section_id": campus.id("section_9a")})
    capsys.readouterr()
    items = api.call(campus.person("owner"), "GET", "/api/v1/breakglass/requests").json()["data"]
    grant_id = next(i["id"] for i in items if i["platform_request_id"] == str(request_id))
    api.call(campus.person("owner"), "POST", f"/api/v1/breakglass/requests/{grant_id}/approve")

    class Op:
        subject = op.subject

    api.call(Op(), "GET", f"/api/v1/sections/{campus.id('section_9a')}")
    api.call(Op(), "POST", "/api/v1/classes", json={"code": "ZZ"})
    api.call(campus.person("owner"), "POST", f"/api/v1/breakglass/grants/{grant_id}/revoke")
    service.sweep_school(campus.tenant_id)
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs
    for secret in (OPERATOR_NAME, "Kalyani", op.email, "duplicate rows", op.subject):
        assert secret not in logs, secret
    assert str(uuid.UUID(grant_id)) in logs or "breakglass" in logs
