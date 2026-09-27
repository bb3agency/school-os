"""``POST /students/search`` over HTTP: personal data in the body, never in the URL
(SEC-008, FR-STU-010, US-301, US-302; invariants 2, 3 and 5). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
GET = "/api/v1/students"
SEARCH = "/api/v1/students/search"


def _ids(res: Any) -> list[str]:
    assert res.status_code == 200, res.text
    return [item["id"] for item in res.json()["data"]]


def _admission_no(api: Any, who: Any, student_id: uuid.UUID) -> str:
    res = api.call(who, "GET", f"{GET}/{student_id}")
    assert res.status_code == 200, res.text
    value = res.json()["admission_no"]
    assert value
    return str(value)


def test_SEC_008_search_sends_names_in_the_body_not_the_url(
    world: Any, api: Any, shared: dict[str, Any]
) -> None:
    res = api.call(
        world.person("office_admin"), "POST", SEARCH, json={"query": "Synthetica Venkata"}
    )
    assert str(shared["s9a"]) in _ids(res)
    assert res.request.url.query == b""
    assert b"Synthetica" not in res.request.url.raw_path
    assert "Deprecation" not in res.headers


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"query": "synthetica"},
        {"query": "Synthetica Venkata"},
        {"query": "9a"},
        {"query": "synthetica ramana"},  # parent name
        {"status": "active"},
        {"section_key": "section_9a"},
        {"class_key": "class_ix"},
        {"query": "synthetica", "section_key": "section_9c"},
    ],
    ids=lambda f: ",".join(sorted(f)) or "none",
)
@pytest.mark.parametrize("role", ["owner", "class_teacher", "teacher"])
def test_FR_STU_010_post_search_matches_get(
    world: Any, api: Any, shared: dict[str, Any], filters: dict[str, Any], role: str
) -> None:
    """Same filters, same scope (SEC-015), same results as the deprecated GET."""
    params: dict[str, Any] = {k: v for k, v in filters.items() if not k.endswith("_key")}
    if "section_key" in filters:
        params["section_id"] = str(world.a.ids[filters["section_key"]])
    if "class_key" in filters:
        params["class_id"] = str(world.a.ids[filters["class_key"]])
    who = world.person(role)
    via_get = _ids(api.call(who, "GET", GET, params={**params, "limit": 200}))
    via_post = _ids(api.call(who, "POST", SEARCH, json={**params, "limit": 200}))
    assert via_post == via_get


def test_FR_STU_010_post_search_by_admission_number(world: Any, api: Any) -> None:
    number = f"SYN{uuid.uuid4().hex[:8].upper()}"
    sid = SW.create(
        world.a, name="Synthetica Admission Search", section_key="section_9c", admission_no=number
    )
    who = world.person("office_admin")
    assert _admission_no(api, who, sid) == number
    by_filter = _ids(api.call(who, "POST", SEARCH, json={"admission_no": number}))
    assert by_filter == [str(sid)]
    by_query = _ids(api.call(who, "POST", SEARCH, json={"query": number}))
    assert str(sid) in by_query
    via_get = _ids(api.call(who, "GET", GET, params={"admission_no": number}))
    assert via_get == by_filter
    # Scope still applies: the class teacher of 9A does not see a 9C student by number.
    teacher = world.person("class_teacher")
    assert _ids(api.call(teacher, "POST", SEARCH, json={"admission_no": number})) == []


def test_FR_STU_010_post_search_cursor_pages_like_get(
    world: Any, api: Any, shared: dict[str, Any]
) -> None:
    who = world.person("owner")
    everything = _ids(api.call(who, "POST", SEARCH, json={"query": "synthetica", "limit": 200}))
    assert len(everything) >= 3
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(len(everything) + 1):
        body: dict[str, Any] = {"query": "synthetica", "limit": 1}
        if cursor:
            body["cursor"] = cursor
        res = api.call(who, "POST", SEARCH, json=body)
        page = _ids(res)
        assert len(page) <= 1
        seen.extend(page)
        cursor = res.json()["next_cursor"]
        if cursor is None:
            break
    assert seen == everything
    bad = api.call(who, "POST", SEARCH, json={"query": "synthetica", "cursor": "not-a-cursor"})
    assert bad.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"limit": 0},
        {"limit": 201},
        {"query": "x" * 201},
        {"admission_no": "1" * 33},
        {"status": "expelled"},
        {"section_id": "not-a-uuid"},
        {"name": "Synthetica"},  # unknown fields are refused, not ignored
    ],
)
def test_FR_STU_010_post_search_validates_the_body(world: Any, api: Any, body: Any) -> None:
    res = api.call(world.person("office_admin"), "POST", SEARCH, json=body)
    assert res.status_code == 422, res.text


def test_SEC_003_post_search_needs_student_read_basic(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    nobody = W.add_member(admin_engine, world.a.tenant_id, [])
    res = api.call(nobody, "POST", SEARCH, json={"query": "synthetica"})
    assert res.status_code == 403
    assert "Synthetica" not in res.text
    anonymous = api.client.post(SEARCH, json={"query": "synthetica"})
    assert anonymous.status_code == 401


def test_SEC_001_post_search_never_reaches_other_school(
    world: Any, api: Any, shared: dict[str, Any]
) -> None:
    owner = world.person("owner")
    for body in (
        {"query": "Synthetica Other School"},
        {"query": "synthetica guardian b"},
        {"section_id": str(world.b.ids["section_9a"])},
    ):
        got = _ids(api.call(owner, "POST", SEARCH, json={**body, "limit": 200}))
        assert str(shared["b_sb"]) not in got, body
    other_section = _ids(
        api.call(owner, "POST", SEARCH, json={"section_id": str(world.b.ids["section_9a"])})
    )
    assert other_section == []
    # A member of school A cannot switch into school B, whatever the method.
    via_get = api.call(owner, "GET", GET, tenant=world.b.tenant_id)
    via_post = api.call(owner, "POST", SEARCH, json={}, tenant=world.b.tenant_id)
    assert via_post.status_code == via_get.status_code
    assert via_post.status_code in (403, 404)
    assert "Synthetica Other School" not in via_post.text


def test_SEC_008_get_search_by_name_is_deprecated(world: Any, api: Any) -> None:
    who = world.person("office_admin")
    named = api.call(who, "GET", GET, params={"query": "synthetica"})
    assert named.status_code == 200
    assert named.headers["Deprecation"].startswith("@")
    assert "/api/v1/students/search" in named.headers["Link"]
    listing = api.call(who, "GET", GET, params={"status": "active"})
    assert listing.status_code == 200
    assert "Deprecation" not in listing.headers
