"""School audit log CSV export (FR-AUD-005, US-1001, SEC-005, SEC-017; invariants 1, 5, 7).

``GET /api/v1/audit/export``: ``audit.read`` plus a recent MFA sign-in; the viewer's filters;
oldest first; audited as ``audit.exported`` before the file streams; streamed in pages; refused
above the configured maximum. Synthetic schools only (tests/api/world.py).
"""

from __future__ import annotations

import csv
import io
import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.audit import export

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
EXPORT = "/api/v1/audit/export"


def _rows(body: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = body.decode("utf-8")
    assert text.startswith("﻿"), "BOM, so Excel reads UTF-8"
    reader = csv.reader(io.StringIO(text[1:], newline=""))
    header, *rows = list(reader)
    return header, [dict(zip(header, r, strict=True)) for r in rows]


def _exported(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = W.audit_events(admin, tenant_id, "audit.exported")
    return events


def test_FR_AUD_005_csv_export_with_filters(world: Any, api: Any, admin_engine: Engine) -> None:
    owner = world.person("owner")
    before = len(_exported(admin_engine, world.a.tenant_id))
    res = api.call(
        owner, "GET", EXPORT, params={"action": "section.created", "resource_type": "section"}
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"].startswith('attachment; filename="audit-log-')
    assert res.headers["cache-control"] == "no-store"
    header, rows = _rows(res.content)
    assert tuple(header) == export.HEADER
    assert len(rows) == 3 == int(res.headers["x-audit-export-rows"])
    assert {r["action"] for r in rows} == {"section.created"}
    seqs = [int(r["seq"]) for r in rows]
    assert seqs == sorted(seqs), "oldest first"
    for row in rows:
        assert row["occurred_at_utc"].endswith("Z")
        assert json.loads(row["summary"]) is not None
        assert row["resource_id"] in {str(v) for v in world.a.ids.values()}

    exported = _exported(admin_engine, world.a.tenant_id)
    assert len(exported) == before + 1
    event = exported[-1]
    assert event["resource_type"] == "audit_log"
    assert event["actor_id"] == owner.user_id
    assert event["summary"]["rows"] == 3
    assert event["summary"]["format"] == "csv"
    assert event["summary"]["filters"]["action"] == "section.created"
    assert event["summary"]["filters"]["resource_type"] == "section"
    assert event["summary"]["up_to_seq"] >= max(seqs)


def test_FR_AUD_005_export_never_contains_its_own_event_and_filters_by_actor_and_time(
    world: Any, api: Any
) -> None:
    owner = world.person("owner")
    res = api.call(owner, "GET", EXPORT, params={"actor": str(owner.user_id)})
    assert res.status_code == 200, res.text
    _, rows = _rows(res.content)
    assert rows
    assert {r["actor_id"] for r in rows} == {str(owner.user_id)}
    last = int(rows[-1]["seq"])
    again = _rows(api.call(owner, "GET", EXPORT, params={"actor": str(owner.user_id)}).content)[1]
    assert any(r["action"] == "audit.exported" and int(r["seq"]) > last for r in again)
    assert not any(r["action"] == "audit.exported" for r in rows if int(r["seq"]) > last)
    empty = api.call(
        owner,
        "GET",
        EXPORT,
        params={"from": "2000-01-01T00:00:00Z", "to": "2000-01-02T00:00:00Z"},
    )
    assert empty.status_code == 200
    assert _rows(empty.content)[1] == []
    assert empty.headers["x-audit-export-rows"] == "0"


def test_FR_AUD_005_export_streams_in_pages(
    world: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = world.person("owner")
    small = export.ExportLimits(version=1, max_rows=200_000, page_size=2)
    monkeypatch.setattr(export, "limits", lambda: small)
    paged = _rows(api.call(owner, "GET", EXPORT, params={"resource_type": "section"}).content)[1]
    monkeypatch.undo()
    whole = _rows(api.call(owner, "GET", EXPORT, params={"resource_type": "section"}).content)[1]
    assert len(paged) >= 3
    assert paged == whole[: len(paged)]
    assert [int(r["seq"]) for r in paged] == sorted(int(r["seq"]) for r in paged)


def test_FR_AUD_005_too_many_events_is_refused_and_not_audited(
    world: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = world.person("owner")
    before = len(_exported(admin_engine, world.a.tenant_id))
    tiny = export.ExportLimits(version=1, max_rows=2, page_size=1000)
    monkeypatch.setattr(export, "limits", lambda: tiny)
    res = api.call(owner, "GET", EXPORT)
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["errors"][0]["code"] == "too_many_events"
    assert "shorter date range" in body["detail"]
    assert len(_exported(admin_engine, world.a.tenant_id)) == before


def test_SEC_005_audit_export_needs_a_recent_mfa_sign_in(world: Any, api: Any) -> None:
    owner = world.person("owner")
    stale = api.call(owner, "GET", EXPORT, auth_age_s=600)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    # The viewer itself does not need step-up.
    assert api.call(owner, "GET", "/api/v1/audit/events", auth_age_s=600).status_code == 200


@pytest.mark.parametrize("role", ["owner", "principal", "office_admin", "auditor_readonly"])
def test_FR_AUD_005_audit_readers_may_export(world: Any, api: Any, role: str) -> None:
    res = api.call(world.person(role), "GET", EXPORT, params={"action": "section.created"})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("role", ["office_staff", "teacher", "class_teacher", "accountant"])
def test_FR_AUD_005_export_needs_audit_read(world: Any, api: Any, role: str) -> None:
    assert api.call(world.person(role), "GET", EXPORT).status_code == 403


def test_FR_AUD_005_export_never_crosses_schools(world: Any, api: Any) -> None:
    """School B's owner exports only B's events; A's IDs as filters find nothing (the export
    has no object id to answer 404 for: another school's ids simply match no row)."""
    b_owner = world.b.people["owner"]
    res = api.call(b_owner, "GET", EXPORT)
    assert res.status_code == 200, res.text
    _, rows = _rows(res.content)
    assert rows
    a_ids = {str(v) for v in world.a.ids.values()} | {str(world.a.tenant_id)}
    a_people = {str(p.user_id) for p in world.a.people.values()}
    assert not any(r["resource_id"] in a_ids for r in rows)
    assert not any(r["actor_id"] in a_people for r in rows)
    a_owner = world.person("owner")
    by_a = api.call(b_owner, "GET", EXPORT, params={"actor": str(a_owner.user_id)})
    assert by_a.status_code == 200
    assert _rows(by_a.content)[1] == []
    section = world.a.ids["section_9a"]
    one = api.call(b_owner, "GET", EXPORT, params={"resource_id": str(section)})
    assert _rows(one.content)[1] == []


def test_SEC_017_cells_are_neutralised_and_masked() -> None:
    assert export.safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert export.safe_cell("-1") == "'-1"
    assert export.safe_cell("@x") == "'@x"
    assert export.safe_cell(None) == ""
    assert export.safe_cell("a\x00b") == "ab"
    assert "234567890124" not in export.safe_cell("n 234567890124")
    assert export.safe_cell("section.created") == "section.created"


@pytest.mark.parametrize("value", [" =1+1", "　=1", "＝1+1", "＠x", " -1+1"])
def test_DL_hardening_4_hidden_formulas_are_neutralised(value: str) -> None:
    assert export.safe_cell(value).startswith("'")


def test_FR_AUD_005_export_limits_are_versioned_config() -> None:
    export.limits.cache_clear()
    cfg = export.limits()
    assert cfg.version == 1
    assert cfg.max_rows == 200_000
    assert 100 <= cfg.page_size <= 10_000
