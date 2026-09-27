"""All-or-nothing commit (FR-IMP-004) and the 24-hour revert (FR-IMP-005).

Synthetic school A (tests/api/world.py); worker functions run synchronously.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, ValidationFailed
from app.imports import service
from app.students import service as students

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]
W = S.W


def _revert(school: Any, batch_id: uuid.UUID, role: str = "office_admin") -> Any:
    with tenant_session(school.tenant_id, school.people[role].user_id) as s:
        return service.revert(s, S.ctx(school, role), batch_id)


def _students_with(admin: Engine, tenant_id: uuid.UUID, numbers: list[str]) -> int:
    counted: int = S.count(
        admin,
        "SELECT count(*) FROM sis.students WHERE tenant_id = :t AND admission_no = ANY(:a)",
        t=tenant_id,
        a=numbers,
    )
    return counted


def test_FR_IMP_004_failure_midway_leaves_nothing(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, numbers = S.class_list(3)
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    real = students.create_student
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise ValidationFailed(
                [
                    {
                        "field": "values.0.value",
                        "code": "synthetic_failure",
                        "message_key": "errors.x",
                    }
                ]
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(students, "create_student", flaky)
    audit_before = S.count(
        admin_engine, "SELECT count(*) FROM audit.events WHERE tenant_id = :t", t=world.a.tenant_id
    )
    assert S.commit(world.a, batch_id) == "aborted"
    assert calls["n"] == 3
    # Nothing of rows 1-2 survived: no students, no values, no enrolments, no events.
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 0
    assert (
        S.D.outbox_events(admin_engine, world.a.tenant_id, "import.committed").count(
            {"batch_id": str(batch_id), "student_ids_count": 3}
        )
        == 0
    )
    audit_after = S.count(
        admin_engine, "SELECT count(*) FROM audit.events WHERE tenant_id = :t", t=world.a.tenant_id
    )
    new_actions = {
        e["action"]
        for e in W.audit_events(admin_engine, world.a.tenant_id)[audit_before:audit_after]
    }
    assert new_actions <= {"import.commit_requested", "import.validated"}
    batch = S.batch(admin_engine, batch_id)
    assert (batch["status"], batch["error_code"], batch["error_count"]) == (
        "validated",
        "commit_row_failed",
        1,
    )
    stored = S.rows(admin_engine, batch_id)
    assert stored[2]["errors"] == [
        {
            "field": "full_name",  # values.0 of the internal call -> the attribute
            "code": "synthetic_failure",
            "message_key": "errors.synthetic_failure",
        }
    ]
    # Fixed (the failure is gone): the same batch commits completely.
    monkeypatch.setattr(students, "create_student", real)
    assert S.commit(world.a, batch_id, skip_error_rows=True) == "committed"


def test_FR_IMP_004_commit_revalidates_against_the_current_records(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(2)
    first = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    # Meanwhile another import adds one of the same students with a different name.
    other = [list(rows[0]), list(rows[1])]
    other[1][1] = "Synthetica Registered Differently"
    S.imported(admin_engine, world.a, S.xlsx_bytes(other))
    assert S.commit(world.a, first) == "aborted"
    batch = S.batch(admin_engine, first)
    assert (batch["status"], batch["error_code"]) == ("validated", "revalidation_errors")
    assert S.rows(admin_engine, first)[0]["errors"][0]["code"] == "identity_change_required"
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 1


def test_FR_IMP_004_commit_preconditions(world: Any, admin_engine: Engine) -> None:
    rows, _ = S.class_list(2)
    rows[2][0] = ""  # a row without admission number
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    with pytest.raises(Conflict) as err:
        S.request_commit(world.a, batch_id)
    assert err.value.code == "import_has_errors"
    S.request_commit(world.a, batch_id, skip_error_rows=True)
    with pytest.raises(Conflict) as err:
        S.request_commit(world.a, batch_id, skip_error_rows=True)
    assert err.value.code == "import_not_validated"  # already committing


def test_FR_IMP_005_revert_removes_created_students_in_one_transaction(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(3)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sids = [S.student_by_adm(admin_engine, world.a.tenant_id, n) for n in numbers]
    out = _revert(world.a, batch_id)
    assert out.status == "reverted"
    assert out.reverted_at is not None
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 0
    for table in ("sis.attribute_values", "sis.enrollments", "sis.student_profiles"):
        assert (
            S.count(
                admin_engine, f"SELECT count(*) FROM {table} WHERE student_id = ANY(:s)", s=sids
            )
            == 0
        )
    assert [r["status"] for r in S.rows(admin_engine, batch_id)] == ["reverted"] * 3
    removed = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "student.removed")
        if e["summary"].get("import_batch_id") == str(batch_id)
    ]
    assert len(removed) == 3
    reverted = W.audit_events(admin_engine, world.a.tenant_id, "import.reverted")
    assert any(
        e["summary"]["students_removed"] == 3
        for e in reverted
        if str(e["resource_id"]) == str(batch_id)
    )
    assert {"batch_id": str(batch_id)} in S.D.outbox_events(
        admin_engine, world.a.tenant_id, "import.reverted"
    )
    with pytest.raises(Conflict) as err:
        _revert(world.a, batch_id)
    assert err.value.code == "import_not_committed"
    # The file can be imported again after a revert.
    again = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    assert S.batch(admin_engine, again)["stats"]["created"] == 3


def test_FR_IMP_005_revert_withdraws_observations_and_restores_replaced_values(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    assert sid is not None
    first = [
        ["Admission Number", "Student Name", "Mother Tongue", "Caste"],
        [numbers[0], "Synthetica Udise Spelling", "Telugu", "Synthetic Caste A"],
    ]
    S.imported(admin_engine, world.a, S.xlsx_bytes(first), source="udise_plus")
    second = [first[0], [numbers[0], "Synthetica Udise Spelling", "Urdu", "Synthetic Caste B"]]
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(second), source="udise_plus")
    version_before = S.SW.version(admin_engine, "sis.students", sid)
    out = _revert(world.a, batch_id)
    assert out.status == "reverted"
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 1  # existing student stays
    with tenant_session(world.a.tenant_id, world.a.people["owner"].user_id) as s:
        current = students.source_values(
            s, [sid], ["mother_tongue", "full_name", "caste"], include_sensitive=True
        )[sid]
    # Replaced values are current again (re-recorded; the batch's values stay in history).
    assert current["mother_tongue"]["udise_plus"].value == "Telugu"
    assert current["caste"]["udise_plus"].value == "Synthetic Caste A"
    assert (
        current["full_name"]["udise_plus"].value == "Synthetica Udise Spelling"
    )  # identical: no-op
    assert (
        S.count(
            admin_engine,
            "SELECT count(*) FROM sis.attribute_values WHERE import_batch_id = :b",
            b=batch_id,
        )
        == 2
    )  # kept in history
    assert S.SW.version(admin_engine, "sis.students", sid) > version_before


def test_FR_IMP_005_withdrawn_values_without_predecessor_are_rejected(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    udise = [["Admission Number", "Mother Tongue", "Remarks"], [numbers[0], "Telugu", ""]]
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(udise), source="udise_plus")
    _revert(world.a, batch_id)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT verification_status, superseded_by FROM sis.attribute_values "
                "WHERE import_batch_id = :b"
            ),
            {"b": batch_id},
        ).one()
    assert (row.verification_status, row.superseded_by) == ("rejected", None)
    with tenant_session(world.a.tenant_id, world.a.people["owner"].user_id) as s:
        profile = students.get_profile(s, S.SW.admin_ctx(world.a), sid)
    assert profile.canonical["mother_tongue"].value is None  # a rejected value never counts


def test_FR_IMP_005_revert_refused_after_24_hours(world: Any, admin_engine: Engine) -> None:
    rows, numbers = S.class_list(1)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    S.age_batch(
        admin_engine,
        batch_id,
        committed_at=dt.timedelta(hours=25),
        revert_deadline=dt.timedelta(hours=25),
    )
    with pytest.raises(Conflict) as err:
        _revert(world.a, batch_id)
    assert err.value.code == "revert_window_closed"
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 1


@pytest.mark.parametrize("change", ["later_value", "guardian", "status"])
def test_FR_IMP_005_revert_refused_when_records_changed_since(
    world: Any, admin_engine: Engine, change: str
) -> None:
    rows, numbers = S.class_list(2)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[1])
    assert sid is not None
    ctx = S.SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as s:
        if change == "later_value":
            students.record_value(s, ctx, sid, "mother_tongue", "parent_form", "Telugu")
        elif change == "guardian":
            from app.students.schemas import GuardianCreate

            students.add_guardian(
                s, ctx, sid, GuardianCreate(relationship="father", full_name="Synthetica Parent")
            )
        else:
            version = S.SW.version(admin_engine, "sis.students", sid)
            students.update_student_status(s, ctx, sid, "left", expected_version=version)
    with pytest.raises(Conflict) as err:
        _revert(world.a, batch_id)
    assert err.value.code == "import_has_dependents"
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 2
    assert S.batch(admin_engine, batch_id)["status"] == "committed"


def test_FR_IMP_005_revert_refused_when_a_batch_value_was_superseded(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    udise = [["Admission Number", "Mother Tongue", "Remarks"], [numbers[0], "Telugu", ""]]
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(udise), source="udise_plus")
    ctx = S.SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as s:
        students.record_value(s, ctx, sid, "mother_tongue", "udise_plus", "Hindi")
    with pytest.raises(Conflict) as err:
        _revert(world.a, batch_id)
    assert err.value.code == "import_has_dependents"


def test_FR_IMP_005_students_are_deleted_only_by_their_import_revert(
    world: Any, admin_engine: Engine, shared: dict[str, Any]
) -> None:
    """The database refuses any other student deletion (values history stays immutable)."""
    from sqlalchemy.exc import DBAPIError

    rows, numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    for target in (sid, shared["s9c"]):
        with (
            pytest.raises(DBAPIError, match="only by reverting"),
            tenant_session(world.a.tenant_id) as s,
        ):
            s.execute(
                text(
                    "WITH e AS (DELETE FROM sis.enrollments WHERE student_id = :s) "
                    "DELETE FROM sis.students WHERE id = :s"
                ),
                {"s": target},
            )
    # sos_app still cannot delete recorded values directly (FR-STU-005).
    from sqlalchemy.exc import ProgrammingError

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(world.a.tenant_id) as s,
    ):
        s.execute(text("DELETE FROM sis.attribute_values WHERE student_id = :s"), {"s": sid})


def test_FR_IMP_005_revert_takes_the_dq_findings_of_removed_students_along(
    world: Any, admin_engine: Engine
) -> None:
    """Every commit triggers a data-quality run (import.committed); its findings about the
    students the batch created must not block the 24-hour revert (FR-IMP-005, FR-DQ-002)."""
    from app.dq import service as dq

    rows, numbers = S.class_list(2)
    for row in rows[1:]:
        row[3] = "01/01/2022"  # far too young for class IX: DQ-003 age/class finding
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sids = [S.student_by_adm(admin_engine, world.a.tenant_id, n) for n in numbers]
    # Values of students the batch created carry the batch, so a batch-scoped run finds them.
    assert (
        S.count(
            admin_engine,
            "SELECT count(*) FROM sis.attribute_values "
            "WHERE student_id = ANY(:s) AND import_batch_id IS DISTINCT FROM :b",
            s=sids,
            b=batch_id,
        )
        == 0
    )
    run = dq.run_for_event(world.a.tenant_id, {"batch_id": str(batch_id), "student_ids_count": 2})
    assert run is not None
    findings = "SELECT count(*) FROM sis.dq_findings WHERE student_id = ANY(:s)"
    assert S.count(admin_engine, findings, s=sids) >= 2
    assert _revert(world.a, batch_id).status == "reverted"
    assert _students_with(admin_engine, world.a.tenant_id, numbers) == 0
    assert S.count(admin_engine, findings, s=sids) == 0


def test_FR_IMP_005_revert_refreshes_the_profile_of_students_whose_values_are_withdrawn(
    world: Any, admin_engine: Engine
) -> None:
    """A withdrawn value no longer counts, so the searchable profile and the ETag change with it
    (the students module owns that projection; imports only asks it to revert the batch)."""
    rows, numbers = S.class_list(1)
    S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0])
    assert sid is not None
    parent = [["Admission Number", "Mother Name", "Remarks"], [numbers[0], "Synthetica Mother", ""]]
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(parent), source="parent_form")
    profile_sql = "SELECT count(*) FROM sis.student_profiles WHERE student_id = :s AND {}"
    assert S.count(admin_engine, profile_sql.format("mother_name_norm IS NOT NULL"), s=sid) == 1
    version_before = S.SW.version(admin_engine, "sis.students", sid)
    _revert(world.a, batch_id)
    assert S.count(admin_engine, profile_sql.format("mother_name_norm IS NULL"), s=sid) == 1
    assert S.SW.version(admin_engine, "sis.students", sid) > version_before
    withdrawn = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "student.values.withdrawn")
        if str(e["resource_id"]) == str(sid)
    ]
    assert len(withdrawn) == 1
    assert withdrawn[0]["resource_type"] == "student"
    assert withdrawn[0]["summary"] == {
        "import_batch_id": str(batch_id),
        "attribute_keys": ["mother_name"],
        "value_count": 1,
        "reason": "import_reverted",
    }


def test_FR_IMP_005_imports_never_write_student_record_tables_directly() -> None:
    """Module ownership (CLAUDE.md §4): the revert goes through students.service."""
    from pathlib import Path

    import app.imports as imports_pkg

    package = Path(imports_pkg.__file__).resolve().parent
    for path in (package / "repository.py", package / "service.py"):
        source = path.read_text("utf-8")
        for table in ("sis.students", "sis.attribute_values", "sis.enrollments"):
            assert table not in source, f"{path.name} names {table}"
    service_source = (package / "service.py").read_text("utf-8")
    assert "app.students import crypto" not in service_source


def _reverted_notices(admin: Engine, batch_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT recipient_membership_id, params, resource_type FROM ops.notifications "
                    "WHERE resource_id = :b AND template_key = 'import.reverted'"
                ),
                {"b": batch_id},
            )
        )


def test_FR_NOT_001_importer_is_told_when_someone_else_reverts_their_import(
    world: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(2)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))  # by the office admin
    _revert(world.a, batch_id, role="principal")
    notices = _reverted_notices(admin_engine, batch_id)
    assert [(n.recipient_membership_id, n.params, n.resource_type) for n in notices] == [
        (
            world.a.people["office_admin"].membership_id,
            {"import_id": str(batch_id), "rows": 2},
            "import_batch",
        )
    ]


def test_FR_NOT_001_no_notice_when_the_importer_reverts_their_own_import(
    world: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(1)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    _revert(world.a, batch_id)
    assert _reverted_notices(admin_engine, batch_id) == []
