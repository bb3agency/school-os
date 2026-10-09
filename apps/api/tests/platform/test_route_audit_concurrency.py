"""Control-plane lost updates and double submits (audit 2026-10-06 R-04, R-05; docs/09 §2,
docs/16 §8). Synthetic data only.

- The price override, the AI bundle and feature flags are replaced by PUT (and cleared by
  DELETE). Two operators editing at once silently overwrote each other; a stale form could turn
  a kill-switch flag back on. They now honour ``If-Match`` like the other platform updates
  (412 when stale; required since AA-13, see test_if_match_required.py); the response carries
  the ETag.
- Break-glass requests and support-ticket messages were created on every POST: a retried
  request made a second request or message. They now accept an optional ``Idempotency-Key``
  (a replay answers with the same resource).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session

from .conftest import Api, MakeOperator, Operator, billing_account_payload, provision_payload

pytestmark = pytest.mark.db


def _school(api: Api, op: Operator, plan_id: uuid.UUID) -> dict[str, Any]:
    body = provision_payload(plan_id)
    body["billing_account"] = billing_account_payload()
    res = api.call("POST", "/tenants", op, json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def test_FR_PLT_013_a_stale_price_override_is_refused(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    billing = make_operator("billing_admin")
    sub = _school(api, owner, make_plan())["subscription_id"]
    got = api.call("GET", f"/subscriptions/{sub}", billing)
    stale = got.headers["ETag"]
    body = {"price_override_inr": "3999.00", "reason": "Pilot school, agreed in writing (SS/1)"}
    first = api.call(
        "PUT",
        f"/subscriptions/{sub}/price-override",
        billing,
        json=body,
        headers={"If-Match": stale},
    )
    assert first.status_code == 200, first.text
    assert first.headers["ETag"] != stale
    second = api.call(
        "PUT",
        f"/subscriptions/{sub}/price-override",
        billing,
        json={**body, "price_override_inr": "1.00"},
        headers={"If-Match": stale},
    )
    assert second.status_code == 412, second.text
    clear = api.call(
        "DELETE", f"/subscriptions/{sub}/price-override", billing, headers={"If-Match": stale}
    )
    assert clear.status_code == 412, clear.text
    now = api.call("GET", f"/subscriptions/{sub}", billing).json()
    assert now["price_override_inr"] == "3999.00"
    # Without If-Match the routes are refused (audit 2026-10-04 AA-13; the UI sends it).
    plain = api.call("DELETE", f"/subscriptions/{sub}/price-override", billing)
    assert (plain.status_code, plain.json()["code"]) == (400, "if_match_required")
    current = api.call(
        "DELETE",
        f"/subscriptions/{sub}/price-override",
        billing,
        headers={"If-Match": first.headers["ETag"]},
    )
    assert current.status_code == 200, current.text


def test_FR_PLT_013_a_stale_ai_bundle_change_is_refused(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    billing = make_operator("billing_admin")
    sub = _school(api, owner, make_plan())["subscription_id"]
    stale = api.call("GET", f"/subscriptions/{sub}", billing).headers["ETag"]
    api.call(
        "PUT",
        f"/subscriptions/{sub}/price-override",
        billing,
        json={"price_override_inr": "10.00", "reason": "Moves the version on (SS/2)"},
        headers={"If-Match": stale},
    )
    res = api.call(
        "DELETE", f"/subscriptions/{sub}/ai-bundle", billing, headers={"If-Match": stale}
    )
    assert res.status_code == 412, res.text
    put = api.call(
        "PUT",
        f"/subscriptions/{sub}/ai-bundle",
        billing,
        json={"ai_bundle_id": str(uuid.uuid4())},
        headers={"If-Match": stale},
    )
    assert put.status_code == 412, put.text


def test_FR_PLT_022_a_stale_flag_edit_does_not_turn_a_flag_back_on(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    engineer = make_operator("platform_engineer")
    tid = _school(api, owner, make_plan())["tenant_id"]
    key = f"synthetic.flag_{uuid.uuid4().hex[:6]}"
    on = api.call("PUT", f"/flags/{key}", engineer, json={"enabled": True})
    assert on.status_code == 200, on.text
    stale = on.headers["ETag"]
    off = api.call(
        "PUT", f"/flags/{key}", engineer, json={"enabled": False}, headers={"If-Match": stale}
    )
    assert off.status_code == 200, off.text
    again = api.call(
        "PUT", f"/flags/{key}", engineer, json={"enabled": True}, headers={"If-Match": stale}
    )
    assert again.status_code == 412, again.text
    override = api.call("PUT", f"/flags/{key}/tenants/{tid}", engineer, json={"enabled": False})
    assert override.status_code == 200, override.text
    o_stale = override.headers["ETag"]
    api.call(
        "PUT",
        f"/flags/{key}/tenants/{tid}",
        engineer,
        json={"enabled": True},
        headers={"If-Match": o_stale},
    )
    stale_override = api.call(
        "PUT",
        f"/flags/{key}/tenants/{tid}",
        engineer,
        json={"enabled": False},
        headers={"If-Match": o_stale},
    )
    assert stale_override.status_code == 412, stale_override.text
    # A flag that does not exist yet cannot match an ETag.
    new_key = f"synthetic.flag_{uuid.uuid4().hex[:6]}"
    fresh = api.call(
        "PUT", f"/flags/{new_key}", engineer, json={"enabled": True}, headers={"If-Match": '"1"'}
    )
    assert fresh.status_code == 412, fresh.text


def test_SEC_029_a_retried_break_glass_request_is_not_created_twice(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    tid = _school(api, owner, make_plan())["tenant_id"]
    body = {
        "tenant_id": tid,
        "reason_code": "support_request",
        "reason": "The school asked for help with an import that failed",
        "scope": {},
        "duration_minutes": 60,
    }
    one = api.call("POST", "/break-glass-requests", agent, json=body, idem="bg-retry-0001")
    two = api.call("POST", "/break-glass-requests", agent, json=body, idem="bg-retry-0001")
    assert one.status_code == two.status_code == 201, two.text
    assert one.json()["id"] == two.json()["id"]
    other = api.call(
        "POST",
        "/break-glass-requests",
        agent,
        json={**body, "duration_minutes": 30},
        idem="bg-retry-0001",
    )
    assert other.status_code == 422, other.text
    plain = api.call("POST", "/break-glass-requests", agent, json=body, idem=None)
    assert plain.status_code == 201, plain.text
    assert plain.json()["id"] != one.json()["id"]


def _message_count(ticket_id: str) -> int:
    with platform_session() as s:
        return int(
            s.execute(
                text("SELECT count(*) FROM platform.support_messages WHERE ticket_id = :t"),
                {"t": ticket_id},
            ).scalar_one()
        )


def test_FR_PLT_027_a_retried_operator_reply_is_not_posted_twice(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    tid = _school(api, owner, make_plan())["tenant_id"]
    ticket = api.call(
        "POST",
        "/support/tickets",
        agent,
        json={
            "tenant_id": tid,
            "channel": "email",
            "category": "other",
            "subject": "Synthetic",
            "body": "Hello",
        },
    )
    assert ticket.status_code == 201, ticket.text
    tkt = ticket.json()["id"]
    before = _message_count(tkt)
    path = f"/support/tickets/{tkt}/messages"
    one = api.call("POST", path, agent, json={"body": "We are on it"}, idem="msg-retry-0001")
    two = api.call("POST", path, agent, json={"body": "We are on it"}, idem="msg-retry-0001")
    assert one.status_code == two.status_code == 201, two.text
    assert _message_count(tkt) == before + 1
    plain = api.call("POST", path, agent, json={"body": "Second note"}, idem=None)
    assert plain.status_code == 201, plain.text
    assert _message_count(tkt) == before + 2


def test_FR_PLT_027_a_retried_school_reply_is_not_posted_twice(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    body = provision_payload(make_plan())
    tid = api.call("POST", "/tenants", owner, json=body).json()["tenant_id"]
    api.call("POST", f"/tenants/{tid}/activate", owner)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
    subject = body["owner"]["idp_subject"]

    def post(path: str, payload: dict[str, Any], key: str | None) -> Any:
        headers = api.headers(None, tenant_subject=subject)
        if key is not None:
            headers["Idempotency-Key"] = key
        return api.client.post(path, headers=headers, json=payload)

    ticket = post(
        "/api/v1/support/tickets",
        {"category": "other", "subject": "Synthetic", "body": "Hello"},
        "school-ticket-0001",
    )
    assert ticket.status_code == 201, ticket.text
    tkt = ticket.json()["id"]
    before = _message_count(tkt)
    path = f"/api/v1/support/tickets/{tkt}/messages"
    one = post(path, {"body": "Any news?"}, "school-reply-0001")
    two = post(path, {"body": "Any news?"}, "school-reply-0001")
    assert one.status_code == two.status_code == 200, two.text
    assert two.headers.get("Idempotent-Replayed") == "true"
    assert _message_count(tkt) == before + 1
    assert post(path, {"body": "Another"}, None).status_code == 200
    assert _message_count(tkt) == before + 2
