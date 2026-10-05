"""Daily verification job and Celery wiring (SEC-007, FR-AUD-004, docs/04 §6, docs/11 §6)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from app.audit import tasks as audit_tasks
from app.audit.signing import LocalDevSigner
from app.audit.verify_all import (
    TenantListingUnavailable,
    check_partition_runway,
    list_tenant_ids,
    verify_all,
)
from app.core.config import Environment, Settings

pytestmark = pytest.mark.db


def test_SEC_007_verify_all_reports_broken_chain_as_p1(
    tenant_ids: tuple[uuid.UUID, uuid.UUID],
    record_events: Any,
    tamper: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    good, bad = tenant_ids
    record_events(good, 2)
    record_events(bad, 3)
    with tamper() as c:
        c.execute(text("DELETE FROM audit.events WHERE tenant_id = :t AND seq = 2"), {"t": bad})
    with caplog.at_level(logging.INFO, logger="app.audit"):
        results = verify_all([good, bad])
    assert results[good].ok
    assert (results[bad].ok, results[bad].first_bad_seq, results[bad].reason) == (
        False,
        2,
        "seq_gap",
    )
    broken = [r for r in caplog.records if r.getMessage() == "audit.chain.broken"]
    assert len(broken) == 1
    assert broken[0].levelno == logging.ERROR
    assert broken[0].tenant_id == str(bad)  # type: ignore[attr-defined]
    assert broken[0].severity == "P1"  # type: ignore[attr-defined]
    verified = [r for r in caplog.records if r.getMessage() == "audit.chain.verified"]
    assert [r.tenant_id for r in verified] == [str(good)]  # type: ignore[attr-defined]


def test_SEC_007_list_tenant_ids_explains_missing_definer_function(
    test_database: Any, make_alembic_config: Any
) -> None:
    """Before 0003_core_schema, the job fails with a clear message (isolated database)."""
    url = test_database.create_fresh("schoolos_audit_only")
    command.upgrade(make_alembic_config(url), "0002_audit")
    engine = create_engine(url.replace("sos_migrator:test-migrator-pw", "sos_app:test-app-pw"))
    try:
        with pytest.raises(TenantListingUnavailable, match="0003_core_schema"):
            list_tenant_ids(engine=engine)
    finally:
        engine.dispose()


def test_partition_runway_warns_when_short(
    app_engine: Any, caplog: pytest.LogCaptureFixture
) -> None:
    bound = check_partition_runway()
    assert bound is not None
    assert bound >= datetime.now(UTC).date() + timedelta(days=300)
    with caplog.at_level(logging.WARNING, logger="app.audit"):
        check_partition_runway(today=bound - timedelta(days=10))
    assert any(r.getMessage() == "audit.partitions.low_runway" for r in caplog.records)


def test_FR_AUD_004_verify_task_runs_per_tenant(
    tenant: uuid.UUID,
    record_events: Any,
    platform_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_events(tenant, 2)
    monkeypatch.setattr(audit_tasks, "_tenant_ids", lambda: [tenant])
    out = audit_tasks.verify_all_chains.apply().get()
    assert out["tenants"] == 1
    assert out["broken"] == []
    assert out["platform_ok"] is True
    assert out["partitions_until"] is not None


def test_FR_AUD_004_archive_task_exports_each_tenant(
    tenant: uuid.UUID,
    record_events: Any,
    fake_s3: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = record_events(tenant, 3)
    day: date = events[0].occurred_at.astimezone(UTC).date()
    signer = LocalDevSigner(Settings(env=Environment.CI, local_dev_master_key=SecretStr("t-key")))
    monkeypatch.setattr(audit_tasks, "_tenant_ids", lambda: [tenant])
    monkeypatch.setattr(audit_tasks, "build_s3_client", lambda _s: fake_s3)
    monkeypatch.setattr(audit_tasks, "build_signer", lambda _s: signer)
    out = audit_tasks.archive_daily.apply(kwargs={"day": day.isoformat()}).get()
    assert out == {
        "idempotency_key": f"audit.archive_daily:{day.isoformat()}",
        "tenants": 1,
        "written": 1,
        "events": 3,
    }
    assert len(fake_s3.objects) == 2


def test_SEC_007_every_school_that_can_hold_a_chain_is_verified_and_archived(
    admin_engine: Any, app_engine: Any
) -> None:
    """Audit 2026-10-05 DP-03: the daily verification and the signed archive listed only active
    and suspended schools. The events written while a school is provisioning, and above all
    while it is offboarding (``tenant.data_purged``, ``tenant.keys_destroyed``: the evidence of
    the deletion, kept a year), were never verified and never reached the Object Lock archive.
    A ``deleted`` school's chain stays until its retention ends, so it is verified too."""
    made: dict[str, uuid.UUID] = {}
    with admin_engine.begin() as c:
        for status in ("provisioning", "active", "suspended", "offboarding", "deleted"):
            tenant_id = uuid.uuid4()
            c.execute(
                text(
                    "INSERT INTO core.tenants (id, code, name, status) "
                    "VALUES (:i, :c, 'Synthetic School', :s)"
                ),
                {"i": tenant_id, "c": f"dp03-{tenant_id.hex[:12]}", "s": status},
            )
            made[status] = tenant_id
    try:
        listed = set(list_tenant_ids())
        assert set(made.values()) <= listed
    finally:
        with admin_engine.begin() as c:
            c.execute(
                text("DELETE FROM core.tenants WHERE id = ANY(:ids)"), {"ids": list(made.values())}
            )


def test_FR_AUD_004_signer_selection_fails_closed() -> None:
    with pytest.raises(Exception, match=r"not allowed|MASTER_KEY"):
        audit_tasks.build_signer(Settings(env=Environment.CI, local_dev_master_key=None))


def test_FR_AUD_004_beat_schedule_and_registration() -> None:
    from sos_worker.celery_app import TASK_MODULES, celery_app

    assert "app.audit.tasks" in TASK_MODULES
    schedule = celery_app.conf.beat_schedule
    by_task = {entry["task"]: entry["schedule"] for entry in schedule.values()}
    archive, verify = by_task["audit.archive_daily"], by_task["audit.verify_all_chains"]
    assert (archive.hour, archive.minute) == ({20}, {30})  # 02:00 IST
    assert (verify.hour, verify.minute) == ({20}, {45})
    assert audit_tasks.archive_daily.acks_late is True
    assert audit_tasks.verify_all_chains.acks_late is True
