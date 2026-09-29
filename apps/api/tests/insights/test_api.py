"""M5 over HTTP: purpose limits of the route table and the class teacher's round trips
(FR-EW-016, FR-ATT-004, FR-EW-007, FR-EW-014; 08 §4)."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine

from app.main import create_app

S = sys.modules["sos_test_insights_support"]
M5_PREFIXES = ("/api/v1/insights", "/api/v1/behaviour-notes")
M5_STUDENT_PATHS = ("/behaviour-notes", "/timeline", "/flags")


def _m5_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rc in iter_route_contexts(create_app().routes):
        if not isinstance(rc.original_route, APIRoute):
            continue
        path = str(rc.path)
        if path.startswith(M5_PREFIXES) or (
            path.startswith("/api/v1/students/{student_id}") and path.endswith(M5_STUDENT_PATHS)
        ):
            out.extend((m, path) for m in sorted(rc.methods or ()))
    return out


def test_FR_EW_016_no_route_exports_or_downloads_insights() -> None:
    routes = _m5_routes()
    assert len(routes) >= 15
    for method, path in routes:
        lowered = path.lower()
        assert not any(w in lowered for w in ("export", "download", "csv", "xlsx", "pdf")), path
        assert not path.startswith("/api/v1/platform/"), path
        assert method in {"GET", "POST", "PUT"}, (method, path)


@pytest.mark.db
def test_FR_ATT_004_class_teacher_previews_then_commits_a_sheet(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    days = S.school_days(2, end=S.school_days(50)[0])
    header = ",".join(d.strftime("%d/%m/%Y") for d in days)
    doc = S.sheet_document(
        admin_engine, school, f"Roll No,{header}\n1,P,A\n2,A,A\n".encode(), kind="csv"
    )
    ct = school.people["ct"]
    section = school.ids["section_9a"]
    preview = api.call(
        ct, "POST", f"/api/v1/sections/{section}/attendance/sheet", json={"document_id": str(doc)}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["issues"] == []
    assert len(body["entries"]) == 4
    # The upload is gone; reading it again is 404.
    again = api.call(
        ct, "POST", f"/api/v1/sections/{section}/attendance/sheet", json={"document_id": str(doc)}
    )
    assert again.status_code == 404
    saved = api.call(
        ct,
        "POST",
        f"/api/v1/sections/{section}/attendance",
        json={"entries": body["entries"], "source": "import"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["written"] == 4
    month = api.call(
        ct,
        "GET",
        f"/api/v1/sections/{section}/attendance/month",
        params={"month": days[-1].strftime("%Y-%m")},
    )
    assert month.status_code == 200


@pytest.mark.db
def test_FR_EW_014_settings_etag_and_step_up(school: Any, api: Any) -> None:
    principal = school.people["principal"]
    got = api.call(principal, "GET", "/api/v1/insights/settings")
    assert got.status_code == 200
    etag = got.headers["ETag"]
    stale = api.call(
        principal,
        "PUT",
        "/api/v1/insights/settings",
        json={"rules": {"attendance_rate": {"threshold": 80}}},
        headers={"If-Match": etag},
        auth_age_s=301,
    )
    assert stale.status_code == 428
    ok = api.call(
        principal,
        "PUT",
        "/api/v1/insights/settings",
        json={"rules": {"attendance_rate": {"threshold": 80}}},
        headers={"If-Match": etag},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] != etag
    reset = api.call(
        principal,
        "PUT",
        "/api/v1/insights/settings",
        json={"rules": {"attendance_rate": {"threshold": 75}}},
        headers={"If-Match": ok.headers["ETag"]},
    )
    assert reset.status_code == 200
    # Class teachers see the rules (explainable flags) but cannot change them.
    ct = school.people["ct"]
    assert api.call(ct, "GET", "/api/v1/insights/settings").status_code == 200
    denied = api.call(
        ct,
        "PUT",
        "/api/v1/insights/settings",
        json={"rules": {}},
        headers={"If-Match": reset.headers["ETag"]},
    )
    assert denied.status_code == 403
