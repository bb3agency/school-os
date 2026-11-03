"""Per-school beat loops keep going when one school fails (deep audit E, 2026-11-03; FR-OPS-004).

Every beat job that fans out over schools runs each school on its own. Before this fix most
loops caught only database and domain errors, so any other exception in one school (a provider
client error, a decryption error, a bug hit by one school's data) stopped every school after it.
The break-glass and breach sweeps also skipped ending grants and sending the breach reminders
of a school whose pull from the control plane failed. The loop bodies are called directly with
the per-school work replaced; no database is needed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

A, B = uuid.uuid4(), uuid.uuid4()


@contextmanager
def _session(*_: Any, **__: Any) -> Iterator[Any]:
    yield None


@contextmanager
def _tenant_session(tenant_id: uuid.UUID, *_: Any, **__: Any) -> Iterator[uuid.UUID]:
    yield tenant_id  # the "session" is the school's id, so the work knows which school it is


def _flaky(seen: list[uuid.UUID], result: Any) -> Callable[..., Any]:
    def work(first: Any, *_: Any, **__: Any) -> Any:
        seen.append(first)
        if first == A:
            raise RuntimeError("unexpected")
        return result

    return work


def _fan_out(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    from app.tenancy import service as tenancy

    monkeypatch.setattr(tenancy, "list_tenant_ids", lambda *_a, **_k: [A, B])
    monkeypatch.setattr(module, "context_free_session", _session, raising=False)
    monkeypatch.setattr(module, "tenant_session", _tenant_session, raising=False)


def test_FR_OPS_004_change_request_expiry_survives_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.changes import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.changes.service.expire_due", _flaky(seen, 1))
    assert tasks.expire_all() == {"tenants": 2, "expired": 1, "failed": 1}
    assert seen == [A, B]


def test_FR_TASK_007_task_reminders_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.circulars import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.circulars.service.send_reminders", _flaky(seen, 3))
    assert tasks.remind_all() == {"tenants": 2, "sent": 3, "failed": 1}
    assert seen == [A, B]


def test_FR_RDY_006_portal_reminders_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.dq import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.dq.portal_tasks.send_reminders", _flaky(seen, 2))
    assert tasks.remind_all() == {"tenants": 2, "sent": 2, "failed": 1}
    assert seen == [A, B]


def test_FR_FEE_009_late_fees_survive_a_failing_school(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.fees import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.fees.service.enabled", lambda *_: True)
    monkeypatch.setattr("app.fees.service.post_late_fees", _flaky(seen, {"posted": 4}))
    assert tasks.post_all() == {"tenants": 2, "posted": 4, "failed": 1}
    assert seen == [A, B]


def test_FR_FEE_026_upi_morning_survives_a_failing_school(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.fees import upi_tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, upi_tasks)
    monkeypatch.setattr(upi_tasks, "morning_for_school", _flaky(seen, {"erased": 1, "notified": 1}))
    out = upi_tasks.morning_all()
    assert (out["erased"], out["notified"], out["failed"]) == (1, 1, 1)
    assert seen == [A, B]


def test_FR_EW_005_early_warning_jobs_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.insights import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    assert tasks.for_each_school("evaluate", _flaky(seen, 5)) == {
        "tenants": 2,
        "done": 5,
        "failed": 1,
    }
    assert seen == [A, B]


def test_FR_DPDP_006_privacy_due_reminders_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.privacy import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.privacy.service.notify_due", _flaky(seen, 1))
    assert tasks.notify_all() == {"tenants": 2, "notified": 1, "failed": 1}
    assert seen == [A, B]


def test_FR_PUB_006_handle_purge_survives_a_failing_school(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.public import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.public.service.purge_expired_handles", _flaky(seen, 6))
    assert tasks.purge_all() == {"tenants": 2, "deleted": 6, "failed": 1}
    assert seen == [A, B]


def test_FR_TALLY_009_silence_check_survives_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tally import tasks

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, tasks)
    monkeypatch.setattr("app.tally.service.connector_enabled", lambda *_: True)
    monkeypatch.setattr("app.tally.service.notify_silent_devices", _flaky(seen, 1))
    monkeypatch.setattr("app.tally.service.purge_old_syncs", lambda *_: 2)
    assert tasks.check_all() == {"tenants": 2, "notified": 1, "purged": 2, "failed": 1}
    assert seen == [A, B]


def test_FR_PLT_030_ai_quota_sweep_survives_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.notifications import quota_sync

    seen: list[uuid.UUID] = []
    _fan_out(monkeypatch, quota_sync)
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda *_: True)
    monkeypatch.setattr(quota_sync, "sync_school", _flaky(seen, 1))
    assert quota_sync.sweep_all() == {"tenants": 2, "sent": 1, "failed": 1}
    assert seen == [A, B]


@pytest.mark.parametrize("module_name", ["app.messaging.tasks", "app.messaging.sends.tasks"])
def test_FR_MSG_016_messaging_loops_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch, module_name: str
) -> None:
    import importlib

    from app.messaging import service as messaging

    module = importlib.import_module(module_name)
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(messaging, "available", lambda: True)
    monkeypatch.setattr(messaging, "live_tenant_ids", lambda: [A, B])
    assert module._each_school("send", _flaky(seen, None)) == {"schools": 1, "failed": 1}
    assert seen == [A, B]


def test_FR_PAY_010_online_payment_loops_survive_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.fees import tasks_online

    seen: list[uuid.UUID] = []
    monkeypatch.setattr("app.fees.online.connected_tenant_ids", lambda: [A, B])
    assert tasks_online._each_school("inbox", _flaky(seen, None)) == {"schools": 1, "failed": 1}
    assert seen == [A, B]


def test_US_103_AC2_a_failed_pull_still_ends_due_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.breakglass import tasks

    _fan_out(monkeypatch, tasks)
    swept: list[uuid.UUID] = []

    def pull(tenant_id: uuid.UUID) -> Any:
        raise RuntimeError("control plane unreachable")

    def sweep(tenant_id: uuid.UUID) -> dict[str, int]:
        swept.append(tenant_id)
        return {"expired": 1, "stale_requests": 0, "reported": 0}

    monkeypatch.setattr("app.breakglass.service.sync_school", pull)
    monkeypatch.setattr("app.breakglass.service.sweep_school", sweep)
    assert tasks.sweep_all() == {"tenants": 2, "received": 0, "expired": 2, "failed": 2}
    assert swept == [A, B]


def test_FR_DPDP_032_a_failed_pull_still_sends_breach_reminders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.privacy import breach_tasks

    _fan_out(monkeypatch, breach_tasks)
    reminded: list[uuid.UUID] = []

    def pull(tenant_id: uuid.UUID) -> int:
        raise RuntimeError("control plane unreachable")

    def remind(tenant_id: uuid.UUID) -> int:
        reminded.append(tenant_id)
        return 1

    monkeypatch.setattr("app.privacy.breach_sync.sync_school", pull)
    monkeypatch.setattr("app.privacy.breach_service.send_reminders", remind)
    assert breach_tasks.sweep_all() == {"tenants": 2, "received": 0, "reminders": 2, "failed": 2}
    assert reminded == [A, B]


def test_FR_EW_017_retention_purge_runs_for_suspended_schools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/04 §6 and docs/16 §5.5: retention purges keep running while a school is suspended;
    the early-warning rules and reminders stay with active schools."""
    from app.insights import tasks
    from app.tenancy import service as tenancy

    asked: list[tuple[str, ...]] = []

    def ids(_: Any, statuses: tuple[str, ...]) -> list[uuid.UUID]:
        asked.append(tuple(statuses))
        return []

    monkeypatch.setattr(tenancy, "list_tenant_ids", ids)
    monkeypatch.setattr(tasks, "context_free_session", _session)
    tasks.purge_expired()
    tasks.send_flag_reminders()
    tasks.evaluate_all()
    assert asked == [("active", "suspended", "offboarding"), ("active",), ("active",)]


