"""The school owner's invitation at provisioning goes by email (FR-PLT-002, ADR-0024, lead
decision D62; docs/16 §5.4, docs/10 §5.2). Synthetic schools and addresses only.

The control plane never reads tenant data: in the invite's platform transaction it queues the
school-chain event ``tenant.owner_invited`` (IDs only). The tenant-side deliverer
(``platform.tenant_audit``) writes it into the school's own chain exactly once and, in that same
tenant transaction, the notifications hook queues the ordinary staff invitation email
(``request_email``: IDs-only outbox event + audit). The worker sends it through the configured
provider (the in-memory fake here).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.audit import service as audit
from app.core.config import EmailProviderKind, Settings
from app.notifications import email, service, tasks
from app.platform import tenant_audit

from .conftest import Api, Operator, provision_payload

pytestmark = pytest.mark.db

ON = Settings(
    email_provider=EmailProviderKind.FAKE,
    email_from="SchoolOS <no-reply@example.test>",
    email_app_url="https://app.example.test",
)
OWNER_EVENT = "tenant.owner_invited"


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> email.FakeEmailSender:
    monkeypatch.setattr(service, "get_settings", lambda: ON)
    monkeypatch.setattr(email, "get_settings", lambda: ON)
    fake = email.fake_sender()
    fake.clear()
    return fake


@pytest.fixture
def body(make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    payload = provision_payload(make_plan())
    payload["owner"]["email"] = f"owner.{uuid.uuid4().hex[:10]}@school.example.test"
    payload["owner"]["language"] = "en"
    return payload


@pytest.fixture
def restore_hooks() -> Iterator[None]:
    saved = list(audit.platform_event_hooks(OWNER_EVENT))
    yield
    audit.PLATFORM_EVENT_HOOKS[OWNER_EVENT] = saved


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _tenant_id(admin: Engine, code: str) -> uuid.UUID:
    tid: uuid.UUID = _scalar(admin, "SELECT id FROM core.tenants WHERE code = :c", c=code)
    return tid


def _owner_user(admin: Engine, tid: uuid.UUID) -> uuid.UUID:
    uid: uuid.UUID = _scalar(
        admin, "SELECT user_id FROM core.memberships WHERE tenant_id = :t", t=tid
    )
    return uid


def _email_events(admin: Engine, tid: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tid, "e": service.EMAIL_EVENT},
        )
        return [dict(r[0]) for r in rows]


def _school_actions(admin: Engine, tid: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text("SELECT action FROM audit.events WHERE tenant_id = :t ORDER BY seq"),
                {"t": tid},
            ).scalars()
        )


def _send(tid: uuid.UUID, payload: dict[str, Any]) -> Any:
    return tasks.send_email.apply(
        kwargs={"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()


def _provision(api: Api, op: Operator, body: dict[str, Any]) -> uuid.UUID:
    res = api.call("POST", "/tenants", op, json=body)
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["tenant_id"])


def test_FR_PLT_002_provisioning_emails_the_owner_invitation_once(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    tid = _provision(api, owner, body)
    user_id = _owner_user(admin_engine, tid)

    (payload,) = _email_events(admin_engine, tid)
    assert payload == {
        "message_id": payload["message_id"],
        "user_id": str(user_id),
        "template_key": service.INVITATION_TEMPLATE,
    }
    with admin_engine.connect() as c:
        requested = c.execute(
            text(
                "SELECT actor_type, resource_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND action = 'notification.email_requested'"
            ),
            {"t": tid},
        ).one()
        invited = c.execute(
            text(
                "SELECT actor_type, resource_type, resource_id, summary FROM audit.events "
                "WHERE tenant_id = :t AND action = :a"
            ),
            {"t": tid, "a": OWNER_EVENT},
        ).one()
    assert requested.actor_type == "system"
    assert requested.resource_id == user_id
    assert requested.summary == {
        "message_id": payload["message_id"],
        "template_key": service.INVITATION_TEMPLATE,
        "via": "owner_invite",
    }
    assert (invited.actor_type, invited.resource_type, invited.resource_id) == (
        "platform",
        "user",
        user_id,
    )
    assert set(invited.summary) == {"membership_id", "owner_role_assigned", "platform_event_id"}
    assert email_on.sent == [], "nothing is sent inside the request"

    assert _send(tid, payload) == "sent"
    (message,) = email_on.sent
    assert message.to == body["owner"]["email"]
    assert message.template_key == service.INVITATION_TEMPLATE
    # The ordinary staff invitation: the school's name, the sign-in link and the expiry date,
    # nothing else (no student data; the template takes exactly these three values).
    assert email.email_catalog()[message.template_key].params == {
        "school",
        "sign_in_url",
        "expires_on",
    }
    assert body["school_name"] in message.text
    assert "https://app.example.test/" in message.text

    out = capsys.readouterr()
    logs = out.out + out.err
    assert "notifications.email.sent" in logs
    for secret in (body["owner"]["email"], body["owner"]["display_name"], message.subject):
        assert secret not in logs


def test_FR_PLT_002_resume_and_redelivery_do_not_email_twice(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tenancy import service as tenancy

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("synthetic KMS outage")

    with monkeypatch.context() as m:
        m.setattr(tenancy, "initialise_tenant", boom)
        assert api.call("POST", "/tenants", owner, json=body).status_code == 503
    tid = _tenant_id(admin_engine, body["code"])
    assert _email_events(admin_engine, tid) == []

    resumed = api.call("POST", f"/tenants/{tid}/provisioning:resume", owner)
    assert resumed.status_code == 200, resumed.text
    assert len(_email_events(admin_engine, tid)) == 1
    # The same request again replays the finished run: no second invitation email.
    assert api.call("POST", "/tenants", owner, json=body).status_code in {200, 201}
    # A deliverer that crashed after the school's commit but before marking the copy delivered
    # finds the event already in the school's chain and does not queue the email again.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE platform.tenant_audit_outbox SET delivered_at = NULL "
                "WHERE tenant_id = :t AND action = :a"
            ),
            {"t": tid, "a": OWNER_EVENT},
        )
    result = tenant_audit.deliver_pending(tenant_id=tid)
    assert result.already_present >= 1
    assert len(_email_events(admin_engine, tid)) == 1
    assert _school_actions(admin_engine, tid).count(OWNER_EVENT) == 1
    assert email_on.sent == []


def test_FR_PLT_002_failure_to_queue_the_email_does_not_break_provisioning(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_a: object, **_k: object) -> bool:
        raise RuntimeError("synthetic outbox failure")

    with monkeypatch.context() as m:
        m.setattr(service, "request_email", broken)
        tid = _provision(api, owner, body)
    # The school exists and its run is finished; the owner event waits undelivered (all or
    # nothing with its email request) and the scheduled deliverer retries it.
    assert _email_events(admin_engine, tid) == []
    assert OWNER_EVENT not in _school_actions(admin_engine, tid)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT delivered_at, attempts, last_error FROM platform.tenant_audit_outbox "
                "WHERE tenant_id = :t AND action = :a"
            ),
            {"t": tid, "a": OWNER_EVENT},
        ).one()
    assert row.delivered_at is None
    assert row.attempts >= 1
    assert row.last_error == "delivery_failed"

    tenant_audit.deliver_pending(tenant_id=tid)
    assert len(_email_events(admin_engine, tid)) == 1
    assert _school_actions(admin_engine, tid).count(OWNER_EVENT) == 1


def test_FR_PLT_002_provider_outage_is_retried_by_the_send_task(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = _provision(api, owner, body)
    (payload,) = _email_events(admin_engine, tid)

    real = email_on.send
    attempts: list[int] = []

    def flaky(message: email.EmailMessage) -> str:
        attempts.append(1)
        if len(attempts) <= 2:
            raise email.EmailUnavailable("ServiceUnavailable")
        return real(message)

    monkeypatch.setattr(email_on, "send", flaky)
    # The send task retries a temporary provider failure with backoff (eager here).
    assert _send(tid, payload) == "sent"
    assert len(attempts) == 3
    assert len(email_on.sent) == 1


def test_FR_PLT_002_no_email_is_queued_while_email_is_off(
    api: Api, owner: Operator, body: dict[str, Any], admin_engine: Engine
) -> None:
    tid = _provision(api, owner, body)
    assert _email_events(admin_engine, tid) == []
    assert _school_actions(admin_engine, tid).count(OWNER_EVENT) == 1


def test_FR_PLT_002_owner_event_waits_for_a_process_with_the_email_hook(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    restore_hooks: None,
) -> None:
    """Fail closed: a process without the notifications hook never delivers the owner event
    (that would lose the email); the worker, which has it, delivers it later."""
    saved = list(audit.platform_event_hooks(OWNER_EVENT))
    audit.PLATFORM_EVENT_HOOKS[OWNER_EVENT] = []
    tid = _provision(api, owner, body)
    with admin_engine.connect() as c:
        last_error = c.execute(
            text(
                "SELECT last_error FROM platform.tenant_audit_outbox "
                "WHERE tenant_id = :t AND action = :a"
            ),
            {"t": tid, "a": OWNER_EVENT},
        ).scalar()
    assert last_error == "hook_missing"
    assert _email_events(admin_engine, tid) == []

    audit.PLATFORM_EVENT_HOOKS[OWNER_EVENT] = saved
    tenant_audit.deliver_pending(tenant_id=tid)
    assert len(_email_events(admin_engine, tid)) == 1


def test_FR_PLT_002_operator_resend_emails_the_invited_owner_again(  # noqa: PLR0917 - fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = _provision(api, owner, body)
    user_id = _owner_user(admin_engine, tid)
    assert len(_email_events(admin_engine, tid)) == 1

    # Within the staff invitation cool-down the re-send is recorded but sends nothing.
    monkeypatch.setattr(service, "_cooldown", lambda _membership_id: False)
    res = api.call("POST", f"/tenants/{tid}/owner-invite:resend", owner)
    assert res.status_code == 202, res.text
    assert len(_email_events(admin_engine, tid)) == 1

    monkeypatch.setattr(service, "_cooldown", lambda _membership_id: True)
    res = api.call("POST", f"/tenants/{tid}/owner-invite:resend", owner)
    assert res.status_code == 202, res.text
    events = _email_events(admin_engine, tid)
    assert len(events) == 2
    assert events[-1]["user_id"] == str(user_id)
    with admin_engine.connect() as c:
        vias: list[str] = list(
            c.execute(
                text(
                    "SELECT summary ->> 'via' FROM audit.events WHERE tenant_id = :t "
                    "AND action = 'notification.email_requested' ORDER BY seq"
                ),
                {"t": tid},
            ).scalars()
        )
    assert vias == ["owner_invite", "owner_resend"]
    assert _school_actions(admin_engine, tid).count("tenant.owner_invite_resent") == 2


def test_D62_owner_event_names_are_shared_and_the_hooks_are_registered() -> None:
    assert service.OWNER_INVITED_EVENT == tenant_audit.OWNER_INVITED == OWNER_EVENT
    assert service.OWNER_INVITE_RESENT_EVENT == tenant_audit.OWNER_INVITE_RESENT
    assert {OWNER_EVENT, tenant_audit.OWNER_INVITE_RESENT} == tenant_audit.HOOKED_ACTIONS
    assert service.queue_owner_invitation_email in audit.platform_event_hooks(OWNER_EVENT)
    assert service.queue_owner_invitation_resend in audit.platform_event_hooks(
        tenant_audit.OWNER_INVITE_RESENT
    )


RESEND_EVENT = "tenant.owner_invite_resent"


def _resend(api: Api, op: Operator, tid: uuid.UUID) -> Any:
    return api.call("POST", f"/tenants/{tid}/owner-invite:resend", op)


def _platform_actions(admin: Engine, tid: uuid.UUID, action: str) -> int:
    n: int = _scalar(
        admin,
        "SELECT count(*) FROM platform.audit_events WHERE subject_tenant_id = :t AND action = :a",
        t=tid,
        a=action,
    )
    return n


@pytest.mark.parametrize("status", ["active", "suspended"])
def test_FR_PLT_002_D65_resend_after_go_live_emails_the_still_invited_owner(  # noqa: PLR0917 - fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    tid = _provision(api, owner, body)
    user_id = _owner_user(admin_engine, tid)
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    if status == "suspended":
        hold = {"reason": "Security incident reported by the school"}
        res = api.call("POST", f"/tenants/{tid}/suspend", owner, json=hold)
        assert res.status_code == 200, res.text

    monkeypatch.setattr(service, "_cooldown", lambda _membership_id: True)
    res = _resend(api, owner, tid)
    assert res.status_code == 202, res.text
    assert res.json() == {"status": "queued"}
    events = _email_events(admin_engine, tid)
    assert len(events) == 2
    assert events[-1]["user_id"] == str(user_id)
    assert _school_actions(admin_engine, tid).count(RESEND_EVENT) == 1
    assert _platform_actions(admin_engine, tid, "tenant.owner_invite_sent") == 1

    # The cool-down still applies after go-live: recorded on both sides, nothing sent.
    monkeypatch.setattr(service, "_cooldown", lambda _membership_id: False)
    assert _resend(api, owner, tid).status_code == 202
    assert len(_email_events(admin_engine, tid)) == 2
    assert _school_actions(admin_engine, tid).count(RESEND_EVENT) == 2
    assert _platform_actions(admin_engine, tid, "tenant.owner_invite_sent") == 2


def test_FR_PLT_002_D65_resend_sends_nothing_once_the_owner_accepted(  # noqa: PLR0917 - fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tid = _provision(api, owner, body)
    assert api.call("POST", f"/tenants/{tid}/activate", owner).status_code == 200
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
    monkeypatch.setattr(service, "_cooldown", lambda _membership_id: True)
    capsys.readouterr()

    res = _resend(api, owner, tid)
    # The control plane cannot see the membership: it accepts and queues; the school side
    # decides and sends nothing, logging an outcome code only.
    assert res.status_code == 202, res.text
    assert res.json() == {"status": "queued"}
    assert len(_email_events(admin_engine, tid)) == 1
    assert _school_actions(admin_engine, tid).count(RESEND_EVENT) == 1
    assert _platform_actions(admin_engine, tid, "tenant.owner_invite_sent") == 1
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "owner_not_invited" in logs
    for secret in (body["owner"]["email"], body["owner"]["display_name"]):
        assert secret not in logs


@pytest.mark.parametrize("status", ["offboarding", "deleted"])
def test_FR_PLT_002_D65_resend_is_refused_once_the_school_is_closing(  # noqa: PLR0917 - fixtures
    api: Api,
    owner: Operator,
    body: dict[str, Any],
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    status: str,
) -> None:
    tid = _provision(api, owner, body)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE platform.deployments SET tenant_status = :s WHERE tenant_id = :t"),
            {"s": status, "t": tid},
        )
    res = _resend(api, owner, tid)
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state")
    assert _platform_actions(admin_engine, tid, "tenant.owner_invite_sent") == 0
    assert RESEND_EVENT not in _school_actions(admin_engine, tid)
    assert len(_email_events(admin_engine, tid)) == 1
