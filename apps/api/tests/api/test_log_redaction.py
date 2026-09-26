"""No personal data in logs during API calls (SEC-008, invariant 5, docs/12 §4.6)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]

NAME = "Kommineni Venkata Lakshmi Synthetica"
EMAIL = "lakshmi.synthetica@example.test"
PHONE_LIKE = "9876543210"


def test_SEC_008_api_calls_do_not_log_names_or_emails(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = world.person("owner")
    subject = f"sub-{uuid.uuid4().hex}"
    capsys.readouterr()
    invited = api.call(
        owner,
        "POST",
        "/api/v1/users",
        json={
            "idp_subject": subject,
            "display_name": NAME,
            "email": EMAIL,
            "roles": ["teacher"],
        },
    )
    assert invited.status_code == 201, invited.text
    api.call(owner, "GET", "/api/v1/users", params={"limit": 200})
    api.call(owner, "GET", f"/api/v1/users/{invited.json()['id']}")
    # Validation errors must not echo or log submitted values either.
    api.call(
        owner,
        "POST",
        "/api/v1/users",
        json={"idp_subject": subject, "display_name": NAME, "email": PHONE_LIKE, "roles": []},
    )
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"], display_name=NAME)
    api.call(person, "GET", "/api/v1/me")
    api.call(person, "POST", "/api/v1/me/login-event")
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    for secret in (NAME, "Venkata", EMAIL, "synthetica@", PHONE_LIKE, subject):
        assert secret not in logs, secret
