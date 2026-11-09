"""UDISE+ list import matched by PEN or name + DOB + class, with a person's pick (FO-04;
FR-UDI-011..FR-UDI-016; invariants 3, 6, 7). Synthetic data only.

The authz matrix (tests/security/test_authz_matrix.py) and BOLA (tests/security/test_bola.py)
cover every role and the other school's ids for the new route; these tests cover behaviour,
the allowed role, a disallowed role, another school's import and the audit in the same
transaction.
"""

from __future__ import annotations

import random
import string
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]

UDISE_HEADER = ["Name of Student", "Date of Birth", "Class", "Section"]


def _register(admin: Engine, world: Any, n: int = 3) -> tuple[list[list[Any]], list[uuid.UUID]]:
    rows, numbers = S.class_list(n, prefix="UDM")
    # Names unique to this test: the world school is shared, and a name match must be unique.
    tag = "".join(random.choices(string.ascii_lowercase, k=8)).capitalize()
    for row in rows[1:]:
        row[1] = f"{row[1]} {tag}"
    S.imported(admin, world.a, S.xlsx_bytes(rows))
    ids = [S.student_by_adm(admin, world.a.tenant_id, number) for number in numbers]
    assert all(ids)
    return rows, [i for i in ids if i is not None]


def _udise_file(register_rows: list[list[Any]]) -> bytes:
    first = register_rows[1]
    data: bytes = S.xlsx_bytes(
        [
            UDISE_HEADER,
            # Same name (other capitals and spaces), DOB and class as the first student.
            [first[1].upper().replace(" ", "  "), first[3], first[5], first[6]],
            ["Synthetica Nobody Known", "02/02/2012", "IX", "A"],
        ]
    )
    return data


def _rows(admin: Engine, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = S.rows(admin, batch_id)
    return rows


def _match(api: Any, who: Any, batch_id: uuid.UUID, row_no: int, body: dict[str, Any]) -> Any:
    got = api.call(who, "GET", f"/api/v1/imports/{batch_id}")
    etag = got.headers.get("ETag", 'W/"0"')
    return api.call(
        who,
        "PUT",
        f"/api/v1/imports/{batch_id}/rows/{row_no}/match",
        json=body,
        headers={"If-Match": etag},
    )


def test_FR_UDI_011_udise_list_without_admission_numbers_matches_and_waits_for_review(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    register, ids = _register(admin_engine, world)
    batch_id = S.start(admin_engine, world.a, _udise_file(register), source="udise_plus")
    batch = S.batch(admin_engine, batch_id)
    assert batch["status"] == "validated", batch
    first, second = _rows(admin_engine, batch_id)
    assert first["status"] == "valid"
    assert first["student_id"] == ids[0]
    assert first["parsed"]["match_basis"] == "name_dob_class"
    assert second["status"] == "error"
    assert [e["code"] for e in second["errors"]] == ["udise_unmatched"]
    assert batch["stats"]["to_review"] == 1
    assert batch["stats"]["matched_name_dob_class"] == 1

    # The validation sheet shows the basis per row (FR-UDI-012).
    admin = world.person("office_admin")
    listed = api.call(admin, "GET", f"/api/v1/imports/{batch_id}/rows", params={"limit": 50})
    assert listed.status_code == 200, listed.text
    assert [r["match_basis"] for r in listed.json()["data"]] == ["name_dob_class", None]


def test_FR_UDI_014_a_person_picks_the_student_audited_in_the_same_transaction(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    register, ids = _register(admin_engine, world)
    batch_id = S.start(admin_engine, world.a, _udise_file(register), source="udise_plus")
    admin = world.person("office_admin")

    picked = _match(api, admin, batch_id, 3, {"decision": "student", "student_id": str(ids[2])})
    assert picked.status_code == 200, picked.text
    body = picked.json()
    assert (body["status"], body["match_basis"], body["student_id"]) == (
        "valid",
        "picked",
        str(ids[2]),
    )
    batch = S.batch(admin_engine, batch_id)
    assert batch["row_matches"] == {"3": str(ids[2])}
    assert batch["stats"]["to_review"] == 0, batch["stats"]
    with admin_engine.connect() as c:
        events: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE resource_id = :r "
                "AND action = 'import.row_matched'"
            ),
            {"r": batch_id},
        ).scalars()
        summaries = list(events)
    assert summaries == [{"row_no": 3, "decision": "student", "student_id": str(ids[2])}]

    skipped = _match(api, admin, batch_id, 3, {"decision": "skip"})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "skipped"
    cleared = _match(api, admin, batch_id, 3, {"decision": "clear"})
    assert cleared.json()["status"] == "error"

    # A row that matched by itself may also be skipped; the commit then adds only the rest.
    assert _match(api, admin, batch_id, 3, {"decision": "skip"}).status_code == 200
    before = S.count(
        admin_engine, "SELECT count(*) FROM sis.students WHERE tenant_id = :t", t=world.a.tenant_id
    )
    assert S.commit(world.a, batch_id) == "committed"
    with admin_engine.connect() as c:
        students: int = c.execute(
            text("SELECT count(*) FROM sis.students WHERE tenant_id = :t"),
            {"t": world.a.tenant_id},
        ).scalar_one()
        udise_names: int = c.execute(
            text(
                "SELECT count(*) FROM sis.attribute_values WHERE student_id = :s "
                "AND attribute_key = 'full_name' AND source = 'udise_plus' "
                "AND import_batch_id = :b"
            ),
            {"s": ids[0], "b": batch_id},
        ).scalar_one()
    assert students == before  # BR-01: a UDISE+ list never creates a student
    assert udise_names == 1  # recorded as a UDISE+ value, the register value untouched


def test_FR_UDI_015_pick_checks_reach_role_and_school(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    register, _ = _register(admin_engine, world)
    batch_id = S.start(admin_engine, world.a, _udise_file(register), source="udise_plus")
    admin = world.person("office_admin")
    # Another school's student: 404, nothing stored.
    b_student = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:i, :t, :a)"),
            {"i": b_student, "t": world.b.tenant_id, "a": S.adm("UDB")},
        )
    other = _match(api, admin, batch_id, 3, {"decision": "student", "student_id": str(b_student)})
    assert other.status_code == 404
    assert S.batch(admin_engine, batch_id)["row_matches"] == {}
    # A role without import.run: 403.
    denied = _match(api, world.person("teacher"), batch_id, 3, {"decision": "skip"})
    assert denied.status_code == 403
    # Another school's import: 404.
    b_udise = S.start(None, world.b, _udise_file(register), role="owner", source="udise_plus")
    assert _match(api, admin, b_udise, 2, {"decision": "skip"}).status_code == 404


def test_FR_UDI_016_only_pen_matching_imports_take_a_pick(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(1, prefix="UDR")
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    res = _match(api, world.person("office_admin"), batch_id, 2, {"decision": "skip"})
    assert res.status_code == 409
    assert res.json()["code"] == "import_not_matchable"
