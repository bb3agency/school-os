"""CBSE Appendix IX format version 2 through the routes (FC-02; ADR-0051 amendment 2026-11-09;
FR-DISC-013..FR-DISC-019; lead decision D84). Real database (RLS), synthetic data only."""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import ratelimit as rl

pytestmark = pytest.mark.db
S = sys.modules["sos_test_disclosure_support"]
BASE = S.BASE

QUALIFICATIONS = {
    "principal": {"postgraduate_bed": 1},
    "pgt": {"doctorate": 1, "postgraduate_bed": 10},
    "tgt": {"graduate_bed": 14},
}


def _public(client: TestClient, key: str) -> Any:
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]
    return S.P.public_call(client, "GET", f"/api/v1/public/disclosure/{key}")


def _items(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for s in sections for i in s["items"]}


def _v1_draft(admin: Engine, school: Any, inputs: dict[str, Any], docs: dict[str, Any]) -> None:
    """A draft as version 1 saved it before FC-02 (synthetic)."""
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.disclosure_drafts (id, tenant_id, format_key, format_version, "
                "inputs, documents, updated_by) VALUES (:i, :t, 'cbse-appendix-ix', 1, "
                "CAST(:inp AS jsonb), CAST(:doc AS jsonb), :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "inp": json.dumps(inputs),
                "doc": json.dumps(docs),
                "u": school.people["owner"].user_id,
            },
        )


# --- format and derived values -------------------------------------------------------------------


def test_FR_DISC_013_the_format_route_serves_version_2(api: Any, school: Any) -> None:
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/format")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 2
    assert body["verified"] is False
    items = _items(body["sections"])
    table = items["staff_qualifications"]
    assert table["type"] == "count_table"
    assert [r["key"] for r in table["rows"]][:3] == ["principal", "vice_principal", "head_teacher"]
    assert table["columns"][0]["key"] == "doctorate"
    assert items["cwsn_toilets"]["type"] == "integer"
    assert items["fire_safety"]["rows"] is None


def test_FR_DISC_013_editing_needs_the_permission(api: Any, school: Any) -> None:
    for role in S.ROLES:
        res = api.call(school.people[role], "GET", f"{BASE}/format")
        allowed = role in {"owner", "principal", "office_admin"}
        assert res.status_code == (200 if allowed else 403), (role, res.text)


def test_FR_DISC_015_registered_students_per_class_with_small_counts_suppressed(
    api: Any, school: Any
) -> None:
    for n in range(10):
        S.SW.create(school, name=f"Synthetica Nine {n}", section_key="section_9a")
    S.SW.create(school, name="Synthetica Nine C", section_key="section_9c")
    S.SW.create(school, name="Synthetica Ten A", section_key="section_10a")
    S.SW.create(school, name="Synthetica Unplaced", section_key=None)
    res = api.call(school.people["principal"], "GET", f"{BASE}/draft")
    assert res.status_code == 200, res.text
    table = res.json()["from_schoolos"]["registered_students"]["table"]
    assert table["columns"] == ["Class", "Registered students"]
    assert table["rows"] == [
        ["Class IX", "11"],
        ["Class X", "fewer than 10"],
        ["Total", "12"],
    ]
    # Nothing that names or identifies a child.
    assert "Synthetica" not in res.text


def test_FR_DISC_015_no_students_means_not_recorded(api: Any, school: Any) -> None:
    res = api.call(school.people["principal"], "GET", f"{BASE}/draft")
    assert res.json()["from_schoolos"]["registered_students"] == {"value": None, "table": None}


def test_FR_DISC_016_staff_strength_by_category_is_counts(api: Any, school: Any) -> None:
    res = api.call(school.people["principal"], "GET", f"{BASE}/draft")
    table = res.json()["from_schoolos"]["staff_by_category"]["table"]
    assert table["columns"] == ["Category", "Number"]
    # One member per role: principal; teacher and class teacher; six other roles.
    assert table["rows"] == [
        ["Principal", "1"],
        ["Teachers", "2"],
        ["Other staff", "6"],
        ["Total", "9"],
    ]
    for role in ("principal", "teacher", "office_admin"):
        assert school.people[role].display_name not in json.dumps(table)


# --- draft and publish ---------------------------------------------------------------------------


