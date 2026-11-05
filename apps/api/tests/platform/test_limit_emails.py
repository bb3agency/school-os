"""E-mail at 100 % of the AI answer bundle or a plan limit (docs/16 §5.10, §19 Q16; FR-PLT-020,
FR-PLT-021, FR-NOT-001; lead decision D70 after D57 and D62; ADR-0038).

The hourly school-side pulls (``app.notifications.quota_sync``) also e-mail the school's owner
and principal (active members with an address) when a 100 % alert is new for them, through the
existing e-mail path (IDs-only outbox event, worker renders and sends). Exactly once per kind,
billing period and recipient: the e-mail is queued in the same transaction as the in-app notice
whose ``dedupe_key`` stops repeats, so reruns and the previous-period read send nothing new.
80 % stays in-app only. Nothing is switched off; no e-mail from the control plane; nothing to
parents. Addresses never reach logs. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import EmailProviderKind, Settings
from app.notifications import email, quota_sync, service, tasks
from app.platform.common import today_ist

from .conftest import Api, Operator
from .test_ai_quota_alerts import _check
from .test_plan_limit_alerts import _cross, _period
from .test_school_ai_bundle import _add_member, _answers, _counts_from, _live_school, _set_bundle

pytestmark = pytest.mark.db

ON = Settings(
    email_provider=EmailProviderKind.FAKE,
    email_from="SchoolOS <no-reply@example.test>",
    email_app_url="https://app.example.test",
)
OWNER_ADDRESS = "owner@school.example.test"  # provision_payload's synthetic owner
AI_EMAIL = "billing.ai_answers_used_up"
LIMIT_EMAIL = "billing.plan_limit_reached"


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> email.FakeEmailSender:
    monkeypatch.setattr(service, "get_settings", lambda: ON)
    monkeypatch.setattr(email, "get_settings", lambda: ON)
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: True)
    fake = email.fake_sender()
    fake.clear()
    return fake


def _principal(admin: Engine, tid: uuid.UUID, *, address: str | None) -> None:
    subject = _add_member(admin, tid, "principal")
    if address is not None:
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.users SET email = :e WHERE idp_subject = :s"),
                {"e": address, "s": subject},
            )


def _address() -> str:
    return f"principal.{uuid.uuid4().hex[:10]}@example.test"


def _events(admin: Engine, tid: uuid.UUID, template_key: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tid, "e": service.EMAIL_EVENT},
        )
        return [dict(r[0]) for r in rows if dict(r[0])["template_key"] == template_key]


def _run(tid: uuid.UUID, payload: dict[str, Any]) -> Any:
    return tasks.send_email.apply(
        kwargs={"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()


NewSchool = Callable[[], tuple[uuid.UUID, uuid.UUID]]


@pytest.fixture
def api_owner(api: Api, owner: Operator) -> tuple[Api, Operator]:
    return api, owner


@pytest.fixture
def new_school(
    api_owner: tuple[Api, Operator], make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> NewSchool:
    def make() -> tuple[uuid.UUID, uuid.UUID]:
        tid, sub, _subject = _live_school(*api_owner, make_plan(), admin_engine)
        return tid, sub

    return make


def _ai_bundle(api_owner: tuple[Api, Operator], sub: uuid.UUID) -> tuple[int, dt.date]:
    included = int(_set_bundle(*api_owner, sub, "ai-lite")["included_answers"])
    month = today_ist().replace(day=1)
    _counts_from(sub, month)
    return included, month


def test_Q16_D70_ai_bundle_at_100_emails_owner_and_principal_once(
    api_owner: tuple[Api, Operator],
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tid, sub = new_school()
    included, month = _ai_bundle(api_owner, sub)
    principal = _address()
    _principal(admin_engine, tid, address=principal)
    _add_member(admin_engine, tid, "accountant")  # reads billing, is not e-mailed

    # 80 %: in-app only.
    _answers(tid, month, included * 80 // 100)
    _check(tid, month)
    assert quota_sync.sync_school(tid) == 2
    assert _events(admin_engine, tid, AI_EMAIL) == []

    # 100 %: one e-mail per recipient, queued with IDs only.
    capsys.readouterr()
    _answers(tid, month + dt.timedelta(days=1), included)
    _check(tid, month)
    assert quota_sync.sync_school(tid) == 2
    payloads = _events(admin_engine, tid, AI_EMAIL)
    assert len(payloads) == 2
    assert all(
        set(p) == {"message_id", "user_id", "template_key", "notification_id"} for p in payloads
    )
    assert email_on.sent == [], "nothing is sent inside the pull"

    # Reruns (and the previous-month read) queue nothing new.
    assert quota_sync.sync_school(tid) == 0
    assert quota_sync.sweep_all()["failed"] == 0
    assert len(_events(admin_engine, tid, AI_EMAIL)) == 2

    assert sorted(_run(tid, p) for p in payloads) == ["sent", "sent"]
    assert {m.to for m in email_on.sent} == {OWNER_ADDRESS, principal}
    for message in email_on.sent:
        assert message.template_key == AI_EMAIL
        assert message.language == "en"
        assert str(included) in message.text
        assert "Plan and billing" in message.text
        assert "https://app.example.test/" in message.text
        assert "switched off" in message.text
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "notifications.email.requested" in logs
    assert "notifications.email.sent" in logs
    for secret in (OWNER_ADDRESS, principal, email_on.sent[0].subject):
        assert secret not in logs


def test_FR_PLT_021_D70_plan_limit_at_100_emails_once_and_80_not(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
) -> None:
    tid, sub = new_school()
    _principal(admin_engine, tid, address=_address())
    previous, current = _period(sub)

    _cross(tid, "students", 80, current)
    assert quota_sync.sync_plan_limits(tid) == 2
    assert _events(admin_engine, tid, LIMIT_EMAIL) == []

    _cross(tid, "students", 100, current, used="1000")
    _cross(tid, "storage_gb", 100, previous, used="1000")  # previous period, read by D57
    assert quota_sync.sync_plan_limits(tid) == 4
    assert len(_events(admin_engine, tid, LIMIT_EMAIL)) == 4  # 2 kinds x 2 recipients
    assert quota_sync.sync_plan_limits(tid) == 0
    assert quota_sync.sweep_plan_limits()["failed"] == 0
    payloads = _events(admin_engine, tid, LIMIT_EMAIL)
    assert len(payloads) == 4

    assert [_run(tid, p) for p in payloads] == ["sent"] * 4
    texts = [m.text for m in email_on.sent]
    assert sum("active students" in t for t in texts) == 2
    assert sum("storage" in t for t in texts) == 2
    assert all("nothing is switched off" in t for t in texts)
    assert all("Plan and billing" in t for t in texts)


def test_D70_only_members_with_an_address_are_emailed(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
) -> None:
    tid, sub = new_school()
    _principal(admin_engine, tid, address=None)
    _cross(tid, "documents", 100, _period(sub)[1], used="1000")
    assert quota_sync.sync_plan_limits(tid) == 2  # both see it in-app
    assert len(_events(admin_engine, tid, LIMIT_EMAIL)) == 1  # the owner only


def test_D70_no_email_while_email_is_off(
    new_school: NewSchool,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota_sync, "control_plane_here", lambda settings=None: True)
    tid, sub = new_school()
    _cross(tid, "students", 100, _period(sub)[1], used="1000")
    assert quota_sync.sync_plan_limits(tid) == 1
    assert _events(admin_engine, tid, LIMIT_EMAIL) == []


def test_D70_email_failure_does_not_break_the_in_app_notice(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid, sub = new_school()
    _cross(tid, "students", 100, _period(sub)[1], used="1000")

    def broken(*_a: Any, **_kw: Any) -> bool:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(service, "request_email", broken)
    assert quota_sync.sync_plan_limits(tid) == 1  # the notice is kept
    assert _events(admin_engine, tid, LIMIT_EMAIL) == []
    monkeypatch.undo()


def test_D70_provider_refusal_leaves_the_notice_and_is_not_retried(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid, sub = new_school()
    _cross(tid, "students", 100, _period(sub)[1], used="1000")
    assert quota_sync.sync_plan_limits(tid) == 1
    (payload,) = _events(admin_engine, tid, LIMIT_EMAIL)

    class Refusing:
        def send(self, message: email.EmailMessage) -> str:
            raise email.EmailRejected("MessageRejected")

    monkeypatch.setattr(email, "get_sender", lambda settings=None: Refusing())
    assert _run(tid, payload) == "rejected"
    with admin_engine.connect() as c:
        kept: int = c.execute(
            text(
                "SELECT count(*) FROM ops.notifications WHERE tenant_id = :t AND template_key = :k"
            ),
            {"t": tid, "k": quota_sync.PLAN_LIMIT_TEMPLATE},
        ).scalar_one()
    assert kept == 1


def test_D70_suspended_school_is_emailed_like_its_in_app_notice(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
) -> None:
    """The pull's rule (D57): a suspended school is still told (it can see Plan and billing and
    settle with SchoolOS); offboarding schools are not pulled at all."""
    assert quota_sync.TENANT_STATUSES == ("active", "suspended")
    tid, sub = new_school()
    _cross(tid, "students", 100, _period(sub)[1], used="1000")
    with admin_engine.begin() as c:
        c.execute(text("UPDATE core.tenants SET status = 'suspended' WHERE id = :t"), {"t": tid})
    try:
        quota_sync.sweep_plan_limits()
        assert len(_events(admin_engine, tid, LIMIT_EMAIL)) == 1
    finally:
        with admin_engine.begin() as c:
            c.execute(text("UPDATE core.tenants SET status = 'active' WHERE id = :t"), {"t": tid})


def test_D70_worker_skips_a_member_no_longer_active(
    new_school: NewSchool,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
) -> None:
    tid, sub = new_school()
    _cross(tid, "students", 100, _period(sub)[1], used="1000")
    quota_sync.sync_plan_limits(tid)
    (payload,) = _events(admin_engine, tid, LIMIT_EMAIL)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'suspended' WHERE tenant_id = :t"),
            {"t": tid},
        )
    assert _run(tid, payload) == "not_active"
    assert email_on.sent == []
