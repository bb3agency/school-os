"""Control-plane updates require ``If-Match`` (audit 2026-10-04 AA-13; docs/09 §2, docs/16 §8).

Two operators editing the same plan, billing account, invoice, deployment, announcement,
ticket, price override, AI bundle or flag at once used to overwrite each other silently when
the request carried no ``If-Match``. Every such update now answers ``400 if_match_required``
without it (the same answer as the tenant routes) and changes nothing; with the ETag it was
read with, it succeeds and returns the new ETag. A flag (or a school's override) that does not
exist yet is created without ``If-Match``: there is nothing to overwrite. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.core.db import platform_session
from app.platform import announcements
from app.platform.announcements import InMemoryAnnouncementCache

from .conftest import (
    Api,
    MakeOperator,
    Operator,
    billing_account_payload,
    plan_payload,
    provision_payload,
)

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = InMemoryAnnouncementCache()
    monkeypatch.setattr(announcements, "get_cache", lambda: cache)


def _school(api: Api, op: Operator, plan_id: uuid.UUID) -> dict[str, Any]:
    body = provision_payload(plan_id, start_as="active")
    body["billing_account"] = billing_account_payload()
    res = api.call("POST", "/tenants", op, json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def _refused(res: httpx.Response) -> None:
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "if_match_required"
    assert res.headers["content-type"].startswith("application/problem+json")


def _etag(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}


def _draft_invoice(sub_id: str) -> dict[str, Any]:
    with platform_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT id, version FROM platform.invoices WHERE subscription_id = :s "
                    "AND status = 'draft' ORDER BY period_start LIMIT 1"
                ),
                {"s": sub_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def test_AA_13_plan_billing_invoice_and_deployment_updates_need_if_match(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    engineer = make_operator("platform_engineer")
    school = _school(api, owner, make_plan())
    tid, sub = school["tenant_id"], school["subscription_id"]

    # Draft plan.
    plan = api.call("POST", "/plans", owner, json=plan_payload())
    assert plan.status_code == 201, plan.text
    pid = plan.json()["id"]
    _refused(api.call("PATCH", f"/plans/{pid}", owner, json={"name": "Changed"}))
    assert api.call("GET", f"/plans/{pid}", owner).json()["name"] == "Synthetic Standard"
    ok = api.call(
        "PATCH",
        f"/plans/{pid}",
        owner,
        json={"name": "Changed"},
        headers=_etag(plan.json()["row_version"]),
    )
    assert ok.status_code == 200, ok.text

    # Billing account.
    acct = api.call("GET", f"/tenants/{tid}/billing-account", owner)
    changed = {**billing_account_payload(), "city": "Guntur"}
    _refused(api.call("PUT", f"/tenants/{tid}/billing-account", owner, json=changed))
    assert api.call("GET", f"/tenants/{tid}/billing-account", owner).json()["city"] != "Guntur"
    ok = api.call(
        "PUT",
        f"/tenants/{tid}/billing-account",
        owner,
        json=changed,
        headers={"If-Match": acct.headers["ETag"]},
    )
    assert ok.status_code == 200, ok.text

    # Draft invoice.
    draft = _draft_invoice(sub)
    notes = {"notes": "Synthetic note"}
    _refused(api.call("PATCH", f"/invoices/{draft['id']}", owner, json=notes))
    assert _draft_invoice(sub)["version"] == draft["version"]
    ok = api.call(
        "PATCH", f"/invoices/{draft['id']}", owner, json=notes, headers=_etag(draft["version"])
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"'

    # Deployment.
    dep_id = school["deployment_id"]
    dep = api.call("GET", f"/deployments/{dep_id}", engineer)
    patch = {"target_version": "2.0.0"}
    _refused(api.call("PATCH", f"/deployments/{dep_id}", engineer, json=patch))
    assert api.call("GET", f"/deployments/{dep_id}", engineer).json()["target_version"] is None
    ok = api.call(
        "PATCH",
        f"/deployments/{dep_id}",
        engineer,
        json=patch,
        headers={"If-Match": dep.headers["ETag"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"'


def test_AA_13_announcement_ticket_and_subscription_updates_need_if_match(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    school = _school(api, owner, make_plan())
    tid, sub = school["tenant_id"], school["subscription_id"]

    # Announcement.
    start = dt.datetime(2030, 3, 4, 4, 30, tzinfo=dt.UTC)
    ann_body = {
        "title_en": "Maintenance on Sunday",
        "body_en": "SchoolOS is unavailable from 10:00 to 12:00 IST.",
        "severity": "maintenance",
        "starts_at": start.isoformat(),
        "ends_at": (start + dt.timedelta(hours=2)).isoformat(),
    }
    ann = api.call("POST", "/announcements", agent, json=ann_body)
    assert ann.status_code == 201, ann.text
    edit = {**ann_body, "title_en": "Maintenance moved"}
    _refused(api.call("PATCH", f"/announcements/{ann.json()['id']}", agent, json=edit))
    ok = api.call(
        "PATCH",
        f"/announcements/{ann.json()['id']}",
        agent,
        json=edit,
        headers=_etag(ann.json()["version"]),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == ann.json()["version"] + 1  # the refused PATCH wrote nothing
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"'

    # Support ticket.
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
    tkt = ticket.json()
    _refused(api.call("PATCH", f"/support/tickets/{tkt['id']}", agent, json={"priority": "p2"}))
    ok = api.call(
        "PATCH",
        f"/support/tickets/{tkt['id']}",
        agent,
        json={"priority": "p2"},
        headers=_etag(tkt["version"]),
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"'

    # Price override and AI bundle (subscription ETag).
    got = api.call("GET", f"/subscriptions/{sub}", owner)
    price = {"price_override_inr": "3999.00", "reason": "Pilot school, agreed in writing (SS/1)"}
    _refused(api.call("PUT", f"/subscriptions/{sub}/price-override", owner, json=price))
    _refused(api.call("DELETE", f"/subscriptions/{sub}/price-override", owner))
    _refused(
        api.call(
            "PUT",
            f"/subscriptions/{sub}/ai-bundle",
            owner,
            json={"ai_bundle_id": str(uuid.uuid4())},
        )
    )
    _refused(api.call("DELETE", f"/subscriptions/{sub}/ai-bundle", owner))
    assert api.call("GET", f"/subscriptions/{sub}", owner).json() == got.json()
    ok = api.call(
        "PUT",
        f"/subscriptions/{sub}/price-override",
        owner,
        json=price,
        headers={"If-Match": got.headers["ETag"]},
    )
    assert ok.status_code == 200, ok.text
    cleared = api.call(
        "DELETE",
        f"/subscriptions/{sub}/price-override",
        owner,
        headers={"If-Match": ok.headers["ETag"]},
    )
    assert cleared.status_code == 200, cleared.text


def test_AA_13_a_flag_is_created_without_if_match_and_changed_only_with_it(
    api: Api, owner: Operator, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    engineer = make_operator("platform_engineer")
    tid = _school(api, owner, make_plan())["tenant_id"]
    key = f"synthetic.flag_{uuid.uuid4().hex[:6]}"
    created = api.call("PUT", f"/flags/{key}", engineer, json={"enabled": False})
    assert created.status_code == 200, created.text
    _refused(api.call("PUT", f"/flags/{key}", engineer, json={"enabled": True}))
    flags = api.call("GET", "/flags", engineer).json()["data"]
    assert [f["enabled"] for f in flags if f["key"] == key and f["tenant_id"] is None] == [False]
    ok = api.call(
        "PUT",
        f"/flags/{key}",
        engineer,
        json={"enabled": True},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert ok.status_code == 200, ok.text

    override = api.call("PUT", f"/flags/{key}/tenants/{tid}", engineer, json={"enabled": False})
    assert override.status_code == 200, override.text
    _refused(api.call("PUT", f"/flags/{key}/tenants/{tid}", engineer, json={"enabled": True}))
    ok = api.call(
        "PUT",
        f"/flags/{key}/tenants/{tid}",
        engineer,
        json={"enabled": True},
        headers={"If-Match": override.headers["ETag"]},
    )
    assert ok.status_code == 200, ok.text