def test_FR_DISC_016_count_tables_are_saved_and_refuse_names(api: Any, school: Any) -> None:
    who = school.people["office_admin"]
    saved = S.save_draft(
        api,
        who,
        inputs={**S.REQUIRED, "staff_qualifications": QUALIFICATIONS, "cwsn_toilets": 2},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["inputs"]["staff_qualifications"] == QUALIFICATIONS
    bad = S.save_draft(
        api,
        who,
        inputs={**S.REQUIRED, "staff_qualifications": {"pgt": {"doctorate": "Synthetica Rao"}}},
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["errors"][0]["field"] == "inputs.staff_qualifications"


def test_FR_DISC_018_version_2_is_published_and_shown_on_the_public_page(
    api: Any, school: Any, public_client: TestClient
) -> None:
    owner = school.people["owner"]
    inputs = {
        **S.REQUIRED,
        "vice_principal_count": 1,
        "staff_qualifications": QUALIFICATIONS,
        "cwsn_toilets": 2,
        "library_books": 4200,
        "library_area": 96.5,
    }
    assert S.save_draft(api, owner, inputs=inputs).status_code == 200
    snap = S.publish(api, owner)
    assert snap.status_code == 201, snap.text
    assert snap.json()["format_version"] == 2
    page = _public(public_client, S.link_key(api, owner))
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["format_version"] == 2
    items = _items(body["sections"])
    assert items["cwsn_toilets"]["value"] == "2"
    assert items["library_area"]["value"] == "96.5"
    assert items["staff_qualifications"]["table"]["rows"][1] == [
        "PGT",
        "1",
        "10",
        "",
        "",
        "",
        "11",
    ]
    assert items["staff_by_category"]["table"]["rows"][-1] == ["Total", "9"]


def test_FR_DISC_014_a_version_1_draft_is_carried_over(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    doc = S.document(admin_engine, school)
    _v1_draft(
        admin_engine,
        school,
        {**S.REQUIRED, "pgt_count": 12, "internet": True, "retired_item": "x"},
        {"fire_safety": [str(doc)]},
    )
    who = school.people["office_admin"]
    got = api.call(who, "GET", f"{BASE}/draft")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["format_version"] == 2
    assert body["carried_over_from"] == 1
    assert body["dropped"] == ["retired_item"]
    assert body["inputs"] == {**S.REQUIRED, "pgt_count": 12, "internet": True}
    assert [d["id"] for d in body["documents"]["fire_safety"]] == [str(doc)]
    # Saving moves the draft to version 2 (audited with the version it came from).
    saved = api.call(
        who,
        "PUT",
        f"{BASE}/draft",
        json={"inputs": body["inputs"], "documents": {"fire_safety": [str(doc)]}},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["carried_over_from"] is None
    assert saved.json()["dropped"] == []
    stored = S.rows(
        admin_engine,
        "SELECT format_version, inputs FROM ops.disclosure_drafts WHERE tenant_id = :t",
        t=school.tenant_id,
    )[0]
    assert stored["format_version"] == 2
    assert "retired_item" not in stored["inputs"]
    events = S.W.audit_events(admin_engine, school.tenant_id, "disclosure.draft_saved")
    assert events[-1]["summary"]["from_version"] == 1
    assert events[-1]["summary"]["format_version"] == 2


def test_FR_DISC_014_publishing_a_version_1_draft_publishes_version_2(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    _v1_draft(admin_engine, school, {**S.REQUIRED, "retired_item": "x"}, {})
    res = S.publish(api, school.people["owner"])
    assert res.status_code == 201, res.text
    assert res.json()["format_version"] == 2
    content = S.scalar(
        admin_engine,
        "SELECT content FROM ops.disclosure_snapshots WHERE id = :i",
        i=res.json()["id"],
    )
    assert content["format_version"] == 2
    assert "retired_item" not in json.dumps(content)


def test_FR_DISC_019_version_1_snapshots_stay_as_published(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    sec = sys.modules.get("sos_test_disclosure_security_support") or S._load(
        "sos_test_disclosure_security_support", S._HERE.with_name("security_support.py")
    )
    old = sec.insert_snapshot(
        admin_engine, school.tenant_id, school.people["owner"].user_id, days_ago=30
    )
    S.save_draft(api, school.people["owner"])
    new = S.publish(api, school.people["owner"])
    assert new.status_code == 201, new.text
    history = api.call(school.people["principal"], "GET", f"{BASE}/snapshots").json()
    versions = {h["id"]: (h["format_version"], h["live"]) for h in history}
    assert versions[str(old)] == (1, False)
    assert versions[new.json()["id"]] == (2, True)
    detail = api.call(school.people["principal"], "GET", f"{BASE}/snapshots/{old}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["content"]["format_version"] == 1


def test_FR_DISC_019_another_schools_v1_snapshot_is_404(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    other = S.cbse_school(admin_engine)
    sec = sys.modules.get("sos_test_disclosure_security_support") or S._load(
        "sos_test_disclosure_security_support", S._HERE.with_name("security_support.py")
    )
    theirs = sec.insert_snapshot(admin_engine, other.tenant_id, other.people["owner"].user_id)
    res = api.call(school.people["principal"], "GET", f"{BASE}/snapshots/{theirs}")
    assert res.status_code == 404
