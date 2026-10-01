"""APAAR ID and UDISE+ PEN over HTTP (ADR-0037; FR-STU-013..015, PRV-020, US-301 AC4, US-303 AC2).

Synthetic data only: the Verhoeff-valid number is built inside the test process (docs/12 §3).
"""

from __future__ import annotations

import random
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.redaction import verhoeff_check_digit, verhoeff_valid
from app.devtools.fake_ids import synthetic_apaar_id

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
BASE = "/api/v1/students"


def verhoeff_apaar() -> str:
    body = "56781234567"
    number = body + verhoeff_check_digit(body)
    assert verhoeff_valid(number)
    return number


def new_student(api: Any, who: Any, name: str, section: str | None = None) -> str:
    body: dict[str, Any] = {
        "values": [{"attribute_key": "full_name", "source": "admission_register", "value": name}]
    }
    if section is not None:
        body["section_id"] = section
    res = api.call(who, "POST", BASE, json=body)
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def record(api: Any, who: Any, sid: str, key: str, source: str, value: str) -> Any:
    return api.call(
        who,
        "POST",
        f"{BASE}/{sid}/values",
        json={"attribute_key": key, "source": source, "value": value},
    )


def test_FR_STU_013_apaar_id_round_trips_unmasked_and_counts_once_verified(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Apaar Roundtrip")
    number = verhoeff_apaar()
    res = record(api, admin, sid, "apaar_id", "udise_plus", f"{number[:4]} {number[4:8]} {number[8:]}")
    assert res.status_code == 201, res.text
    value_id = res.json()["id"]
    with admin_engine.connect() as c:
        stored = c.execute(
            text("SELECT value_text, value_ciphertext FROM sis.attribute_values WHERE id = :i"),
            {"i": value_id},
        ).one()
    assert stored.value_text == number  # C2: plain column, 12 digits, never an Aadhaar field
    assert stored.value_ciphertext is None

    profile = api.call(admin, "GET", f"{BASE}/{sid}").json()
    canonical = profile["canonical"]["apaar_id"]
    assert canonical["value"] == number  # the typed field is shown unmasked
    assert canonical["masked"] is False
    assert canonical["verified"] is False
    assert canonical["provisional"] is True, "an unverified APAAR ID does not count yet"
    (value,) = profile["values"]["apaar_id"]
    assert value["value"] == number
    assert value["source"] == "udise_plus"
    assert value["verification_status"] == "unverified"

    ok = api.call(admin, "POST", f"{BASE}/{sid}/values/{value_id}/verify", json={})
    assert ok.status_code == 200, ok.text
    canonical = api.call(admin, "GET", f"{BASE}/{sid}").json()["canonical"]["apaar_id"]
    assert canonical["verified"] is True
    assert canonical["provisional"] is False
    history = api.call(admin, "GET", f"{BASE}/{sid}/values", params={"attribute": "apaar_id"})
    assert [v["value"] for v in history.json()] == [number]


def test_FR_STU_014_udise_pen_from_udise_plus_or_office(world: Any, api: Any) -> None:
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Pen Case")
    assert record(api, admin, sid, "udise_pen", "udise_plus", "21345678901").status_code == 201
    refused = record(api, admin, sid, "udise_pen", "parent_form", "21345678901")
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["code"] == "source_not_allowed"
    bad = record(api, admin, sid, "udise_pen", "manual_entry", "2134 5678")
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "invalid_format"


def test_FR_STU_013_apaar_id_correction_is_a_new_verified_value_not_a_change_request(
    world: Any, api: Any
) -> None:
    """Not an identity field (ADR-0037): the office records the right value and verifies it."""
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Apaar Correction")
    rng = random.Random(5)
    first, second = synthetic_apaar_id(rng), synthetic_apaar_id(rng)
    a = record(api, admin, sid, "apaar_id", "parent_form", first)
    assert a.status_code == 201
    b = record(api, admin, sid, "apaar_id", "parent_form", second)
    assert b.status_code == 201, "no identity_change_required for the APAAR ID"
    assert b.json()["superseded"] == a.json()["id"]
    history = api.call(admin, "GET", f"{BASE}/{sid}/values", params={"attribute": "apaar_id"})
    assert [v["value"] for v in history.json()] == [second, first]


@pytest.mark.parametrize(
    ("key", "source", "value"),
    [
        ("apaar_id", "udise_plus", "12345678901"),
        ("apaar_id", "udise_plus", "1234-5678-901X"),
        ("apaar_id", "admission_register", "123456789011"),
    ],
)
def test_FR_STU_015_apaar_id_refuses_bad_values(
    world: Any, api: Any, key: str, source: str, value: str
) -> None:
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Apaar Bad")
    res = record(api, admin, sid, key, source, value)
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] in {"digits12_required", "source_not_allowed"}


