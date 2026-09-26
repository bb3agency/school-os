"""School settings and academic structure routes (US-202, FR-TEN-010, FR-TEN-012, docs/09 §2)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


@pytest.fixture
def school(api: Any, admin_engine: Engine) -> Any:
    """A fresh school with only an owner (so structure tests do not disturb the shared world)."""
    tid = W.provision_school()
    s = W.School(tid)
    s.people["owner"] = W.add_member(admin_engine, tid, ["owner"])
    s.people["office_admin"] = W.add_member(admin_engine, tid, ["office_admin"])
    s.people["office_staff"] = W.add_member(admin_engine, tid, ["office_staff"])
    return s


def test_US_202_AC1_set_up_year_classes_and_sections(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    year = api.call(
        admin,
        "POST",
        "/api/v1/academic-years",
        json={
            "label": "2026-27",
            "starts_on": "2026-06-01",
            "ends_on": "2027-03-31",
            "is_current": True,
        },
    )
    assert year.status_code == 201, year.text
    assert year.headers["Location"] == f"/api/v1/academic-years/{year.json()['id']}"
    classes = api.call(admin, "POST", "/api/v1/classes/defaults")
    assert classes.status_code == 200, classes.text
    codes = [c["code"] for c in classes.json()["data"]]
    assert codes[0] == "NUR"
    assert codes[-1] == "XII"
    assert len(codes) == 15
    again = api.call(admin, "POST", "/api/v1/classes/defaults")
    assert len(again.json()["data"]) == 15
    ix = next(c for c in classes.json()["data"] if c["code"] == "IX")
    for name in "ABCD":
        res = api.call(
            admin,
            "POST",
            "/api/v1/sections",
            json={"academic_year_id": year.json()["id"], "class_id": ix["id"], "name": name},
        )
        assert res.status_code == 201, res.text
    listed = api.call(admin, "GET", "/api/v1/sections", params={"class_id": ix["id"]})
    assert [s["name"] for s in listed.json()["data"]] == ["A", "B", "C", "D"]

    events = W.audit_events(admin_engine, school.tenant_id)
    actions = [e["action"] for e in events]
    assert actions.count("academic_year.created") == 1
    assert actions.count("academic_year.current_set") == 1
    assert actions.count("class.defaults_added") == 1, "second call added nothing"
    assert next(e for e in events if e["action"] == "class.defaults_added")["summary"] == {
        "count": 15
    }
    assert actions.count("section.created") == 4
    assert actions.count("tenant.key.created") == 1
    assert actions.count("role.created") == 9


def test_US_202_AC3_without_structure_permission_is_403(school: Any, api: Any) -> None:
    res = api.call(
        school.people["office_staff"],
        "POST",
        "/api/v1/classes",
        json={"code": "XI", "display_en": "Class XI", "display_te": "11వ తరగతి", "sort_order": 1},
    )
    assert res.status_code == 403


def test_FR_TEN_010_one_current_year_and_etags(school: Any, api: Any, admin_engine: Engine) -> None:
    owner = school.people["owner"]
    y1 = api.call(
        owner,
        "POST",
        "/api/v1/academic-years",
        json={"label": "2025-26", "starts_on": "2025-06-01", "ends_on": "2026-03-31"},
    ).json()
    y2 = api.call(
        owner,
        "POST",
        "/api/v1/academic-years",
        json={
            "label": "2026-27",
            "starts_on": "2026-06-01",
            "ends_on": "2027-03-31",
            "is_current": True,
        },
    ).json()
    got = api.call(owner, "GET", f"/api/v1/academic-years/{y1['id']}")
    etag = got.headers["ETag"]
    res = api.call(
        owner,
        "POST",
        f"/api/v1/academic-years/{y1['id']}/make-current",
        headers={"If-Match": etag},
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_current"] is True
    years = api.call(owner, "GET", "/api/v1/academic-years").json()["data"]
    assert [y["label"] for y in years] == ["2026-27", "2025-26"]
    assert [y["is_current"] for y in years] == [False, True]
    stale = api.call(
        owner,
        "PATCH",
        f"/api/v1/academic-years/{y1['id']}",
        json={"ends_on": "2026-04-30"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412
    current_set = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "academic_year.current_set")
        if e["resource_id"] == uuid.UUID(y1["id"])
    ]
    assert len(current_set) == 1
    assert current_set[0]["summary"]["previous_year_id"] == y2["id"]


def test_docs_09_patch_needs_if_match_and_rejects_stale(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    created = api.call(
        owner,
        "POST",
        "/api/v1/classes",
        json={"code": "IX", "display_en": "Class IX", "display_te": "9వ తరగతి", "sort_order": 120},
    )
    cid, etag = created.json()["id"], created.headers["ETag"]
    url = f"/api/v1/classes/{cid}"
    assert api.call(owner, "PATCH", url, json={"sort_order": 5}).status_code == 400
    bad = api.call(owner, "PATCH", url, json={"sort_order": 5}, headers={"If-Match": "*"})
    assert bad.status_code == 400
    ok = api.call(owner, "PATCH", url, json={"sort_order": 5}, headers={"If-Match": etag})
    assert ok.status_code == 200
    assert ok.headers["ETag"] != etag
    before = len(W.audit_events(admin_engine, school.tenant_id, "class.updated"))
    stale = api.call(owner, "PATCH", url, json={"sort_order": 6}, headers={"If-Match": etag})
    assert stale.status_code == 412
    assert len(W.audit_events(admin_engine, school.tenant_id, "class.updated")) == before
    updated = W.audit_events(admin_engine, school.tenant_id, "class.updated")
    assert updated[-1]["summary"] == {"fields": ["sort_order"]}
    code_change = api.call(
        owner, "PATCH", url, json={"code": "X"}, headers={"If-Match": ok.headers["ETag"]}
    )
    assert code_change.status_code == 422


def test_docs_09_idempotent_create_replays_and_detects_reuse(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    body = {"code": "UKG", "display_en": "UKG", "display_te": "యూకేజీ", "sort_order": 30}
    key = {"Idempotency-Key": "class-ukg-0001"}
    first = api.call(owner, "POST", "/api/v1/classes", json=body, headers=key)
    second = api.call(owner, "POST", "/api/v1/classes", json=body, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    created = W.audit_events(admin_engine, school.tenant_id, "class.created")
    assert len(created) == 1
    reused = api.call(
        owner, "POST", "/api/v1/classes", json={**body, "sort_order": 31}, headers=key
    )
    assert reused.status_code == 422
    assert reused.json()["code"] == "idempotency_key_reused"
    other_user = api.call(
        school.people["office_admin"], "POST", "/api/v1/classes", json=body, headers=key
    )
    assert other_user.status_code == 409, "keys are per user; the class code already exists"
    assert (
        api.call(
            owner, "POST", "/api/v1/classes", json=body, headers={"Idempotency-Key": "bad key!"}
        ).status_code
        == 400
    )


def test_FR_AUD_001_failed_create_leaves_no_event(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    body = {"code": "LKG", "display_en": "LKG", "display_te": "ఎల్‌కేజీ", "sort_order": 20}
    assert api.call(owner, "POST", "/api/v1/classes", json=body).status_code == 201
    before = W.audit_events(admin_engine, school.tenant_id)
    dup = api.call(owner, "POST", "/api/v1/classes", json=body)
    assert dup.status_code == 409
    assert W.audit_events(admin_engine, school.tenant_id) == before


def test_FR_TEN_012_settings_read_and_update(school: Any, api: Any, admin_engine: Engine) -> None:
    staff = school.people["office_staff"]
    got = api.call(staff, "GET", "/api/v1/tenant")
    assert got.status_code == 200
    assert got.json()["settings"] == {
        "languages": ["en", "te"],
        "date_format": "DD/MM/YYYY",
        "idle_timeout_minutes": 15,
        "ai_features_enabled": True,
        "ai_monthly_budget_inr": 5000,
    }
    owner = school.people["owner"]
    etag = got.headers["ETag"]
    denied = api.call(
        school.people["office_admin"],
        "PATCH",
        "/api/v1/tenant",
        json={"idle_timeout_minutes": 10},
        headers={"If-Match": etag},
    )
    assert denied.status_code == 403
    no_step_up = api.call(
        owner,
        "PATCH",
        "/api/v1/tenant",
        json={"idle_timeout_minutes": 10},
        headers={"If-Match": etag},
        auth_age_s=301,
    )
    assert no_step_up.status_code == 428
    bad_bodies: list[dict[str, Any]] = [
        {"idle_timeout_minutes": 4},
        {"idle_timeout_minutes": 31},
        {"languages": []},
    ]
    for bad in bad_bodies:
        res = api.call(owner, "PATCH", "/api/v1/tenant", json=bad, headers={"If-Match": etag})
        assert res.status_code == 422, bad
    ok = api.call(
        owner,
        "PATCH",
        "/api/v1/tenant",
        json={"idle_timeout_minutes": 10, "ai_features_enabled": False, "languages": ["te"]},
        headers={"If-Match": etag},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["settings"]["idle_timeout_minutes"] == 10
    assert ok.json()["settings"]["languages"] == ["te"]
    events = W.audit_events(admin_engine, school.tenant_id, "tenant.settings_updated")
    assert len(events) == 1
    assert events[0]["summary"] == {
        "fields": ["ai_features_enabled", "idle_timeout_minutes", "languages"]
    }
    stale = api.call(
        owner,
        "PATCH",
        "/api/v1/tenant",
        json={"date_format": "YYYY-MM-DD"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412


def test_FR_IAM_012_scoped_structure_reads(world: Any, api: Any) -> None:
    ct = world.person("class_teacher")  # scope: section 9A
    sections = api.call(ct, "GET", "/api/v1/sections").json()["data"]
    assert [s["id"] for s in sections] == [str(world.a.ids["section_9a"])]
    classes = api.call(ct, "GET", "/api/v1/classes").json()["data"]
    assert [c["id"] for c in classes] == [str(world.a.ids["class_ix"])]
    teacher = world.person("teacher")  # scope: class X
    sections = api.call(teacher, "GET", "/api/v1/sections").json()["data"]
    assert [s["id"] for s in sections] == [str(world.a.ids["section_10a"])]
    assert api.call(teacher, "GET", f"/api/v1/classes/{world.a.ids['class_ix']}").status_code == 404
    years = api.call(teacher, "GET", "/api/v1/academic-years").json()["data"]
    assert len(years) == 2
    owner_sections = api.call(world.person("owner"), "GET", "/api/v1/sections").json()["data"]
    assert len(owner_sections) == 3


def test_docs_09_cursor_pagination(world: Any, api: Any) -> None:
    owner = world.person("owner")
    first = api.call(owner, "GET", "/api/v1/sections", params={"limit": 2}).json()
    assert len(first["data"]) == 2
    rest = api.call(
        owner, "GET", "/api/v1/sections", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(rest["data"]) == 1
    assert rest["next_cursor"] is None
