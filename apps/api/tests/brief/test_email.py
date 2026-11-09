"""Weekly brief e-mail (FA-05; FR-BRF-012; D85: opt-in). Synthetic schools and addresses only.

Only a member who turned the e-mail on gets it; it is queued with the week's in-app notice
(IDs-only outbox event), and the worker sends counts and a sign-in link, never names.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.brief import service as brief
from app.core.config import EmailProviderKind, Settings
from app.core.db import tenant_session
from app.notifications import email, service, tasks

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
ON = Settings(
    email_provider=EmailProviderKind.FAKE,
    email_from="SchoolOS <no-reply@example.test>",
    email_app_url="https://app.example.test",
)


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> email.FakeEmailSender:
    monkeypatch.setattr(service, "get_settings", lambda: ON)
    monkeypatch.setattr(email, "get_settings", lambda: ON)
    monkeypatch.setattr(brief, "get_settings", lambda: ON)
    fake = email.fake_sender()
    fake.clear()
    return fake


def test_FR_BRF_012_only_members_who_opted_in_get_the_email(
    admin_engine: Engine, api: Any, email_on: email.FakeEmailSender
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"], email="owner.brief@example.test")
    principal = W.add_member(admin_engine, tid, ["principal"], email="principal.brief@example.test")
    res = api.call(owner, "PUT", "/api/v1/brief/preferences", json={"in_app": False, "email": True})
    assert res.status_code == 200, res.text
    assert res.json()["email_available"] is True
    with tenant_session(tid) as db:
        assert brief.build_week(db, today=dt.date(2026, 11, 9)) == 2
    with admin_engine.connect() as c:
        payloads = [
            dict(r[0])
            for r in c.execute(
                text("SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e"),
                {"t": tid, "e": service.EMAIL_EVENT},
            )
        ]
    (payload,) = payloads
    assert payload["user_id"] == str(owner.user_id)
    assert payload["template_key"] == brief.EMAIL_TEMPLATE
    assert set(payload) == {"message_id", "user_id", "template_key", "notification_id"}
    tasks.send_email.apply(
        kwargs={"tenant_id": str(tid), "event_id": str(uuid.uuid4()), "payload": payload}
    ).get()
    (message,) = email_on.sent
    assert message.to == "owner.brief@example.test"
    assert "weekly school brief" in message.subject
    assert "https://app.example.test/" in message.text
    for name in (owner.display_name, principal.display_name):
        assert name not in message.text
