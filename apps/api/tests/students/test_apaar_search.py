"""Exact search by APAAR ID (ADR-0037 §search, FR-STU-016; SEC-008, SEC-013, SEC-015,
invariants 2, 3, 4 and 5).

``POST /students/search`` takes an explicit ``apaar_id`` (12 digits, spaces or hyphens allowed)
that matches only the typed ``apaar_id`` attribute: current values that are verified or recorded
(not rejected, not superseded), within the caller's scope. The free-text Aadhaar guard is
unchanged everywhere else. Synthetic data only: Verhoeff-valid numbers are built in the test.
"""

from __future__ import annotations

import random
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit, verhoeff_valid
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
BASE = "/api/v1/students"
SEARCH = "/api/v1/students/search"
_RNG = random.Random(20261003)


def apaar_number() -> str:
    """A fresh Verhoeff-valid 12-digit number (the hardest case for the Aadhaar guard)."""
    body = "".join(str(_RNG.randrange(10)) for _ in range(11))
    number = body + verhoeff_check_digit(body)
    assert verhoeff_valid(number)
    return number


def spaced(number: str) -> str:
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def student_with_apaar(
    school: Any, number: str, *, section_key: str = "section_9c", source: str = "udise_plus"
) -> uuid.UUID:
    return SW.create(  # type: ignore[no-any-return]
        school,
        name=f"Synthetica Apaar {uuid.uuid4().hex[:6]}",
        section_key=section_key,
        extra=[ValueIn(attribute_key="apaar_id", source=source, value=number)],
    )


def _ids(res: Any) -> list[str]:
    assert res.status_code == 200, res.text
    return [item["id"] for item in res.json()["data"]]


def test_FR_STU_016_exact_apaar_search_finds_the_student(world: Any, api: Any) -> None:
    number = apaar_number()
    sid = student_with_apaar(world.a, number)
    who = world.person("office_admin")
    for typed in (number, spaced(number), f"{number[:4]}-{number[4:8]}-{number[8:]}"):
        res = api.call(who, "POST", SEARCH, json={"apaar_id": typed})
        assert _ids(res) == [str(sid)], typed
        assert res.json()["data"][0]["match"] == {"field": "apaar_id", "score": None}
        assert res.request.url.query == b""
    # Exact only: another number finds nobody (a prefix is not 12 digits: 422, below).
    other = apaar_number()
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": other})) == []
    # Combined with the other filters (AND).
    in_9c = {"apaar_id": number, "section_id": str(world.a.ids["section_9c"])}
    assert _ids(api.call(who, "POST", SEARCH, json=in_9c)) == [str(sid)]
    in_9a = {"apaar_id": number, "section_id": str(world.a.ids["section_9a"])}
    assert _ids(api.call(who, "POST", SEARCH, json=in_9a)) == []


