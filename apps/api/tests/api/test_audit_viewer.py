"""Audit viewer and chain verification over the API (FR-AUD-005, US-1001, SEC-007)."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.audit import verification

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


def test_FR_AUD_005_filters_and_cursor(world: Any, api: Any, admin_engine: Engine) -> None:
    owner = world.person("owner")
    page = api.call(owner, "GET", "/api/v1/audit/events", params={"limit": 5})
    assert page.status_code == 200, page.text
    first = page.json()
    seqs = [e["seq"] for e in first["data"]]
    assert seqs == sorted(seqs, reverse=True)
    assert first["next_cursor"]
    more = api.call(
        owner, "GET", "/api/v1/audit/events", params={"limit": 5, "cursor": first["next_cursor"]}
    ).json()
    assert max(e["seq"] for e in more["data"]) < min(seqs)
    for event in first["data"]:
        assert set(event) == {
            "id",
            "seq",
            "occurred_at",
            "actor_type",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "summary",
            "request_id",
        }
    sections = api.call(
        owner,
        "GET",
        "/api/v1/audit/events",
        params={"action": "section.created", "resource_type": "section"},
    ).json()["data"]
    assert len(sections) == 3
    by_actor = api.call(
        owner, "GET", "/api/v1/audit/events", params={"actor": str(owner.user_id)}
    ).json()["data"]
    assert by_actor
    assert all(e["actor_id"] == str(owner.user_id) for e in by_actor)
    window = api.call(
        owner,
        "GET",
        "/api/v1/audit/events",
        params={"from": "2000-01-01T00:00:00Z", "to": "2000-01-02T00:00:00Z"},
    ).json()["data"]
    assert window == []
    assert (
        api.call(owner, "GET", "/api/v1/audit/events", params={"action": "DROP TABLE"}).status_code
        == 422
    )


def test_FR_AUD_001_audit_events_store_the_apis_own_request_id(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A caller-chosen X-Request-Id never reaches the audit chain (audit 2026-10-06 hardening):
    the event carries the id the API generated and returned."""
    from sqlalchemy import text

    owner = world.person("owner")
    res = api.call(
        owner,
        "GET",
        "/api/v1/audit/export",
        params={"from": "2000-01-01T00:00:00Z", "to": "2000-01-02T00:00:00Z"},
        headers={"X-Request-Id": "chosen_by_client_0001"},
    )
    assert res.status_code == 200, res.text
    with admin_engine.connect() as c:
        stored = c.execute(
            text(
                "SELECT request_id FROM audit.events WHERE tenant_id = :t "
                "AND action = 'audit.exported' ORDER BY seq DESC LIMIT 1"
            ),
            {"t": world.a.tenant_id},
        ).scalar_one()
    assert stored == res.headers["X-Request-Id"]
    assert stored != "chosen_by_client_0001"


def test_FR_AUD_005_time_filters_need_an_offset(world: Any, api: Any) -> None:
    """A time without an offset was silently read as UTC (audit 2026-10-06 hardening): the
    viewer and the export refuse it (422) and take any explicit offset."""
    owner = world.person("owner")
    for path in ("/api/v1/audit/events", "/api/v1/audit/export"):
        for params in (
            {"from": "2026-10-01T00:00:00"},
            {"to": "2026-10-01"},
            {"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00"},
        ):
            res = api.call(owner, "GET", path, params=params)
            assert res.status_code == 422, (path, params, res.text)
        ok = api.call(
            owner,
            "GET",
            path,
            params={"from": "2000-01-01T00:00:00+05:30", "to": "2000-01-02T00:00:00+05:30"},
        )
        assert ok.status_code == 200, (path, ok.text)


def test_FR_AUD_005_cross_tenant_events_never_visible(world: Any, api: Any) -> None:
    events = api.call(
        world.person("auditor_readonly"), "GET", "/api/v1/audit/events", params={"limit": 200}
    ).json()["data"]
    b_ids = {str(v) for v in world.b.ids.values()} | {str(world.b.tenant_id)}
    assert not any(e["resource_id"] in b_ids for e in events)


def test_US_1001_AC2_verify_reports_intact_chain(world: Any, api: Any) -> None:
    # The result is stored by a verification run (the daily job or a queued one; R-19).
    queued = api.call(world.person("principal"), "POST", "/api/v1/audit/verify", json={})
    assert queued.status_code == 202, queued.text
    verification.run_requested(world.a.tenant_id)
    res = api.call(world.person("principal"), "GET", "/api/v1/audit/verify")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["checked"] > 0
    assert body["first_bad_seq"] is None


def test_FR_AUD_005_needs_audit_read(world: Any, api: Any) -> None:
    for role in ("office_staff", "teacher", "accountant"):
        assert api.call(world.person(role), "GET", "/api/v1/audit/events").status_code == 403
        assert api.call(world.person(role), "GET", "/api/v1/audit/verify").status_code == 403
