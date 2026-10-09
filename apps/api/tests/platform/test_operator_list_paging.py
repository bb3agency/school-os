"""Operator lists of deployments, announcements and break-glass requests are cursor-paged
(audit 2026-10-06 R-14; OWASP API4).

Before: deployments came back unpaged (one row per school), and announcements and break-glass
requests stopped at 200 with ``next_cursor: null``, so older rows were silently missing. Now
each takes ``limit`` (1..200, default 50) and ``cursor`` and answers the standard ``Page``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.platform import announcements
from app.platform.announcements import InMemoryAnnouncementCache

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = InMemoryAnnouncementCache()
    monkeypatch.setattr(announcements, "get_cache", lambda: cache)


def _walk(api: Api, op: Operator, path: str, *, limit: int, **query: str) -> list[str]:
    """Every id of a paged list, following next_cursor; checks the page size on the way."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(1000):
        params: dict[str, Any] = {"limit": limit, **query}
        if cursor:
            params["cursor"] = cursor
        res = api.call("GET", path, op, params=params)
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["data"]) <= limit
        seen.extend(item["id"] for item in body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert body["data"], "a page with a next_cursor is never empty"
    assert len(seen) == len(set(seen)), "no row appears on two pages"
    return seen


def _first_page(api: Api, op: Operator, path: str, limit: int, **query: str) -> dict[str, Any]:
    res = api.call("GET", path, op, params={"limit": limit, **query})
    assert res.status_code == 200, res.text
    page: dict[str, Any] = res.json()
    return page


def test_R_14_deployments_are_cursor_paged(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    created, created_tenants = [], []
    for _ in range(3):
        out = api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()
        created.append(out["deployment_id"])
        created_tenants.append(out["tenant_id"])
    first = _first_page(api, owner, "/deployments", 2)
    assert len(first["data"]) == 2
    assert first["next_cursor"] is not None
    assert set(created) <= set(_walk(api, owner, "/deployments", limit=2))
    assert api.call("GET", "/deployments", owner, params={"limit": 201}).status_code == 422
    one = _first_page(api, owner, "/deployments", 1, tenant_id=created_tenants[0])
    assert [d["id"] for d in one["data"]] == [created[0]]
    assert one["next_cursor"] is None


def test_R_14_announcements_are_cursor_paged(api: Api, make_operator: MakeOperator) -> None:
    agent = make_operator("support_agent")
    start = dt.datetime(2031, 1, 6, 4, 30, tzinfo=dt.UTC)
    created = []
    for i in range(3):
        res = api.call(
            "POST",
            "/announcements",
            agent,
            json={
                "title_en": f"Maintenance window {i + 1}",
                "body_en": "SchoolOS is unavailable from 10:00 to 12:00 IST.",
                "severity": "maintenance",
                "starts_at": (start + dt.timedelta(days=i)).isoformat(),
                "ends_at": (start + dt.timedelta(days=i, hours=2)).isoformat(),
            },
        )
        assert res.status_code == 201, res.text
        created.append(res.json()["id"])
    first = _first_page(api, agent, "/announcements", 2)
    assert len(first["data"]) == 2
    assert first["next_cursor"] is not None
    assert set(created) <= set(_walk(api, agent, "/announcements", limit=2))
    assert api.call("GET", "/announcements", agent, params={"limit": 0}).status_code == 422


def test_R_14_break_glass_requests_are_cursor_paged(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    tid = api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()[
        "tenant_id"
    ]
    created = []
    for _ in range(3):
        res = api.call(
            "POST",
            "/break-glass-requests",
            agent,
            json={
                "tenant_id": tid,
                "reason_code": "support_request",
                "reason": "Import batch stuck; the school asked for help in writing",
                "scope": {},
                "duration_minutes": 60,
                "emergency": False,
            },
        )
        assert res.status_code == 201, res.text
        created.append(res.json()["id"])
    first = _first_page(api, agent, "/break-glass-requests", 2, tenant_id=tid)
    assert [r["id"] for r in first["data"]] == created[:0:-1]  # newest first
    assert first["next_cursor"] is not None
    second = api.call(
        "GET",
        "/break-glass-requests",
        agent,
        params={"limit": 2, "tenant_id": tid, "cursor": first["next_cursor"]},
    ).json()
    assert [r["id"] for r in second["data"]] == [created[0]]
    assert second["next_cursor"] is None
    assert set(created) <= set(_walk(api, agent, "/break-glass-requests", limit=50))
