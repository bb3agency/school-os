"""Student routes over HTTP (docs/09 Students; US-301, US-303, SEC-013, SEC-015)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
BASE = "/api/v1/students"


def aadhaar() -> str:
    body = "34567890123"
    return body + verhoeff_check_digit(body)


def grouped(n: str) -> str:
    return f"{n[:4]} {n[4:8]} {n[8:]}"


def new_body(name: str = "Synthetica Api Student", **extra: Any) -> dict[str, Any]:
    return {
        "values": [
            {"attribute_key": "full_name", "source": "admission_register", "value": name},
            {"attribute_key": "dob", "source": "admission_register", "value": "2013-01-05"},
        ],
        **extra,
    }


def count_students(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM sis.students WHERE tenant_id = :t"), {"t": tenant_id}
            ).scalar_one()
        )


def test_US_301_create_get_and_idempotent_replay(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    before = count_students(admin_engine, world.a.tenant_id)
    key = {"Idempotency-Key": f"create-{uuid.uuid4().hex}"}
    body = new_body(section_id=str(world.a.ids["section_9a"]), roll_no="17")
    res = api.call(admin, "POST", BASE, json=body, headers=key)
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    assert res.headers["Location"] == f"{BASE}/{sid}"
    assert res.headers["ETag"] == f'W/"{res.json()["version"]}"'
    again = api.call(admin, "POST", BASE, json=body, headers=key)
    assert again.status_code == 201
    assert again.json()["id"] == sid
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert count_students(admin_engine, world.a.tenant_id) == before + 1
    got = api.call(admin, "GET", f"{BASE}/{sid}")
    assert got.status_code == 200
    data = got.json()
    assert data["enrollment"]["label"] == "IX-A"
    assert data["enrollment"]["roll_no"] == "17"
    assert data["canonical"]["full_name"]["provisional"] is True
    assert got.headers["ETag"] == f'W/"{data["version"]}"'


def test_US_301_AC3_sensitive_fields_hidden_or_masked(
    world: Any, api: Any, shared: dict[str, Any]
) -> None:
    staff = api.call(world.person("office_staff"), "GET", f"{BASE}/{shared['s9a']}").json()
    assert "health_notes" not in staff["canonical"]
    assert "aadhaar_last4" not in staff["values"]
    assert staff["sensitive_revealable"] is False
    admin = api.call(world.person("office_admin"), "GET", f"{BASE}/{shared['s9a']}").json()
    assert admin["sensitive_revealable"] is True
    assert admin["canonical"]["health_notes"] == {
        "value": "••••",
        "source": "parent_form",
        "verified": False,
        "provisional": False,
        "masked": True,
        "conflicts": [],
    }
    assert admin["values"]["aadhaar_last4"][0]["value"] == "••••"
    assert "4821" not in str(admin)
    assert "asthma" not in str(admin)
    history = api.call(world.person("office_staff"), "GET", f"{BASE}/{shared['s9a']}/values")
    assert {v["attribute_key"] for v in history.json()}.isdisjoint(
        {"health_notes", "aadhaar_last4"}
    )


def test_US_301_AC3_reveal_route(
    world: Any, api: Any, shared: dict[str, Any], admin_engine: Engine
) -> None:
    path = f"{BASE}/{shared['s9a']}/sensitive-reveal"
    res = api.call(
        world.person("class_teacher"), "POST", path, json={"attribute_key": "health_notes"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["value"] == "Synthetic asthma note"
    assert res.headers["Cache-Control"] == "no-store"
    assert (
        api.call(
            world.person("office_staff"), "POST", path, json={"attribute_key": "health_notes"}
        ).status_code
        == 403
    )
    other = f"{BASE}/{shared['s9c']}/sensitive-reveal"
    assert (
        api.call(
            world.person("class_teacher"), "POST", other, json={"attribute_key": "health_notes"}
        ).status_code
        == 404
    )
    phone = api.call(
        world.person("office_admin"),
        "POST",
        path,
        json={"attribute_key": "guardian_phone", "guardian_id": str(shared["g9a"])},
    )
    assert phone.json()["display"] == "+91 98765 01234"
    revealed = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "student.sensitive_revealed")
        if e["resource_id"] == shared["s9a"]
    ]
    assert len(revealed) >= 2
    assert "Synthetic asthma" not in str(revealed)


AADHAAR_CASES = [
    ("POST", "", lambda w, s, n: new_body(n), "values.0.value"),
    ("POST", "", lambda w, s, n: new_body(roll_no=n[:12]), "roll_no"),
    (
        "POST",
        "/{sid}/values",
        lambda w, s, n: {
            "attribute_key": "caste",
            "source": "parent_form",
            "value": f"no {grouped(n)}",
        },
        "value",
    ),
    (
        "POST",
        "/{sid}/values",
        lambda w, s, n: {
            "attribute_key": "aadhaar_last4",
            "source": "aadhaar_as_printed",
            "value": n,
        },
        "value",
    ),
    (
        "POST",
        "/{sid}/guardians",
        lambda w, s, n: {"relationship": "father", "full_name": n},
        "full_name",
    ),
    (
        "POST",
        "/{sid}/guardians",
        lambda w, s, n: {
            "relationship": "father",
            "full_name": "Synthetica G",
            "phone": grouped(n),
        },
        "phone",
    ),
    (
        "POST",
        "/{sid}/guardians",
        lambda w, s, n: {
            "relationship": "father",
            "full_name": "Synthetica G",
            "address": f"{n} road",
        },
        "address",
    ),
    ("PATCH", "/{sid}/guardians/{gid}", lambda w, s, n: {"address": f"UID {n}"}, "address"),
    ("POST", "/{sid}/sensitive-reveal", lambda w, s, n: {"attribute_key": n}, "attribute_key"),
    ("PATCH", "/{sid}", lambda w, s, n: {"status": n}, "status"),
]


@pytest.mark.parametrize(("method", "suffix", "body", "field"), AADHAAR_CASES)
def test_FR_STU_012_every_text_input_rejects_full_aadhaar(
    *,
    world: Any,
    api: Any,
    shared: dict[str, Any],
    admin_engine: Engine,
    method: str,
    suffix: str,
    body: Any,
    field: str,
) -> None:
    n = aadhaar()
    path = BASE + suffix.format(sid=shared["s9a"], gid=shared["g9a"])
    before = W.audit_events(admin_engine, world.a.tenant_id)
    res = api.call(
        world.person("office_admin"),
        method,
        path,
        json=body(world, shared, n),
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 422, res.text
    problem = res.json()
    assert problem["errors"] == [
        {
            "field": field,
            "code": "aadhaar_full_number_rejected",
            "message_key": "errors.aadhaar_last4_only",
        }
    ]
    assert n not in res.text
    assert grouped(n) not in res.text
    assert W.audit_events(admin_engine, world.a.tenant_id) == before, "nothing was written"


def test_FR_STU_012_search_query_rejects_full_aadhaar(world: Any, api: Any) -> None:
    res = api.call(world.person("office_admin"), "GET", BASE, params={"query": grouped(aadhaar())})
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "aadhaar_full_number_rejected"


def test_BR_01_identity_change_required_over_http(
    world: Any, api: Any, shared: dict[str, Any]
) -> None:
    res = api.call(
        world.person("office_staff"),
        "POST",
        f"{BASE}/{shared['s9c']}/values",
        json={"attribute_key": "dob", "source": "admission_register", "value": "2012-03-15"},
    )
    assert res.status_code == 403
    assert res.json()["code"] == "identity_change_required"


def test_values_etag_verify_and_status_patch(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    sid = api.call(admin, "POST", BASE, json=new_body("Synthetica Etag Case")).json()["id"]
    version = SW.version(admin_engine, "sis.students", uuid.UUID(sid))
    stale = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/values",
        json={"attribute_key": "religion", "source": "parent_form", "value": "Synthetic"},
        headers={"If-Match": f'W/"{version + 3}"'},
    )
    assert stale.status_code == 412
    rec = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/values",
        json={"attribute_key": "mother_tongue", "source": "parent_form", "value": "Telugu"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert rec.status_code == 201
    assert rec.headers["ETag"] == f'W/"{version + 1}"'
    verify = api.call(
        admin, "POST", f"{BASE}/{sid}/values/{rec.json()['id']}/verify", json={"status": "verified"}
    )
    assert verify.status_code == 200
    assert verify.json()["verification_status"] == "verified"
    no_match = api.call(admin, "PATCH", f"{BASE}/{sid}", json={"status": "left"})
    assert no_match.status_code == 400
    assert no_match.json()["code"] == "if_match_required"
    etag = api.call(admin, "GET", f"{BASE}/{sid}").headers["ETag"]
    patched = api.call(
        admin, "PATCH", f"{BASE}/{sid}", json={"status": "left"}, headers={"If-Match": etag}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "left"
    history = api.call(admin, "GET", f"{BASE}/{sid}/values", params={"attribute": "mother_tongue"})
    assert [v["value"] for v in history.json()] == ["Telugu"]
    unknown = api.call(admin, "GET", f"{BASE}/{sid}/values", params={"attribute": "no_such_key"})
    assert unknown.status_code == 422


def test_guardians_and_enrollments_routes(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    sid = api.call(admin, "POST", BASE, json=new_body("Synthetica Guardian Case")).json()["id"]
    g = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/guardians",
        json={"relationship": "mother", "full_name": "Synthetica Mother", "phone": "9876533333"},
    )
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    listed = api.call(world.person("office_staff"), "GET", f"{BASE}/{sid}/guardians").json()
    assert listed[0]["full_name"] == "Synthetica Mother"
    assert listed[0]["phone"] is None, "hidden without read_sensitive"
    assert listed[0]["has_phone"] is False, "hidden without read_sensitive"
    stale = api.call(
        admin,
        "PATCH",
        f"{BASE}/{sid}/guardians/{gid}",
        json={"is_primary": True},
        headers={"If-Match": 'W/"9"'},
    )
    assert stale.status_code == 412
    ok = api.call(
        admin,
        "PATCH",
        f"{BASE}/{sid}/guardians/{gid}",
        json={"is_primary": True},
        headers={"If-Match": g.headers["ETag"]},
    )
    assert ok.status_code == 200
    assert ok.json()["is_primary"] is True
    enrol = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(world.a.ids["section_10a"])},
    )
    assert enrol.status_code == 201
    assert enrol.json()["status"] == "active"
    bad = api.call(
        admin,
        "POST",
        f"{BASE}/{sid}/enrollments",
        json={"section_id": str(world.b.ids["section_9a"])},
    )
    assert bad.status_code == 422


def test_attribute_catalog_route(world: Any, api: Any) -> None:
    res = api.call(world.person("teacher"), "GET", "/api/v1/attributes")
    assert res.status_code == 200
    by_key = {a["key"]: a for a in res.json()}
    assert by_key["full_name"]["is_identity"] is True
    assert by_key["full_name"]["label_te"]
    assert by_key["aadhaar_last4"]["classification"] == "C3"
    assert by_key["aadhaar_last4"]["allowed_sources"] == ["aadhaar_as_printed"]


@pytest.mark.parametrize(
    ("role", "student", "status"),
    [
        ("class_teacher", "s9a", 200),
        ("class_teacher", "s9c", 404),
        ("class_teacher", "s10a", 404),
        ("teacher", "s10a", 200),
        ("teacher", "s9a", 404),
        ("teacher", "s9c", 404),
        ("accountant", "s9c", 200),
    ],
)
def test_SEC_015_student_scope_over_http(
    *, world: Any, api: Any, shared: dict[str, Any], role: str, student: str, status: int
) -> None:
    who = world.person(role)
    for path in ("", "/values", "/guardians"):
        res = api.call(who, "GET", f"{BASE}/{shared[student]}{path}")
        assert res.status_code == status, (path, res.text)
    if status == 404:
        random = api.call(who, "GET", f"{BASE}/{uuid.uuid4()}").json()
        hidden = api.call(who, "GET", f"{BASE}/{shared[student]}").json()
        assert {k: hidden[k] for k in ("status", "code", "detail")} == {
            k: random[k] for k in ("status", "code", "detail")
        }