def test_FR_STU_015_twelve_digits_still_refused_everywhere_else(world: Any, api: Any) -> None:
    """Only the value of a typed APAAR entry is exempt; the Aadhaar guard is otherwise intact."""
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Apaar Guard")
    number = verhoeff_apaar()
    cases = [
        ("udise_pen", "udise_plus", number),
        ("aadhaar_last4", "aadhaar_as_printed", number),
        ("mother_tongue", "parent_form", number),
        ("apaar_id", "udise_plus", f"APAAR {number}"),
    ]
    for key, source, value in cases:
        res = record(api, admin, sid, key, source, value)
        assert res.status_code == 422, key
        assert res.json()["errors"][0]["code"] == "aadhaar_full_number_rejected", key
    search = api.call(admin, "POST", f"{BASE}/search", json={"query": number})
    assert search.status_code == 422
    create = api.call(
        admin,
        "POST",
        BASE,
        json={
            "values": [
                {"attribute_key": "full_name", "source": "admission_register", "value": "S"},
                {"attribute_key": "apaar_id", "source": "udise_plus", "value": number},
            ],
            "roll_no": number,
        },
    )
    assert create.status_code == 422
    assert [e["field"] for e in create.json()["errors"]] == ["roll_no"]


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("office_admin", 201),
        ("office_staff", 201),
        ("class_teacher", 403),
        ("teacher", 403),
        ("accountant", 403),
        ("exam_coordinator", 403),
        ("auditor_readonly", 403),
    ],
)
def test_FR_STU_013_recording_apaar_needs_update_nonidentity(
    *, world: Any, api: Any, shared: dict[str, Any], role: str, status: int
) -> None:
    res = record(
        api,
        world.person(role),
        str(shared["s9a"]),
        "apaar_id",
        "manual_entry",
        synthetic_apaar_id(random.Random(role)),
    )
    assert res.status_code == status, res.text


def test_FR_STU_013_other_school_and_out_of_scope_get_404(
    world: Any, api: Any, shared: dict[str, Any], admin_engine: Engine
) -> None:
    """Cross-tenant and BOLA: another school's student, or a value id of another student."""
    admin = world.person("office_admin")
    number = synthetic_apaar_id(random.Random(9))
    other = record(api, admin, str(shared["b_sb"]), "apaar_id", "udise_plus", number)
    assert other.status_code == 404
    sid = new_student(api, admin, "Synthetica Apaar Bola", str(world.a.ids["section_9c"]))
    res = record(api, admin, sid, "apaar_id", "udise_plus", number)
    assert res.status_code == 201
    value_id = res.json()["id"]
    # The class teacher of 9A cannot see the 9C student's APAAR ID at all.
    teacher = world.person("class_teacher")
    for path in ("", "/values"):
        assert api.call(teacher, "GET", f"{BASE}/{sid}{path}").status_code == 404
    # A value id of this student under another student's path is not found.
    s9a = str(shared["s9a"])
    wrong = api.call(admin, "POST", f"{BASE}/{s9a}/values/{value_id}/verify", json={})
    assert wrong.status_code == 404
    # School B's owner cannot read it: another school's student does not exist for them.
    b_owner = world.b.people["owner"]
    for path in ("", "/values"):
        assert api.call(b_owner, "GET", f"{BASE}/{sid}{path}").status_code == 404
    with admin_engine.connect() as c:
        status = c.execute(
            text("SELECT verification_status FROM sis.attribute_values WHERE id = :i"),
            {"i": uuid.UUID(value_id)},
        ).scalar_one()
    assert status == "unverified"


def test_SEC_008_apaar_and_pen_never_reach_logs(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invariant 5 / PRV-020: neither a synthetic nor a Verhoeff-valid APAAR ID is logged (the
    synthetic one would pass ``redact()`` untouched, so this proves it is never logged at all)."""
    admin = world.person("office_admin")
    sid = new_student(api, admin, "Synthetica Apaar Logs")
    synthetic = synthetic_apaar_id(random.Random(77))
    number = verhoeff_apaar()
    pen = "31345678902"
    capsys.readouterr()
    rec = record(api, admin, sid, "apaar_id", "udise_plus", synthetic)
    record(api, admin, sid, "apaar_id", "parent_form", number)
    record(api, admin, sid, "udise_pen", "udise_plus", pen)
    record(api, admin, sid, "apaar_id", "udise_plus", f"{synthetic}9")  # refused input
    api.call(admin, "POST", f"{BASE}/{sid}/values/{rec.json()['id']}/verify", json={})
    api.call(admin, "GET", f"{BASE}/{sid}")
    api.call(admin, "GET", f"{BASE}/{sid}/values", params={"attribute": "apaar_id"})
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    for secret in (synthetic, number, pen, number[:8], synthetic[:8]):
        assert secret not in logs, secret
