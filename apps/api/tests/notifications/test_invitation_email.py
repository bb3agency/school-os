"""Staff invitation emails over the API and the worker (US-102, docs/03 §5 Email via SES,
FR-IAM-013, SEC-005, SEC-008; invariants 5, 7). Synthetic schools and addresses only.

Queued in the invite's transaction (IDs-only outbox event + audit), sent by the worker through
the configured provider (the in-memory fake here), resent on request with a cool-down.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.authz.kv import kv_store
from app.core.config import EmailProviderKind, Settings
from app.core.db import tenant_session
from app.core.languages import contains_telugu
from app.notifications import email, service, tasks

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"
ON = Settings(
    email_provider=EmailProviderKind.FAKE,
    email_from="SchoolOS <no-reply@example.test>",
    email_app_url="https://app.example.test",
)


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> email.FakeEmailSender:
    monkeypatch.setattr(service, "get_settings", lambda: ON)
    monkeypatch.setattr(email, "get_settings", lambda: ON)
    fake = email.fake_sender()
    fake.clear()
    return fake


def _address() -> str:
    return f"invitee.{uuid.uuid4().hex[:10]}@example.test"


def _invite(api: Any, who: Any, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "idp_subject": f"sub-{uuid.uuid4().hex}",
        "display_name": "Synthetic Invitee",
        "email": _address(),
        "preferred_language": "te",
        "roles": ["teacher"],
        "scopes": [],
    }
    body.update(overrides)
    res = api.call(who, "POST", USERS, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _email_events(admin: Engine, tenant_id: uuid.UUID, user_id: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                "ORDER BY created_at"
            ),
            {"t": tenant_id, "e": service.EMAIL_EVENT},
        )
        return [dict(r[0]) for r in rows if dict(r[0])["user_id"] == user_id]


def _run(tenant_id: uuid.UUID, payload: dict[str, Any]) -> Any:
    return tasks.send_email.apply(
        kwargs={"tenant_id": str(tenant_id), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()


def _resend(api: Any, who: Any, user_id: str, **kw: Any) -> Any:
    return api.call(who, "POST", f"{USERS}/{user_id}/invitation-email", **kw)


def test_US_102_no_email_is_queued_while_email_is_off(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invited = _invite(api, world.person("owner"))
    assert _email_events(admin_engine, world.a.tenant_id, invited["id"]) == []
    res = _resend(api, world.person("owner"), invited["id"])
    assert res.status_code == 409
    assert res.json()["code"] == "email_disabled"


@pytest.mark.usefixtures("telugu_on")  # the invitee prefers Telugu (ADR-0036: switched on here)
def test_US_102_invite_queues_an_ids_only_email_and_the_worker_sends_it(
    world: Any,
    api: Any,
    admin_engine: Engine,
    email_on: email.FakeEmailSender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    address = _address()
    capsys.readouterr()
    invited = _invite(api, world.person("office_admin"), email=address)
    (payload,) = _email_events(admin_engine, world.a.tenant_id, invited["id"])
    assert set(payload) == {"message_id", "user_id", "template_key"}
    assert payload["template_key"] == "invitation.staff"
    audited = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "notification.email_requested")
        if str(e["resource_id"]) == invited["id"]
    ]
    assert [e["summary"] for e in audited] == [
        {"message_id": payload["message_id"], "template_key": "invitation.staff", "via": "invite"}
    ]
    assert email_on.sent == [], "nothing is sent inside the request"

    assert _run(world.a.tenant_id, payload) == "sent"
    (message,) = email_on.sent
    assert message.to == address
    assert message.language == "te"
    assert "https://app.example.test/te" in message.text
    expires = (dt.datetime.fromisoformat(invited["created_at"]) + dt.timedelta(days=30)).astimezone(
        service.IST
    )
    assert expires.strftime("%d/%m/%Y") in message.text
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "notifications.email.sent" in logs
    for secret in (address, "Synthetic Invitee", message.subject):
        assert secret not in logs


def test_ADR_0036_invitation_email_is_english_while_telugu_is_hidden(
    world: Any, api: Any, admin_engine: Engine, email_on: email.FakeEmailSender
) -> None:
    """A person whose profile says Telugu gets the English email and an /en sign-in link."""
    invited = _invite(api, world.person("office_admin"), preferred_language="te")
    (payload,) = _email_events(admin_engine, world.a.tenant_id, invited["id"])
    assert _run(world.a.tenant_id, payload) == "sent"
    (message,) = email_on.sent
    assert message.language == "en"
    assert "https://app.example.test/en" in message.text
    assert "https://app.example.test/te" not in message.text
    assert not contains_telugu(message.subject + message.text)


def test_US_102_worker_skips_accepted_expired_and_unknown_invitations(
    world: Any, api: Any, admin_engine: Engine, email_on: email.FakeEmailSender
) -> None:
    tenant = world.a.tenant_id
    invited = _invite(api, world.person("owner"))
    user_id = uuid.UUID(invited["id"])
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=31)
    assert (
        service.send_requested_email(tenant, user_id, "invitation.staff", now=later)
        == "invitation_expired"
    )
    assert service.send_requested_email(world.b.tenant_id, user_id, "invitation.staff") == (
        "no_member"
    )
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE id = :m"),
            {"m": invited["membership_id"]},
        )
    assert service.send_requested_email(tenant, user_id, "invitation.staff") == "not_invited"
    assert email_on.sent == []


def test_US_102_worker_retries_while_the_provider_is_unavailable(
    world: Any, api: Any, admin_engine: Engine, email_on: email.FakeEmailSender
) -> None:
    invited = _invite(api, world.person("owner"))

    class Down:
        def send(self, message: email.EmailMessage) -> str:
            raise email.EmailUnavailable("TooManyRequestsException")

    with pytest.raises(email.EmailUnavailable):
        service.send_requested_email(
            world.a.tenant_id, uuid.UUID(invited["id"]), "invitation.staff", sender=Down()
        )


def test_US_102_resend_has_a_cool_down_and_is_audited(
    world: Any, api: Any, admin_engine: Engine, email_on: email.FakeEmailSender
) -> None:
    owner = world.person("owner")
    invited = _invite(api, owner)
    first = _resend(api, owner, invited["id"])
    assert first.status_code == 429, "the invite itself started the cool-down"
    kv_store().delete(f"sos:rl:invitation-email:{invited['membership_id']}")
    res = _resend(api, owner, invited["id"])
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued"
    assert body["user_id"] == invited["id"]
    assert body["membership_id"] == invited["membership_id"]
    assert len(_email_events(admin_engine, world.a.tenant_id, invited["id"])) == 2
    vias = [
        e["summary"]["via"]
        for e in W.audit_events(admin_engine, world.a.tenant_id, "notification.email_requested")
        if str(e["resource_id"]) == invited["id"]
    ]
    assert vias == ["invite", "resend"]
    assert _resend(api, owner, invited["id"]).status_code == 429


def test_US_102_resend_refusals(
    world: Any, api: Any, admin_engine: Engine, email_on: email.FakeEmailSender
) -> None:
    owner = world.person("owner")
    active = world.person("target")
    res = _resend(api, owner, str(active.user_id))
    assert (res.status_code, res.json()["code"]) == (409, "not_invited")
    no_address = _invite(api, owner, email=None)
    res = _resend(api, owner, no_address["id"])
    assert (res.status_code, res.json()["code"]) == (409, "email_missing")
    old = _invite(api, owner)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.memberships SET created_at = now() - interval '31 days' WHERE id = :m"
            ),
            {"m": old["membership_id"]},
        )
    res = _resend(api, owner, old["id"])
    assert (res.status_code, res.json()["code"]) == (409, "invitation_expired")


def test_SEC_001_resend_for_another_schools_person_is_404(
    world: Any, api: Any, email_on: email.FakeEmailSender
) -> None:
    b_person = world.b.people["target"]
    a = _resend(api, world.person("owner"), str(b_person.user_id))
    r = _resend(api, world.person("owner"), str(uuid.uuid4()))
    assert a.status_code == r.status_code == 404
    assert a.json()["code"] == r.json()["code"]


@pytest.mark.parametrize("role", ["teacher", "office_staff", "accountant", "auditor_readonly"])
def test_SEC_003_resend_needs_user_manage(
    world: Any, api: Any, role: str, email_on: email.FakeEmailSender
) -> None:
    invited = _invite(api, world.person("owner"))
    assert _resend(api, world.person(role), invited["id"]).status_code == 403


def test_SEC_005_resend_needs_a_recent_mfa_sign_in(
    world: Any, api: Any, email_on: email.FakeEmailSender
) -> None:
    invited = _invite(api, world.person("owner"))
    res = _resend(api, world.person("owner"), invited["id"], auth_age_s=600)
    assert res.status_code == 428
    assert res.json()["code"] == "step_up_required"


def test_request_email_must_run_in_the_schools_session(
    world: Any, email_on: email.FakeEmailSender
) -> None:
    with tenant_session(world.b.tenant_id) as s, pytest.raises(RuntimeError):
        service.request_email(
            s,
            tenant_id=world.a.tenant_id,
            user_id=uuid.uuid4(),
            template_key="invitation.staff",
            via="invite",
        )
