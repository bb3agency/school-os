"""Daily expiry of pending change requests (FR-CR-004)."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.changes import service as changes
from app.changes import tasks
from app.core.errors import Conflict
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]


def test_FR_CR_004_expiry_task_expires_each_school_separately(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    fresh = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    broken = uuid.uuid4()
    monkeypatch.setattr(
        tenancy, "list_tenant_ids", lambda session, statuses: [broken, school.tenant_id]
    )
    later = req.expires_at + dt.timedelta(seconds=1)
    real = changes.expire_due
    calls: list[int] = []

    def flaky(session: Session, *, at: dt.datetime | None = None) -> int:
        calls.append(1)
        if len(calls) == 1:  # the first school fails; the next one still runs
            raise Conflict("synthetic failure")
        return real(session, at=at)

    monkeypatch.setattr(changes, "_now", lambda session: later)
    monkeypatch.setattr(changes, "expire_due", flaky)
    first = tasks.expire_all()
    assert (first["tenants"], first["failed"]) == (2, 1)
    assert first["expired"] >= 2  # both requests are older than `later`
    assert CR.row(admin_engine, req.id)["status"] == "expired"
    assert CR.row(admin_engine, fresh.id)["status"] == "expired"
    assert tasks.expire_all()["expired"] == 0, "idempotent"


def test_FR_CR_004_expiry_is_daily_in_the_worker_beat() -> None:
    schedule = tasks.beat_schedule()
    assert schedule["changes-expire-requests"]["task"] == "changes.expire_requests"
    from sos_worker.celery_app import TASK_MODULES, celery_app

    assert "app.changes.tasks" in TASK_MODULES
    assert "changes-expire-requests" in celery_app.conf.beat_schedule
    celery_app.loader.import_default_modules()
    assert "changes.expire_requests" in celery_app.tasks
