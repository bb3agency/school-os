"""School-side billing page, announcements and support tickets, plus the operator side of
announcements and support (FR-PLT-026, FR-PLT-027, FR-PLT-030)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.core.languages import contains_telugu
from app.platform import announcements, billing, support
from app.platform.announcements import InMemoryAnnouncementCache
from app.platform.schemas import TicketCreateSchool

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db

AADHAAR_LIKE = "2345 6789 0124"  # synthetic; passes Verhoeff? masked either way by redact()
PHONE = "9876543210"


def _live_school(
    api: Api, owner: Operator, plan: uuid.UUID, admin: Engine, *, start_as: str = "active"
) -> tuple[str, str]:
    body = provision_payload(plan, start_as=start_as)
    out = api.call("POST", "/tenants", owner, json=body).json()
    tid = out["tenant_id"]
    api.call("POST", f"/tenants/{tid}/activate", owner)
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
    return tid, body["owner"]["idp_subject"]


def _school_get(api: Api, subject: str, path: str, **kw: Any) -> Any:
    return api.client.get(path, headers=api.headers(None, tenant_subject=subject), **kw)


def _school_post(api: Api, subject: str, path: str, body: Any, **extra: str) -> Any:
    headers = api.headers(None, tenant_subject=subject)
    headers.update(extra)
    return api.client.post(path, headers=headers, json=body)


def test_FR_PLT_030_school_billing_page_shows_only_own_subscription(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    plan = make_plan(base_price_inr="5000.00", limits={"students": 1000, "staff_users": 10})
    tid, subject = _live_school(api, owner, plan, admin_engine)
    other, _ = _live_school(api, owner, make_plan(), admin_engine)
    with platform_session() as s:
        draft: Any = s.execute(
            text("SELECT id FROM platform.invoices WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
    billing.issue_invoice(owner.actor, draft)
    res = _school_get(api, subject, "/api/v1/tenant/billing")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["available"], body["status"], body["tier"]) == (True, "active", "shared")
    assert body["amount_due_inr"] == "5900.00"
    assert {u["metric"] for u in body["usage"]} >= {"students", "staff_users"}
    invoices = _school_get(api, subject, "/api/v1/tenant/billing/invoices").json()["data"]
    assert len(invoices) == 1
    assert invoices[0]["invoice_number"].startswith("SOS/")
    assert other not in res.text


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_PLT_026_announcements_bilingual_audience_and_cache(  # noqa: PLR0917 - fixtures
    api: Api,
    make_operator: MakeOperator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = InMemoryAnnouncementCache()
    monkeypatch.setattr(announcements, "get_cache", lambda: cache)
    agent = make_operator("support_agent")
    tid, subject = _live_school(api, owner, make_plan(), admin_engine)
    now = dt.datetime.now(dt.UTC)
    base = {
        "title_en": "Maintenance tonight",
        "title_te": "ఈ రాత్రి నిర్వహణ",
        "body_en": "SchoolOS will be unavailable from 22:00 to 22:30 IST.",
        "body_te": "SchoolOS రాత్రి 22:00 నుండి 22:30 వరకు అందుబాటులో ఉండదు.",
        "severity": "maintenance",
        "starts_at": (now - dt.timedelta(minutes=1)).isoformat(),
        "ends_at": (now + dt.timedelta(hours=2)).isoformat(),
    }
    missing_te = {k: v for k, v in base.items() if k != "title_te"}
    assert api.call("POST", "/announcements", agent, json=missing_te).status_code == 422
    everyone = api.call("POST", "/announcements", agent, json=base)
    assert everyone.status_code == 201, everyone.text
    dedicated_only = api.call(
        "POST",
        "/announcements",
        agent,
        json={**base, "audience": "tier", "audience_tier": "dedicated"},
    ).json()
    other_school = api.call(
        "POST",
        "/announcements",
        agent,
        json={**base, "audience": "tenants", "audience_tenant_ids": [str(uuid.uuid4())]},
    ).json()
    seen = _school_get(api, subject, "/api/v1/announcements").json()
    ids = {a["id"] for a in seen}
    assert everyone.json()["id"] in ids
    assert dedicated_only["id"] not in ids
    assert other_school["id"] not in ids
    assert seen[0]["title_te"]
    api.call("POST", f"/announcements/{everyone.json()['id']}/cancel", agent)
    ids = {a["id"] for a in _school_get(api, subject, "/api/v1/announcements").json()}
    assert everyone.json()["id"] not in ids
    assert announcements.for_deployment(uuid.UUID(tid), "dedicated")  # heartbeat delivery


def test_ADR_0036_announcements_need_no_telugu_and_show_none_while_telugu_is_hidden(  # noqa: PLR0917 - fixtures
    api: Api,
    make_operator: MakeOperator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = InMemoryAnnouncementCache()
    monkeypatch.setattr(announcements, "get_cache", lambda: cache)
    agent = make_operator("support_agent")
    _tid, subject = _live_school(api, owner, make_plan(), admin_engine)
    now = dt.datetime.now(dt.UTC)
    english_only = {
        "title_en": "Maintenance tonight",
        "body_en": "SchoolOS will be unavailable from 22:00 to 22:30 IST.",
        "severity": "maintenance",
        "starts_at": (now - dt.timedelta(minutes=1)).isoformat(),
        "ends_at": (now + dt.timedelta(hours=2)).isoformat(),
    }
    created = api.call("POST", "/announcements", agent, json=english_only)
    assert created.status_code == 201, created.text
    assert created.json()["title_te"] == ""
    with platform_session() as s:
        stored = (
            s.execute(
                text("SELECT title_te, body_te FROM platform.announcements WHERE id = :i"),
                {"i": created.json()["id"]},
            )
            .mappings()
            .one()
        )
    assert stored["title_te"] == english_only["title_en"], "the NOT NULL column holds English"
    with_telugu = api.call(
        "POST", "/announcements", agent, json={**english_only, "title_te": "ఈ రాత్రి నిర్వహణ"}
    )
    assert with_telugu.status_code == 201
    assert not contains_telugu(with_telugu.text)
    seen = _school_get(api, subject, "/api/v1/announcements")
    assert {a["id"] for a in seen.json()} >= {created.json()["id"], with_telugu.json()["id"]}
    assert all(a["title_te"] == "" and a["body_te"] == "" for a in seen.json())
    assert not contains_telugu(seen.text)
    listed = api.call("GET", "/announcements", agent)
    assert not contains_telugu(listed.text)


def test_FR_PLT_027_school_tickets_are_redacted_scoped_and_answered(
    api: Api,
    make_operator: MakeOperator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    agent = make_operator("support_agent")
    tid, subject = _live_school(api, owner, make_plan(), admin_engine)
    body = {
        "category": "import",
        "priority": "p2",
        "subject": f"Import stuck, call me on {PHONE}",
        "body": f"Student with Aadhaar {AADHAAR_LIKE} failed to import. Phone {PHONE}.",
    }
    res = _school_post(
        api, subject, "/api/v1/support/tickets", body, **{"Idempotency-Key": "ticket-key-0001"}
    )
    assert res.status_code == 201, res.text
    ticket = res.json()
    assert PHONE not in res.text
    assert ticket["number"].startswith("T-")
    replay = _school_post(
        api, subject, "/api/v1/support/tickets", body, **{"Idempotency-Key": "ticket-key-0001"}
    )
    assert replay.json()["id"] == ticket["id"]
    with platform_session() as s:
        stored = " ".join(
            s.execute(
                text("SELECT body FROM platform.support_messages WHERE ticket_id = :t"),
                {"t": ticket["id"]},
            ).scalars()
        )
    assert PHONE not in stored
    assert AADHAAR_LIKE.replace(" ", "") not in stored.replace(" ", "")
    reply = api.call(
        "POST", f"/support/tickets/{ticket['id']}/messages", agent, json={"body": "Looking into it"}
    )
    note = api.call(
        "POST",
        f"/support/tickets/{ticket['id']}/messages",
        agent,
        json={"body": "Internal: check batch", "internal_note": True},
    )
    assert reply.status_code == note.status_code == 201
    assert reply.json()["first_responded_at"] is not None
    school_view = _school_get(api, subject, f"/api/v1/support/tickets/{ticket['id']}").json()
    assert [m["body"] for m in school_view["messages"]][-1] == "Looking into it"
    assert all(not m["internal_note"] for m in school_view["messages"])
    follow = _school_post(
        api, subject, f"/api/v1/support/tickets/{ticket['id']}/messages", {"body": "Thanks"}
    )
    assert follow.status_code == 200, follow.text
    other = support.open_ticket_from_tenant(
        uuid.uuid4(), uuid.uuid4(), TicketCreateSchool(category="other", subject="x", body="y")
    )
    assert _school_get(api, subject, f"/api/v1/support/tickets/{other.id}").status_code == 404
    listing = _school_get(api, subject, "/api/v1/support/tickets").json()["data"]
    assert [t["id"] for t in listing] == [ticket["id"]]
    closed = api.call("PATCH", f"/support/tickets/{ticket['id']}", agent, json={"status": "closed"})
    assert closed.json()["status"] == "closed"
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.support_tickets SET purge_after = DATE '2000-01-01', "
                "closed_at = TIMESTAMPTZ '1998-01-01' WHERE id = :t"
            ),
            {"t": ticket["id"]},
        )
    assert support.purge_closed() >= 1
    assert api.call("GET", f"/support/tickets/{ticket['id']}", agent).status_code == 404
    with admin_engine.connect() as c:
        actions: Any = set(
            c.execute(
                text("SELECT action FROM audit.events WHERE tenant_id = :t"), {"t": tid}
            ).scalars()
        )
    assert {"support.ticket_opened", "support.ticket_updated"} <= actions


def test_FR_PLT_027_ticket_status_transitions(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    tid = api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()[
        "tenant_id"
    ]
    t = api.call(
        "POST",
        "/support/tickets",
        agent,
        json={
            "tenant_id": tid,
            "channel": "phone",
            "category": "access",
            "priority": "p1",
            "subject": "Cannot sign in",
            "body": "Principal cannot sign in",
        },
    ).json()
    assert t["resolution_due_at"] > t["first_response_due_at"]
    ok = api.call(
        "PATCH",
        f"/support/tickets/{t['id']}",
        agent,
        json={"status": "resolved", "assigned_to": str(agent.id)},
    )
    assert ok.json()["assigned_to"] == str(agent.id)
    api.call("PATCH", f"/support/tickets/{t['id']}", agent, json={"status": "closed"})
    bad = api.call("PATCH", f"/support/tickets/{t['id']}", agent, json={"status": "open"})
    assert bad.status_code == 409


def test_FR_PLT_001_dashboard_tiles_follow_permissions(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    api.call("POST", "/tenants", owner, json=provision_payload(make_plan(), start_as="active"))
    full = api.call("GET", "/dashboard", owner).json()
    assert float(full["mrr_inr"]) >= 5000
    assert full["arr_inr"] == f"{float(full['mrr_inr']) * 12:.2f}"
    assert full["schools_by_tier"]["shared"] >= 1
    agent = api.call("GET", "/dashboard", make_operator("support_agent")).json()
    assert agent["mrr_inr"] is None
    assert agent["open_tickets_by_priority"] is not None


def test_FR_PLT_029_platform_audit_viewer_filters_csv_and_verify(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    viewer = make_operator("platform_viewer")
    tid = api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()[
        "tenant_id"
    ]
    events = api.call("GET", f"/audit/events?tenant_id={tid}", viewer).json()["data"]
    assert [e["action"] for e in events][-1] == "tenant.provisioned"
    assert all(e["subject_tenant_id"] == tid for e in events)
    csv = api.call("GET", f"/audit/events?tenant_id={tid}", viewer, headers={"Accept": "text/csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("seq,occurred_at")
    verify = api.call("POST", "/audit/verify", viewer)
    assert verify.status_code == 202
    assert verify.json()["ok"] is True
    job = api.call("GET", f"/jobs/{verify.json()['job_id']}", viewer)
    assert job.json()["status"] == "succeeded"
    other = make_operator("support_agent")
    assert api.call("GET", f"/jobs/{verify.json()['job_id']}", other).status_code == 404
