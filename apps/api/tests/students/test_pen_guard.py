"""UDISE+ PEN format, search and the transfer-in duplicate guard (ADR-0039; FR-STU-017..019,
US-1904, PRV-020). Synthetic data only."""

from __future__ import annotations

import random
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.devtools.fake_ids import synthetic_apaar_id

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
BASE = "/api/v1/students"
RNG = random.Random(uuid.uuid4().int)


def pen() -> str:
    return str(RNG.randint(1, 9)) + "".join(str(RNG.randint(0, 9)) for _ in range(10))


def new_student(api: Any, who: Any, *values: tuple[str, str, str], **extra: Any) -> Any:
    body: dict[str, Any] = {
        "values": [
            {
                "attribute_key": "full_name",
                "source": "admission_register",
                "value": "Synthetica Pen",
            },
            *({"attribute_key": k, "source": s, "value": v} for k, s, v in values),
        ],
        **extra,
    }
    return api.call(who, "POST", BASE, json=body)


def record(api: Any, who: Any, sid: str, key: str, source: str, value: str) -> Any:  # noqa: PLR0917
    return api.call(
        who,
        "POST",
        f"{BASE}/{sid}/values",
        json={"attribute_key": key, "source": source, "value": value},
    )


def test_FR_STU_017_pen_is_eleven_digits_and_separators_are_removed(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    sid = new_student(api, admin).json()["id"]
    number = pen()
    spaced = f"{number[:4]} {number[4:8]}-{number[8:]}"
    ok = record(api, admin, sid, "udise_pen", "udise_plus", spaced)
    assert ok.status_code == 201, ok.text
    with admin_engine.connect() as c:
        stored: str = c.execute(
            text("SELECT value_text FROM sis.attribute_values WHERE id = :i"),
            {"i": ok.json()["id"]},
        ).scalar_one()
    assert stored == number
    for bad in ("2134 5678", "AB345678901", "2134567890123", "2134567890123456"):
        res = record(api, admin, sid, "udise_pen", "manual_entry", bad)
        assert res.status_code == 422, bad
        assert res.json()["errors"][0]["code"] in {"digits11_required", "too_long"}
    tc = record(api, admin, sid, "udise_pen", "tc_incoming", number)
    assert tc.status_code == 201, "the PEN on the previous school's TC is a source"


def test_FR_STU_018_second_record_with_the_same_pen_is_refused_with_a_link(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    number = pen()
    first = new_student(api, admin, ("udise_pen", "udise_plus", number))
    assert first.status_code == 201, first.text
    again = new_student(api, admin, ("udise_pen", "tc_incoming", number))
    assert again.status_code == 422
    (problem,) = again.json()["errors"]
    assert problem["code"] == "national_id_in_use"
    assert problem["message_key"] == "errors.udise_pen_in_use"
    assert problem["field"] == "values.1.value"
    assert problem["student_id"] == first.json()["id"]
    # On an existing record too.
    other = new_student(api, admin).json()["id"]
    clash = record(api, admin, other, "udise_pen", "manual_entry", number)
    assert clash.status_code == 422
    assert clash.json()["errors"][0]["student_id"] == first.json()["id"]
    # The same student may confirm its own PEN from another source.
    own = record(api, admin, first.json()["id"], "udise_pen", "manual_entry", number)
    assert own.status_code == 201


def test_FR_STU_018_apaar_id_is_guarded_too_and_left_students_free_the_number(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    apaar = synthetic_apaar_id(RNG)
    first = new_student(api, admin, ("apaar_id", "udise_plus", apaar)).json()["id"]
    refused = new_student(api, admin, ("apaar_id", "parent_form", apaar))
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["message_key"] == "errors.apaar_id_in_use"
    with admin_engine.begin() as c:
        c.execute(text("UPDATE sis.students SET status = 'left' WHERE id = :i"), {"i": first})
    assert new_student(api, admin, ("apaar_id", "parent_form", apaar)).status_code == 201


def test_FR_STU_018_transfer_in_needs_a_pen(world: Any, api: Any, admin_engine: Engine) -> None:
    admin = world.person("office_admin")
    missing = new_student(api, admin, admission_kind="transfer_in")
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["code"] == "pen_required_for_transfer_in"
    ok = new_student(api, admin, ("udise_pen", "tc_incoming", pen()), admission_kind="transfer_in")
    assert ok.status_code == 201, ok.text
    events = SW.W.audit_events(admin_engine, world.a.tenant_id, "student.created")
    mine = [e for e in events if str(e["resource_id"]) == ok.json()["id"]]
    assert mine[0]["summary"]["admission_kind"] == "transfer_in"


def test_FR_STU_018_national_id_check_tells_the_udise_action(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    number, fresh = pen(), pen()
    sid = new_student(api, admin, ("udise_pen", "udise_plus", number)).json()["id"]
    url = f"{BASE}/national-id-check"
    live = api.call(admin, "POST", url, json={"udise_pen": number}).json()
    assert live["udise_action"] == "open_existing_record"
    assert [m["student_id"] for m in live["matches"]] == [sid]
    assert live["matches"][0]["attribute_key"] == "udise_pen"
    new = api.call(admin, "POST", url, json={"udise_pen": fresh}).json()
    assert new == {"matches": [], "udise_action": "import_by_pen"}
    with admin_engine.begin() as c:
        c.execute(text("UPDATE sis.students SET status = 'left' WHERE id = :i"), {"i": sid})
    former = api.call(admin, "POST", url, json={"udise_pen": number}).json()
    assert former["udise_action"] == "readmit_existing_record"
    no_pen = api.call(admin, "POST", url, json={"apaar_id": synthetic_apaar_id(RNG)}).json()
    assert no_pen["udise_action"] == "new_udise_record"
    bad = api.call(admin, "POST", url, json={"udise_pen": "12345"})
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "digits11_required"
    # student.create only (school-wide): a class teacher may not probe numbers.
    assert (
        api.call(world.person("class_teacher"), "POST", url, json={"udise_pen": number}).status_code
        == 403
    )


def test_FR_STU_018_other_schools_pen_is_not_seen(world: Any, api: Any) -> None:
    number = pen()
    owner_b = world.b.people["owner"]
    SW.configure_keyring()
    from app.core.db import tenant_session
    from app.students import service as students
    from app.students.schemas import StudentCreate, ValueIn

    with tenant_session(world.b.tenant_id, owner_b.user_id) as db:
        students.create_student(
            db,
            SW.admin_ctx(world.b),
            StudentCreate(
                values=[
                    ValueIn(attribute_key="full_name", source="admission_register", value="B Pen"),
                    ValueIn(attribute_key="udise_pen", source="udise_plus", value=number),
                ]
            ),
        )
    admin = world.person("office_admin")
    check = api.call(admin, "POST", f"{BASE}/national-id-check", json={"udise_pen": number})
    assert check.json() == {"matches": [], "udise_action": "import_by_pen"}
    assert new_student(api, admin, ("udise_pen", "udise_plus", number)).status_code == 201


def test_FR_STU_019_search_by_exact_pen_respects_scope(world: Any, api: Any) -> None:
    number = pen()
    sid = SW.create(
        world.a,
        name="Synthetica Pen Search",
        section_key="section_9c",
        extra=[SW.ValueIn(attribute_key="udise_pen", source="udise_plus", value=number)],
    )
    admin = world.person("office_admin")
    found = api.call(
        admin, "POST", f"{BASE}/search", json={"udise_pen": f"{number[:5]} {number[5:]}"}
    )
    assert found.status_code == 200, found.text
    assert [s["id"] for s in found.json()["data"]] == [str(sid)]
    assert found.json()["data"][0]["match"]["field"] == "udise_pen"
    teacher = api.call(
        world.person("class_teacher"), "POST", f"{BASE}/search", json={"udise_pen": number}
    )
    assert teacher.json()["data"] == [], "9C is outside the class teacher's section"
    bad = api.call(admin, "POST", f"{BASE}/search", json={"udise_pen": "1234"})
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "digits11_required"