def test_FR_MSG_023_message_retention_runs_for_suspended_schools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import service as messaging
    from app.messaging import tasks
    from app.messaging.sends import service as sends
    from app.tenancy import service as tenancy

    asked: list[tuple[str, ...]] = []
    purged: list[uuid.UUID] = []

    def ids(_: Any, statuses: tuple[str, ...]) -> list[uuid.UUID]:
        asked.append(tuple(statuses))
        return [A, B]

    def purge(tenant_id: uuid.UUID) -> dict[str, int]:
        purged.append(tenant_id)
        return {}

    monkeypatch.setattr(tenancy, "list_tenant_ids", ids)
    monkeypatch.setattr(tasks, "context_free_session", _session)
    monkeypatch.setattr(messaging, "available", lambda: True)
    monkeypatch.setattr(messaging, "live_tenant_ids", lambda: [])
    monkeypatch.setattr(messaging, "purge_expired", purge)
    monkeypatch.setattr(sends, "purge_expired", lambda _t: {})
    assert tasks.purge_expired() == {"schools": 2, "failed": 0}
    assert asked == [("active", "suspended", "offboarding")]
    assert purged == [A, B]


def test_FR_PAY_012_inbox_retention_runs_for_suspended_schools_and_after_a_failed_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.fees import tasks_online
    from app.tenancy import service as tenancy

    asked: list[tuple[str, ...]] = []
    purged: list[uuid.UUID] = []

    def ids(_: Any, statuses: tuple[str, ...]) -> list[uuid.UUID]:
        asked.append(tuple(statuses))
        return [A, B]

    def refresh(tenant_id: uuid.UUID) -> str:
        raise RuntimeError("provider down")

    def purge(tenant_id: uuid.UUID) -> int:
        purged.append(tenant_id)
        return 0

    monkeypatch.setattr(tenancy, "list_tenant_ids", ids)
    monkeypatch.setattr(tasks_online, "context_free_session", _session)
    monkeypatch.setattr("app.fees.online.connected_tenant_ids", lambda: [A])
    monkeypatch.setattr("app.fees.online.refresh_tokens", refresh)
    monkeypatch.setattr("app.fees.online.purge_events", purge)
    out = tasks_online.payments_daily()
    assert out == {"schools": 0, "purged_schools": 2, "failed": 1}
    assert asked == [("active", "suspended", "offboarding")]
    assert purged == [A, B]