def test_FR_STU_016_only_current_verified_or_recorded_values_match(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    who = world.person("office_admin")
    number = apaar_number()
    sid = student_with_apaar(world.a, number, source="parent_form")
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": number})) == [str(sid)]

    value_id = SW.current_value_id(admin_engine, sid, "apaar_id", "parent_form")
    ok = api.call(who, "POST", f"{BASE}/{sid}/values/{value_id}/verify", json={})
    assert ok.status_code == 200, ok.text
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": number})) == [str(sid)]

    # A corrected value supersedes the old one: the old number no longer finds the student.
    corrected = apaar_number()
    res = api.call(
        who,
        "POST",
        f"{BASE}/{sid}/values",
        json={"attribute_key": "apaar_id", "source": "parent_form", "value": corrected},
    )
    assert res.status_code == 201, res.text
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": number})) == []
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": corrected})) == [str(sid)]

    # A rejected value is not a match.
    rejected = api.call(
        who,
        "POST",
        f"{BASE}/{sid}/values/{res.json()['id']}/verify",
        json={"status": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    assert _ids(api.call(who, "POST", SEARCH, json={"apaar_id": corrected})) == []


def test_FR_STU_016_the_number_in_another_field_never_matches(world: Any, api: Any) -> None:
    """Only the typed ``apaar_id`` attribute is searched: the same digits stored as the
    admission number are not an APAAR match."""
    number = apaar_number()
    admission = f"SYN{number[:8]}"
    sid = SW.create(
        world.a, name="Synthetica Not Apaar", section_key="section_9c", admission_no=admission
    )
    who = world.person("office_admin")
    assert str(sid) not in _ids(api.call(who, "POST", SEARCH, json={"apaar_id": number}))


def test_SEC_015_apaar_search_respects_scope_and_school(world: Any, api: Any) -> None:
    number = apaar_number()
    in_9c = student_with_apaar(world.a, number, section_key="section_9c")
    # The class teacher of 9A does not see a 9C student by APAAR ID (BOLA, US-302 AC2).
    for role in ("class_teacher", "teacher"):
        got = _ids(api.call(world.person(role), "POST", SEARCH, json={"apaar_id": number}))
        assert got == [], role
    # The same number in 9A is visible to the class teacher of 9A.
    number_9a = apaar_number()
    in_9a = student_with_apaar(world.a, number_9a, section_key="section_9a")
    got = _ids(
        api.call(world.person("class_teacher"), "POST", SEARCH, json={"apaar_id": number_9a})
    )
    assert got == [str(in_9a)]
    # Another school's student with the same APAAR ID is never found (RLS), and vice versa.
    other = student_with_apaar(world.b, number, section_key="section_9a")
    owner_a = world.person("owner")
    assert _ids(api.call(owner_a, "POST", SEARCH, json={"apaar_id": number})) == [str(in_9c)]
    owner_b = world.b.people["owner"]
    got_b = _ids(
        api.call(owner_b, "POST", SEARCH, json={"apaar_id": number}, tenant=world.b.tenant_id)
    )
    assert got_b == [str(other)]


def test_SEC_003_apaar_search_needs_student_read_basic(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    number = apaar_number()
    student_with_apaar(world.a, number)
    nobody = W.add_member(admin_engine, world.a.tenant_id, [])
    res = api.call(nobody, "POST", SEARCH, json={"apaar_id": number})
    assert res.status_code == 403
    assert number not in res.text
    assert api.client.post(SEARCH, json={"apaar_id": number}).status_code == 401


@pytest.mark.parametrize(
    "value",
    ["12345678901", "1234567890123", "1234 5678 901X", "APAAR 123456789012", "", "1" * 33],
)
def test_FR_STU_016_apaar_search_refuses_anything_but_12_digits(
    world: Any, api: Any, value: str
) -> None:
    res = api.call(world.person("office_admin"), "POST", SEARCH, json={"apaar_id": value})
    assert res.status_code == 422, res.text
    if value:
        assert value not in res.text, "the submitted value is never echoed"


def test_SEC_013_free_text_aadhaar_guard_is_unchanged(world: Any, api: Any) -> None:
    """A Verhoeff-valid 12-digit number is still refused in ``query``, ``admission_no`` and
    the deprecated GET, also when an ``apaar_id`` is sent alongside (invariant 4)."""
    number = apaar_number()
    student_with_apaar(world.a, number)
    who = world.person("office_admin")
    for body in (
        {"query": number},
        {"query": spaced(number)},
        {"admission_no": number},
        {"apaar_id": number, "query": number},
        {"apaar_id": number, "admission_no": spaced(number)},
    ):
        res = api.call(who, "POST", SEARCH, json=body)
        assert res.status_code == 422, body
        codes = {e["code"] for e in res.json()["errors"]}
        assert codes == {"aadhaar_full_number_rejected"}, body
        assert "apaar_id" not in {e["field"] for e in res.json()["errors"]}, body
    via_get = api.call(who, "GET", BASE, params={"query": number})
    assert via_get.status_code == 422
    # The search body is the only route that takes the exemption.
    assert "apaar_id" not in {
        p["name"]
        for p in api.client.app.openapi()["paths"]["/api/v1/students"]["get"]["parameters"]
    }


def test_PRV_020_apaar_search_value_is_never_logged(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    number = apaar_number()
    student_with_apaar(world.a, number)
    who = world.person("office_admin")
    capsys.readouterr()
    api.call(who, "POST", SEARCH, json={"apaar_id": spaced(number)})
    api.call(who, "POST", SEARCH, json={"apaar_id": number, "query": number})  # refused
    api.call(who, "POST", SEARCH, json={"apaar_id": number[:11]})  # invalid
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    for secret in (number, spaced(number), number[:11]):
        assert secret not in logs, secret
