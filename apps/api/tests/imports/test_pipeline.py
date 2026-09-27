"""Import pipeline through the service and workers (US-401 AC1-AC3; FR-IMP-001..005).

Synthetic school A from tests/api/world.py (current year 2026-27 with 9A, 9C, 10A); the worker
functions run synchronously here (the Celery tasks only unpack IDs and call them).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.errors import NotFound
from app.imports import service
from app.imports.config import import_config
from app.imports.schemas import ColumnMap, MappingIn, TemplateCreate
from app.imports.sheet import read_sheet
from app.imports.validation import validate_sheet
from app.students import service as students

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]
W = S.W


def _profile(school: Any, sid: uuid.UUID) -> Any:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        return students.get_profile(s, S.SW.admin_ctx(school), sid)


def test_US_401_AC1_upload_parse_suggest_and_validate(world: Any, admin_engine: Engine) -> None:
    rows, numbers = S.class_list(3)
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    batch = S.batch(admin_engine, batch_id)
    assert batch["status"] == "validated"
    assert batch["file_kind"] == "xlsx"
    assert batch["header_row"] == 1
    assert batch["mapping"] == {
        "0": "admission_no",
        "1": "full_name",
        "2": "father_name",
        "3": "dob",
        "4": "gender",
        "5": "class",
        "6": "section",
    }
    assert [c["suggested"] for c in batch["columns"]][:2] == ["admission_no", "full_name"]
    assert batch["row_count"] == 3
    assert batch["error_count"] == 0
    stored = S.rows(admin_engine, batch_id)
    assert [r["status"] for r in stored] == ["valid"] * 3
    assert stored[0]["parsed"]["values"]["admission_no"] == numbers[0]
    assert stored[0]["parsed"]["section_id"] == str(world.a.ids["section_9a"])
    assert stored[0]["parsed"]["class_label"] == "IX-A"
    # Nothing is saved to the student record before commit (US-401 AC2).
    assert S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0]) is None
    # The importer is told the file was checked.
    assert (
        S.count(
            admin_engine,
            "SELECT count(*) FROM ops.notifications WHERE resource_id = :b "
            "AND template_key = 'import.validated' AND recipient_membership_id = :m",
            b=batch_id,
            m=world.a.people["office_admin"].membership_id,
        )
        == 1
    )


def test_US_401_AC3_commit_creates_students_attributed_to_the_source(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(3, section="C")
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    batch = S.batch(admin_engine, batch_id)
    assert batch["status"] == "committed"
    assert batch["revert_deadline"] - batch["committed_at"] == dt.timedelta(hours=24)
    assert batch["stats"]["created"] == 3
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[1])
    assert sid is not None
    profile = _profile(world.a, sid)
    assert profile.enrollment is not None
    assert profile.enrollment.section_id == world.a.ids["section_9c"]
    name = profile.canonical["full_name"]
    assert (name.value, name.source, name.provisional) == (
        "Synthetica Import Student 0001",
        "admission_register",
        True,  # identity values from an import stay provisional until verified
    )
    assert profile.canonical["gender"].value == "male"
    assert profile.canonical["dob"].value == "2012-02-02"
    committed = S.rows(admin_engine, batch_id)
    assert all(r["status"] == "committed" and r["created_student"] for r in committed)
    assert all(r["student_version"] >= 1 for r in committed)
    # Audit, outbox (IDs/counts only) and the importer's notification (same transaction).
    events = W.audit_events(admin_engine, world.a.tenant_id, "import.committed")
    mine = [e for e in events if str(e["resource_id"]) == str(batch_id)]
    assert mine
    assert mine[0]["summary"]["students_created"] == 3
    payloads = S.D.outbox_events(admin_engine, world.a.tenant_id, "import.committed")
    assert {"batch_id": str(batch_id), "student_ids_count": 3} in payloads
    assert (
        S.count(
            admin_engine,
            "SELECT count(*) FROM ops.notifications WHERE resource_id = :b "
            "AND template_key = 'import.committed'",
            b=batch_id,
        )
        == 1
    )


def test_FR_IMP_004_other_sources_add_observations_to_existing_students(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(2)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    udise = [
        ["Admission Number", "Student Name", "Mother Tongue", "DOB"],
        [numbers[0], "Synthetica Imprt Student 0000", "Telugu", "02/01/2012"],
        [numbers[1], "Synthetica Import Student 0001", "Urdu", "03/03/2012"],
    ]
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(udise), source="udise_plus")
    assert S.batch(admin_engine, batch_id)["stats"]["updated"] == 2
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    assert sid is not None
    profile = _profile(world.a, sid)
    name = profile.canonical["full_name"]
    assert name.source == "admission_register"  # UDISE+ never becomes canonical for names
    assert name.conflicts == ["udise_plus"]
    assert profile.canonical["dob"].conflicts == ["udise_plus"]
    udise_values = [v for v in profile.values["full_name"] if v.source == "udise_plus"]
    assert udise_values[0].import_batch_id == batch_id
    assert profile.canonical["mother_tongue"].value == "Telugu"


def test_FR_IMP_003_register_identity_is_not_overwritten_by_a_reimport(
    world: Any, admin_engine: Engine
) -> None:
    rows, _numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    changed = [list(rows[0]), list(rows[1])]
    changed[1][1] = "Synthetica Someone Else"
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(changed))
    stored = S.rows(admin_engine, batch_id)
    assert stored[0]["status"] == "error"
    assert stored[0]["errors"] == [
        {
            "field": "full_name",
            "code": "identity_change_required",
            "message_key": "errors.identity_change_required",
        }
    ]
    # Re-importing the same file is harmless: identical values are no-ops.
    same = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    assert S.batch(admin_engine, same)["stats"]["updated"] == 1


def test_FR_IMP_003_duplicates_against_existing_and_within_file(
    world: Any, admin_engine: Engine
) -> None:
    existing_rows, existing = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(existing_rows))
    rows, _numbers = S.class_list(2)
    rows.append(list(rows[1]))  # the first data row again: duplicate admission number
    rows.append(list(existing_rows[1]))  # already a student: matched, never created twice
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    stored = S.rows(admin_engine, batch_id)
    assert [r["status"] for r in stored] == ["error", "valid", "error", "valid"]
    assert stored[0]["errors"][0] == {
        "field": "admission_no",
        "code": "duplicate_in_file",
        "message_key": "errors.duplicate_in_file",
        "ref": "4",
    }
    assert [r["action"] for r in stored] == ["create", "create", "create", "update"]
    assert stored[3]["student_id"] == S.student_by_adm(admin_engine, world.a.tenant_id, existing[0])
    assert S.batch(admin_engine, batch_id)["error_count"] == 2


def test_FR_IMP_002_mapping_change_template_and_reuse(world: Any, admin_engine: Engine) -> None:
    rows = [
        ["Ref", "Pupil", "Born", "Std", "Div", f"Layout {uuid.uuid4().hex[:8]}"],
        [S.adm(), "Synthetica Template One", "14/03/2012", "9", "A"],
    ]
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    batch = S.batch(admin_engine, batch_id)
    assert batch["status"] == "parsed"  # no admission number column recognised
    admin = S.ctx(world.a)
    columns = [
        ColumnMap(index=0, target="admission_no"),
        ColumnMap(index=1, target="full_name"),
        ColumnMap(index=2, target="dob"),
        ColumnMap(index=3, target="class"),
        ColumnMap(index=4, target="section"),
    ]
    with tenant_session(world.a.tenant_id, admin.user_id) as s:
        out = service.set_mapping(
            s, admin, batch_id, MappingIn(columns=columns), expected_version=batch["version"]
        )
        assert out.status == "parsed"
        service.request_validation(s, admin, batch_id)
        template = service.create_template(
            s,
            admin,
            TemplateCreate(name=f"Synthetic layout {uuid.uuid4().hex[:6]}", import_id=batch_id),
        )
    assert S.run_validate(world.a, batch_id) == "validated"
    assert S.batch(admin_engine, batch_id)["error_count"] == 0
    # The next file with the same headers is mapped by the saved template automatically.
    again = [rows[0], [S.adm(), "Synthetica Template Two", "15/03/2012", "9", "A"]]
    second = S.start(admin_engine, world.a, S.xlsx_bytes(again))
    batch2 = S.batch(admin_engine, second)
    assert batch2["mapping_template_id"] == template.id
    assert batch2["status"] == "validated"
    assert batch2["mapping"]["0"] == "admission_no"


def test_FR_IMP_002_mapping_needs_if_match_and_valid_targets(
    world: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(1)
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    admin = S.ctx(world.a)
    version = S.batch(admin_engine, batch_id)["version"]
    from app.core.errors import PreconditionFailed, ValidationFailed

    with pytest.raises(PreconditionFailed), tenant_session(world.a.tenant_id) as s:
        service.set_mapping(s, admin, batch_id, MappingIn(columns=[]), expected_version=version - 1)
    with pytest.raises(ValidationFailed) as err, tenant_session(world.a.tenant_id) as s:
        service.set_mapping(
            s,
            admin,
            batch_id,
            MappingIn(columns=[ColumnMap(index=0, target="aadhaar_last4")]),
            expected_version=version,
        )
    assert err.value.errors[0]["code"] == "source_not_allowed"


def test_SEC_017_formula_cells_do_not_become_values(world: Any, admin_engine: Engine) -> None:
    number = S.adm()
    rows = [
        S.HEADER,
        [
            number,
            '=HYPERLINK("https://evil.example","Synthetica")',
            "",
            "14/03/2012",
            "M",
            "IX",
            "A",
        ],
    ]
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    stored = S.rows(admin_engine, batch_id)[0]
    assert stored["status"] == "error"
    assert {
        "field": "full_name",
        "code": "formula_not_evaluated",
        "message_key": "errors.formula_not_evaluated",
    } in stored["errors"]
    assert "full_name" not in stored["parsed"]["values"]
    assert "HYPERLINK" not in repr(stored)


def test_SEC_013_full_aadhaar_rows_fail_and_nothing_is_stored(
    world: Any, admin_engine: Engine
) -> None:
    number = S.valid_aadhaar("34567890123")
    rows = [
        [*S.HEADER, "Aadhaar"],
        [S.adm(), "Synthetica Aadhaar One", "", "14/03/2012", "M", "IX", "A", number],
        [S.adm(), "Synthetica Aadhaar Two", "", "14/03/2012", "F", "IX", "A", ""],
    ]
    batch_id = S.start(admin_engine, world.a, S.csv_bytes(rows), kind="csv")
    stored = S.rows(admin_engine, batch_id)
    assert [r["status"] for r in stored] == ["error", "valid"]
    assert stored[0]["errors"] == [
        {
            "field": "column_8",
            "code": "aadhaar_full_number_rejected",
            "message_key": "errors.aadhaar_last4_only",
        }
    ]
    everything = repr(S.batch(admin_engine, batch_id)) + repr(stored)
    everything += repr(W.audit_events(admin_engine, world.a.tenant_id))
    assert number not in everything
    assert number[-4:] not in everything
    # Commit only the valid row: the Aadhaar row is skipped and nothing of it is kept.
    assert S.commit(world.a, batch_id, skip_error_rows=True) == "committed"
    assert [r["status"] for r in S.rows(admin_engine, batch_id)] == ["skipped", "committed"]
    assert (
        S.count(
            admin_engine,
            "SELECT count(*) FROM sis.attribute_values v "
            "JOIN sis.import_rows r ON r.student_id = v.student_id "
            "WHERE r.batch_id = :b AND v.attribute_key LIKE 'aadhaar%'",
            b=batch_id,
        )
        == 0
    )


def test_FR_IMP_004_c3_values_are_encrypted_at_commit_and_never_in_import_rows(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(1)
    rows[0].append("Caste")
    rows[1].append("Synthetic Caste Value")
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    stored = S.rows(admin_engine, batch_id)[0]
    assert stored["parsed"]["sensitive"] == ["caste"]
    assert "Synthetic Caste Value" not in repr(stored)
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    with admin_engine.connect() as c:
        from sqlalchemy import text

        row = c.execute(
            text(
                "SELECT value_text, value_ciphertext FROM sis.attribute_values "
                "WHERE student_id = :s AND attribute_key = 'caste'"
            ),
            {"s": sid},
        ).one()
    assert row.value_text is None
    assert row.value_ciphertext is not None


def test_SEC_015_scoped_importer_cannot_write_other_sections(
    world: Any, admin_engine: Engine
) -> None:
    """A custom role holding import.* only for section 9A (scoped grant)."""
    rows, _ = S.class_list(2)
    rows[2][5], rows[2][6] = "X", "A"  # second student into 10A: outside the scope
    importer = S.ctx(world.a)
    from dataclasses import replace

    from app.authz.context import Scopes

    scoped = replace(
        importer,
        scopes=Scopes(section_ids=frozenset({world.a.ids["section_9a"]})),
        scoped_permissions=frozenset({"import.run", "import.commit", "student.create"}),
    )
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    sheet = read_sheet(S.xlsx_bytes(rows), "xlsx", import_config().limits)
    with tenant_session(world.a.tenant_id, importer.user_id) as s:
        batch = service._visible(s, scoped, batch_id, "import.run")
        vctx = service._validation_context(s, scoped, batch, sheet, "import.commit")
        result = validate_sheet(sheet, batch.mapping, vctx)
    assert [r.status for r in result.rows] == ["valid", "error"]
    assert result.rows[1].errors[0]["code"] == "section_out_of_scope"
    # A scoped holder sees only the imports they started (404 for the others).
    other = S.start(admin_engine, world.a, S.xlsx_bytes(S.class_list(1)[0]), role="principal")
    with pytest.raises(NotFound), tenant_session(world.a.tenant_id) as s:
        service.get_import(s, scoped, other)
